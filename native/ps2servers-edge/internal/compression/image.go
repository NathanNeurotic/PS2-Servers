package compression

import (
	"bytes"
	"compress/flate"
	"compress/zlib"
	"encoding/binary"
	"fmt"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/memory"
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
	// Desktop server's --compression-cache-size, AND by total bytes.
	//
	// The count alone is not a bound on memory. blockSize is a field in the
	// image header, valid up to 16 MiB, so a count of 32 is anywhere from
	// 64 KiB to 512 MiB depending on a number the file gets to choose. The
	// byte ceiling is what actually holds, and it is what the operator's
	// intuition means by "cache size".
	cacheLimit int
	cacheBytes int64
	cache      map[int64][]byte
	cacheOrder []int64
}

// DefaultCacheBlocks matches the Desktop/Core server's default so the two
// behave the same when neither is tuned.
const DefaultCacheBlocks = 32

// MaxCacheBlocks caps the cache. The limit is used as a map's initial capacity,
// so an absurd value allocates eagerly rather than growing into it -- and Edge
// runs on routers with tens of megabytes of RAM.
//
// This used to be described as "roughly 8 MiB per open image at a typical
// 2 KiB block". The arithmetic was right and the conclusion was not: nothing
// enforced that typical block size, which is a header field valid up to 16
// MiB, so the real ceiling was 4096 x 16 MiB. preferredCacheBytes is the bound
// that was meant.
const MaxCacheBlocks = 4096

// preferredCacheBytes is the total decompressed-block cache one image may hold
// on a machine with memory to spare -- the ceiling MaxCacheBlocks was always
// intended to imply.
const preferredCacheBytes = 8 << 20

// cacheByteBudget is preferredCacheBytes, or less where the machine is small.
//
// Resolved once rather than per open: it reads /proc/meminfo, and an image can
// be opened on the transfer path. sync.OnceValue rather than a package-level
// initialiser so that no file is read merely by linking this package in.
var cacheByteBudget = sync.OnceValue(func() int64 {
	return memory.Detect().TransferCap(preferredCacheBytes)
})

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
	// Reset with the rest of the cache state. Leaving it behind would charge
	// the byte budget for blocks that are no longer held, and since nothing
	// ever credits it back the cache would then refuse to store anything for
	// the life of the handle.
	i.cacheBytes = 0
}

// cacheStore records a decompressed block, evicting the oldest once the limit
// is reached. Insertion order rather than true LRU: reads here are
// overwhelmingly sequential, so the oldest block is also the least likely to be
// wanted again, and this avoids per-hit bookkeeping on the transfer path.
func (i *Image) cacheStore(n int64, block []byte) {
	if i.cacheLimit <= 0 {
		return
	}
	budget := cacheByteBudget()
	// A block that would not fit even in an empty cache is not cached at all.
	// Without this the eviction loop below would empty the cache and store it
	// anyway, which is the one case where honouring the count would blow the
	// byte ceiling by design.
	if int64(len(block)) > budget {
		return
	}
	if i.cache == nil {
		i.cache = make(map[int64][]byte, i.cacheLimit)
	}
	if _, exists := i.cache[n]; exists {
		return
	}
	// Evict on either bound. The count keeps the Desktop server's behaviour
	// for ordinary 2 KiB blocks, where it is the one that binds; the byte
	// budget is what stops an image with a large declared block size from
	// turning that same count into hundreds of megabytes.
	for len(i.cacheOrder) > 0 &&
		(len(i.cacheOrder) >= i.cacheLimit || i.cacheBytes+int64(len(block)) > budget) {
		oldest := i.cacheOrder[0]
		i.cacheOrder = i.cacheOrder[1:]
		if evicted, ok := i.cache[oldest]; ok {
			i.cacheBytes -= int64(len(evicted))
			delete(i.cache, oldest)
		}
	}
	i.cache[n] = block
	i.cacheBytes += int64(len(block))
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
	h, err := parseHeader(hdr)
	if err != nil {
		f.Close()
		return nil, err
	}
	format, headerSize, size, block, align := h.format, h.headerSize, h.size, h.block, h.align
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
	// Streamed through a small scratch buffer rather than binary.Read.
	//
	// binary.Read on a []uint32 takes its fixed-size fast path, which
	// allocates a second buffer the full width of the index -- bs :=
	// make([]byte, 4*len(index)) -- and holds it alongside the destination for
	// the length of the read. The index for a DVD-5 CSO at the conventional
	// 2 KiB block is 2,295,105 entries, so that is 8.75 MiB doubled to 17.5 MiB
	// at peak. On a 32 MB router the transient copy alone is over half the
	// machine, for no benefit: the entries are decoded one at a time either
	// way.
	if err = readIndex(f, index); err != nil {
		f.Close()
		return nil, err
	}
	return &Image{f: f, format: format, size: size, fileSize: rawFileSize, blockSize: block, align: align, index: index}, nil
}

// header is the fixed 24-byte prologue shared by CSO and ZSO.
type header struct {
	format     string
	headerSize uint32
	size       int64
	block      int64
	align      uint8
}

// parseHeader decodes and validates the prologue. Kept separate so that
// SizeOf and Open cannot drift apart on what counts as a valid image: a probe
// that accepted a header the opener rejects would report a size for a file
// that then fails to open.
func parseHeader(hdr []byte) (header, error) {
	if len(hdr) < 24 {
		return header{}, fmt.Errorf("short image header")
	}
	var h header
	switch magic := string(hdr[:4]); magic {
	case "CISO":
		h.format = "cso"
	case "ZISO", "ZSO\x00":
		h.format = "zso"
	default:
		return header{}, fmt.Errorf("unsupported image magic %q", magic)
	}
	h.headerSize = binary.LittleEndian.Uint32(hdr[4:8])
	h.size = int64(binary.LittleEndian.Uint64(hdr[8:16]))
	h.block = int64(binary.LittleEndian.Uint32(hdr[16:20]))
	h.align = hdr[21]
	if h.headerSize < 24 || h.size < 0 || h.block <= 0 || h.block > 16<<20 || h.align > 31 {
		return header{}, fmt.Errorf("invalid %s header", h.format)
	}
	return h, nil
}

// readIndex fills index from r through a fixed scratch buffer.
//
// The alternative, binary.Read on the []uint32 directly, allocates a second
// buffer as wide as the whole index and holds it for the length of the read.
// See the call site for why that doubling matters on a small machine.
func readIndex(r io.Reader, index []uint32) error {
	const scratchEntries = 2048 // 8 KiB at a time, regardless of image size
	scratch := make([]byte, scratchEntries*4)
	for off := 0; off < len(index); {
		n := len(index) - off
		if n > scratchEntries {
			n = scratchEntries
		}
		if _, err := io.ReadFull(r, scratch[:n*4]); err != nil {
			return err
		}
		for i := 0; i < n; i++ {
			index[off+i] = binary.LittleEndian.Uint32(scratch[i*4:])
		}
		off += n
	}
	return nil
}

// SizeOf reports the decompressed size of an image without building its block
// index.
//
// This exists because reading that one number was, until it did, the most
// expensive thing the server could do. Listing a directory calls it once per
// compressed entry purely to show the console what the file will expand to,
// and going through Open for that allocated the entire index -- 8.75 MiB for a
// DVD-5 CSO at the conventional 2 KiB block, and about twice that at peak --
// then immediately discarded it. A share of thirty compressed games turned
// browsing into thirty multi-megabyte allocations, which is how a 32 MB router
// runs out of memory without anyone doing anything unusual.
//
// The size is a field in the 24-byte prologue. Only that is read.
func SizeOf(path string) (int64, error) {
	f, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer f.Close()

	st, err := f.Stat()
	if err != nil {
		return 0, err
	}
	// A file that is not a container is its own size, matching Open's
	// treatment of anything without a recognised extension.
	ext := strings.ToLower(filepath.Ext(path))
	if ext != ".cso" && ext != ".ciso" && ext != ".zso" && ext != ".ziso" {
		return st.Size(), nil
	}

	hdr := make([]byte, 24)
	if _, err := io.ReadFull(f, hdr); err != nil {
		return 0, err
	}
	h, err := parseHeader(hdr)
	if err != nil {
		return 0, err
	}
	// The index is not read, but its declared extent is still checked: a
	// header whose index could not fit in the file is corrupt, and reporting a
	// size from it would advertise a game that cannot then be opened.
	blocks := (h.size + h.block - 1) / h.block
	if blocks > 1<<28 || uint64(h.headerSize)+uint64(blocks+1)*4 > uint64(st.Size()) {
		return 0, fmt.Errorf("image index too large or extends past file size")
	}
	return h.size, nil
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
	// Bound the raw read by the block it is supposed to encode, not by a flat
	// 32 MiB. start and end come from the index table in the file, so a
	// truncated or corrupt image -- an interrupted copy to a USB stick is
	// enough, no attacker required -- can present a pair that are megabytes
	// apart, and the allocation below happens before any of those bytes are
	// looked at. On a 32 MB router the old ceiling was larger than the whole
	// machine, so a bad image took the server down instead of returning an
	// error naming the file.
	//
	// A compressed block cannot usefully be bigger than the block it encodes:
	// both CSO and ZSO store a block plain when compressing it would grow it,
	// which is the case this has to leave room for. Double it plus a page for
	// the plain marker and any per-block header, and a corrupt index becomes
	// "invalid block range" on a machine of any size.
	maxRaw := i.blockSize*2 + 4096
	if start < 0 || end < start || end-start > maxRaw {
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
