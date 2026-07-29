package udpbd

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"sync"
	"time"

	edgelog "github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/statuslistener"
)

// Config describes one UDPBD service.
type Config struct {
	Image    string
	Bind     string
	Port     int
	ReadOnly bool
	Log      *edgelog.Logger
	// StatusPort answers router status queries. Zero disables it; negative
	// means the standard port, which is UDPFS's discovery port -- legitimately
	// taken on a box already running that server, so a bind failure is logged
	// and ignored rather than taking UDPBD down over a diagnostic.
	StatusPort int
}

// device is an image file addressed as fixed-size sectors.
type device struct {
	path     string
	file     *os.File
	size     int64
	readOnly bool
}

func openDevice(path string, readOnly bool) (*device, error) {
	flag := os.O_RDWR
	if readOnly {
		flag = os.O_RDONLY
	}
	f, err := os.OpenFile(path, flag, 0o644)
	if err != nil && !readOnly {
		// Fall back to read-only rather than refusing to serve: an image on a
		// read-only mount is still perfectly useful for loading games, and this
		// mirrors the Python server's behaviour.
		f, err = os.OpenFile(path, os.O_RDONLY, 0o644)
		readOnly = true
	}
	if err != nil {
		return nil, err
	}
	info, err := f.Stat()
	if err != nil {
		f.Close()
		return nil, err
	}
	return &device{path: path, file: f, size: info.Size(), readOnly: readOnly}, nil
}

func (d *device) sectorCount() int64 { return d.size / SectorSize }

// readAtOffset reads size bytes at an absolute byte offset, zero-padding a
// short read so RDMA packet sizing stays correct rather than truncating.
//
// Positional reads are used throughout rather than a shared file cursor, so two
// consoles reading concurrently cannot pull each other's data out from under
// one another.
func (d *device) readAtOffset(offset int64, size int) ([]byte, error) {
	buf := make([]byte, size)
	n, err := d.file.ReadAt(buf, offset)
	if err != nil && !errors.Is(err, io.EOF) {
		return nil, err
	}
	for i := n; i < size; i++ {
		buf[i] = 0
	}
	return buf, nil
}

func (d *device) writeAt(offset int64, data []byte) error {
	if d.readOnly {
		return nil
	}
	_, err := d.file.WriteAt(data, offset)
	return err
}

func (d *device) close() { _ = d.file.Close() }

// writeState tracks one in-flight CMD_WRITE handshake.
//
// UDPBD serves a single image through a single file, so a write sequence is
// inherently one-client-at-a-time; the reference server keeps exactly one such
// state. The peer is recorded as well so a stray RDMA from a *different*
// console cannot land in the middle of another one's write.
type writeState struct {
	peer   string
	offset int64
	left   int64
}

// Server serves one disk image over UDPBD.
type Server struct {
	cfg  Config
	dev  *device
	conn *net.UDPConn

	mu    sync.Mutex
	write writeState
	seen  map[string]bool // peers already announced, to keep the log quiet

	totalRead  int64
	totalWrite int64

	started time.Time
	status  *statuslistener.Listener
}

func New(cfg Config) (*Server, error) {
	if cfg.Image == "" {
		return nil, fmt.Errorf("a disk image is required")
	}
	if cfg.Port == 0 {
		cfg.Port = Port
	}
	if cfg.Port < 1 || cfg.Port > 65535 {
		return nil, fmt.Errorf("port out of range")
	}
	if cfg.Log == nil {
		cfg.Log = edgelog.New(os.Stdout, "text", false, false)
	}
	dev, err := openDevice(cfg.Image, cfg.ReadOnly)
	if err != nil {
		return nil, err
	}
	if dev.size < SectorSize {
		dev.close()
		return nil, fmt.Errorf("image is smaller than one %d-byte sector", SectorSize)
	}
	return &Server{cfg: cfg, dev: dev, seen: make(map[string]bool), started: time.Now()}, nil
}

// Listen binds the UDPBD port.
func (s *Server) Listen() error {
	ip := net.ParseIP(s.cfg.Bind)
	if s.cfg.Bind != "" && ip == nil {
		return fmt.Errorf("invalid bind address %q", s.cfg.Bind)
	}
	conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: ip, Port: s.cfg.Port})
	if err != nil {
		return err
	}
	s.conn = conn
	return nil
}

// Addr reports the bound address, for tests.
func (s *Server) Addr() *net.UDPAddr {
	if s.conn == nil {
		return nil
	}
	return s.conn.LocalAddr().(*net.UDPAddr)
}

// statusReply reports what this server is doing, for the router status
// protocol. See docs/ROUTER-STATUS.md.
func (s *Server) statusReply() protocol.StatusReply {
	flags := protocol.FlagServesUDPBD
	if s.dev != nil && s.dev.readOnly {
		flags |= protocol.FlagReadOnly
	}

	state := protocol.StateReady
	if s.dev == nil || s.dev.file == nil {
		state = protocol.StateDegraded
	} else if _, err := os.Stat(s.dev.path); err != nil {
		// The image went away -- an unmounted USB stick, most likely, which is
		// the ordinary failure on this hardware.
		state = protocol.StateDegraded
	}

	s.mu.Lock()
	peers := len(s.seen)
	// left > 0 is a write with bytes still outstanding. There is no separate
	// "active" flag: a completed write leaves left at zero.
	writing := s.write.left > 0
	s.mu.Unlock()
	if state == protocol.StateReady && writing {
		// Only a write in flight counts as busy. That is the case worth
		// warning about: cutting power mid-write is what costs a save.
		state = protocol.StateBusy
	}

	return protocol.StatusReply{
		State:    state,
		Flags:    flags,
		Sessions: uint16(peers),
		Uptime:   statuslistener.Uptime(s.started),
		Name:     "PS2 Servers Edge",
	}
}

func (s *Server) Close() error {
	_ = s.status.Close()
	if s.conn != nil {
		err := s.conn.Close()
		s.dev.close()
		return err
	}
	s.dev.close()
	return nil
}

// Serve runs until ctx is cancelled or the socket closes.
func (s *Server) Serve(ctx context.Context) error {
	if s.conn == nil {
		if err := s.Listen(); err != nil {
			return err
		}
	}
	mode := "read/write"
	if s.dev.readOnly {
		mode = "read-only"
	}
	if s.cfg.StatusPort != 0 {
		port := s.cfg.StatusPort
		if port < 0 {
			port = int(protocol.DefaultPort)
		}
		if sl, err := statuslistener.Start(s.cfg.Bind, port, s.statusReply, s.cfg.Log); err != nil {
			s.cfg.Log.Warn("UDPBD status channel unavailable", map[string]any{
				"port": port, "error": err.Error()})
		} else {
			s.status = sl
			s.cfg.Log.Info("UDPBD status channel", map[string]any{"addr": sl.Addr().String()})
		}
	}

	s.cfg.Log.Info("UDPBD listening", map[string]any{
		"image":   s.dev.path,
		"mode":    mode,
		"sectors": s.dev.sectorCount(),
		"size_mb": s.dev.size / (1024 * 1024),
		"listen":  s.Addr().String(),
	})

	done := make(chan struct{})
	go func() {
		select {
		case <-ctx.Done():
			_ = s.conn.Close()
		case <-done:
		}
	}()
	defer close(done)

	buf := make([]byte, RecvBufLen)
	for {
		n, peer, err := s.conn.ReadFromUDP(buf)
		if err != nil {
			select {
			case <-ctx.Done():
				return nil
			default:
				return nil // socket closed
			}
		}
		if n < 2 {
			continue
		}
		s.dispatch(append([]byte(nil), buf[:n]...), peer)
	}
}

func (s *Server) dispatch(p []byte, peer *net.UDPAddr) {
	switch headerCmd(p) {
	case CmdInfo:
		s.handleInfo(p, peer)
	case CmdRead:
		if len(p) >= 8 {
			s.handleRead(p, peer)
		}
	case CmdWrite:
		if len(p) >= 8 {
			s.handleWrite(p, peer)
		}
	case CmdWriteRDMA:
		if len(p) >= 6 {
			s.handleWriteRDMA(p, peer)
		}
	default:
		s.cfg.Log.Debug("ignoring unknown UDPBD command",
			map[string]any{"peer": peer.String(), "cmd": headerCmd(p)})
	}
}

// sectorRequest reads the (sector_nr, sector_count) pair shared by CMD_READ and
// CMD_WRITE. Layout is header:uint16, sector_nr:uint32, sector_count:uint16.
func sectorRequest(p []byte) (sectorNr int64, sectorCount int64) {
	nr := int64(uint32(p[2]) | uint32(p[3])<<8 | uint32(p[4])<<16 | uint32(p[5])<<24)
	count := int64(uint16(p[6]) | uint16(p[7])<<8)
	return nr, count
}

func (s *Server) handleInfo(p []byte, peer *net.UDPAddr) {
	_, cmdid, _ := unpackHeader(p)

	// First contact names the console's address, which is what tells a wrong
	// subnet or firewall apart from a server that never heard anything.
	s.mu.Lock()
	first := !s.seen[peer.String()]
	s.seen[peer.String()] = true
	readTotal, writeTotal := s.totalRead, s.totalWrite
	s.mu.Unlock()
	if first {
		s.cfg.Log.Info("UDPBD INFO received, console found this server",
			map[string]any{"peer": peer.String()})
	} else {
		s.cfg.Log.Debug("UDPBD INFO", map[string]any{
			"peer": peer.String(), "read_kib": readTotal / 1024, "write_kib": writeTotal / 1024})
	}

	reply := packHeader(CmdInfoReply, cmdid, 1)
	reply = append(reply, le32(SectorSize)...)
	reply = append(reply, le32(uint32(s.dev.sectorCount()))...)
	s.send(reply, peer)
}

func (s *Server) handleRead(p []byte, peer *net.UDPAddr) {
	_, cmdid, _ := unpackHeader(p)
	sectorNr, sectorCount := sectorRequest(p)
	if sectorCount <= 0 {
		return
	}
	// Refuse a request that runs past the image rather than streaming zero
	// padding for an arbitrary length: a bad or hostile count would otherwise
	// make the server emit unbounded traffic.
	if sectorNr < 0 || sectorNr+sectorCount > s.dev.sectorCount() {
		s.cfg.Log.Warn("UDPBD READ out of range", map[string]any{
			"peer": peer.String(), "sector": sectorNr, "count": sectorCount,
			"sectors": s.dev.sectorCount()})
		return
	}

	shift := blockShiftForSectors(sectorCount)
	blockSize, blocksPerPacket, blocksPerSector := blockGeometry(shift)
	blocksLeft := sectorCount * int64(blocksPerSector)

	s.mu.Lock()
	s.totalRead += blocksLeft * int64(blockSize)
	s.mu.Unlock()

	s.cfg.Log.Debug("UDPBD READ", map[string]any{
		"peer": peer.String(), "sector": sectorNr, "count": sectorCount,
		"block_size": blockSize})

	offsetSector := sectorNr
	consumed := int64(0)
	cmdpkt := uint8(1)
	for blocksLeft > 0 {
		blockCount := blocksLeft
		if blockCount > int64(blocksPerPacket) {
			blockCount = int64(blocksPerPacket)
		}
		blocksLeft -= blockCount
		size := int(blockCount) * blockSize

		// Read at an absolute byte offset rather than relying on a shared file
		// cursor, so two consoles reading at once cannot pull each other's data.
		data, err := s.dev.readAtOffset(offsetSector*SectorSize+consumed, size)
		if err != nil {
			s.cfg.Log.Warn("UDPBD read failed",
				map[string]any{"peer": peer.String(), "error": err.Error()})
			return
		}
		consumed += int64(size)

		packet := packHeader(CmdReadRDMA, cmdid, cmdpkt)
		packet = append(packet, packBlockType(shift, uint16(blockCount))...)
		packet = append(packet, data...)
		s.send(packet, peer)
		cmdpkt = (cmdpkt + 1) & 0xFF
	}
}

func (s *Server) handleWrite(p []byte, peer *net.UDPAddr) {
	_, _, _ = unpackHeader(p)
	sectorNr, sectorCount := sectorRequest(p)
	if sectorCount <= 0 {
		return
	}
	if sectorNr < 0 || sectorNr+sectorCount > s.dev.sectorCount() {
		s.cfg.Log.Warn("UDPBD WRITE out of range", map[string]any{
			"peer": peer.String(), "sector": sectorNr, "count": sectorCount})
		return
	}
	if s.dev.readOnly {
		s.cfg.Log.Warn("UDPBD WRITE on a read-only image, ignoring",
			map[string]any{"peer": peer.String()})
	}
	s.cfg.Log.Debug("UDPBD WRITE", map[string]any{
		"peer": peer.String(), "sector": sectorNr, "count": sectorCount})

	s.mu.Lock()
	s.write = writeState{
		peer:   peer.String(),
		offset: sectorNr * SectorSize,
		left:   sectorCount * SectorSize,
	}
	s.totalWrite += sectorCount * SectorSize
	s.mu.Unlock()
}

func (s *Server) handleWriteRDMA(p []byte, peer *net.UDPAddr) {
	_, cmdid, _ := unpackHeader(p)

	s.mu.Lock()
	st := s.write
	if st.left <= 0 || st.peer != peer.String() {
		s.mu.Unlock()
		// No CMD_WRITE handshake in progress for this peer. Writing here would
		// land data at whatever offset happened to be current -- an arbitrary
		// sector -- so drop it.
		s.cfg.Log.Debug("dropping unsolicited UDPBD WRITE_RDMA",
			map[string]any{"peer": peer.String()})
		return
	}
	s.mu.Unlock()

	blockShift, blockCount := unpackBlockType(p, 2)
	size := int(blockCount) * (1 << (blockShift + 2))
	if len(p) < 6+size {
		// Truncated payload: writing it short would misalign every following
		// block in the sequence, so drop the packet rather than corrupt.
		s.cfg.Log.Debug("dropping truncated UDPBD WRITE_RDMA",
			map[string]any{"peer": peer.String()})
		return
	}
	if int64(size) > st.left {
		size = int(st.left) // never write past the requested region
	}

	if err := s.dev.writeAt(st.offset, p[6:6+size]); err != nil {
		s.cfg.Log.Warn("UDPBD write failed",
			map[string]any{"peer": peer.String(), "error": err.Error()})
		s.mu.Lock()
		s.write = writeState{}
		s.mu.Unlock()
		s.sendWriteDone(cmdid, -1, peer)
		return
	}

	s.mu.Lock()
	s.write.offset += int64(size)
	s.write.left -= int64(size)
	finished := s.write.left <= 0
	if finished {
		s.write = writeState{}
	}
	s.mu.Unlock()

	if finished {
		// Report failure on a read-only image rather than faking success, so the
		// console does not believe a save committed when nothing was written.
		result := int32(0)
		if s.dev.readOnly {
			result = -1
		} else if err := s.dev.file.Sync(); err != nil {
			s.cfg.Log.Warn("UDPBD sync failed",
				map[string]any{"peer": peer.String(), "error": err.Error()})
			result = -1
		}
		s.sendWriteDone(cmdid, result, peer)
	}
}

func (s *Server) sendWriteDone(cmdid uint8, result int32, peer *net.UDPAddr) {
	reply := packHeader(CmdWriteDone, cmdid, (cmdid+1)&0xFF)
	reply = append(reply, le32(uint32(result))...)
	s.send(reply, peer)
}

func (s *Server) send(p []byte, peer *net.UDPAddr) {
	if _, err := s.conn.WriteToUDP(p, peer); err != nil {
		s.cfg.Log.Debug("UDPBD send failed",
			map[string]any{"peer": peer.String(), "error": err.Error()})
	}
}

func le32(v uint32) []byte {
	return []byte{byte(v), byte(v >> 8), byte(v >> 16), byte(v >> 24)}
}
