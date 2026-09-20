package udpfs

import (
	"bytes"
	"errors"
	"net"
	"testing"
	"time"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
)

// A fresh Modulo client opens with discovery sequence 0 and waits for the
// compatibility INFORM before sending anything. handleDiscovery used to treat
// sequence 0 as proof of a standard client: it committed Profile to Standard
// and skipped scheduleFallback, so that INFORM never arrived and the client sat
// there until it timed out. Because Profile was already Standard rather than
// Pending, the classify() path in handleData never ran either, so the session
// stayed misclassified for its whole life.
//
// classify(0, 1) returns Modulo correctly, which is why the unit tests passed
// while the shared conformance probe timed out against a real Edge process on
// exactly the fixture named "modulo-fresh" in
// conformance/fixtures/handshake_cases.json.
//
// This test drives the discovery handshake over real sockets, because the bug
// lived in the path between discovery and the first DATA packet -- the part a
// classifier unit test cannot see.
func TestSequenceZeroDiscoveryStillOffersModuloFallback(t *testing.T) {
	_, disc, _ := startTestServer(t, session.Pending)
	client, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()

	if _, err := client.WriteToUDP(discoveryPacket(0), disc); err != nil {
		t.Fatal(err)
	}

	canonical, canonicalFrom := recvPacket(t, client)
	h, err := protocol.ParseHeader(canonical)
	if err != nil || h.Type != protocol.Inform {
		t.Fatalf("first reply was not an INFORM: %v %+v", err, h)
	}
	if canonicalFrom.Port == disc.Port {
		t.Fatalf("canonical INFORM came from the discovery port %d; expected the data port", disc.Port)
	}

	// The fallback is what a fresh Modulo client is waiting on. It must arrive
	// without the client sending any DATA first, and on the discovery port.
	if err := client.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
		t.Fatal(err)
	}
	buf := make([]byte, 64*1024)
	for {
		n, from, err := client.ReadFromUDP(buf)
		if err != nil {
			t.Fatalf("no Modulo fallback INFORM after a sequence-zero discovery: %v "+
				"(a fresh Modulo client would hang here)", err)
		}
		fh, err := protocol.ParseHeader(buf[:n])
		if err != nil || fh.Type != protocol.Inform {
			continue
		}
		if from.Port != disc.Port {
			// Another canonical INFORM; keep waiting for the discovery-port one.
			continue
		}
		return // fallback delivered on the discovery port
	}
}

// The companion case: a standard client that answers promptly must not be sent
// the compatibility INFORM. scheduleFallback bails once the first DATA has
// moved Profile off Pending, and this pins that behaviour so widening the
// sequence-zero path did not start spraying Modulo INFORMs at standard clients.
func TestStandardClientDoesNotReceiveModuloFallback(t *testing.T) {
	_, disc, _ := startTestServer(t, session.Pending)
	client, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()

	if _, err := client.WriteToUDP(discoveryPacket(0), disc); err != nil {
		t.Fatal(err)
	}
	canonical, dataAddr := recvPacket(t, client)
	if h, err := protocol.ParseHeader(canonical); err != nil || h.Type != protocol.Inform {
		t.Fatalf("expected canonical INFORM: %v", err)
	}

	// Standard shape: first DATA sequence equals the discovery sequence.
	open := make([]byte, 8)
	open[0] = byte(protocol.OpenRequest)
	open = append(open, []byte("game.iso\x00")...)
	if _, err := client.WriteToUDP(dataPacket(0, open), dataAddr); err != nil {
		t.Fatal(err)
	}
	respHeader, payload, respAddr := recvDataPayload(t, client)
	if protocol.MessageType(payload[0]) != protocol.OpenReply {
		t.Fatalf("expected OPEN reply, got opcode 0x%02x", payload[0])
	}
	_, _ = client.WriteToUDP(ackPacket(respHeader.Sequence), respAddr)

	// Past the fallback delay, nothing further should arrive from the
	// discovery port.
	if err := client.SetReadDeadline(time.Now().Add(400 * time.Millisecond)); err != nil {
		t.Fatal(err)
	}
	buf := make([]byte, 64*1024)
	for {
		n, from, err := client.ReadFromUDP(buf)
		if err != nil {
			// Only the deadline elapsing proves nothing arrived. Treating any
			// error as success would let a broken socket pass this silently.
			var nerr net.Error
			if errors.As(err, &nerr) && nerr.Timeout() {
				return
			}
			t.Fatalf("unexpected socket error while watching for a stray INFORM: %v", err)
		}
		fh, perr := protocol.ParseHeader(buf[:n])
		if perr == nil && fh.Type == protocol.Inform && from.Port == disc.Port {
			t.Fatal("standard client received a Modulo fallback INFORM it never needed")
		}
	}
}

func prepareHotStandardSession(t *testing.T, server *Server, client *net.UDPConn, disc *net.UDPAddr, expected uint16) *net.UDPAddr {
	t.Helper()
	if _, err := client.WriteToUDP(discoveryPacket(0), disc); err != nil {
		t.Fatal(err)
	}
	_, dataAddr := recvPacket(t, client)

	peer := client.LocalAddr().(*net.UDPAddr)
	w := server.getWorker(peer)
	if w == nil {
		t.Fatal("server did not create peer worker")
	}
	w.state.Mu.Lock()
	w.state.Profile = session.Standard
	w.state.Streaming = true
	w.state.ExpectedReceive = expected
	w.state.PendingZeroDiscovery = time.Time{}
	w.state.LastActivity = time.Now()
	w.state.Mu.Unlock()
	return dataAddr
}

func openGamePayload() []byte {
	open := make([]byte, 8)
	open[0] = byte(protocol.OpenRequest)
	return append(open, []byte("game.iso\x00")...)
}

// Hardware regression from nuno6573: RiptOPL's live UDPFS session expected
// sequence 2213, Neutrino immediately issued DISCOVERY 0 from the same endpoint,
// then began its replacement stream at DATA 1. The old <1s guard preserved the
// 2213 expectation and NACKed DATA 1 forever. The discovery is now provisional:
// DATA 1 resolves it as a fresh Modulo-shaped replacement.
func TestHotSeq0DiscoveryThenData1ReplacesOldSession(t *testing.T) {
	server, disc, _ := startTestServer(t, session.Pending)
	client, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()

	dataAddr := prepareHotStandardSession(t, server, client, disc, 2213)
	if _, err := client.WriteToUDP(discoveryPacket(0), disc); err != nil {
		t.Fatal(err)
	}
	// Candidate discovery still receives the canonical INFORM immediately.
	if p, _ := recvPacket(t, client); len(p) < 6 {
		t.Fatal("short candidate INFORM")
	}

	if _, err := client.WriteToUDP(dataPacket(1, openGamePayload()), dataAddr); err != nil {
		t.Fatal(err)
	}
	_, payload, _ := recvDataPayload(t, client)
	if len(payload) == 0 || protocol.MessageType(payload[0]) != protocol.OpenReply {
		t.Fatalf("replacement DATA 1 was not accepted, payload=%x", payload)
	}

	w := server.getWorker(client.LocalAddr().(*net.UDPAddr))
	w.state.Mu.Lock()
	defer w.state.Mu.Unlock()
	if w.state.Profile != session.Modulo {
		t.Fatalf("replacement profile=%q, want modulo", w.state.Profile)
	}
	if w.state.ExpectedReceive != 2 {
		t.Fatalf("expected receive=%d, want 2 after DATA 1", w.state.ExpectedReceive)
	}
	if !w.state.PendingZeroDiscovery.IsZero() {
		t.Fatal("replacement candidate was not cleared")
	}
}

// A fresh Standard loader uses DATA 0 after the same ambiguous discovery.
// It must also replace the old hot session rather than inherit its sequence.
func TestDelayedSeq0DiscoveryThenData0PreservesFileID(t *testing.T) {
	server, disc, _ := startTestServer(t, session.Pending)
	client, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()

	dataAddr := prepareHotStandardSession(t, server, client, disc, 2213)

	// Neutrino's loader has already opened the ISO and stored the returned
	// server handle in the FILEID settings handed to udpfs_fhi.irx.
	gameData := bytes.Repeat([]byte{0x5a}, 1024)
	w := server.getWorker(client.LocalAddr().(*net.UDPAddr))
	w.state.Mu.Lock()
	w.state.Handles[81] = &session.Handle{Reader: bytes.NewReader(gameData)}
	w.state.NextHandle = 82
	// Loader startup can leave the old stream quiet for longer than one second.
	w.state.LastActivity = time.Now().Add(-2 * sessionReplaceQuiet)
	w.state.Mu.Unlock()

	if _, err := client.WriteToUDP(discoveryPacket(0), disc); err != nil {
		t.Fatal(err)
	}
	_, _ = recvPacket(t, client)
	w.state.Mu.Lock()
	if w.state.PendingZeroDiscovery.IsZero() {
		w.state.Mu.Unlock()
		t.Fatal("delayed discovery discarded the candidate handoff")
	}
	// The first Neutrino DATA can itself be delayed beyond the old cutoff.
	w.state.PendingZeroDiscovery = time.Now().Add(-2 * sessionReplaceQuiet)
	w.state.Mu.Unlock()

	// The first request from the new stage can immediately be a sector read
	// against that pre-opened handle. A full session reset makes this EBADF.
	if _, err := client.WriteToUDP(
		dataPacket(0, blockMsg(protocol.BReadRequest, 81, 0, 1)), dataAddr); err != nil {
		t.Fatal(err)
	}
	_, payload, _ := recvDataPayload(t, client)
	if len(payload) < 8 || protocol.MessageType(payload[0]) != protocol.ResultReply {
		t.Fatalf("replacement FILEID BREAD was not accepted, payload=%x", payload)
	}
	if got := result(payload); got != 512 {
		t.Fatalf("replacement FILEID BREAD returned %d bytes, want 512", got)
	}
	if len(payload) < 8+512 || !bytes.Equal(payload[8:8+512], gameData[:512]) {
		t.Fatal("replacement FILEID BREAD did not return preserved game data")
	}

	w.state.Mu.Lock()
	defer w.state.Mu.Unlock()
	if w.state.Handles[81] == nil {
		t.Fatal("hot handoff discarded Neutrino's pre-opened FILEID handle")
	}
	if w.state.NextHandle != 82 {
		t.Fatalf("next handle=%d, want 82 preserved across hot handoff", w.state.NextHandle)
	}
	if w.state.Profile != session.Standard {
		t.Fatalf("replacement profile=%q, want standard", w.state.Profile)
	}
	if w.state.ExpectedReceive != 1 {
		t.Fatalf("expected receive=%d, want 1 after DATA 0", w.state.ExpectedReceive)
	}
	if !w.state.PendingZeroDiscovery.IsZero() {
		t.Fatal("replacement candidate was not cleared")
	}
}

// Preserve #185's original protection: if the next real DATA continues at the
// old expected sequence, the seq0 discovery was only background rediscovery.
func TestHotSeq0DiscoveryThenExpectedDataPreservesOldSession(t *testing.T) {
	server, disc, _ := startTestServer(t, session.Pending)
	client, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()

	dataAddr := prepareHotStandardSession(t, server, client, disc, 2213)
	if _, err := client.WriteToUDP(discoveryPacket(0), disc); err != nil {
		t.Fatal(err)
	}
	_, _ = recvPacket(t, client)

	if _, err := client.WriteToUDP(dataPacket(2213, openGamePayload()), dataAddr); err != nil {
		t.Fatal(err)
	}
	_, payload, _ := recvDataPayload(t, client)
	if len(payload) == 0 || protocol.MessageType(payload[0]) != protocol.OpenReply {
		t.Fatalf("continuing DATA was not accepted, payload=%x", payload)
	}

	w := server.getWorker(client.LocalAddr().(*net.UDPAddr))
	w.state.Mu.Lock()
	defer w.state.Mu.Unlock()
	if w.state.Profile != session.Standard {
		t.Fatalf("profile changed to %q; old stream should remain standard", w.state.Profile)
	}
	if w.state.ExpectedReceive != 2214 {
		t.Fatalf("expected receive=%d, want 2214 after continuing DATA", w.state.ExpectedReceive)
	}
	if !w.state.PendingZeroDiscovery.IsZero() {
		t.Fatal("background-discovery candidate was not cleared")
	}
}
