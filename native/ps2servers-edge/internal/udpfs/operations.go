package udpfs

import (
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
	switch protocol.MessageType(p[0]) {
	case protocol.OpenRequest:
		s.handleOpen(st, p)
	case protocol.CloseRequest:
		s.handleClose(st, p)
	case protocol.ReadRequest:
		s.handleRead(st, p)
	case protocol.SeekRequest:
		s.handleSeek(st, p)
	case protocol.DReadRequest:
		s.handleDRead(st, p)
	case protocol.GetStatRequest:
		s.handleGetStat(st, p)
	default:
		s.cfg.Log.Debug("unknown UDPFS opcode", map[string]any{"peer": st.Peer, "opcode": p[0]})
	}
}
func cString(b []byte) (string, error) {
	if len(b) > maxPathBytes {
		return "", fmt.Errorf("path too long")
	}
	if i := strings.IndexByte(string(b), 0); i >= 0 {
		b = b[:i]
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
	if flags&0x02 != 0 {
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
	real, err := s.resolveImagePath(path)
	if err != nil {
		s.sendOpen(st, errno(err), 0, 0)
		return
	}
	img, err := compression.Open(real)
	if err != nil {
		s.sendOpen(st, errno(err), 0, 0)
		return
	}
	st.Handles[id] = &session.Handle{Reader: img, Closer: img}
	s.sendOpen(st, id, 0x2000, uint64(img.Size()))
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
	h := st.Handles[id]
	if h == nil {
		s.sendSimple(st, result8(protocol.CloseReply, -int32(syscall.EBADF)))
		return
	}
	if h.Closer != nil {
		_ = h.Closer.Close()
	}
	delete(st.Handles, id)
	s.sendSimple(st, result8(protocol.CloseReply, 0))
}
func (s *Server) handleRead(st *session.State, p []byte) {
	if len(p) < 12 {
		s.sendTransfer(st, result8(protocol.ResultReply, -int32(syscall.EINVAL)), nil)
		return
	}
	id := int32(binary.LittleEndian.Uint32(p[4:8]))
	size := binary.LittleEndian.Uint32(p[8:12])
	if size > 64<<20 {
		s.sendTransfer(st, result8(protocol.ResultReply, -int32(syscall.EINVAL)), nil)
		return
	}
	h := st.Handles[id]
	if h == nil || h.Reader == nil {
		s.sendTransfer(st, result8(protocol.ResultReply, -int32(syscall.EBADF)), nil)
		return
	}
	buf := make([]byte, size)
	n, err := h.Reader.Read(buf)
	if err != nil && err != io.EOF {
		s.sendTransfer(st, result8(protocol.ResultReply, errno(err)), nil)
		return
	}
	s.sendTransfer(st, result8(protocol.ResultReply, int32(n)), buf[:n])
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
	lower := strings.ToLower(entry.Name)
	for _, ext := range []string{".cso", ".ciso", ".zso", ".ziso"} {
		if strings.HasSuffix(lower, ext) {
			entry.Name = entry.Name[:len(entry.Name)-len(ext)] + ".iso"
			if img, err := compression.Open(entry.SourcePath); err == nil {
				entry.Size = uint64(img.Size())
				_ = img.Close()
			}
			break
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
	if ext := strings.ToLower(filepath.Ext(real)); ext == ".cso" || ext == ".ciso" || ext == ".zso" || ext == ".ziso" {
		if img, e := compression.Open(real); e == nil {
			size = uint64(img.Size())
			_ = img.Close()
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
