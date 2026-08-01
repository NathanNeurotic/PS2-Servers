package udpbd

import (
	"context"
	"encoding/binary"
	"errors"
	"math/rand"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	edgelog "github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
)

// TestBlockShiftMatchesReferenceTable pins the RDMA block-size optimizer against
// values computed from the hardware-validated Python server
// (udpbd_server/udpbd_server.py `_set_block_shift_for_sectors`, which
// udpbd_server/selftest.py asserts in turn).
//
// A mismatch here does not fail loudly on a console -- it silently picks a worse
// packet size, so transfers get slower rather than broken. That is exactly the
// kind of regression a table test has to catch, because nothing else would.
func TestBlockShiftMatchesReferenceTable(t *testing.T) {
	cases := []struct {
		sectors int64
		want    uint8
	}{
		{sectors: 1, want: 7}, {sectors: 2, want: 7}, {sectors: 3, want: 7},
		{sectors: 4, want: 7}, {sectors: 5, want: 6}, {sectors: 6, want: 7},
		{sectors: 7, want: 6}, {sectors: 8, want: 5}, {sectors: 9, want: 6},
		{sectors: 16, want: 5}, {sectors: 17, want: 6}, {sectors: 32, want: 5},
		{sectors: 33, want: 5}, {sectors: 64, want: 3}, {sectors: 100, want: 3},
		{sectors: 128, want: 3}, {sectors: 255, want: 3}, {sectors: 256, want: 3},
		{sectors: 512, want: 3}, {sectors: 1000, want: 3}, {sectors: 1024, want: 3},
		{sectors: 2048, want: 3},
	}
	for _, c := range cases {
		if got := blockShiftForSectors(c.sectors); got != c.want {
			t.Errorf("blockShiftForSectors(%d) = %d, reference says %d",
				c.sectors, got, c.want)
		}
	}
}

// TestBlockGeometryIsConsistent guards the derived sizes. blocks_per_sector in
// particular must divide evenly, or a sector would straddle a block boundary
// and every read after the first would be misaligned.
func TestBlockGeometryIsConsistent(t *testing.T) {
	for _, shift := range []uint8{3, 5, 6, 7} {
		size, perPacket, perSector := blockGeometry(shift)
		if size != 1<<(shift+2) {
			t.Errorf("shift %d: block size %d", shift, size)
		}
		if perSector*size != SectorSize {
			t.Errorf("shift %d: %d blocks x %d != one %d-byte sector",
				shift, perSector, size, SectorSize)
		}
		if perPacket*size > rdmaMaxPayload {
			t.Errorf("shift %d: %d blocks x %d exceeds the %d-byte RDMA payload",
				shift, perPacket, size, rdmaMaxPayload)
		}
	}
}

func TestHeaderAndBlockTypeRoundTrip(t *testing.T) {
	for _, c := range []struct{ cmd, cmdid, cmdpkt uint8 }{
		{CmdInfo, 0, 0}, {CmdReadRDMA, 7, 255}, {CmdWriteDone, 3, 128},
	} {
		cmd, cmdid, cmdpkt := unpackHeader(packHeader(c.cmd, c.cmdid, c.cmdpkt))
		if cmd != c.cmd || cmdid != c.cmdid || cmdpkt != c.cmdpkt {
			t.Errorf("header round trip: got (%d,%d,%d) want (%d,%d,%d)",
				cmd, cmdid, cmdpkt, c.cmd, c.cmdid, c.cmdpkt)
		}
	}
	for _, c := range []struct {
		shift uint8
		count uint16
	}{{7, 1}, {3, 45}, {5, 511}} {
		shift, count := unpackBlockType(packBlockType(c.shift, c.count), 0)
		if shift != c.shift || count != c.count {
			t.Errorf("block_type round trip: got (%d,%d) want (%d,%d)",
				shift, count, c.shift, c.count)
		}
	}
}

// --- integration over real sockets ---------------------------------------- //

func startServer(t *testing.T, readOnly bool) (*net.UDPAddr, string, []byte) {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "test.img")
	img := make([]byte, 1<<20) // 2048 sectors
	rand.New(rand.NewSource(1234)).Read(img)
	if err := os.WriteFile(path, img, 0o644); err != nil {
		t.Fatal(err)
	}
	// Port 0 lets the OS choose, so tests never collide with a real server.
	srv, err := New(Config{
		Image: path, Bind: "127.0.0.1", Port: freePort(t), ReadOnly: readOnly,
		Log: edgelog.New(discard{}, "text", true, false),
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := srv.Listen(); err != nil {
		t.Fatal(err)
	}
	addr := srv.Addr()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- srv.Serve(ctx) }()
	t.Cleanup(func() {
		cancel()
		select {
		case <-done:
		case <-time.After(2 * time.Second):
			t.Error("server did not stop")
		}
		srv.Close()
	})
	return addr, path, img
}

type discard struct{}

func (discard) Write(p []byte) (int, error) { return len(p), nil }

func freePort(t *testing.T) int {
	t.Helper()
	c, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	return c.LocalAddr().(*net.UDPAddr).Port
}

// clientReadBuffer sizes the test client's receive buffer for the largest burst
// it asks for -- a 512-sector READ is 256 KiB, which handleRead sends as ~180
// datagrams back to back with no pacing, because that is what the console
// expects and what the Python reference does.
//
// Nothing throttles that on loopback. There is no 100 Mbit link between the two
// sockets to spread the burst out the way a real console's wire does, so the
// send loop can outrun the receiving goroutine by more than the OS default
// buffer holds (64 KiB on Windows, ~208 KiB on Linux -- both under 256 KiB).
// The kernel then drops the tail, and since UDPBD has no retransmission those
// packets never arrive: the test blocked until its deadline and failed with an
// i/o timeout after ~66 KiB, which read like a server bug and is not one.
//
// This is not leniency. A console has its own buffering in the SMAP driver and
// re-requests what it misses; the strict reassembler here does neither, so it
// has to be given the room the burst actually needs.
const clientReadBuffer = 1 << 20

// packetDeadline bounds the wait for ONE datagram. The connection used to carry
// a single absolute 5s deadline covering everything after the dial, so a slow
// early read spent budget the last read needed, and the failure named whichever
// read happened to be holding the clock rather than whatever actually stalled.
const packetDeadline = 3 * time.Second

// readAttempts bounds the re-requesting in readSectors. Three, because one
// stall is the host's buffer policy and three in a row is the server.
const readAttempts = 3

func dialServer(t *testing.T, addr *net.UDPAddr) *net.UDPConn {
	t.Helper()
	c, err := net.DialUDP("udp4", nil, addr)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { c.Close() })
	if err := c.SetReadBuffer(clientReadBuffer); err != nil {
		t.Fatalf("could not size the receive buffer to %d bytes: %v",
			clientReadBuffer, err)
	}
	_ = c.SetWriteDeadline(time.Now().Add(10 * time.Second))
	_ = c.SetReadDeadline(time.Now().Add(packetDeadline))
	return c
}

// readPacket reads one datagram, giving it its own deadline.
func readPacket(t *testing.T, c *net.UDPConn, buf []byte) (int, error) {
	t.Helper()
	_ = c.SetReadDeadline(time.Now().Add(packetDeadline))
	return c.Read(buf)
}

func sectorReq(cmd, cmdid uint8, sector uint32, count uint16) []byte {
	b := packHeader(cmd, cmdid, 0)
	b = append(b, byte(sector), byte(sector>>8), byte(sector>>16), byte(sector>>24))
	return append(b, byte(count), byte(count>>8))
}

func TestInfoReportsSectorGeometry(t *testing.T) {
	addr, _, img := startServer(t, false)
	c := dialServer(t, addr)

	if _, err := c.Write(append(packHeader(CmdInfo, 0, 0), make([]byte, 6)...)); err != nil {
		t.Fatal(err)
	}
	buf := make([]byte, 2048)
	n, err := readPacket(t, c, buf)
	if err != nil {
		t.Fatal(err)
	}
	cmd, _, _ := unpackHeader(buf[:n])
	if cmd != CmdInfoReply {
		t.Fatalf("got cmd 0x%02x, want INFO_REPLY", cmd)
	}
	size := binary.LittleEndian.Uint32(buf[2:])
	count := binary.LittleEndian.Uint32(buf[6:])
	if size != SectorSize || int64(count) != int64(len(img)/SectorSize) {
		t.Fatalf("INFO reported %d-byte sectors x %d, want %d x %d",
			size, count, SectorSize, len(img)/SectorSize)
	}
}

// readSectors reassembles an RDMA stream, the way a console does.
func readSectors(t *testing.T, c *net.UDPConn, cmdid uint8, sector uint32, count uint16) []byte {
	t.Helper()
	want := int(count) * SectorSize
	buf := make([]byte, 2048)

	// Re-request a stream that stalls, which is what a console does: UDPBD has
	// no retransmission, so a lost RDMA packet is only ever recovered by asking
	// again. Sizing the receive buffer is not enough on its own to make that
	// impossible -- Linux caps SO_RCVBUF at net.core.rmem_max (212992 by
	// default, under the 256 KiB a 512-sector read delivers), so how much of
	// the ask survives is the host's policy, not ours.
	//
	// This does not soften what the test checks. Every byte still has to match
	// the image, and a server that truncates or stops answering fails all the
	// attempts rather than one.
	for attempt := 1; ; attempt++ {
		if _, err := c.Write(sectorReq(CmdRead, cmdid, sector, count)); err != nil {
			t.Fatal(err)
		}
		out := make([]byte, 0, want)
		stalled := false
		for len(out) < want {
			// Per packet, not per transfer: 180 datagrams sharing one deadline
			// made the timeout depend on how many had already arrived.
			n, err := readPacket(t, c, buf)
			if err != nil {
				if errors.Is(err, os.ErrDeadlineExceeded) && attempt < readAttempts {
					stalled = true
					break
				}
				t.Fatalf("read sectors %d+%d after %d attempt(s): %v (got %d/%d bytes)",
					sector, count, attempt, err, len(out), want)
			}
			cmd, gotID, _ := unpackHeader(buf[:n])
			if cmd != CmdReadRDMA || gotID != cmdid {
				t.Fatalf("unexpected reply cmd=0x%02x cmdid=%d", cmd, gotID)
			}
			shift, blocks := unpackBlockType(buf[:n], 2)
			size := int(blocks) * (1 << (shift + 2))
			if n < 6+size {
				t.Fatalf("RDMA payload %d shorter than its block_type %d", n-6, size)
			}
			out = append(out, buf[6:6+size]...)
		}
		if !stalled {
			return out[:want]
		}
		// Late packets from the abandoned burst are still queued, and appending
		// them to the retry would splice two streams into one wrong answer.
		drain(t, c)
	}
}

// drain discards whatever is already queued, so an abandoned transfer cannot
// leak into the next one.
func drain(t *testing.T, c *net.UDPConn) {
	t.Helper()
	buf := make([]byte, 2048)
	for {
		_ = c.SetReadDeadline(time.Now().Add(150 * time.Millisecond))
		if _, err := c.Read(buf); err != nil {
			return
		}
	}
}

// TestReadsMatchTheImageAcrossBlockRegimes covers each branch of the optimizer,
// because packet sizing changes with the request size and an off-by-one in the
// loop would only show up in one regime.
func TestReadsMatchTheImageAcrossBlockRegimes(t *testing.T) {
	addr, _, img := startServer(t, false)
	c := dialServer(t, addr)

	for i, tc := range []struct {
		sector uint32
		count  uint16
	}{{0, 1}, {100, 8}, {777, 17}, {1000, 512}} {
		got := readSectors(t, c, uint8(i%8), tc.sector, tc.count)
		want := img[int(tc.sector)*SectorSize : (int(tc.sector)+int(tc.count))*SectorSize]
		if string(got) != string(want) {
			t.Fatalf("sectors %d+%d did not match the image", tc.sector, tc.count)
		}
	}
}

func TestWriteLandsInTheImage(t *testing.T) {
	addr, path, _ := startServer(t, false)
	c := dialServer(t, addr)

	const sector, count = 200, 2
	payload := make([]byte, count*SectorSize)
	for i := range payload {
		payload[i] = byte(i % 251)
	}
	if _, err := c.Write(sectorReq(CmdWrite, 2, sector, count)); err != nil {
		t.Fatal(err)
	}
	pkt := packHeader(CmdWriteRDMA, 2, 0)
	pkt = append(pkt, packBlockType(7, count)...) // 512-byte blocks
	pkt = append(pkt, payload...)
	if _, err := c.Write(pkt); err != nil {
		t.Fatal(err)
	}
	buf := make([]byte, 2048)
	n, err := readPacket(t, c, buf)
	if err != nil {
		t.Fatal(err)
	}
	cmd, _, _ := unpackHeader(buf[:n])
	result := int32(binary.LittleEndian.Uint32(buf[2:]))
	if cmd != CmdWriteDone || result != 0 {
		t.Fatalf("WRITE_DONE cmd=0x%02x result=%d, want (0x%02x, 0)", cmd, result, CmdWriteDone)
	}
	onDisk, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(onDisk[sector*SectorSize:(sector+count)*SectorSize]) != string(payload) {
		t.Fatal("write did not land in the image")
	}
}

// TestUnsolicitedWriteRDMAIsIgnored: without a CMD_WRITE in front of it, an RDMA
// has no offset of its own. Honouring it would write at whatever offset happened
// to be current -- an arbitrary sector -- which is silent corruption.
func TestUnsolicitedWriteRDMAIsIgnored(t *testing.T) {
	addr, _, img := startServer(t, false)
	c := dialServer(t, addr)

	pkt := packHeader(CmdWriteRDMA, 6, 0)
	pkt = append(pkt, packBlockType(7, 1)...)
	pkt = append(pkt, make([]byte, SectorSize)...)
	for i := range pkt[6:] {
		pkt[6+i] = 0xCD
	}
	if _, err := c.Write(pkt); err != nil {
		t.Fatal(err)
	}
	got := readSectors(t, c, 6, 600, 2)
	if string(got) != string(img[600*SectorSize:602*SectorSize]) {
		t.Fatal("an unsolicited WRITE_RDMA corrupted the image")
	}
}

// TestTruncatedWriteRDMAIsIgnored: a payload shorter than its own block_type
// would, if written, misalign every following block in the sequence.
func TestTruncatedWriteRDMAIsIgnored(t *testing.T) {
	addr, _, img := startServer(t, false)
	c := dialServer(t, addr)

	if _, err := c.Write(sectorReq(CmdWrite, 5, 400, 1)); err != nil {
		t.Fatal(err)
	}
	pkt := packHeader(CmdWriteRDMA, 5, 0)
	pkt = append(pkt, packBlockType(7, 1)...) // claims 512 bytes
	pkt = append(pkt, make([]byte, 100)...)   // supplies 100
	if _, err := c.Write(pkt); err != nil {
		t.Fatal(err)
	}
	got := readSectors(t, c, 5, 400, 1)
	if string(got) != string(img[400*SectorSize:401*SectorSize]) {
		t.Fatal("a truncated WRITE_RDMA corrupted the image")
	}
}

// TestReadOnlyImageReportsFailure: reporting success would let the console
// believe a save committed when nothing was written.
func TestReadOnlyImageReportsWriteFailure(t *testing.T) {
	addr, path, img := startServer(t, true)
	c := dialServer(t, addr)

	if _, err := c.Write(sectorReq(CmdWrite, 3, 10, 1)); err != nil {
		t.Fatal(err)
	}
	pkt := packHeader(CmdWriteRDMA, 3, 0)
	pkt = append(pkt, packBlockType(7, 1)...)
	pkt = append(pkt, make([]byte, SectorSize)...)
	if _, err := c.Write(pkt); err != nil {
		t.Fatal(err)
	}
	buf := make([]byte, 2048)
	n, err := readPacket(t, c, buf)
	if err != nil {
		t.Fatal(err)
	}
	if result := int32(binary.LittleEndian.Uint32(buf[2:n])); result != -1 {
		t.Fatalf("read-only write reported %d, want -1", result)
	}
	onDisk, _ := os.ReadFile(path)
	if string(onDisk[10*SectorSize:11*SectorSize]) != string(img[10*SectorSize:11*SectorSize]) {
		t.Fatal("a read-only image was modified")
	}
}

// TestOutOfRangeRequestsAreRefused: a count running past the image would make
// the server stream unbounded zero padding on request.
func TestOutOfRangeRequestsAreRefused(t *testing.T) {
	addr, _, img := startServer(t, false)
	c := dialServer(t, addr)
	sectors := uint32(len(img) / SectorSize)

	if _, err := c.Write(sectorReq(CmdRead, 1, sectors-1, 64)); err != nil {
		t.Fatal(err)
	}
	_ = c.SetReadDeadline(time.Now().Add(400 * time.Millisecond))
	buf := make([]byte, 2048)
	if _, err := c.Read(buf); err == nil {
		t.Fatal("an out-of-range READ was answered instead of refused")
	}

	// The server must still be alive and answering afterwards.
	_ = c.SetReadDeadline(time.Now().Add(3 * time.Second))
	if _, err := c.Write(append(packHeader(CmdInfo, 0, 0), make([]byte, 6)...)); err != nil {
		t.Fatal(err)
	}
	if _, err := c.Read(buf); err != nil {
		t.Fatalf("server stopped answering after an out-of-range request: %v", err)
	}
}

// TestMalformedDatagramsDoNotKillTheServer mirrors the Python selftest's fuzz
// pass: a port scan or a broken client must not take the server down.
func TestMalformedDatagramsDoNotKillTheServer(t *testing.T) {
	addr, _, _ := startServer(t, false)
	c := dialServer(t, addr)

	for _, junk := range [][]byte{
		{}, {0x00}, {0xff, 0xff, 0xff},
		append(packHeader(CmdRead, 0, 0), 0x01),
		append(packHeader(0x1F, 7, 255), make([]byte, 32)...),
		append(packHeader(CmdWriteRDMA, 0, 0), 0x00, 0x00),
	} {
		_, _ = c.Write(junk)
	}
	buf := make([]byte, 2048)
	if _, err := c.Write(append(packHeader(CmdInfo, 0, 0), make([]byte, 6)...)); err != nil {
		t.Fatal(err)
	}
	if _, err := readPacket(t, c, buf); err != nil {
		t.Fatalf("server died on malformed input: %v", err)
	}
}
