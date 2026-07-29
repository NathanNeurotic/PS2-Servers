package udpfs

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/compression"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/filesystem"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"
)

func (s *Server) handleMessage(st *session.State, p []byte) {
	// Counters are incremented at dispatch so one request counts once,
	// regardless of how a handler exits. WriteData is not counted separately:
	// a write is one operation however many chunks carry it, which is how the
	// Desktop server counts it too.
	switch protocol.MessageType(p[0]) {
	case protocol.OpenRequest:
		s.stats.open.Add(1)
		s.handleOpen(st, p)
	case protocol.CloseRequest:
		s.stats.closeOp.Add(1)
		s.handleClose(st, p)
	case protocol.ReadRequest:
		s.stats.read.Add(1)
		s.handleRead(st, p)
	case protocol.WriteRequest:
		s.stats.write.Add(1)
		s.handleWriteRequest(st, p)
	case protocol.WriteData:
		s.handleWriteData(st, p)
	case protocol.BReadRequest:
		s.stats.bread.Add(1)
		s.handleBRead(st, p)
	case protocol.BWriteRequest:
		s.stats.bwrite.Add(1)
		s.handleBWriteRequest(st, p)
	case protocol.SeekRequest:
		s.stats.lseek.Add(1)
		s.handleSeek(st, p)
	case protocol.DReadRequest:
		s.stats.dread.Add(1)
		s.handleDRead(st, p)
	case protocol.GetStatRequest:
		s.stats.getstat.Add(1)
		s.handleGetStat(st, p)
	default:
		s.cfg.Log.Debug("unknown UDPFS opcode", map[string]any{"peer": st.Peer, "opcode": p[0]})
	}
}
func cString(b []byte) (string, error) {
	if idx := bytes.IndexByte(b, 0); idx >= 0 {
		b = b[:idx]
	}
	if len(b) > maxPathBytes {
		return "", fmt.Errorf("path too long")
	}
	return string(b), nil
}
func (s *Server) resolveImagePath(client string) (string, error) {
	p, err := s.root.Resolve(client, false)
	if err == nil {
		return p, nil
	}
	if !errors.Is(err, os.ErrNotExist) {
		return "", err
	}
	// With --no-compression a missing .iso must stay missing: answering with a
	// CSO/ZSO container under an .iso name would hand the console bytes it
	// cannot read.
	if !s.compressedSiblingsEnabled() {
		return "", err
	}
	if !strings.HasSuffix(strings.ToLower(client), ".iso") {
		return "", err
	}
	base := client[:len(client)-4]
	for _, ext := range []string{".cso", ".ciso", ".zso", ".ziso"} {
		candidate, er := s.root.Resolve(base+ext, false)
		if er == nil {
			return candidate, nil
		}
	}
	return "", err
}
func (s *Server) handleOpen(st *session.State, p []byte) {
	if len(p) < 8 {
		s.sendOpen(st, -int32(syscall.EINVAL), 0, 0)
		return
	}
	isDir := p[1] != 0
	flags := binary.LittleEndian.Uint16(p[2:4])
	path, err := cString(p[8:])
	if err != nil {
		s.sendOpen(st, -int32(syscall.ENAMETOOLONG), 0, 0)
		return
	}
	// PS2 FIO access bits: 0x01 read, 0x02 write, 0x03 read+write.
	wantWrite := flags&0x02 != 0
	if wantWrite && s.cfg.ReadOnly {
		s.sendOpen(st, -int32(syscall.EACCES), 0, 0)
		return
	}
	if len(st.Handles) >= maxHandles {
		s.sendOpen(st, -int32(syscall.EMFILE), 0, 0)
		return
	}
	id := st.NextHandle
	st.NextHandle++
	if isDir {
		entries, err := s.root.List(path)
		if err != nil {
			s.sendOpen(st, errno(err), 0, 0)
			return
		}
		st.Handles[id] = &session.Handle{Directory: entries}
		s.sendOpen(st, id, 0x1000, 0)
		return
	}
	if wantWrite {
		s.openForWrite(st, id, path, flags)
		return
	}
	real, err := s.resolveImagePath(path)
	if err != nil {
		s.sendOpen(st, errno(err), 0, 0)
		return
	}
	img, err := s.openImage(real)
	if err != nil {
		s.sendOpen(st, errno(err), 0, 0)
		return
	}
	st.Handles[id] = &session.Handle{Reader: img, Closer: img}
	s.sendOpen(st, id, 0x2000, uint64(img.Size()))
}

// openForWrite handles an OPEN carrying the write access bit.
//
// Writes deliberately never go through the compression layer: the CSO/ZSO
// readers present a decompressed view, so a client writing at a decompressed
// offset would land bytes at the wrong place in the container and destroy the
// image. A write to a compressed file is refused outright, and a compressed
// sibling is never substituted for a missing .iso the way the read path does.
func (s *Server) openForWrite(st *session.State, id int32, path string, flags uint16) {
	create := flags&0x0200 != 0
	truncate := flags&0x0400 != 0
	appendMode := flags&0x0100 != 0

	if isCompressedName(path) {
		s.cfg.Log.Warn("refusing write to compressed image", map[string]any{"peer": st.Peer, "path": path})
		s.sendOpen(st, -int32(syscall.EACCES), 0, 0)
		return
	}
	real, err := s.root.Resolve(path, create)
	if err != nil {
		s.sendOpen(st, errno(err), 0, 0)
		return
	}
	// Resolve(allowMissing) permits a path that does not exist yet; without
	// O_CREAT that is still ENOENT rather than an implicit create.
	if _, statErr := os.Stat(real); statErr != nil {
		if !os.IsNotExist(statErr) {
			s.sendOpen(st, errno(statErr), 0, 0)
			return
		}
		if !create {
			s.sendOpen(st, -int32(syscall.ENOENT), 0, 0)
			return
		}
	}
	mode := os.O_RDWR
	if create {
		mode |= os.O_CREATE
	}
	if truncate {
		mode |= os.O_TRUNC
	}
	if appendMode {
		mode |= os.O_APPEND
	}
	if !s.reserveWriter(real) {
		s.cfg.Log.Warn("file already open for writing by another peer", map[string]any{"peer": st.Peer, "path": path})
		s.sendOpen(st, -int32(syscall.EBUSY), 0, 0)
		return
	}
	f, err := os.OpenFile(real, mode, 0o644)
	if err != nil {
		s.releaseWriter(real)
		s.sendOpen(st, errno(err), 0, 0)
		return
	}
	size := int64(0)
	if info, err := f.Stat(); err == nil {
		size = info.Size()
	}
	// *os.File is both ReadSeeker and Writer, so read and seek on this handle
	// keep working through the existing paths.
	st.Handles[id] = &session.Handle{Reader: f, Writer: f, Closer: f, RealPath: real}
	s.cfg.Log.Debug("OPEN for write", map[string]any{"peer": st.Peer, "path": path, "handle": id})
	s.sendOpen(st, id, 0x2000, uint64(size))
}

func isCompressedName(name string) bool {
	lower := strings.ToLower(name)
	for _, ext := range []string{".cso", ".ciso", ".zso", ".ziso", ".chd"} {
		if strings.HasSuffix(lower, ext) {
			return true
		}
	}
	return false
}
func (s *Server) sendOpen(st *session.State, result int32, mode uint32, size uint64) {
	b := make([]byte, 36)
	b[0] = byte(protocol.OpenReply)
	binary.LittleEndian.PutUint32(b[4:], uint32(result))
	binary.LittleEndian.PutUint32(b[8:], mode)
	binary.LittleEndian.PutUint32(b[12:], uint32(size))
	binary.LittleEndian.PutUint32(b[16:], uint32(size>>32))
	s.sendSimple(st, b)
}
func (s *Server) handleClose(st *session.State, p []byte) {
	if len(p) < 8 {
		s.sendSimple(st, result8(protocol.CloseReply, -int32(syscall.EINVAL)))
		return
	}
	id := int32(binary.LittleEndian.Uint32(p[4:8]))
	// CloseHandle also drops any single-writer reservation and discards a
	// partially assembled write aimed at this handle.
	if !st.CloseHandle(id) {
		s.sendSimple(st, result8(protocol.CloseReply, -int32(syscall.EBADF)))
		return
	}
	s.sendSimple(st, result8(protocol.CloseReply, 0))
}
func (s *Server) handleRead(st *session.State, p []byte) {
	if len(p) < 12 {
		s.sendTransfer(st, result8(protocol.ResultReply, -int32(syscall.EINVAL)), nil)
		return
	}
	id := int32(binary.LittleEndian.Uint32(p[4:8]))
	size := binary.LittleEndian.Uint32(p[8:12])
	if size > 64<<10 {
		s.sendTransfer(st, result8(protocol.ResultReply, -int32(syscall.EINVAL)), nil)
		return
	}
	h := st.Handles[id]
	if h == nil || h.Reader == nil {
		s.sendTransfer(st, result8(protocol.ResultReply, -int32(syscall.EBADF)), nil)
		return
	}
	buf := make([]byte, size)
	n, err := io.ReadFull(h.Reader, buf)
	if err == io.ErrUnexpectedEOF {
		err = nil
	}
	if err != nil && err != io.EOF {
		s.sendTransfer(st, result8(protocol.ResultReply, errno(err)), nil)
		return
	}
	s.stats.bytesRead.Add(int64(n))
	s.sendTransfer(st, result8(protocol.ResultReply, int32(n)), buf[:n])
}

// preferredTransfer caps one assembled write and one BREAD on a machine with
// memory to spare. The WRITE_REQ size field is 32-bit and the BREAD sector
// count is 16-bit, so without a ceiling a peer could announce 4 GiB, or ask
// for 65535 sectors, and make the server buffer it before a single byte is
// validated. 8 MiB is far above any real PS2 transfer (saves are kilobytes)
// while keeping the worst case per peer bounded.
//
// It is the ceiling, not the answer: Server.transferCap is this value or the
// smaller one the machine's RAM implies, because the write buffer is reserved
// eagerly from the declared size and 8 MiB is a quarter of a 32 MB router.
// See internal/memory.
const preferredTransfer = 8 << 20

// handleWriteRequest begins a write: handle plus total byte count, optionally
// with the first WRITE_DATA chunk appended inline to the same datagram.
func (s *Server) handleWriteRequest(st *session.State, p []byte) {
	if len(p) < 12 {
		s.sendWriteDone(st, -int32(syscall.EINVAL))
		return
	}
	id := int32(binary.LittleEndian.Uint32(p[4:8]))
	size := binary.LittleEndian.Uint32(p[8:12])
	if s.cfg.ReadOnly {
		s.cfg.Log.Debug("WRITE refused, server is read-only", map[string]any{"peer": st.Peer, "handle": id})
		s.sendWriteDone(st, -int32(syscall.EACCES))
		return
	}
	// int64(size), not a narrowing of the cap: size is the 32-bit field off the
	// wire and int is 32-bit on the MIPS routers this ships to, so comparing in
	// int would be the one platform where the check could wrap.
	if int64(size) > s.transferCap {
		s.cfg.Log.Warn("WRITE refused, size over cap", map[string]any{"peer": st.Peer, "size": size, "cap": s.transferCap})
		s.sendWriteDone(st, -int32(syscall.EFBIG))
		return
	}
	h := st.Handles[id]
	if h == nil || h.Directory != nil {
		s.sendWriteDone(st, -int32(syscall.EBADF))
		return
	}
	// A handle opened read-only must not become writable just because the
	// client asked; the access check belongs at OPEN and is enforced again here.
	if h.Writer == nil {
		s.sendWriteDone(st, -int32(syscall.EACCES))
		return
	}
	st.WriteActive = true
	st.WriteHandle = id
	st.WriteBuffer = make([]byte, 0, size)
	st.WriteTotalChunks = 0
	st.WriteReceivedChunk = 0
	if len(p) > 12 {
		s.handleWriteData(st, p[12:])
		return
	}
	s.sendACK(st, true)
}

// handleWriteData accumulates one chunk. msg starts at the WRITE_DATA type
// byte, matching how the reference server parses both the standalone and the
// inline form.
func (s *Server) handleWriteData(st *session.State, msg []byte) {
	if len(msg) < 8 {
		s.sendACK(st, true)
		return
	}
	chunkNr := binary.LittleEndian.Uint16(msg[2:4])
	chunkSize := binary.LittleEndian.Uint16(msg[4:6])
	totalChunks := binary.LittleEndian.Uint16(msg[6:8])
	if !st.WriteActive {
		// Chunk with no WRITE_REQ in front of it: ignore rather than write
		// somewhere arbitrary.
		s.sendACK(st, true)
		return
	}
	body := msg[8:]
	if int(chunkSize) > len(body) {
		chunkSize = uint16(len(body))
	}
	if chunkNr != st.WriteReceivedChunk {
		// Out-of-order chunk. Concatenating it would silently corrupt the
		// file, so drop the whole sequence and let the client retry.
		s.cfg.Log.Warn("WRITE chunk out of order", map[string]any{
			"peer": st.Peer, "expected": st.WriteReceivedChunk, "got": chunkNr})
		st.ResetWrite()
		s.sendWriteDone(st, -int32(syscall.EIO))
		return
	}
	if int64(len(st.WriteBuffer))+int64(chunkSize) > s.transferCap {
		st.ResetWrite()
		s.sendWriteDone(st, -int32(syscall.EFBIG))
		return
	}
	// A BWRITE declared its length up front, so stop the moment the chunks
	// exceed it rather than buffering to maxWriteBytes first. completeBlockWrite
	// would reject the result anyway; refusing here means a client that declares
	// one sector cannot make the server hold 8 MiB on its behalf.
	if st.WriteExpectedLen > 0 &&
		int64(len(st.WriteBuffer))+int64(chunkSize) > st.WriteExpectedLen {
		s.cfg.Log.Warn("BWRITE overran its declared length", map[string]any{
			"peer": st.Peer, "declared": st.WriteExpectedLen})
		st.ResetWrite()
		s.sendWriteDone(st, -int32(syscall.EINVAL))
		return
	}
	st.WriteBuffer = append(st.WriteBuffer, body[:chunkSize]...)
	st.WriteTotalChunks = totalChunks
	st.WriteReceivedChunk++
	if totalChunks == 0 || st.WriteReceivedChunk < totalChunks {
		s.sendACK(st, true)
		return
	}
	s.completeWrite(st)
}

// completeWrite flushes the assembled buffer to the handle in one call.
func (s *Server) completeWrite(st *session.State) {
	id := st.WriteHandle
	buf := st.WriteBuffer
	isBlock := st.WriteIsBlock
	offset := st.WriteOffset
	expected := st.WriteExpectedLen
	st.ResetWrite()
	if isBlock {
		s.completeBlockWrite(st, buf, offset, expected)
		return
	}
	h := st.Handles[id]
	if h == nil || h.Writer == nil {
		s.sendWriteDone(st, -int32(syscall.EBADF))
		return
	}
	n, err := h.Writer.Write(buf)
	if err != nil {
		s.cfg.Log.Warn("WRITE failed", map[string]any{"peer": st.Peer, "handle": id, "error": err.Error()})
		s.sendWriteDone(st, errno(err))
		return
	}
	// Push to the OS now. A console yanked mid-session is the normal way this
	// server loses a peer, and an unflushed save is a lost save.
	if f, ok := h.Writer.(*os.File); ok {
		if err := f.Sync(); err != nil {
			s.cfg.Log.Warn("WRITE sync failed", map[string]any{"peer": st.Peer, "handle": id, "error": err.Error()})
			s.sendWriteDone(st, errno(err))
			return
		}
	}
	s.stats.bytesWritten.Add(int64(n))
	s.cfg.Log.Debug("WRITE ok", map[string]any{"peer": st.Peer, "handle": id, "bytes": n})
	s.sendWriteDone(st, int32(n))
}

func (s *Server) sendWriteDone(st *session.State, result int32) {
	b := make([]byte, 8)
	b[0] = byte(protocol.WriteDone)
	binary.LittleEndian.PutUint32(b[4:], uint32(result))
	s.sendSimple(st, b)
}

func (s *Server) handleSeek(st *session.State, p []byte) {
	if len(p) < 16 {
		s.sendSeek(st, -int64(syscall.EINVAL))
		return
	}
	whence := int(p[1])
	id := int32(binary.LittleEndian.Uint32(p[4:8]))
	lo := uint64(binary.LittleEndian.Uint32(p[8:12]))
	hi := uint64(binary.LittleEndian.Uint32(p[12:16]))
	off := int64(lo | hi<<32)
	h := st.Handles[id]
	if h == nil || h.Reader == nil {
		s.sendSeek(st, -int64(syscall.EBADF))
		return
	}
	pos, err := h.Reader.Seek(off, whence)
	if err != nil {
		s.sendSeek(st, int64(errno(err)))
		return
	}
	s.sendSeek(st, pos)
}
func (s *Server) sendSeek(st *session.State, pos int64) {
	b := make([]byte, 12)
	b[0] = byte(protocol.SeekReply)
	binary.LittleEndian.PutUint64(b[4:], uint64(pos))
	s.sendSimple(st, b)
}
func (s *Server) handleDRead(st *session.State, p []byte) {
	if len(p) < 8 {
		s.sendDRead(st, -int32(syscall.EINVAL), session.DirEntry{})
		return
	}
	id := int32(binary.LittleEndian.Uint32(p[4:8]))
	h := st.Handles[id]
	if h == nil || h.Directory == nil {
		s.sendDRead(st, -int32(syscall.EBADF), session.DirEntry{})
		return
	}
	if h.Index >= len(h.Directory) {
		s.sendDRead(st, 0, session.DirEntry{})
		return
	}
	entry := h.Directory[h.Index]
	h.Index++
	// Compressed containers are listed under an .iso name with their
	// decompressed size, so the console sees what it will actually receive.
	// --no-compression turns that off: the entry keeps its real name and size.
	if s.compressedSiblingsEnabled() {
		lower := strings.ToLower(entry.Name)
		for _, ext := range []string{".cso", ".ciso", ".zso", ".ziso"} {
			if strings.HasSuffix(lower, ext) {
				entry.Name = entry.Name[:len(entry.Name)-len(ext)] + ".iso"
				// SizeOf, not openImage: this needs one number out of the
				// 24-byte header, and opening the image to get it built the
				// whole block index -- megabytes per entry, discarded
				// immediately, once for every compressed game in the folder.
				// Reachable by a console simply listing a directory, which is
				// what made it the likeliest way to exhaust a small router.
				if size, err := compression.SizeOf(entry.SourcePath); err == nil {
					entry.Size = uint64(size)
				}
				break
			}
		}
	}
	s.sendDRead(st, 1, entry)
}
func (s *Server) sendDRead(st *session.State, result int32, e session.DirEntry) {
	name := []byte(e.Name)
	if len(name) > 255 {
		name = name[:255]
	}
	b := make([]byte, 48)
	b[0] = byte(protocol.DReadReply)
	binary.LittleEndian.PutUint16(b[2:], uint16(len(name)))
	binary.LittleEndian.PutUint32(b[4:], uint32(result))
	binary.LittleEndian.PutUint32(b[8:], e.Mode)
	binary.LittleEndian.PutUint32(b[16:], uint32(e.Size))
	binary.LittleEndian.PutUint32(b[20:], uint32(e.Size>>32))
	if result > 0 {
		b = append(b, name...)
		b = append(b, 0)
		b = pad4(b)
	}
	s.sendSimple(st, b)
}
func (s *Server) handleGetStat(st *session.State, p []byte) {
	if len(p) < 4 {
		s.sendGetStat(st, -int32(syscall.EINVAL), 0, 0)
		return
	}
	path, err := cString(p[4:])
	if err != nil {
		s.sendGetStat(st, -int32(syscall.ENAMETOOLONG), 0, 0)
		return
	}
	real, err := s.resolveImagePath(path)
	if err != nil {
		s.sendGetStat(st, errno(err), 0, 0)
		return
	}
	info, err := os.Stat(real)
	if err != nil {
		s.sendGetStat(st, errno(err), 0, 0)
		return
	}
	size := uint64(info.Size())
	if ext := strings.ToLower(filepath.Ext(real)); s.compressedSiblingsEnabled() &&
		(ext == ".cso" || ext == ".ciso" || ext == ".zso" || ext == ".ziso") {
		// Header only, for the same reason as the DREAD path above.
		if n, e := compression.SizeOf(real); e == nil {
			size = uint64(n)
		}
	}
	s.sendGetStat(st, 0, filesystem.Mode(info), size)
}
func (s *Server) sendGetStat(st *session.State, result int32, mode uint32, size uint64) {
	b := make([]byte, 48)
	b[0] = byte(protocol.GetStatReply)
	binary.LittleEndian.PutUint32(b[4:], uint32(result))
	binary.LittleEndian.PutUint32(b[8:], mode)
	binary.LittleEndian.PutUint32(b[16:], uint32(size))
	binary.LittleEndian.PutUint32(b[20:], uint32(size>>32))
	s.sendSimple(st, b)
}

// Classify exposes the compatibility decision to the shared conformance tests.
func Classify(discovery, first uint16) session.Profile { return classify(discovery, first) }
