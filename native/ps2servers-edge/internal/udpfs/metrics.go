package udpfs

import (
	"net"
	"sync/atomic"
	"time"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
)

// metrics counts protocol operations and bytes moved.
//
// Edge is headless and usually lives on a router or NAS with no screen, so
// "is it actually transferring anything?" is otherwise unanswerable without a
// packet capture. The Desktop/Core server has emitted these counters behind
// --metrics for a long time; Edge not having them was an omission rather than a
// decision, and it is the last real capability gap against udpfsd.
//
// Counters are atomic rather than mutex-guarded: they are written on every
// operation from per-peer worker goroutines, and a lock there would put
// contention on the transfer path purely for bookkeeping.
type metrics struct {
	discovery atomic.Int64
	open      atomic.Int64
	read      atomic.Int64
	write     atomic.Int64
	closeOp   atomic.Int64
	dread     atomic.Int64
	getstat   atomic.Int64
	lseek     atomic.Int64
	bread     atomic.Int64
	bwrite    atomic.Int64

	bytesRead    atomic.Int64
	bytesWritten atomic.Int64

	rxDiscoveryDatagrams atomic.Int64
	rxDiscoveryBytes     atomic.Int64
	rxDataDatagrams      atomic.Int64
	rxDataBytes          atomic.Int64
	txDiscoveryDatagrams atomic.Int64
	txDiscoveryBytes     atomic.Int64
	txDataDatagrams      atomic.Int64
	txDataBytes          atomic.Int64
	txSendErrors         atomic.Int64
	malformedDatagrams   atomic.Int64
	sequenceMismatches   atomic.Int64
	sequenceResets       atomic.Int64
	sameIPReplacements   atomic.Int64

	rxOrdinal atomic.Uint64
	lastRX    atomic.Pointer[lastRXObservation]

	started time.Time
}

// lastRXObservation is immutable once published through lastRX. Keeping the
// related fields in one atomic pointer gives a metrics snapshot a coherent
// peer/socket/header tuple without putting a mutex in the receive path.
type lastRXObservation struct {
	ordinal    uint64
	at         time.Time
	peer       string
	socket     session.Socket
	packetType int
	sequence   int
}

func (m *metrics) recordRX(which session.Socket, bytes int, peer *net.UDPAddr, packet []byte) {
	ordinal := m.rxOrdinal.Add(1)
	switch which {
	case session.DiscoverySocket:
		m.rxDiscoveryDatagrams.Add(1)
		m.rxDiscoveryBytes.Add(int64(bytes))
	case session.DataSocket:
		m.rxDataDatagrams.Add(1)
		m.rxDataBytes.Add(int64(bytes))
	}

	peerString := ""
	if peer != nil {
		peerString = peer.String()
	}
	last := &lastRXObservation{
		ordinal:    ordinal,
		at:         time.Now(),
		peer:       peerString,
		socket:     which,
		packetType: -1,
		sequence:   -1,
	}
	if h, err := protocol.ParseHeader(packet); err == nil {
		last.packetType = int(h.Type)
		last.sequence = int(h.Sequence)
	}
	m.publishLastRX(last)
}

// publishLastRX keeps the most recently started observation when the two
// socket read loops finish recordRX out of order. A plain Store allows an older
// discovery observation that was descheduled to overwrite a newer data one.
func (m *metrics) publishLastRX(next *lastRXObservation) {
	for {
		current := m.lastRX.Load()
		if current != nil && current.ordinal >= next.ordinal {
			return
		}
		if m.lastRX.CompareAndSwap(current, next) {
			return
		}
	}
}

func (m *metrics) recordTX(which session.Socket, bytes int) {
	switch which {
	case session.DiscoverySocket:
		m.txDiscoveryDatagrams.Add(1)
		m.txDiscoveryBytes.Add(int64(bytes))
	case session.DataSocket:
		m.txDataDatagrams.Add(1)
		m.txDataBytes.Add(int64(bytes))
	}
}

// snapshot renders the counters for one log line. The operation names match
// the Python server's stats keys; the transport and handoff fields are
// Edge-specific diagnostics for traffic that has not reached an operation.
func (m *metrics) snapshot() map[string]any {
	elapsed := time.Since(m.started)
	readBytes := m.bytesRead.Load()
	fields := map[string]any{
		"uptime":                 elapsed.Truncate(time.Second).String(),
		"discovery":              m.discovery.Load(),
		"open":                   m.open.Load(),
		"read":                   m.read.Load(),
		"write":                  m.write.Load(),
		"close":                  m.closeOp.Load(),
		"dread":                  m.dread.Load(),
		"getstat":                m.getstat.Load(),
		"lseek":                  m.lseek.Load(),
		"bread":                  m.bread.Load(),
		"bwrite":                 m.bwrite.Load(),
		"bytes_read":             readBytes,
		"bytes_written":          m.bytesWritten.Load(),
		"rx_discovery_datagrams": m.rxDiscoveryDatagrams.Load(),
		"rx_discovery_bytes":     m.rxDiscoveryBytes.Load(),
		"rx_data_datagrams":      m.rxDataDatagrams.Load(),
		"rx_data_bytes":          m.rxDataBytes.Load(),
		"tx_discovery_datagrams": m.txDiscoveryDatagrams.Load(),
		"tx_discovery_bytes":     m.txDiscoveryBytes.Load(),
		"tx_data_datagrams":      m.txDataDatagrams.Load(),
		"tx_data_bytes":          m.txDataBytes.Load(),
		"tx_send_errors":         m.txSendErrors.Load(),
		"malformed_datagrams":    m.malformedDatagrams.Load(),
		"sequence_mismatches":    m.sequenceMismatches.Load(),
		"sequence_resets":        m.sequenceResets.Load(),
		"same_ip_replacements":   m.sameIPReplacements.Load(),
	}
	if last := m.lastRX.Load(); last != nil {
		age := time.Since(last.at)
		if age < 0 {
			age = 0
		}
		fields["last_rx_age"] = age.Truncate(time.Millisecond).String()
		fields["last_rx_peer"] = last.peer
		fields["last_rx_socket"] = last.socket
		if last.packetType < 0 {
			fields["last_rx_type"] = "unknown"
			fields["last_rx_sequence"] = "unknown"
		} else {
			fields["last_rx_type"] = last.packetType
			fields["last_rx_sequence"] = last.sequence
		}
	}
	// A rate is what actually tells a slow transfer from a stalled one, which
	// is the question someone enables metrics to answer.
	if secs := elapsed.Seconds(); secs >= 1 {
		fields["read_bytes_per_sec"] = int64(float64(readBytes) / secs)
	}
	return fields
}

// emitMetrics logs a counter snapshot on an interval until ctx-driven shutdown
// closes the done channel.
func (s *Server) emitMetrics(period time.Duration, done <-chan struct{}) {
	defer s.wg.Done()
	tick := time.NewTicker(period)
	defer tick.Stop()
	for {
		select {
		case <-done:
			// One final line on the way out, so a short-lived run still reports
			// what it moved rather than nothing at all.
			s.cfg.Log.Info("UDPFS metrics", s.stats.snapshot())
			return
		case <-tick.C:
			s.cfg.Log.Info("UDPFS metrics", s.stats.snapshot())
		}
	}
}
