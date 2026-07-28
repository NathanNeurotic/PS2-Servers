package udpfs

import (
	"context"
	"encoding/binary"
	"math"
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

// startBlockServer brings up UDPFS with a disk image attached, so the same
// server answers both file and sector requests -- which is the point of
// carrying block access inside UDPFS rather than requiring a second service.
func startBlockServer(t *testing.T, cfg Config) (*net.UDPAddr, string, []byte) {
	t.Helper()
	_, disc, imgPath, img := startBlockServerFull(t, cfg)
	return disc, imgPath, img
}

// startBlockServerFull additionally hands back the server, for tests that need
// to read its counters rather than only its wire responses.
func startBlockServerFull(t *testing.T, cfg Config) (*Server, *net.UDPAddr, string, []byte) {
	t.Helper()
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "game.iso"), []byte("0123456789abcdef"), 0o644); err != nil {
		t.Fatal(err)
	}
	img := make([]byte, 64*512) // 64 sectors
	for i := range img {
		img[i] = byte((i*7 + i/512) & 0xFF)
	}
	imgPath := filepath.Join(t.TempDir(), "ps2.img")
	if err := os.WriteFile(imgPath, img, 0o644); err != nil {
		t.Fatal(err)
	}

	cfg.Root = root
	cfg.Bind = "127.0.0.1"
	cfg.Port = freeUDPPort(t)
	cfg.BlockDevice = imgPath
	cfg.PeerTimeout = time.Minute
	cfg.FallbackDelay = 30 * time.Millisecond
	cfg.Log = edgelog.New(discardWriter{}, "text", true, false)

	server, err := New(cfg)
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
		case <-done:
		case <-time.After(2 * time.Second):
			t.Error("server did not stop")
		}
		server.Close()
	})
	return server, disc, imgPath, img
}

// breadMsg builds the 16-byte BREAD/BWRITE header.
func blockMsg(op protocol.MessageType, handle int32, sector uint64, count uint16) []byte {
	b := make([]byte, 16)
	b[0] = byte(op)
	binary.LittleEndian.PutUint16(b[2:4], count)
	binary.LittleEndian.PutUint32(b[4:8], uint32(handle))
	binary.LittleEndian.PutUint32(b[8:12], uint32(sector))
	binary.LittleEndian.PutUint32(b[12:16], uint32(sector>>32))
	return b
}

func TestBlockReadReturnsTheImageSectors(t *testing.T) {
	disc, _, img := startBlockServer(t, Config{})
	c := dial(t, disc)

	// Handle 0 is the shared image and is never returned by OPEN.
	reply := c.send(blockMsg(protocol.BReadRequest, 0, 4, 2))
	if protocol.MessageType(reply[0]) != protocol.ResultReply {
		t.Fatalf("expected ResultReply, got opcode 0x%02x", reply[0])
	}
	n := result(reply)
	if n != 2*512 {
		t.Fatalf("BREAD returned %d bytes, want %d", n, 2*512)
	}
	got := reply[8 : 8+int(n)]
	want := img[4*512 : 6*512]
	if string(got) != string(want) {
		t.Fatal("BREAD data did not match the image")
	}
}

func TestBlockReadPastTheEndIsRefused(t *testing.T) {
	disc, _, img := startBlockServer(t, Config{})
	c := dial(t, disc)
	sectors := uint64(len(img) / 512)

	// Answering this by padding with zeroes would let a bad count pull an
	// arbitrary amount of traffic out of the server.
	reply := c.send(blockMsg(protocol.BReadRequest, 0, sectors-1, 8))
	if got := result(reply); got != -int32(syscall.EINVAL) {
		t.Fatalf("out-of-range BREAD returned %d, want -EINVAL", got)
	}
}

func TestBlockReadOnAFileHandleIsRefused(t *testing.T) {
	disc, _, _ := startBlockServer(t, Config{})
	c := dial(t, disc)

	h := openForWriteOK(t, c, "game.iso", fioRead)
	if h == 0 {
		t.Fatal("OPEN returned the reserved block handle")
	}
	reply := c.send(blockMsg(protocol.BReadRequest, h, 0, 1))
	if got := result(reply); got != -int32(syscall.EBADF) {
		t.Fatalf("BREAD on a file handle returned %d, want -EBADF", got)
	}
}

func TestBlockWriteLandsInTheImage(t *testing.T) {
	disc, imgPath, _ := startBlockServer(t, Config{})
	c := dial(t, disc)

	payload := make([]byte, 512)
	for i := range payload {
		payload[i] = 0xA5
	}
	req := append(blockMsg(protocol.BWriteRequest, 0, 10, 1), writeDataMsg(0, 1, payload)...)
	reply := c.send(req)
	if protocol.MessageType(reply[0]) != protocol.WriteDone {
		t.Fatalf("expected WriteDone, got opcode 0x%02x", reply[0])
	}
	if got := result(reply); got != 512 {
		t.Fatalf("BWRITE reported %d bytes, want 512", got)
	}
	onDisk, err := os.ReadFile(imgPath)
	if err != nil {
		t.Fatal(err)
	}
	if string(onDisk[10*512:11*512]) != string(payload) {
		t.Fatal("BWRITE did not land at the requested sector")
	}
}

func TestBlockWriteRefusedOnAReadOnlyServer(t *testing.T) {
	disc, imgPath, img := startBlockServer(t, Config{ReadOnly: true})
	c := dial(t, disc)

	req := append(blockMsg(protocol.BWriteRequest, 0, 3, 1), writeDataMsg(0, 1, make([]byte, 512))...)
	reply := c.send(req)
	if got := result(reply); got != -int32(syscall.EACCES) {
		t.Fatalf("BWRITE on a read-only server returned %d, want -EACCES", got)
	}
	onDisk, err := os.ReadFile(imgPath)
	if err != nil {
		// Discarding this would leave onDisk nil and panic on the slice below,
		// hiding the real I/O failure behind an index-out-of-range.
		t.Fatal(err)
	}
	if string(onDisk[3*512:4*512]) != string(img[3*512:4*512]) {
		t.Fatal("a read-only server modified the image")
	}
}

// TestBlockAndFileShareOneServer is the whole point of block access inside
// UDPFS: one server, one port, both kinds of request.
func TestBlockAndFileShareOneServer(t *testing.T) {
	disc, _, img := startBlockServer(t, Config{})
	c := dial(t, disc)

	h := openForWriteOK(t, c, "game.iso", fioRead)
	read := make([]byte, 12)
	read[0] = byte(protocol.ReadRequest)
	binary.LittleEndian.PutUint32(read[4:8], uint32(h))
	binary.LittleEndian.PutUint32(read[8:12], 16)
	fileReply := c.send(read)
	if got := string(fileReply[8:24]); got != "0123456789abcdef" {
		t.Fatalf("file read through the same server returned %q", got)
	}

	blockReply := c.send(blockMsg(protocol.BReadRequest, 0, 0, 1))
	if string(blockReply[8:8+512]) != string(img[:512]) {
		t.Fatal("sector read through the same server did not match the image")
	}
}

func TestCustomSectorSizeIsHonoured(t *testing.T) {
	disc, _, img := startBlockServer(t, Config{SectorSize: 2048})
	c := dial(t, disc)

	reply := c.send(blockMsg(protocol.BReadRequest, 0, 1, 1))
	// The reported length is what proves the sector size was honoured: one
	// sector now means 2048 bytes rather than 512, so the read starts at a
	// different offset and returns four times as much.
	if got := result(reply); got != 2048 {
		t.Fatalf("with --sector-size 2048 a one-sector read returned %d bytes", got)
	}
	// Only compare the bytes that arrived in this datagram. A 2048-byte payload
	// exceeds one UDPRDMA packet, and c.send deliberately returns a single
	// reply rather than reassembling a transfer -- the reassembly path is
	// covered by the conformance probe.
	got := reply[8:]
	want := img[1*2048:]
	if len(got) > len(want) {
		got = got[:len(want)]
	}
	if len(got) == 0 || string(got) != string(want[:len(got)]) {
		t.Fatalf("2048-byte sector read started at the wrong offset (compared %d bytes)", len(got))
	}
}

// TestNoCompressionStopsAdvertisingContainersAsIso: with decompression off, a
// .cso must not be offered under an .iso name -- the console cannot read
// container bytes, so renaming them would hand it garbage.
func TestNoCompressionStopsAdvertisingContainersAsIso(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "game.cso"), []byte("CISOxxxxxxxxxxxx"), 0o644); err != nil {
		t.Fatal(err)
	}
	server, err := New(Config{
		Root: root, Bind: "127.0.0.1", Port: freeUDPPort(t),
		NoCompression: true, PeerTimeout: time.Minute,
		Log: edgelog.New(discardWriter{}, "text", true, false),
	})
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()

	// A request for the virtual .iso must not resolve to the container.
	if _, err := server.resolveImagePath("game.iso"); err == nil {
		t.Fatal("--no-compression still substituted a .cso for a missing .iso")
	}
	// The real name still resolves.
	if _, err := server.resolveImagePath("game.cso"); err != nil {
		t.Fatalf("the container itself should still be reachable: %v", err)
	}
}

func TestCompressionCacheDefaultsAndDisables(t *testing.T) {
	root := t.TempDir()
	mk := func(cache int) *Server {
		s, err := New(Config{
			Root: root, Bind: "127.0.0.1", Port: freeUDPPort(t),
			CompressionCache: cache, PeerTimeout: time.Minute,
			Log: edgelog.New(discardWriter{}, "text", true, false),
		})
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { s.Close() })
		return s
	}
	if got := mk(0).cfg.CompressionCache; got != 0 {
		t.Fatalf("unset cache size stored as %d", got)
	}
	if got := mk(8).cfg.CompressionCache; got != 8 {
		t.Fatalf("cache size 8 stored as %d", got)
	}
}

var _ = session.Pending // keep the session import meaningful across refactors

// TestBlockRangeCheckSurvivesIntegerOverflow guards a real defect found in
// review: sector arrives from the client as lo|hi<<32, so a value near
// MaxInt64 made `sector + count` wrap silently -- Go does not panic on signed
// overflow. The wrapped sum is negative, so both the `sector < 0` guard and the
// upper-bound guard passed and the range check was bypassed entirely. The
// resulting offset could land inside the real file, which on BWRITE is silent
// corruption of the operator's image at an attacker-chosen position.
func TestBlockRangeCheckSurvivesIntegerOverflow(t *testing.T) {
	dev := &blockDevice{size: 64 * 512, sectorSize: 512}

	hostile := []struct {
		name   string
		sector int64
		count  int64
	}{
		{"near MaxInt64", math.MaxInt64 - 4, 8},
		{"exactly MaxInt64", math.MaxInt64, 1},
		{"negative sector", -1, 1},
		{"zero count", 0, 0},
		{"negative count", 0, -1},
		{"one past the end", 64, 1},
		{"straddles the end", 60, 8},
	}
	for _, c := range hostile {
		if dev.inRange(c.sector, c.count) {
			t.Errorf("%s: sector=%d count=%d was accepted; it must be refused",
				c.name, c.sector, c.count)
		}
	}

	valid := []struct{ sector, count int64 }{{0, 1}, {0, 64}, {63, 1}, {32, 32}}
	for _, c := range valid {
		if !dev.inRange(c.sector, c.count) {
			t.Errorf("sector=%d count=%d is inside the image but was refused",
				c.sector, c.count)
		}
	}
}

// TestBlockReadRejectsOverflowingSectorOverTheWire drives the same case through
// a live server, because the unit check above would still pass if a handler
// stopped calling inRange.
func TestBlockReadRejectsOverflowingSectorOverTheWire(t *testing.T) {
	disc, imgPath, img := startBlockServer(t, Config{})
	c := dial(t, disc)

	reply := c.send(blockMsg(protocol.BReadRequest, 0, uint64(math.MaxInt64-4), 8))
	if got := result(reply); got >= 0 {
		t.Fatalf("an overflowing BREAD returned %d instead of an error", got)
	}

	// And the write path, which is the one that could corrupt the image.
	req := append(blockMsg(protocol.BWriteRequest, 0, uint64(math.MaxInt64-4), 8),
		writeDataMsg(0, 1, make([]byte, 512))...)
	if got := result(c.send(req)); got >= 0 {
		t.Fatalf("an overflowing BWRITE returned %d instead of an error", got)
	}
	after, err := os.ReadFile(imgPath)
	if err != nil {
		t.Fatal(err)
	}
	if string(after) != string(img) {
		t.Fatal("an overflowing BWRITE modified the image")
	}
}

// TestBlockWriteCannotExceedItsDeclaredSectorCount closes the second way the
// range check could be walked around.
//
// inRange validates the sector range the BWRITE header DECLARES. It does not
// constrain what the chunk-assembly path then accumulates: handleWriteData is
// driven by the client's own totalChunks/chunkSize fields and bounds the buffer
// only by maxWriteBytes, which has nothing to do with the declared count. So a
// client could declare one in-range sector at the very end of the image, then
// stream megabytes of chunks, and completeBlockWrite would hand all of it to
// WriteAt at the validated offset. WriteAt past EOF extends the file, so the
// image silently grows and the bytes past the declared range are attacker-chosen.
//
// Validating a length and then writing a different one is the same defect as
// the overflow bug, wearing different clothes: the quantity that was checked is
// not the quantity that is used.
func TestBlockWriteCannotExceedItsDeclaredSectorCount(t *testing.T) {
	disc, imgPath, img := startBlockServer(t, Config{})
	c := dial(t, disc)

	last := uint64(len(img)/512) - 1 // in range: [last, last+1) is the final sector
	payload := make([]byte, 512)
	for i := range payload {
		payload[i] = 0xEE
	}

	// Declare one sector, then announce four chunks and send them. Only the
	// final chunk draws a WRITE_DONE; the rest answer with a bare ACK.
	c.sendNoReply(append(blockMsg(protocol.BWriteRequest, 0, last, 1),
		writeDataMsg(0, 4, payload)...))
	c.sendNoReply(writeDataMsg(1, 4, payload))
	c.sendNoReply(writeDataMsg(2, 4, payload))
	reply := c.send(writeDataMsg(3, 4, payload))

	if got := result(reply); got > 512 {
		t.Errorf("server accepted %d bytes for a write that declared 512", got)
	}

	onDisk, err := os.ReadFile(imgPath)
	if err != nil {
		t.Fatal(err)
	}
	if len(onDisk) != len(img) {
		t.Fatalf("the image grew from %d to %d bytes; a BWRITE wrote past the "+
			"end of the disk it declared", len(img), len(onDisk))
	}
	// Everything before the declared sector must be untouched.
	if string(onDisk[:last*512]) != string(img[:last*512]) {
		t.Fatal("a BWRITE modified sectors outside its declared range")
	}
}

// TestBlockWriteShortOfItsDeclaredCountIsRefused is the mirror case. Declaring
// four sectors and delivering one is not corruption, but reporting it as a
// success tells the console a save committed that only partly landed -- and the
// three sectors it believes it wrote still hold their old contents.
func TestBlockWriteShortOfItsDeclaredCountIsRefused(t *testing.T) {
	disc, imgPath, img := startBlockServer(t, Config{})
	c := dial(t, disc)

	payload := make([]byte, 512)
	for i := range payload {
		payload[i] = 0x5A
	}
	// Header declares 4 sectors; the chunk sequence delivers one and ends.
	reply := c.send(append(blockMsg(protocol.BWriteRequest, 0, 2, 4),
		writeDataMsg(0, 1, payload)...))
	if got := result(reply); got >= 0 {
		t.Errorf("a BWRITE that delivered 512 of a declared 2048 bytes "+
			"reported success (%d)", got)
	}
	onDisk, err := os.ReadFile(imgPath)
	if err != nil {
		t.Fatal(err)
	}
	if string(onDisk) != string(img) {
		t.Fatal("a refused BWRITE still modified the image")
	}
}

// TestRejectedBlockRequestsAreStillCounted pins the counting invariant that
// handleMessage's comment states: one request counts once, at dispatch,
// regardless of how the handler exits.
//
// BREAD and BWRITE originally counted inside their handlers, after validation,
// so a rejected request counted zero. That is backwards for the case metrics
// exist to serve: an operator on a headless box watching --metrics while a
// console fails to read needs to see requests arriving and being refused, not a
// counter frozen at zero that looks identical to a console that never connected.
func TestRejectedBlockRequestsAreStillCounted(t *testing.T) {
	server, disc, _, img := startBlockServerFull(t, Config{})
	c := dial(t, disc)
	sectors := uint64(len(img) / 512)

	// Both of these are refused: out of range, and aimed at a file handle.
	c.send(blockMsg(protocol.BReadRequest, 0, sectors+100, 1))
	c.send(blockMsg(protocol.BReadRequest, 99, 0, 1))
	// A BWRITE that is refused before any data is assembled.
	c.send(append(blockMsg(protocol.BWriteRequest, 0, sectors+100, 1),
		writeDataMsg(0, 1, make([]byte, 512))...))

	if got := server.stats.bread.Load(); got != 2 {
		t.Errorf("refused BREADs counted %d times, want 2", got)
	}
	if got := server.stats.bwrite.Load(); got != 1 {
		t.Errorf("refused BWRITE counted %d times, want 1", got)
	}
	if got := server.stats.bytesRead.Load(); got != 0 {
		t.Errorf("refused BREADs reported %d bytes transferred, want 0", got)
	}
}
