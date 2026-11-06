//go:build cgo && chd

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
	"math"
	"os"
	"unsafe"
)

const cdBinCacheBytes = 4 << 20

type chdBinReader struct {
	handle        *C.chd_file
	table         cdTrackTable
	layout        *cdLayout
	framesPerHunk int64
	hunkBuffer    []byte
	position      int64
	blocks        int64
	cache         map[int64][]byte
	cacheOrder    []int64
	cachedBytes   int64
	closed        bool
}

func openCHDBIN(path string, table cdTrackTable, layout *cdLayout, expected os.FileInfo) (io.ReadSeeker, io.Closer, error) {
	current, err := os.Lstat(path)
	if err != nil || !sameImage(expected, current) {
		return nil, nil, fmt.Errorf("CHD changed after its virtual view was prepared")
	}
	cpath := C.CString(path)
	defer C.free(unsafe.Pointer(cpath))
	var handle *C.chd_file
	if result := C.chd_open(cpath, C.CHD_OPEN_READ, nil, &handle); result != 0 {
		return nil, nil, fmt.Errorf("chd_open failed: %d", result)
	}
	current, err = os.Lstat(path)
	if err != nil || !sameImage(expected, current) {
		C.chd_close(handle)
		return nil, nil, fmt.Errorf("CHD changed while its decoder was opening")
	}
	if actual := backendCHDHunkBytes(unsafe.Pointer(handle)); int64(actual) != table.hunkBytes {
		C.chd_close(handle)
		return nil, nil, fmt.Errorf("CHD hunk size mismatch: parsed %d, backend %d", table.hunkBytes, actual)
	}
	framesPerHunk := table.hunkBytes / cdFrameBytes
	if framesPerHunk <= 0 || table.logicalSize < 0 {
		C.chd_close(handle)
		return nil, nil, fmt.Errorf("invalid CD hunk geometry")
	}
	blocks := table.logicalSize / table.hunkBytes
	if table.logicalSize%table.hunkBytes != 0 {
		blocks++
	}
	reader := &chdBinReader{
		handle:        handle,
		table:         table,
		layout:        layout,
		framesPerHunk: framesPerHunk,
		hunkBuffer:    make([]byte, table.hunkBytes),
		blocks:        blocks,
		cache:         make(map[int64][]byte),
	}
	return reader, reader, nil
}

func (r *chdBinReader) Close() error {
	if r.closed {
		return nil
	}
	if r.handle != nil {
		C.chd_close(r.handle)
		r.handle = nil
	}
	r.closed = true
	r.cache = nil
	r.cacheOrder = nil
	r.cachedBytes = 0
	r.hunkBuffer = nil
	return nil
}

func (r *chdBinReader) Seek(offset int64, whence int) (int64, error) {
	var base int64
	switch whence {
	case io.SeekStart:
	case io.SeekCurrent:
		base = r.position
	case io.SeekEnd:
		base = r.layout.size()
	default:
		return r.position, fmt.Errorf("invalid whence")
	}
	if (offset > 0 && base > math.MaxInt64-offset) || (offset < 0 && base < math.MinInt64-offset) {
		return r.position, fmt.Errorf("seek offset overflows int64")
	}
	position := base + offset
	if position < 0 {
		return r.position, fmt.Errorf("negative seek")
	}
	r.position = position
	return position, nil
}

func (r *chdBinReader) Read(out []byte) (int, error) {
	if r.closed || r.handle == nil {
		return 0, os.ErrClosed
	}
	if len(out) == 0 {
		return 0, nil
	}
	if r.position >= r.layout.size() {
		return 0, io.EOF
	}
	want := int64(len(out))
	if remaining := r.layout.size() - r.position; want > remaining {
		want = remaining
	}
	written := 0
	for int64(written) < want {
		frame, sectorOffset := r.position/cdSectorBytes, r.position%cdSectorBytes
		span := r.layout.spanAt(frame)
		if span == nil {
			return written, fmt.Errorf("virtual BIN track layout has a hole")
		}
		count := int64(cdSectorBytes) - sectorOffset
		if count > want-int64(written) {
			count = want - int64(written)
		}
		if span.physicalBase < 0 {
			clear(out[written : written+int(count)])
		} else {
			physical := span.physicalBase + frame - span.start
			hunkIndex := physical / r.framesPerHunk
			hunkFrame := physical % r.framesPerHunk
			data, err := r.hunk(hunkIndex)
			if err != nil {
				return written, err
			}
			start := hunkFrame * cdFrameBytes
			sector := data[start : start+cdSectorBytes]
			if span.audio {
				for i := int64(0); i < count; i++ {
					out[written+int(i)] = sector[(sectorOffset+i)^1]
				}
			} else {
				copy(out[written:written+int(count)], sector[sectorOffset:sectorOffset+count])
			}
		}
		written += int(count)
		r.position += count
	}
	if written < len(out) {
		return written, io.EOF
	}
	return written, nil
}

func (r *chdBinReader) hunk(index int64) ([]byte, error) {
	if index < 0 || index >= r.blocks {
		return nil, io.EOF
	}
	if data, ok := r.cache[index]; ok {
		for i, cached := range r.cacheOrder {
			if cached == index {
				copy(r.cacheOrder[i:], r.cacheOrder[i+1:])
				r.cacheOrder[len(r.cacheOrder)-1] = index
				break
			}
		}
		return data, nil
	}
	if len(r.hunkBuffer) == 0 {
		return nil, os.ErrClosed
	}
	if result := C.chd_read(r.handle, C.uint(index), unsafe.Pointer(&r.hunkBuffer[0])); result != 0 {
		return nil, fmt.Errorf("chd_read failed for hunk %d: %d", index, result)
	}
	data := make([]byte, len(r.hunkBuffer))
	copy(data, r.hunkBuffer)
	if int64(len(data)) <= cdBinCacheBytes {
		for r.cachedBytes+int64(len(data)) > cdBinCacheBytes && len(r.cacheOrder) > 0 {
			oldest := r.cacheOrder[0]
			r.cacheOrder = r.cacheOrder[1:]
			r.cachedBytes -= int64(len(r.cache[oldest]))
			delete(r.cache, oldest)
		}
		r.cache[index] = data
		r.cacheOrder = append(r.cacheOrder, index)
		r.cachedBytes += int64(len(data))
	}
	return data, nil
}
