package udpfs

import (
	"bytes"
	"context"
	"encoding/binary"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	edgelog "github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
)

func freeUDPPort(t *testing.T) int {
	t.Helper()
	c, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4zero, Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	return c.LocalAddr().(*net.UDPAddr).Port
}

func startTestServer(t *testing.T, mode session.Profile) (*Server, *net.UDPAddr, context.CancelFunc) {
	t.Helper()
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "game.iso"), []byte("0123456789abcdef"), 0o644); err != nil {
		t.Fatal(err)
	}
	server, err := New(Config{
		Root: root, Bind: "127.0.0.1", Port: freeUDPPort(t), DataPort: 0,
		ProtocolMode: mode, PeerTimeout: time.Minute, FallbackDelay: 30 * time.Millisecond,
		Log: edgelog.New(discardWriter{}, "text", true, false),
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
	t.Cleanup(func() {
		cancel()
		select {
		case err := <-done:
			if err != nil {
				t.Errorf("Serve: %v", err)
			}
		case <-time.After(2 * time.Second):
			t.Error("server did not stop")
		}
	})
	return server, disc, cancel
}

type discardWriter struct{}

func (discardWriter) Write(p []byte) (int, error) { return len(p), nil }

func discoveryPacket(seq uint16) []byte {
	return append(protocol.Header{Type: protocol.Discovery, Sequence: seq}.Marshal(), protocol.DiscoveryHeader{ServiceID: protocol.ServiceUDPFS}.Marshal()...)
}

func dataPacket(seq uint16, payload []byte) []byte {
	payload = pad4(append([]byte(nil), payload...))
	p := append(protocol.Header{Type: protocol.Data, Sequence: seq}.Marshal(), protocol.DataHeader{DataBytes: uint16(len(payload))}.Marshal()...)
	return append(p, payload...)
}

func ackPacket(serverSeq uint16) []byte {
	return append(protocol.Header{Type: protocol.Data}.Marshal(), protocol.DataHeader{AckSequence: serverSeq, Flags: protocol.FlagACK}.Marshal()...)
}

func recvPacket(t *testing.T, c *net.UDPConn) ([]byte, *net.UDPAddr) {
	t.Helper()
	if err := c.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
		t.Fatal(err)
	}
	buf := make([]byte, 64*1024)
	n, from, err := c.ReadFromUDP(buf)
	if err != nil {
		t.Fatal(err)
	}
	return append([]byte(nil), buf[:n]...), from
}

func recvDataPayload(t *testing.T, c *net.UDPConn) (protocol.Header, []byte, *net.UDPAddr) {
	t.Helper()
	for {
		p, from := recvPacket(t, c)
		h, dh, payload, err := protocol.ParseDataPacket(p)
		if err != nil {
			continue
		}
		if len(payload) == 0 && dh.Flags&protocol.FlagACK != 0 {
			continue
		}
		return h, payload, from
	}
}

func TestStandardWireOpenReadAndBytes(t *testing.T) {
	_, disc, _ := startTestServer(t, session.Pending)
	client, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	if _, err = client.WriteToUDP(discoveryPacket(0), disc); err != nil {
		t.Fatal(err)
	}
	inform, dataAddr := recvPacket(t, client)
	h, err := protocol.ParseHeader(inform)
	if err != nil || h.Type != protocol.Inform || h.Sequence != 1 {
		t.Fatalf("bad canonical INFORM: %v %+v", err, h)
	}
	dh, err := protocol.ParseDiscoveryHeader(inform[2:])
	if err != nil || int(dh.Port) != dataAddr.Port {
		t.Fatalf("advertised data port %d, source %d", dh.Port, dataAddr.Port)
	}

	open := make([]byte, 8)
	open[0] = byte(protocol.OpenRequest)
	binary.LittleEndian.PutUint16(open[2:4], 1)
	open = append(open, []byte("game.iso\x00")...)
	if _, err = client.WriteToUDP(dataPacket(0, open), dataAddr); err != nil {
		t.Fatal(err)
	}
	respHeader, payload, responseAddr := recvDataPayload(t, client)
	if respHeader.Sequence != 0 || len(payload) < 36 || protocol.MessageType(payload[0]) != protocol.OpenReply {
		t.Fatalf("bad OPEN reply seq=%d len=%d", respHeader.Sequence, len(payload))
	}
	handle := int32(binary.LittleEndian.Uint32(payload[4:8]))
	if handle <= 0 || binary.LittleEndian.Uint32(payload[12:16]) != 16 {
		t.Fatalf("bad OPEN handle/size: %d %d", handle, binary.LittleEndian.Uint32(payload[12:16]))
	}
	_, _ = client.WriteToUDP(ackPacket(respHeader.Sequence), responseAddr)

	read := make([]byte, 12)
	read[0] = byte(protocol.ReadRequest)
	binary.LittleEndian.PutUint32(read[4:8], uint32(handle))
	binary.LittleEndian.PutUint32(read[8:12], 16)
	if _, err = client.WriteToUDP(dataPacket(1, read), dataAddr); err != nil {
		t.Fatal(err)
	}
	respHeader, payload, responseAddr = recvDataPayload(t, client)
	if respHeader.Sequence != 1 || len(payload) < 24 || protocol.MessageType(payload[0]) != protocol.ResultReply {
		t.Fatalf("bad READ reply seq=%d len=%d", respHeader.Sequence, len(payload))
	}
	if got := string(payload[8:24]); got != "0123456789abcdef" {
		t.Fatalf("read bytes %q", got)
	}
	_, _ = client.WriteToUDP(ackPacket(respHeader.Sequence), responseAddr)
}

func TestModuloFallbackWrapAndDiscoverySocket(t *testing.T) {
	_, disc, _ := startTestServer(t, session.Pending)
	client, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	if _, err = client.WriteToUDP(discoveryPacket(4095), disc); err != nil {
		t.Fatal(err)
	}
	canonical, _ := recvPacket(t, client)
	ch, _ := protocol.ParseHeader(canonical)
	if ch.Type != protocol.Inform || ch.Sequence != 1 {
		t.Fatalf("bad canonical INFORM: %+v", ch)
	}
	compat, compatAddr := recvPacket(t, client)
	mh, _ := protocol.ParseHeader(compat)
	md, _ := protocol.ParseDiscoveryHeader(compat[2:])
	if mh.Type != protocol.Inform || mh.Sequence != 0 || md.Port != 0 || compatAddr.Port != disc.Port {
		t.Fatalf("bad compatibility INFORM: header=%+v port=%d source=%d", mh, md.Port, compatAddr.Port)
	}

	getstat := append([]byte{byte(protocol.GetStatRequest), 0, 0, 0}, []byte("game.iso\x00")...)
	if _, err = client.WriteToUDP(dataPacket(0, getstat), disc); err != nil {
		t.Fatal(err)
	}
	respHeader, payload, responseAddr := recvDataPayload(t, client)
	if respHeader.Sequence != 1 || responseAddr.Port != disc.Port || len(payload) < 48 {
		t.Fatalf("bad modulo response seq=%d source=%d len=%d", respHeader.Sequence, responseAddr.Port, len(payload))
	}
	if result := int32(binary.LittleEndian.Uint32(payload[4:8])); result != 0 {
		t.Fatalf("GETSTAT result %d", result)
	}
	if size := binary.LittleEndian.Uint32(payload[16:20]); size != 16 {
		t.Fatalf("GETSTAT size %d", size)
	}
	_, _ = client.WriteToUDP(ackPacket(respHeader.Sequence), responseAddr)
}

func nonLoopbackIPv4(t *testing.T) net.IP {
	t.Helper()
	addresses, err := net.InterfaceAddrs()
	if err != nil {
		t.Fatal(err)
	}
	for _, address := range addresses {
		var ip net.IP
		switch value := address.(type) {
		case *net.IPNet:
			ip = value.IP
		case *net.IPAddr:
			ip = value.IP
		}
		if ip = ip.To4(); ip == nil || ip.IsLoopback() || ip.IsUnspecified() {
			continue
		}
		probe, err := net.ListenUDP("udp4", &net.UDPAddr{IP: ip, Port: 0})
		if err == nil {
			_ = probe.Close()
			return ip
		}
	}
	t.Skip("no non-loopback IPv4 interface available")
	return nil
}

func startServerOnIP(t *testing.T, root string, ip net.IP) *net.UDPAddr {
	t.Helper()
	server, err := New(Config{
		Root: root, Bind: ip.String(), Port: freeUDPPort(t), DataPort: 0,
		ProtocolMode: session.Standard, PeerTimeout: time.Minute,
		Log: edgelog.New(discardWriter{}, "text", true, false),
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
	t.Cleanup(func() {
		cancel()
		select {
		case err := <-done:
			if err != nil {
				t.Errorf("Serve: %v", err)
			}
		case <-time.After(2 * time.Second):
			t.Error("server did not stop")
		}
	})
	return disc
}

func dialOnIP(t *testing.T, disc *net.UDPAddr, ip net.IP) *client {
	t.Helper()
	conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: ip, Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	if _, err := conn.WriteToUDP(discoveryPacket(0), disc); err != nil {
		t.Fatal(err)
	}
	_, dataAddr := recvPacket(t, conn)
	return &client{t: t, conn: conn, dataAddr: dataAddr}
}

func TestSameIPDifferentPortsRemainConcurrentOnWire(t *testing.T) {
	ip := nonLoopbackIPv4(t)
	root := t.TempDir()
	content := []byte("0123456789abcdef")
	if err := os.WriteFile(filepath.Join(root, "game.iso"), content, 0o644); err != nil {
		t.Fatal(err)
	}
	disc := startServerOnIP(t, root, ip)
	first := dialOnIP(t, disc, ip)
	firstHandle := openForWriteOK(t, first, "game.iso", fioRead)
	second := dialOnIP(t, disc, ip)
	secondHandle := openForWriteOK(t, second, "game.iso", fioRead)

	for _, test := range []struct {
		name   string
		client *client
		handle int32
	}{{"first", first, firstHandle}, {"second", second, secondHandle}} {
		request := make([]byte, 12)
		request[0] = byte(protocol.ReadRequest)
		binary.LittleEndian.PutUint32(request[4:8], uint32(test.handle))
		binary.LittleEndian.PutUint32(request[8:12], uint32(len(content)))
		reply := test.client.send(request)
		if len(reply) < 8 {
			t.Errorf("%s endpoint READ returned an empty control reply", test.name)
			continue
		}
		if got := result(reply); got != int32(len(content)) {
			t.Errorf("%s endpoint READ returned %d (opcode %#x), want %d", test.name, got, reply[0], len(content))
			continue
		}
		if got := reply[8 : 8+len(content)]; !bytes.Equal(got, content) {
			t.Errorf("%s endpoint returned %q", test.name, got)
		}
	}
}
