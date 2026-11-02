package udpfs

import (
	"encoding/binary"
	"errors"
	"os"
	"runtime"
	"syscall"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/filesystem"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
)

// MKDIR, REMOVE and RMDIR answer as the Desktop server (udpfs_server.py) does,
// since it is the one validated against consoles:
//
//   - a read-only server refuses all three with EACCES;
//   - the path names a directory entry (filesystem.Root.Entry): its parent must
//     lie inside the root, and a symlink in the last component is the entry
//     itself. REMOVE deletes the link and never its target, RMDIR of a link is
//     ENOTDIR even when it points at a folder, and MKDIR over a dangling link
//     is EEXIST rather than a folder made wherever the link points;
//   - REMOVE of a real directory is EISDIR and RMDIR of anything else is
//     ENOTDIR, decided from lstat so every host gives the same answer;
//   - REMOVE and RMDIR of a path that passes through a symlinked folder or a
//     junction are EACCES (filesystem.Root.RealFolders). Edge's DREAD lists a
//     link to a folder as a folder (the Desktop server's lists a junction as
//     one), so a console deleting a folder recursively would otherwise go
//     into the link and empty the folder it points at;
//   - RMDIR of the share root is EACCES, MKDIR of it EEXIST.
//
// Edge adds one refusal the Desktop server has no state for: REMOVE of a file
// that some peer holds open for writing is EBUSY, from the same single-writer
// reservation that refuses a second writer.

func pathOpName(t protocol.MessageType) string {
	switch t {
	case protocol.MkdirRequest:
		return "MKDIR"
	case protocol.RemoveRequest:
		return "REMOVE"
	default:
		return "RMDIR"
	}
}

// pathRequest decodes the request the three share: msg_type, a reserved byte,
// a 16-bit mode and a NUL-terminated path. ok is false when the request has
// already been refused: a short one with EINVAL, and on a read-only server
// any other with EACCES, before its path is looked at.
func (s *Server) pathRequest(st *session.State, p []byte) (mode uint16, path string, ok bool) {
	if len(p) < 4 {
		s.sendPathResult(st, -int32(syscall.EINVAL))
		return 0, "", false
	}
	if s.cfg.ReadOnly {
		s.sendPathResult(st, -int32(syscall.EACCES))
		return 0, "", false
	}
	path, err := cString(p[4:])
	if err != nil {
		s.sendPathResult(st, -int32(syscall.ENAMETOOLONG))
		return 0, "", false
	}
	return binary.LittleEndian.Uint16(p[2:4]), path, true
}

func (s *Server) handleMkdir(st *session.State, p []byte) {
	mode, path, ok := s.pathRequest(st, p)
	if !ok {
		return
	}
	entry, _, err := s.root.Entry(path)
	if err != nil {
		s.sendPathResult(st, pathErrno(err))
		return
	}
	// The permission bits of the console's mode, subject to the umask; none
	// at all gets 0755, as the Desktop server gives a mode of 0.
	perm := os.FileMode(mode) & os.ModePerm
	if perm == 0 {
		perm = 0o755
	}
	if err := os.Mkdir(entry, perm); err != nil {
		s.sendPathResult(st, pathErrno(err))
		return
	}
	s.cfg.Log.Debug("MKDIR ok", s.pathLogFields(st, path))
	s.sendPathResult(st, 0)
}

func (s *Server) handleRemove(st *session.State, p []byte) {
	_, path, ok := s.pathRequest(st, p)
	if !ok {
		return
	}
	entry, _, err := s.root.Entry(path)
	if err == nil {
		err = s.root.RealFolders(path)
	}
	if err != nil {
		s.sendPathResult(st, pathErrno(err))
		return
	}
	info, err := os.Lstat(entry)
	if err != nil {
		s.sendPathResult(st, pathErrno(err))
		return
	}
	if info.IsDir() {
		// unlink(2) of a directory is EISDIR on Linux, EPERM on macOS and
		// EACCES on Windows; answer as the Desktop server does on all of them.
		s.sendPathResult(st, -int32(syscall.EISDIR))
		return
	}
	busy, err := s.removeUnlessWriting(entry)
	if busy {
		s.cfg.Log.Warn("REMOVE refused, file is open for writing", s.pathLogFields(st, path))
		s.sendPathResult(st, -int32(syscall.EBUSY))
		return
	}
	if err != nil {
		s.sendPathResult(st, pathErrno(err))
		return
	}
	s.cfg.Log.Debug("REMOVE ok", s.pathLogFields(st, path))
	s.sendPathResult(st, 0)
}

func (s *Server) handleRmdir(st *session.State, p []byte) {
	_, path, ok := s.pathRequest(st, p)
	if !ok {
		return
	}
	entry, isRoot, err := s.root.Entry(path)
	if err == nil {
		err = s.root.RealFolders(path)
	}
	if err != nil {
		s.sendPathResult(st, pathErrno(err))
		return
	}
	if isRoot {
		// rmdir would succeed on an empty share and delete the served folder.
		s.sendPathResult(st, -int32(syscall.EACCES))
		return
	}
	info, err := os.Lstat(entry)
	if err != nil {
		s.sendPathResult(st, pathErrno(err))
		return
	}
	if !info.IsDir() {
		// A symlink is not a directory, even one that points at a folder.
		s.sendPathResult(st, -int32(syscall.ENOTDIR))
		return
	}
	if err := removeEntry(entry, true); err != nil {
		s.sendPathResult(st, pathErrno(err))
		return
	}
	s.cfg.Log.Debug("RMDIR ok", s.pathLogFields(st, path))
	s.sendPathResult(st, 0)
}

// sendPathResult answers MKDIR, REMOVE or RMDIR with a ResultReply. It skips
// sendTransfer's SESSION accounting, as the Desktop server's reply does: the
// SESSION line has no kind for these, and a console asking for a folder that
// already exists has not hit a transfer error.
func (s *Server) sendPathResult(st *session.State, result int32) {
	s.sendTransferPackets(st, nil, result8(protocol.ResultReply, result))
}

func (s *Server) pathLogFields(st *session.State, path string) map[string]any {
	fields := map[string]any{"peer": inboundPeerString(st.Peer)}
	if safe, ok := safePathForLog(path); ok {
		fields["path"] = safe
	} else {
		fields["path_omitted"] = true
	}
	return fields
}

// pathErrno answers a failed MKDIR, REMOVE or RMDIR with the host's errno, as
// the Desktop server replies -e.errno, rather than folding everything that is
// neither missing nor denied into EIO: EEXIST, ENOTEMPTY and ENOTDIR are
// answers a console file manager acts on. Windows reports its own error codes,
// not errno values, so there only the portable classes are mapped.
func pathErrno(err error) int32 {
	if errors.Is(err, filesystem.ErrEscape) || errors.Is(err, filesystem.ErrThroughLink) {
		return -int32(syscall.EACCES)
	}
	var en syscall.Errno
	if runtime.GOOS != "windows" && errors.As(err, &en) && en != 0 {
		return -int32(en)
	}
	if errors.Is(err, os.ErrExist) {
		// Go files Windows' "directory not empty" under ErrExist as well, and
		// EEXIST is one of the two answers POSIX allows rmdir for it.
		return -int32(syscall.EEXIST)
	}
	return errno(err)
}

// removeEntry deletes the entry at path, which lstat found to be a directory
// (dir) or not. On Unix it is exactly rmdir(2) or unlink(2), so an entry that
// became the other kind after the lstat is refused rather than removed;
// os.Remove would try both. Windows uses os.Remove, which handles long paths
// and deletes a directory symlink or junction with RemoveDirectory -- the
// link, not what it points at.
func removeEntry(path string, dir bool) error {
	if runtime.GOOS == "windows" {
		return os.Remove(path)
	}
	op, call := "unlink", syscall.Unlink
	if dir {
		op, call = "rmdir", syscall.Rmdir
	}
	for {
		err := call(path)
		if err == syscall.EINTR {
			continue
		}
		if err != nil {
			return &os.PathError{Op: op, Path: path, Err: err}
		}
		return nil
	}
}
