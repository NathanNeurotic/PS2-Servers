// Package sessionstats keeps one bounded counter set per console and prints a
// SESSION line when the console has been quiet for a minute, when it is pushed
// out by a newer one, or when the server stops.
//
// It is the Go twin of launcher/session_stats.py, and the line is byte-for-byte
// the same shape, so the launcher's view and a human reading a router log see
// one format across every PS2 Servers service:
//
//	SESSION <service> client=<ip> seconds=<n> connections=<n> list=<n> open=<n>
//	        read=<n> bytes=<n> write=<n> close=<n> errors=<n> ended=<idle|evicted|stopped>
//
// Nothing is logged per request: one counter update per request, one line per
// console per session. Keep it that way -- Edge lives on routers where a log
// line per packet is a denial of service against the flash.
//
// A nil *Stats is safe to call, so a server can hold one unconditionally and
// leave it nil when --session-summary is off.
package sessionstats

import (
	"fmt"
	"strings"
	"sync"
	"time"
)

// Kinds, in the order they appear on the line. The names are the Python
// module's KINDS and must stay in step with it.
var Kinds = []string{"connections", "list", "open", "read", "bytes", "write", "close", "errors"}

const (
	// MaxClients bounds memory: a LAN has a handful of consoles, and a scan
	// from a stray host must not grow the table without limit. Same as the
	// Python module.
	MaxClients = 64
	// IdleSeconds is how long a console must be quiet before its line prints.
	IdleSeconds = 60 * time.Second
)

type entry struct {
	first, last time.Time
	counts      map[string]int64
}

// Emit receives each finished line. The fields map carries the same values
// typed, for a JSON log; a text log prints the line as it is.
type Emit func(line string, fields map[string]any)

type Stats struct {
	service string
	max     int
	idle    time.Duration
	clock   func() time.Time
	emit    Emit

	mu      sync.Mutex
	clients map[string]*entry
	order   []string // least recently seen first
}

// New makes a Stats for one service name ("udpfs", "smbv1", "udpbd").
func New(service string, emit Emit) *Stats {
	return &Stats{service: service, max: MaxClients, idle: IdleSeconds, clock: time.Now,
		emit: emit, clients: map[string]*entry{}}
}

// WithClock replaces the clock, for tests.
func (s *Stats) WithClock(clock func() time.Time) *Stats { s.clock = clock; return s }

// WithLimits replaces the client bound and idle timeout, for tests.
func (s *Stats) WithLimits(maxClients int, idle time.Duration) *Stats {
	s.max, s.idle = maxClients, idle
	return s
}

func (s *Stats) touchLocked(client string) {
	for i, c := range s.order {
		if c == client {
			s.order = append(s.order[:i], s.order[i+1:]...)
			break
		}
	}
	s.order = append(s.order, client)
}

// Record adds count to one counter for this client. An unknown kind panics:
// it is a programming error, as in the Python module.
func (s *Stats) Record(client, kind string, count int64) {
	if s == nil {
		return
	}
	known := false
	for _, k := range Kinds {
		if k == kind {
			known = true
			break
		}
	}
	if !known {
		panic("sessionstats: unknown kind " + kind)
	}
	var evictedClient string
	var evicted *entry
	s.mu.Lock()
	now := s.clock()
	e := s.clients[client]
	if e == nil {
		if len(s.clients) >= s.max {
			evictedClient = s.order[0]
			evicted = s.clients[evictedClient]
			delete(s.clients, evictedClient)
			s.order = s.order[1:]
		}
		e = &entry{first: now, last: now, counts: map[string]int64{}}
		s.clients[client] = e
	}
	e.last = now
	e.counts[kind] += count
	s.touchLocked(client)
	s.mu.Unlock()
	if evicted != nil {
		s.emitLine(evictedClient, evicted, "evicted")
	}
}

// Touch marks a known client as still active without counting anything, so a
// transfer that outlasts the idle timeout is not summarised mid-way. A client
// with no entry is left alone.
func (s *Stats) Touch(client string) bool {
	if s == nil {
		return false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	e := s.clients[client]
	if e == nil {
		return false
	}
	e.last = s.clock()
	s.touchLocked(client)
	return true
}

// Expire summarises and forgets every client idle for the timeout. It returns
// the lines it printed.
func (s *Stats) Expire() []string {
	if s == nil {
		return nil
	}
	now := s.clock()
	type done struct {
		client string
		e      *entry
	}
	var finished []done
	s.mu.Lock()
	kept := s.order[:0]
	for _, client := range s.order {
		e := s.clients[client]
		if now.Sub(e.last) >= s.idle {
			finished = append(finished, done{client, e})
			delete(s.clients, client)
			continue
		}
		kept = append(kept, client)
	}
	s.order = kept
	s.mu.Unlock()
	lines := make([]string, 0, len(finished))
	for _, d := range finished {
		lines = append(lines, s.emitLine(d.client, d.e, "idle"))
	}
	return lines
}

// Stop summarises every remaining client. It returns the lines it printed.
func (s *Stats) Stop() []string {
	if s == nil {
		return nil
	}
	type done struct {
		client string
		e      *entry
	}
	var finished []done
	s.mu.Lock()
	for _, client := range s.order {
		finished = append(finished, done{client, s.clients[client]})
	}
	s.clients = map[string]*entry{}
	s.order = nil
	s.mu.Unlock()
	lines := make([]string, 0, len(finished))
	for _, d := range finished {
		lines = append(lines, s.emitLine(d.client, d.e, "stopped"))
	}
	return lines
}

// Active is how many consoles currently have an entry.
func (s *Stats) Active() int {
	if s == nil {
		return 0
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.clients)
}

// RunExpiry calls Expire every interval until stop is closed. Run it as a
// goroutine on a server whose main loop blocks (an accept or a receive).
func (s *Stats) RunExpiry(stop <-chan struct{}, interval time.Duration) {
	if s == nil {
		return
	}
	tick := time.NewTicker(interval)
	defer tick.Stop()
	for {
		select {
		case <-stop:
			return
		case <-tick.C:
			s.Expire()
		}
	}
}

func (s *Stats) emitLine(client string, e *entry, ended string) string {
	seconds := int64(e.last.Sub(e.first) / time.Second)
	fields := map[string]any{"service": s.service, "client": client, "seconds": seconds, "ended": ended}
	var b strings.Builder
	fmt.Fprintf(&b, "SESSION %s client=%s seconds=%d", s.service, client, seconds)
	for _, k := range Kinds {
		fmt.Fprintf(&b, " %s=%d", k, e.counts[k])
		fields[k] = e.counts[k]
	}
	fmt.Fprintf(&b, " ended=%s", ended)
	line := b.String()
	if s.emit != nil {
		s.emit(line, fields)
	}
	return line
}

// Parse returns the fields of a SESSION line, or nil if the line is not one.
// Counters come back as int64, the rest as string.
func Parse(line string) map[string]any {
	parts := strings.Fields(line)
	if len(parts) < 3 || parts[0] != "SESSION" {
		return nil
	}
	out := map[string]any{"service": parts[1]}
	for _, part := range parts[2:] {
		key, value, ok := strings.Cut(part, "=")
		if !ok {
			return nil
		}
		var n int64
		if _, err := fmt.Sscanf(value, "%d", &n); err == nil && fmt.Sprint(n) == value {
			out[key] = n
		} else {
			out[key] = value
		}
	}
	return out
}
