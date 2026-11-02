//go:build !windows

package udpfs

import (
	"errors"
	"syscall"
)

// The errno numbers replies carry off Windows: the host's own, as the Desktop
// server's Python reports them there. See errno.go.
const (
	eNOENT       = int32(syscall.ENOENT)
	eIO          = int32(syscall.EIO)
	eBADF        = int32(syscall.EBADF)
	eACCES       = int32(syscall.EACCES)
	eBUSY        = int32(syscall.EBUSY)
	eEXIST       = int32(syscall.EEXIST)
	eNOTDIR      = int32(syscall.ENOTDIR)
	eISDIR       = int32(syscall.EISDIR)
	eINVAL       = int32(syscall.EINVAL)
	eMFILE       = int32(syscall.EMFILE)
	eFBIG        = int32(syscall.EFBIG)
	eNAMETOOLONG = int32(syscall.ENAMETOOLONG)
	eNOTEMPTY    = int32(syscall.ENOTEMPTY)
)

// hostErrno is the errno number err carries, as the Desktop server replies
// -e.errno. ok is false when err carries none.
func hostErrno(err error) (n int32, ok bool) {
	var en syscall.Errno
	if !errors.As(err, &en) || en == 0 {
		return 0, false
	}
	return int32(en), true
}
