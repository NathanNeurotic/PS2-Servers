package protocol

import "testing"

func TestHeaderWrap(t *testing.T) {
	for _, seq := range []uint16{0, 1, 4095} {
		h := Header{Type: Data, Sequence: seq}
		got, err := ParseHeader(h.Marshal())
		if err != nil || got != h {
			t.Fatalf("roundtrip %#v %#v %v", h, got, err)
		}
	}
	if Next(4095) != 0 || Previous(0) != 4095 {
		t.Fatal("sequence wrap failed")
	}
}
func TestRejectTruncated(t *testing.T) {
	if _, _, _, err := ParseDataPacket([]byte{2, 0, 0}); err == nil {
		t.Fatal("expected error")
	}
}

func FuzzParseDataPacket(f *testing.F) {
	f.Add([]byte{})
	f.Add([]byte{0x02, 0x00, 0, 0, 0, 0})
	f.Add(append(Header{Type: Data, Sequence: 4095}.Marshal(), DataHeader{AckSequence: 4095, Flags: FlagACK}.Marshal()...))
	f.Fuzz(func(t *testing.T, packet []byte) {
		_, _, _, _ = ParseDataPacket(packet)
	})
}
