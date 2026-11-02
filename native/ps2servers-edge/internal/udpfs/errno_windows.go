package udpfs

import (
	"errors"
	"syscall"
)

// The errno numbers replies carry on Windows: the C runtime's, as the Desktop
// server's Python reports them there. See errno.go.
const (
	eNOENT       int32 = crtENOENT
	eIO          int32 = crtEIO
	eBADF        int32 = crtEBADF
	eACCES       int32 = crtEACCES
	eBUSY        int32 = crtEBUSY
	eEXIST       int32 = crtEEXIST
	eNOTDIR      int32 = crtENOTDIR
	eISDIR       int32 = crtEISDIR
	eINVAL       int32 = crtEINVAL
	eMFILE       int32 = crtEMFILE
	eFBIG        int32 = crtEFBIG
	eNAMETOOLONG int32 = crtENAMETOOLONG
	eNOTEMPTY    int32 = crtENOTEMPTY
)

// hostErrno is the errno number Python would report for the Windows error err
// carries, as the Desktop server replies -e.errno. ok is false when err
// carries no Windows error code, which includes the values Go invented.
func hostErrno(err error) (n int32, ok bool) {
	var code syscall.Errno
	if !errors.As(err, &code) || code == 0 || code >= syscall.APPLICATION_ERROR {
		return 0, false
	}
	return crtErrno(uint32(code)), true
}
