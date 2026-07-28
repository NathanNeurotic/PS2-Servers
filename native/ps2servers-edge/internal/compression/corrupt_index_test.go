package compression

import (
	"encoding/binary"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// writeCorruptIndex builds a CSO whose index brackets one block with entries
// gapBytes apart, regardless of the block size in its header.
//
// This is what a truncated or interrupted copy produces -- no attacker needed,
// a USB stick pulled mid-write will do it. The file itself is padded out so the
// bogus range still lies inside it, because the reader already refuses a range
// that runs past EOF and that guard is not the one under test. A real game
// image is hundreds of megabytes, so "inside the file" is not a meaningful
// limit on a 32 MB board: the read has to be bounded by the block size instead.
func writeCorruptIndex(t *testing.T, path string, blockSize, gapBytes int) {
	t.Helper()
	const headerSize = 24
	hdr := make([]byte, headerSize)
	copy(hdr[:4], []byte("CISO"))
	binary.LittleEndian.PutUint32(hdr[4:8], headerSize)
	binary.LittleEndian.PutUint64(hdr[8:16], uint64(blockSize)) // one block
	binary.LittleEndian.PutUint32(hdr[16:20], uint32(blockSize))
	hdr[20] = 1
	hdr[21] = 0

	start := uint32(headerSize + 8)
	index := make([]byte, 8)
	binary.LittleEndian.PutUint32(index[:4], start)
	binary.LittleEndian.PutUint32(index[4:], start+uint32(gapBytes))

	body := make([]byte, gapBytes)
	if err := os.WriteFile(path, append(append(hdr, index...), body...), 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestCorruptIndexIsRefusedNotAllocated(t *testing.T) {
	// 2 KiB blocks, but an index claiming the encoded block is 200 KiB. The
	// bound is derived from the block size, so this is refused however large
	// the surrounding file happens to be.
	path := filepath.Join(t.TempDir(), "game.cso")
	writeCorruptIndex(t, path, 2048, 200<<10)

	img, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer img.Close()

	buf := make([]byte, 2048)
	_, err = img.Read(buf)
	if err == nil {
		t.Fatal("a block range far larger than the block size was accepted")
	}
	if !strings.Contains(err.Error(), "invalid block range") {
		t.Fatalf("got %v, want an invalid block range error", err)
	}
}

func TestPlainBlockAtFullBlockSizeStillReads(t *testing.T) {
	// The bound must leave room for the legitimate worst case: a block that
	// compression would have grown, which both CSO and ZSO store plain at
	// exactly the block size. A bound that only allowed the compressed case
	// would reject valid images.
	const blockSize = 2048
	path := filepath.Join(t.TempDir(), "plain.cso")

	const headerSize = 24
	hdr := make([]byte, headerSize)
	copy(hdr[:4], []byte("CISO"))
	binary.LittleEndian.PutUint32(hdr[4:8], headerSize)
	binary.LittleEndian.PutUint64(hdr[8:16], blockSize)
	binary.LittleEndian.PutUint32(hdr[16:20], blockSize)
	hdr[20] = 1
	hdr[21] = 0

	start := uint32(headerSize + 8)
	index := make([]byte, 8)
	// High bit marks the block as stored plain rather than compressed.
	binary.LittleEndian.PutUint32(index[:4], start|0x80000000)
	binary.LittleEndian.PutUint32(index[4:], start+blockSize)

	body := make([]byte, blockSize)
	for i := range body {
		body[i] = byte(i)
	}
	if err := os.WriteFile(path, append(append(hdr, index...), body...), 0o600); err != nil {
		t.Fatal(err)
	}

	img, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer img.Close()

	got := make([]byte, blockSize)
	if _, err := img.Read(got); err != nil {
		t.Fatalf("a full-block plain entry should read, got %v", err)
	}
	for i := range got {
		if got[i] != byte(i) {
			t.Fatalf("byte %d = %d, want %d", i, got[i], byte(i))
		}
	}
}
