package compression

import (
	"encoding/binary"
	"errors"
	"math"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

func writeCHDTestHeader(t *testing.T, path string, version, hunk, unit uint32, logical uint64, codec uint32) {
	t.Helper()
	hdr := make([]byte, 64)
	copy(hdr[:8], "MComprHD")
	binary.BigEndian.PutUint32(hdr[8:12], 64)
	binary.BigEndian.PutUint32(hdr[12:16], version)
	binary.BigEndian.PutUint32(hdr[16:20], codec)
	binary.BigEndian.PutUint64(hdr[32:40], logical)
	binary.BigEndian.PutUint32(hdr[56:60], hunk)
	binary.BigEndian.PutUint32(hdr[60:64], unit)
	if err := os.WriteFile(path, hdr, 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestParseCHDHeaderDVD(t *testing.T) {
	path := filepath.Join(t.TempDir(), "dvd.chd")
	writeCHDTestHeader(t, path, 5, 4096, 2048, 3*4096, 0)
	h, err := parseCHDHeader(path)
	if err != nil {
		t.Fatal(err)
	}
	if h.isCD || h.blocks != 3 || h.blockSize != 4096 || h.size != 3*4096 {
		t.Fatalf("unexpected DVD layout: %+v", h)
	}
}

func TestParseCHDHeaderCD(t *testing.T) {
	path := filepath.Join(t.TempDir(), "cd.chd")
	writeCHDTestHeader(t, path, 5, 4896, 2448, 3*4896, 0x63646c7a)
	h, err := parseCHDHeader(path)
	if err != nil {
		t.Fatal(err)
	}
	if !h.isCD || h.blocks != 3 || h.framesPerHunk != 2 || h.blockSize != 4096 || h.size != 3*4096 {
		t.Fatalf("unexpected CD layout: %+v", h)
	}
}

func TestParseCHDHeaderRejectsUnsafeValues(t *testing.T) {
	for _, test := range []struct {
		name    string
		version uint32
		hunk    uint32
		unit    uint32
		logical uint64
		codec   uint32
	}{
		{"version", 4, 4096, 2048, 4096, 0},
		{"zero hunk", 5, 0, 2048, 4096, 0},
		{"oversized hunk", 5, 16<<20 + 1, 2048, 32 << 20, 0},
		{"misaligned CD hunk", 5, 4096, 2448, 8192, 0x63646c7a},
		{"logical size overflow", 5, 4096, 2048, math.MaxUint64, 0},
	} {
		t.Run(test.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "invalid.chd")
			writeCHDTestHeader(t, path, test.version, test.hunk, test.unit, test.logical, test.codec)
			if _, err := parseCHDHeader(path); err == nil {
				t.Fatal("invalid CHD header was accepted")
			}
		})
	}
	path := filepath.Join(t.TempDir(), "magic.chd")
	writeCHDTestHeader(t, path, 5, 4096, 2048, 4096, 0)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	copy(data[:8], "NotACHD!")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := parseCHDHeader(path); err == nil {
		t.Fatal("invalid CHD magic was accepted")
	}
}

func TestCHDUserDataExtraction(t *testing.T) {
	sector := make([]byte, 2448)
	copy(sector[:12], []byte{0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x00})
	sector[15] = 1
	for i := 16; i < len(sector); i++ {
		sector[i] = 0x51
	}
	if got := chdExtractUserData(sector); len(got) != 2048 || got[0] != 0x51 || got[2047] != 0x51 {
		t.Fatalf("Mode 1 extraction returned %x/%x", got[:1], got[2047:])
	}
	sector[15] = 2
	for i := 24; i < 24+2048; i++ {
		sector[i] = 0x52
	}
	if got := chdExtractUserData(sector); len(got) != 2048 || got[0] != 0x52 || got[2047] != 0x52 {
		t.Fatalf("Mode 2 extraction returned %x/%x", got[:1], got[2047:])
	}
	copy(sector[:12], make([]byte, 12))
	sector[0] = 0x52
	if got := chdExtractUserData(sector); len(got) != 2048 || got[0] != 0x52 {
		t.Fatalf("non-sync extraction returned %x", got[:1])
	}
	if got := chdExtractUserData([]byte{1, 2, 3}); len(got) != 2048 || got[0] != 1 || got[2] != 3 || got[3] != 0 {
		t.Fatalf("short-sector extraction returned %x", got[:4])
	}
}

func TestCHDCloseWaitsForRead(t *testing.T) {
	started := make(chan struct{})
	release := make(chan struct{})
	closed := make(chan struct{})
	state := &chdState{}
	state.readBlock = func(block int64) ([]byte, error) {
		close(started)
		<-release
		select {
		case <-closed:
			return nil, errors.New("read after close")
		default:
			return []byte{1, 2, 3, 4}, nil
		}
	}
	var closeOnce sync.Once
	state.close = func() error {
		closeOnce.Do(func() { close(closed) })
		return nil
	}
	img := &Image{format: "chd", size: 4, blockSize: 4, chd: state}
	readDone := make(chan error, 1)
	go func() {
		_, err := img.Read(make([]byte, 4))
		readDone <- err
	}()
	<-started
	closeDone := make(chan error, 1)
	go func() { closeDone <- img.Close() }()
	select {
	case err := <-closeDone:
		t.Fatalf("Close completed during an active read: %v", err)
	case <-time.After(20 * time.Millisecond):
	}
	close(release)
	if err := <-readDone; err != nil {
		t.Fatalf("active read failed: %v", err)
	}
	if err := <-closeDone; err != nil {
		t.Fatalf("Close failed: %v", err)
	}
	if err := img.Close(); err != nil {
		t.Fatalf("second Close failed: %v", err)
	}
}
