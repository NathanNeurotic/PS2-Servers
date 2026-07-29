package smb

import (
	"bytes"
	"encoding/binary"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/filesystem"
	edgelog "github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
)

// A full conversation in the order OPL actually performs it: NEGOTIATE ->
// SESSION_SETUP -> TREE_CONNECT -> FIND_FIRST2/FIND_NEXT2 -> OPEN -> READ.
// Unit tests on individual handlers would not catch the things that actually
// break a console -- a tid or uid not carried forward, a reply whose offsets
// are computed from the wrong base, a search cursor that never terminates.

type discardWriter struct{}

func (discardWriter) Write(p []byte) (int, error) { return len(p), nil }

// client is a minimal SMB client, enough to drive the server.
type client struct {
	t   *testing.T
	c   net.Conn
	tid uint16
	uid uint16
	mid uint16
}

func startServer(t *testing.T, root string, readOnly bool) *Server {
	t.Helper()
	r, err := filesystem.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	srv, err := New(Config{
		Shares:   []Share{{Name: "games", Root: r}},
		Bind:     "127.0.0.1",
		Port:     0,
		ReadOnly: readOnly,
		Log:      edgelog.New(discardWriter{}, "text", true, false),
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := srv.Listen(); err != nil {
		t.Fatal(err)
	}
	go func() { _ = srv.Serve() }()
	t.Cleanup(func() { _ = srv.Close() })
	return srv
}

func dial(t *testing.T, srv *Server) *client {
	t.Helper()
	c, err := net.Dial("tcp", srv.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = c.Close() })
	_ = c.SetDeadline(time.Now().Add(10 * time.Second))
	return &client{t: t, c: c}
}

// call sends one command and returns the reply's header, params and data.
func (cl *client) call(cmd uint8, params, data []byte) (Header, []byte, []byte) {
	cl.t.Helper()
	cl.mid++
	msg := append(PackHeader(cmd, 0, cl.tid, 0xFEFE, cl.uid, cl.mid), buildBody(params, data)...)
	// A request header carries no reply flag; the server never checks, and
	// reusing PackHeader keeps the test from re-implementing the layout.
	msg[9] = 0
	if err := WriteMessage(cl.c, msg); err != nil {
		cl.t.Fatalf("send %#x: %v", cmd, err)
	}
	reply, err := ReadMessage(cl.c)
	if err != nil {
		cl.t.Fatalf("recv %#x: %v", cmd, err)
	}
	h, err := ParseHeader(reply)
	if err != nil {
		cl.t.Fatalf("parse %#x: %v", cmd, err)
	}
	wc := int(reply[32])
	p := reply[33 : 33+wc*2]
	bcOff := 33 + wc*2
	var d []byte
	if bcOff+2 <= len(reply) {
		bc := int(binary.LittleEndian.Uint16(reply[bcOff:]))
		end := bcOff + 2 + bc
		if end > len(reply) {
			end = len(reply)
		}
		d = reply[bcOff+2 : end]
	}
	return h, p, d
}

// handshake runs NEGOTIATE + SESSION_SETUP + TREE_CONNECT.
func (cl *client) handshake(share string) {
	cl.t.Helper()

	h, p, _ := cl.call(ComNegotiate, nil, []byte("\x02NT LM 0.12\x00"))
	if h.Status != StatusSuccess {
		cl.t.Fatalf("negotiate status %#x", h.Status)
	}
	// 17 words exactly. OPL matches on the struct size and retries forever if
	// it differs, which presents as a console that never connects.
	if len(p) != 34 {
		cl.t.Fatalf("negotiate params are %d bytes, want 34 (17 words)", len(p))
	}

	h, _, _ = cl.call(ComSessionSetupAndX, make([]byte, 26), []byte("\x00"))
	if h.Status != StatusSuccess {
		cl.t.Fatalf("session setup status %#x", h.Status)
	}
	if h.UID == 0 {
		cl.t.Fatal("session setup returned uid 0; the client would stay unauthenticated")
	}
	cl.uid = h.UID

	tcParams := make([]byte, 8)
	binary.LittleEndian.PutUint16(tcParams[6:], 1) // PasswordLength
	tcData := append([]byte{0}, []byte("\\\\SERVER\\"+share+"\x00")...)
	tcData = append(tcData, []byte("A:\x00")...)
	h, _, _ = cl.call(ComTreeConnectAndX, tcParams, tcData)
	if h.Status != StatusSuccess {
		cl.t.Fatalf("tree connect status %#x", h.Status)
	}
	if h.TID == 0 {
		cl.t.Fatal("tree connect returned tid 0")
	}
	cl.tid = h.TID
}

// trans2 issues a TRANS2 with one setup word and returns the reply's parameter
// and data blocks, located via the offsets the server reported.
func (cl *client) trans2(sub uint16, tParams []byte) ([]byte, []byte) {
	cl.t.Helper()
	// Request: 14 fixed words + SetupCount/Reserved + one setup word, then the
	// parameter block. Offsets are absolute from the SMB header start.
	const wordCount = 15
	paramOff := 32 + 1 + wordCount*2 + 2
	p := make([]byte, wordCount*2)
	binary.LittleEndian.PutUint16(p[0:], uint16(len(tParams))) // TotalParameterCount
	binary.LittleEndian.PutUint16(p[2:], 0)                    // TotalDataCount
	binary.LittleEndian.PutUint16(p[4:], 10)                   // MaxParameterCount
	binary.LittleEndian.PutUint16(p[6:], 0xFFFF)               // MaxDataCount
	binary.LittleEndian.PutUint16(p[18:], uint16(len(tParams)))
	binary.LittleEndian.PutUint16(p[20:], uint16(paramOff))
	binary.LittleEndian.PutUint16(p[22:], 0)
	binary.LittleEndian.PutUint16(p[24:], 0)
	p[26] = 1 // SetupCount
	binary.LittleEndian.PutUint16(p[28:], sub)

	h, rp, rd := cl.call(ComTrans2, p, tParams)
	if h.Status != StatusSuccess {
		cl.t.Fatalf("trans2 %#x status %#x", sub, h.Status)
	}
	// The reply's offsets are absolute from the header, and the body we hold
	// starts after ByteCount at 32+1+20+2 = 55.
	const bodyBase = 32 + 1 + 20 + 2
	pOff := int(binary.LittleEndian.Uint16(rp[8:])) - bodyBase
	pCnt := int(binary.LittleEndian.Uint16(rp[6:]))
	dOff := int(binary.LittleEndian.Uint16(rp[14:])) - bodyBase
	dCnt := int(binary.LittleEndian.Uint16(rp[12:]))
	clamp := func(off, n int) []byte {
		if off < 0 || off > len(rd) {
			return nil
		}
		end := off + n
		if end > len(rd) {
			end = len(rd)
		}
		return rd[off:end]
	}
	return clamp(pOff, pCnt), clamp(dOff, dCnt)
}

func writeFile(t *testing.T, path string, content []byte) {
	t.Helper()
	if err := os.WriteFile(path, content, 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestFullConversationListsAndReadsAFile(t *testing.T) {
	root := t.TempDir()
	payload := bytes.Repeat([]byte("PS2DATA!"), 2048) // 16 KiB
	writeFile(t, filepath.Join(root, "game.iso"), payload)
	if err := os.Mkdir(filepath.Join(root, "ART"), 0o755); err != nil {
		t.Fatal(err)
	}

	cl := dial(t, startServer(t, root, false))
	cl.handshake("games")

	// FIND_FIRST2 over the share root. Request params are
	// SearchAttributes(2) SearchCount(2) Flags(2) LevelOfInterest(2)
	// StorageType(4) then the pattern.
	ff := make([]byte, 12)
	binary.LittleEndian.PutUint16(ff[0:], 0x0016)
	binary.LittleEndian.PutUint16(ff[2:], 1)
	binary.LittleEndian.PutUint16(ff[6:], 0x0104)
	ff = append(ff, []byte("\\*\x00")...)
	rp, rd := cl.trans2(trans2FindFirst2, ff)
	if len(rp) < 10 {
		t.Fatalf("find_first2 params are %d bytes, want 10", len(rp))
	}
	sid := binary.LittleEndian.Uint16(rp[0:])
	if binary.LittleEndian.Uint16(rp[2:]) != 1 {
		t.Fatal("find_first2 did not report exactly one entry")
	}
	if len(rd) < 94 {
		t.Fatalf("directory entry is %d bytes, want at least 94", len(rd))
	}

	// Walk the whole listing via FIND_NEXT2 and collect the names.
	names := []string{string(rd[94:])}
	for i := 0; i < 32; i++ {
		next := make([]byte, 12)
		binary.LittleEndian.PutUint16(next[0:], sid)
		binary.LittleEndian.PutUint16(next[2:], 1)
		binary.LittleEndian.PutUint16(next[10:], 0x0104)
		np, nd := cl.trans2(trans2FindNext2, next)
		if len(np) < 4 {
			t.Fatalf("find_next2 params are %d bytes", len(np))
		}
		count := binary.LittleEndian.Uint16(np[0:])
		if count == 0 {
			break
		}
		if len(nd) > 94 {
			names = append(names, string(nd[94:]))
		}
		if binary.LittleEndian.Uint16(np[2:]) == 1 {
			break
		}
	}

	found := map[string]bool{}
	for _, n := range names {
		found[n] = true
	}
	for _, want := range []string{".", "..", "ART", "game.iso"} {
		if !found[want] {
			t.Fatalf("listing %v is missing %q", names, want)
		}
	}

	// OPEN_ANDX then READ_ANDX, which is how a game actually loads.
	h, p, _ := cl.call(ComOpenAndX, make([]byte, 30), []byte("game.iso\x00"))
	if h.Status != StatusSuccess {
		t.Fatalf("open status %#x", h.Status)
	}
	fid := binary.LittleEndian.Uint16(p[4:])
	if size := binary.LittleEndian.Uint32(p[12:]); size != uint32(len(payload)) {
		t.Fatalf("open reported size %d, want %d", size, len(payload))
	}

	got := make([]byte, 0, len(payload))
	for off := 0; off < len(payload); {
		rq := make([]byte, 24)
		binary.LittleEndian.PutUint16(rq[4:], fid)
		binary.LittleEndian.PutUint32(rq[6:], uint32(off))
		binary.LittleEndian.PutUint16(rq[10:], 4096) // MaxCountLow
		h, rp, rdata := cl.call(ComReadAndX, rq, nil)
		if h.Status != StatusSuccess {
			t.Fatalf("read status %#x at offset %d", h.Status, off)
		}
		n := int(binary.LittleEndian.Uint16(rp[10:]))
		if n == 0 {
			break
		}
		// DataOffset must be exactly 59: OPL reads from that offset
		// unconditionally rather than honouring what the server reports.
		if dataOff := binary.LittleEndian.Uint16(rp[12:]); dataOff != readAndXDataOffset {
			t.Fatalf("DataOffset = %d, want %d", dataOff, readAndXDataOffset)
		}
		if len(rdata) < n {
			t.Fatalf("read claimed %d bytes but sent %d", n, len(rdata))
		}
		got = append(got, rdata[:n]...)
		off += n
	}
	if !bytes.Equal(got, payload) {
		t.Fatalf("read back %d bytes, want %d, and they differ", len(got), len(payload))
	}

	cl.call(ComClose, func() []byte {
		b := make([]byte, 6)
		binary.LittleEndian.PutUint16(b[0:], fid)
		return b
	}(), nil)
}

func TestReadOnlyRefusesWrites(t *testing.T) {
	root := t.TempDir()
	writeFile(t, filepath.Join(root, "save.bin"), bytes.Repeat([]byte{0}, 64))

	cl := dial(t, startServer(t, root, true))
	cl.handshake("games")

	h, p, _ := cl.call(ComOpenAndX, make([]byte, 30), []byte("save.bin\x00"))
	if h.Status != StatusSuccess {
		t.Fatalf("open status %#x", h.Status)
	}
	fid := binary.LittleEndian.Uint16(p[4:])

	wq := make([]byte, 28)
	binary.LittleEndian.PutUint16(wq[4:], fid)
	binary.LittleEndian.PutUint16(wq[20:], 4) // DataLengthLow
	binary.LittleEndian.PutUint16(wq[22:], 0)
	h, _, _ = cl.call(ComWriteAndX, wq, []byte("XXXX"))
	if h.Status != StatusAccessDenied {
		t.Fatalf("write to a read-only share returned %#x, want ACCESS_DENIED", h.Status)
	}
}

func TestWriteThenReadBack(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "save.bin")
	writeFile(t, path, bytes.Repeat([]byte{0}, 32))

	cl := dial(t, startServer(t, root, false))
	cl.handshake("games")

	h, p, _ := cl.call(ComOpenAndX, make([]byte, 30), []byte("save.bin\x00"))
	if h.Status != StatusSuccess {
		t.Fatalf("open status %#x", h.Status)
	}
	fid := binary.LittleEndian.Uint16(p[4:])

	// DataOffset is absolute from the SMB header, so it must account for this
	// request's own word count -- the server indexes the raw message with it.
	const wc = 14
	dataOff := 32 + 1 + wc*2 + 2
	wq := make([]byte, wc*2)
	binary.LittleEndian.PutUint16(wq[4:], fid)
	binary.LittleEndian.PutUint32(wq[6:], 8) // offset into the file
	binary.LittleEndian.PutUint16(wq[20:], 5)
	binary.LittleEndian.PutUint16(wq[22:], uint16(dataOff))
	h, wp, _ := cl.call(ComWriteAndX, wq, []byte("HELLO"))
	if h.Status != StatusSuccess {
		t.Fatalf("write status %#x", h.Status)
	}
	if n := binary.LittleEndian.Uint16(wp[4:]); n != 5 {
		t.Fatalf("server reported %d bytes written, want 5", n)
	}

	onDisk, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(onDisk[8:13], []byte("HELLO")) {
		t.Fatalf("file contains %q at offset 8, want HELLO", onDisk[8:13])
	}
}

func TestPathsCannotEscapeTheShare(t *testing.T) {
	root := t.TempDir()
	outside := filepath.Join(filepath.Dir(root), "outside-secret.txt")
	writeFile(t, outside, []byte("NOT SHAREABLE"))
	t.Cleanup(func() { _ = os.Remove(outside) })

	cl := dial(t, startServer(t, root, false))
	cl.handshake("games")

	for _, probe := range []string{
		"..\\outside-secret.txt",
		"..\\..\\outside-secret.txt",
		"sub\\..\\..\\outside-secret.txt",
		"\\..\\outside-secret.txt",
	} {
		t.Run(probe, func(t *testing.T) {
			h, _, _ := cl.call(ComOpenAndX, make([]byte, 30), append([]byte(probe), 0))
			if h.Status == StatusSuccess {
				t.Fatalf("%q was served from outside the share", probe)
			}
		})
	}
}

func TestUnknownCommandIsRefusedNotFatal(t *testing.T) {
	cl := dial(t, startServer(t, t.TempDir(), false))
	cl.handshake("games")

	h, _, _ := cl.call(0x99, nil, nil)
	if h.Status != StatusNotImplemented {
		t.Fatalf("unknown command returned %#x, want NOT_IMPLEMENTED", h.Status)
	}
	// The connection must survive it: OPL probes commands it may not need.
	h, _, _ = cl.call(ComEcho, func() []byte {
		b := make([]byte, 2)
		binary.LittleEndian.PutUint16(b, 1)
		return b
	}(), []byte("ping"))
	if h.Status != StatusSuccess {
		t.Fatal("connection did not survive an unknown command")
	}
}

func TestEchoBouncesThePayload(t *testing.T) {
	cl := dial(t, startServer(t, t.TempDir(), false))
	cl.handshake("games")
	_, _, d := cl.call(ComEcho, func() []byte {
		b := make([]byte, 2)
		binary.LittleEndian.PutUint16(b, 1)
		return b
	}(), []byte("keepalive"))
	if string(d) != "keepalive" {
		t.Fatalf("echo returned %q", d)
	}
}
