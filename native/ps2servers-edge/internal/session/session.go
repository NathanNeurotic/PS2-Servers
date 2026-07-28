package session

import (
	"io"
	"net"
	"sync"
	"time"
)

type Profile string

const (
	Pending  Profile = "pending"
	Standard Profile = "standard"
	Modulo   Profile = "modulo"
)

type Socket string

const (
	DataSocket      Socket = "data"
	DiscoverySocket Socket = "discovery"
)

type Handle struct {
	Reader    io.ReadSeeker
	Closer    io.Closer
	Directory []DirEntry
	Index     int
	// Writer is non-nil only for a handle opened with write access. For a
	// writable regular file it is the same *os.File as Reader, so read and
	// seek keep working on it unchanged.
	Writer io.Writer
	// RealPath is the resolved on-disk path, retained so closing the handle
	// can release this file's single-writer reservation.
	RealPath string
}

type DirEntry struct {
	Name       string
	SourcePath string
	Mode       uint32
	Size       uint64
	ModTime    time.Time
}

type AckEvent struct {
	ACK      bool
	Sequence uint16
}

type State struct {
	Mu                 sync.Mutex
	Peer               *net.UDPAddr
	DiscoverySequence  uint16
	Profile            Profile
	ResponseSocket     Socket
	ExpectedReceive    uint16
	TransmitSequence   uint16
	TransmitAcked      uint16
	LastActivity       time.Time
	FallbackGeneration uint64
	FallbackSent       bool
	Streaming          bool
	Handles            map[int32]*Handle
	NextHandle         int32
	TxBuffer           []BufferedPacket
	AckEvents          chan AckEvent

	// In-flight write assembly. A WriteRequest opens the sequence and the
	// chunks that follow accumulate here until ReceivedChunks reaches
	// TotalChunks, at which point the buffer is written in one call. Held
	// per-peer rather than per-server so two consoles writing at once cannot
	// interleave into each other's buffer.
	WriteActive        bool
	WriteHandle        int32
	WriteBuffer        []byte
	WriteTotalChunks   uint16
	WriteReceivedChunk uint16
	// WriteIsBlock routes the assembled buffer to the shared block device at
	// WriteOffset instead of to a file handle. BWRITE and a file write share
	// the same WriteData chunk sequence, so only the destination differs.
	WriteIsBlock bool
	WriteOffset  int64
	// WriteExpectedLen is how many bytes a BWRITE said it would deliver, or 0
	// for a file write, which declares no length up front.
	//
	// It exists because the sector range a BWRITE declares and the byte count
	// the chunk sequence actually delivers are two independent numbers. The
	// range check validates the first; the chunk loop is driven by the client's
	// own totalChunks and is bounded only by maxWriteBytes. Without recording
	// this, a client could declare one in-range sector and then stream
	// megabytes, and the flush would write all of it at the validated offset --
	// past the end of the image, which extends the file.
	WriteExpectedLen int64

	// ReleaseWriter, when set by the server, is called with a handle's
	// RealPath as that handle is closed if it held write access, so the
	// server's single-writer reservation for that file is dropped.
	ReleaseWriter func(realPath string)
}

// ResetWrite abandons any partially assembled write. Called when a write
// sequence completes, errors, or the session is reset, so a stale buffer can
// never be flushed into a later unrelated write.
func (s *State) ResetWrite() {
	s.WriteActive = false
	s.WriteHandle = 0
	s.WriteBuffer = nil
	s.WriteTotalChunks = 0
	s.WriteReceivedChunk = 0
	s.WriteIsBlock = false
	s.WriteOffset = 0
	s.WriteExpectedLen = 0
}

type BufferedPacket struct {
	Sequence uint16
	Bytes    []byte
}

func New(peer *net.UDPAddr, forced Profile) *State {
	profile := forced
	if profile == "" {
		profile = Pending
	}
	return &State{
		Peer: peer, Profile: profile, ResponseSocket: DataSocket,
		TransmitAcked: 0x0FFF, LastActivity: time.Now(),
		Handles: make(map[int32]*Handle), NextHandle: 1,
		AckEvents: make(chan AckEvent, 32),
	}
}

func (s *State) Touch() { s.LastActivity = time.Now() }

// Reset closes per-peer resources and returns protocol counters to a clean
// pre-handshake state. The caller must hold Mu.
func (s *State) Reset(profile Profile) {
	s.Close()
	s.Profile = profile
	s.ResponseSocket = DataSocket
	s.ExpectedReceive = 0
	s.TransmitSequence = 0
	s.TransmitAcked = 0x0FFF
	s.FallbackSent = false
	s.Streaming = false
	s.TxBuffer = nil
	s.NextHandle = 1
	s.ResetWrite()
	for {
		select {
		case <-s.AckEvents:
		default:
			return
		}
	}
}

// Close closes every file owned by this peer. The caller must serialize access.
func (s *State) Close() {
	for id, h := range s.Handles {
		s.releaseWrite(h)
		if h.Closer != nil {
			_ = h.Closer.Close()
		}
		delete(s.Handles, id)
	}
	s.ResetWrite()
}

// releaseWrite drops a writable handle's single-writer reservation. Routing
// every teardown path through here -- explicit close, session reset, idle
// reap, and server shutdown -- means a peer that vanishes mid-write cannot
// leave a file reserved forever.
func (s *State) releaseWrite(h *Handle) {
	if h == nil || h.Writer == nil || h.RealPath == "" || s.ReleaseWriter == nil {
		return
	}
	s.ReleaseWriter(h.RealPath)
}

// CloseHandle closes one handle and releases anything it held.
func (s *State) CloseHandle(id int32) bool {
	h := s.Handles[id]
	if h == nil {
		return false
	}
	s.releaseWrite(h)
	if h.Closer != nil {
		_ = h.Closer.Close()
	}
	delete(s.Handles, id)
	if s.WriteActive && s.WriteHandle == id {
		s.ResetWrite()
	}
	return true
}
