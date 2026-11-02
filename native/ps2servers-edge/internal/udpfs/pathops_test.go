package udpfs

import (
	"encoding/binary"
	"os"
	"path/filepath"
	"syscall"
	"testing"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/filesystem"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
)

// MKDIR, REMOVE and RMDIR were unknown opcodes to Edge: the console's request
// was dropped with no reply, so a delete or new folder from the PS2 hung until
// the client's 5 s timeout and then failed with EIO. These drive the real
// server over loopback, as a console does, and check what is left on disk.
// The expected answers are the Desktop server's (tests/
// test_udpfs_symlink_path_ops.py runs the same cases against it).

func pathMsg(op protocol.MessageType, path string, mode uint16) []byte {
	b := []byte{byte(op), 0, 0, 0}
	binary.LittleEndian.PutUint16(b[2:4], mode)
	return append(b, append([]byte(path), 0)...)
}

// pathOp sends one MKDIR, REMOVE or RMDIR and returns its result.
func (c *client) pathOp(op protocol.MessageType, path string) int32 {
	c.t.Helper()
	reply := c.send(pathMsg(op, path, 0o755))
	if len(reply) < 8 || protocol.MessageType(reply[0]) != protocol.ResultReply {
		c.t.Fatalf("%s %q: expected a RESULT_REPLY, got %x", pathOpName(op), path, reply)
	}
	return result(reply)
}

func errnoResult(e syscall.Errno) int32 { return -int32(e) }

// pathOpsShare builds the share the Desktop server's tests use:
//
//	GAMES/real.elf, EMPTY/, FULL/keep.bin
//	link.elf -> GAMES/real.elf     dirlink -> EMPTY      fulllink -> FULL
//	outlink -> <outside>/secret.bin                      outdirlink -> <outside>/OUTDIR
//	dangling -> GAMES/ghost        dangling_out -> <outside>/ghostdir
type pathOpsShare struct {
	t       *testing.T
	root    string
	outside string
}

func newPathOpsShare(t *testing.T) *pathOpsShare {
	t.Helper()
	s := &pathOpsShare{t: t, root: t.TempDir(), outside: t.TempDir()}
	s.mkdir("GAMES")
	s.write("GAMES/real.elf", "real elf bytes")
	s.mkdir("EMPTY")
	s.mkdir("FULL")
	s.write("FULL/keep.bin", "keep")
	if err := os.WriteFile(filepath.Join(s.outside, "secret.bin"), []byte("secret"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(s.outside, "OUTDIR"), 0o755); err != nil {
		t.Fatal(err)
	}
	links := [][2]string{
		{filepath.Join("GAMES", "real.elf"), "link.elf"},
		{"EMPTY", "dirlink"},
		{"FULL", "fulllink"},
		{filepath.Join(s.outside, "secret.bin"), "outlink"},
		{filepath.Join(s.outside, "OUTDIR"), "outdirlink"},
		{filepath.Join("GAMES", "ghost"), "dangling"},
		{filepath.Join(s.outside, "ghostdir"), "dangling_out"},
	}
	for _, l := range links {
		if err := os.Symlink(l[0], s.at(l[1])); err != nil {
			t.Skipf("cannot create symlinks here: %v", err)
		}
	}
	return s
}

func (s *pathOpsShare) at(rel string) string { return filepath.Join(s.root, filepath.FromSlash(rel)) }

func (s *pathOpsShare) mkdir(rel string) {
	s.t.Helper()
	if err := os.Mkdir(s.at(rel), 0o755); err != nil {
		s.t.Fatal(err)
	}
}

func (s *pathOpsShare) write(rel, data string) {
	s.t.Helper()
	if err := os.WriteFile(s.at(rel), []byte(data), 0o644); err != nil {
		s.t.Fatal(err)
	}
}

func (s *pathOpsShare) read(path string) string {
	s.t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		s.t.Fatalf("%s: %v", path, err)
	}
	return string(data)
}

func (s *pathOpsShare) assertLink(rel string) {
	s.t.Helper()
	info, err := os.Lstat(s.at(rel))
	if err != nil || info.Mode()&os.ModeSymlink == 0 {
		s.t.Fatalf("%s is no longer a symlink (%v)", rel, err)
	}
}

func (s *pathOpsShare) assertGone(rel string) {
	s.t.Helper()
	if _, err := os.Lstat(s.at(rel)); !os.IsNotExist(err) {
		s.t.Fatalf("%s still exists (%v)", rel, err)
	}
}

func (s *pathOpsShare) assertIntact() {
	s.t.Helper()
	if got := s.read(s.at("GAMES/real.elf")); got != "real elf bytes" {
		s.t.Fatalf("GAMES/real.elf holds %q", got)
	}
	if got := s.read(s.at("FULL/keep.bin")); got != "keep" {
		s.t.Fatalf("FULL/keep.bin holds %q", got)
	}
	if got := s.read(filepath.Join(s.outside, "secret.bin")); got != "secret" {
		s.t.Fatalf("the file outside the root holds %q", got)
	}
	if entries, err := os.ReadDir(filepath.Join(s.outside, "OUTDIR")); err != nil || len(entries) != 0 {
		s.t.Fatalf("the folder outside the root changed: %v %v", entries, err)
	}
	if _, err := os.Lstat(filepath.Join(s.outside, "ghostdir")); !os.IsNotExist(err) {
		s.t.Fatalf("something was created outside the root: %v", err)
	}
	if info, err := os.Stat(s.at("EMPTY")); err != nil || !info.IsDir() {
		s.t.Fatalf("EMPTY is gone: %v", err)
	}
}

func TestRemoveDeletesTheSymlinkNotItsTarget(t *testing.T) {
	share := newPathOpsShare(t)
	c := dial(t, startServerAt(t, share.root, false))

	for _, name := range []string{"link.elf", "outlink", "fulllink", "dangling", "dangling_out"} {
		if got := c.pathOp(protocol.RemoveRequest, name); got != 0 {
			t.Fatalf("REMOVE %s returned %d, want 0", name, got)
		}
		share.assertGone(name)
	}
	share.assertIntact()
}

func TestRemoveRefusesDirectoriesAndReportsMissingFiles(t *testing.T) {
	share := newPathOpsShare(t)
	c := dial(t, startServerAt(t, share.root, false))

	for _, name := range []string{"FULL", "EMPTY", "", "/"} {
		if got := c.pathOp(protocol.RemoveRequest, name); got != errnoResult(syscall.EISDIR) {
			t.Fatalf("REMOVE %q returned %d, want -EISDIR", name, got)
		}
	}
	share.assertIntact()
	if got := c.pathOp(protocol.RemoveRequest, "GAMES/real.elf"); got != 0 {
		t.Fatalf("REMOVE of a file returned %d, want 0", got)
	}
	share.assertGone("GAMES/real.elf")
	if got := c.pathOp(protocol.RemoveRequest, "GAMES/real.elf"); got != errnoResult(syscall.ENOENT) {
		t.Fatalf("REMOVE of a missing file returned %d, want -ENOENT", got)
	}
}

func TestRmdirRefusesSymlinksAndFiles(t *testing.T) {
	share := newPathOpsShare(t)
	c := dial(t, startServerAt(t, share.root, false))

	for _, name := range []string{"dirlink", "fulllink", "outdirlink", "link.elf", "dangling", "GAMES/real.elf"} {
		if got := c.pathOp(protocol.RmdirRequest, name); got != errnoResult(syscall.ENOTDIR) {
			t.Fatalf("RMDIR %s returned %d, want -ENOTDIR", name, got)
		}
	}
	for _, name := range []string{"dirlink", "fulllink", "outdirlink", "link.elf", "dangling"} {
		share.assertLink(name)
	}
	share.assertIntact()
}

func TestRmdirRemovesOnlyEmptyDirectories(t *testing.T) {
	share := newPathOpsShare(t)
	c := dial(t, startServerAt(t, share.root, false))

	if got := c.pathOp(protocol.RmdirRequest, "FULL"); got != errnoResult(syscall.ENOTEMPTY) {
		t.Fatalf("RMDIR of a full folder returned %d, want -ENOTEMPTY", got)
	}
	share.assertIntact()
	if got := c.pathOp(protocol.RmdirRequest, "EMPTY/"); got != 0 {
		t.Fatalf("RMDIR of an empty folder returned %d, want 0", got)
	}
	share.assertGone("EMPTY")
	if got := c.pathOp(protocol.RmdirRequest, "EMPTY"); got != errnoResult(syscall.ENOENT) {
		t.Fatalf("RMDIR of a missing folder returned %d, want -ENOENT", got)
	}
}

func TestRmdirOfTheShareRootIsRefused(t *testing.T) {
	// An empty share is the case that matters: rmdir of it would succeed.
	root := t.TempDir()
	c := dial(t, startServerAt(t, root, false))
	for _, name := range []string{"", "/", ".", "./"} {
		if got := c.pathOp(protocol.RmdirRequest, name); got != errnoResult(syscall.EACCES) {
			t.Fatalf("RMDIR %q returned %d, want -EACCES", name, got)
		}
		if info, err := os.Stat(root); err != nil || !info.IsDir() {
			t.Fatalf("RMDIR %q removed the share root: %v", name, err)
		}
	}
}

func TestMkdirCreatesAndReportsWhatExists(t *testing.T) {
	share := newPathOpsShare(t)
	c := dial(t, startServerAt(t, share.root, false))

	if got := c.pathOp(protocol.MkdirRequest, "NEW/"); got != 0 {
		t.Fatalf("MKDIR NEW returned %d, want 0", got)
	}
	if info, err := os.Stat(share.at("NEW")); err != nil || !info.IsDir() {
		t.Fatalf("MKDIR did not create a folder: %v", err)
	}
	// dangling and dangling_out: the link itself is the entry, so the folder
	// is not made wherever it points, inside the root or out of it.
	for _, name := range []string{"NEW", "GAMES", "dirlink", "link.elf", "dangling", "dangling_out", ""} {
		if got := c.pathOp(protocol.MkdirRequest, name); got != errnoResult(syscall.EEXIST) {
			t.Fatalf("MKDIR %q returned %d, want -EEXIST", name, got)
		}
	}
	if _, err := os.Lstat(share.at("GAMES/ghost")); !os.IsNotExist(err) {
		t.Fatalf("MKDIR over a dangling link created its target: %v", err)
	}
	share.assertLink("dangling")
	share.assertLink("dangling_out")
	if got := c.pathOp(protocol.MkdirRequest, "NOPARENT/child"); got != errnoResult(syscall.ENOENT) {
		t.Fatalf("MKDIR under a missing folder returned %d, want -ENOENT", got)
	}
	share.assertIntact()
}

func TestMkdirThroughALinkedFolderStaysInsideTheRoot(t *testing.T) {
	share := newPathOpsShare(t)
	c := dial(t, startServerAt(t, share.root, false))

	if got := c.pathOp(protocol.MkdirRequest, "fulllink/SUB"); got != 0 {
		t.Fatalf("MKDIR through an in-root link returned %d", got)
	}
	if info, err := os.Stat(share.at("FULL/SUB")); err != nil || !info.IsDir() {
		t.Fatalf("FULL/SUB was not created: %v", err)
	}
}

// Edge's DREAD lists a link to a folder as a folder, so a console deleting a
// folder recursively goes into the link like any other subfolder. Deleting
// through it would empty the folder the link points at, which the console was
// never asked to touch: REMOVE and RMDIR refuse a path through a linked folder,
// as the Desktop server does.
func TestDeletesDoNotGoThroughALinkedFolder(t *testing.T) {
	share := newPathOpsShare(t)
	share.mkdir("FULL/SUB")
	c := dial(t, startServerAt(t, share.root, false))

	root, err := filesystem.Open(share.root)
	if err != nil {
		t.Fatal(err)
	}
	listed, err := root.List("")
	if err != nil {
		t.Fatal(err)
	}
	asFolder := false
	for _, e := range listed {
		if e.Name == "fulllink" && e.Mode == 0x1000 {
			asFolder = true
		}
	}
	if !asFolder {
		t.Fatalf("DREAD no longer lists the link to FULL as a folder: %+v", listed)
	}

	for _, req := range []struct {
		op   protocol.MessageType
		path string
	}{
		{protocol.RemoveRequest, "fulllink/keep.bin"},
		{protocol.RmdirRequest, "fulllink/SUB"},
		{protocol.RemoveRequest, `fulllink\keep.bin`},
		{protocol.RemoveRequest, "./fulllink/keep.bin"},
	} {
		if got := c.pathOp(req.op, req.path); got != errnoResult(syscall.EACCES) {
			t.Errorf("%s %q returned %d, want -EACCES", pathOpName(req.op), req.path, got)
		}
	}
	share.assertIntact()
	if info, err := os.Stat(share.at("FULL/SUB")); err != nil || !info.IsDir() {
		t.Fatalf("FULL/SUB is gone: %v", err)
	}

	// By its own path the folder is the console's to delete, and so is the
	// link itself, which REMOVE deletes without touching FULL.
	if got := c.pathOp(protocol.RmdirRequest, "FULL/SUB"); got != 0 {
		t.Fatalf("RMDIR FULL/SUB returned %d, want 0", got)
	}
	if got := c.pathOp(protocol.RemoveRequest, "fulllink"); got != 0 {
		t.Fatalf("REMOVE of the link returned %d, want 0", got)
	}
	share.assertGone("fulllink")
	share.assertIntact()
}

// On Windows the Desktop server's os.unlink is DeleteFile, which refuses a
// file whose read-only attribute is set; os.Remove clears the attribute and
// deletes it anyway. POSIX unlink ignores a file's own mode, so elsewhere both
// servers delete it. removeRefusesReadOnly stands in for the host here.
func TestRemoveOfAReadOnlyFileAnswersAsTheHostDoes(t *testing.T) {
	defer removeRefusesReadOnly.Store(removeRefusesReadOnly.Load())
	root := t.TempDir()
	save := filepath.Join(root, "keep.sav")
	if err := os.WriteFile(save, []byte("keep"), 0o444); err != nil {
		t.Fatal(err)
	}
	c := dial(t, startServerAt(t, root, false))

	removeRefusesReadOnly.Store(true)
	if got := c.pathOp(protocol.RemoveRequest, "keep.sav"); got != errnoResult(syscall.EACCES) {
		t.Fatalf("REMOVE of a read-only file as on Windows returned %d, want -EACCES", got)
	}
	if data, err := os.ReadFile(save); err != nil || string(data) != "keep" {
		t.Fatalf("the read-only file was deleted or changed: %q %v", data, err)
	}

	removeRefusesReadOnly.Store(false)
	if got := c.pathOp(protocol.RemoveRequest, "keep.sav"); got != 0 {
		t.Fatalf("REMOVE of a read-only file as on POSIX returned %d, want 0", got)
	}
	if _, err := os.Lstat(save); !os.IsNotExist(err) {
		t.Fatalf("keep.sav still exists: %v", err)
	}
}

// Only links are refused on the way; anything else is the operation's to
// answer, as on the Desktop server.
func TestAFileUsedAsAFolderIsNotALink(t *testing.T) {
	share := newPathOpsShare(t)
	c := dial(t, startServerAt(t, share.root, false))

	for _, req := range []struct {
		op   protocol.MessageType
		path string
		want int32
	}{
		{protocol.RemoveRequest, "FULL/keep.bin/x", errnoResult(syscall.ENOTDIR)},
		{protocol.RmdirRequest, "FULL/keep.bin/x", errnoResult(syscall.ENOTDIR)},
		{protocol.RemoveRequest, "FULL/NOPE/x", errnoResult(syscall.ENOENT)},
	} {
		if got := c.pathOp(req.op, req.path); got != req.want {
			t.Errorf("%s %q returned %d, want %d", pathOpName(req.op), req.path, got, req.want)
		}
	}
	share.assertIntact()
}

func TestPathOpsCannotLeaveTheRoot(t *testing.T) {
	share := newPathOpsShare(t)
	c := dial(t, startServerAt(t, share.root, false))

	for _, op := range []protocol.MessageType{protocol.MkdirRequest, protocol.RemoveRequest, protocol.RmdirRequest} {
		for _, path := range []string{
			"../x", "GAMES/../../x", "..", "GAMES/..", "GAMES/../real.elf",
			"/x", `\x`, "C:x", "outdirlink/x",
		} {
			// No subtests: the client's helpers fail the test that owns it.
			if got := c.pathOp(op, path); got != errnoResult(syscall.EACCES) {
				t.Errorf("%s %q returned %d, want -EACCES", pathOpName(op), path, got)
			}
		}
		// A parent that does not exist under a link out of the root cannot be
		// resolved far enough to place it, so it is ENOENT; either way, refused.
		if got := c.pathOp(op, "outdirlink/sub/x"); got >= 0 {
			t.Fatalf("%s outdirlink/sub/x returned %d, want an error", pathOpName(op), got)
		}
	}
	if got := c.pathOp(protocol.RemoveRequest, "outdirlink/../secret.bin"); got != errnoResult(syscall.EACCES) {
		t.Fatalf("REMOVE through a link out of the root returned %d, want -EACCES", got)
	}
	share.assertIntact()
	if info, err := os.Stat(share.root); err != nil || !info.IsDir() {
		t.Fatalf("the root is gone: %v", err)
	}
}

func TestPathOpsRefusedWhenReadOnly(t *testing.T) {
	share := newPathOpsShare(t)
	c := dial(t, startServerAt(t, share.root, true))

	for _, req := range []struct {
		op   protocol.MessageType
		path string
	}{
		{protocol.MkdirRequest, "NEW"}, {protocol.MkdirRequest, "dangling"},
		{protocol.RemoveRequest, "link.elf"}, {protocol.RemoveRequest, "outlink"},
		{protocol.RemoveRequest, "GAMES/real.elf"}, {protocol.RmdirRequest, "EMPTY"},
		{protocol.RmdirRequest, "dirlink"}, {protocol.RmdirRequest, ""},
		{protocol.RemoveRequest, "../x"},
	} {
		if got := c.pathOp(req.op, req.path); got != errnoResult(syscall.EACCES) {
			t.Fatalf("read-only %s %q returned %d, want -EACCES", pathOpName(req.op), req.path, got)
		}
	}
	if _, err := os.Lstat(share.at("NEW")); !os.IsNotExist(err) {
		t.Fatalf("read-only MKDIR created a folder: %v", err)
	}
	for _, name := range []string{"link.elf", "outlink", "dirlink", "dangling"} {
		share.assertLink(name)
	}
	share.assertIntact()
}

// A file another console is writing must not be deleted under it: on a host
// that allows unlinking an open file, the rest of that save would go into a
// file no longer in any folder and be lost when it is closed.
func TestRemoveOfAFileOpenForWritingIsBusy(t *testing.T) {
	root := t.TempDir()
	disc := startServerAt(t, root, false)
	writer := dial(t, disc)
	other := dial(t, disc)

	h := openForWriteOK(t, writer, "save.bin", fioRead|fioWrite|fioCreate|fioTrunc)
	for _, c := range []*client{other, writer} {
		if got := c.pathOp(protocol.RemoveRequest, "save.bin"); got != errnoResult(syscall.EBUSY) {
			t.Fatalf("REMOVE of a file open for writing returned %d, want -EBUSY", got)
		}
	}
	if got := result(writer.send(writeReqMsg(h, 4, writeDataMsg(0, 1, []byte("SAVE"))))); got != 4 {
		t.Fatalf("WRITE returned %d, want 4", got)
	}
	writer.send(closeMsg(h))
	if data, err := os.ReadFile(filepath.Join(root, "save.bin")); err != nil || string(data) != "SAVE" {
		t.Fatalf("save.bin holds %q %v", data, err)
	}

	// Closed, the file is anyone's to delete; a reader does not hold it.
	if got := result(other.send(openMsg("save.bin", fioRead, false))); got <= 0 {
		t.Fatalf("OPEN for read returned %d", got)
	}
	if got := other.pathOp(protocol.RemoveRequest, "save.bin"); got != 0 {
		t.Fatalf("REMOVE after the writer closed returned %d, want 0", got)
	}
	if _, err := os.Lstat(filepath.Join(root, "save.bin")); !os.IsNotExist(err) {
		t.Fatalf("save.bin still exists: %v", err)
	}
}

func TestPathOpsAreCountedAndLogged(t *testing.T) {
	observed := startObservedTestServer(t, true, false)
	c := dial(t, observed.discovery)
	c.pathOp(protocol.MkdirRequest, "NEWDIR")
	c.pathOp(protocol.RmdirRequest, "NEWDIR")
	c.pathOp(protocol.RemoveRequest, "game.iso")
	c.pathOp(protocol.RemoveRequest, "game.iso")

	snapshot := observed.server.stats.snapshot()
	for key, want := range map[string]int64{"mkdir": 1, "rmdir": 1, "remove": 2} {
		if got := metricInt64(t, snapshot, key); got != want {
			t.Errorf("metric %s = %d, want %d", key, got, want)
		}
	}
	observed.stop()
	records := parseJSONLogRecords(t, observed.logs.String())
	for op, path := range map[string]string{"MKDIR": "NEWDIR", "RMDIR": "NEWDIR", "REMOVE": "game.iso"} {
		record := findJSONLogRecord(t, records, "UDPFS request", func(r map[string]any) bool {
			return r["operation"] == op
		})
		assertJSONField(t, record, "path", path)
	}
}
