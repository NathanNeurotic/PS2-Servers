package udpfs

import (
	"bytes"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
)

func TestUnexpectedDataZeroWithoutDiscoveryPreservesFileID(t *testing.T) {
	server, disc, _ := startTestServer(t, session.Pending)
	client, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	dataAddr := prepareHotStandardSession(t, server, client, disc, 2213)
	gameData := bytes.Repeat([]byte{0x5a}, 1024)
	w := server.getWorker(client.LocalAddr().(*net.UDPAddr))
	w.state.Mu.Lock()
	w.state.Handles[81] = &session.Handle{Reader: bytes.NewReader(gameData)}
	w.state.NextHandle = 82
	w.state.Mu.Unlock()

	if _, err := client.WriteToUDP(dataPacket(0, blockMsg(protocol.BReadRequest, 81, 0, 1)), dataAddr); err != nil {
		t.Fatal(err)
	}
	_, payload, _ := recvDataPayload(t, client)
	if len(payload) < 8+512 || result(payload) != 512 || !bytes.Equal(payload[8:8+512], gameData[:512]) {
		t.Fatalf("DATA-first restart did not preserve FILEID data: %x", payload)
	}
	w.state.Mu.Lock()
	defer w.state.Mu.Unlock()
	if w.state.Handles[81] == nil || !w.state.Handles[81].Inherited {
		t.Fatal("DATA-first restart closed or failed to inherit FILEID")
	}
	if w.state.NextHandle != 82 || w.state.ExpectedReceive != 1 {
		t.Fatalf("restart left next handle %d and expected sequence %d", w.state.NextHandle, w.state.ExpectedReceive)
	}
}

func TestUnexpectedDataZeroWithoutDiscoveryPreservesWritableFileID(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "vmc.bin")
	if err := os.WriteFile(path, bytes.Repeat([]byte{0x30}, 1024), 0o644); err != nil {
		t.Fatal(err)
	}
	server, disc, _ := startTestServer(t, session.Pending)
	client, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	dataAddr := prepareHotStandardSession(t, server, client, disc, 9)
	file, err := os.OpenFile(path, os.O_RDWR, 0)
	if err != nil {
		t.Fatal(err)
	}
	w := server.getWorker(client.LocalAddr().(*net.UDPAddr))
	w.state.Mu.Lock()
	w.state.Handles[81] = &session.Handle{Reader: file, Writer: file, Closer: file, RealPath: path}
	w.state.NextHandle = 82
	w.state.Mu.Unlock()

	payload := bytes.Repeat([]byte{0x53}, 512)
	request := append(blockMsg(protocol.BWriteRequest, 81, 0, 1), writeDataMsg(0, 1, payload)...)
	if _, err := client.WriteToUDP(dataPacket(0, request), dataAddr); err != nil {
		t.Fatal(err)
	}
	_, reply, _ := recvDataPayload(t, client)
	if len(reply) < 8 || result(reply) != 512 {
		t.Fatalf("DATA-first writable FILEID BWRITE failed: %x", reply)
	}
	onDisk, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(onDisk[:512], payload) {
		t.Fatal("DATA-first BWRITE did not land through the inherited handle")
	}
}

func TestBackgroundDiscoveryPreservesActiveStream(t *testing.T) {
	for _, sequence := range []uint16{0, 7, 4095} {
		t.Run(fmtSequence(sequence), func(t *testing.T) {
			server, disc, _ := startTestServer(t, session.Standard)
			client, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
			if err != nil {
				t.Fatal(err)
			}
			defer client.Close()
			dataAddr := prepareHotStandardSession(t, server, client, disc, 2213)
			gameData := bytes.Repeat([]byte{0x5a}, 1024)
			w := server.getWorker(client.LocalAddr().(*net.UDPAddr))
			w.state.Mu.Lock()
			w.state.Handles[81] = &session.Handle{Reader: bytes.NewReader(gameData)}
			w.state.NextHandle = 82
			w.state.DiscoverySequence = 11
			w.state.FallbackGeneration = 4
			w.state.TransmitSequence = 9
			w.state.LastActivity = time.Now().Add(-3 * time.Second)
			w.state.Mu.Unlock()

			if _, err := client.WriteToUDP(discoveryPacket(sequence), disc); err != nil {
				t.Fatal(err)
			}
			if packet, _ := recvPacket(t, client); len(packet) < 6 {
				t.Fatal("short discovery reply")
			}

			w.state.Mu.Lock()
			if w.state.Handles[81] == nil || w.state.NextHandle != 82 {
				w.state.Mu.Unlock()
				t.Fatal("background discovery closed FILEID state")
			}
			if w.state.ExpectedReceive != 2213 || w.state.TransmitSequence != 9 ||
				w.state.DiscoverySequence != 11 || w.state.FallbackGeneration != 4 {
				w.state.Mu.Unlock()
				t.Fatal("background discovery reset transport state")
			}
			pending := w.state.PendingZeroDiscovery
			w.state.Mu.Unlock()
			if sequence == 0 && pending.IsZero() {
				t.Fatal("sequence-zero discovery did not retain its handoff marker")
			}
			if sequence != 0 && !pending.IsZero() {
				t.Fatal("nonzero discovery created a handoff marker")
			}

			if _, err := client.WriteToUDP(dataPacket(2213, blockMsg(protocol.BReadRequest, 81, 0, 1)), dataAddr); err != nil {
				t.Fatal(err)
			}
			_, payload, _ := recvDataPayload(t, client)
			if len(payload) < 8+512 || result(payload) != 512 || !bytes.Equal(payload[8:8+512], gameData[:512]) {
				t.Fatalf("read after discovery failed: %x", payload)
			}
		})
	}
}

func TestNonzeroDiscoveryHandoffPreservesFileID(t *testing.T) {
	server, disc, _ := startTestServer(t, session.Pending)
	client, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	dataAddr := prepareHotStandardSession(t, server, client, disc, 2213)
	gameData := bytes.Repeat([]byte{0x5a}, 1024)
	w := server.getWorker(client.LocalAddr().(*net.UDPAddr))
	w.state.Mu.Lock()
	w.state.Handles[81] = &session.Handle{Reader: bytes.NewReader(gameData)}
	w.state.NextHandle = 82
	w.state.Mu.Unlock()
	if _, err := client.WriteToUDP(discoveryPacket(7), disc); err != nil {
		t.Fatal(err)
	}
	if packet, _ := recvPacket(t, client); len(packet) < 6 {
		t.Fatal("short discovery reply")
	}
	if _, err := client.WriteToUDP(dataPacket(8, blockMsg(protocol.BReadRequest, 81, 0, 1)), dataAddr); err != nil {
		t.Fatal(err)
	}
	_, payload, _ := recvDataPayload(t, client)
	if len(payload) < 8+512 || result(payload) != 512 || !bytes.Equal(payload[8:8+512], gameData[:512]) {
		t.Fatalf("nonzero-discovery replacement did not preserve FILEID data: %x", payload)
	}
	w.state.Mu.Lock()
	defer w.state.Mu.Unlock()
	if w.state.Handles[81] == nil || !w.state.Handles[81].Inherited {
		t.Fatal("replacement closed or failed to inherit FILEID")
	}
	if w.state.Profile != session.Modulo || w.state.ExpectedReceive != 9 {
		t.Fatalf("replacement left profile %v and expected sequence %d", w.state.Profile, w.state.ExpectedReceive)
	}
}

func TestStreamingDiscoveryRefreshesLiveness(t *testing.T) {
	server, disc, _ := startTestServer(t, session.Standard)
	client, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	prepareHotStandardSession(t, server, client, disc, 2213)
	w := server.getWorker(client.LocalAddr().(*net.UDPAddr))
	w.state.Mu.Lock()
	w.state.LastActivity = time.Now().Add(-2 * time.Minute)
	w.state.Mu.Unlock()

	before := time.Now()
	if _, err := client.WriteToUDP(discoveryPacket(7), disc); err != nil {
		t.Fatal(err)
	}
	if packet, _ := recvPacket(t, client); len(packet) < 6 {
		t.Fatal("short discovery reply")
	}
	w.state.Mu.Lock()
	lastActivity := w.state.LastActivity
	w.state.Mu.Unlock()
	if lastActivity.Before(before) {
		t.Fatalf("streaming discovery left stale activity %v, want at least %v", lastActivity, before)
	}
	server.expireSessions(time.Now())
	if server.getWorker(client.LocalAddr().(*net.UDPAddr)) != w {
		t.Fatal("streaming peer expired after a validated discovery")
	}
}

func fmtSequence(sequence uint16) string {
	if sequence == 0 {
		return "zero"
	}
	if sequence == 4095 {
		return "wrap"
	}
	return "nonzero"
}
