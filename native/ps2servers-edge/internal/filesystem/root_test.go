package filesystem

import (
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestTraversalRejected(t *testing.T) {
	r, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	for _, p := range []string{"../x", "/etc/passwd", "..\\x", `C:\Windows\win.ini`, `D:/games`} {
		if _, err := r.Resolve(p, false); err == nil {
			t.Fatalf("accepted %q", p)
		}
	}
}
func TestSymlinkEscapeRejected(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink permission varies")
	}
	root := t.TempDir()
	outside := t.TempDir()
	if err := os.WriteFile(filepath.Join(outside, "x"), []byte("x"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(root, "escape")); err != nil {
		t.Fatal(err)
	}
	r, _ := Open(root)
	if _, err := r.Resolve("escape/x", false); err == nil {
		t.Fatal("symlink escape accepted")
	}
}

func TestListOmitsEscapingSymlink(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink permission varies")
	}
	root := t.TempDir()
	outside := t.TempDir()
	if err := os.WriteFile(filepath.Join(outside, "secret.cso"), []byte("secret"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(outside, "secret.cso"), filepath.Join(root, "secret.cso")); err != nil {
		t.Fatal(err)
	}
	r, err := Open(root)
	if err != nil {
		t.Fatal(err)
	}
	entries, err := r.List("")
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 0 {
		t.Fatalf("escaping symlink was listed: %+v", entries)
	}
}

// Resolve(_, true) is the gate for an open that may create its file. A last
// component that already exists must be checked like any other path: the open
// goes through a symlink to its target. Returning the link unchecked let an
// O_CREAT open, or an SMB create, read, truncate and write a file outside the
// root through a link inside it, and create one through a dangling link.
func TestResolveForCreateChecksAnExistingLastComponent(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink permission varies")
	}
	root := t.TempDir()
	outside := t.TempDir()
	mustWrite(t, filepath.Join(outside, "secret.bin"), "secret")
	if err := os.Mkdir(filepath.Join(root, "GAMES"), 0o755); err != nil {
		t.Fatal(err)
	}
	mustWrite(t, filepath.Join(root, "GAMES", "real.sav"), "save")
	mustSymlink(t, filepath.Join(outside, "secret.bin"), filepath.Join(root, "outlink"))
	mustSymlink(t, filepath.Join(outside, "ghost"), filepath.Join(root, "dangling_out"))
	mustSymlink(t, filepath.Join("GAMES", "ghost"), filepath.Join(root, "dangling"))
	mustSymlink(t, filepath.Join("GAMES", "real.sav"), filepath.Join(root, "link.sav"))
	r, err := Open(root)
	if err != nil {
		t.Fatal(err)
	}
	canonical, err := filepath.EvalSymlinks(root)
	if err != nil {
		t.Fatal(err)
	}

	if p, err := r.Resolve("outlink", true); !errors.Is(err, ErrEscape) {
		t.Fatalf("a link out of the root resolved for create to %q, %v; want ErrEscape", p, err)
	}
	for _, name := range []string{"dangling_out", "dangling"} {
		if p, err := r.Resolve(name, true); err == nil {
			t.Fatalf("dangling link %s resolved for create to %q; creating through it "+
				"would make its target, which nothing checked", name, p)
		}
	}
	if p, err := r.Resolve("link.sav", true); err != nil || p != filepath.Join(canonical, "GAMES", "real.sav") {
		t.Fatalf("a link inside the root resolved to %q, %v; want its target", p, err)
	}
	if p, err := r.Resolve("GAMES/real.sav", true); err != nil || p != filepath.Join(canonical, "GAMES", "real.sav") {
		t.Fatalf("an existing file resolved to %q, %v", p, err)
	}
	if p, err := r.Resolve("GAMES/new.sav", true); err != nil || p != filepath.Join(canonical, "GAMES", "new.sav") {
		t.Fatalf("a missing file resolved to %q, %v; want it inside its parent", p, err)
	}
	if _, err := r.Resolve("NOPARENT/new.sav", true); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("a missing parent gave %v, want ErrNotExist", err)
	}
}

func mustWrite(t *testing.T, path, data string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(data), 0o644); err != nil {
		t.Fatal(err)
	}
}

func mustSymlink(t *testing.T, target, link string) {
	t.Helper()
	if err := os.Symlink(target, link); err != nil {
		t.Fatal(err)
	}
}

// Entry is the gate for MKDIR, REMOVE and RMDIR, which act on an entry and not
// on what it points at: only the parent is resolved, and must stay inside.
func TestEntryDoesNotFollowTheLastComponent(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink permission varies")
	}
	root := t.TempDir()
	outside := t.TempDir()
	if err := os.Mkdir(filepath.Join(root, "GAMES"), 0o755); err != nil {
		t.Fatal(err)
	}
	mustSymlink(t, filepath.Join("GAMES", "real.sav"), filepath.Join(root, "link.sav"))
	mustSymlink(t, outside, filepath.Join(root, "out"))
	r, err := Open(root)
	if err != nil {
		t.Fatal(err)
	}
	canonical, err := filepath.EvalSymlinks(root)
	if err != nil {
		t.Fatal(err)
	}

	for client, want := range map[string]string{
		"link.sav":      filepath.Join(canonical, "link.sav"),
		"out":           filepath.Join(canonical, "out"),
		"GAMES/x.sav/":  filepath.Join(canonical, "GAMES", "x.sav"),
		`GAMES\x.sav`:   filepath.Join(canonical, "GAMES", "x.sav"),
		"GAMES/./x.sav": filepath.Join(canonical, "GAMES", "x.sav"),
	} {
		if p, isRoot, err := r.Entry(client); err != nil || isRoot || p != want {
			t.Errorf("Entry(%q) = %q, %v, %v; want %q", client, p, isRoot, err, want)
		}
	}
	for _, client := range []string{"", ".", "/", "./", `\`, "/./"} {
		if p, isRoot, err := r.Entry(client); err != nil || !isRoot || p != canonical {
			t.Errorf("Entry(%q) = %q, %v, %v; want the root", client, p, isRoot, err)
		}
	}
	for _, client := range []string{"..", "GAMES/..", "../x", "GAMES/../../x", "/x", `\x`, "C:x", "out/x"} {
		if p, _, err := r.Entry(client); !errors.Is(err, ErrEscape) {
			t.Errorf("Entry(%q) = %q, %v; want ErrEscape", client, p, err)
		}
	}
	if _, _, err := r.Entry("NOPARENT/x"); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("a missing parent gave %v, want ErrNotExist", err)
	}
}

// Windows strips trailing dots and spaces from a last component, so the OS
// would act on a different entry than the one checked: "..." is the parent
// itself, which for a top-level name is the root.
func TestEntryRefusesNamesWindowsWouldReinterpret(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("Windows path normalisation")
	}
	root := t.TempDir()
	if err := os.Mkdir(filepath.Join(root, "GAMES"), 0o755); err != nil {
		t.Fatal(err)
	}
	r, err := Open(root)
	if err != nil {
		t.Fatal(err)
	}
	for _, client := range []string{"...", "GAMES/...", "GAMES/x.sav.", "GAMES/x.sav "} {
		if p, _, err := r.Entry(client); !errors.Is(err, ErrEscape) {
			t.Errorf("Entry(%q) = %q, %v; want ErrEscape", client, p, err)
		}
	}
}
