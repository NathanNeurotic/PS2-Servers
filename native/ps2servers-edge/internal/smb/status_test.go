package smb

import (
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/filesystem"
	edgelog "github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
)

// The router status protocol exists because a board serving a console should
// be able to say whether it is ready. Before this, only UDPFS answered -- so a
// router running SMB, which is the case the protocol was actually requested
// for, went dark to the launcher on the one service it was built to provide.

func startSMBWithStatus(t *testing.T, root string, readOnly bool) *Server {
	t.Helper()
	r, err := filesystem.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	srv, err := New(Config{
		Shares:     []Share{{Name: "games", Root: r}},
		Bind:       "127.0.0.1",
		Port:       0,
		ReadOnly:   readOnly,
		StatusPort: 0, // replaced below with an ephemeral port
		Log:        edgelog.New(discardWriter{}, "text", true, false),
	})
	if err != nil {
		t.Fatal(err)
	}
	// Ephemeral rather than the standard port: the tests must not fight each
	// other, or a udpfs server on the same machine, for a fixed address.
	srv.cfg.StatusPort = ephemeralUDP(t)
	if err := srv.Listen(); err != nil {
		t.Fatal(err)
	}
	go func() { _ = srv.Serve() }()
	t.Cleanup(func() { _ = srv.Close() })
	return srv
}

func ephemeralUDP(t *testing.T) int {
	t.Helper()
	c, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	port := c.LocalAddr().(*net.UDPAddr).Port
	_ = c.Close()
	return port
}

func askStatus(t *testing.T, srv *Server) protocol.StatusReply {
	t.Helper()
	addr := &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: srv.status.Addr().Port}
	c, err := net.DialUDP("udp4", nil, addr)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	if _, err := c.Write(protocol.MarshalStatusQuery()); err != nil {
		t.Fatal(err)
	}
	_ = c.SetReadDeadline(time.Now().Add(3 * time.Second))
	buf := make([]byte, 1500)
	n, err := c.Read(buf)
	if err != nil {
		t.Fatalf("no status reply from the SMB server: %v", err)
	}
	reply, err := protocol.ParseStatusReply(buf[:n])
	if err != nil {
		t.Fatalf("status reply did not parse: %v", err)
	}
	return reply
}

func TestSMBAnswersStatusAndSaysItServesSMB(t *testing.T) {
	srv := startSMBWithStatus(t, t.TempDir(), false)
	reply := askStatus(t, srv)

	if reply.State != protocol.StateReady {
		t.Fatalf("state = %v, want ready", reply.State)
	}
	if reply.Flags&protocol.FlagServesSMB == 0 {
		t.Fatal("the SMB flag is not set, so a launcher cannot tell what this is")
	}
	if reply.Flags&protocol.FlagReadOnly != 0 {
		t.Fatal("read-only flag set on a writable server")
	}
	if reply.Name == "" {
		t.Fatal("no server name")
	}
}

func TestSMBStatusReportsReadOnly(t *testing.T) {
	srv := startSMBWithStatus(t, t.TempDir(), true)
	if reply := askStatus(t, srv); reply.Flags&protocol.FlagReadOnly == 0 {
		t.Fatal("read-only server did not report it")
	}
}

func TestSMBStatusGoesBusyWhileAConsoleIsConnected(t *testing.T) {
	// SMB has no idle state a server can observe -- a console holds the
	// connection open across a whole game load -- so a live connection is the
	// honest answer to "is something happening", and it is the one that
	// matters for "do not cut power".
	srv := startSMBWithStatus(t, t.TempDir(), false)
	if reply := askStatus(t, srv); reply.State != protocol.StateReady {
		t.Fatalf("state = %v before any connection, want ready", reply.State)
	}

	c, err := net.Dial("tcp", srv.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		reply := askStatus(t, srv)
		if reply.State == protocol.StateBusy && reply.Sessions >= 1 {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("status never reported busy while a connection was open")
}

func TestSMBStatusDegradesWhenTheShareVanishes(t *testing.T) {
	// The ordinary hardware failure on a router: someone pulls the USB stick.
	// Reporting ready would send them hunting the network for a storage fault.
	root := filepath.Join(t.TempDir(), "games")
	if err := os.Mkdir(root, 0o755); err != nil {
		t.Fatal(err)
	}
	srv := startSMBWithStatus(t, root, false)
	if reply := askStatus(t, srv); reply.State != protocol.StateReady {
		t.Fatalf("state = %v before removal, want ready", reply.State)
	}
	if err := os.Remove(root); err != nil {
		t.Skipf("cannot remove the share root here: %v", err)
	}
	if reply := askStatus(t, srv); reply.State != protocol.StateDegraded {
		t.Fatalf("state = %v after the share vanished, want degraded", reply.State)
	}
}

func TestSMBStartsEvenWhenTheStatusPortIsTaken(t *testing.T) {
	// The standard status port is UDPFS's discovery port, so on a board
	// already running Edge's udpfs it is legitimately occupied. Refusing to
	// serve SMB over a diagnostic would be the wrong trade.
	port := ephemeralUDP(t)
	squatter, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: port})
	if err != nil {
		t.Skipf("could not occupy a port to test with: %v", err)
	}
	defer squatter.Close()

	r, err := filesystem.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	srv, err := New(Config{
		Shares: []Share{{Name: "games", Root: r}}, Bind: "127.0.0.1", Port: 0,
		StatusPort: port, Log: edgelog.New(discardWriter{}, "text", true, false),
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := srv.Listen(); err != nil {
		t.Fatalf("SMB refused to start because the status port was taken: %v", err)
	}
	defer srv.Close()
	if srv.status != nil {
		t.Fatal("status listener claims to have bound an occupied port")
	}
	// And SMB itself must still be serving.
	if srv.Addr() == nil {
		t.Fatal("SMB did not bind")
	}
}

func TestStatusCanBeDisabled(t *testing.T) {
	r, err := filesystem.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	srv, err := New(Config{
		Shares: []Share{{Name: "games", Root: r}}, Bind: "127.0.0.1", Port: 0,
		StatusPort: 0, Log: edgelog.New(discardWriter{}, "text", true, false),
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := srv.Listen(); err != nil {
		t.Fatal(err)
	}
	defer srv.Close()
	if srv.status != nil {
		t.Fatal("StatusPort 0 should disable the listener entirely")
	}
}
