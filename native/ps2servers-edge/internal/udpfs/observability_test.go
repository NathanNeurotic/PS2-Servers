package udpfs

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	edgelog "github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
)

// synchronizedLogBuffer permits assertions while the server goroutines are
// winding down without introducing a race in the test's log capture.
type synchronizedLogBuffer struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (b *synchronizedLogBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.b.Write(p)
}

func (b *synchronizedLogBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.b.String()
}

type observedTestServer struct {
	server    *Server
	discovery *net.UDPAddr
	logs      *synchronizedLogBuffer
	stop      func()
}

func startObservedTestServer(t *testing.T, verbose, metricsEnabled bool) *observedTestServer {
	t.Helper()
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "game.iso"), []byte("0123456789abcdef"), 0o644); err != nil {
		t.Fatal(err)
	}

	logs := &synchronizedLogBuffer{}
	metricsPeriod := time.Duration(0)
	if metricsEnabled {
		metricsPeriod = time.Second
	}
	server, err := New(Config{
		Root: root, Bind: "127.0.0.1", Port: freeUDPPort(t), DataPort: 0,
		ProtocolMode: session.Standard, PeerTimeout: time.Minute,
		MetricsPeriod: metricsPeriod,
		Log:           edgelog.New(logs, "json", false, verbose),
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := server.Listen(); err != nil {
		t.Fatal(err)
	}
	discovery, _ := server.Addr()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- server.Serve(ctx) }()

	var stopOnce sync.Once
	stop := func() {
		t.Helper()
		stopOnce.Do(func() {
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
	}
	t.Cleanup(stop)
	return &observedTestServer{server: server, discovery: discovery, logs: logs, stop: stop}
}

func waitForMetric(t *testing.T, snapshot func() map[string]any, key string, want int64) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for {
		if got := metricInt64(t, snapshot(), key); got == want {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("metric %q did not reach %d; last snapshot: %#v", key, want, snapshot())
		}
		time.Sleep(time.Millisecond)
	}
}

func metricInt64(t *testing.T, fields map[string]any, key string) int64 {
	t.Helper()
	v, ok := fields[key]
	if !ok {
		t.Fatalf("metric %q missing from snapshot: %#v", key, fields)
	}
	switch n := v.(type) {
	case int:
		return int64(n)
	case int64:
		return n
	case uint16:
		return int64(n)
	case float64:
		return int64(n)
	default:
		t.Fatalf("metric %q has non-numeric type %T (%v)", key, v, v)
		return 0
	}
}

func parseJSONLogRecords(t *testing.T, text string) []map[string]any {
	t.Helper()
	var records []map[string]any
	scanner := bufio.NewScanner(strings.NewReader(text))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var record map[string]any
		if err := json.Unmarshal([]byte(line), &record); err != nil {
			t.Fatalf("invalid JSON log line %q: %v", line, err)
		}
		records = append(records, record)
	}
	if err := scanner.Err(); err != nil {
		t.Fatal(err)
	}
	return records
}

func findJSONLogRecord(t *testing.T, records []map[string]any, message string, match func(map[string]any) bool) map[string]any {
	t.Helper()
	for _, record := range records {
		if record["message"] == message && (match == nil || match(record)) {
			return record
		}
	}
	t.Fatalf("log record %q not found in %#v", message, records)
	return nil
}

func assertJSONField(t *testing.T, record map[string]any, key string, want any) {
	t.Helper()
	got, ok := record[key]
	if !ok {
		t.Fatalf("field %q missing from log record: %#v", key, record)
	}
	if fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("field %q = %v, want %v; record: %#v", key, got, want, record)
	}
}

func TestRawDatagramMetricsAndLastRXMetadata(t *testing.T) {
	observed := startObservedTestServer(t, false, true)
	client, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()

	request := discoveryPacket(23)
	if _, err := client.WriteToUDP(request, observed.discovery); err != nil {
		t.Fatal(err)
	}
	reply, replyFrom := recvPacket(t, client)
	h, err := protocol.ParseHeader(reply)
	if err != nil || h.Type != protocol.Inform {
		t.Fatalf("expected INFORM reply, got header=%+v err=%v", h, err)
	}

	waitForMetric(t, observed.server.stats.snapshot, "tx_data_datagrams", 1)
	observed.stop()
	snapshot := observed.server.stats.snapshot()
	checks := map[string]int64{
		"rx_discovery_datagrams": 1,
		"rx_discovery_bytes":     int64(len(request)),
		"rx_data_datagrams":      0,
		"rx_data_bytes":          0,
		"tx_discovery_datagrams": 0,
		"tx_discovery_bytes":     0,
		"tx_data_datagrams":      1,
		"tx_data_bytes":          int64(len(reply)),
		"tx_send_errors":         0,
	}
	for key, want := range checks {
		if got := metricInt64(t, snapshot, key); got != want {
			t.Errorf("%s = %d, want %d", key, got, want)
		}
	}
	if got := fmt.Sprint(snapshot["last_rx_peer"]); got != client.LocalAddr().String() {
		t.Errorf("last_rx_peer = %q, want %q", got, client.LocalAddr())
	}
	if got := fmt.Sprint(snapshot["last_rx_socket"]); got != string(session.DiscoverySocket) {
		t.Errorf("last_rx_socket = %q, want %q", got, session.DiscoverySocket)
	}
	if got := metricInt64(t, snapshot, "last_rx_type"); got != int64(protocol.Discovery) {
		t.Errorf("last_rx_type = %d, want %d", got, protocol.Discovery)
	}
	if got := metricInt64(t, snapshot, "last_rx_sequence"); got != 23 {
		t.Errorf("last_rx_sequence = %d, want 23", got)
	}
	if age, ok := snapshot["last_rx_age"].(string); !ok || age == "" {
		t.Errorf("last_rx_age missing or invalid: %#v", snapshot["last_rx_age"])
	}
	if replyFrom.Port == observed.discovery.Port {
		t.Fatalf("standard INFORM unexpectedly came from discovery port %d", observed.discovery.Port)
	}
}

func TestLastRXPublicationRejectsOlderConcurrentObservation(t *testing.T) {
	var stats metrics
	newer := &lastRXObservation{
		ordinal: 2, at: time.Now(), peer: "192.0.2.2:2000",
		socket: session.DataSocket, packetType: int(protocol.Data), sequence: 8,
	}
	older := &lastRXObservation{
		ordinal: 1, at: newer.at.Add(-time.Millisecond), peer: "192.0.2.1:1000",
		socket: session.DiscoverySocket, packetType: int(protocol.Discovery), sequence: 7,
	}

	// Model the discovery read loop starting first, being descheduled, and
	// completing only after the data read loop has published its observation.
	olderStarted := make(chan struct{})
	finishOlder := make(chan struct{})
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		close(olderStarted)
		<-finishOlder
		stats.publishLastRX(older)
	}()
	<-olderStarted
	stats.publishLastRX(newer)
	close(finishOlder)
	wg.Wait()

	got := stats.lastRX.Load()
	if got != newer {
		t.Fatalf("last RX regressed to ordinal %d (%s); want ordinal %d (%s)", got.ordinal, got.peer, newer.ordinal, newer.peer)
	}
}

func TestVerboseJSONDatagramTraceContainsHeadersNotPayload(t *testing.T) {
	observed := startObservedTestServer(t, true, true)
	client, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()

	if _, err := client.WriteToUDP(discoveryPacket(0), observed.discovery); err != nil {
		t.Fatal(err)
	}
	_, dataAddr := recvPacket(t, client)

	const payloadMarker = "TRACE_PAYLOAD_MUST_NOT_APPEAR.iso"
	open := make([]byte, 8)
	open[0] = byte(protocol.OpenRequest)
	open = append(open, payloadMarker...)
	open = append(open, 0)
	request := dataPacket(0, open)
	if _, err := client.WriteToUDP(request, dataAddr); err != nil {
		t.Fatal(err)
	}
	replyHeader, _, replyFrom := recvDataPayload(t, client)
	if _, err := client.WriteToUDP(ackPacket(replyHeader.Sequence), replyFrom); err != nil {
		t.Fatal(err)
	}

	waitForMetric(t, observed.server.stats.snapshot, "rx_data_datagrams", 2)
	observed.stop()
	records := parseJSONLogRecords(t, observed.logs.String())
	rx := findJSONLogRecord(t, records, "UDP RX", func(record map[string]any) bool {
		return fmt.Sprint(record["type"]) == fmt.Sprint(protocol.Data) &&
			fmt.Sprint(record["opcode"]) == fmt.Sprint(protocol.OpenRequest)
	})
	assertJSONField(t, rx, "socket", session.DataSocket)
	assertJSONField(t, rx, "local", dataAddr.String())
	assertJSONField(t, rx, "peer", client.LocalAddr().String())
	assertJSONField(t, rx, "bytes", len(request))
	assertJSONField(t, rx, "type", protocol.Data)
	assertJSONField(t, rx, "sequence", 0)
	assertJSONField(t, rx, "ack", 0)
	assertJSONField(t, rx, "flags", 0)
	assertJSONField(t, rx, "header_words", 0)
	assertJSONField(t, rx, "data_bytes", len(request)-6)
	assertJSONField(t, rx, "opcode", protocol.OpenRequest)
	encoded, err := json.Marshal(rx)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(encoded, []byte(payloadMarker)) {
		t.Fatalf("UDP header trace leaked payload bytes: %s", encoded)
	}
	for _, forbidden := range []string{"payload", "packet", "body"} {
		if _, ok := rx[forbidden]; ok {
			t.Errorf("UDP header trace contains forbidden %q field: %#v", forbidden, rx)
		}
	}
	requestLog := findJSONLogRecord(t, records, "UDPFS request", func(record map[string]any) bool {
		return record["operation"] == "OPEN"
	})
	assertJSONField(t, requestLog, "path", payloadMarker)
	assertJSONField(t, requestLog, "is_dir", false)
	assertJSONField(t, requestLog, "flags", 0)

	tx := findJSONLogRecord(t, records, "UDP TX", func(record map[string]any) bool {
		return fmt.Sprint(record["type"]) == fmt.Sprint(protocol.Inform)
	})
	assertJSONField(t, tx, "socket", session.DataSocket)
	assertJSONField(t, tx, "local", dataAddr.String())
	assertJSONField(t, tx, "peer", client.LocalAddr().String())
	assertJSONField(t, tx, "type", protocol.Inform)
	assertJSONField(t, tx, "sequence", 1)
	if metricInt64(t, tx, "bytes") <= 0 {
		t.Fatalf("UDP TX byte count is not positive: %#v", tx)
	}
}

func TestSentContinuationTraceDoesNotExposePayloadByteAsOpcode(t *testing.T) {
	packet := dataPacket(7, []byte{byte(protocol.OpenRequest), 0xaa, 0xbb, 0xcc})

	tx := datagramTraceFields(nil, session.DataSocket, nil, packet, len(packet))
	if opcode, ok := tx["opcode"]; ok {
		t.Fatalf("TX continuation exposed payload byte as opcode: %v", opcode)
	}

	rx := receivedDatagramTraceFields(nil, session.DataSocket, nil, packet, len(packet))
	assertJSONField(t, rx, "opcode", protocol.OpenRequest)
}

func TestVerboseOffSuppressesDatagramTrace(t *testing.T) {
	observed := startObservedTestServer(t, false, true)
	client, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()

	if _, err := client.WriteToUDP(discoveryPacket(4), observed.discovery); err != nil {
		t.Fatal(err)
	}
	_, _ = recvPacket(t, client)
	waitForMetric(t, observed.server.stats.snapshot, "tx_data_datagrams", 1)
	observed.stop()

	records := parseJSONLogRecords(t, observed.logs.String())
	findJSONLogRecord(t, records, "UDPFS listening", nil)
	for _, record := range records {
		if record["message"] == "UDP RX" || record["message"] == "UDP TX" {
			t.Fatalf("verbose-off server emitted packet trace: %#v", record)
		}
	}
}

func TestMetricsOffSkipsRawTransportObservation(t *testing.T) {
	observed := startObservedTestServer(t, false, false)
	client, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()

	if _, err := client.WriteToUDP(discoveryPacket(9), observed.discovery); err != nil {
		t.Fatal(err)
	}
	_, _ = recvPacket(t, client)
	observed.stop()

	snapshot := observed.server.stats.snapshot()
	if got := metricInt64(t, snapshot, "rx_discovery_datagrams"); got != 0 {
		t.Fatalf("rx_discovery_datagrams = %d with metrics off, want 0", got)
	}
	if _, ok := snapshot["last_rx_peer"]; ok {
		t.Fatalf("last_rx_peer recorded with metrics off: %#v", snapshot)
	}
}

func TestSafePathForLogRejectsControlAndSeparatorCharacters(t *testing.T) {
	tests := []struct {
		name string
		path string
	}{
		{name: "ASCII control", path: "game\nname.iso"},
		{name: "Unicode format control", path: "game\u202ename.iso"},
		{name: "Unicode line separator", path: "game\u2028name.iso"},
		{name: "Unicode paragraph separator", path: "game\u2029name.iso"},
		{name: "invalid UTF-8", path: string([]byte{'g', 'a', 'm', 'e', 0xff})},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got, ok := safePathForLog(tc.path); ok {
				t.Fatalf("safePathForLog(%q) = %q, true; want rejection", tc.path, got)
			}
		})
	}
}

func newDirectObservabilityServer(verbose bool) (*Server, *synchronizedLogBuffer) {
	logs := &synchronizedLogBuffer{}
	server := &Server{
		cfg: Config{
			ProtocolMode: session.Standard,
			Log:          edgelog.New(logs, "json", false, verbose),
		},
		sessions: make(map[string]*peerWorker),
		closed:   make(chan struct{}),
	}
	server.stats.started = time.Now()
	return server, logs
}

func TestSequenceMismatchAndResetCountersAndEvents(t *testing.T) {
	server, logs := newDirectObservabilityServer(true)
	peer := &net.UDPAddr{IP: net.ParseIP("192.0.2.10"), Port: 25000}

	nackState := session.New(peer, session.Standard)
	nackState.Streaming = true
	nackState.ExpectedReceive = 5
	server.handleData(nackState, inbound{
		packet: dataPacket(3, []byte{0xff}),
		peer:   peer,
		socket: session.DataSocket,
	})

	resetState := session.New(peer, session.Standard)
	resetState.Streaming = true
	resetState.ExpectedReceive = 5
	server.handleData(resetState, inbound{
		packet: dataPacket(0, []byte{0xff}),
		peer:   peer,
		socket: session.DataSocket,
	})

	snapshot := server.stats.snapshot()
	if got := metricInt64(t, snapshot, "sequence_mismatches"); got != 2 {
		t.Fatalf("sequence_mismatches = %d, want 2", got)
	}
	if got := metricInt64(t, snapshot, "sequence_resets"); got != 1 {
		t.Fatalf("sequence_resets = %d, want 1", got)
	}

	records := parseJSONLogRecords(t, logs.String())
	nack := findJSONLogRecord(t, records, "peer sequence mismatch", func(record map[string]any) bool {
		return record["outcome"] == "nack"
	})
	assertJSONField(t, nack, "peer", peer.String())
	assertJSONField(t, nack, "socket", session.DataSocket)
	assertJSONField(t, nack, "profile", session.Standard)
	assertJSONField(t, nack, "expected", 5)
	assertJSONField(t, nack, "received", 3)
	assertJSONField(t, nack, "ack", 0)
	assertJSONField(t, nack, "flags", 0)

	reset := findJSONLogRecord(t, records, "peer sequence mismatch", func(record map[string]any) bool {
		return record["outcome"] == "session_reset"
	})
	assertJSONField(t, reset, "expected", 5)
	assertJSONField(t, reset, "received", 0)
	findJSONLogRecord(t, records, "peer sequence reset (seq 0 received)", func(record map[string]any) bool {
		return record["outcome"] == "session_reset" && fmt.Sprint(record["expected"]) == "5" && fmt.Sprint(record["received"]) == "0"
	})
}

func TestMalformedDatagramCounter(t *testing.T) {
	server, logs := newDirectObservabilityServer(true)
	peer := &net.UDPAddr{IP: net.ParseIP("192.0.2.20"), Port: 25001}
	server.dispatch(inbound{packet: []byte{0x02}, peer: peer, socket: session.DataSocket})

	if got := metricInt64(t, server.stats.snapshot(), "malformed_datagrams"); got != 1 {
		t.Fatalf("malformed_datagrams = %d, want 1", got)
	}
	record := findJSONLogRecord(t, parseJSONLogRecords(t, logs.String()), "dropping malformed datagram", nil)
	assertJSONField(t, record, "peer", peer.String())
	assertJSONField(t, record, "socket", session.DataSocket)
	assertJSONField(t, record, "bytes", 1)
}

func stopDirectWorkers(server *Server) {
	server.sessionsMu.Lock()
	workers := make([]*peerWorker, 0, len(server.sessions))
	for _, worker := range server.sessions {
		workers = append(workers, worker)
	}
	server.sessions = make(map[string]*peerWorker)
	server.sessionsMu.Unlock()
	for _, worker := range workers {
		worker.stop()
	}
	server.wg.Wait()
	for _, worker := range workers {
		worker.state.Close()
	}
}

func TestSameIPSourcePortReplacementInstrumentation(t *testing.T) {
	t.Run("eligible replacement", func(t *testing.T) {
		server, logs := newDirectObservabilityServer(true)
		defer stopDirectWorkers(server)
		oldPeer := &net.UDPAddr{IP: net.ParseIP("192.0.2.30"), Port: 26000}
		newPeer := &net.UDPAddr{IP: net.ParseIP("192.0.2.30"), Port: 26001}
		server.getWorker(oldPeer)
		server.getWorker(newPeer)

		if got := metricInt64(t, server.stats.snapshot(), "same_ip_replacements"); got != 1 {
			t.Fatalf("same_ip_replacements = %d, want 1", got)
		}
		records := parseJSONLogRecords(t, logs.String())
		candidate := findJSONLogRecord(t, records, "same-IP session replacement candidate", nil)
		assertJSONField(t, candidate, "old_peer", oldPeer.String())
		assertJSONField(t, candidate, "new_peer", newPeer.String())
		assertJSONField(t, candidate, "old_source_port", oldPeer.Port)
		assertJSONField(t, candidate, "new_source_port", newPeer.Port)
		assertJSONField(t, candidate, "write_active", false)
		assertJSONField(t, candidate, "eligible", true)
		assertJSONField(t, candidate, "reason", "no_active_write")
		findJSONLogRecord(t, records, "replacing stale session for IP", func(record map[string]any) bool {
			return record["reason"] == "no_active_write" && record["eligible"] == true
		})
	})

	t.Run("recent active write is retained", func(t *testing.T) {
		server, logs := newDirectObservabilityServer(true)
		defer stopDirectWorkers(server)
		oldPeer := &net.UDPAddr{IP: net.ParseIP("192.0.2.31"), Port: 26100}
		newPeer := &net.UDPAddr{IP: net.ParseIP("192.0.2.31"), Port: 26101}
		oldWorker := server.getWorker(oldPeer)
		oldWorker.state.Mu.Lock()
		oldWorker.state.WriteActive = true
		oldWorker.state.LastActivity = time.Now()
		oldWorker.state.Mu.Unlock()
		server.getWorker(newPeer)

		if got := metricInt64(t, server.stats.snapshot(), "same_ip_replacements"); got != 0 {
			t.Fatalf("same_ip_replacements = %d, want 0", got)
		}
		records := parseJSONLogRecords(t, logs.String())
		candidate := findJSONLogRecord(t, records, "same-IP session replacement candidate", nil)
		assertJSONField(t, candidate, "old_peer", oldPeer.String())
		assertJSONField(t, candidate, "new_peer", newPeer.String())
		assertJSONField(t, candidate, "write_active", true)
		assertJSONField(t, candidate, "eligible", false)
		assertJSONField(t, candidate, "reason", "active_write_recent")
		for _, record := range records {
			if record["message"] == "replacing stale session for IP" {
				t.Fatalf("recent active-write session was reported as replaced: %#v", record)
			}
		}
	})
}
