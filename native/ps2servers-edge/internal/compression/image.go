package compression

import (
	"bytes"
	"compress/flate"
	"compress/zlib"
	"encoding/binary"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

type Image struct {
	mu        sync.Mutex
	f         *os.File
	format    string
	size      int64
	blockSize int64
	align     uint8
	index     []uint32
	pos       int64
}

func Open(path string) (*Image, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	ext := strings.ToLower(filepath.Ext(path))
	if ext != ".cso" && ext != ".ciso" && ext != ".zso" && ext != ".ziso" {
		st, err := f.Stat()
		if err != nil {
			f.Close()
			return nil, err
		}
		return &Image{f: f, format: "plain", size: st.Size()}, nil
	}
	hdr := make([]byte, 24)
	if _, err = io.ReadFull(f, hdr); err != nil {
		f.Close()
		return nil, err
	}
	magic := string(hdr[:4])
	format := ""
	if magic == "CISO" {
		format = "cso"
	} else if magic == "ZISO" || magic == "ZSO\x00" {
		format = "zso"
	} else {
		f.Close()
		return nil, fmt.Errorf("unsupported image magic %q", magic)
	}
	headerSize := binary.LittleEndian.Uint32(hdr[4:8])
	size := int64(binary.LittleEndian.Uint64(hdr[8:16]))
	block := int64(binary.LittleEndian.Uint32(hdr[16:20]))
	align := hdr[21]
	if headerSize < 24 || size < 0 || block <= 0 || block > 16<<20 || align > 31 {
		f.Close()
		return nil, fmt.Errorf("invalid %s header", format)
	}
	blocks := (size + block - 1) / block
	if blocks > 1<<28 {
		f.Close()
		return nil, fmt.Errorf("image index too large")
	}
	index := make([]uint32, blocks+1)
	if _, err = f.Seek(int64(headerSize), io.SeekStart); err != nil {
		f.Close()
		return nil, err
	}
	if err = binary.Read(f, binary.LittleEndian, index); err != nil {
		f.Close()
		return nil, err
	}
	return &Image{f: f, format: format, size: size, blockSize: block, align: align, index: index}, nil
}
func (i *Image) Close() error { return i.f.Close() }
func (i *Image) Size() int64  { return i.size }
func (i *Image) Seek(off int64, whence int) (int64, error) {
	i.mu.Lock()
	defer i.mu.Unlock()
	var n int64
	switch whence {
	case io.SeekStart:
		n = off
	case io.SeekCurrent:
		n = i.pos + off
	case io.SeekEnd:
		n = i.size + off
	default:
		return i.pos, fmt.Errorf("invalid whence")
	}
	if n < 0 {
		return i.pos, fmt.Errorf("negative seek")
	}
	i.pos = n
	return n, nil
}
func (i *Image) Read(p []byte) (int, error) {
	i.mu.Lock()
	defer i.mu.Unlock()
	n, err := i.readAt(p, i.pos)
	i.pos += int64(n)
	return n, err
}
func (i *Image) readAt(p []byte, off int64) (int, error) {
	if off >= i.size {
		return 0, io.EOF
	}
	if int64(len(p)) > i.size-off {
		p = p[:i.size-off]
	}
	if i.format == "plain" {
		return i.f.ReadAt(p, off)
	}
	total := 0
	for len(p) > 0 {
		blockNo := off / i.blockSize
		within := off % i.blockSize
		block, err := i.readBlock(blockNo)
		if err != nil {
			return total, err
		}
		n := copy(p, block[within:])
		total += n
		off += int64(n)
		p = p[n:]
	}
	return total, nil
}
func (i *Image) readBlock(n int64) ([]byte, error) {
	if n < 0 || n >= int64(len(i.index)-1) {
		return nil, io.EOF
	}
	a, b := i.index[n], i.index[n+1]
	plain := a&0x80000000 != 0
	start := int64(a&0x7fffffff) << i.align
	end := int64(b&0x7fffffff) << i.align
	expected := i.blockSize
	if remain := i.size - n*i.blockSize; remain < expected {
		expected = remain
	}
	if start < 0 || end < start || end-start > 32<<20 {
		return nil, fmt.Errorf("invalid block range")
	}
	st, err := i.f.Stat()
	if err != nil {
		return nil, err
	}
	if end > st.Size() {
		return nil, io.ErrUnexpectedEOF
	}
	raw := make([]byte, end-start)
	nread, err := i.f.ReadAt(raw, start)
	if err != nil && err != io.EOF {
		return nil, err
	}
	if nread != len(raw) {
		return nil, io.ErrUnexpectedEOF
	}
	if plain {
		if int64(len(raw)) < expected {
			return nil, io.ErrUnexpectedEOF
		}
		return raw[:expected], nil
	}
	if i.format == "zso" {
		return decodeLZ4Block(raw, int(expected))
	}
	// CSO files exist with both raw DEFLATE blocks and zlib-wrapped blocks.
	// Prefer the conventional raw stream, then retry with the wrapper.
	read := func(r io.ReadCloser) ([]byte, error) {
		defer r.Close()
		out := make([]byte, expected)
		_, err := io.ReadFull(r, out)
		return out, err
	}
	if out, err := read(flate.NewReader(bytes.NewReader(raw))); err == nil {
		return out, nil
	}
	zr, err := zlib.NewReader(bytes.NewReader(raw))
	if err != nil {
		return nil, err
	}
	return read(zr)
}
