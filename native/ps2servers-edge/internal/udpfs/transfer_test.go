package udpfs

import (
	"io"
	"net"
	"testing"
	"time"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
)

func newTestServer(t *testing.T) *Server {
	t.Helper()
	s, err := New(Config{
		Root:             t.TempDir(),
		Log:              logging.New(io.Discard, "text", false, false),
		WindowAckTimeout: 5 * time.Millisecond,
		MaxWindowRetries: 4,
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return s
}

// A peer that keeps talking (stale ACKs that never cover the target) must
// never exhaust the window budget: liveness resets the quiet run, exactly as
// the reference server and the Python Core behave. Before the liveness reset,
// MaxWindowRetries consecutive timeouts abandoned the transfer even though
// the console was still polling -- the failure behind "transfer aborted after
// ACK retries" on slow or slightly lossy links.
func TestConfirmWindowPeerActivityResetsQuietBudget(t *testing.T) {
	s := newTestServer(t)
	st := session.New(&net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 9699}, session.Standard)
	for seq := uint16(0); seq < 3; seq++ {
		st.TxBuffer = append(st.TxBuffer, session.BufferedPacket{Sequence: seq})
	}
	target := uint16(3)

	done := make(chan bool, 1)
	go func() { done <- s.confirmWindow(st, target) }()

	// Keep the peer alive with non-covering events well past the quiet budget
	// (retries x timeout = 20 ms; pokes run for ~200 ms), then cover the target.
	poke := time.NewTicker(7 * time.Millisecond)
	defer poke.Stop()
	live := time.NewTimer(200 * time.Millisecond)
	defer live.Stop()
	for {
		select {
		case <-poke.C:
			select {
			case st.AckEvents <- session.AckEvent{ACK: true, Sequence: 0}:
			default:
			}
		case <-live.C:
			st.Mu.Lock()
			st.TransmitAcked = target
			st.Mu.Unlock()
			select {
			case st.AckEvents <- session.AckEvent{ACK: true, Sequence: target}:
			default:
			}
		case result := <-done:
			if !result {
				t.Fatal("confirmWindow gave up on a live peer (quiet counter not reset by activity)")
			}
			return
		}
	}
}

// A truly silent peer still aborts, after the bounded quiet budget.
func TestConfirmWindowQuietPeerAborts(t *testing.T) {
	s := newTestServer(t)
	st := session.New(&net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 9699}, session.Standard)
	st.TxBuffer = append(st.TxBuffer, session.BufferedPacket{Sequence: 0})

	start := time.Now()
	if s.confirmWindow(st, 1) {
		t.Fatal("confirmWindow succeeded with a silent peer")
	}
	if elapsed := time.Since(start); elapsed < 4*5*time.Millisecond {
		t.Fatalf("aborted too early (%v): budget not honored", elapsed)
	}
}

// Forward send-window progress (the console ACKed some but not all of the
// window) is also liveness and must reset the quiet run.
func TestConfirmWindowProgressResetsQuietBudget(t *testing.T) {
	s := newTestServer(t)
	st := session.New(&net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 9699}, session.Standard)
	for seq := uint16(0); seq < 4; seq++ {
		st.TxBuffer = append(st.TxBuffer, session.BufferedPacket{Sequence: seq})
	}
	target := uint16(4)

	done := make(chan bool, 1)
	go func() { done <- s.confirmWindow(st, target) }()

	// Repeatedly prune the head without ever covering the target: the head
	// moves, so each wait must reset the quiet counter.
	prune := time.NewTicker(7 * time.Millisecond)
	defer prune.Stop()
	live := time.NewTimer(200 * time.Millisecond)
	defer live.Stop()
	for {
		select {
		case <-prune.C:
			st.Mu.Lock()
			if len(st.TxBuffer) > 1 {
				st.TxBuffer = st.TxBuffer[1:]
			}
			st.Mu.Unlock()
			select {
			case st.AckEvents <- session.AckEvent{ACK: true, Sequence: 0}:
			default:
			}
		case <-live.C:
			st.Mu.Lock()
			st.TransmitAcked = target
			st.Mu.Unlock()
			select {
			case st.AckEvents <- session.AckEvent{ACK: true, Sequence: target}:
			default:
			}
		case result := <-done:
			if !result {
				t.Fatal("confirmWindow gave up despite forward progress (quiet counter not reset)")
			}
			return
		}
	}
}
