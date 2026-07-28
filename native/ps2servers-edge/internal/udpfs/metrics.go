package udpfs

import (
	"sync/atomic"
	"time"
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

	started time.Time
}

// snapshot renders the counters for one log line. The names match the Python
// server's stats keys so the two are comparable when diagnosing a transfer that
// behaves differently on one of them.
func (m *metrics) snapshot() map[string]any {
	elapsed := time.Since(m.started)
	readBytes := m.bytesRead.Load()
	fields := map[string]any{
		"uptime":        elapsed.Truncate(time.Second).String(),
		"discovery":     m.discovery.Load(),
		"open":          m.open.Load(),
		"read":          m.read.Load(),
		"write":         m.write.Load(),
		"close":         m.closeOp.Load(),
		"dread":         m.dread.Load(),
		"getstat":       m.getstat.Load(),
		"lseek":         m.lseek.Load(),
		"bread":         m.bread.Load(),
		"bwrite":        m.bwrite.Load(),
		"bytes_read":    readBytes,
		"bytes_written": m.bytesWritten.Load(),
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
