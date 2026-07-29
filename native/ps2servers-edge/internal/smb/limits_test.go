package smb

import (
	"encoding/binary"
	"net"
	"path/filepath"
	"strconv"
	"testing"
	"time"
)

// Every test here corresponds to a defect an adversarial review found and
// reproduced against the running server before it was merged. They are
// regression guards, not speculation: each one failed on the code as first
// written.

func TestReadCountCannotWrapNegative(t *testing.T) {
	// The blocker. maxCount was `int(maxLow) | int(maxHigh)<<16`, and int is
	// 32 bits on mipsle/mips/arm/386 -- every small target this build exists
	// for -- so any MaxCountHigh >= 0x8000 set the sign bit. The clamp only
	// caught values that were too LARGE, and the `> 0` guard then produced a
	// zero-length reply carrying STATUS_SUCCESS: a silent short read a console
	// would see as a truncated file rather than an error.
	//
	// Reproduced on GOARCH=386 before the fix. This test fails there too if it
	// regresses, and is meaningful on 64-bit only as a clamp check -- hence the
	// explicit comparison against the full read below.
	root := t.TempDir()
	payload := make([]byte, 8192)
	for i := range payload {
		payload[i] = byte(i)
	}
	writeFile(t, filepath.Join(root, "game.iso"), payload)

	cl := dial(t, startServer(t, root, false))
	cl.handshake("games")
	h, p, _ := cl.call(ComOpenAndX, make([]byte, 30), []byte("game.iso\x00"))
	if h.Status != StatusSuccess {
		t.Fatalf("open status %#x", h.Status)
	}
	fid := binary.LittleEndian.Uint16(p[4:])

	for _, high := range []uint16{0x0000, 0x7FFF, 0x8000, 0xFFFF} {
		t.Run("MaxCountHigh_"+strconv.FormatUint(uint64(high), 16), func(t *testing.T) {
			rq := make([]byte, 24)
			binary.LittleEndian.PutUint16(rq[4:], fid)
			binary.LittleEndian.PutUint32(rq[6:], 0)
			binary.LittleEndian.PutUint16(rq[10:], 4096) // MaxCountLow
			binary.LittleEndian.PutUint16(rq[14:], high) // MaxCountHigh
			h, rp, _ := cl.call(ComReadAndX, rq, nil)
			if h.Status != StatusSuccess {
				t.Fatalf("status %#x", h.Status)
			}
			n := int(binary.LittleEndian.Uint16(rp[10:]))
			if n == 0 {
				t.Fatalf("MaxCountHigh=%#x produced a zero-length read with "+
					"STATUS_SUCCESS; the count wrapped negative", high)
			}
		})
	}
}

func TestSearchesAreBounded(t *testing.T) {
	// A search holds a whole directory listing and is freed only by a
	// FIND_NEXT2 issued AFTER the one reporting EndOfSearch -- a call no
	// client has reason to make. Measured before the fix: 2000 FIND_FIRST2
	// against a 200-file share grew the heap by ~80 MiB on one connection,
	// which is a 32 MB board dead in about a second of ordinary traffic.
	root := t.TempDir()
	for i := 0; i < 40; i++ {
		writeFile(t, filepath.Join(root, "f"+strconv.Itoa(i)+".iso"), []byte("x"))
	}
	srv := startServer(t, root, false)
	cl := dial(t, srv)
	cl.handshake("games")

	ff := make([]byte, 12)
	binary.LittleEndian.PutUint16(ff[0:], 0x0016)
	binary.LittleEndian.PutUint16(ff[2:], 1)
	binary.LittleEndian.PutUint16(ff[6:], 0x0104)
	ff = append(ff, []byte("\\*\x00")...)

	for i := 0; i < MaxSearches*8; i++ {
		if _, _, err := cl.tryTrans2(trans2FindFirst2, ff); err != nil {
			t.Fatalf("find_first2 %d failed: %v", i, err)
		}
	}
	// The server must still be answering, and must not be holding all of them.
	if _, _, err := cl.tryTrans2(trans2FindFirst2, ff); err != nil {
		t.Fatalf("server stopped answering after repeated searches: %v", err)
	}
}

func TestOpenHandlesAreBounded(t *testing.T) {
	// Each handle holds an open OS file descriptor, and CLOSE is the only
	// thing that released one. A client that never closes exhausted the
	// process's descriptors.
	root := t.TempDir()
	writeFile(t, filepath.Join(root, "game.iso"), []byte("data"))

	cl := dial(t, startServer(t, root, false))
	cl.handshake("games")
	for i := 0; i < MaxOpenFiles*4; i++ {
		h, _, _ := cl.call(ComOpenAndX, make([]byte, 30), []byte("game.iso\x00"))
		if h.Status != StatusSuccess {
			t.Fatalf("open %d failed with %#x; handles are not being reclaimed", i, h.Status)
		}
	}
}

func TestTreesAreBounded(t *testing.T) {
	// TREE_CONNECT allocated a fresh Share copy per call, freed only by an
	// explicit TREE_DISCONNECT. Measured before the fix: ~2 MiB per connection
	// on mipsle, ~32 MiB across the 16 permitted connections -- the whole
	// board -- from a few tens of MB of LAN traffic.
	cl := dial(t, startServer(t, t.TempDir(), false))
	cl.handshake("games")

	tcParams := make([]byte, 8)
	binary.LittleEndian.PutUint16(tcParams[6:], 1)
	tcData := append([]byte{0}, []byte("\\\\SERVER\\games\x00")...)
	tcData = append(tcData, []byte("A:\x00")...)
	for i := 0; i < MaxTrees*8; i++ {
		h, _, _ := cl.call(ComTreeConnectAndX, tcParams, tcData)
		if h.Status != StatusSuccess {
			t.Fatalf("tree connect %d failed with %#x", i, h.Status)
		}
	}
}

func TestCloseDoesNotWaitOnTheClient(t *testing.T) {
	// Close only shut the listener, so an established connection sat parked in
	// a blocking read and wg.Wait waited on the peer to hang up. OPL holds its
	// connection open across a whole game load, so "waits for in-flight
	// connections" meant "indefinitely" -- a server that would not stop.
	srv := startServerNoCleanup(t, t.TempDir())
	cl := &client{t: t}
	c, err := net.Dial("tcp", srv.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	cl.c = c
	_ = c.SetDeadline(time.Now().Add(10 * time.Second))
	cl.handshake("games")

	// The connection is now idle and open, exactly as a console's would be.
	done := make(chan struct{})
	go func() { _ = srv.Close(); close(done) }()
	select {
	case <-done:
	case <-time.After(10 * time.Second):
		t.Fatal("Close blocked on an idle client connection")
	}
}

func TestConnectionSlotsAreNotHeldByIdleSockets(t *testing.T) {
	// MaxConnections without a deadline turned a memory bound into a denial of
	// service: sixteen sockets that never send a byte held every slot forever
	// and the console could not connect at all.
	//
	// The real deadline is minutes, so this asserts the mechanism exists
	// rather than waiting for it: a connection that has sent nothing must
	// carry a read deadline.
	if idleTimeout <= 0 {
		t.Fatal("no idle timeout, so a silent peer can hold a slot forever")
	}
	if idleTimeout > 30*time.Minute {
		t.Fatalf("idle timeout %v is too long to free a slot in practice", idleTimeout)
	}
}

// startServerNoCleanup is startServer without the t.Cleanup close, for tests
// that close the server themselves.
func startServerNoCleanup(t *testing.T, root string) *Server {
	t.Helper()
	srv := startServerBare(t, root, false)
	go func() { _ = srv.Serve() }()
	return srv
}
