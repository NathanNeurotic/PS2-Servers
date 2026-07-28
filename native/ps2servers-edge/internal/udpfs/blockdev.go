package udpfs

import (
	"encoding/binary"
	"errors"
	"io"
	"os"
	"syscall"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/compression"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
)

// blockHandle is the handle number reserved for the shared block device. OPEN
// never returns it -- handles allocated to clients start at 1 -- so a client
// addressing handle 0 is unambiguously addressing the image.
const blockHandle int32 = 0

// blockDevice is one disk image served over UDPFS's BREAD/BWRITE opcodes.
//
// This is a different thing from the udpbd package: UDPBD is its own protocol
// on its own port, whereas this is block access carried inside UDPFS, which is
// what the Desktop server's --block-device and udpfsd's -bdpath provide. A
// client that speaks UDPFS can therefore reach a folder and an image from one
// server, without a second service.
//
// All access is positional. One image is shared by every session, so a seek
// cursor would let two consoles move each other's read position.
type blockDevice struct {
	path       string
	file       *os.File
	size       int64
	sectorSize int64
	readOnly   bool
}

func openBlockDevice(path string, sectorSize int, readOnly bool) (*blockDevice, error) {
	flag := os.O_RDWR
	if readOnly {
		flag = os.O_RDONLY
	}
	f, err := os.OpenFile(path, flag, 0o644)
	if err != nil && !readOnly {
		// An image on a read-only mount still loads games perfectly well, so
		// degrade rather than refuse to serve. Matches the Desktop server.
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
	if sectorSize <= 0 {
		sectorSize = DefaultSectorSize
	}
	return &blockDevice{
		path: path, file: f, size: info.Size(),
		sectorSize: int64(sectorSize), readOnly: readOnly,
	}, nil
}

// DefaultSectorSize is the PS2's native sector size and the value every known
// client uses. It is configurable only because the Desktop server and udpfsd
// both expose it.
const DefaultSectorSize = 512

func (b *blockDevice) sectorCount() int64 { return b.size / b.sectorSize }

func (b *blockDevice) readAt(offset int64, size int) ([]byte, error) {
	buf := make([]byte, size)
	n, err := b.file.ReadAt(buf, offset)
	if err != nil && !errors.Is(err, io.EOF) {
		return nil, err
	}
	return buf[:n], nil
}

func (b *blockDevice) writeAt(offset int64, data []byte) error {
	if b.readOnly {
		return syscall.EACCES
	}
	_, err := b.file.WriteAt(data, offset)
	return err
}

func (b *blockDevice) close() {
	if b.file != nil {
		_ = b.file.Close()
	}
}

// blockRequest parses the 16-byte header shared by BREAD and BWRITE:
// type(1) reserved(1) sector_count(2) handle(4) sector_lo(4) sector_hi(4).
func blockRequest(p []byte) (handle int32, sector int64, count int64, ok bool) {
	if len(p) < 16 {
		return 0, 0, 0, false
	}
	count = int64(binary.LittleEndian.Uint16(p[2:4]))
	handle = int32(binary.LittleEndian.Uint32(p[4:8]))
	lo := uint64(binary.LittleEndian.Uint32(p[8:12]))
	hi := uint64(binary.LittleEndian.Uint32(p[12:16]))
	return handle, int64(lo | hi<<32), count, true
}

// handleBRead answers a sector read with a ResultReply carrying the raw bytes,
// the same envelope a file READ uses.
func (s *Server) handleBRead(st *session.State, p []byte) {
	handle, sector, count, ok := blockRequest(p)
	if !ok {
		s.sendTransfer(st, result8(protocol.ResultReply, -int32(syscall.EINVAL)), nil)
		return
	}
	dev := s.blockDev
	if dev == nil || handle != blockHandle {
		// No image configured, or the client aimed a block read at a file
		// handle. Either way there are no sectors to serve.
		s.sendTransfer(st, result8(protocol.ResultReply, -int32(syscall.EBADF)), nil)
		return
	}
	total := count * dev.sectorSize
	if count <= 0 || total > maxBlockRead {
		s.sendTransfer(st, result8(protocol.ResultReply, -int32(syscall.EINVAL)), nil)
		return
	}
	if sector < 0 || sector+count > dev.sectorCount() {
		// Refuse rather than pad: a bad count would otherwise make the server
		// emit an arbitrary amount of zeroes on request.
		s.cfg.Log.Warn("BREAD out of range", map[string]any{
			"peer": st.Peer, "sector": sector, "count": count, "sectors": dev.sectorCount()})
		s.sendTransfer(st, result8(protocol.ResultReply, -int32(syscall.EINVAL)), nil)
		return
	}
	data, err := dev.readAt(sector*dev.sectorSize, int(total))
	if err != nil {
		s.cfg.Log.Warn("BREAD failed", map[string]any{"peer": st.Peer, "error": err.Error()})
		s.sendTransfer(st, result8(protocol.ResultReply, errno(err)), nil)
		return
	}
	s.stats.bread.Add(1)
	s.stats.bytesRead.Add(int64(len(data)))
	s.cfg.Log.Debug("BREAD", map[string]any{
		"peer": st.Peer, "sector": sector, "count": count, "bytes": len(data)})
	s.sendTransfer(st, result8(protocol.ResultReply, int32(len(data))), data)
}

// handleBWriteRequest opens a block write. The chunk sequence that follows is
// the same WriteData flow a file write uses, so only the destination differs --
// completeWrite routes on st.WriteIsBlock.
func (s *Server) handleBWriteRequest(st *session.State, p []byte) {
	handle, sector, count, ok := blockRequest(p)
	if !ok {
		s.sendWriteDone(st, -int32(syscall.EINVAL))
		return
	}
	if s.cfg.ReadOnly {
		s.sendWriteDone(st, -int32(syscall.EACCES))
		return
	}
	dev := s.blockDev
	if dev == nil || handle != blockHandle {
		s.sendWriteDone(st, -int32(syscall.EBADF))
		return
	}
	if dev.readOnly {
		// Report failure rather than accept and discard, so the console does
		// not believe a save committed.
		s.sendWriteDone(st, -int32(syscall.EACCES))
		return
	}
	total := count * dev.sectorSize
	if count <= 0 || total > maxWriteBytes {
		s.sendWriteDone(st, -int32(syscall.EFBIG))
		return
	}
	if sector < 0 || sector+count > dev.sectorCount() {
		s.cfg.Log.Warn("BWRITE out of range", map[string]any{
			"peer": st.Peer, "sector": sector, "count": count})
		s.sendWriteDone(st, -int32(syscall.EINVAL))
		return
	}
	s.stats.bwrite.Add(1)
	st.WriteActive = true
	st.WriteIsBlock = true
	st.WriteHandle = blockHandle
	st.WriteOffset = sector * dev.sectorSize
	st.WriteBuffer = make([]byte, 0, total)
	st.WriteTotalChunks = 0
	st.WriteReceivedChunk = 0
	s.cfg.Log.Debug("BWRITE", map[string]any{
		"peer": st.Peer, "sector": sector, "count": count})
	if len(p) > 16 {
		s.handleWriteData(st, p[16:])
		return
	}
	s.sendACK(st, true)
}

// completeBlockWrite flushes an assembled block write to the image.
func (s *Server) completeBlockWrite(st *session.State, buf []byte, offset int64) {
	dev := s.blockDev
	if dev == nil {
		s.sendWriteDone(st, -int32(syscall.EBADF))
		return
	}
	if err := dev.writeAt(offset, buf); err != nil {
		s.cfg.Log.Warn("BWRITE failed", map[string]any{"peer": st.Peer, "error": err.Error()})
		s.sendWriteDone(st, errno(err))
		return
	}
	if err := dev.file.Sync(); err != nil {
		s.cfg.Log.Warn("BWRITE sync failed", map[string]any{"peer": st.Peer, "error": err.Error()})
		s.sendWriteDone(st, errno(err))
		return
	}
	s.stats.bytesWritten.Add(int64(len(buf)))
	s.cfg.Log.Debug("BWRITE ok", map[string]any{"peer": st.Peer, "bytes": len(buf)})
	s.sendWriteDone(st, int32(len(buf)))
}

// maxBlockRead bounds one BREAD. The sector count is 16-bit, so a client could
// otherwise ask for 65535 sectors -- 32 MiB -- in a single request.
const maxBlockRead = 8 << 20

// openImage opens a game image with the configured cache size, and without the
// decompression layer when --no-compression is set.
//
// With compression off a CSO/ZSO container is served as the raw bytes on disk.
// That is what the flag means on the Desktop server and udpfsd: it is a
// diagnostic for comparing against an undecoded transfer, not a way to make a
// console read a compressed image -- the PS2 cannot interpret those bytes.
func (s *Server) openImage(path string) (*compression.Image, error) {
	if s.cfg.NoCompression {
		return compression.OpenRaw(path)
	}
	cache := s.cfg.CompressionCache
	if cache == 0 {
		cache = compression.DefaultCacheBlocks
	}
	return compression.OpenCached(path, cache)
}

// compressedSiblingsEnabled reports whether a missing .iso may be satisfied by
// a CSO/ZSO container of the same name. With --no-compression it may not:
// answering with container bytes under an .iso name would hand the console
// something it cannot read.
func (s *Server) compressedSiblingsEnabled() bool { return !s.cfg.NoCompression }
