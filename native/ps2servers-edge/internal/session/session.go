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
		if h.Closer != nil {
			_ = h.Closer.Close()
		}
		delete(s.Handles, id)
	}
}
