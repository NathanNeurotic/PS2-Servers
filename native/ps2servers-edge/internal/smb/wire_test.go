package smb

import (
	"bytes"
	"encoding/hex"
	"errors"
	"io"
	"testing"
)

// The framing and header are the base every command sits on, so an error here
// is not a broken command -- it is a connection that desynchronises and a
// console that hangs partway through a game load. They are pinned to exact
// bytes rather than round-tripped, because a round trip only proves this
// implementation agrees with itself, and the thing it has to agree with is the
// Python server that consoles were actually tested against.

func TestFramingIsBigEndianLengthOnly(t *testing.T) {
	var buf bytes.Buffer
	if err := WriteMessage(&buf, []byte{0xAA, 0xBB, 0xCC}); err != nil {
		t.Fatal(err)
	}
	// 0x00 session message, then a 24-bit BIG-endian length. This is the only
	// big-endian field in the protocol; little-endian here would announce
	// 0x030000 bytes and hang the peer waiting for them.
	want := "00000003aabbcc"
	if got := hex.EncodeToString(buf.Bytes()); got != want {
		t.Fatalf("framing = %s, want %s", got, want)
	}
}

func TestReadMessageRoundTrip(t *testing.T) {
	var buf bytes.Buffer
	payload := bytes.Repeat([]byte{0x5A}, 300)
	if err := WriteMessage(&buf, payload); err != nil {
		t.Fatal(err)
	}
	got, err := ReadMessage(&buf)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, payload) {
		t.Fatal("payload did not survive the round trip")
	}
}

func TestKeepAliveIsNotEOF(t *testing.T) {
	// OPL holds one connection open across a whole game load and sends
	// keep-alives. Reporting those as EOF drops the console mid-load; reporting
	// them as a zero-length message makes the dispatcher parse a header that
	// is not there.
	for name, framed := range map[string][]byte{
		"zero-length session message": {0x00, 0x00, 0x00, 0x00},
		"session keep-alive type":     {0x85, 0x00, 0x00, 0x00},
	} {
		t.Run(name, func(t *testing.T) {
			_, err := ReadMessage(bytes.NewReader(framed))
			if !errors.Is(err, ErrKeepAlive) {
				t.Fatalf("got %v, want ErrKeepAlive", err)
			}
		})
	}
}

func TestNonSessionBodyIsConsumed(t *testing.T) {
	// A non-session message with a body must have that body drained, or every
	// subsequent read is offset by its length and the connection is lost.
	stream := []byte{0x81, 0x00, 0x00, 0x04, 'j', 'u', 'n', 'k'}
	var full bytes.Buffer
	full.Write(stream)
	if err := WriteMessage(&full, []byte{0x11, 0x22}); err != nil {
		t.Fatal(err)
	}

	r := bytes.NewReader(full.Bytes())
	if _, err := ReadMessage(r); !errors.Is(err, ErrKeepAlive) {
		t.Fatalf("first read: got %v, want ErrKeepAlive", err)
	}
	got, err := ReadMessage(r)
	if err != nil {
		t.Fatalf("second read failed, so the junk body was not consumed: %v", err)
	}
	if !bytes.Equal(got, []byte{0x11, 0x22}) {
		t.Fatalf("second message = %x, want 1122", got)
	}
}

func TestOversizeIsRefusedBeforeAllocating(t *testing.T) {
	// The length field is 24 bits, so a peer can announce 16 MiB. On a 32 MB
	// router that allocation is the machine. Refused on the length, before any
	// buffer exists -- the same rule as the UDPFS transfer caps.
	_, err := ReadMessage(bytes.NewReader([]byte{0x00, 0xFF, 0xFF, 0xFF}))
	if !errors.Is(err, ErrOversize) {
		t.Fatalf("got %v, want ErrOversize", err)
	}
}

func TestTruncatedMessageIsAnError(t *testing.T) {
	_, err := ReadMessage(bytes.NewReader([]byte{0x00, 0x00, 0x00, 0x10, 0x01, 0x02}))
	if err == nil || errors.Is(err, ErrKeepAlive) {
		t.Fatalf("got %v, want a read error", err)
	}
	if _, err := ReadMessage(bytes.NewReader(nil)); !errors.Is(err, io.EOF) {
		t.Fatalf("empty stream: got %v, want EOF", err)
	}
}

func TestHeaderBytesMatchThePythonServer(t *testing.T) {
	// Byte-for-byte against what smbv1_server/smbserver_opl.py emits. The
	// status split is the part worth pinning: the NTSTATUS is NOT written as a
	// little-endian uint32 at offset 5. ErrorClass at byte 5 gets the low byte
	// and ErrorCode at bytes 7-8 gets the HIGH half, with byte 6 reserved.
	// Writing the obvious uint32 puts different bytes on the wire.
	got := PackHeader(ComNegotiate, StatusObjectNameNotFound, 0x0102, 0x0304, 0x0506, 0x0708)
	want, err := hex.DecodeString(
		"ff534d42" + // magic
			"72" + // command
			"34" + // ErrorClass = status & 0xff
			"00" + // reserved
			"00c0" + // ErrorCode = status >> 16, little-endian
			"80" + // Flags1 = SERVER_TO_REDIR
			"0140" + // Flags2 = 0x4001 little-endian
			"000000000000000000000000" + // security features / reserved
			"0201" + "0403" + "0605" + "0807") // tid pid uid mid
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, want) {
		t.Fatalf("header mismatch\n got %x\nwant %x", got, want)
	}
	if len(got) != HeaderLen {
		t.Fatalf("header is %d bytes, want %d", len(got), HeaderLen)
	}
}

func TestSuccessHeaderHasZeroStatusFields(t *testing.T) {
	got := PackHeader(ComEcho, StatusSuccess, 1, 2, 3, 4)
	if got[5] != 0 || got[6] != 0 || got[7] != 0 || got[8] != 0 {
		t.Fatalf("status bytes not zero on success: %x", got[5:9])
	}
}

func TestParseHeaderReassemblesTheSplitStatus(t *testing.T) {
	packed := PackHeader(ComReadAndX, StatusAccessDenied, 9, 8, 7, 6)
	h, err := ParseHeader(packed)
	if err != nil {
		t.Fatal(err)
	}
	if h.Status != StatusAccessDenied {
		t.Fatalf("status = %#x, want %#x", h.Status, StatusAccessDenied)
	}
	if h.Command != ComReadAndX || h.TID != 9 || h.PID != 8 || h.UID != 7 || h.MID != 6 {
		t.Fatalf("header fields did not survive: %+v", h)
	}
}

func TestParseHeaderRejectsRubbish(t *testing.T) {
	if _, err := ParseHeader(make([]byte, 31)); err == nil {
		t.Fatal("accepted a short header")
	}
	bad := PackHeader(ComEcho, 0, 0, 0, 0, 0)
	bad[1] = 'X'
	if _, err := ParseHeader(bad); err == nil {
		t.Fatal("accepted a header without the SMB magic")
	}
}
