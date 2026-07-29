package smb

import (
	"encoding/binary"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// asciiz reads a NUL-terminated ASCII string from the front of b.
//
// Every string in this protocol is single-byte OEM/ASCII: the reply Flags2
// deliberately omits the Unicode bit (0x8000), so there is no UTF-16 path
// anywhere in this server and none is needed.
func asciiz(b []byte) string {
	if i := indexZero(b); i >= 0 {
		return string(b[:i])
	}
	return string(b)
}

func indexZero(b []byte) int {
	for i, v := range b {
		if v == 0 {
			return i
		}
	}
	return -1
}

func u16(b []byte, off int) uint16 {
	if off < 0 || off+2 > len(b) {
		return 0
	}
	return binary.LittleEndian.Uint16(b[off:])
}

func u32(b []byte, off int) uint32 {
	if off < 0 || off+4 > len(b) {
		return 0
	}
	return binary.LittleEndian.Uint32(b[off:])
}

// shareForTID returns the share a tree id refers to, or nil for IPC$ and for
// an unknown tree.
func (c *conn) shareForTID(tid uint16) *Share {
	return c.trees[tid]
}

// resolve maps an SMB path under a share, refusing anything that escapes.
//
// filesystem.Root is the same guard UDPFS uses -- realpath-based, so a symlink
// out of the share is refused. The reference resolves with abspath, which
// normalises ".." textually but never follows a link, so its declared root is
// not quite its real boundary. This is the one place the port deliberately
// improves on it rather than copying it.
func (s *Share) resolve(smbPath string, allowMissing bool) (string, bool) {
	p := strings.ReplaceAll(smbPath, "\\", "/")
	p = strings.TrimLeft(p, "/")
	local, err := s.Root.Resolve(p, allowMissing)
	if err != nil {
		return "", false
	}
	return local, true
}

func (c *conn) negotiate(r *req) reply {
	// OPL sends dialect strings as format byte 0x02 followed by an ASCII
	// z-string. Only "NT LM 0.12" is ever offered, but its index is echoed
	// rather than assumed.
	idx := 0
	d := r.data
	for i, n := 0, 0; i < len(d); n++ {
		if d[i] != 0x02 {
			i++
			continue
		}
		rest := d[i+1:]
		end := indexZero(rest)
		if end < 0 {
			end = len(rest)
		}
		if string(rest[:end]) == "NT LM 0.12" {
			idx = n
			break
		}
		i += 1 + end + 1
	}

	// 17 words / 34 bytes exactly. WordCount MUST be 17 or OPL retries
	// forever -- it is matched on the struct size, not parsed leniently.
	p := make([]byte, 34)
	binary.LittleEndian.PutUint16(p[0:], uint16(idx)) // DialectIndex
	p[2] = 0x00                                       // SecurityMode: share-level + plaintext => guest, no challenge
	binary.LittleEndian.PutUint16(p[3:], 1)           // MaxMpxCount
	binary.LittleEndian.PutUint16(p[5:], 1)           // MaxNumberVcs
	binary.LittleEndian.PutUint32(p[7:], 65535)       // MaxBufferSize, >= OPL's 8192
	binary.LittleEndian.PutUint32(p[11:], 65536)      // MaxRawSize
	binary.LittleEndian.PutUint32(p[15:], 0)          // SessionKey, echoed back by OPL
	binary.LittleEndian.PutUint32(p[19:], serverCaps) // Capabilities, unicode OFF
	binary.LittleEndian.PutUint64(p[23:], 0)          // SystemTime; zero is accepted
	binary.LittleEndian.PutUint16(p[31:], 0)          // ServerTimeZone
	p[33] = 0                                         // EncryptionKeyLength = 0 => plaintext/guest

	return reply{params: p, data: []byte("WORKGROUP\x00")}
}

func (c *conn) sessionSetup(r *req) reply {
	// The guest logon is accepted unconditionally -- there is no password
	// path at all, which is the whole reason this server exists alongside a
	// host SMB stack that refuses OPL's ancient client.
	if c.uid == 0 {
		c.uid = 0x0800
	}
	p := make([]byte, 6)
	p[0] = ComNone
	p[1] = 0
	binary.LittleEndian.PutUint16(p[2:], 0)      // AndXOffset
	binary.LittleEndian.PutUint16(p[4:], 0x0001) // Action = logged in as guest
	uid := c.uid
	return reply{
		params: p,
		data:   []byte("OPLSMB\x00OPLSMB\x00"), // NativeOS, NativeLanMan; OPL ignores both
		uid:    &uid,
	}
}

func (c *conn) treeConnect(r *req) reply {
	// WordCount=4: AndXCommand, AndXReserved, AndXOffset, Flags, PasswordLength
	pwlen := 1
	if r.wordCount >= 4 {
		pwlen = int(u16(r.params, 6))
	}
	d := r.data
	if pwlen > len(d) {
		pwlen = len(d)
	}
	path := asciiz(d[pwlen:])
	// "\\SERVER\share" -> the last component
	path = strings.ReplaceAll(path, "/", "\\")
	path = strings.TrimRight(path, "\\")
	parts := strings.Split(path, "\\")
	name := parts[len(parts)-1]

	tid := c.allocTID()
	var service []byte
	if strings.EqualFold(name, "IPC$") {
		c.ipc[tid] = true
		service = []byte("IPC\x00")
	} else {
		sh, ok := c.server.shares[strings.ToLower(name)]
		if !ok {
			// Lenient by design: with exactly one share configured, serve it
			// whatever the client called it. OPL sends odd names, and failing
			// here looks to a user like the server is down.
			if len(c.server.shares) == 1 {
				for _, only := range c.server.shares {
					sh, ok = only, true
				}
			}
		}
		if !ok {
			return fail(StatusObjectNameNotFound)
		}
		share := sh
		c.trees[tid] = &share
		service = []byte("A:\x00")
	}
	c.server.cfg.Log.Debug("SMB tree connect", map[string]any{"share": name, "tid": tid})

	p := make([]byte, 6)
	p[0] = ComNone
	p[1] = 0
	binary.LittleEndian.PutUint16(p[2:], 0)
	binary.LittleEndian.PutUint16(p[4:], 0x0001) // OptionalSupport
	out := tid
	return reply{params: p, data: append(service, []byte("NTFS\x00")...), tid: &out}
}

func (c *conn) openAndX(r *req) reply {
	sh := c.shareForTID(r.tid)
	if sh == nil {
		return fail(StatusObjectNameNotFound)
	}
	name := asciiz(r.data)
	local, ok := sh.resolve(name, false)
	if !ok {
		return fail(StatusObjectNameNotFound)
	}
	info, err := os.Stat(local)
	if err != nil || info.IsDir() {
		c.server.cfg.Log.Debug("SMB open miss", map[string]any{"name": name})
		return fail(StatusObjectNameNotFound)
	}
	fh, err := os.Open(local)
	if err != nil {
		return fail(StatusObjectNameNotFound)
	}
	fid := c.allocFID()
	c.files[fid] = &openFile{path: local, fh: fh, size: info.Size()}
	c.server.cfg.Log.Debug("SMB open", map[string]any{
		"name": name, "fid": fid, "size": info.Size()})

	p := make([]byte, 30)
	p[0] = ComNone
	p[1] = 0
	binary.LittleEndian.PutUint16(p[2:], 0)                    // AndXOffset
	binary.LittleEndian.PutUint16(p[4:], fid)                  // FID
	binary.LittleEndian.PutUint16(p[6:], 0)                    // FileAttributes
	binary.LittleEndian.PutUint32(p[8:], 0)                    // LastWriteTime
	binary.LittleEndian.PutUint32(p[12:], uint32(info.Size())) // FileSize, low 32
	binary.LittleEndian.PutUint16(p[16:], 0)                   // GrantedAccess
	binary.LittleEndian.PutUint16(p[18:], 0)                   // FileType
	binary.LittleEndian.PutUint16(p[20:], 0)                   // IPCState
	binary.LittleEndian.PutUint16(p[22:], 0x0001)              // Action: existed and opened
	binary.LittleEndian.PutUint32(p[24:], 0)                   // ServerFID
	binary.LittleEndian.PutUint16(p[28:], 0)                   // reserved
	return reply{params: p, data: []byte{}}
}

func (c *conn) ntCreateAndX(r *req) reply {
	sh := c.shareForTID(r.tid)
	if sh == nil {
		return fail(StatusObjectNameNotFound)
	}
	// NameLength sits after AndX(4) + Reserved(1).
	nameLen := int(u16(r.params, 5))
	raw := r.data
	// ASCII with unicode off: the name may be preceded by a single pad byte.
	if len(raw) > 0 && raw[0] == 0x00 {
		raw = raw[1:]
	}
	if nameLen > len(raw) {
		nameLen = len(raw)
	}
	name := asciiz(raw[:nameLen])

	local, ok := sh.resolve(name, false)
	if !ok {
		return fail(StatusObjectNameNotFound)
	}
	info, err := os.Stat(local)
	if err != nil {
		c.server.cfg.Log.Debug("SMB ntcreate miss", map[string]any{"name": name})
		return fail(StatusObjectNameNotFound)
	}
	isDir := info.IsDir()
	of := &openFile{path: local, isDir: isDir}
	if !isDir {
		fh, err := os.Open(local)
		if err != nil {
			return fail(StatusObjectNameNotFound)
		}
		of.fh = fh
		of.size = info.Size()
	}
	fid := c.allocFID()
	c.files[fid] = of
	ft := toFiletime(info.ModTime())
	attrs := uint32(attrNormal)
	if isDir {
		attrs = attrDirectory
	}

	p := make([]byte, 0, 68)
	p = append(p, ComNone, 0)
	p = le16(p, 0)           // AndXOffset
	p = append(p, 0)         // OplockLevel
	p = le16(p, fid)         // FID
	p = le32(p, 1)           // CreateAction = FILE_OPENED
	for i := 0; i < 4; i++ { // Creation/Access/Write/Change
		p = le64(p, uint64(ft))
	}
	p = le32(p, attrs)           // ExtFileAttributes
	p = le64(p, uint64(of.size)) // AllocationSize
	p = le64(p, uint64(of.size)) // EndOfFile
	p = le16(p, 0)               // FileType
	p = le16(p, 0)               // IPCState
	if isDir {
		p = append(p, 1)
	} else {
		p = append(p, 0)
	}
	// The parameter block must be a whole number of 16-bit words; the trailing
	// IsDirectory byte makes it odd, so it is padded. buildBody divides by two
	// to produce WordCount, and an odd length would understate it.
	if len(p)%2 != 0 {
		p = append(p, 0)
	}
	return reply{params: p, data: []byte{}}
}

func (c *conn) readAndX(r *req) reply {
	// The hot path. Params: AndX(4) FID(2) OffsetLow(4) MaxCountLow(2)
	// MinCount(2) Timeout/MaxCountHigh(4) Remaining(2) OffsetHigh(4)
	p := r.params
	fid := u16(p, 4)
	offLow := u32(p, 6)
	maxLow := u16(p, 10)
	maxHigh := u16(p, 14)
	var offHigh uint32
	if len(p) >= 24 {
		offHigh = u32(p, 20)
	}
	maxCount := int(maxLow) | int(maxHigh)<<16

	of := c.files[fid]
	if of == nil || of.fh == nil {
		return fail(StatusObjectNameNotFound)
	}
	if maxCount > maxReadChunk {
		maxCount = maxReadChunk
	}
	chunk := []byte{}
	if maxCount > 0 {
		offset := int64(offLow) | int64(offHigh)<<32
		buf := make([]byte, maxCount)
		n, err := of.fh.ReadAt(buf, offset)
		if n > 0 {
			chunk = buf[:n]
		} else if err != nil && n == 0 {
			// A read past EOF is a zero-length reply, not an error: OPL probes
			// the end of a file this way.
			chunk = []byte{}
		}
	}
	n := len(chunk)

	params := make([]byte, 24)
	params[0] = ComNone
	params[1] = 0
	binary.LittleEndian.PutUint16(params[2:], 0)                   // AndXOffset
	binary.LittleEndian.PutUint16(params[4:], 0)                   // Remaining
	binary.LittleEndian.PutUint16(params[6:], 0)                   // DataCompactionMode
	binary.LittleEndian.PutUint16(params[8:], 0)                   // reserved
	binary.LittleEndian.PutUint16(params[10:], uint16(n&0xFFFF))   // DataLengthLow
	binary.LittleEndian.PutUint16(params[12:], readAndXDataOffset) // DataOffset, fixed at 59
	binary.LittleEndian.PutUint32(params[14:], uint32(n>>16))      // DataLengthHigh
	// params[18:24] is reserved2[6] and stays zero.
	return reply{params: params, data: chunk}
}

func (c *conn) writeAndX(r *req) reply {
	if c.server.cfg.ReadOnly {
		return fail(StatusAccessDenied)
	}
	p := r.params
	fid := u16(p, 4)
	offLow := u32(p, 6)
	dlenHigh := u16(p, 18)
	dlenLow := u16(p, 20)
	dataOff := int(u16(p, 22))
	var offHigh uint32
	if len(p) >= 28 {
		offHigh = u32(p, 24)
	}
	n := int(dlenLow) | int(dlenHigh)<<16

	of := c.files[fid]
	if of == nil {
		return fail(StatusAccessDenied)
	}
	// A slice of the message already received, bounds-checked. The declared
	// count is the client's and is not trusted to size anything.
	payload := r.slice(dataOff, n)

	fh, err := os.OpenFile(of.path, os.O_WRONLY, 0)
	if err != nil {
		return fail(StatusAccessDenied)
	}
	written, err := fh.WriteAt(payload, int64(offLow)|int64(offHigh)<<32)
	closeErr := fh.Close()
	if err != nil || closeErr != nil {
		return fail(StatusAccessDenied)
	}

	params := make([]byte, 12)
	params[0] = ComNone
	params[1] = 0
	binary.LittleEndian.PutUint16(params[2:], 0)                      // AndXOffset
	binary.LittleEndian.PutUint16(params[4:], uint16(written&0xFFFF)) // CountLow
	binary.LittleEndian.PutUint16(params[6:], 0)                      // Remaining
	binary.LittleEndian.PutUint16(params[8:], 0)                      // CountHigh
	binary.LittleEndian.PutUint16(params[10:], 0)                     // reserved
	return reply{params: params, data: []byte{}}
}

func (c *conn) closeFile(r *req) reply {
	fid := u16(r.params, 0)
	if of, ok := c.files[fid]; ok {
		of.close()
		delete(c.files, fid)
	}
	return reply{params: []byte{}, data: []byte{}}
}

func (c *conn) checkDirectory(r *req) reply {
	sh := c.shareForTID(r.tid)
	if sh == nil {
		return fail(StatusObjectNameNotFound)
	}
	name := asciiz(r.data)
	if name == "" {
		name = "\\"
	}
	local, ok := sh.resolve(name, false)
	if !ok {
		return fail(StatusObjectNameNotFound)
	}
	if info, err := os.Stat(local); err == nil && info.IsDir() {
		return reply{params: []byte{}, data: []byte{}}
	}
	return fail(StatusObjectNameNotFound)
}

// --- TRANS2 ---------------------------------------------------------------

func (c *conn) trans2(r *req) reply {
	p := r.params
	paramCount := int(u16(p, 18))
	paramOff := int(u16(p, 20))
	dataCount := int(u16(p, 22))
	dataOff := int(u16(p, 24))
	var setup uint16
	if len(p) > 26 && p[26] >= 1 {
		setup = u16(p, 28)
	}
	tParams := r.slice(paramOff, paramCount)
	tData := r.slice(dataOff, dataCount)

	switch setup {
	case trans2FindFirst2:
		return c.findFirst2(r, tParams)
	case trans2FindNext2:
		return c.findNext2(r, tParams)
	case trans2QueryPathInfo:
		return c.queryPathInfo(r, tParams)
	}
	c.server.cfg.Log.Debug("SMB unhandled trans2", map[string]any{"subcmd": setup})
	_ = tData
	return fail(StatusNotImplemented)
}

// trans2Reply builds the fixed 10-word Transaction2 envelope with its
// 4-byte-aligned parameter and data blocks.
//
// The offsets are absolute from the start of the SMB header, which is why the
// base is computed from the header size rather than from the body: header(32)
// + WordCount(1) + 10 words(20) + ByteCount(2) = 55.
func trans2Reply(tParams, tData []byte) reply {
	const base = 32 + 1 + 20 + 2
	paramOff := (base + 3) &^ 3
	pad1 := paramOff - base
	dataStart := paramOff + len(tParams)
	dataOff := (dataStart + 3) &^ 3
	pad2 := dataOff - dataStart

	p := make([]byte, 0, 22)
	p = le16(p, uint16(len(tParams))) // TotalParameterCount
	p = le16(p, uint16(len(tData)))   // TotalDataCount
	p = le16(p, 0)                    // Reserved
	p = le16(p, uint16(len(tParams))) // ParameterCount
	p = le16(p, uint16(paramOff))     // ParameterOffset
	p = le16(p, 0)                    // ParameterDisplacement
	p = le16(p, uint16(len(tData)))   // DataCount
	p = le16(p, uint16(dataOff))      // DataOffset
	p = le16(p, 0)                    // DataDisplacement
	p = append(p, 0, 0)               // SetupCount, Reserved2

	body := make([]byte, 0, pad1+len(tParams)+pad2+len(tData))
	body = append(body, make([]byte, pad1)...)
	body = append(body, tParams...)
	body = append(body, make([]byte, pad2)...)
	body = append(body, tData...)
	return reply{params: p, data: body}
}

// dirEntry104 is one SMB_FIND_FILE_BOTH_DIRECTORY_INFO record with
// NextEntryOffset = 0, because this server returns exactly one entry per
// round trip.
func dirEntry104(name, localPath string) []byte {
	var size int64
	var ft int64
	isDir := name == "." || name == ".."
	if info, err := os.Stat(localPath); err == nil {
		isDir = info.IsDir()
		if !isDir {
			size = info.Size()
		}
		ft = toFiletime(info.ModTime())
	}
	attrs := uint32(attrNormal)
	if isDir {
		attrs = attrDirectory
	}
	nb := []byte(name)

	rec := make([]byte, 0, 94+len(nb))
	rec = le32(rec, 0)       // NextEntryOffset: only/last
	rec = le32(rec, 0)       // FileIndex
	for i := 0; i < 4; i++ { // Creation/Access/Write/Change
		rec = le64(rec, uint64(ft))
	}
	rec = le64(rec, uint64(size))          // EndOfFile
	rec = le64(rec, uint64(size))          // AllocationSize
	rec = le32(rec, attrs)                 // ExtFileAttributes
	rec = le32(rec, uint32(len(nb)))       // FileNameLength, byte-exact
	rec = le32(rec, 0)                     // EaSize
	rec = append(rec, 0)                   // ShortNameLength
	rec = append(rec, 0)                   // Reserved
	rec = append(rec, make([]byte, 24)...) // ShortName
	return append(rec, nb...)
}

func (c *conn) findFirst2(r *req, tParams []byte) reply {
	// SearchAttributes(2) SearchCount(2) Flags(2) LevelOfInterest(2)
	// StorageType(4) SearchPattern(asciiz)
	if len(tParams) < 12 {
		return fail(StatusObjectNameNotFound)
	}
	pattern := asciiz(tParams[12:])
	sh := c.shareForTID(r.tid)
	if sh == nil {
		return fail(StatusObjectNameNotFound)
	}
	dirPath := strings.ReplaceAll(pattern, "/", "\\")
	switch {
	case strings.HasSuffix(dirPath, "\\*"):
		dirPath = dirPath[:len(dirPath)-2]
	case strings.HasSuffix(dirPath, "*"):
		dirPath = dirPath[:len(dirPath)-1]
	}
	if dirPath == "" {
		dirPath = "\\"
	}
	local, ok := sh.resolve(dirPath, false)
	if !ok {
		return fail(StatusObjectNameNotFound)
	}
	info, err := os.Stat(local)
	if err != nil || !info.IsDir() {
		c.server.cfg.Log.Debug("SMB findfirst miss", map[string]any{"pattern": pattern})
		return fail(StatusObjectNameNotFound)
	}
	names, err := readDirNames(local)
	if err != nil {
		return fail(StatusObjectNameNotFound)
	}
	entries := make([]searchEntry, 0, len(names)+2)
	entries = append(entries,
		searchEntry{name: ".", local: local},
		searchEntry{name: "..", local: local})
	for _, n := range names {
		entries = append(entries, searchEntry{name: n, local: filepath.Join(local, n)})
	}

	sid := c.allocSID()
	eos := uint16(0)
	if len(entries) == 1 {
		eos = 1
	}
	c.searches[sid] = &search{entries: entries, cursor: 1}
	c.server.cfg.Log.Debug("SMB findfirst", map[string]any{
		"dir": dirPath, "sid": sid, "entries": len(entries)})

	rp := make([]byte, 0, 10)
	rp = le16(rp, sid) // SID
	rp = le16(rp, 1)   // SearchCount
	rp = le16(rp, eos) // EndOfSearch
	rp = le16(rp, 0)   // EaErrorOffset
	rp = le16(rp, 0)   // LastNameOffset
	return trans2Reply(rp, dirEntry104(entries[0].name, entries[0].local))
}

func (c *conn) findNext2(r *req, tParams []byte) reply {
	sid := u16(tParams, 0)
	st := c.searches[sid]
	exhausted := func() reply {
		rp := make([]byte, 0, 8)
		rp = le16(rp, 0) // SearchCount
		rp = le16(rp, 1) // EndOfSearch
		rp = le16(rp, 0)
		rp = le16(rp, 0)
		return trans2Reply(rp, nil)
	}
	if st == nil {
		return exhausted()
	}
	if st.cursor >= len(st.entries) {
		delete(c.searches, sid)
		return exhausted()
	}
	e := st.entries[st.cursor]
	eos := uint16(0)
	if st.cursor == len(st.entries)-1 {
		eos = 1
	}
	st.cursor++

	// FIND_NEXT2 carries no leading SID -- eight bytes, not ten. Sending the
	// FIND_FIRST2 shape here shifts every field and the client reads garbage.
	rp := make([]byte, 0, 8)
	rp = le16(rp, 1)   // SearchCount
	rp = le16(rp, eos) // EndOfSearch
	rp = le16(rp, 0)   // EaErrorOffset
	rp = le16(rp, 0)   // LastNameOffset
	return trans2Reply(rp, dirEntry104(e.name, e.local))
}

func (c *conn) queryPathInfo(r *req, tParams []byte) reply {
	level := u16(tParams, 0)
	if len(tParams) < 6 {
		return fail(StatusObjectNameNotFound)
	}
	name := asciiz(tParams[6:])
	sh := c.shareForTID(r.tid)
	if sh == nil {
		return fail(StatusObjectNameNotFound)
	}
	if name == "" {
		name = "\\"
	}
	local, ok := sh.resolve(name, false)
	if !ok {
		return fail(StatusObjectNameNotFound)
	}
	info, err := os.Stat(local)
	if err != nil {
		return fail(StatusObjectNameNotFound)
	}
	isDir := info.IsDir()
	var size int64
	if !isDir {
		size = info.Size()
	}
	ft := toFiletime(info.ModTime())
	attrs := uint32(attrNormal)
	if isDir {
		attrs = attrDirectory
	}

	var rd []byte
	if level == queryFileStandardInfo {
		rd = make([]byte, 0, 24)
		rd = le64(rd, uint64(size)) // AllocationSize
		rd = le64(rd, uint64(size)) // EndOfFile
		rd = le32(rd, 0)            // NumberOfLinks
		rd = append(rd, 0)          // DeletePending
		if isDir {
			rd = append(rd, 1)
		} else {
			rd = append(rd, 0)
		}
		rd = le16(rd, 0) // Reserved
	} else {
		// Basic info, and the fallback for any level this server does not
		// implement -- the reference answers rather than erroring, because OPL
		// treats a failure here as the file being absent.
		rd = make([]byte, 0, 40)
		for i := 0; i < 4; i++ {
			rd = le64(rd, uint64(ft))
		}
		rd = le32(rd, attrs)
		rd = le32(rd, 0)
	}
	return trans2Reply(nil, rd)
}

// transaction answers the RAP NetShareEnum that OPL's share picker sends when
// its Share field is left empty.
func (c *conn) transaction(r *req) reply {
	names := make([]string, 0, len(c.server.shares))
	for _, sh := range c.server.shares {
		names = append(names, sh.Name)
	}
	sort.Strings(names)

	// Level 1 record: 13-byte name + pad(1) + type(2) + comment pointer(4).
	const recSize = 20
	commentPoolOff := recSize * len(names)
	data := make([]byte, 0, commentPoolOff+len(names))
	comments := make([]byte, 0, len(names))
	for range names {
		comments = append(comments, 0)
	}
	for i, n := range names {
		name13 := make([]byte, 13)
		copy(name13, n)
		data = append(data, name13...)
		data = append(data, 0)                      // pad
		data = le16(data, 0)                        // type 0 = STYPE_DISKTREE
		data = le32(data, uint32(commentPoolOff+i)) // comment offset
	}
	data = append(data, comments...)

	rp := make([]byte, 0, 8)
	rp = le16(rp, 0)                  // Status
	rp = le16(rp, 0)                  // Converter
	rp = le16(rp, uint16(len(names))) // EntryCount
	rp = le16(rp, uint16(len(names))) // AvailableEntries
	return trans2Reply(rp, data)
}

func readDirNames(dir string) ([]string, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		names = append(names, e.Name())
	}
	sort.Strings(names)
	return names, nil
}

func le16(b []byte, v uint16) []byte {
	var t [2]byte
	binary.LittleEndian.PutUint16(t[:], v)
	return append(b, t[:]...)
}

func le32(b []byte, v uint32) []byte {
	var t [4]byte
	binary.LittleEndian.PutUint32(t[:], v)
	return append(b, t[:]...)
}

func le64(b []byte, v uint64) []byte {
	var t [8]byte
	binary.LittleEndian.PutUint64(t[:], v)
	return append(b, t[:]...)
}
