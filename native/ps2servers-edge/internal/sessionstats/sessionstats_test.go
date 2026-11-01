package sessionstats

import (
	"strings"
	"testing"
	"time"
)

type capture struct {
	lines  []string
	fields []map[string]any
}

func (c *capture) emit(line string, fields map[string]any) {
	c.lines = append(c.lines, line)
	c.fields = append(c.fields, fields)
}

func newClock() (func() time.Time, func(time.Duration)) {
	now := time.Unix(1000, 0)
	return func() time.Time { return now }, func(d time.Duration) { now = now.Add(d) }
}

func TestIdleClientIsSummarisedOnceInThePythonFormat(t *testing.T) {
	clock, advance := newClock()
	var out capture
	s := New("udpfs", out.emit).WithClock(clock)
	s.Record("10.0.0.2", "connections", 1)
	s.Record("10.0.0.2", "open", 1)
	s.Record("10.0.0.2", "read", 1)
	s.Record("10.0.0.2", "bytes", 2048)
	advance(30 * time.Second)
	if lines := s.Expire(); len(lines) != 0 {
		t.Fatalf("expired early: %v", lines)
	}
	s.Record("10.0.0.2", "read", 1)
	advance(61 * time.Second)
	lines := s.Expire()
	want := "SESSION udpfs client=10.0.0.2 seconds=30 connections=1 list=0 open=1 read=2 bytes=2048 write=0 close=0 errors=0 ended=idle"
	if len(lines) != 1 || lines[0] != want {
		t.Fatalf("got %q\nwant %q", lines, want)
	}
	if len(out.lines) != 1 || out.lines[0] != want || out.fields[0]["bytes"] != int64(2048) || out.fields[0]["ended"] != "idle" {
		t.Fatalf("emit got %v / %v", out.lines, out.fields)
	}
	if s.Active() != 0 || len(s.Expire()) != 0 {
		t.Fatal("the client was summarised twice")
	}
}

func TestTouchKeepsAnActiveClient(t *testing.T) {
	clock, advance := newClock()
	s := New("udpfs", nil).WithClock(clock)
	s.Record("a", "read", 1)
	advance(45 * time.Second)
	if !s.Touch("a") || s.Touch("never-seen") {
		t.Fatal("touch: known client refreshed, unknown one left alone")
	}
	advance(45 * time.Second)
	if len(s.Expire()) != 0 || s.Active() != 1 {
		t.Fatal("touched client expired 90 s after its read, 45 s after the touch")
	}
	advance(61 * time.Second)
	if lines := s.Expire(); len(lines) != 1 || !strings.Contains(lines[0], " read=1 ") {
		t.Fatalf("got %v", lines)
	}
}

func TestClientsAreKeptApartAndBounded(t *testing.T) {
	var out capture
	s := New("smbv1", out.emit).WithLimits(2, time.Minute)
	s.Record("a", "open", 1)
	s.Record("b", "open", 1)
	s.Record("a", "read", 1) // a is now the most recently seen
	s.Record("c", "open", 1) // pushes out b, the least recently seen
	if s.Active() != 2 {
		t.Fatalf("active %d", s.Active())
	}
	if len(out.lines) != 1 || Parse(out.lines[0])["client"] != "b" || Parse(out.lines[0])["ended"] != "evicted" {
		t.Fatalf("evicted line %v", out.lines)
	}
}

func TestStopSummarisesEveryone(t *testing.T) {
	s := New("udpbd", nil)
	s.Record("a", "errors", 1)
	s.Record("b", "connections", 1)
	lines := s.Stop()
	if len(lines) != 2 {
		t.Fatalf("got %v", lines)
	}
	for _, line := range lines {
		if Parse(line)["ended"] != "stopped" {
			t.Fatalf("got %q", line)
		}
	}
	if s.Active() != 0 {
		t.Fatal("not cleared")
	}
}

func TestNilStatsIsSafe(t *testing.T) {
	var s *Stats
	s.Record("a", "read", 1)
	s.Touch("a")
	if s.Expire() != nil || s.Stop() != nil || s.Active() != 0 {
		t.Fatal("nil stats did something")
	}
}

func TestUnknownKindIsRefused(t *testing.T) {
	defer func() {
		if recover() == nil {
			t.Fatal("no panic")
		}
	}()
	New("udpfs", nil).Record("a", "packets", 1)
}

func TestParseIgnoresOtherOutput(t *testing.T) {
	if Parse("[info] UDPFS listening root=/mnt") != nil {
		t.Fatal("parsed a non-SESSION line")
	}
	got := Parse("SESSION udpfs client=10.0.0.2 seconds=30 read=2 ended=idle")
	if got["service"] != "udpfs" || got["client"] != "10.0.0.2" || got["read"] != int64(2) || got["ended"] != "idle" {
		t.Fatalf("got %v", got)
	}
}
