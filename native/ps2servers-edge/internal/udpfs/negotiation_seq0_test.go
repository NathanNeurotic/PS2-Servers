package udpfs

import (
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
