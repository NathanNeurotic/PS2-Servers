package udpfs

import (
	"bytes"
	"context"
	"encoding/binary"
	"os"
	"path/filepath"
	"testing"
	"time"

	edgelog "github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
)

func TestEmptyGetStatReportsBlockCapacity(t *testing.T) {
	disc, _, img := startBlockServer(t, Config{})
	c := dial(t, disc)
	reply := c.send(pathMsg(protocol.GetStatRequest, "", 0))
	if got := result(reply); got != 0 {
		t.Fatalf("empty GETSTAT returned %d", got)
	}
	size := uint64(binary.LittleEndian.Uint32(reply[16:20])) | uint64(binary.LittleEndian.Uint32(reply[20:24]))<<32
	if size != uint64(len(img)) {
		t.Fatalf("empty GETSTAT size = %d, want block capacity %d", size, len(img))
	}
	if mode := binary.LittleEndian.Uint32(reply[8:12]); mode != 0 {
		t.Fatalf("empty GETSTAT mode = %#x, want 0", mode)
	}
}

func TestBlockDeviceOnlyServer(t *testing.T) {
	img := bytes.Repeat([]byte{0x5a}, 64*512+17)
	imgPath := filepath.Join(t.TempDir(), "ps2.img")
	if err := os.WriteFile(imgPath, img, 0o644); err != nil {
		t.Fatal(err)
	}
	server, err := New(Config{
		Bind: "127.0.0.1", Port: freeUDPPort(t), BlockDevice: imgPath,
		PeerTimeout: time.Minute, Log: edgelog.New(discardWriter{}, "text", true, false),
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
	defer func() {
		cancel()
		select {
		case <-done:
		case <-time.After(2 * time.Second):
			t.Error("server did not stop")
		}
	}()

	c := dial(t, disc)
	stat := c.send(pathMsg(protocol.GetStatRequest, "", 0))
	if got := result(stat); got != 0 {
		t.Fatalf("block-only empty GETSTAT returned %d", got)
	}
	size := uint64(binary.LittleEndian.Uint32(stat[16:20])) | uint64(binary.LittleEndian.Uint32(stat[20:24]))<<32
	if want := uint64(64 * 512); size != want {
		t.Fatalf("block-only capacity = %d, want sector-aligned capacity %d", size, want)
	}
	read := c.send(blockMsg(protocol.BReadRequest, 0, 0, 1))
	if got := result(read); got != 512 || !bytes.Equal(read[8:8+512], img[:512]) {
		t.Fatalf("block-only BREAD returned %d or wrong bytes", got)
	}
	payload := bytes.Repeat([]byte{0xa5}, 512)
	write := c.send(append(blockMsg(protocol.BWriteRequest, 0, 1, 1), writeDataMsg(0, 1, payload)...))
	if got := result(write); got != 512 {
		t.Fatalf("block-only BWRITE returned %d", got)
	}
	onDisk, err := os.ReadFile(imgPath)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(onDisk[512:1024], payload) {
		t.Fatal("block-only BWRITE did not land")
	}
	if got := result(c.send(closeMsg(0))); got != 0 {
		t.Fatalf("CLOSE block handle 0 returned %d", got)
	}
	if got := result(c.send(openMsg("missing.iso", fioRead, false))); got != -eNOENT {
		t.Errorf("filesystem OPEN without a root returned %d, want -ENOENT", got)
	}
	if got := result(c.send(openMsg("", fioRead, true))); got != -eNOENT {
		t.Errorf("filesystem DOPEN without a root returned %d, want -ENOENT", got)
	}
	if got := result(c.send(pathMsg(protocol.GetStatRequest, "missing.iso", 0))); got != -eNOENT {
		t.Errorf("filesystem GETSTAT without a root returned %d, want -ENOENT", got)
	}
	if got := c.pathOp(protocol.MkdirRequest, "NEW"); got != -eACCES {
		t.Errorf("filesystem MKDIR without a root returned %d, want -EACCES", got)
	}
}

func TestServerRequiresSomeStorage(t *testing.T) {
	if server, err := New(Config{Log: edgelog.New(discardWriter{}, "text", true, false)}); err == nil {
		server.Close()
		t.Fatal("New accepted neither a filesystem root nor a block device")
	}
}
