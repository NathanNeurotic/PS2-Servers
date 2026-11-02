package udpfs

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
)

// Neutrino's UDPFS FILEID backend writes a VMC or an HDD image with BWRITE on
// the server handle its loader opened read-write (udpfs_fhi.c, fhi_write),
// 512-byte sectors at a time, as it reads them with BREAD. Edge refused every
// such BWRITE with EBADF, so a game saving to a VMC through it failed; the
// Desktop server writes them. No --block-device is involved.

// vmcShare is a folder holding a four-sector VMC whose sectors are told apart
// by their fill byte.
func vmcShare(t *testing.T) (root string, vmc []byte) {
	t.Helper()
	root = t.TempDir()
	vmc = make([]byte, 4*512)
	for i := range vmc {
		vmc[i] = byte('0' + i/512)
	}
	if err := os.WriteFile(filepath.Join(root, "vmc0.bin"), vmc, 0o644); err != nil {
		t.Fatal(err)
	}
	return root, vmc
}

func sector(fill byte) []byte { return bytes.Repeat([]byte{fill}, 512) }

// assertVMC fails unless vmc0.bin holds want.
func assertVMC(t *testing.T, root string, want []byte) {
	t.Helper()
	got, err := os.ReadFile(filepath.Join(root, "vmc0.bin"))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, want) {
		t.Fatalf("vmc0.bin is %d bytes, sector fill %q; want %d bytes, %q",
			len(got), fills(got), len(want), fills(want))
	}
}

// fills names each 512-byte sector by its first byte.
func fills(b []byte) string {
	var s []byte
	for i := 0; i < len(b); i += 512 {
		s = append(s, b[i])
	}
	return string(s)
}

func TestBlockWriteOnAFileHandleServesNeutrinoFILEID(t *testing.T) {
	root, vmc := vmcShare(t)
	c := dial(t, startServerAt(t, root, false))
	h := openForWriteOK(t, c, "vmc0.bin", fioRead|fioWrite)

	// One sector, its data inline with the request.
	reply := c.send(append(blockMsg(protocol.BWriteRequest, h, 2, 1), writeDataMsg(0, 1, sector('B'))...))
	if protocol.MessageType(reply[0]) != protocol.WriteDone {
		t.Fatalf("expected WRITE_DONE, got opcode 0x%02x", reply[0])
	}
	if got := result(reply); got != 512 {
		t.Fatalf("BWRITE on a FILEID handle returned %d, want 512", got)
	}
	copy(vmc[2*512:], sector('B'))
	assertVMC(t, root, vmc)

	// Two sectors in two chunks, the way a larger save arrives.
	c.sendAcked(blockMsg(protocol.BWriteRequest, h, 0, 2))
	c.sendAcked(writeDataMsg(0, 2, sector('C')))
	if got := result(c.send(writeDataMsg(1, 2, sector('D')))); got != 1024 {
		t.Fatalf("two-chunk BWRITE on a FILEID handle returned %d, want 1024", got)
	}
	copy(vmc, append(sector('C'), sector('D')...))
	assertVMC(t, root, vmc)

	// The game reads its save back through the same handle.
	back := c.send(blockMsg(protocol.BReadRequest, h, 2, 1))
	if got := result(back); got != 512 || !bytes.Equal(back[8:8+512], sector('B')) {
		t.Fatalf("BREAD after BWRITE returned %d bytes, sector fill %q", got, fills(back[8:]))
	}
}

// Neutrino's loader opens the VMC, and the stage that runs the game takes the
// endpoint over and writes through that handle by number.
func TestBlockWriteOnAFileHandleSurvivesALoaderHandoff(t *testing.T) {
	for _, first := range []uint16{0, 1} {
		t.Run(fmt.Sprintf("first DATA %d", first), func(t *testing.T) {
			root, vmc := vmcShare(t)
			disc := startServerAt(t, root, false)
			c := dial(t, disc)
			h := openForWriteOK(t, c, "vmc0.bin", fioRead|fioWrite)

			replaceLoader(t, c, disc, first)

			reply := c.send(append(blockMsg(protocol.BWriteRequest, h, 3, 1), writeDataMsg(0, 1, sector('S'))...))
			if got := result(reply); got != 512 {
				t.Fatalf("BWRITE on the loader's VMC handle after the handoff returned %d, want 512", got)
			}
			copy(vmc[3*512:], sector('S'))
			assertVMC(t, root, vmc)
		})
	}
}

func TestBlockWriteOnAFileHandleIsRefusedWhenItCannotLand(t *testing.T) {
	root, vmc := vmcShare(t)
	c := dial(t, startServerAt(t, root, false))
	writer := openForWriteOK(t, c, "vmc0.bin", fioRead|fioWrite)
	reader := openForWriteOK(t, c, "vmc0.bin", fioRead)
	dir := result(c.send(openMsg("", fioRead, true)))
	if dir <= 0 {
		t.Fatalf("OPEN of the root folder returned %d", dir)
	}

	for _, req := range []struct {
		what   string
		handle int32
		sector uint64
		want   int32
	}{
		// Access is decided at OPEN, as for WRITE.
		{"a handle opened read-only", reader, 1, -eACCES},
		{"a folder handle", dir, 1, -eBADF},
		{"a handle never opened", 77, 1, -eBADF},
		// sector*512 would overflow the file offset.
		{"a sector no file offset can hold", writer, 1 << 62, -eINVAL},
	} {
		reply := c.send(append(blockMsg(protocol.BWriteRequest, req.handle, req.sector, 1), writeDataMsg(0, 1, sector('X'))...))
		if got := result(reply); got != req.want {
			t.Errorf("BWRITE on %s returned %d, want %d", req.what, got, req.want)
		}
	}
	assertVMC(t, root, vmc)
}

// As for the image, the bytes delivered must be the bytes declared, neither
// more nor fewer.
func TestBlockWriteOnAFileHandleIsHeldToItsDeclaredLength(t *testing.T) {
	root, vmc := vmcShare(t)
	c := dial(t, startServerAt(t, root, false))
	h := openForWriteOK(t, c, "vmc0.bin", fioRead|fioWrite)

	c.sendAcked(blockMsg(protocol.BWriteRequest, h, 3, 1))
	c.sendAcked(writeDataMsg(0, 2, sector('O')))
	if got := result(c.send(writeDataMsg(1, 2, sector('O')))); got != -eINVAL {
		t.Fatalf("a one-sector BWRITE carrying two returned %d, want -EINVAL", got)
	}
	c.sendAcked(blockMsg(protocol.BWriteRequest, h, 2, 2))
	if got := result(c.send(writeDataMsg(0, 1, sector('U')))); got != -eINVAL {
		t.Fatalf("a two-sector BWRITE carrying one returned %d, want -EINVAL", got)
	}
	assertVMC(t, root, vmc)
}
