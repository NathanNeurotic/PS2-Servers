package compression

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
)

var ErrCHDUnavailable = errors.New("CHD support requires CGO and libchdr (build with -tags chd)")

const maxCHDHunkBytes = 16 << 20

type chdHeader struct {
	isCD          bool
	blocks        int64
	framesPerHunk int64
	hunkSize      int64
	blockSize     int64
	size          int64
}

type chdState struct {
	readBlock func(int64) ([]byte, error)
	close     func() error
}

func parseCHDHeader(path string) (chdHeader, error) {
	var h chdHeader
	f, err := os.Open(path)
	if err != nil {
		return h, err
	}
	defer f.Close()
	raw := make([]byte, 64)
	if _, err := io.ReadFull(f, raw); err != nil {
		return h, err
	}
	if string(raw[:8]) != "MComprHD" {
		return h, fmt.Errorf("not a valid CHD file")
	}
	if version := binary.BigEndian.Uint32(raw[12:16]); version != 5 {
		return h, fmt.Errorf("unsupported CHD version %d", version)
	}
	logical := binary.BigEndian.Uint64(raw[32:40])
	hunk := binary.BigEndian.Uint32(raw[56:60])
	unit := binary.BigEndian.Uint32(raw[60:64])
	if hunk == 0 || hunk > maxCHDHunkBytes {
		return h, fmt.Errorf("invalid CHD hunk size %d", hunk)
	}
	if logical > math.MaxInt64 {
		return h, fmt.Errorf("CHD logical size overflows int64")
	}
	blocks := logical / uint64(hunk)
	if logical%uint64(hunk) != 0 {
		blocks++
	}
	if blocks > math.MaxUint32 {
		return h, fmt.Errorf("CHD has too many hunks")
	}
	h.blocks = int64(blocks)
	h.hunkSize = int64(hunk)
	h.blockSize = int64(hunk)
	h.size = int64(logical)

	isCD := unit == 2448
	for i := 0; i < 4; i++ {
		switch binary.BigEndian.Uint32(raw[16+i*4 : 20+i*4]) {
		case 0x63646c7a, 0x63647a6c, 0x6364666c:
			isCD = true
		}
	}
	if !isCD {
		return h, nil
	}
	if hunk%2448 != 0 {
		return h, fmt.Errorf("CD CHD hunk size %d is not frame-aligned", hunk)
	}
	h.isCD = true
	h.framesPerHunk = int64(hunk / 2448)
	h.blockSize = h.framesPerHunk * 2048
	if h.blocks > 0 && h.framesPerHunk > math.MaxInt64/2048/h.blocks {
		return h, fmt.Errorf("CD CHD output size overflows int64")
	}
	h.size = h.blocks * h.framesPerHunk * 2048
	return h, nil
}

func chdExtractUserData(sector []byte) []byte {
	out := make([]byte, 2048)
	if len(sector) < 2048 {
		copy(out, sector)
		return out
	}
	offset := 0
	sync := []byte{0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x00}
	if len(sector) >= 16 {
		match := true
		for i := range sync {
			if sector[i] != sync[i] {
				match = false
				break
			}
		}
		if match {
			offset = 16
			if sector[15] == 2 {
				offset = 24
			}
		}
	}
	if offset+2048 > len(sector) {
		copy(out, sector[offset:])
		return out
	}
	copy(out, sector[offset:offset+2048])
	return out
}
