//go:build cgo && chd

package compression

/*
#include "chd_libchdr.h"
*/
import "C"

import (
	"bytes"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func readCHDReference(t *testing.T) []byte {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("testdata", "test.iso"))
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func TestCHDReadSeekEOFAndSize(t *testing.T) {
	want := readCHDReference(t)
	for _, name := range []string{"test_dvd.chd", "test_cd.chd"} {
		t.Run(name, func(t *testing.T) {
			path := filepath.Join("testdata", name)
			img, err := Open(path)
			if err != nil {
				t.Fatal(err)
			}
			wantSize := len(want)
			if name == "test_cd.chd" {
				wantSize = 1114112
			}
			if img.Size() != int64(wantSize) {
				t.Fatalf("Size = %d, want %d", img.Size(), wantSize)
			}
			if size, err := SizeOf(path); err != nil || size != int64(wantSize) {
				t.Fatalf("SizeOf = %d, %v; want %d", size, err, wantSize)
			}
			got, err := io.ReadAll(img)
			if err != nil || len(got) != wantSize || !bytes.Equal(got[:len(want)], want) {
				t.Fatalf("full decode differs: len=%d, err=%v", len(got), err)
			}
			if name == "test_cd.chd" && !bytes.Equal(got[len(want):], make([]byte, wantSize-len(want))) {
				t.Fatal("CD hunk-padding sectors are not the expected zero bytes")
			}
			if _, err := img.Seek(0, io.SeekEnd); err != nil {
				t.Fatal(err)
			}
			if n, err := img.Read(make([]byte, 1)); n != 0 || err != io.EOF {
				t.Fatalf("EOF read = %d, %v", n, err)
			}
			if _, err := img.Seek(512, io.SeekStart); err != nil {
				t.Fatal(err)
			}
			prefix := make([]byte, 32)
			if _, err := io.ReadFull(img, prefix); err != nil || !bytes.Equal(prefix, want[512:544]) {
				t.Fatalf("seek/read differs: %x, %v", prefix, err)
			}
			if err := img.Close(); err != nil {
				t.Fatal(err)
			}
			if err := img.Close(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestCHDIndependentHandlesAndCacheBounds(t *testing.T) {
	path := filepath.Join("testdata", "test_dvd.chd")
	first, err := OpenCached(path, 1)
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	second, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer second.Close()
	a := make([]byte, 64)
	b := make([]byte, 64)
	if _, err := io.ReadFull(first, a); err != nil {
		t.Fatal(err)
	}
	if _, err := io.ReadFull(second, b); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(a, b) {
		t.Fatal("independent handles returned different data")
	}
	for i := 0; i < 4; i++ {
		if _, err := first.Seek(int64(i*4096), io.SeekStart); err != nil {
			t.Fatal(err)
		}
		if _, err := io.ReadFull(first, make([]byte, 4096)); err != nil {
			t.Fatal(err)
		}
	}
	if len(first.cacheOrder) > 1 || first.cacheBytes > int64(len(first.cacheOrder))*first.blockSize {
		t.Fatalf("cache exceeded its bounds: blocks=%d bytes=%d", len(first.cacheOrder), first.cacheBytes)
	}
}

func TestCHDRejectsCorruptContainer(t *testing.T) {
	path := filepath.Join(t.TempDir(), "bad.chd")
	if err := os.WriteFile(path, []byte("not a CHD"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Open(path); err == nil {
		t.Fatal("corrupt CHD was accepted")
	}
}

func TestCHDRejectsBackendHunkSizeMismatch(t *testing.T) {
	original := backendCHDHunkBytes
	backendCHDHunkBytes = func(handle *C.chd_file) uint32 {
		return uint32(C.ps2_chd_hunk_bytes(handle)) + 1
	}
	defer func() { backendCHDHunkBytes = original }()
	img, err := openCHD(filepath.Join("testdata", "test_dvd.chd"))
	if err == nil {
		img.Close()
		t.Fatal("backend hunk-size mismatch was accepted")
	}
	if !strings.Contains(err.Error(), "hunk size mismatch") {
		t.Fatalf("unexpected error: %v", err)
	}
}
