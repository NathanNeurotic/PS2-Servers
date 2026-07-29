package memory

import (
	"os"
	"path/filepath"
	"testing"
)

// writeMeminfo puts a /proc/meminfo-shaped file on disk so detection can be
// tested on any platform, including the Windows machines this is developed on
// where the real file does not exist.
func writeMeminfo(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "meminfo")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

// The A5-V11 is the machine this whole package exists for: 32 MB of SDRAM, and
// the desktop 8 MiB ceiling is a quarter of it per transfer.
const a5v11 = `MemTotal:          30516 kB
MemFree:            9200 kB
Buffers:             800 kB
`

func TestDetectsRouterMemory(t *testing.T) {
	b := detectFrom(writeMeminfo(t, a5v11))
	if b.TotalBytes != 30516*1024 {
		t.Fatalf("TotalBytes = %d, want %d", b.TotalBytes, 30516*1024)
	}
	if b.Source == "unknown" {
		t.Fatal("Source should name the file it read")
	}
}

func TestRouterGetsASmallerCapThanTheDesktopCeiling(t *testing.T) {
	b := detectFrom(writeMeminfo(t, a5v11))
	got := b.TransferCap(8 << 20)
	if got >= 8<<20 {
		t.Fatalf("cap %d was not reduced below the 8 MiB ceiling", got)
	}
	// Still far above anything a console asks for. A cap that throttled real
	// traffic would be a worse bug than the one this fixes.
	if got < 1<<20 {
		t.Fatalf("cap %d is below 1 MiB, too small for ordinary transfers", got)
	}
	// And it must be a genuinely small slice of the machine, not a token cut.
	if got > b.TotalBytes/8 {
		t.Fatalf("cap %d is more than an eighth of %d", got, b.TotalBytes)
	}
}

func TestUnknownMemoryChangesNothing(t *testing.T) {
	// The critical compatibility property: on any platform where the total
	// cannot be read -- Windows, macOS, an odd container -- the caller must get
	// back exactly the constant it would have used before this package existed.
	b := detectFrom(filepath.Join(t.TempDir(), "does-not-exist"))
	if b.TotalBytes != 0 {
		t.Fatalf("TotalBytes = %d, want 0 for an unreadable source", b.TotalBytes)
	}
	if got := b.TransferCap(8 << 20); got != 8<<20 {
		t.Fatalf("cap = %d, want the preferred 8 MiB unchanged", got)
	}
	if got := b.SoftLimit(); got != 0 {
		t.Fatalf("SoftLimit = %d, want 0 so the runtime is left alone", got)
	}
}

func TestLargeMachineKeepsThePreferredCeiling(t *testing.T) {
	b := detectFrom(writeMeminfo(t, "MemTotal:       16305200 kB\n"))
	if got := b.TransferCap(8 << 20); got != 8<<20 {
		t.Fatalf("cap = %d, want the preferred 8 MiB on a 16 GB machine", got)
	}
	// No soft limit on a big machine: it would never be reached and would only
	// be one more number that can be wrong.
	if got := b.SoftLimit(); got != 0 {
		t.Fatalf("SoftLimit = %d, want 0 on a large machine", got)
	}
}

func TestSoftLimitIsSetOnSmallMachinesAndLeavesHeadroom(t *testing.T) {
	b := detectFrom(writeMeminfo(t, a5v11))
	limit := b.SoftLimit()
	if limit <= 0 {
		t.Fatal("a 32 MB machine should get a soft limit")
	}
	if limit >= b.TotalBytes {
		t.Fatalf("SoftLimit %d is not below total %d", limit, b.TotalBytes)
	}
	// Measured steady-state use is a few MB, so the limit must sit well above
	// that or the collector would thrash during ordinary operation.
	if limit < 8<<20 {
		t.Fatalf("SoftLimit %d is below 8 MiB and would thrash", limit)
	}
}

func TestNeverReturnsACapTooSmallToServe(t *testing.T) {
	// A machine small enough that total/16 falls under the floor. The server
	// must still be able to satisfy an ordinary request: booting and then
	// refusing every read is worse than risking memory pressure.
	b := detectFrom(writeMeminfo(t, "MemTotal:           2048 kB\n"))
	if got := b.TransferCap(8 << 20); got != minTransferCap {
		t.Fatalf("cap = %d, want the %d floor", got, minTransferCap)
	}
}

func TestMalformedMeminfoIsTreatedAsUnknown(t *testing.T) {
	// Anything unparseable must fall back to "unknown" rather than to a number.
	// Guessing here would size every buffer in the process wrong.
	for name, body := range map[string]string{
		"no MemTotal line": "MemFree:  1000 kB\n",
		"missing unit":     "MemTotal:       30516\n",
		"unexpected unit":  "MemTotal:       30516 MB\n",
		"not a number":     "MemTotal:       banana kB\n",
		"negative":         "MemTotal:       -5 kB\n",
		"empty":            "",
	} {
		t.Run(name, func(t *testing.T) {
			b := detectFrom(writeMeminfo(t, body))
			if b.TotalBytes != 0 {
				t.Fatalf("TotalBytes = %d, want 0", b.TotalBytes)
			}
			if got := b.TransferCap(8 << 20); got != 8<<20 {
				t.Fatalf("cap = %d, want preferred unchanged", got)
			}
		})
	}
}

func TestCapNeverExceedsPreferred(t *testing.T) {
	// Whatever the machine reports, the caller's ceiling is a ceiling. A
	// 16 GB box must not raise an 8 MiB preference to 1 GB.
	for _, kb := range []int64{2048, 30516, 131072, 1048576, 16305200} {
		b := Budget{TotalBytes: kb * 1024, Source: "test"}
		if got := b.TransferCap(8 << 20); got > 8<<20 {
			t.Fatalf("total %d kB produced cap %d, above the preferred ceiling", kb, got)
		}
	}
}
