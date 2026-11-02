package udpfs

// A reply's result is a negated errno number, and the console sees nothing
// else, so the number has to be the one the Desktop server would send from
// the same host: the host's own errno on Linux and macOS, which Python and
// Go's syscall package share, and on Windows the C runtime's, which Python
// turns each Windows error code into. ENOENT, EIO, EBADF, EACCES, EBUSY,
// EEXIST, ENOTDIR, EISDIR, EINVAL, EMFILE and EFBIG have the same numbers on
// all of these hosts and in the console's ps2sdk errno.h; ENAMETOOLONG and
// ENOTEMPTY differ from host to host.
//
// Go has no such numbers on Windows: there syscall.EACCES and most of its
// siblings are values Go invented from 1<<29 up, and syscall.ENOTDIR is
// ERROR_PATH_NOT_FOUND, 3, which is ESRCH everywhere else. So replies use the
// e* constants of errno_windows.go and errno_other.go, never syscall's.

// The C runtime's errno numbers (errno.h), which Python reports on Windows.
const (
	crtENOENT       = 2
	crtEIO          = 5
	crtE2BIG        = 7
	crtENOEXEC      = 8
	crtEBADF        = 9
	crtECHILD       = 10
	crtEAGAIN       = 11
	crtENOMEM       = 12
	crtEACCES       = 13
	crtEBUSY        = 16
	crtEEXIST       = 17
	crtEXDEV        = 18
	crtENOTDIR      = 20
	crtEISDIR       = 21
	crtEINVAL       = 22
	crtEMFILE       = 24
	crtEFBIG        = 27
	crtENOSPC       = 28
	crtEPIPE        = 32
	crtENAMETOOLONG = 38
	crtENOTEMPTY    = 41
	crtEILSEQ       = 42
)

// crtErrno is the errno number Python gives a Windows error code, so that a
// failure reaches the console as the Desktop server's -e.errno does there. It
// is the table of CPython's winerror_to_errno (PC/errmap.h) less its HRESULT
// and Winsock ranges, which file operations do not produce, and like that
// table it answers EINVAL for a code it does not list.
func crtErrno(code uint32) int32 {
	switch code {
	case 2, 3, 15, 18, 53, 67, 161, 206:
		// FILE_NOT_FOUND, PATH_NOT_FOUND, INVALID_DRIVE, NO_MORE_FILES,
		// BAD_NETPATH, BAD_NET_NAME, BAD_PATHNAME, FILENAME_EXCED_RANGE
		return crtENOENT
	case 10: // BAD_ENVIRONMENT
		return crtE2BIG
	case 11, 188, 189, 190, 191, 192, 193, 194, 195, 196, 197, 198, 199, 200, 201, 202:
		// BAD_FORMAT and the bad-executable errors
		return crtENOEXEC
	case 6, 114, 130: // INVALID_HANDLE, INVALID_TARGET_HANDLE, DIRECT_ACCESS_HANDLE
		return crtEBADF
	case 128, 129: // WAIT_NO_CHILDREN, CHILD_NOT_COMPLETE
		return crtECHILD
	case 89, 164, 215: // NO_PROC_SLOTS, MAX_THRDS_REACHED, NESTING_NOT_ALLOWED
		return crtEAGAIN
	case 7, 8, 9, 1816: // ARENA_TRASHED, NOT_ENOUGH_MEMORY, INVALID_BLOCK, NOT_ENOUGH_QUOTA
		return crtENOMEM
	case 5, 16, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36,
		65, 82, 83, 108, 132, 158, 167:
		// ACCESS_DENIED, WRITE_PROTECT (19), SHARING_VIOLATION (32),
		// LOCK_VIOLATION (33), NETWORK_ACCESS_DENIED (65) and the other
		// device and locking failures
		return crtEACCES
	case 80, 183: // FILE_EXISTS, ALREADY_EXISTS
		return crtEEXIST
	case 17: // NOT_SAME_DEVICE
		return crtEXDEV
	case 267: // DIRECTORY
		return crtENOTDIR
	case 4: // TOO_MANY_OPEN_FILES
		return crtEMFILE
	case 112: // DISK_FULL
		return crtENOSPC
	case 109, 232: // BROKEN_PIPE, NO_DATA
		return crtEPIPE
	case 145: // DIR_NOT_EMPTY
		return crtENOTEMPTY
	case 1113: // NO_UNICODE_TRANSLATION
		return crtEILSEQ
	}
	return crtEINVAL
}
