package udpfs

import (
	"os"
	"path/filepath"
	"runtime"
	"syscall"
	"testing"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
)

// A reply's errno number is all the console learns of a failure. These
// numbers are the same on every host the Desktop server runs on and in the
// console's ps2sdk errno.h, so they are written out here: on Windows Go's own
// constants are other numbers altogether (syscall.EACCES is 1<<29 + 1), and
// those are what Edge sent there.
func TestRepliesCarryTheNumbersTheConsoleKnows(t *testing.T) {
	const (
		ENOENT  = 2
		EBADF   = 9
		EACCES  = 13
		EEXIST  = 17
		ENOTDIR = 20
		EISDIR  = 21
		EINVAL  = 22
	)
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "SAVES"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "SAVES", "slot1.bin"), []byte("slot"), 0o644); err != nil {
		t.Fatal(err)
	}
	c := dial(t, startServerAt(t, root, false))
	ro := dial(t, startServerAt(t, root, true))

	for _, check := range []struct {
		what string
		got  int32
		want int32
	}{
		{"OPEN of a missing file", result(c.send(openMsg("SAVES/none.bin", fioRead, false))), -ENOENT},
		{"CLOSE of a handle never opened", result(c.send(closeMsg(999))), -EBADF},
		{"MKDIR of a folder that exists", c.pathOp(protocol.MkdirRequest, "SAVES"), -EEXIST},
		{"REMOVE of a folder", c.pathOp(protocol.RemoveRequest, "SAVES"), -EISDIR},
		{"RMDIR of a file", c.pathOp(protocol.RmdirRequest, "SAVES/slot1.bin"), -ENOTDIR},
		{"REMOVE of a missing file", c.pathOp(protocol.RemoveRequest, "SAVES/none.bin"), -ENOENT},
		{"MKDIR inside a missing folder", c.pathOp(protocol.MkdirRequest, "NONE/SUB"), -ENOENT},
		{"REMOVE on a read-only server", ro.pathOp(protocol.RemoveRequest, "SAVES/slot1.bin"), -EACCES},
		{"CLOSE too short to name a handle", result(c.send([]byte{byte(protocol.CloseRequest), 0, 0, 0})), -EINVAL},
	} {
		if check.got != check.want {
			t.Errorf("%s returned %d, want %d", check.what, check.got, check.want)
		}
	}

	// ENOTEMPTY has a different number on every host, and the Desktop
	// server sends its host's: the C runtime's 41 on Windows.
	want := -int32(syscall.ENOTEMPTY)
	if runtime.GOOS == "windows" {
		want = -41
	}
	if got := c.pathOp(protocol.RmdirRequest, "SAVES"); got != want {
		t.Errorf("RMDIR of a folder that is not empty returned %d, want %d", got, want)
	}
}

// crtErrno has to give a Windows error code the number Python gives it, since
// that is what the Desktop server sends. The pairs are from CPython's
// PC/errmap.h and the C runtime's errno.h.
func TestCrtErrnoNumbersWindowsErrorsAsPythonDoes(t *testing.T) {
	for _, pair := range []struct {
		code uint32
		name string
		want int32
	}{
		{2, "FILE_NOT_FOUND", 2},
		{3, "PATH_NOT_FOUND", 2},
		{206, "FILENAME_EXCED_RANGE", 2},
		{5, "ACCESS_DENIED", 13},
		{19, "WRITE_PROTECT", 13},
		{32, "SHARING_VIOLATION", 13},
		{33, "LOCK_VIOLATION", 13},
		{80, "FILE_EXISTS", 17},
		{183, "ALREADY_EXISTS", 17},
		{267, "DIRECTORY", 20},
		{145, "DIR_NOT_EMPTY", 41},
		{4, "TOO_MANY_OPEN_FILES", 24},
		{6, "INVALID_HANDLE", 9},
		{8, "NOT_ENOUGH_MEMORY", 12},
		{112, "DISK_FULL", 28},
		{1, "INVALID_FUNCTION", 22},
		{87, "INVALID_PARAMETER", 22},
		{123, "INVALID_NAME, which the table leaves to its default", 22},
	} {
		if got := crtErrno(pair.code); got != pair.want {
			t.Errorf("ERROR_%s (%d) became errno %d, want %d", pair.name, pair.code, got, pair.want)
		}
	}
}
