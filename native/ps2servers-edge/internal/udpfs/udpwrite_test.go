package udpfs

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"io"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	edgelog "github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
)

func newUDPWriteTestServer(t *testing.T, enabled bool) (*Server, *net.UDPConn, *net.UDPAddr, *net.UDPConn) {
	t.Helper()
	receiver, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	sender, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		receiver.Close()
		t.Fatal(err)
	}
	t.Cleanup(func() {
		sender.Close()
		receiver.Close()
	})
	server := &Server{
		cfg:  Config{UDPBatch: enabled, MetricsPeriod: time.Minute, Log: edgelog.New(io.Discard, "text", false, true)},
		data: sender, closed: make(chan struct{}),
	}
	server.stats.started = time.Now()
	return server, sender, receiver.LocalAddr().(*net.UDPAddr), receiver
}

func receiveDatagrams(t *testing.T, conn *net.UDPConn, count int) [][]byte {
	t.Helper()
	packets := make([][]byte, 0, count)
	for len(packets) < count {
		if err := conn.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
			t.Fatal(err)
		}
		buffer := make([]byte, 64*1024)
		n, _, err := conn.ReadFromUDP(buffer)
		if err != nil {
			t.Fatalf("received %d/%d datagrams: %v", len(packets), count, err)
		}
		packets = append(packets, append([]byte(nil), buffer[:n]...))
	}
	return packets
}

func TestSendPacketsFallbackAndMetrics(t *testing.T) {
	for _, test := range []struct {
		name    string
		sent    int
		err     error
		wantErr int64
	}{
		{"full", 3, nil, 0},
		{"partial", 1, errors.New("partial batch"), 1},
		{"zero", 0, errors.New("failed batch"), 1},
	} {
		t.Run(test.name, func(t *testing.T) {
			server, sender, peer, receiver := newUDPWriteTestServer(t, true)
			packets := [][]byte{[]byte("first"), []byte("second"), []byte("third")}
			server.batchWrite = func(addr *net.UDPAddr, batch [][]byte) (int, error) {
				for i := 0; i < test.sent; i++ {
					if _, err := server.data.WriteToUDP(batch[i], addr); err != nil {
						t.Fatal(err)
					}
				}
				return test.sent, test.err
			}
			server.sendPackets(session.DataSocket, packets, peer)
			received := receiveDatagrams(t, receiver, len(packets))
			for i := range packets {
				if !bytes.Equal(received[i], packets[i]) {
					t.Errorf("packet %d = %q, want %q", i, received[i], packets[i])
				}
			}
			snapshot := server.stats.snapshot()
			if got := metricInt64(t, snapshot, "tx_data_datagrams"); got != int64(len(packets)) {
				t.Errorf("TX datagrams = %d, want %d", got, len(packets))
			}
			if got := metricInt64(t, snapshot, "tx_data_bytes"); got != int64(len(packets[0])+len(packets[1])+len(packets[2])) {
				t.Errorf("TX bytes = %d", got)
			}
			if got := metricInt64(t, snapshot, "tx_send_errors"); got != test.wantErr {
				t.Errorf("TX errors = %d, want %d", got, test.wantErr)
			}
			_ = sender
		})
	}
}

func TestSendPacketsPacingSkipsBatch(t *testing.T) {
	server, _, peer, receiver := newUDPWriteTestServer(t, true)
	server.cfg.TxDelay = time.Millisecond
	calls := 0
	server.batchWrite = func(addr *net.UDPAddr, batch [][]byte) (int, error) {
		calls++
		return len(batch), nil
	}
	server.sendPackets(session.DataSocket, [][]byte{[]byte("one"), []byte("two")}, peer)
	if calls != 0 {
		t.Fatalf("paced send used batch writer %d times", calls)
	}
	if got := len(receiveDatagrams(t, receiver, 2)); got != 2 {
		t.Fatalf("paced send delivered %d packets", got)
	}
}

func TestBatchedTransferPreservesSequenceAndRetransmission(t *testing.T) {
	server, _, peer, receiver := newUDPWriteTestServer(t, true)
	st := session.New(peer, session.Standard)
	st.TransmitSequence = 4090
	st.TransmitAcked = 3
	var windows []int
	server.batchWrite = func(addr *net.UDPAddr, batch [][]byte) (int, error) {
		windows = append(windows, len(batch))
		for _, packet := range batch {
			if _, err := server.data.WriteToUDP(packet, addr); err != nil {
				t.Fatal(err)
			}
		}
		return len(batch), nil
	}
	if !server.sendTransferPackets(st, nil, make([]byte, protocol.MaxPayload*10)) {
		t.Fatal("transfer was not confirmed")
	}
	if len(windows) != 2 || windows[0] != 8 || windows[1] != 2 {
		t.Fatalf("batch windows = %v, want [8 2]", windows)
	}
	received := receiveDatagrams(t, receiver, 10)
	for i, packet := range received {
		h, err := protocol.ParseHeader(packet)
		if err != nil {
			t.Fatal(err)
		}
		wantSequence := uint16(4090+i) & protocol.SequenceMask
		if h.Sequence != wantSequence {
			t.Errorf("packet %d sequence = %d, want %d", i, h.Sequence, wantSequence)
		}
		if !bytes.Equal(st.TxBuffer[i].Bytes, packet) || st.TxBuffer[i].Sequence != wantSequence {
			t.Errorf("retransmission buffer differs at %d", i)
		}
	}
	if st.TransmitSequence != 4 {
		t.Fatalf("next sequence = %d, want 4", st.TransmitSequence)
	}
	server.retransmit(st, 4098)
	retransmitted := receiveDatagrams(t, receiver, 2)
	for i, packet := range retransmitted {
		h, _ := protocol.ParseHeader(packet)
		if want := uint16(4098+i) & protocol.SequenceMask; h.Sequence != want {
			t.Errorf("retransmitted sequence = %d, want %d", h.Sequence, want)
		}
	}
}

func TestBatchedTransferWrapsSequence(t *testing.T) {
	server, _, peer, receiver := newUDPWriteTestServer(t, true)
	st := session.New(peer, session.Standard)
	st.TransmitSequence = 4095
	st.TransmitAcked = 4095
	server.batchWrite = func(addr *net.UDPAddr, batch [][]byte) (int, error) {
		for _, packet := range batch {
			if _, err := server.data.WriteToUDP(packet, addr); err != nil {
				t.Fatal(err)
			}
		}
		return len(batch), nil
	}
	if !server.sendTransferPackets(st, nil, []byte("wrap")) {
		t.Fatal("wrap transfer was not confirmed")
	}
	packet := receiveDatagrams(t, receiver, 1)[0]
	h, err := protocol.ParseHeader(packet)
	if err != nil || h.Sequence != 4095 {
		t.Fatalf("wrap packet header = %+v, %v", h, err)
	}
	if st.TransmitSequence != 0 {
		t.Fatalf("sequence after 4095 = %d, want 0", st.TransmitSequence)
	}
}

func startBatchServer(t *testing.T, root string) *net.UDPAddr {
	t.Helper()
	server, err := New(Config{
		Root: root, Bind: "127.0.0.1", Port: freeUDPPort(t), UDPBatch: true,
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

func readUDPFSLarge(t *testing.T, c *client, handle int32, size uint32) []byte {
	t.Helper()
	request := make([]byte, 12)
	request[0] = byte(protocol.ReadRequest)
	binary.LittleEndian.PutUint32(request[4:8], uint32(handle))
	binary.LittleEndian.PutUint32(request[8:12], size)
	if _, err := c.conn.WriteToUDP(dataPacket(c.seq, request), c.dataAddr); err != nil {
		t.Fatal(err)
	}
	c.seq++
	var output []byte
	first := true
	for {
		packet, from := recvPacket(t, c.conn)
		h, dh, payload, err := protocol.ParseDataPacket(packet)
		if err != nil || len(payload) == 0 {
			continue
		}
		if first {
			if got := result(payload); got != int32(size) {
				t.Fatalf("READ result = %d, want %d", got, size)
			}
			payload = payload[8:]
			first = false
		}
		output = append(output, payload...)
		_, _ = c.conn.WriteToUDP(ackPacket(h.Sequence), from)
		if dh.Flags&protocol.FlagFIN != 0 {
			return output
		}
	}
}

func TestBatchedMultiWindowRead(t *testing.T) {
	root := t.TempDir()
	content := bytes.Repeat([]byte{0x5a}, 64*1024)
	if err := os.WriteFile(filepath.Join(root, "game.iso"), content, 0o644); err != nil {
		t.Fatal(err)
	}
	c := dial(t, startBatchServer(t, root))
	handle := openForWriteOK(t, c, "game.iso", fioRead)
	if got := readUDPFSLarge(t, c, handle, uint32(len(content))); !bytes.Equal(got, content) {
		t.Fatalf("multi-window READ returned %d bytes or wrong content", len(got))
	}
}

func BenchmarkUDPBatch(b *testing.B) {
	for _, enabled := range []bool{false, true} {
		b.Run(map[bool]string{false: "legacy", true: "batch"}[enabled], func(b *testing.B) {
			receiver, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
			if err != nil {
				b.Fatal(err)
			}
			sender, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
			if err != nil {
				receiver.Close()
				b.Fatal(err)
			}
			b.Cleanup(func() {
				sender.Close()
				receiver.Close()
			})
			server := &Server{cfg: Config{UDPBatch: enabled, Log: edgelog.New(io.Discard, "text", false, false)}, data: sender}
			peer := receiver.LocalAddr().(*net.UDPAddr)
			packets := make([][]byte, 8)
			for i := range packets {
				packets[i] = bytes.Repeat([]byte{byte(i)}, 1400)
			}
			go func() {
				buffer := make([]byte, 2048)
				for {
					if _, _, err := receiver.ReadFromUDP(buffer); err != nil {
						return
					}
				}
			}()
			b.ResetTimer()
			for i := 0; i < b.N; i++ {
				server.sendPackets(session.DataSocket, packets, peer)
			}
		})
	}
}

func TestSendPacketsCachesBatchWriter(t *testing.T) {
	server, _, peer, _ := newUDPWriteTestServer(t, true)
	server.sendPackets(session.DataSocket, [][]byte{[]byte("one"), []byte("two")}, peer)
	if server.batchWrite == nil {
		t.Fatal("batch writer was constructed for one call and discarded")
	}
	if server.batchWriteConn != server.data {
		t.Fatal("batch writer is not cached for the data socket")
	}
}

func TestBatchedTransferDoesNotPublishQueuedPacketsBeforeFlush(t *testing.T) {
	for _, control := range []string{"nack", "ack"} {
		t.Run(control, func(t *testing.T) {
			server, _, peer, receiver := newUDPWriteTestServer(t, true)
			st := session.New(peer, session.Standard)
			st.TransmitSequence = 4090
			st.TransmitAcked = 3
			first := true
			server.batchWrite = func(addr *net.UDPAddr, batch [][]byte) (int, error) {
				if first {
					first = false
					if control == "nack" {
						server.retransmit(st, 4090)
					} else {
						server.pruneAcked(st, 4097)
					}
				}
				for _, packet := range batch {
					if _, err := server.data.WriteToUDP(packet, addr); err != nil {
						t.Fatal(err)
					}
				}
				return len(batch), nil
			}
			if !server.sendTransferPackets(st, nil, make([]byte, protocol.MaxPayload*10)) {
				t.Fatal("transfer was not confirmed")
			}
			received := receiveDatagrams(t, receiver, 10)
			for i, packet := range received {
				h, err := protocol.ParseHeader(packet)
				if err != nil {
					t.Fatal(err)
				}
				want := uint16(4090+i) & protocol.SequenceMask
				if h.Sequence != want {
					t.Errorf("packet %d sequence = %d, want %d", i, h.Sequence, want)
				}
			}
			if len(st.TxBuffer) != 10 {
				t.Fatalf("control pruned or lost retransmission entries: %d", len(st.TxBuffer))
			}
		})
	}
}
