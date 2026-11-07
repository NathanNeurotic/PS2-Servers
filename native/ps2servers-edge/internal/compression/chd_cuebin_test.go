package compression

import (
	"encoding/binary"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeSyntheticCDCHD(t *testing.T, tracks ...string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "disc.chd")
	header := make([]byte, chdV5HeaderBytes)
	copy(header[:8], "MComprHD")
	binary.BigEndian.PutUint32(header[8:12], chdV5HeaderBytes)
	binary.BigEndian.PutUint32(header[12:16], 5)
	logicalFrames := uint64(8)
	binary.BigEndian.PutUint64(header[32:40], logicalFrames*cdFrameBytes)
	binary.BigEndian.PutUint64(header[48:56], chdV5HeaderBytes)
	binary.BigEndian.PutUint32(header[56:60], 4*cdFrameBytes)
	binary.BigEndian.PutUint32(header[60:64], cdFrameBytes)

	data := append([]byte(nil), header...)
	for i, track := range tracks {
		next := uint64(0)
		metaLen := len(track)
		if i+1 < len(tracks) {
			next = uint64(len(data) + 16 + metaLen)
		}
		metadata := make([]byte, 16)
		copy(metadata[:4], "CHT2")
		metadata[5] = byte(metaLen >> 16)
		metadata[6] = byte(metaLen >> 8)
		metadata[7] = byte(metaLen)
		binary.BigEndian.PutUint64(metadata[8:16], next)
		data = append(data, metadata...)
		data = append(data, track...)
	}
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestCDTrackTableGeneratesVirtualLayoutAndCue(t *testing.T) {
	path := writeSyntheticCDCHD(t,
		"TRACK:1 TYPE:MODE1_RAW SUBTYPE:NONE FRAMES:3 PREGAP:2 PGTYPE:VAUDIO PGSUB:NONE POSTGAP:1\x00",
		"TRACK:2 TYPE:AUDIO SUBTYPE:NONE FRAMES:3 PREGAP:2 PGTYPE:MODE1 PGSUB:NONE POSTGAP:0\x00",
	)
	table, _, err := readCDTrackTable(path)
	if err != nil {
		t.Fatal(err)
	}
	layout, err := table.layout()
	if err != nil {
		t.Fatal(err)
	}
	if got, want := layout.size(), int64(9*cdSectorBytes); got != want {
		t.Fatalf("virtual BIN size = %d, want %d", got, want)
	}
	if span := layout.spanAt(3); span == nil || span.physicalBase != -1 {
		t.Fatalf("postgap span = %+v, want synthetic silence", span)
	}
	if span := layout.spanAt(4); span == nil || span.physicalBase != -1 {
		t.Fatalf("non-stored pregap span = %+v, want synthetic silence", span)
	}
	if span := layout.spanAt(6); span == nil || span.physicalBase != 4 {
		t.Fatalf("second track span = %+v, want CHD frame 4", span)
	}
	got := string(layout.cue("disc.bin"))
	want := "FILE \"disc.bin\" BINARY\r\n" +
		"  TRACK 01 MODE1/2352\r\n" +
		"    INDEX 00 00:00:00\r\n" +
		"    INDEX 01 00:00:02\r\n" +
		"  TRACK 02 AUDIO\r\n" +
		"    INDEX 00 00:00:04\r\n" +
		"    INDEX 01 00:00:06\r\n"
	if got != want {
		t.Fatalf("CUE mismatch\n got: %q\nwant: %q", got, want)
	}
}

func TestCDTrackTableRejectsUnsupportedAndMalformedMetadata(t *testing.T) {
	tests := []struct {
		name  string
		track string
	}{
		{name: "cooked track", track: "TRACK:1 TYPE:MODE1 SUBTYPE:NONE FRAMES:4\x00"},
		{name: "missing frame count", track: "TRACK:1 TYPE:AUDIO SUBTYPE:NONE\x00"},
		{name: "unknown type", track: "TRACK:1 TYPE:VIDEO SUBTYPE:NONE FRAMES:4\x00"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			path := writeSyntheticCDCHD(t, test.track)
			table, _, err := readCDTrackTable(path)
			if err == nil {
				if _, err = table.layout(); err == nil {
					t.Fatal("unsupported metadata was accepted")
				}
			}
		})
	}

	path := writeSyntheticCDCHD(t, "TRACK:1 TYPE:AUDIO SUBTYPE:NONE FRAMES:4\x00")
	image, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	binary.BigEndian.PutUint64(image[48:56], uint64(len(image)+100))
	if err := os.WriteFile(path, image, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, _, err := readCDTrackTable(path); err == nil || !strings.Contains(err.Error(), "metadata") {
		t.Fatalf("invalid metadata offset error = %v", err)
	}
}

func TestSafeCueNameRejectsInvalidAndOverlongUTF8(t *testing.T) {
	if safeCueName(string([]byte{0xff}) + ".bin") {
		t.Fatal("invalid UTF-8 name was accepted")
	}
	if safeCueName(strings.Repeat("x", 252) + ".bin") {
		t.Fatal("overlong filename was accepted")
	}
}
