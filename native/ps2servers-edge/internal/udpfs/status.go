package udpfs

import (
	"os"
	"time"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
)

// handleStatusQuery answers "are you ready?" for a launcher or loader.
//
// See docs/ROUTER-STATUS.md. Deliberately stateless: it creates no session,
// touches none, and cannot disturb a transfer in progress. A launcher polling
// once a second must be invisible to the console being served.
//
// The reply goes back on the socket the query arrived on rather than on the
// data socket, so this behaves under --single-port without a special case.
func (s *Server) handleStatusQuery(in inbound) {
	reply := protocol.StatusReply{
		State:    s.serverState(),
		Flags:    s.serviceFlags(),
		Sessions: uint16(s.sessionCount()),
		Uptime:   uint32(time.Since(s.stats.started).Seconds()),
		Name:     s.cfg.ServerName,
	}
	s.cfg.Log.Debug("status query", map[string]any{
		"peer": in.peer.String(), "state": reply.State.String(), "sessions": reply.Sessions})
	s.sendOn(in.socket, reply.Marshal(), in.peer)
}

// serverState collapses the server into the five states a client understands.
func (s *Server) serverState() protocol.ServerState {
	select {
	case <-s.closed:
		return protocol.StateStopping
	default:
	}

	// Busy means a transfer is in flight, and it is the reason this protocol
	// carries a state at all: a board wired to console power must not be cut
	// mid-write, and this is how a launcher knows to say so.
	if s.anyTransferInFlight() {
		return protocol.StateBusy
	}

	// A share that has become unreadable -- an unmounted USB stick, most
	// likely, which is the ordinary failure on this hardware -- is reported
	// rather than hidden. The server is still listening, so it is degraded and
	// not stopping, but answering "ready" would send someone hunting the
	// network for a storage fault.
	if s.root == nil {
		return protocol.StateDegraded
	}
	if info, err := os.Stat(s.root.Path()); err != nil || !info.IsDir() {
		return protocol.StateDegraded
	}
	return protocol.StateReady
}

// anyTransferInFlight reports whether any peer is mid-transfer.
func (s *Server) anyTransferInFlight() bool {
	s.sessionsMu.Lock()
	workers := make([]*peerWorker, 0, len(s.sessions))
	for _, w := range s.sessions {
		workers = append(workers, w)
	}
	s.sessionsMu.Unlock()

	// The session lock is released before taking per-session locks. Holding
	// both would invert the order the transfer path takes them in, and a
	// status poll is the last thing that should be able to deadlock a server.
	for _, w := range workers {
		st := w.state
		st.Mu.Lock()
		active := st.Streaming || st.WriteActive
		st.Mu.Unlock()
		if active {
			return true
		}
	}
	return false
}

func (s *Server) sessionCount() int {
	s.sessionsMu.Lock()
	defer s.sessionsMu.Unlock()
	return len(s.sessions)
}

func (s *Server) serviceFlags() uint16 {
	flags := protocol.FlagServesUDPFS
	if s.cfg.ReadOnly {
		flags |= protocol.FlagReadOnly
	}
	if !s.cfg.NoCompression {
		flags |= protocol.FlagDecompresses
	}
	// FlagServesUDPBD is deliberately not set when a --block-device is
	// attached. That is block access carried inside UDPFS, which is a
	// different thing from the UDPBD protocol the flag names, and a launcher
	// that lit up a "UDPBD" indicator for it would be lying.
	return flags
}
