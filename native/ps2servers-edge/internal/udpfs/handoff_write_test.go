package udpfs

import (
	"bytes"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"syscall"
	"testing"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
)

// sendAcked transmits a request that draws no reply, such as a write chunk
// that is not the last, and waits for the server's ACK of it. DATA and
// DISCOVERY arrive on different sockets, so without the wait a DISCOVERY sent
// next could be handled first.
func (c *client) sendAcked(msg []byte) {
	c.t.Helper()
	seq := c.seq
	c.sendNoReply(msg)
	for {
		p, _ := recvPacket(c.t, c.conn)
		_, dh, payload, err := protocol.ParseDataPacket(p)
		if err == nil && len(payload) == 0 && dh.Flags&protocol.FlagACK != 0 && dh.AckSequence == seq {
			return
		}
	}
}

// replaceLoader plays a new loader taking over c's UDP endpoint while the old
// loader's stream is live: a sequence-0 DISCOVERY, then DATA that starts again
// at first -- 0 for a Standard client, 1 for a Modulo one.
func replaceLoader(t *testing.T, c *client, disc *net.UDPAddr, first uint16) {
	t.Helper()
	if _, err := c.conn.WriteToUDP(discoveryPacket(0), disc); err != nil {
		t.Fatal(err)
	}
	// Skip anything still queued for the old loader, such as the ACK of an
	// unfinished write, up to the INFORM.
	for {
		p, _ := recvPacket(t, c.conn)
		if h, err := protocol.ParseHeader(p); err == nil && h.Type == protocol.Inform {
			break
		}
	}
	c.seq = first
}

// A save the old loader was writing when a new loader replaced it on the same
// endpoint. The handoff keeps the old loader's handles, because Neutrino's
// FILEID backend carries an open one across the switch, so the old write
// handle kept its single-writer reservation: the new loader's OPEN of the same
// file for writing was refused with EBUSY for as long as the session stayed in
// use, and every retry of the save failed.
func TestReplacementLoaderCanRewriteAFileTheOldOneHadOpen(t *testing.T) {
	for _, first := range []uint16{0, 1} {
		t.Run(fmt.Sprintf("first DATA %d", first), func(t *testing.T) {
			root := t.TempDir()
			disc := startServerAt(t, root, false)
			c := dial(t, disc)

			old := openForWriteOK(t, c, "save.bin", fioRead|fioWrite|fioCreate|fioTrunc)
			if got := result(c.send(writeReqMsg(old, 12, writeDataMsg(0, 1, []byte("OLD SAVE OLD"))))); got != 12 {
				t.Fatalf("WRITE before the handoff returned %d, want 12", got)
			}
			// Cut off halfway through its next write: one chunk of two.
			c.sendAcked(writeReqMsg(old, 8, writeDataMsg(0, 2, []byte("HALF"))))

			replaceLoader(t, c, disc, first)

			h := openForWriteOK(t, c, "save.bin", fioRead|fioWrite|fioCreate|fioTrunc)
			if h == old {
				t.Fatalf("the new OPEN was given the old loader's handle %d", old)
			}
			if got := result(c.send(writeReqMsg(h, 8, writeDataMsg(0, 1, []byte("NEW SAVE"))))); got != 8 {
				t.Fatalf("WRITE after the handoff returned %d, want 8", got)
			}
			if got := result(c.send(closeMsg(h))); got != 0 {
				t.Fatalf("CLOSE returned %d, want 0", got)
			}
			// The file in the folder holds the new save alone: O_TRUNC dropped
			// the old one, and the unfinished write never landed.
			if data, err := os.ReadFile(filepath.Join(root, "save.bin")); err != nil || string(data) != "NEW SAVE" {
				t.Fatalf("save.bin holds %q %v, want %q", data, err, "NEW SAVE")
			}
			// The old handle went with the takeover, so nothing written
			// through it late can land on top of the new save.
			if got := result(c.send(writeReqMsg(old, 4, writeDataMsg(0, 1, []byte("LATE"))))); got != -int32(syscall.EBADF) {
				t.Fatalf("WRITE on the old loader's handle returned %d, want -EBADF", got)
			}
		})
	}
}

// The takeover is narrow. It closes only the old loader's write handle on the
// file being reopened: what else the old loader had open stays usable by
// number, as Neutrino's FILEID backend needs, and other consoles are kept off
// the file throughout.
func TestTakingOverASaveKeepsTheOldLoadersOtherHandles(t *testing.T) {
	root := t.TempDir()
	game := bytes.Repeat([]byte{0x5a}, 1024)
	card := bytes.Repeat([]byte{0xa5}, 1024)
	if err := os.WriteFile(filepath.Join(root, "game.iso"), game, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "card.vmc"), card, 0o644); err != nil {
		t.Fatal(err)
	}
	disc := startServerAt(t, root, false)
	c := dial(t, disc)
	other := dial(t, disc)

	iso := result(c.send(openMsg("game.iso", fioRead, false)))
	if iso <= 0 {
		t.Fatalf("OPEN game.iso returned %d", iso)
	}
	vmc := openForWriteOK(t, c, "card.vmc", fioRead|fioWrite)
	save := openForWriteOK(t, c, "save.bin", fioRead|fioWrite|fioCreate|fioTrunc)

	replaceLoader(t, c, disc, 0)

	busy := -int32(syscall.EBUSY)
	if got := result(other.send(openMsg("save.bin", fioRead|fioWrite, false))); got != busy {
		t.Fatalf("another console's write OPEN of the old loader's save returned %d, want -EBUSY", got)
	}
	h := openForWriteOK(t, c, "save.bin", fioRead|fioWrite|fioCreate|fioTrunc)
	if got := result(other.send(openMsg("save.bin", fioRead|fioWrite, false))); got != busy {
		t.Fatalf("another console's write OPEN after the takeover returned %d, want -EBUSY", got)
	}
	// Only a replaced loader's handle is taken over: the new loader holding the
	// file is one writer, as before.
	if got := result(c.send(openMsg("save.bin", fioRead|fioWrite, false))); got != busy {
		t.Fatalf("a second write OPEN by the new loader returned %d, want -EBUSY", got)
	}
	if got := result(c.send(writeReqMsg(save, 4, writeDataMsg(0, 1, []byte("LATE"))))); got != -int32(syscall.EBADF) {
		t.Fatalf("WRITE on the taken-over handle returned %d, want -EBADF", got)
	}

	for _, f := range []struct {
		name   string
		handle int32
		data   []byte
	}{{"game.iso", iso, game}, {"card.vmc", vmc, card}} {
		payload := c.send(blockMsg(protocol.BReadRequest, f.handle, 1, 1))
		if got := result(payload); got != 512 || len(payload) < 8+512 || !bytes.Equal(payload[8:8+512], f.data[512:]) {
			t.Fatalf("BREAD of %s by the old loader's handle %d returned %d", f.name, f.handle, got)
		}
	}
	if got := result(other.send(openMsg("card.vmc", fioRead|fioWrite, false))); got != busy {
		t.Fatalf("another console's write OPEN of the old loader's memory card returned %d, want -EBUSY", got)
	}
	if got := result(c.send(closeMsg(h))); got != 0 {
		t.Fatalf("CLOSE returned %d, want 0", got)
	}
}
