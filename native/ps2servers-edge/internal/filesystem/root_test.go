package filesystem

import (
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
