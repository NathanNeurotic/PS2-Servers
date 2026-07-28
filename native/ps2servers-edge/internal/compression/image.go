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
	fileSize  int64
	blockSize int64
	align     uint8
	index     []uint32
	pos       int64

	// Decompressed-block cache. Without one, every read re-inflates its block
	// straight off disk, and a console reading sequentially touches the same
	// block repeatedly inside a single request -- wasted CPU on exactly the
	// low-power routers Edge targets. Bounded by block count, matching the
	// Desktop server's --compression-cache-size.
	cacheLimit int
	cache      map[int64][]byte
	cacheOrder []int64
}

// DefaultCacheBlocks matches the Desktop/Core server's default so the two
// behave the same when neither is tuned.
const DefaultCacheBlocks = 32

// MaxCacheBlocks caps the cache. The limit is used as a map's initial capacity,
// so an absurd value allocates eagerly rather than growing into it -- and Edge
// runs on routers with tens of megabytes of RAM. At a typical 2 KiB block this
// ceiling is roughly 8 MiB per open image, far past any useful working set.
const MaxCacheBlocks = 4096

// SetCacheBlocks bounds the decompressed-block cache. Zero disables caching.
// Clamped rather than trusted: this is a library entry point, so it must hold
// even when a caller skips the CLI's validation.
func (i *Image) SetCacheBlocks(n int) {
	i.mu.Lock()
	defer i.mu.Unlock()
	if n < 0 {
		n = 0
	}
	if n > MaxCacheBlocks {
		n = MaxCacheBlocks
	}
	i.cacheLimit = n
	i.cache = nil
	i.cacheOrder = nil
}

// cacheStore records a decompressed block, evicting the oldest once the limit
// is reached. Insertion order rather than true LRU: reads here are
// overwhelmingly sequential, so the oldest block is also the least likely to be
// wanted again, and this avoids per-hit bookkeeping on the transfer path.
func (i *Image) cacheStore(n int64, block []byte) {
	if i.cacheLimit <= 0 {
		return
	}
	if i.cache == nil {
		i.cache = make(map[int64][]byte, i.cacheLimit)
	}
	if _, exists := i.cache[n]; exists {
		return
	}
	for len(i.cacheOrder) >= i.cacheLimit {
		oldest := i.cacheOrder[0]
		i.cacheOrder = i.cacheOrder[1:]
		delete(i.cache, oldest)
	}
	i.cache[n] = block
	i.cacheOrder = append(i.cacheOrder, n)
}

// Open reads an image with the default block cache. Use OpenCached to size it.
func Open(path string) (*Image, error) { return OpenCached(path, DefaultCacheBlocks) }

// OpenCached reads an image, bounding the decompressed-block cache to n blocks.
// Zero disables caching.
func OpenCached(path string, n int) (*Image, error) {
	img, err := openImage(path)
	if err != nil {
		return nil, err
	}
	img.SetCacheBlocks(n)
	return img, nil
}

// OpenRaw serves a file byte-for-byte with no decompression, whatever its
// extension. Used by --no-compression, which is a diagnostic: a console cannot
// interpret CSO/ZSO container bytes, so this exists to compare against an
// undecoded transfer, not to make compressed images loadable.
func OpenRaw(path string) (*Image, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	info, err := f.Stat()
	if err != nil {
		f.Close()
		return nil, err
	}
	return &Image{f: f, format: "plain", size: info.Size(), fileSize: info.Size()}, nil
}

func openImage(path string) (*Image, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	st, err := f.Stat()
	if err != nil {
		f.Close()
		return nil, err
	}
	rawFileSize := st.Size()
	ext := strings.ToLower(filepath.Ext(path))
	if ext != ".cso" && ext != ".ciso" && ext != ".zso" && ext != ".ziso" {
		return &Image{f: f, format: "plain", size: rawFileSize, fileSize: rawFileSize}, nil
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
	if blocks > 1<<28 || uint64(headerSize)+uint64(blocks+1)*4 > uint64(rawFileSize) {
		f.Close()
		return nil, fmt.Errorf("image index too large or extends past file size")
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
	return &Image{f: f, format: format, size: size, fileSize: rawFileSize, blockSize: block, align: align, index: index}, nil
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
// readBlock returns a decompressed block, from cache when possible.
//
// Caching is done here, wrapping the decoder, rather than at each of the
// decoder's several success returns -- one of them would eventually be missed,
// and a half-cached decoder is the kind of bug that shows up as intermittent
// slowness rather than as a failure.
func (i *Image) readBlock(n int64) ([]byte, error) {
	if blk, ok := i.cache[n]; ok {
		return blk, nil
	}
	blk, err := i.decodeBlock(n)
	if err != nil {
		return nil, err
	}
	i.cacheStore(n, blk)
	return blk, nil
}

func (i *Image) decodeBlock(n int64) ([]byte, error) {
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
	if end > i.fileSize {
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
