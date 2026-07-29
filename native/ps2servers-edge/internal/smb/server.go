package smb

import (
	"encoding/binary"
	"errors"
	"io"
	"net"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/filesystem"
	edgelog "github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/statuslistener"
)

// Capability bits advertised in the NEGOTIATE reply.
const (
	capLargeFiles         = 0x00000008
	capNTSMBs             = 0x00000010
	capStatus32           = 0x00000040
	capLargeReadX         = 0x00004000
	serverCaps            = capNTSMBs | capStatus32 | capLargeFiles | capLargeReadX
	attrNormal            = 0x80
	attrDirectory         = 0x10
	trans2FindFirst2      = 0x01
	trans2FindNext2       = 0x02
	trans2QueryPathInfo   = 0x05
	queryFileBasicInfo    = 0x0101
	queryFileStandardInfo = 0x0102
)

// maxReadChunk is the clamp the reference applies to every READ_ANDX:
// min(maxcount, 0xFFFF). It is what bounds this server's memory, so it is
// named rather than left as a literal in the read path.
const maxReadChunk = 0xFFFF

// readAndXDataOffset is sizeof(ReadAndXResponse_t) in OPL's cdvdman: the data
// begins immediately after ByteCount at a fixed offset of 59 from the start of
// the SMB header. OPL reads from that offset unconditionally, so it is not a
// value this server may choose.
const readAndXDataOffset = 59

// toFiletime converts a Go time to a Windows FILETIME: 100ns ticks since
// 1601-01-01 UTC.
func toFiletime(t time.Time) int64 {
	if t.IsZero() || t.Unix() <= 0 {
		return 0
	}
	return (t.Unix() + 11644473600) * 10000000
}

// Share is one exported directory.
type Share struct {
	Name string
	Root *filesystem.Root
}

// Config configures a server.
type Config struct {
	Shares   []Share
	Bind     string
	Port     int
	ReadOnly bool
	Log      *edgelog.Logger
	// StatusPort answers router status queries, so a launcher can tell whether
	// this server is ready and whether a transfer is in flight. Zero disables
	// it; negative means "use the standard port".
	//
	// The standard port is UDPFS's discovery port, which is what a client
	// probes. On a box already running Edge's udpfs that address is
	// legitimately taken, and the failure is logged and ignored rather than
	// taking SMB down over a diagnostic.
	StatusPort int
}

// Server serves SMBv1 to Open-PS2-Loader.
type Server struct {
	cfg      Config
	shares   map[string]Share
	listener net.Listener

	// sem bounds concurrent connections. See MaxConnections: the reference
	// spawns an unbounded thread per accepted socket, which is fine on a
	// desktop and poor hygiene on a 32 MB router.
	sem chan struct{}

	// live tracks accepted connections so Close can shut them down instead of
	// waiting on the peer's goodwill, and guards wg.Add against a concurrent
	// wg.Wait -- adding to a WaitGroup whose counter may already be zero while
	// another goroutine waits on it is a race, not merely untidy.
	mu        sync.Mutex
	live      map[net.Conn]struct{}
	stopping  bool
	wg        sync.WaitGroup
	closeOnce sync.Once
	closed    chan struct{}

	started time.Time
	status  *statuslistener.Listener
}

// idleTimeout bounds how long a connection may sit without sending anything.
//
// Without it, sixteen sockets that never send a byte hold every slot forever
// and the console cannot connect at all -- MaxConnections turned a memory
// concern into an availability one. It is a per-read deadline rather than a
// session limit because OPL legitimately holds one connection open across a
// whole game load; every message it sends, including its ComEcho and NetBIOS
// keep-alives, refreshes it.
const idleTimeout = 5 * time.Minute

// New validates a configuration and returns a server.
func New(cfg Config) (*Server, error) {
	if len(cfg.Shares) == 0 {
		return nil, errors.New("smb: no shares configured")
	}
	if cfg.Port == 0 {
		cfg.Port = DefaultPort
	}
	if cfg.Port < 1 || cfg.Port > 65535 {
		return nil, errors.New("smb: port out of range")
	}
	if cfg.Log == nil {
		cfg.Log = edgelog.New(os.Stdout, "text", false, false)
	}
	s := &Server{
		live:    make(map[net.Conn]struct{}),
		cfg:     cfg,
		shares:  make(map[string]Share, len(cfg.Shares)),
		sem:     make(chan struct{}, MaxConnections),
		closed:  make(chan struct{}),
		started: time.Now(),
	}
	for _, sh := range cfg.Shares {
		// Keyed lowercase: SMB share names are case-insensitive, and matching
		// exactly would make "GAMES" fail where "games" worked, which reads as
		// a broken server rather than a wrong name.
		s.shares[strings.ToLower(sh.Name)] = sh
	}
	return s, nil
}

// Addr reports the bound address, or nil before Listen.
func (s *Server) Addr() net.Addr {
	if s.listener == nil {
		return nil
	}
	return s.listener.Addr()
}

// Listen binds the TCP port.
func (s *Server) Listen() error {
	addr := net.JoinHostPort(s.cfg.Bind, itoa(s.cfg.Port))
	ln, err := net.Listen("tcp4", addr)
	if err != nil {
		return err
	}
	s.listener = ln

	if s.cfg.StatusPort != 0 {
		port := s.cfg.StatusPort
		if port < 0 {
			port = int(protocol.DefaultPort)
		}
		sl, err := statuslistener.Start(s.cfg.Bind, port, s.statusReply, s.cfg.Log)
		if err != nil {
			// Non-fatal on purpose: see Config.StatusPort. Losing the status
			// channel is a smaller harm than refusing to serve SMB.
			s.cfg.Log.Warn("SMB status channel unavailable", map[string]any{
				"port": port, "error": err.Error()})
		} else {
			s.status = sl
			s.cfg.Log.Info("SMB status channel", map[string]any{"addr": sl.Addr().String()})
		}
	}
	return nil
}

// statusReply reports what this server is doing, for the router status
// protocol. See docs/ROUTER-STATUS.md.
func (s *Server) statusReply() protocol.StatusReply {
	flags := protocol.FlagServesSMB
	if s.cfg.ReadOnly {
		flags |= protocol.FlagReadOnly
	}

	s.mu.Lock()
	sessions := len(s.live)
	stopping := s.stopping
	s.mu.Unlock()

	state := protocol.StateReady
	switch {
	case stopping:
		state = protocol.StateStopping
	case s.sharesUnreadable():
		// The ordinary hardware failure on a router: the USB stick went away.
		// Reporting ready would send someone hunting the network for what is a
		// storage fault.
		state = protocol.StateDegraded
	case sessions > 0:
		// Busy is per-connection rather than per-transfer here. SMB has no
		// idle state a server can see -- a console keeps the connection open
		// across a whole game load -- so a live connection is the closest
		// honest answer to "is something happening", and it is the one that
		// matters for "do not cut power".
		state = protocol.StateBusy
	}

	return protocol.StatusReply{
		State:    state,
		Flags:    flags,
		Sessions: uint16(sessions),
		Uptime:   statuslistener.Uptime(s.started),
		Name:     "PS2 Servers Edge",
	}
}

func (s *Server) sharesUnreadable() bool {
	for _, sh := range s.cfg.Shares {
		if sh.Root == nil {
			return true
		}
		if info, err := os.Stat(sh.Root.Path()); err != nil || !info.IsDir() {
			return true
		}
	}
	return false
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [8]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}

// Serve accepts connections until Close.
func (s *Server) Serve() error {
	for {
		conn, err := s.listener.Accept()
		if err != nil {
			select {
			case <-s.closed:
				return nil
			default:
			}
			return err
		}
		// TCP_NODELAY: the reference sets it on every accepted socket so each
		// reply is pushed immediately rather than waiting for Nagle. Go
		// defaults to it, but it is set explicitly because a change in that
		// default would show up as unexplained latency on a game load.
		if tcp, ok := conn.(*net.TCPConn); ok {
			_ = tcp.SetNoDelay(true)
		}

		s.mu.Lock()
		if s.stopping {
			s.mu.Unlock()
			_ = conn.Close()
			continue
		}
		s.mu.Unlock()

		select {
		case s.sem <- struct{}{}:
		default:
			// At the limit. Closing immediately is better than queuing: a
			// console that cannot connect retries, whereas one held in a
			// backlog waits on a server that may never free a slot.
			s.cfg.Log.Warn("SMB connection refused, at the limit", map[string]any{
				"peer": conn.RemoteAddr().String(), "limit": MaxConnections})
			_ = conn.Close()
			continue
		}

		// Registered under the lock, together with the stopping check, so a
		// connection accepted as Close runs is either tracked (and therefore
		// closed by Close) or rejected -- never left running past shutdown.
		s.mu.Lock()
		if s.stopping {
			s.mu.Unlock()
			<-s.sem
			_ = conn.Close()
			continue
		}
		s.live[conn] = struct{}{}
		s.wg.Add(1)
		s.mu.Unlock()

		go func(c net.Conn) {
			defer s.wg.Done()
			defer func() { <-s.sem }()
			defer func() {
				s.mu.Lock()
				delete(s.live, c)
				s.mu.Unlock()
			}()
			s.serveConn(c)
		}(conn)
	}
}

// Close stops the listener and waits for in-flight connections.
func (s *Server) Close() error {
	s.closeOnce.Do(func() {
		close(s.closed)
		if s.listener != nil {
			_ = s.listener.Close()
		}
		// Closing the listener only stops new connections. An established one
		// is parked in a blocking read, so without closing it too this waited
		// on the peer to hang up -- and OPL holds its connection open across a
		// whole game load, so "in-flight connections" meant "indefinitely".
		s.mu.Lock()
		s.stopping = true
		for c := range s.live {
			_ = c.Close()
		}
		s.mu.Unlock()
		_ = s.status.Close()
	})
	s.wg.Wait()
	return nil
}

// openFile is one handle held by a connection.
type openFile struct {
	path  string
	fh    *os.File
	isDir bool
	size  int64
}

func (o *openFile) close() {
	if o.fh != nil {
		_ = o.fh.Close()
		o.fh = nil
	}
}

// search is an in-progress directory listing.
type search struct {
	entries []searchEntry
	cursor  int
}

type searchEntry struct {
	name  string
	local string
}

// conn is per-connection state for one PS2 TCP session.
type conn struct {
	server    *Server
	net       net.Conn
	uid       uint16
	nextFID   uint16
	nextTID   uint16
	nextSID   uint16
	trees     map[uint16]*Share
	treeAge   []uint16
	files     map[uint16]*openFile
	fileAge   []uint16
	searches  map[uint16]*search
	searchAge []uint16
}

// evictOldest keeps a map at or below its cap by dropping the earliest entry,
// and returns the trimmed age list. Eviction rather than refusal: a client
// that legitimately exceeds a cap should keep working, and these ceilings sit
// far above anything a real console does.
func evictOldest[T any](m map[uint16]T, age []uint16, cap int, free func(T)) []uint16 {
	for len(age) > 0 && len(m) >= cap {
		oldest := age[0]
		age = age[1:]
		if v, ok := m[oldest]; ok {
			if free != nil {
				free(v)
			}
			delete(m, oldest)
		}
	}
	return age
}

func (c *conn) allocFID() uint16 {
	c.nextFID++
	if c.nextFID == 0 {
		c.nextFID = 0x1000
	}
	return c.nextFID
}

func (c *conn) allocTID() uint16 {
	c.nextTID++
	if c.nextTID == 0 {
		c.nextTID = 0x0001
	}
	return c.nextTID
}

func (c *conn) allocSID() uint16 {
	c.nextSID++
	if c.nextSID == 0 {
		c.nextSID = 0x0001
	}
	return c.nextSID
}

func (c *conn) cleanup() {
	for _, f := range c.files {
		f.close()
	}
	c.files = nil
	c.fileAge = nil
	c.searches = nil
	c.searchAge = nil
	c.trees = nil
	c.treeAge = nil
}

// req is a parsed request: the header fields plus the raw message.
//
// The raw message is retained because WRITE_ANDX's DataOffset and TRANS2's
// ParameterOffset/DataOffset are absolute offsets from the start of the SMB
// header, and index directly into this same buffer. Parsing into a fresh
// body slice and forgetting the original would silently read the wrong bytes.
type req struct {
	cmd       uint8
	tid       uint16
	pid       uint16
	uid       uint16
	mid       uint16
	msg       []byte
	wordCount uint8
	params    []byte
	data      []byte
}

func parseReq(msg []byte) (*req, error) {
	if len(msg) < 33 {
		return nil, errors.New("smb: short message")
	}
	h, err := ParseHeader(msg)
	if err != nil {
		return nil, err
	}
	r := &req{
		cmd: h.Command, tid: h.TID, pid: h.PID, uid: h.UID, mid: h.MID,
		msg: msg, wordCount: msg[32],
	}
	// Body: WordCount(1) + Params(WordCount*2) + ByteCount(2) + Data.
	// Every slice is bounds-checked against the real message length. The
	// Python original relies on slicing that silently clamps; in Go the same
	// arithmetic panics, so a truncated or hostile message would take the
	// connection down rather than being refused.
	pEnd := 33 + int(r.wordCount)*2
	if pEnd > len(msg) {
		return nil, errors.New("smb: params run past the message")
	}
	r.params = msg[33:pEnd]
	if pEnd+2 <= len(msg) {
		bc := int(binary.LittleEndian.Uint16(msg[pEnd:]))
		dStart := pEnd + 2
		dEnd := dStart + bc
		if dEnd > len(msg) {
			dEnd = len(msg)
		}
		r.data = msg[dStart:dEnd]
	}
	return r, nil
}

// slice returns msg[off:off+n] clamped to the message, never panicking.
func (r *req) slice(off, n int) []byte {
	if off < 0 || off > len(r.msg) || n <= 0 {
		return nil
	}
	end := off + n
	if end > len(r.msg) || end < off {
		end = len(r.msg)
	}
	return r.msg[off:end]
}

func buildBody(params, data []byte) []byte {
	body := make([]byte, 0, 1+len(params)+2+len(data))
	body = append(body, byte(len(params)/2))
	body = append(body, params...)
	var bc [2]byte
	binary.LittleEndian.PutUint16(bc[:], uint16(len(data)))
	body = append(body, bc[:]...)
	return append(body, data...)
}

// reply is what a handler produces.
type reply struct {
	params []byte
	data   []byte
	status uint32
	// uid and tid override the echoed header values. Only SESSION_SETUP and
	// TREE_CONNECT set them; every other reply echoes the request.
	uid    *uint16
	tid    *uint16
	silent bool
}

func fail(status uint32) reply { return reply{status: status} }

func (s *Server) serveConn(nc net.Conn) {
	peer := nc.RemoteAddr().String()
	c := &conn{
		server: s, net: nc,
		nextFID: 0x1000, nextTID: 0x0001, nextSID: 0x0001,
		trees:    make(map[uint16]*Share),
		files:    make(map[uint16]*openFile),
		searches: make(map[uint16]*search),
	}
	s.cfg.Log.Info("SMB connect", map[string]any{"peer": peer})
	defer func() {
		c.cleanup()
		_ = nc.Close()
		s.cfg.Log.Info("SMB disconnect", map[string]any{"peer": peer})
	}()

	for {
		// Refreshed per message, so an active console is never cut off while a
		// silent socket cannot squat on a slot.
		_ = nc.SetReadDeadline(time.Now().Add(idleTimeout))
		msg, err := ReadMessage(nc)
		if errors.Is(err, ErrKeepAlive) {
			continue
		}
		if err != nil {
			if !errors.Is(err, io.EOF) && !isClosed(err) {
				s.cfg.Log.Debug("SMB read ended", map[string]any{"peer": peer, "error": err.Error()})
			}
			return
		}
		r, err := parseReq(msg)
		if err != nil {
			// Malformed rather than fatal: the reference simply skips these.
			s.cfg.Log.Debug("SMB dropping malformed message", map[string]any{
				"peer": peer, "error": err.Error()})
			continue
		}

		rep := c.dispatch(r)
		if rep.silent {
			continue
		}
		if err := c.send(r, rep); err != nil {
			s.cfg.Log.Debug("SMB write failed", map[string]any{"peer": peer, "error": err.Error()})
			return
		}
	}
}

func isClosed(err error) bool {
	return errors.Is(err, net.ErrClosed) || errors.Is(err, io.ErrUnexpectedEOF)
}

func (c *conn) send(r *req, rep reply) error {
	// An error reply carries an empty body and echoes the request's own tid
	// and uid, ignoring any override -- matching the reference exactly.
	if rep.status != StatusSuccess || rep.params == nil {
		msg := append(PackHeader(r.cmd, rep.status, r.tid, r.pid, r.uid, r.mid),
			buildBody(nil, nil)...)
		return WriteMessage(c.net, msg)
	}
	tid, uid := r.tid, r.uid
	if rep.tid != nil {
		tid = *rep.tid
	}
	if rep.uid != nil {
		uid = *rep.uid
	}
	msg := append(PackHeader(r.cmd, rep.status, tid, r.pid, uid, r.mid),
		buildBody(rep.params, rep.data)...)
	return WriteMessage(c.net, msg)
}

func (c *conn) dispatch(r *req) reply {
	switch r.cmd {
	case ComNegotiate:
		return c.negotiate(r)
	case ComSessionSetupAndX:
		return c.sessionSetup(r)
	case ComTreeConnectAndX:
		return c.treeConnect(r)
	case ComTreeDisconnect:
		delete(c.trees, r.tid)
		// Searches belong to a tree, so dropping the tree must drop them too.
		// Without this a client that disconnects politely still strands every
		// listing it made, which is the ordinary case rather than an attack.
		c.dropSearches()
		return reply{params: []byte{}, data: []byte{}}
	case ComLogoffAndX:
		c.dropSearches()
		return reply{params: []byte{ComNone, 0, 0, 0}, data: []byte{}}
	case ComOpenAndX:
		return c.openAndX(r)
	case ComNTCreateAndX:
		return c.ntCreateAndX(r)
	case ComReadAndX:
		return c.readAndX(r)
	case ComWriteAndX:
		return c.writeAndX(r)
	case ComClose:
		return c.closeFile(r)
	case ComEcho:
		// smbman keep-alive: bounce the payload back with SequenceNumber=1.
		p := make([]byte, 2)
		binary.LittleEndian.PutUint16(p, 1)
		return reply{params: p, data: r.data}
	case ComQueryInformationDisk:
		// All u16 to avoid overflow; smbman only needs non-zero free space.
		p := make([]byte, 10)
		binary.LittleEndian.PutUint16(p[0:], 0xFFFF)
		binary.LittleEndian.PutUint16(p[2:], 64)
		binary.LittleEndian.PutUint16(p[4:], 512)
		binary.LittleEndian.PutUint16(p[6:], 0xFFFF)
		binary.LittleEndian.PutUint16(p[8:], 0)
		return reply{params: p, data: []byte{}}
	case ComCheckDirectory:
		return c.checkDirectory(r)
	case ComTrans2:
		return c.trans2(r)
	case ComTransaction:
		return c.transaction(r)
	}
	c.server.cfg.Log.Debug("SMB unhandled command", map[string]any{"cmd": r.cmd})
	return fail(StatusNotImplemented)
}
