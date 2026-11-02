package smb

import (
	"bytes"
	"encoding/binary"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/filesystem"
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

// TestSessionSummaryCountsAConsole runs OPL's conversation -- handshake,
// FIND_FIRST2, OPEN, READ, CLOSE -- plus a probe for a missing file and an
// unknown command, and checks the one SESSION line printed at shutdown.
func TestSessionSummaryCountsAConsole(t *testing.T) {
	root := t.TempDir()
	payload := bytes.Repeat([]byte{0x5a}, 3000)
	if err := os.WriteFile(filepath.Join(root, "game.iso"), payload, 0o644); err != nil {
		t.Fatal(err)
	}
	r, err := filesystem.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	var log lockedBuffer
	srv, err := New(Config{
		Shares: []Share{{Name: "games", Root: r}}, Bind: "127.0.0.1", Port: 0,
		SessionSummary: true,
		Log:            edgelog.New(&log, "text", true, false), // quiet: the SESSION line must still print
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := srv.Listen(); err != nil {
		t.Fatal(err)
	}
	go func() { _ = srv.Serve() }()

	cl := dial(t, srv)
	cl.handshake("games")

	ff := make([]byte, 12)
	binary.LittleEndian.PutUint16(ff[0:], 0x0016)
	binary.LittleEndian.PutUint16(ff[2:], 1)
	binary.LittleEndian.PutUint16(ff[6:], 0x0104)
	ff = append(ff, []byte("\\*\x00")...)
	cl.trans2(trans2FindFirst2, ff) // one folder list

	h, p, _ := cl.call(ComOpenAndX, make([]byte, 30), []byte("game.iso\x00"))
	if h.Status != StatusSuccess {
		t.Fatalf("open status %#x", h.Status)
	}
	fid := binary.LittleEndian.Uint16(p[4:])
	rq := make([]byte, 24)
	binary.LittleEndian.PutUint16(rq[4:], fid)
	binary.LittleEndian.PutUint16(rq[10:], 1024) // MaxCountLow
	h, rp, _ := cl.call(ComReadAndX, rq, nil)
	if h.Status != StatusSuccess || binary.LittleEndian.Uint16(rp[10:]) != 1024 {
		t.Fatalf("read status %#x", h.Status)
	}
	closeParams := make([]byte, 6)
	binary.LittleEndian.PutUint16(closeParams, fid)
	cl.call(ComClose, closeParams, nil)

	// A probe for an optional file: not an error.
	if h, _, _ = cl.call(ComOpenAndX, make([]byte, 30), []byte("missing.cfg\x00")); h.Status != StatusObjectNameNotFound {
		t.Fatalf("probe status %#x", h.Status)
	}
	// An unknown command: an error.
	if h, _, _ = cl.call(0x99, nil, nil); h.Status != StatusNotImplemented {
		t.Fatalf("unknown command status %#x", h.Status)
	}

	_ = srv.Close() // prints the final line

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
		"service": "smbv1", "client": "127.0.0.1", "connections": int64(1), "list": int64(1),
		"open": int64(2), "read": int64(1), "bytes": int64(1024), "write": int64(0), "close": int64(1),
		"errors": int64(1), "ended": "stopped",
	}
	for k, v := range want {
		if got[k] != v {
			t.Errorf("%s = %v, want %v (line: %v)", k, got[k], v, got)
		}
	}
}

func TestSessionSummaryIsOffByDefault(t *testing.T) {
	srv := startServer(t, t.TempDir(), true)
	if srv.sessions != nil {
		t.Fatal("summary allocated without SessionSummary")
	}
}
