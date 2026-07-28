package compression

import (
	"encoding/binary"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

// writeBigIndexCSO builds a CSO whose header declares enough blocks that the
// index alone is several megabytes, and pads the file so that index would
// really fit. The blocks are never read; what is under test is how much the
// server allocates to answer "how big is this game".
//
// A real DVD-5 CSO at the conventional 2 KiB block has a 8.75 MiB index. This
// uses a smaller one so the test file stays a few megabytes, which is enough
// to tell an index-sized allocation from a header-sized one.
func writeBigIndexCSO(t *testing.T, path string, blockSize, blocks int64) int64 {
	t.Helper()
	const headerSize = 24
	declared := blockSize * blocks

	hdr := make([]byte, headerSize)
	copy(hdr[:4], []byte("CISO"))
	binary.LittleEndian.PutUint32(hdr[4:8], headerSize)
	binary.LittleEndian.PutUint64(hdr[8:16], uint64(declared))
	binary.LittleEndian.PutUint32(hdr[16:20], uint32(blockSize))
	hdr[20] = 1
	hdr[21] = 0

	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if _, err := f.Write(hdr); err != nil {
		t.Fatal(err)
	}
	// The index must be present and the file must be at least as long as the
	// header claims, or both Open and SizeOf reject it as corrupt.
	indexBytes := (blocks + 1) * 4
	if err := f.Truncate(headerSize + indexBytes); err != nil {
		t.Fatal(err)
	}
	return declared
}

// allocatedBy reports the bytes allocated while fn runs.
func allocatedBy(t *testing.T, fn func()) uint64 {
	t.Helper()
	var before, after runtime.MemStats
	runtime.GC()
	runtime.ReadMemStats(&before)
	fn()
	runtime.ReadMemStats(&after)
	return after.TotalAlloc - before.TotalAlloc
}

func TestSizeOfDoesNotAllocateTheIndex(t *testing.T) {
	const blockSize = 2048
	const blocks = 1 << 20 // index is 4 MiB + 4 B
	path := filepath.Join(t.TempDir(), "big.cso")
	declared := writeBigIndexCSO(t, path, blockSize, blocks)

	const indexBytes = (blocks + 1) * 4

	var probed int64
	probeAlloc := allocatedBy(t, func() {
		var err error
		probed, err = SizeOf(path)
		if err != nil {
			t.Fatal(err)
		}
	})
	if probed != declared {
		t.Fatalf("SizeOf = %d, want the declared %d", probed, declared)
	}

	openAlloc := allocatedBy(t, func() {
		img, err := Open(path)
		if err != nil {
			t.Fatal(err)
		}
		if img.Size() != declared {
			t.Fatalf("Open().Size() = %d, want %d", img.Size(), declared)
		}
		img.Close()
	})

	// The probe must not be in the same league as the index. A tenth of it is
	// a generous line: the header path allocates a 24-byte header, a file
	// handle and a little os.Stat scaffolding, three orders of magnitude below
	// this, while any regression that goes back through Open lands above it.
	if probeAlloc > indexBytes/10 {
		t.Fatalf("SizeOf allocated %d bytes for a %d-byte index; it should read only the header",
			probeAlloc, indexBytes)
	}
	// And the comparison that actually states the point.
	if probeAlloc*8 > openAlloc {
		t.Fatalf("SizeOf allocated %d and Open allocated %d; the probe is not meaningfully cheaper",
			probeAlloc, openAlloc)
	}
	t.Logf("SizeOf %d bytes vs Open %d bytes for a %d-byte index", probeAlloc, openAlloc, indexBytes)
}

func TestStreamedIndexReadHalvesOpenPeak(t *testing.T) {
	// Open must allocate about the index, not twice it. binary.Read on the
	// []uint32 took a fast path that allocated a second full-width byte buffer
	// alongside the destination; on a 32 MB board that transient copy was the
	// difference between fitting and not.
	const blockSize = 2048
	const blocks = 1 << 20
	const indexBytes = (blocks + 1) * 4
	path := filepath.Join(t.TempDir(), "peak.cso")
	writeBigIndexCSO(t, path, blockSize, blocks)

	alloc := allocatedBy(t, func() {
		img, err := Open(path)
		if err != nil {
			t.Fatal(err)
		}
		img.Close()
	})

	// Allow half again over the index for the scratch buffer and scaffolding.
	// Doubling would sail past this.
	if alloc > indexBytes*3/2 {
		t.Fatalf("Open allocated %d for a %d-byte index; a full second copy is being made",
			alloc, indexBytes)
	}
	t.Logf("Open allocated %d bytes for a %d-byte index", alloc, indexBytes)
}

func TestSizeOfAgreesWithOpen(t *testing.T) {
	// The two must not drift: a probe that reported a size the opener would
	// not agree with would advertise a game that then fails to load.
	dir := t.TempDir()

	plain := filepath.Join(dir, "movie.iso")
	if err := os.WriteFile(plain, make([]byte, 4096), 0o600); err != nil {
		t.Fatal(err)
	}
	cso := filepath.Join(dir, "game.cso")
	declared := writeBigIndexCSO(t, cso, 2048, 64)

	for _, tc := range []struct {
		path string
		want int64
	}{
		{plain, 4096},
		{cso, declared},
	} {
		got, err := SizeOf(tc.path)
		if err != nil {
			t.Fatalf("%s: %v", tc.path, err)
		}
		if got != tc.want {
			t.Fatalf("%s: SizeOf = %d, want %d", tc.path, got, tc.want)
		}
		img, err := Open(tc.path)
		if err != nil {
			t.Fatalf("%s: %v", tc.path, err)
		}
		if img.Size() != got {
			t.Fatalf("%s: Open().Size() = %d but SizeOf = %d", tc.path, img.Size(), got)
		}
		img.Close()
	}
}

func TestSizeOfRejectsWhatOpenRejects(t *testing.T) {
	dir := t.TempDir()

	// An index that could not fit in the file. Reporting a size here would
	// list a game that cannot be opened.
	truncated := filepath.Join(dir, "truncated.cso")
	hdr := make([]byte, 24)
	copy(hdr[:4], []byte("CISO"))
	binary.LittleEndian.PutUint32(hdr[4:8], 24)
	binary.LittleEndian.PutUint64(hdr[8:16], 1<<30) // claims a gigabyte
	binary.LittleEndian.PutUint32(hdr[16:20], 2048)
	hdr[20] = 1
	if err := os.WriteFile(truncated, hdr, 0o600); err != nil {
		t.Fatal(err)
	}

	bogus := filepath.Join(dir, "bogus.cso")
	if err := os.WriteFile(bogus, []byte("NOTACISOHEADER..........."), 0o600); err != nil {
		t.Fatal(err)
	}

	for _, path := range []string{truncated, bogus} {
		if _, err := SizeOf(path); err == nil {
			t.Fatalf("%s: SizeOf accepted an image Open rejects", path)
		}
		if img, err := Open(path); err == nil {
			img.Close()
			t.Fatalf("%s: Open unexpectedly accepted it", path)
		}
	}
}
