//go:build cgo && chd

// CHD backend adapted from pcm720/udpfsd at ee94414.
// Copyright 2026 Ivan V (pcm720). See THIRD_PARTY_NOTICES.md for the BSD license.
package compression

/*
#cgo LDFLAGS: -lchdr
#include "chd_libchdr.h"
#include <stdlib.h>
*/
import "C"

import (
	"fmt"
	"io"
	"os"
	"unsafe"
)

const CHDAvailable = true

var backendCHDHunkBytes = func(handle unsafe.Pointer) uint32 {
	return uint32(C.ps2_chd_hunk_bytes((*C.chd_file)(handle)))
}

func openCHD(path string) (*Image, error) {
	h, err := parseCHDHeader(path)
	if err != nil {
		return nil, err
	}
	cpath := C.CString(path)
	defer C.free(unsafe.Pointer(cpath))
	var handle *C.chd_file
	if result := C.chd_open(cpath, C.CHD_OPEN_READ, nil, &handle); result != 0 {
		return nil, fmt.Errorf("chd_open failed: %d", result)
	}
	if actual := backendCHDHunkBytes(unsafe.Pointer(handle)); int64(actual) != h.hunkSize {
		C.chd_close(handle)
		return nil, fmt.Errorf("CHD hunk size mismatch: parsed %d, backend %d", h.hunkSize, actual)
	}
	info, err := os.Stat(path)
	if err != nil {
		C.chd_close(handle)
		return nil, err
	}
	hunkBuffer := make([]byte, int(h.hunkSize))
	img := &Image{format: "chd", size: h.size, fileSize: info.Size(), blockSize: h.blockSize, chd: &chdState{}}
	img.chd.readBlock = func(block int64) ([]byte, error) {
		if handle == nil {
			return nil, os.ErrClosed
		}
		if block < 0 || block >= h.blocks {
			return nil, io.EOF
		}
		if result := C.chd_read(handle, C.uint(block), unsafe.Pointer(&hunkBuffer[0])); result != 0 {
			return nil, fmt.Errorf("chd_read failed for hunk %d: %d", block, result)
		}
		if !h.isCD {
			out := make([]byte, len(hunkBuffer))
			copy(out, hunkBuffer)
			return out, nil
		}
		out := make([]byte, 0, h.blockSize)
		for frame := int64(0); frame < h.framesPerHunk; frame++ {
			start := frame * 2448
			out = append(out, chdExtractUserData(hunkBuffer[start:start+2352])...)
		}
		return out, nil
	}
	img.chd.close = func() error {
		if handle != nil {
			C.chd_close(handle)
			handle = nil
		}
		return nil
	}
	return img, nil
}

func sizeOfCHD(path string) (int64, error) {
	h, err := parseCHDHeader(path)
	if err != nil {
		return 0, err
	}
	return h.size, nil
}
