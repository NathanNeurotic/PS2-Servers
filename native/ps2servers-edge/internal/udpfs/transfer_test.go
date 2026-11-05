package udpfs

import (
	"io"
	"net"
	"testing"
	"time"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
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
		st.TxBuffer = append(st.TxBuffer, session.BufferedPacket{Sequence: seq, Sent: true})
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
	st.TxBuffer = append(st.TxBuffer, session.BufferedPacket{Sequence: 0, Sent: true})

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
		st.TxBuffer = append(st.TxBuffer, session.BufferedPacket{Sequence: seq, Sent: true})
	}
	target := uint16(4)

	done := make(chan bool, 1)
	go func() { done <- s.confirmWindow(st, target) }()

	// Repeatedly move the buffer head without ever covering the target: the
	// head moves, so each wait must reset the quiet counter. No AckEvents are
	// injected here -- channel activity resets the quiet run on its own, which
	// would mask a broken progress check and let this test pass with its named
	// behavior removed.
	next := uint16(4)
	prune := time.NewTicker(7 * time.Millisecond)
	defer prune.Stop()
	live := time.NewTimer(200 * time.Millisecond)
	defer live.Stop()
	for {
		select {
		case <-prune.C:
			st.Mu.Lock()
			st.TxBuffer = append(st.TxBuffer[1:], session.BufferedPacket{Sequence: next, Sent: true})
			next++
			st.Mu.Unlock()
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

// A console that keeps broadcasting sequence-zero discovery mid-reply is
// still alive: both client families poll discovery for the life of a
// transfer, and the reference server resets its retransmit budget on any
// inbound peer packet. The preserved-stream path in handleDiscovery must
// surface that validated packet as ACK-channel activity, or confirmWindow
// can exhaust its quiet budget and abort the reply of a console that never
// stopped talking -- the loop reported in #240.
func TestStreamingDiscoverySignalsPeerActivity(t *testing.T) {
	s := newTestServer(t)
	peer := &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 9699}

	pkt := discoveryPacket(0)
	h, err := protocol.ParseHeader(pkt)
	if err != nil {
		t.Fatal(err)
	}
	s.handleDiscovery(inbound{packet: pkt, peer: peer, socket: session.DiscoverySocket}, h)
	w := s.getWorker(peer)
	if w == nil {
		t.Fatal("discovery did not create a session")
	}
	st := w.state
	st.Mu.Lock()
	st.Streaming = true
	st.Mu.Unlock()

	// Drain any event the establishing discovery queued, then confirm a
	// preserved-stream discovery registers liveness.
	for {
		select {
		case <-st.AckEvents:
		default:
			goto drained
		}
	}
drained:
	s.handleDiscovery(inbound{packet: pkt, peer: peer, socket: session.DiscoverySocket}, h)
	select {
	case ev := <-st.AckEvents:
		if ev.ACK {
			t.Fatal("discovery must not pose as an ACK covering progress")
		}
	case <-time.After(time.Second):
		t.Fatal("streaming discovery did not signal peer activity")
	}
}
