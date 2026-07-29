package compression

import (
	"testing"
)

// cacheTotal is what the cache is actually holding, summed from the blocks
// themselves rather than from the counter, so the counter can be checked
// against reality instead of against itself.
func cacheTotal(i *Image) int64 {
	var n int64
	for _, b := range i.cache {
		n += int64(len(b))
	}
	return n
}

func TestCacheIsBoundedByBytesNotJustBlockCount(t *testing.T) {
	// The case the block count does not bound: a large declared block size.
	// 32 blocks sounds small and is 512 MiB at the maximum block size the
	// header may legally declare.
	budget := cacheByteBudget()
	blockSize := int(budget/4) + 1 // four of these overrun the budget

	img := &Image{cacheLimit: DefaultCacheBlocks}
	for n := int64(0); n < DefaultCacheBlocks; n++ {
		img.cacheStore(n, make([]byte, blockSize))
		if got := cacheTotal(img); got > budget {
			t.Fatalf("after %d blocks the cache holds %d bytes, over the %d budget",
				n+1, got, budget)
		}
	}
	// The count limit was never reached, so the byte budget must be what
	// stopped it: far fewer than 32 blocks are resident.
	if len(img.cache) >= DefaultCacheBlocks {
		t.Fatalf("cache holds %d blocks; the byte budget did not bind", len(img.cache))
	}
}

func TestCounterTracksWhatIsActuallyHeld(t *testing.T) {
	// Eviction has to credit the counter back, or the cache silently stops
	// accepting anything once the budget has been "spent" by blocks that were
	// dropped long ago.
	img := &Image{cacheLimit: 4}
	for n := int64(0); n < 32; n++ {
		img.cacheStore(n, make([]byte, 1024))
	}
	if len(img.cache) != 4 {
		t.Fatalf("cache holds %d blocks, want the 4-block limit", len(img.cache))
	}
	if img.cacheBytes != cacheTotal(img) {
		t.Fatalf("counter says %d, blocks total %d", img.cacheBytes, cacheTotal(img))
	}
	if img.cacheBytes != 4*1024 {
		t.Fatalf("counter = %d, want %d", img.cacheBytes, 4*1024)
	}
}

func TestBlockLargerThanTheBudgetIsNotCached(t *testing.T) {
	// It must not empty the cache and then store the oversized block anyway.
	img := &Image{cacheLimit: DefaultCacheBlocks}
	img.cacheStore(0, make([]byte, 2048))
	img.cacheStore(1, make([]byte, int(cacheByteBudget())+1))

	if _, ok := img.cache[1]; ok {
		t.Fatal("a block larger than the whole budget was cached")
	}
	if _, ok := img.cache[0]; !ok {
		t.Fatal("the existing block was evicted to make room for one that was then not stored")
	}
	if img.cacheBytes != cacheTotal(img) {
		t.Fatalf("counter says %d, blocks total %d", img.cacheBytes, cacheTotal(img))
	}
}

func TestSetCacheBlocksResetsTheByteCounter(t *testing.T) {
	// SetCacheBlocks drops the cache; if the counter survived, the budget
	// would stay spent for the life of the handle and nothing would cache
	// again.
	img := &Image{cacheLimit: 8}
	for n := int64(0); n < 8; n++ {
		img.cacheStore(n, make([]byte, 4096))
	}
	if img.cacheBytes == 0 {
		t.Fatal("nothing was cached, the test proves nothing")
	}

	img.SetCacheBlocks(8)
	if img.cacheBytes != 0 {
		t.Fatalf("cacheBytes = %d after reset, want 0", img.cacheBytes)
	}

	// And it still caches afterwards.
	img.cacheStore(99, make([]byte, 4096))
	if _, ok := img.cache[99]; !ok {
		t.Fatal("cache refused a block after SetCacheBlocks")
	}
	if img.cacheBytes != cacheTotal(img) {
		t.Fatalf("counter says %d, blocks total %d", img.cacheBytes, cacheTotal(img))
	}
}

func TestOrdinaryBlocksStillFillTheCountLimit(t *testing.T) {
	// The regression guard for the normal case: at the conventional 2 KiB
	// block the count is what binds, exactly as before, so behaviour on a
	// desktop is unchanged.
	img := &Image{cacheLimit: DefaultCacheBlocks}
	for n := int64(0); n < DefaultCacheBlocks*2; n++ {
		img.cacheStore(n, make([]byte, 2048))
	}
	if len(img.cache) != DefaultCacheBlocks {
		t.Fatalf("cache holds %d blocks, want the full %d", len(img.cache), DefaultCacheBlocks)
	}
	if img.cacheBytes != DefaultCacheBlocks*2048 {
		t.Fatalf("cacheBytes = %d, want %d", img.cacheBytes, DefaultCacheBlocks*2048)
	}
}
