package smb

import (
	"encoding/binary"
	"os"
	"path/filepath"
	"testing"
)

func ntCreateParams(name string, disposition uint32) ([]byte, []byte) {
	p := make([]byte, 48)
	binary.LittleEndian.PutUint16(p[5:], uint16(len(name)))
	binary.LittleEndian.PutUint32(p[35:], disposition)
	return p, append([]byte{0}, []byte(name)...)
}

func writeParams(fid uint16, payload []byte) []byte {
	p := make([]byte, 28)
	binary.LittleEndian.PutUint16(p[4:], fid)
	binary.LittleEndian.PutUint16(p[20:], uint16(len(payload)))
	binary.LittleEndian.PutUint16(p[22:], uint16(32+1+len(p)+2))
	return p
}

func TestFreshShareCreatesDirectoryAndNewCFG(t *testing.T) {
	root := t.TempDir()
	srv := startServer(t, root, false)
	cl := dial(t, srv)
	cl.handshake("games")

	h, _, _ := cl.call(ComCreateDirectory, nil, []byte("\x04CFG\x00"))
	if h.Status != StatusSuccess {
		t.Fatalf("CREATE_DIRECTORY status %#x", h.Status)
	}
	if info, err := os.Stat(filepath.Join(root, "CFG")); err != nil || !info.IsDir() {
		t.Fatalf("CFG was not created: info=%v err=%v", info, err)
	}

	p, d := ntCreateParams(`CFG\SLUS_000.00.cfg`, fileOpenIf)
	h, rp, _ := cl.call(ComNTCreateAndX, p, d)
	if h.Status != StatusSuccess {
		t.Fatalf("NT_CREATE OPEN_IF status %#x", h.Status)
	}
	fid := binary.LittleEndian.Uint16(rp[5:])
	if action := binary.LittleEndian.Uint32(rp[7:]); action != fileCreated {
		t.Fatalf("CreateAction=%d want FILE_CREATED", action)
	}
	payload := []byte("compat=3\r\n")
	h, _, _ = cl.call(ComWriteAndX, writeParams(fid, payload), payload)
	if h.Status != StatusSuccess {
		t.Fatalf("WRITE_ANDX status %#x", h.Status)
	}
	closeParams := make([]byte, 2)
	binary.LittleEndian.PutUint16(closeParams, fid)
	cl.call(ComClose, closeParams, nil)

	got, err := os.ReadFile(filepath.Join(root, "CFG", "SLUS_000.00.cfg"))
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != string(payload) {
		t.Fatalf("cfg=%q want %q", got, payload)
	}
}

func TestOverwriteIfTruncatesOldTail(t *testing.T) {
	root := t.TempDir()
	if err := os.Mkdir(filepath.Join(root, "CFG"), 0o755); err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(root, "CFG", "SLUS_000.00.cfg")
	if err := os.WriteFile(target, []byte("compat=7\r\nvmc=old-long-value\r\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	srv := startServer(t, root, false)
	cl := dial(t, srv)
	cl.handshake("games")
	p, d := ntCreateParams(`CFG\SLUS_000.00.cfg`, fileOverwriteIf)
	h, rp, _ := cl.call(ComNTCreateAndX, p, d)
	if h.Status != StatusSuccess {
		t.Fatalf("NT_CREATE OVERWRITE_IF status %#x", h.Status)
	}
	if action := binary.LittleEndian.Uint32(rp[7:]); action != fileOverwritten {
		t.Fatalf("CreateAction=%d want FILE_OVERWRITTEN", action)
	}
	if info, err := os.Stat(target); err != nil || info.Size() != 0 {
		t.Fatalf("target not truncated at create: info=%v err=%v", info, err)
	}
	fid := binary.LittleEndian.Uint16(rp[5:])
	payload := []byte("compat=1\r\n")
	h, _, _ = cl.call(ComWriteAndX, writeParams(fid, payload), payload)
	if h.Status != StatusSuccess {
		t.Fatalf("WRITE_ANDX status %#x", h.Status)
	}
	got, err := os.ReadFile(target)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != string(payload) {
		t.Fatalf("stale tail survived: got %q want %q", got, payload)
	}
}

func TestReadOnlyRefusesCreates(t *testing.T) {
	root := t.TempDir()
	srv := startServer(t, root, true)
	cl := dial(t, srv)
	cl.handshake("games")

	h, _, _ := cl.call(ComCreateDirectory, nil, []byte("\x04CFG\x00"))
	if h.Status != StatusAccessDenied {
		t.Fatalf("CREATE_DIRECTORY status %#x want ACCESS_DENIED", h.Status)
	}
	p, d := ntCreateParams("new.cfg", fileOpenIf)
	h, _, _ = cl.call(ComNTCreateAndX, p, d)
	if h.Status != StatusAccessDenied {
		t.Fatalf("NT_CREATE status %#x want ACCESS_DENIED", h.Status)
	}
	if _, err := os.Stat(filepath.Join(root, "new.cfg")); !os.IsNotExist(err) {
		t.Fatalf("read-only server created new.cfg: %v", err)
	}
}
