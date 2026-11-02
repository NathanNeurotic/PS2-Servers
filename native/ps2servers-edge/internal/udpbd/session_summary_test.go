package udpbd

import (
	"bytes"
	"context"
	"math/rand"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	edgelog "github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/sessionstats"
)

type lockedBuffer struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (l *lockedBuffer) Write(p []byte) (int, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.b.Write(p)
}

func (l *lockedBuffer) String() string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.b.String()
}

// TestSessionSummaryCountsAConsole: an INFO (the console mounting the disk),
// a one-sector read, and an out-of-range read, then the line at shutdown.
func TestSessionSummaryCountsAConsole(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "test.img")
	img := make([]byte, 1<<20)
	rand.New(rand.NewSource(99)).Read(img)
	if err := os.WriteFile(path, img, 0o644); err != nil {
		t.Fatal(err)
	}
	var log lockedBuffer
	srv, err := New(Config{
		Image: path, Bind: "127.0.0.1", Port: freePort(t), SessionSummary: true,
		Log: edgelog.New(&log, "text", true, false), // quiet: the SESSION line must still print
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := srv.Listen(); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- srv.Serve(ctx) }()

	c := dialServer(t, srv.Addr())
	buf := make([]byte, 2048)
	if _, err := c.Write(append(packHeader(CmdInfo, 0, 0), make([]byte, 6)...)); err != nil {
		t.Fatal(err)
	}
	if _, err := readPacket(t, c, buf); err != nil {
		t.Fatal(err)
	}
	if got := readSectors(t, c, 1, 5, 1); !bytes.Equal(got, img[5*SectorSize:6*SectorSize]) {
		t.Fatal("sector 5 did not match the image")
	}
	sectors := uint32(len(img) / SectorSize)
	if _, err := c.Write(sectorReq(CmdRead, 2, sectors-1, 64)); err != nil {
		t.Fatal(err)
	}
	// The refusal is silent; a following INFO proves the server got there.
	if _, err := c.Write(append(packHeader(CmdInfo, 0, 0), make([]byte, 6)...)); err != nil {
		t.Fatal(err)
	}
	if _, err := readPacket(t, c, buf); err != nil {
		t.Fatal(err)
	}

	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("server did not stop")
	}
	srv.Close() // prints the final line

	var lines []map[string]any
	for _, line := range strings.Split(log.String(), "\n") {
		if f := sessionstats.Parse(line); f != nil {
			lines = append(lines, f)
		}
	}
	if len(lines) != 1 {
		t.Fatalf("want one SESSION line, got %d in:\n%s", len(lines), log.String())
	}
	got := lines[0]
	want := map[string]any{
		"service": "udpbd", "client": "127.0.0.1", "connections": int64(2), "list": int64(0),
		"open": int64(0), "read": int64(2), "bytes": int64(SectorSize), "write": int64(0),
		"close": int64(0), "errors": int64(1), "ended": "stopped",
	}
	for k, v := range want {
		if got[k] != v {
			t.Errorf("%s = %v, want %v (line: %v)", k, got[k], v, got)
		}
	}
}

func TestSessionSummaryIsOffByDefault(t *testing.T) {
	path := filepath.Join(t.TempDir(), "test.img")
	if err := os.WriteFile(path, make([]byte, 4*SectorSize), 0o644); err != nil {
		t.Fatal(err)
	}
	srv, err := New(Config{Image: path, Log: edgelog.New(discard{}, "text", true, false)})
	if err != nil {
		t.Fatal(err)
	}
	defer srv.Close()
	if srv.sessions != nil {
		t.Fatal("summary allocated without SessionSummary")
	}
}
