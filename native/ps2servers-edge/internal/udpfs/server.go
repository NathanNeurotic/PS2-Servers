package udpfs

import (
	"context"
	"fmt"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/filesystem"
	edgelog "github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
	"net"
	"os"
	"sync"
	"time"
)

const (
	defaultFallback     = 250 * time.Millisecond
	sessionReplaceQuiet = 2 * time.Second
	windowSize          = 8
	ackTimeout          = 120 * time.Millisecond
	maxWindowRetries    = 4
	maxDatagram         = 64 * 1024
	maxPathBytes        = 1024
	maxHandles          = 64
	// maxPeers bounds concurrent peer sessions (PR #119 review): without a cap,
	// a spoofed UDP flood could grow s.sessions -- a goroutine plus buffered
	// channels per peer -- without bound. 64 is far above any real PS2 client
	// count, and the reaper frees expired sessions so legit peers regain slots.
	maxPeers = 64
)

type Config struct {
	Root          string
	Bind          string
	Port          int
	DataPort      int
	SinglePort    bool
	ProtocolMode  session.Profile
	PeerTimeout   time.Duration
	ReadOnly      bool
	Log           *edgelog.Logger
	ServerName    string
	FallbackDelay time.Duration
}

type inbound struct {
	packet []byte
	peer   *net.UDPAddr
	socket session.Socket
}

type peerWorker struct {
	state    *session.State
	queue    chan inbound
	done     chan struct{}
	stopOnce sync.Once
}

func (w *peerWorker) stop() {
	w.stopOnce.Do(func() { close(w.done) })
}

type Server struct {
	cfg        Config
	root       *filesystem.Root
	discovery  *net.UDPConn
	data       *net.UDPConn
	sessionsMu sync.Mutex
	sessions   map[string]*peerWorker
	incoming   chan inbound
	closed     chan struct{}
	closeOnce  sync.Once
	wg         sync.WaitGroup
}

func New(cfg Config) (*Server, error) {
	if cfg.Port == 0 {
		cfg.Port = int(protocol.DefaultPort)
	}
	if cfg.Port < 1 || cfg.Port > 65535 || cfg.DataPort < 0 || cfg.DataPort > 65535 {
		return nil, fmt.Errorf("port out of range")
	}
	if cfg.ProtocolMode == "" {
		cfg.ProtocolMode = session.Pending
	}
	if cfg.ProtocolMode != session.Pending && cfg.ProtocolMode != session.Standard && cfg.ProtocolMode != session.Modulo {
		return nil, fmt.Errorf("invalid protocol mode %q", cfg.ProtocolMode)
	}
	if cfg.PeerTimeout <= 0 {
		cfg.PeerTimeout = time.Hour
	}
	if cfg.FallbackDelay <= 0 {
		cfg.FallbackDelay = defaultFallback
	}
	if cfg.Log == nil {
		cfg.Log = edgelog.New(os.Stdout, "text", false, false)
	}
	if cfg.ServerName == "" {
		cfg.ServerName = "PS2 Servers Edge"
	}
	root, err := filesystem.Open(cfg.Root)
	if err != nil {
		return nil, err
	}
	s := &Server{cfg: cfg, root: root, sessions: make(map[string]*peerWorker), incoming: make(chan inbound, 256), closed: make(chan struct{})}
	return s, nil
}

func (s *Server) Listen() error {
	ip := net.ParseIP(s.cfg.Bind)
	if s.cfg.Bind != "" && ip == nil {
		return fmt.Errorf("invalid bind address %q", s.cfg.Bind)
	}
	disc, err := net.ListenUDP("udp4", &net.UDPAddr{IP: ip, Port: s.cfg.Port})
	if err != nil {
		return err
	}
	s.discovery = disc
	if s.cfg.SinglePort {
		s.data = disc
	} else {
		data, err := net.ListenUDP("udp4", &net.UDPAddr{IP: ip, Port: s.cfg.DataPort})
		if err != nil {
			disc.Close()
			return err
		}
		s.data = data
	}
	return nil
}
func (s *Server) Addr() (discovery, data *net.UDPAddr) {
	if s.discovery != nil {
		discovery = s.discovery.LocalAddr().(*net.UDPAddr)
	}
	if s.data != nil {
		data = s.data.LocalAddr().(*net.UDPAddr)
	}
	return
}

func (s *Server) Serve(ctx context.Context) error {
	if s.discovery == nil {
		if err := s.Listen(); err != nil {
			return err
		}
	}
	disc, data := s.Addr()
	s.cfg.Log.Info("UDPFS listening", map[string]any{"root": s.root.Path(), "discovery": disc.String(), "data": data.String(), "protocol": s.cfg.ProtocolMode, "read_only": true})
	s.wg.Add(1)
	go s.readLoop(s.discovery, session.DiscoverySocket)
	if s.data != s.discovery {
		s.wg.Add(1)
		go s.readLoop(s.data, session.DataSocket)
	}
	s.wg.Add(1)
	go s.reaper()
	for {
		select {
		case <-ctx.Done():
			s.Close()
			return nil
		case <-s.closed:
			return nil
		case in := <-s.incoming:
			s.dispatch(in)
		}
	}
}
func (s *Server) Close() error {
	var err error
	s.closeOnce.Do(func() {
		close(s.closed)
		if s.discovery != nil {
			err = s.discovery.Close()
		}
		if s.data != nil && s.data != s.discovery {
			_ = s.data.Close()
		}
		s.sessionsMu.Lock()
		for _, w := range s.sessions {
			w.state.Mu.Lock()
			w.state.Close()
			w.state.Mu.Unlock()
			w.stop()
		}
		s.sessions = map[string]*peerWorker{}
		s.sessionsMu.Unlock()
	})
	s.wg.Wait()
	return err
}
func (s *Server) readLoop(conn *net.UDPConn, which session.Socket) {
	defer s.wg.Done()
	buf := make([]byte, maxDatagram)
	for {
		n, peer, err := conn.ReadFromUDP(buf)
		if err != nil {
			select {
			case <-s.closed:
				return
			default:
				s.cfg.Log.Warn("UDP read failed", map[string]any{"socket": which, "error": err})
				continue
			}
		}
		pkt := append([]byte(nil), buf[:n]...)
		select {
		case s.incoming <- inbound{pkt, peer, which}:
		case <-s.closed:
			return
		}
	}
}
func (s *Server) reaper() {
	defer s.wg.Done()
	tick := time.NewTicker(5 * time.Second)
	defer tick.Stop()
	for {
		select {
		case <-s.closed:
			return
		case now := <-tick.C:
			s.sessionsMu.Lock()
			for key, w := range s.sessions {
				w.state.Mu.Lock()
				idle := now.Sub(w.state.LastActivity)
				if idle > s.cfg.PeerTimeout {
					w.state.Close()
					delete(s.sessions, key)
					w.stop()
					s.cfg.Log.Info("session expired", map[string]any{"peer": key, "idle": idle.String()})
				}
				w.state.Mu.Unlock()
			}
			s.sessionsMu.Unlock()
		}
	}
}

func (s *Server) getWorker(peer *net.UDPAddr) *peerWorker {
	key := peer.String()
	s.sessionsMu.Lock()
	defer s.sessionsMu.Unlock()
	if w := s.sessions[key]; w != nil {
		return w
	}
	if len(s.sessions) >= maxPeers {
		return nil
	}
	forced := s.cfg.ProtocolMode
	if forced == session.Pending {
		forced = ""
	}
	w := &peerWorker{state: session.New(peer, forced), queue: make(chan inbound, 256), done: make(chan struct{})}
	s.sessions[key] = w
	s.wg.Add(1)
	go s.worker(w)
	return w
}
func (s *Server) worker(w *peerWorker) {
	defer s.wg.Done()
	for {
		select {
		case <-w.done:
			return
		case in := <-w.queue:
			s.handleData(w.state, in)
		}
	}
}
func (s *Server) dispatch(in inbound) {
	h, err := protocol.ParseHeader(in.packet)
	if err != nil {
		s.cfg.Log.Debug("dropping malformed datagram", map[string]any{"peer": in.peer, "error": err})
		return
	}
	switch h.Type {
	case protocol.Discovery:
		s.handleDiscovery(in, h)
	case protocol.Data:
		w := s.getWorker(in.peer)
		if w == nil {
			s.cfg.Log.Warn("peer limit reached, dropping datagram", map[string]any{"peer": in.peer})
			return
		}
		_, dh, payload, parseErr := protocol.ParseDataPacket(in.packet)
		if parseErr == nil && len(payload) == 0 {
			s.handleControl(w.state, dh)
			return
		}
		select {
		case <-w.done:
			return
		case w.queue <- in:
		default:
			s.cfg.Log.Warn("peer queue full", map[string]any{"peer": in.peer})
		}
	default:
		s.cfg.Log.Debug("unknown packet type", map[string]any{"peer": in.peer, "type": h.Type})
	}
}
