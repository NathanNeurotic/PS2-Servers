package udpfs

import (
	"bytes"
	"context"
	"encoding/binary"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	edgelog "github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/sessionstats"
)

// lockedBuffer collects the server's log from its goroutines.
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

// sessionLines picks the SESSION lines out of a captured log.
func sessionLines(log string) []map[string]any {
	var out []map[string]any
	for _, line := range strings.Split(log, "\n") {
		if f := sessionstats.Parse(line); f != nil {
			out = append(out, f)
		}
	}
	return out
}

// TestSessionSummaryCountsAConsole drives a real server over loopback the way
// a console does -- DISCOVERY, OPEN, READ, a probe for a missing file, a bad
// handle -- and checks the one SESSION line printed at shutdown.
func TestSessionSummaryCountsAConsole(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "game.iso"), []byte("0123456789abcdef"), 0o644); err != nil {
		t.Fatal(err)
	}
	var log lockedBuffer
	server, err := New(Config{
		Root: root, Bind: "127.0.0.1", Port: freeUDPPort(t), PeerTimeout: time.Minute,
		FallbackDelay: 30 * time.Millisecond, SessionSummary: true,
		Log: edgelog.New(&log, "text", true, false), // quiet: the SESSION line must still print
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := server.Listen(); err != nil {
		t.Fatal(err)
	}
	disc, _ := server.Addr()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- server.Serve(ctx) }()

	client, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	if _, err = client.WriteToUDP(discoveryPacket(0), disc); err != nil {
		t.Fatal(err)
	}
	inform, dataAddr := recvPacket(t, client)
	if h, err := protocol.ParseHeader(inform); err != nil || h.Type != protocol.Inform {
		t.Fatalf("bad INFORM: %v %+v", err, h)
	}

	seq := uint16(0)
	exchange := func(req []byte) []byte {
		t.Helper()
		if _, err := client.WriteToUDP(dataPacket(seq, req), dataAddr); err != nil {
			t.Fatal(err)
		}
		seq++
		respHeader, payload, responseAddr := recvDataPayload(t, client)
		_, _ = client.WriteToUDP(ackPacket(respHeader.Sequence), responseAddr)
		return payload
	}
	openReq := func(name string) []byte {
		b := make([]byte, 8)
		b[0] = byte(protocol.OpenRequest)
		binary.LittleEndian.PutUint16(b[2:4], 1)
		return append(b, []byte(name+"\x00")...)
	}
	readReq := func(handle int32, size uint32) []byte {
		b := make([]byte, 12)
		b[0] = byte(protocol.ReadRequest)
		binary.LittleEndian.PutUint32(b[4:8], uint32(handle))
		binary.LittleEndian.PutUint32(b[8:12], size)
		return b
	}

	payload := exchange(openReq("game.iso"))
	handle := int32(binary.LittleEndian.Uint32(payload[4:8]))
	if handle <= 0 {
		t.Fatalf("OPEN result %d", handle)
	}
	if payload = exchange(readReq(handle, 16)); string(payload[8:24]) != "0123456789abcdef" {
		t.Fatalf("read %q", payload[8:])
	}
	if payload = exchange(openReq("missing.cfg")); int32(binary.LittleEndian.Uint32(payload[4:8])) != -int32(syscall.ENOENT) {
		t.Fatalf("probe result %d", int32(binary.LittleEndian.Uint32(payload[4:8])))
	}
	if payload = exchange(readReq(999, 16)); int32(binary.LittleEndian.Uint32(payload[4:8])) != -int32(syscall.EBADF) {
		t.Fatalf("bad handle result %d", int32(binary.LittleEndian.Uint32(payload[4:8])))
	}
	// One more round trip so the previous ACK has certainly been consumed
	// before the server is stopped from under the worker.
	exchange(openReq("missing.cfg"))

	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("Serve: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("server did not stop")
	}

	lines := sessionLines(log.String())
	if len(lines) != 1 {
		t.Fatalf("want one SESSION line, got %d in:\n%s", len(lines), log.String())
	}
	got := lines[0]
	want := map[string]any{
		"service": "udpfs", "client": "127.0.0.1", "connections": int64(1), "list": int64(0),
		"open": int64(3), "read": int64(2), "bytes": int64(16), "write": int64(0), "close": int64(0),
		"errors": int64(1), "ended": "stopped",
	}
	for k, v := range want {
		if got[k] != v {
			t.Errorf("%s = %v, want %v (line: %v)", k, got[k], v, got)
		}
	}
}

// TestSessionSummaryIsOffByDefault: without the flag nothing is allocated and
// nothing is printed, so a router that did not ask pays nothing.
func TestSessionSummaryIsOffByDefault(t *testing.T) {
	server, _, _ := startTestServer(t, session.Pending)
	if server.summary != nil {
		t.Fatal("summary allocated without SessionSummary")
	}
}
