package compression

import (
	"bytes"
	"compress/flate"
	"compress/zlib"
	"encoding/binary"
	"os"
	"path/filepath"
	"testing"
)

func writeContainer(t *testing.T, path, magic string, source, encoded []byte) {
	t.Helper()
	const headerSize = 24
	indexSize := 8
	start := uint32(headerSize + indexSize)
	end := start + uint32(len(encoded))
	hdr := make([]byte, headerSize)
	copy(hdr[:4], []byte(magic))
	binary.LittleEndian.PutUint32(hdr[4:8], headerSize)
	binary.LittleEndian.PutUint64(hdr[8:16], uint64(len(source)))
	binary.LittleEndian.PutUint32(hdr[16:20], uint32(len(source)))
	hdr[20] = 1
	hdr[21] = 0
	index := make([]byte, indexSize)
	binary.LittleEndian.PutUint32(index[:4], start)
	binary.LittleEndian.PutUint32(index[4:], end)
	if err := os.WriteFile(path, append(append(hdr, index...), encoded...), 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestOpenPlainFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "README.txt")
	want := []byte("ordinary files remain readable")
	if err := os.WriteFile(path, want, 0o600); err != nil {
		t.Fatal(err)
	}
	img, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer img.Close()
	got := make([]byte, len(want))
	if _, err := img.Read(got); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, want) {
		t.Fatalf("got %q want %q", got, want)
	}
}

func TestCSOReadReturnsSourceBytes(t *testing.T) {
	source := []byte("0123456789abcdef")
	var encoded bytes.Buffer
	zw := zlib.NewWriter(&encoded)
	if _, err := zw.Write(source); err != nil {
		t.Fatal(err)
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "game.cso")
	writeContainer(t, path, "CISO", source, encoded.Bytes())
	img, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer img.Close()
	got := make([]byte, len(source))
	if _, err := img.Read(got); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, source) {
		t.Fatalf("got %x want %x", got, source)
	}
}

func TestCSORawDeflateReadReturnsSourceBytes(t *testing.T) {
	source := []byte("fedcba9876543210")
	var encoded bytes.Buffer
	zw, err := flate.NewWriter(&encoded, flate.DefaultCompression)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := zw.Write(source); err != nil {
		t.Fatal(err)
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "game.ciso")
	writeContainer(t, path, "CISO", source, encoded.Bytes())
	img, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer img.Close()
	got := make([]byte, len(source))
	if _, err := img.Read(got); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, source) {
		t.Fatalf("got %x want %x", got, source)
	}
}

func TestZSOReadReturnsSourceBytes(t *testing.T) {
	source := []byte("0123456789abcdef")
	// LZ4 block containing sixteen literal bytes and no match.
	encoded := append([]byte{0xf0, 0x01}, source...)
	path := filepath.Join(t.TempDir(), "game.zso")
	writeContainer(t, path, "ZISO", source, encoded)
	img, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer img.Close()
	got := make([]byte, len(source))
	if _, err := img.Read(got); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, source) {
		t.Fatalf("got %x want %x", got, source)
	}
}
