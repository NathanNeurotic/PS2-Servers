package udpfs

import (
	"encoding/binary"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
)

// statusQuery asks a running server for its status and decodes the reply.
func statusQuery(t *testing.T, disc *net.UDPAddr) (protocol.StatusReply, error) {
	t.Helper()
	c, err := net.DialUDP("udp4", nil, disc)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	if _, err := c.Write(protocol.MarshalStatusQuery()); err != nil {
		t.Fatal(err)
	}
	_ = c.SetReadDeadline(time.Now().Add(2 * time.Second))
	buf := make([]byte, 1500)
	n, err := c.Read(buf)
	if err != nil {
		return protocol.StatusReply{}, err
	}
	return protocol.ParseStatusReply(buf[:n])
}

func TestStatusQueryReportsReady(t *testing.T) {
	root := t.TempDir()
	disc := startServerAt(t, root, false)

	reply, err := statusQuery(t, disc)
	if err != nil {
		t.Fatalf("no usable status reply: %v", err)
	}
	if reply.State != protocol.StateReady {
		t.Fatalf("state = %v, want ready", reply.State)
	}
	if reply.Flags&protocol.FlagServesUDPFS == 0 {
		t.Fatal("UDPFS flag not set on a UDPFS server")
	}
	if reply.Flags&protocol.FlagReadOnly != 0 {
		t.Fatal("read-only flag set on a writable server")
	}
	if reply.Name == "" {
		t.Fatal("no server name in the reply")
	}
}

func TestStatusReportsReadOnly(t *testing.T) {
	disc := startServerAt(t, t.TempDir(), true)
	reply, err := statusQuery(t, disc)
	if err != nil {
		t.Fatal(err)
	}
	if reply.Flags&protocol.FlagReadOnly == 0 {
		t.Fatal("read-only server did not set the read-only flag")
	}
}

func TestStatusQueryCreatesNoSession(t *testing.T) {
	// The property that makes polling safe. A launcher asking once a second
	// must not manufacture sessions -- they would occupy slots against the
	// concurrent-session cap and show up in the very count being reported.
	disc := startServerAt(t, t.TempDir(), false)

	for i := 0; i < 5; i++ {
		reply, err := statusQuery(t, disc)
		if err != nil {
			t.Fatal(err)
		}
		if reply.Sessions != 0 {
			t.Fatalf("after %d status queries the server reports %d sessions; polling is creating them",
				i+1, reply.Sessions)
		}
	}
}

func TestStatusDegradesWhenTheShareDisappears(t *testing.T) {
	// The ordinary hardware failure: someone pulls the USB stick. Answering
	// "ready" would send them hunting the network for a storage fault.
	root := filepath.Join(t.TempDir(), "games")
	if err := os.Mkdir(root, 0o755); err != nil {
		t.Fatal(err)
	}
	disc := startServerAt(t, root, false)

	if reply, err := statusQuery(t, disc); err != nil || reply.State != protocol.StateReady {
		t.Fatalf("expected ready before removal, got %v (%v)", reply.State, err)
	}
	if err := os.Remove(root); err != nil {
		t.Skipf("cannot remove the share root on this platform: %v", err)
	}
	reply, err := statusQuery(t, disc)
	if err != nil {
		t.Fatal(err)
	}
	if reply.State != protocol.StateDegraded {
		t.Fatalf("state = %v after the share vanished, want degraded", reply.State)
	}
}

func TestOrdinaryDiscoveryStillWorksAlongsideStatus(t *testing.T) {
	// The compatibility guarantee, from the server side: adding the status
	// path must not have altered the DISCOVERY/INFORM exchange the console
	// depends on. INFORM is six bytes and its shape is load-bearing.
	disc := startServerAt(t, t.TempDir(), false)

	c, err := net.DialUDP("udp4", nil, disc)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	query := append(protocol.Header{Type: protocol.Discovery, Sequence: 0}.Marshal(),
		protocol.DiscoveryHeader{ServiceID: protocol.ServiceUDPFS, Port: 0}.Marshal()...)
	if _, err := c.Write(query); err != nil {
		t.Fatal(err)
	}
	_ = c.SetReadDeadline(time.Now().Add(2 * time.Second))
	buf := make([]byte, 1500)
	n, err := c.Read(buf)
	if err != nil {
		t.Fatalf("no INFORM for an ordinary DISCOVERY: %v", err)
	}
	// Length is not asserted. In Pending mode the reply arriving on the
	// discovery socket is the Modulo compatibility INFORM, which carries an
	// info payload after the six-byte core -- the canonical six-byte INFORM
	// goes to the data socket. Pinning a length here would be pinning which
	// profile the fallback happened to pick, which is not what this test is
	// about.
	if n < 6 {
		t.Fatalf("INFORM is %d bytes, shorter than the six-byte core", n)
	}
	h, err := protocol.ParseHeader(buf[:n])
	if err != nil || h.Type != protocol.Inform {
		t.Fatalf("reply is not an INFORM: %v %v", h, err)
	}
	if got := binary.LittleEndian.Uint16(buf[2:]); got != protocol.ServiceUDPFS {
		t.Fatalf("INFORM service ID = %#x, want %#x", got, protocol.ServiceUDPFS)
	}
}

func TestUnknownServiceIDIsStillIgnored(t *testing.T) {
	// The other half of the compatibility rule: a service ID that is neither
	// UDPFS nor status must still be dropped silently. That is the behaviour
	// this whole design is built on, so it is pinned rather than assumed --
	// answering foreign discovery packets would make this server a reflector.
	disc := startServerAt(t, t.TempDir(), false)

	c, err := net.DialUDP("udp4", nil, disc)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	query := append(protocol.Header{Type: protocol.Discovery, Sequence: 0}.Marshal(),
		protocol.DiscoveryHeader{ServiceID: 0x1234, Port: 0}.Marshal()...)
	if _, err := c.Write(query); err != nil {
		t.Fatal(err)
	}
	_ = c.SetReadDeadline(time.Now().Add(500 * time.Millisecond))
	buf := make([]byte, 1500)
	if n, err := c.Read(buf); err == nil {
		t.Fatalf("server replied with %d bytes to an unknown service ID", n)
	}
}
