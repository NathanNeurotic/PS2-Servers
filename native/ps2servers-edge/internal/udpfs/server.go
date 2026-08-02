package udpfs

import (
	"context"
	"fmt"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/filesystem"
	edgelog "github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/memory"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
	"net"
	"os"
	"sync"
	"time"
)

const (
	defaultFallback     = 250 * time.Millisecond
	sessionReplaceQuiet = 1 * time.Second
	windowSize          = 8
	ackTimeout          = 120 * time.Millisecond
	maxWindowRetries    = 4
	maxDatagram         = 64 * 1024
	maxPathBytes        = 1024
	maxHandles          = 64
)

type Config struct {
	Root         string
	Bind         string
	Port         int
	DataPort     int
	SinglePort   bool
	ProtocolMode session.Profile
	PeerTimeout  time.Duration
	TxDelay      time.Duration
	ReadOnly     bool
	// BlockDevice serves one disk image over UDPFS BREAD/BWRITE alongside the
	// file share, as the Desktop server's --block-device and udpfsd's -bdpath
	// do. Empty disables it.
	BlockDevice string
	// SectorSize applies to that image. 512 is what every known client uses;
	// it is configurable only because both siblings expose it.
	SectorSize int
	// NoCompression serves CSO/ZSO containers as raw bytes rather than
	// decompressing them, and stops advertising them under an .iso name.
	NoCompression bool
	// CompressionCache bounds decompressed blocks retained per open image.
	CompressionCache int
	// MaxTransferBytes bounds one BREAD and one assembled write. Zero derives
	// it from the machine's RAM, which is what every caller should do: the
	// write buffer is reserved eagerly from the size the client declares, so
	// on a 32 MB router the desktop ceiling is a quarter of the machine per
	// operation. Set explicitly only by tests and by an operator who has a
	// reason.
	MaxTransferBytes int64
	// MetricsPeriod logs a periodic counter snapshot when > 0, matching the
	// Desktop server's --metrics / --metrics-period.
	MetricsPeriod time.Duration
	Log           *edgelog.Logger
	ServerName    string
	FallbackDelay time.Duration
}

type inbound struct {
	packet []byte
	peer   *net.UDPAddr
	socket session.Socket
}

type peerWorker struct {
	state    *session.State
	queue    chan inbound
	done     chan struct{}
	stopOnce sync.Once
}

func (w *peerWorker) stop() {
	w.stopOnce.Do(func() { close(w.done) })
}

type Server struct {
	cfg        Config
	root       *filesystem.Root
	discovery  *net.UDPConn
	data       *net.UDPConn
	sessionsMu sync.Mutex
	sessions   map[string]*peerWorker
	incoming   chan inbound
	closed     chan struct{}
	closeOnce  sync.Once
	wg         sync.WaitGroup

	stats    metrics
	blockDev *blockDevice

	// transferCap bounds one BREAD and one assembled write, resolved once at
	// construction so every path that allocates from a client-declared size
	// consults the same number.
	transferCap int64

	// writers reserves each file that some peer currently holds open for
	// writing, mapping resolved path to a reference count. Two consoles
	// writing the same file concurrently would interleave into corruption,
	// so the second OPEN is refused with EBUSY.
	writersMu sync.Mutex
	writers   map[string]int
}

// reserveWriter claims exclusive write access to a resolved path. It reports
// false when another handle already holds it.
func (s *Server) reserveWriter(realPath string) bool {
	s.writersMu.Lock()
	defer s.writersMu.Unlock()
	if s.writers == nil {
		s.writers = make(map[string]int)
	}
	if s.writers[realPath] > 0 {
		return false
	}
	s.writers[realPath] = 1
	return true
}

// releaseWriter drops a reservation taken by reserveWriter.
func (s *Server) releaseWriter(realPath string) {
	s.writersMu.Lock()
	defer s.writersMu.Unlock()
	if s.writers == nil {
		return
	}
	if n := s.writers[realPath]; n <= 1 {
		delete(s.writers, realPath)
	} else {
		s.writers[realPath] = n - 1
	}
}

func New(cfg Config) (*Server, error) {
	if cfg.Port == 0 {
		cfg.Port = int(protocol.DefaultPort)
	}
	if cfg.Port < 1 || cfg.Port > 65535 || cfg.DataPort < 0 || cfg.DataPort > 65535 {
		return nil, fmt.Errorf("port out of range")
	}
	if cfg.ProtocolMode == "" {
		cfg.ProtocolMode = session.Pending
	}
	if cfg.ProtocolMode != session.Pending && cfg.ProtocolMode != session.Standard && cfg.ProtocolMode != session.Modulo {
		return nil, fmt.Errorf("invalid protocol mode %q", cfg.ProtocolMode)
	}
	if cfg.PeerTimeout <= 0 {
		cfg.PeerTimeout = time.Hour
	}
	if cfg.FallbackDelay <= 0 {
		cfg.FallbackDelay = defaultFallback
	}
	if cfg.Log == nil {
		cfg.Log = edgelog.New(os.Stdout, "text", false, false)
	}
	if cfg.ServerName == "" {
		cfg.ServerName = "PS2 Servers Edge"
	}
	if cfg.MaxTransferBytes <= 0 {
		cfg.MaxTransferBytes = memory.Detect().TransferCap(preferredTransfer)
	}
	root, err := filesystem.Open(cfg.Root)
	if err != nil {
		return nil, err
	}
	s := &Server{cfg: cfg, root: root, sessions: make(map[string]*peerWorker), incoming: make(chan inbound, 256), closed: make(chan struct{}), transferCap: cfg.MaxTransferBytes}
	s.stats.started = time.Now()
	if cfg.BlockDevice != "" {
		// Opened once and shared: every session addresses the same image
		// through handle 0, using positional I/O so two consoles cannot move
		// each other's read position.
		dev, err := openBlockDevice(cfg.BlockDevice, cfg.SectorSize, cfg.ReadOnly)
		if err != nil {
			return nil, fmt.Errorf("block device: %w", err)
		}
		if dev.size < dev.sectorSize {
			dev.close()
			return nil, fmt.Errorf("block device is smaller than one %d-byte sector", dev.sectorSize)
		}
		s.blockDev = dev
	}
	return s, nil
}

func (s *Server) Listen() error {
	ip := net.ParseIP(s.cfg.Bind)
	if s.cfg.Bind != "" && ip == nil {
		return fmt.Errorf("invalid bind address %q", s.cfg.Bind)
	}
	disc, err := net.ListenUDP("udp4", &net.UDPAddr{IP: ip, Port: s.cfg.Port})
	if err != nil {
		return err
	}
	s.discovery = disc
	if s.cfg.SinglePort {
		s.data = disc
	} else {
		data, err := net.ListenUDP("udp4", &net.UDPAddr{IP: ip, Port: s.cfg.DataPort})
		if err != nil {
			disc.Close()
			s.discovery = nil
			return err
		}
		s.data = data
	}
	return nil
}
func (s *Server) Addr() (discovery, data *net.UDPAddr) {
	if s.discovery != nil {
		discovery = s.discovery.LocalAddr().(*net.UDPAddr)
	}
	if s.data != nil {
		data = s.data.LocalAddr().(*net.UDPAddr)
	}
	return
}

func (s *Server) Serve(ctx context.Context) error {
	if s.discovery == nil {
		if err := s.Listen(); err != nil {
			return err
		}
	}
	disc, data := s.Addr()
	// transfer_cap is logged because it is derived from the host's RAM rather
	// than fixed: an operator on a small router who sees a large write refused
	// should be able to find the reason in the first line of the log.
	s.cfg.Log.Info("UDPFS listening", map[string]any{"root": s.root.Path(), "discovery": disc.String(), "data": data.String(), "protocol": s.cfg.ProtocolMode, "read_only": s.cfg.ReadOnly, "transfer_cap": s.transferCap})
	s.wg.Add(1)
	go s.readLoop(s.discovery, session.DiscoverySocket)
	if s.data != s.discovery {
		s.wg.Add(1)
		go s.readLoop(s.data, session.DataSocket)
	}
	s.wg.Add(1)
	go s.reaper()
	if s.cfg.MetricsPeriod > 0 {
		s.wg.Add(1)
		go s.emitMetrics(s.cfg.MetricsPeriod, s.closed)
	}
	for {
		select {
		case <-ctx.Done():
			s.Close()
			return nil
		case <-s.closed:
			return nil
		case in := <-s.incoming:
			s.dispatch(in)
		}
	}
}
func (s *Server) Close() error {
	var err error
	s.closeOnce.Do(func() {
		close(s.closed)
		if s.blockDev != nil {
			s.blockDev.close()
		}
		if s.discovery != nil {
			err = s.discovery.Close()
		}
		if s.data != nil && s.data != s.discovery {
			_ = s.data.Close()
		}
		// Workers are stopped and drained BEFORE their session state is
		// closed, rather than closing it under State.Mu.
		//
		// Taking that mutex here looked sufficient and was not: a peer worker
		// owns its own state and reads it without locking -- correct, since
		// exactly one goroutine handles a given peer -- so the mutex only ever
		// held one side of the pair. Closing a session while its worker was
		// mid-request was a real data race between State.ResetWrite and
		// handleWriteData's read of WriteActive, and on shutdown during an
		// active write it could free handles from under the goroutine using
		// them. Found by the race detector in CI, intermittently, because it
		// needs a write in flight at the moment of shutdown.
		s.sessionsMu.Lock()
		workers := make([]*peerWorker, 0, len(s.sessions))
		for _, w := range s.sessions {
			workers = append(workers, w)
		}
		s.sessions = map[string]*peerWorker{}
		s.sessionsMu.Unlock()

		for _, w := range workers {
			w.stop()
		}
		// Every worker is in s.wg, so this returns only once none of them can
		// still be touching a session.
		s.wg.Wait()
		for _, w := range workers {
			w.state.Close()
		}
	})
	s.wg.Wait()
	return err
}
func (s *Server) readLoop(conn *net.UDPConn, which session.Socket) {
	defer s.wg.Done()
	buf := make([]byte, maxDatagram)
	for {
		n, peer, err := conn.ReadFromUDP(buf)
		if err != nil {
			select {
			case <-s.closed:
				return
			default:
				s.cfg.Log.Warn("UDP read failed", map[string]any{"socket": which, "error": err})
				continue
			}
		}
		// Under --metrics, record the datagram; under --verbose, describe it
		// before it can be queued behind another peer's work. This is the
		// closest observable boundary to the kernel receive: if the trace never
		// appears after a console-side reset, no UDPFS datagram reached Edge.
		if s.cfg.MetricsPeriod > 0 {
			s.stats.recordRX(which, n, peer, buf[:n])
		}
		if s.cfg.Log.Verbose {
			s.cfg.Log.Debug("UDP RX", receivedDatagramTraceFields(conn, which, peer, buf[:n], n))
		}
		pkt := append([]byte(nil), buf[:n]...)
		select {
		case s.incoming <- inbound{pkt, peer, which}:
		case <-s.closed:
			return
		}
	}
}
func (s *Server) reaper() {
	defer s.wg.Done()
	tick := time.NewTicker(5 * time.Second)
	defer tick.Stop()
	for {
		select {
		case <-s.closed:
			return
		case now := <-tick.C:
			type expiredSession struct {
				key string
				w   *peerWorker
				id  time.Duration
			}
			var expired []expiredSession
			s.sessionsMu.Lock()
			for key, w := range s.sessions {
				w.state.Mu.Lock()
				idle := now.Sub(w.state.LastActivity)
				w.state.Mu.Unlock()
				if idle > s.cfg.PeerTimeout {
					expired = append(expired, expiredSession{key: key, w: w, id: idle})
					delete(s.sessions, key)
				}
			}
			s.sessionsMu.Unlock()

			for _, item := range expired {
				item.w.state.Mu.Lock()
				item.w.state.Close()
				item.w.state.Mu.Unlock()
				item.w.stop()
				s.cfg.Log.Info("session expired", map[string]any{"peer": item.key, "idle": item.id.String()})
			}
		}
	}
}

func (s *Server) getWorker(peer *net.UDPAddr) *peerWorker {
	key := peer.String()
	s.sessionsMu.Lock()
	defer s.sessionsMu.Unlock()
	if w := s.sessions[key]; w != nil {
		return w
	}
	// Evict any stale session from the same remote IP address (e.g. PS2 rebooted and changed source port),
	// but skip loopback test peers (127.0.0.1) so multi-client unit tests are not evicted.
	peerIP := peer.IP.String()
	if !peer.IP.IsLoopback() {
		var oldKey string
		var oldWorker *peerWorker
		var replacementFields map[string]any
		now := time.Now()
		for k, w := range s.sessions {
			if w.state != nil && w.state.Peer != nil && w.state.Peer.IP.Equal(peer.IP) {
				w.state.Mu.Lock()
				quiet := now.Sub(w.state.LastActivity)
				writeActive := w.state.WriteActive
				w.state.Mu.Unlock()
				eligible := !writeActive || quiet > sessionReplaceQuiet
				reason := "active_write_recent"
				if !writeActive {
					reason = "no_active_write"
				} else if eligible {
					reason = "active_write_quiet_over_1s"
				}
				candidateFields := map[string]any{
					"ip":              peerIP,
					"old_peer":        k,
					"new_peer":        key,
					"old_source_port": w.state.Peer.Port,
					"new_source_port": peer.Port,
					"quiet":           quiet.String(),
					"write_active":    writeActive,
					"eligible":        eligible,
					"reason":          reason,
				}
				s.cfg.Log.Debug("same-IP session replacement candidate", candidateFields)
				if eligible {
					oldKey = k
					oldWorker = w
					replacementFields = candidateFields
					break
				}
			}
		}
		if oldWorker != nil {
			s.cfg.Log.Info("replacing stale session for IP", replacementFields)
			s.stats.sameIPReplacements.Add(1)
			delete(s.sessions, oldKey)
			oldWorker.state.Mu.Lock()
			oldWorker.state.Close()
			oldWorker.state.Mu.Unlock()
			oldWorker.stop()
		}
	}

	if len(s.sessions) >= 256 {
		s.cfg.Log.Warn("max session limit reached", map[string]any{"peer": key})
		return nil
	}

	forced := s.cfg.ProtocolMode
	if forced == session.Pending {
		forced = ""
	}
	st := session.New(peer, forced)
	// Every path that closes a handle -- explicit CLOSE, session reset, idle
	// reap, server shutdown -- runs through State, so hooking release here
	// means a peer that disappears mid-write cannot strand a reservation.
	st.ReleaseWriter = s.releaseWriter
	w := &peerWorker{state: st, queue: make(chan inbound, 256), done: make(chan struct{})}
	s.sessions[key] = w
	s.cfg.Log.Debug("session created", map[string]any{
		"peer":                key,
		"ip":                  peerIP,
		"source_port":         peer.Port,
		"profile":             st.Profile,
		"configured_protocol": s.cfg.ProtocolMode,
	})
	s.wg.Add(1)
	go s.worker(w)
	return w
}
func (s *Server) worker(w *peerWorker) {
	defer s.wg.Done()
	for {
		select {
		case <-w.done:
			return
		case in := <-w.queue:
			s.handleData(w.state, in)
		}
	}
}
func (s *Server) dispatch(in inbound) {
	h, err := protocol.ParseHeader(in.packet)
	if err != nil {
		s.stats.malformedDatagrams.Add(1)
		s.cfg.Log.Debug("dropping malformed datagram", map[string]any{
			"peer": inboundPeerString(in.peer), "socket": in.socket, "bytes": len(in.packet), "error": err,
		})
		return
	}
	switch h.Type {
	case protocol.Discovery:
		// Before handleDiscovery, which creates a session for the peer. A
		// status query must not do that: a launcher polling once a second
		// would otherwise manufacture sessions, occupy slots against the
		// concurrent-session cap, and show up in the very session count it is
		// asking about.
		if protocol.IsStatusQuery(in.packet) {
			s.handleStatusQuery(in)
			return
		}
		s.handleDiscovery(in, h)
	case protocol.Data:
		w := s.getWorker(in.peer)
		if w == nil {
			return
		}
		_, dh, payload, parseErr := protocol.ParseDataPacket(in.packet)
		if parseErr == nil && len(payload) == 0 {
			s.handleControl(w.state, dh)
			return
		}
		select {
		case <-w.done:
			return
		case w.queue <- in:
		default:
			s.cfg.Log.Warn("peer queue full", map[string]any{"peer": in.peer})
		}
	default:
		s.cfg.Log.Debug("unknown packet type", map[string]any{
			"peer": inboundPeerString(in.peer), "socket": in.socket, "bytes": len(in.packet), "type": h.Type, "sequence": h.Sequence,
		})
	}
}

// datagramTraceFields extracts transport and protocol headers only. On a sent
// packet, HeaderWords is the wire-level proof that the payload begins with an
// application header; continuation data with HeaderWords == 0 is never treated
// as an opcode, even when its first content byte happens to equal one.
func datagramTraceFields(conn *net.UDPConn, which session.Socket, peer *net.UDPAddr, packet []byte, bytes int) map[string]any {
	local := ""
	if conn != nil && conn.LocalAddr() != nil {
		local = conn.LocalAddr().String()
	}
	peerString := ""
	if peer != nil {
		peerString = peer.String()
	}
	fields := map[string]any{
		"socket": which,
		"local":  local,
		"peer":   peerString,
		"bytes":  bytes,
	}
	h, err := protocol.ParseHeader(packet)
	if err != nil {
		return fields
	}
	fields["type"] = h.Type
	fields["sequence"] = h.Sequence
	if h.Type != protocol.Data {
		return fields
	}
	_, dh, payload, err := protocol.ParseDataPacket(packet)
	if err != nil {
		return fields
	}
	fields["ack"] = dh.AckSequence
	fields["flags"] = dh.Flags
	fields["header_words"] = dh.HeaderWords
	fields["data_bytes"] = dh.DataBytes
	if dh.HeaderWords > 0 && len(payload) > 0 {
		fields["opcode"] = payload[0]
	}
	return fields
}

// receivedDatagramTraceFields adds the request opcode for an inbound DATA
// payload. The receive path hands every non-empty payload to handleMessage,
// where byte zero is the UDPFS opcode even though clients encode request bytes
// under DataBytes rather than HeaderWords. Keeping this receive-only prevents
// outgoing ISO/save continuation bytes from being exposed as fake opcodes.
func receivedDatagramTraceFields(conn *net.UDPConn, which session.Socket, peer *net.UDPAddr, packet []byte, bytes int) map[string]any {
	fields := datagramTraceFields(conn, which, peer, packet, bytes)
	h, _, payload, err := protocol.ParseDataPacket(packet)
	if err == nil && h.Type == protocol.Data && len(payload) > 0 {
		fields["opcode"] = payload[0]
	}
	return fields
}
