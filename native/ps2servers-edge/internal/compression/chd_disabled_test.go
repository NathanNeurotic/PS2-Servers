//go:build !cgo || !chd

package compression

import (
	"errors"
	"path/filepath"
	"testing"
)

func TestCHDUnavailableWithoutBuildTag(t *testing.T) {
	if CHDAvailable {
		t.Fatal("CHD reported available in a default build")
	}
	if _, err := Open(filepath.Join("testdata", "test_dvd.chd")); !errors.Is(err, ErrCHDUnavailable) {
		t.Fatalf("Open CHD error = %v, want ErrCHDUnavailable", err)
	}
}
