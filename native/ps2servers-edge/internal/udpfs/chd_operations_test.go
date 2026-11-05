//go:build cgo && chd

package udpfs

import (
	"bytes"
	"encoding/binary"
	"os"
	"path/filepath"
	"testing"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
)

func readUDPFSHandlePrefix(t *testing.T, c *client, path string, size int) (int32, []byte) {
	t.Helper()
	reply := c.send(openMsg(path, fioRead, false))
	handle := result(reply)
	if handle < 0 {
		return handle, nil
	}
	request := make([]byte, 12)
	request[0] = byte(protocol.ReadRequest)
	binary.LittleEndian.PutUint32(request[4:8], uint32(handle))
	binary.LittleEndian.PutUint32(request[8:12], uint32(size))
	read := c.send(request)
	if got := result(read); got < 0 {
		t.Fatalf("READ %q returned %d", path, got)
	}
	data := append([]byte(nil), read[8:]...)
	if got := result(c.send(closeMsg(handle))); got != 0 {
		t.Fatalf("CLOSE %q returned %d", path, got)
	}
	return 0, data
}

func TestCHDVirtualAliasAndRawContainer(t *testing.T) {
	root := t.TempDir()
	for _, name := range []string{"test_dvd.chd", "test_cd.chd"} {
		data, err := os.ReadFile(filepath.Join("..", "compression", "testdata", name))
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(root, name), data, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	want, err := os.ReadFile(filepath.Join("..", "compression", "testdata", "test.iso"))
	if err != nil {
		t.Fatal(err)
	}
	c := dial(t, startServerAt(t, root, false))
	entries := listUDPFSImages(t, c)
	for _, name := range []string{"test_dvd.chd.iso", "test_cd.chd.iso"} {
		wantSize := len(want)
		if name == "test_cd.chd.iso" {
			wantSize = 1114112
		}
		if entries[name] != uint64(wantSize) {
			t.Errorf("%s advertised size = %d, want %d", name, entries[name], wantSize)
		}
		if result, data := readUDPFSHandlePrefix(t, c, name, 64); result != 0 || !bytes.Equal(data, want[:64]) {
			t.Errorf("%s returned %x, result %d", name, data, result)
		}
		if result, size := statUDPFSImage(t, c, name); result != 0 || size != uint64(wantSize) {
			t.Errorf("%s GETSTAT = %d, %d; want %d", name, result, size, wantSize)
		}
	}
	raw, err := os.ReadFile(filepath.Join(root, "test_dvd.chd"))
	if err != nil {
		t.Fatal(err)
	}
	if result, data := readUDPFSHandlePrefix(t, c, "test_dvd.chd", 64); result != 0 || !bytes.Equal(data, raw[:64]) {
		t.Errorf("direct CHD returned decoded bytes instead of raw container: %x, result %d", data, result)
	}
	if result, size := statUDPFSImage(t, c, "test_dvd.chd"); result != 0 || size != uint64(len(raw)) {
		t.Errorf("direct CHD GETSTAT = %d, %d; want raw %d", result, size, len(raw))
	}
}
