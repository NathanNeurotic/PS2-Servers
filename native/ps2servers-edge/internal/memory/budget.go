// Package memory sizes the server's buffers against the machine it is actually
// running on.
//
// Edge targets everything from a desktop to a 32 MB router. The transfer
// ceilings were chosen for the former: one BREAD or one assembled write may
// allocate 8 MiB, and the write buffer is reserved eagerly, before a single
// byte of the payload has arrived. On a PC that is a rounding error. On an
// A5-V11 -- 32 MB of SDRAM total, with OpenWrt already resident -- it is a
// quarter of the machine per operation, and two concurrent transfers are
// enough to get the process OOM-killed.
//
// So the ceiling becomes a function of the hardware rather than a constant.
// Where the total cannot be determined the previous fixed value is used
// unchanged, which keeps every existing platform behaving exactly as it did.
package memory

import (
	"bufio"
	"os"
	"strconv"
	"strings"
)

// Budget is what the host reports about itself.
type Budget struct {
	// TotalBytes is the machine's physical RAM, or 0 when it could not be
	// determined. Zero is not an error: it means "do not scale anything", and
	// every caller must treat it as "use the value you would have used before
	// this package existed".
	TotalBytes int64
	// Source records where the number came from, for the startup log. An
	// operator debugging a transfer that is smaller than they expected should
	// be able to see why from the log alone.
	Source string
}

const (
	// transferDivisor bounds one transfer to this fraction of total RAM.
	//
	// 1/16 leaves room for the several buffers that can legitimately be live
	// at once -- concurrent sessions, the decompression cache, the runtime's
	// own heap -- without arithmetic that has to know about each of them. On a
	// 32 MB box it yields 2 MiB, which is still an order of magnitude above
	// any real PS2 request: a console reads in sector-sized chunks and the
	// largest transfer observed from any client is well under 256 KiB.
	transferDivisor = 16

	// minTransferCap is the floor. Scaling must never produce a cap so small
	// that an ordinary console request is refused; a server that boots and
	// then rejects every read is worse than one that risks memory pressure.
	minTransferCap = 256 << 10

	// smallMachine is the ceiling below which a soft heap limit is worth
	// setting. Above it the limit would never be approached and would only be
	// one more number that can be wrong.
	smallMachine = 512 << 20
)

// Detect reads the machine's total RAM.
//
// Linux only, by reading /proc/meminfo -- which is the platform every router
// build targets, and the only one where the answer changes anything. Elsewhere
// the file is simply absent and the budget is unknown, so no build tags are
// needed and the same code path is exercised on every platform.
func Detect() Budget { return detectFrom("/proc/meminfo") }

func detectFrom(path string) Budget {
	f, err := os.Open(path)
	if err != nil {
		return Budget{Source: "unknown"}
	}
	defer f.Close()

	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := scanner.Text()
		if !strings.HasPrefix(line, "MemTotal:") {
			continue
		}
		// "MemTotal:       30516 kB" -- the unit is always kB in practice, but
		// it is checked rather than assumed, because silently treating some
		// other unit as kB would size every buffer wrong by three orders of
		// magnitude in whichever direction.
		fields := strings.Fields(line)
		if len(fields) < 3 || !strings.EqualFold(fields[2], "kB") {
			return Budget{Source: "unknown"}
		}
		kb, err := strconv.ParseInt(fields[1], 10, 64)
		if err != nil || kb <= 0 {
			return Budget{Source: "unknown"}
		}
		return Budget{TotalBytes: kb * 1024, Source: path}
	}
	return Budget{Source: "unknown"}
}

// TransferCap is the largest single transfer buffer worth allowing.
//
// preferred is the value the caller would use on a machine with memory to
// spare; the result is never larger than it. An unknown budget returns
// preferred unchanged, so a platform this package cannot measure keeps exactly
// the behaviour it had before.
func (b Budget) TransferCap(preferred int64) int64 {
	if b.TotalBytes <= 0 {
		return preferred
	}
	cap := b.TotalBytes / transferDivisor
	if cap > preferred {
		return preferred
	}
	if cap < minTransferCap {
		return minTransferCap
	}
	return cap
}

// SoftLimit is a value for debug.SetMemoryLimit, or 0 to leave the runtime
// alone.
//
// This is a backstop, not the fix. A soft limit makes the collector work
// harder as the heap approaches it; it does not make an allocation fail, so on
// its own it cannot stop a single oversized buffer from taking the process
// down -- that is what TransferCap is for. Its value is in reclaiming
// aggressively once the heap is larger than this machine can comfortably hold,
// instead of letting it drift up to whatever the OOM killer notices first.
//
// Only set on small machines. Half of total sits far above the few megabytes
// steady-state use measured on this server, so reaching it means something has
// already gone wrong and collecting hard is the right response.
func (b Budget) SoftLimit() int64 {
	if b.TotalBytes <= 0 || b.TotalBytes > smallMachine {
		return 0
	}
	return b.TotalBytes / 2
}
