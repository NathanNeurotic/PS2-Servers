package udpfs

import (
	"bytes"
	"compress/zlib"
	"encoding/binary"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/compression"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
)

func writeUDPFSContainer(t *testing.T, path, magic string, source, encoded []byte) {
	t.Helper()
	const headerSize = 24
	start := uint32(headerSize + 8)
	hdr := make([]byte, headerSize)
	copy(hdr[:4], magic)
	binary.LittleEndian.PutUint32(hdr[4:8], headerSize)
	binary.LittleEndian.PutUint64(hdr[8:16], uint64(len(source)))
	binary.LittleEndian.PutUint32(hdr[16:20], uint32(len(source)))
	hdr[20] = 1
	index := make([]byte, 8)
	binary.LittleEndian.PutUint32(index[:4], start)
	binary.LittleEndian.PutUint32(index[4:], start+uint32(len(encoded)))
	if err := os.WriteFile(path, append(append(hdr, index...), encoded...), 0o644); err != nil {
		t.Fatal(err)
	}
}

func writeUDPFSCSO(t *testing.T, path string, source []byte) {
	t.Helper()
	var encoded bytes.Buffer
	writer := zlib.NewWriter(&encoded)
	if _, err := writer.Write(source); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	writeUDPFSContainer(t, path, "CISO", source, encoded.Bytes())
}

func readUDPFSImage(t *testing.T, c *client, path string) (int32, uint64, []byte) {
	t.Helper()
	reply := c.send(openMsg(path, fioRead, false))
	handle := result(reply)
	if handle < 0 {
		return handle, 0, nil
	}
	size := uint64(binary.LittleEndian.Uint32(reply[12:16])) | uint64(binary.LittleEndian.Uint32(reply[16:20]))<<32
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
	return 0, size, data
}

func statUDPFSImage(t *testing.T, c *client, path string) (int32, uint64) {
	t.Helper()
	reply := c.send(pathMsg(protocol.GetStatRequest, path, 0))
	size := uint64(binary.LittleEndian.Uint32(reply[16:20])) | uint64(binary.LittleEndian.Uint32(reply[20:24]))<<32
	return result(reply), size
}

func listUDPFSImages(t *testing.T, c *client) map[string]uint64 {
	t.Helper()
	directory := result(c.send(openMsg("", fioRead, true)))
	if directory <= 0 {
		t.Fatalf("DOPEN returned %d", directory)
	}
	entries := make(map[string]uint64)
	for {
		request := make([]byte, 8)
		request[0] = byte(protocol.DReadRequest)
		binary.LittleEndian.PutUint32(request[4:8], uint32(directory))
		reply := c.send(request)
		if got := result(reply); got == 0 {
			break
		} else if got < 0 {
			t.Fatalf("DREAD returned %d", got)
		}
		nameLen := int(binary.LittleEndian.Uint16(reply[2:4]))
		name := string(reply[48 : 48+nameLen])
		entries[name] = uint64(binary.LittleEndian.Uint32(reply[16:20])) | uint64(binary.LittleEndian.Uint32(reply[20:24]))<<32
	}
	if got := result(c.send(closeMsg(directory))); got != 0 {
		t.Fatalf("directory CLOSE returned %d", got)
	}
	return entries
}

func TestCompressedAliasesAndRawContainerSemantics(t *testing.T) {
	root := t.TempDir()
	source := []byte("0123456789abcdef")
	plain := []byte("PLAIN-ISO-BYTES!")
	for name, data := range map[string][]byte{"game.iso": plain, "game.cso": source, "legacy.cso": source, "MIXED.CSO": source, "other.zso": source} {
		switch filepath.Ext(name) {
		case ".iso":
			if err := os.WriteFile(filepath.Join(root, name), data, 0o644); err != nil {
				t.Fatal(err)
			}
		case ".zso":
			encoded := append([]byte{0xf0, byte(len(source) - 15)}, source...)
			writeUDPFSContainer(t, filepath.Join(root, name), "ZISO", data, encoded)
		default:
			writeUDPFSCSO(t, filepath.Join(root, name), data)
		}
	}
	if err := os.WriteFile(filepath.Join(root, "other.chd"), []byte("raw CHD"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "corrupt.cso"), []byte("not a valid CSO"), 0o644); err != nil {
		t.Fatal(err)
	}
	c := dial(t, startServerAt(t, root, false))
	entries := listUDPFSImages(t, c)
	for _, name := range []string{"game.iso", "game.cso.iso", "legacy.cso.iso", "MIXED.CSO.iso", "other.zso.iso"} {
		if _, ok := entries[name]; !ok {
			t.Errorf("directory does not advertise %q: %+v", name, entries)
		}
	}
	if compression.CHDAvailable {
		if _, ok := entries["other.chd.iso"]; ok {
			t.Error("undecodable CHD was advertised as a virtual ISO")
		}
	} else if _, ok := entries["other.chd"]; !ok {
		t.Error("raw CHD is missing from the default build's listing")
	}
	if _, ok := entries["corrupt.cso.iso"]; ok {
		t.Error("corrupt CSO was advertised as a virtual ISO")
	}

	if _, size, data := readUDPFSImage(t, c, "game.iso"); size != uint64(len(plain)) || !bytes.Equal(data, plain) {
		t.Errorf("plain ISO returned size %d, data %q", size, data)
	}
	container, err := os.ReadFile(filepath.Join(root, "game.cso"))
	if err != nil {
		t.Fatal(err)
	}
	if _, size, data := readUDPFSImage(t, c, "game.cso"); size != uint64(len(container)) || !bytes.Equal(data, container) {
		t.Errorf("direct CSO returned size %d, decoded=%t; want raw size %d and bytes", size, bytes.Equal(data, source), len(container))
	}
	if result, size := statUDPFSImage(t, c, "game.cso"); result != 0 || size != uint64(len(container)) {
		t.Errorf("direct CSO GETSTAT = %d, %d; want 0, %d", result, size, len(container))
	}
	for _, path := range []string{"game.cso.iso", "legacy.iso", "MIXED.CSO.iso", "other.zso.iso"} {
		if _, size, data := readUDPFSImage(t, c, path); size != uint64(len(source)) || !bytes.Equal(data, source) {
			t.Errorf("virtual %s returned size %d, data %x; want decoded source", path, size, data)
		}
		if result, size := statUDPFSImage(t, c, path); result != 0 || size != uint64(len(source)) {
			t.Errorf("virtual %s GETSTAT = %d, %d; want 0, %d", path, result, size, len(source))
		}
	}
	if result, _, _ := readUDPFSImage(t, c, "corrupt.cso.iso"); result != -eIO {
		t.Errorf("corrupt canonical alias returned %d, want -EIO", result)
	}
}

func TestCompressedAliasesKeepCaseOnlyNamesDistinct(t *testing.T) {
	root := t.TempDir()
	probe := filepath.Join(root, "CaseProbe")
	if err := os.WriteFile(probe, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, "caseprobe")); err == nil {
		t.Skip("case-insensitive filesystem")
	}
	source := []byte("0123456789abcdef")
	writeUDPFSCSO(t, filepath.Join(root, "Coll.CSO"), source)
	writeUDPFSCSO(t, filepath.Join(root, "coll.cso"), source)
	c := dial(t, startServerAt(t, root, false))
	entries := listUDPFSImages(t, c)
	for _, name := range []string{"Coll.CSO.iso", "coll.cso.iso"} {
		if _, ok := entries[name]; !ok {
			t.Errorf("missing case-distinct alias %q: %+v", name, entries)
		}
		if _, size, data := readUDPFSImage(t, c, name); size != uint64(len(source)) || !bytes.Equal(data, source) {
			t.Errorf("%s returned size %d, data %x", name, size, data)
		}
	}
}

func listUDPFSImageNames(t *testing.T, c *client) []string {
	t.Helper()
	directory := result(c.send(openMsg("", fioRead, true)))
	if directory <= 0 {
		t.Fatalf("DOPEN returned %d", directory)
	}
	var names []string
	for {
		request := make([]byte, 8)
		request[0] = byte(protocol.DReadRequest)
		binary.LittleEndian.PutUint32(request[4:8], uint32(directory))
		reply := c.send(request)
		if got := result(reply); got == 0 {
			break
		} else if got < 0 {
			t.Fatalf("DREAD returned %d", got)
		}
		nameLen := int(binary.LittleEndian.Uint16(reply[2:4]))
		names = append(names, string(reply[48:48+nameLen]))
	}
	if got := result(c.send(closeMsg(directory))); got != 0 {
		t.Fatalf("directory CLOSE returned %d", got)
	}
	return names
}

func TestCompressedListingNamespaceReservesRealNamesAndLimitsAliases(t *testing.T) {
	root := t.TempDir()
	source := []byte("0123456789abcdef")
	writeUDPFSCSO(t, filepath.Join(root, "game.cso"), source)
	if err := os.WriteFile(filepath.Join(root, "game.cso.iso"), []byte("REAL"), 0o644); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{
		strings.Repeat("a", 247) + ".cso",
		strings.Repeat("b", 248) + ".cso",
		strings.Repeat("é", 123) + ".cso",
		strings.Repeat("é", 124) + ".cso",
	} {
		writeUDPFSCSO(t, filepath.Join(root, name), source)
	}
	c := dial(t, startServerAt(t, root, false))
	counts := map[string]int{}
	for _, name := range listUDPFSImageNames(t, c) {
		counts[name]++
	}
	if got := counts["game.cso.iso"]; got != 1 {
		t.Errorf("real ISO and virtual alias produced %d duplicate names, want 1", got)
	}
	for _, name := range []string{strings.Repeat("a", 247) + ".cso.iso", strings.Repeat("é", 123) + ".cso.iso"} {
		if counts[name] != 1 {
			t.Errorf("boundary alias %q missing: %+v", name, counts)
		}
	}
	for _, name := range []string{strings.Repeat("b", 248) + ".cso.iso", strings.Repeat("é", 124) + ".cso.iso"} {
		if counts[name] != 0 {
			t.Errorf("overlength alias %q was advertised: %+v", name, counts)
		}
	}
}

func TestLegacyAliasPrefersParseableSiblingAndCorruptAliasesAreHidden(t *testing.T) {
	root := t.TempDir()
	source := []byte("0123456789abcdef")
	if err := os.WriteFile(filepath.Join(root, "PrefG.cso"), []byte("not a CSO"), 0o644); err != nil {
		t.Fatal(err)
	}
	encoded := append([]byte{0xf0, 1}, source...)
	writeUDPFSContainer(t, filepath.Join(root, "PrefG.zso"), "ZISO", source, encoded)
	c := dial(t, startServerAt(t, root, false))
	names := listUDPFSImageNames(t, c)
	for _, name := range names {
		if name == "PrefG.cso.iso" {
			t.Fatal("corrupt virtual alias was advertised")
		}
	}
	found := false
	for _, name := range names {
		if name == "PrefG.zso.iso" {
			found = true
		}
	}
	if !found {
		t.Fatal("valid later sibling was hidden")
	}
	if _, size, data := readUDPFSImage(t, c, "PrefG.iso"); size != uint64(len(source)) || !bytes.Equal(data, source) {
		t.Fatalf("legacy alias did not select valid later sibling: size=%d data=%x", size, data)
	}
	if result, size := statUDPFSImage(t, c, "PrefG.iso"); result != 0 || size != uint64(len(source)) {
		t.Fatalf("legacy GETSTAT = %d, %d; want valid decoded size", result, size)
	}
	if result, _, _ := readUDPFSImage(t, c, "PrefG.cso.iso"); result != -eIO {
		t.Fatalf("corrupt canonical alias returned %d, want -EIO", result)
	}
	if result, size := statUDPFSImage(t, c, "PrefG.cso.iso"); result != -eIO || size != 0 {
		t.Fatalf("corrupt canonical GETSTAT = %d, %d; want -EIO, 0", result, size)
	}
}
