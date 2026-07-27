package udpfs

import (
	"context"
	"encoding/binary"
	"net"
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"

	edgelog "github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
)

// PS2 FIO open flags, as the client sends them.
const (
	fioRead   = 0x0001
	fioWrite  = 0x0002
	fioAppend = 0x0100
	fioCreate = 0x0200
	fioTrunc  = 0x0400
)

// startServerAt brings up a server over the given root so a test can inspect
// what actually landed on disk.
func startServerAt(t *testing.T, root string, readOnly bool) *net.UDPAddr {
	t.Helper()
	server, err := New(Config{
		Root: root, Bind: "127.0.0.1", Port: freeUDPPort(t), DataPort: 0,
		ProtocolMode: session.Pending, PeerTimeout: time.Minute,
		FallbackDelay: 30 * time.Millisecond, ReadOnly: readOnly,
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

// client drives one peer through the discovery handshake and then tracks its
// own request sequence, so each test reads as a sequence of operations rather
// than packet bookkeeping.
type client struct {
	t        *testing.T
	conn     *net.UDPConn
	dataAddr *net.UDPAddr
	seq      uint16
}

func dial(t *testing.T, disc *net.UDPAddr) *client {
	t.Helper()
	conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
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

// send transmits one request and returns the server's reply payload,
// acknowledging it so the server's transmit window does not stall.
func (c *client) send(msg []byte) []byte {
	c.t.Helper()
	if _, err := c.conn.WriteToUDP(dataPacket(c.seq, msg), c.dataAddr); err != nil {
		c.t.Fatal(err)
	}
	c.seq++
	h, payload, from := recvDataPayload(c.t, c.conn)
	_, _ = c.conn.WriteToUDP(ackPacket(h.Sequence), from)
	return payload
}

// sendNoReply transmits a request whose only response is a bare ACK, which
// recvDataPayload filters out. Used for non-final write chunks.
func (c *client) sendNoReply(msg []byte) {
	c.t.Helper()
	if _, err := c.conn.WriteToUDP(dataPacket(c.seq, msg), c.dataAddr); err != nil {
		c.t.Fatal(err)
	}
	c.seq++
}

func openMsg(path string, flags uint16, isDir bool) []byte {
	b := make([]byte, 8)
	b[0] = byte(protocol.OpenRequest)
	if isDir {
		b[1] = 1
	}
	binary.LittleEndian.PutUint16(b[2:4], flags)
	return append(b, append([]byte(path), 0)...)
}

func writeReqMsg(handle int32, size uint32, inline []byte) []byte {
	b := make([]byte, 12)
	b[0] = byte(protocol.WriteRequest)
	binary.LittleEndian.PutUint32(b[4:8], uint32(handle))
	binary.LittleEndian.PutUint32(b[8:12], size)
	return append(b, inline...)
}

func writeDataMsg(chunkNr, totalChunks uint16, data []byte) []byte {
	b := make([]byte, 8)
	b[0] = byte(protocol.WriteData)
	binary.LittleEndian.PutUint16(b[2:4], chunkNr)
	binary.LittleEndian.PutUint16(b[4:6], uint16(len(data)))
	binary.LittleEndian.PutUint16(b[6:8], totalChunks)
	return append(b, data...)
}

func closeMsg(handle int32) []byte {
	b := make([]byte, 8)
	b[0] = byte(protocol.CloseRequest)
	binary.LittleEndian.PutUint32(b[4:8], uint32(handle))
	return b
}

// result reads the signed result field shared by OPEN/WRITE_DONE/CLOSE replies.
func result(payload []byte) int32 {
	if len(payload) < 8 {
		return 0
	}
	return int32(binary.LittleEndian.Uint32(payload[4:8]))
}

func openForWriteOK(t *testing.T, c *client, path string, flags uint16) int32 {
	t.Helper()
	reply := c.send(openMsg(path, flags, false))
	if protocol.MessageType(reply[0]) != protocol.OpenReply {
		t.Fatalf("expected OPEN reply, got opcode 0x%02x", reply[0])
	}
	h := result(reply)
	if h <= 0 {
		t.Fatalf("OPEN %q failed with %d (errno %v)", path, h, syscall.Errno(-h))
	}
	return h
}

func TestWriteRefusedWhenServerIsReadOnly(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "save.bin"), []byte("old"), 0o644); err != nil {
		t.Fatal(err)
	}
	c := dial(t, startServerAt(t, root, true))

	reply := c.send(openMsg("save.bin", fioWrite, false))
	if got := result(reply); got != -int32(syscall.EACCES) {
		t.Fatalf("read-only server allowed write open: got %d, want -EACCES", got)
	}
	// The file on disk must be untouched.
	data, err := os.ReadFile(filepath.Join(root, "save.bin"))
	if err != nil || string(data) != "old" {
		t.Fatalf("file changed on a read-only server: %q %v", data, err)
	}
}

func TestWriteCreatesFileAndStoresBytes(t *testing.T) {
	root := t.TempDir()
	c := dial(t, startServerAt(t, root, false))

	h := openForWriteOK(t, c, "new.sav", fioRead|fioWrite|fioCreate|fioTrunc)
	payload := []byte("PS2 SAVE DATA")
	reply := c.send(writeReqMsg(h, uint32(len(payload)), writeDataMsg(0, 1, payload)))
	if protocol.MessageType(reply[0]) != protocol.WriteDone {
		t.Fatalf("expected WRITE_DONE, got opcode 0x%02x", reply[0])
	}
	if got := result(reply); got != int32(len(payload)) {
		t.Fatalf("WRITE_DONE reported %d bytes, want %d", got, len(payload))
	}
	c.send(closeMsg(h))

	got, err := os.ReadFile(filepath.Join(root, "new.sav"))
	if err != nil {
		t.Fatalf("file was not created: %v", err)
	}
	if string(got) != string(payload) {
		t.Fatalf("on-disk contents %q, want %q", got, payload)
	}
}

func TestWriteAssemblesMultipleChunksInOrder(t *testing.T) {
	root := t.TempDir()
	c := dial(t, startServerAt(t, root, false))

	h := openForWriteOK(t, c, "multi.sav", fioRead|fioWrite|fioCreate|fioTrunc)
	// Three chunks; only the last draws a WRITE_DONE.
	c.sendNoReply(writeReqMsg(h, 9, writeDataMsg(0, 3, []byte("aaa"))))
	c.sendNoReply(writeDataMsg(1, 3, []byte("bbb")))
	reply := c.send(writeDataMsg(2, 3, []byte("ccc")))
	if got := result(reply); got != 9 {
		t.Fatalf("WRITE_DONE reported %d, want 9", got)
	}
	c.send(closeMsg(h))

	got, err := os.ReadFile(filepath.Join(root, "multi.sav"))
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "aaabbbccc" {
		t.Fatalf("chunks assembled as %q, want %q", got, "aaabbbccc")
	}
}

func TestWriteRejectsOutOfOrderChunk(t *testing.T) {
	root := t.TempDir()
	c := dial(t, startServerAt(t, root, false))

	h := openForWriteOK(t, c, "ooo.sav", fioRead|fioWrite|fioCreate|fioTrunc)
	// Announce three chunks, then skip chunk 1. Concatenating chunk 2 here
	// would silently corrupt the file, so the sequence must be abandoned.
	c.sendNoReply(writeReqMsg(h, 9, writeDataMsg(0, 3, []byte("aaa"))))
	reply := c.send(writeDataMsg(2, 3, []byte("ccc")))
	if got := result(reply); got != -int32(syscall.EIO) {
		t.Fatalf("out-of-order chunk returned %d, want -EIO", got)
	}
	c.send(closeMsg(h))

	// Nothing partial may have reached the file.
	got, err := os.ReadFile(filepath.Join(root, "ooo.sav"))
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 0 {
		t.Fatalf("aborted write left %q on disk, want empty", got)
	}
}

func TestWriteToCompressedImageIsRefused(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "game.cso"), []byte("CISOxxxx"), 0o644); err != nil {
		t.Fatal(err)
	}
	c := dial(t, startServerAt(t, root, false))

	// The compression layer presents a decompressed view, so a client writing
	// at a decompressed offset would land bytes at the wrong place in the
	// container. Refuse rather than destroy the image.
	reply := c.send(openMsg("game.cso", fioRead|fioWrite, false))
	if got := result(reply); got != -int32(syscall.EACCES) {
		t.Fatalf("write open on a .cso returned %d, want -EACCES", got)
	}
	data, err := os.ReadFile(filepath.Join(root, "game.cso"))
	if err != nil || string(data) != "CISOxxxx" {
		t.Fatalf("compressed image was modified: %q %v", data, err)
	}
}

func TestWriteOpenWithoutCreateOnMissingFileIsENOENT(t *testing.T) {
	c := dial(t, startServerAt(t, t.TempDir(), false))
	reply := c.send(openMsg("absent.sav", fioRead|fioWrite, false))
	if got := result(reply); got != -int32(syscall.ENOENT) {
		t.Fatalf("missing file without O_CREAT returned %d, want -ENOENT", got)
	}
}

func TestSecondPeerCannotOpenTheSameFileForWriting(t *testing.T) {
	root := t.TempDir()
	disc := startServerAt(t, root, false)
	first := dial(t, disc)
	second := dial(t, disc)

	h := openForWriteOK(t, first, "shared.sav", fioRead|fioWrite|fioCreate)
	reply := second.send(openMsg("shared.sav", fioRead|fioWrite|fioCreate, false))
	if got := result(reply); got != -int32(syscall.EBUSY) {
		t.Fatalf("second writer got %d, want -EBUSY", got)
	}
	// Reading the same file concurrently stays allowed.
	if got := result(second.send(openMsg("shared.sav", fioRead, false))); got <= 0 {
		t.Fatalf("concurrent reader was refused with %d", got)
	}
	first.send(closeMsg(h))
}

func TestClosingAWriterReleasesTheFileForOthers(t *testing.T) {
	root := t.TempDir()
	disc := startServerAt(t, root, false)
	first := dial(t, disc)
	second := dial(t, disc)

	h := openForWriteOK(t, first, "handoff.sav", fioRead|fioWrite|fioCreate)
	first.send(closeMsg(h))
	// The reservation must be gone, otherwise one crashed console would lock
	// a save file until the server restarted.
	if got := result(second.send(openMsg("handoff.sav", fioRead|fioWrite, false))); got <= 0 {
		t.Fatalf("file still reserved after close: %d", got)
	}
}

func TestWriteRequestOverCapIsRefused(t *testing.T) {
	root := t.TempDir()
	c := dial(t, startServerAt(t, root, false))

	h := openForWriteOK(t, c, "big.sav", fioRead|fioWrite|fioCreate)
	// A peer announcing a huge write must be refused before the server
	// allocates a buffer for it.
	reply := c.send(writeReqMsg(h, maxWriteBytes+1, nil))
	if got := result(reply); got != -int32(syscall.EFBIG) {
		t.Fatalf("oversized WRITE_REQ returned %d, want -EFBIG", got)
	}
}

func TestWriteToReadOnlyHandleIsRefused(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "ro.sav"), []byte("keep"), 0o644); err != nil {
		t.Fatal(err)
	}
	c := dial(t, startServerAt(t, root, false))

	// Handle opened read-only on a writable server: the access decision is
	// made at OPEN and must not be bypassable by sending WRITE_REQ anyway.
	h := openForWriteOK(t, c, "ro.sav", fioRead)
	reply := c.send(writeReqMsg(h, 4, writeDataMsg(0, 1, []byte("bad!"))))
	if got := result(reply); got != -int32(syscall.EACCES) {
		t.Fatalf("write on a read-only handle returned %d, want -EACCES", got)
	}
	data, err := os.ReadFile(filepath.Join(root, "ro.sav"))
	if err != nil || string(data) != "keep" {
		t.Fatalf("read-only handle allowed a write: %q %v", data, err)
	}
}

func TestAppendModePreservesExistingBytes(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "log.sav"), []byte("head"), 0o644); err != nil {
		t.Fatal(err)
	}
	c := dial(t, startServerAt(t, root, false))

	h := openForWriteOK(t, c, "log.sav", fioRead|fioWrite|fioAppend)
	if got := result(c.send(writeReqMsg(h, 4, writeDataMsg(0, 1, []byte("tail"))))); got != 4 {
		t.Fatalf("append write returned %d, want 4", got)
	}
	c.send(closeMsg(h))

	got, err := os.ReadFile(filepath.Join(root, "log.sav"))
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "headtail" {
		t.Fatalf("append produced %q, want %q", got, "headtail")
	}
}
