package compression

import "testing"

func TestLiteralOnlyLZ4(t *testing.T) {
	src := append([]byte{0xF0, 5}, []byte("abcdefghijklmnopqrst")...)
	out, err := decodeLZ4Block(src, 20)
	if err != nil {
		t.Fatal(err)
	}
	if string(out) != "abcdefghijklmnopqrst" {
		t.Fatalf("%q", out)
	}
}
func TestLZ4RejectsBadOffset(t *testing.T) {
	if _, err := decodeLZ4Block([]byte{0, 0, 0}, 4); err == nil {
		t.Fatal("expected error")
	}
}
