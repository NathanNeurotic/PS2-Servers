//go:build !cgo || !chd

package compression

const CHDAvailable = false

func openCHD(path string) (*Image, error) {
	return nil, ErrCHDUnavailable
}

func sizeOfCHD(path string) (int64, error) {
	return 0, ErrCHDUnavailable
}
