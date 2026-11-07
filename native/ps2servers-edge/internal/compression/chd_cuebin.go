package compression

// The virtual CD layout follows the behavior described by Okeanos86's
// Neutrino prototype (https://github.com/Okeanos86/neutrino/tree/6aed059a289c000753f40b04ed1ded0f7e503830/pc).
// This implementation uses Edge's existing libchdr backend and filesystem.

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

func stemKey(stem string) string {
	if runtime.GOOS == "windows" {
		return strings.ToLower(stem)
	}
	return stem
}

const (
	cdFrameBytes       = 2448
	cdSectorBytes      = 2352
	maxCDMetadataItems = 4096
	maxCDMetadataBytes = 4096
	maxCDTracks        = 99
	cdTrackPadding     = 4
)

var (
	errNotCDImage       = errors.New("CHD has no supported CD track table")
	errUnsupportedCDCHD = errors.New("CHD CD layout is unsupported")
)

type cdTrack struct {
	number    int64
	kind      string
	frames    int64
	pregap    int64
	pregapTyp string
	postgap   int64
}

func (t cdTrack) audio() bool { return t.kind == "AUDIO" }

func (t cdTrack) storedPregap() bool { return strings.HasPrefix(t.pregapTyp, "V") }

func (t cdTrack) cueMode() string {
	switch t.kind {
	case "MODE1_RAW":
		return "MODE1/2352"
	case "MODE2_RAW":
		return "MODE2/2352"
	default:
		return "AUDIO"
	}
}

type cdTrackTable struct {
	hunkBytes   int64
	logicalSize int64
	tracks      []cdTrack
}

type cdSpan struct {
	start        int64
	frames       int64
	physicalBase int64 // -1 means synthetic silence
	audio        bool
}

type cdTrackIndex struct {
	track  cdTrack
	index0 int64 // -1 means no INDEX 00
	index1 int64
}

type cdLayout struct {
	spans       []cdSpan
	tracks      []cdTrackIndex
	totalFrames int64
}

func (l *cdLayout) spanAt(frame int64) *cdSpan {
	i := sort.Search(len(l.spans), func(i int) bool {
		return l.spans[i].start > frame
	}) - 1
	if i < 0 || frame >= l.spans[i].start+l.spans[i].frames {
		return nil
	}
	return &l.spans[i]
}

func (l *cdLayout) size() int64 { return l.totalFrames * cdSectorBytes }

func (l *cdLayout) cue(binName string) []byte {
	var out strings.Builder
	fmt.Fprintf(&out, "FILE %q BINARY\r\n", binName)
	for _, index := range l.tracks {
		fmt.Fprintf(&out, "  TRACK %02d %s\r\n", index.track.number, index.track.cueMode())
		if index.index0 >= 0 {
			fmt.Fprintf(&out, "    INDEX 00 %s\r\n", msf(index.index0))
		}
		fmt.Fprintf(&out, "    INDEX 01 %s\r\n", msf(index.index1))
	}
	return []byte(out.String())
}

func msf(frame int64) string {
	minutes := frame / (75 * 60)
	remainder := frame % (75 * 60)
	return fmt.Sprintf("%02d:%02d:%02d", minutes, remainder/75, remainder%75)
}

func (table *cdTrackTable) layout() (*cdLayout, error) {
	layout := &cdLayout{}
	physical := int64(0)
	virtual := int64(0)
	if table.logicalSize <= 0 || table.logicalSize%cdFrameBytes != 0 {
		return nil, fmt.Errorf("invalid CD logical size: %w", errUnsupportedCDCHD)
	}
	chdFrames := table.logicalSize / cdFrameBytes
	for _, track := range table.tracks {
		if track.kind != "MODE1_RAW" && track.kind != "MODE2_RAW" && track.kind != "AUDIO" {
			return nil, fmt.Errorf("track %d uses unsupported type %q: %w", track.number, track.kind, errUnsupportedCDCHD)
		}
		if track.frames <= 0 || track.pregap < 0 || track.postgap < 0 || track.pregap > track.frames {
			return nil, fmt.Errorf("track %d has invalid frame counts: %w", track.number, errUnsupportedCDCHD)
		}
		if track.postgap > chdFrames {
			return nil, fmt.Errorf("track %d postgap exceeds the image: %w", track.number, errUnsupportedCDCHD)
		}
		if track.pregap > 0 && track.storedPregap() && track.pregap >= track.frames {
			return nil, fmt.Errorf("track %d stored pregap consumes the track: %w", track.number, errUnsupportedCDCHD)
		}

		index0 := int64(-1)
		index1 := virtual
		if track.pregap > 0 && track.storedPregap() {
			index0 = virtual
			index1 = virtual + track.pregap
		} else if track.pregap > 0 {
			layout.spans = append(layout.spans, cdSpan{start: virtual, frames: track.pregap, physicalBase: -1, audio: track.audio()})
			index0 = virtual
			virtual += track.pregap
			index1 = virtual
		}
		if physical > chdFrames || track.frames > chdFrames-physical {
			return nil, fmt.Errorf("track %d extends beyond the CHD frame range: %w", track.number, errUnsupportedCDCHD)
		}
		layout.spans = append(layout.spans, cdSpan{start: virtual, frames: track.frames, physicalBase: physical, audio: track.audio()})
		layout.tracks = append(layout.tracks, cdTrackIndex{track: track, index0: index0, index1: index1})
		virtual += track.frames
		pad := (cdTrackPadding - (track.frames % cdTrackPadding)) % cdTrackPadding
		physical += track.frames + pad
		if track.postgap > 0 {
			layout.spans = append(layout.spans, cdSpan{start: virtual, frames: track.postgap, physicalBase: -1, audio: track.audio()})
			virtual += track.postgap
		}
	}
	if virtual > math.MaxInt64/cdSectorBytes {
		return nil, fmt.Errorf("virtual BIN is too large: %w", errUnsupportedCDCHD)
	}
	layout.totalFrames = virtual
	return layout, nil
}

func readCDTrackTable(path string) (cdTrackTable, os.FileInfo, error) {
	var table cdTrackTable
	before, err := os.Lstat(path)
	if err != nil {
		return table, nil, err
	}
	if !before.Mode().IsRegular() {
		return table, nil, fmt.Errorf("CHD source is not a regular file")
	}
	f, err := os.Open(path)
	if err != nil {
		return table, nil, err
	}
	defer f.Close()
	fileSize := before.Size()
	if fileSize < chdV5HeaderBytes {
		return table, nil, fmt.Errorf("short CHD header: %w", errUnsupportedCDCHD)
	}
	header := make([]byte, chdV5HeaderBytes)
	if _, err := io.ReadFull(f, header); err != nil {
		return table, nil, err
	}
	if string(header[:8]) != "MComprHD" || binary.BigEndian.Uint32(header[8:12]) != chdV5HeaderBytes || binary.BigEndian.Uint32(header[12:16]) != 5 {
		return table, nil, fmt.Errorf("only CHD v5 is supported: %w", errUnsupportedCDCHD)
	}
	logical := binary.BigEndian.Uint64(header[32:40])
	metaOffset := binary.BigEndian.Uint64(header[48:56])
	hunk := binary.BigEndian.Uint32(header[56:60])
	unit := binary.BigEndian.Uint32(header[60:64])
	if logical == 0 || logical > math.MaxInt64 || logical%cdFrameBytes != 0 {
		return table, nil, fmt.Errorf("invalid CD logical size: %w", errUnsupportedCDCHD)
	}
	if unit != cdFrameBytes || hunk == 0 || hunk > maxCHDHunkBytes || hunk%cdFrameBytes != 0 {
		return table, nil, fmt.Errorf("unsupported CD hunk geometry: %w", errUnsupportedCDCHD)
	}
	table.hunkBytes = int64(hunk)
	table.logicalSize = int64(logical)
	blocks := logical / uint64(hunk)
	if logical%uint64(hunk) != 0 {
		blocks++
	}
	if blocks > math.MaxUint32 {
		return table, nil, fmt.Errorf("CHD has too many hunks: %w", errUnsupportedCDCHD)
	}
	if metaOffset != 0 && (metaOffset < chdV5HeaderBytes || metaOffset > uint64(fileSize)-16) {
		return table, nil, fmt.Errorf("invalid CD metadata offset: %w", errUnsupportedCDCHD)
	}

	visited := make(map[uint64]struct{})
	offset := metaOffset
	for offset != 0 {
		if len(visited) >= maxCDMetadataItems || offset < chdV5HeaderBytes || offset > uint64(fileSize)-16 {
			return table, nil, fmt.Errorf("invalid CHD metadata chain: %w", errUnsupportedCDCHD)
		}
		if _, found := visited[offset]; found {
			return table, nil, fmt.Errorf("cyclic CHD metadata chain: %w", errUnsupportedCDCHD)
		}
		visited[offset] = struct{}{}
		if _, err := f.Seek(int64(offset), io.SeekStart); err != nil {
			return table, nil, err
		}
		metaHeader := make([]byte, 16)
		if _, err := io.ReadFull(f, metaHeader); err != nil {
			return table, nil, err
		}
		tag := string(metaHeader[:4])
		metaLen := int(metaHeader[5])<<16 | int(metaHeader[6])<<8 | int(metaHeader[7])
		next := binary.BigEndian.Uint64(metaHeader[8:16])
		if next != 0 && (next < chdV5HeaderBytes || next > uint64(fileSize)-16) {
			return table, nil, fmt.Errorf("CHD metadata points outside the file: %w", errUnsupportedCDCHD)
		}
		switch tag {
		case "CHCD", "CHGD", "CHGT":
			return table, nil, fmt.Errorf("unsupported CD metadata %s: %w", tag, errUnsupportedCDCHD)
		case "CHT2", "CHTR":
			if metaLen < 1 || metaLen > maxCDMetadataBytes || uint64(offset)+16+uint64(metaLen) > uint64(fileSize) {
				return table, nil, fmt.Errorf("invalid CD track metadata length: %w", errUnsupportedCDCHD)
			}
			data := make([]byte, metaLen)
			if _, err := io.ReadFull(f, data); err != nil {
				return table, nil, err
			}
			track, err := parseCDTrack(data)
			if err != nil {
				return table, nil, err
			}
			table.tracks = append(table.tracks, track)
			if len(table.tracks) > maxCDTracks {
				return table, nil, fmt.Errorf("too many CD tracks: %w", errUnsupportedCDCHD)
			}
		}
		offset = next
	}
	if len(table.tracks) == 0 {
		return table, nil, errNotCDImage
	}
	sort.Slice(table.tracks, func(i, j int) bool { return table.tracks[i].number < table.tracks[j].number })
	for i, track := range table.tracks {
		if track.number != int64(i+1) {
			return table, nil, fmt.Errorf("CD track numbers must be contiguous: %w", errUnsupportedCDCHD)
		}
	}
	if _, err := table.layout(); err != nil {
		return table, nil, err
	}
	after, err := os.Lstat(path)
	if err != nil {
		return table, nil, err
	}
	if !os.SameFile(before, after) || before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
		return table, nil, fmt.Errorf("CHD changed while its track table was read")
	}
	return table, after, nil
}

func parseCDTrack(raw []byte) (cdTrack, error) {
	var track cdTrack
	fields := make(map[string]string)
	for _, item := range strings.Fields(strings.TrimRight(string(raw), "\x00")) {
		key, value, found := strings.Cut(item, ":")
		if found {
			fields[key] = value
		}
	}
	parseNumber := func(key string, optional bool) (int64, error) {
		value, found := fields[key]
		if !found && optional {
			return 0, nil
		}
		n, err := strconv.ParseInt(value, 10, 64)
		if err != nil || n < 0 {
			return 0, fmt.Errorf("invalid %s in CD track metadata: %w", key, errUnsupportedCDCHD)
		}
		return n, nil
	}
	var err error
	if track.number, err = parseNumber("TRACK", false); err != nil {
		return track, err
	}
	if track.frames, err = parseNumber("FRAMES", false); err != nil {
		return track, err
	}
	if track.pregap, err = parseNumber("PREGAP", true); err != nil {
		return track, err
	}
	if track.postgap, err = parseNumber("POSTGAP", true); err != nil {
		return track, err
	}
	track.kind = fields["TYPE"]
	track.pregapTyp = fields["PGTYPE"]
	if track.number < 1 || track.number > maxCDTracks || track.frames <= 0 || track.kind == "" || track.pregap > track.frames {
		return track, fmt.Errorf("invalid CD track metadata: %w", errUnsupportedCDCHD)
	}
	return track, nil
}

// VirtualCDFile describes one generated .cue or .bin view of a CD CHD.
type VirtualCDFile struct {
	Name       string
	Path       string
	SourcePath string
	Kind       string
	Size       int64

	table      cdTrackTable
	layout     *cdLayout
	sourceInfo os.FileInfo
	cueData    []byte
}

// Open returns a read-only, seekable CUE or raw 2352-byte BIN view.
func (v *VirtualCDFile) Open() (io.ReadSeeker, io.Closer, error) {
	current, err := os.Lstat(v.SourcePath)
	if err != nil || !sameImage(v.sourceInfo, current) {
		return nil, nil, fmt.Errorf("CHD changed after its virtual view was prepared")
	}
	if v.Kind == "cue" {
		reader := &cueReader{Reader: *bytes.NewReader(v.cueData)}
		return reader, reader, nil
	}
	return openCHDBIN(v.SourcePath, v.table, v.layout, v.sourceInfo)
}

type cueReader struct{ bytes.Reader }

func (*cueReader) Close() error { return nil }

func sameImage(a, b os.FileInfo) bool {
	return a != nil && b != nil && b.Mode().IsRegular() && os.SameFile(a, b) &&
		a.Size() == b.Size() && a.ModTime().Equal(b.ModTime())
}

func safeCueName(name string) bool {
	if !utf8.ValidString(name) || len(name) > 255 {
		return false
	}
	for _, r := range name {
		if r == '"' || r == '\\' || r < 0x20 || r == 0x7f {
			return false
		}
	}
	return true
}

func makeVirtualCDPair(path string, table cdTrackTable, layout *cdLayout, source os.FileInfo) ([]VirtualCDFile, error) {
	directory := filepath.Dir(path)
	stem := strings.TrimSuffix(filepath.Base(path), filepath.Ext(path))
	binName := stem + ".bin"
	if !safeCueName(binName) || len(stem+".cue") > 255 || len(binName) > 255 {
		return nil, fmt.Errorf("virtual CUE/BIN name is not representable")
	}
	cue := layout.cue(binName)
	return []VirtualCDFile{
		{Name: stem + ".cue", Path: filepath.Join(directory, stem+".cue"), SourcePath: path,
			Kind: "cue", Size: int64(len(cue)), table: table, layout: layout, sourceInfo: source, cueData: cue},
		{Name: binName, Path: filepath.Join(directory, binName), SourcePath: path,
			Kind: "bin", Size: layout.size(), table: table, layout: layout, sourceInfo: source},
	}, nil
}

func ListVirtualCD(directory string, names []string) []VirtualCDFile {
	if !CHDAvailable {
		return nil
	}
	entries, err := os.ReadDir(directory)
	if err != nil {
		return nil
	}
	realNames := make(map[string]struct{}, len(entries)+len(names))
	for _, entry := range entries {
		realNames[strings.ToLower(entry.Name())] = struct{}{}
	}
	for _, name := range names {
		realNames[strings.ToLower(name)] = struct{}{}
	}
	sources := make(map[string]string)
	ambiguous := make(map[string]struct{})
	for _, entry := range entries {
		stem, extension := strings.TrimSuffix(entry.Name(), filepath.Ext(entry.Name())), strings.ToLower(filepath.Ext(entry.Name()))
		if extension != ".chd" {
			continue
		}
		key := stemKey(stem)
		if _, exists := sources[key]; exists {
			ambiguous[key] = struct{}{}
		} else {
			sources[key] = filepath.Join(directory, entry.Name())
		}
	}
	var out []VirtualCDFile
	keys := make([]string, 0, len(sources))
	for key := range sources {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		path := sources[key]
		if _, found := ambiguous[key]; found {
			continue
		}
		stem := strings.TrimSuffix(filepath.Base(path), filepath.Ext(path))
		if !safeCueName(stem + ".bin") {
			continue
		}
		if _, cueExists := realNames[strings.ToLower(stem+".cue")]; cueExists {
			continue
		}
		if _, binExists := realNames[strings.ToLower(stem+".bin")]; binExists {
			continue
		}
		info, err := os.Lstat(path)
		if err != nil || !info.Mode().IsRegular() {
			continue
		}
		table, source, err := readCDTrackTable(path)
		if err != nil {
			continue
		}
		layout, err := table.layout()
		if err != nil {
			continue
		}
		pair, err := makeVirtualCDPair(path, table, layout, source)
		if err == nil {
			out = append(out, pair...)
		}
	}
	return out
}

func FindVirtualCD(path string) (*VirtualCDFile, error) {
	if !CHDAvailable {
		return nil, nil
	}
	directory := filepath.Dir(path)
	base := filepath.Base(path)
	stem := strings.TrimSuffix(base, filepath.Ext(base))
	extension := strings.ToLower(filepath.Ext(base))
	kind := ""
	if extension == ".cue" {
		kind = "cue"
	} else if extension == ".bin" {
		kind = "bin"
	} else {
		return nil, nil
	}
	if !safeCueName(stem + ".bin") {
		return nil, nil
	}
	entries, err := os.ReadDir(directory)
	if err != nil {
		return nil, err
	}
	wantedCue, wantedBin := stemKey(stem+".cue"), stemKey(stem+".bin")
	var candidates []string
	key := stemKey(stem)
	for _, entry := range entries {
		name := entry.Name()
		k := stemKey(name)
		if k == wantedCue || k == wantedBin {
			return nil, nil
		}
		candidateStem := strings.TrimSuffix(name, filepath.Ext(name))
		if stemKey(candidateStem) == key && strings.EqualFold(filepath.Ext(name), ".chd") {
			candidates = append(candidates, filepath.Join(directory, name))
		}
	}
	if len(candidates) != 1 {
		return nil, nil
	}
	info, err := os.Lstat(candidates[0])
	if err != nil || !info.Mode().IsRegular() {
		return nil, nil
	}
	table, source, err := readCDTrackTable(candidates[0])
	if err != nil {
		return nil, nil
	}
	layout, err := table.layout()
	if err != nil {
		return nil, nil
	}
	pair, err := makeVirtualCDPair(candidates[0], table, layout, source)
	if err != nil {
		return nil, nil
	}
	if kind == "cue" {
		return &pair[0], nil
	}
	return &pair[1], nil
}

var _ io.ReadSeeker = (*cueReader)(nil)
var _ io.Closer = (*cueReader)(nil)
