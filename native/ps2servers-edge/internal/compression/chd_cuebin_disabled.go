//go:build !cgo || !chd

package compression

import (
	"io"
	"os"
)

func openCHDBIN(string, cdTrackTable, *cdLayout, os.FileInfo) (io.ReadSeeker, io.Closer, error) {
	return nil, nil, ErrCHDUnavailable
}
