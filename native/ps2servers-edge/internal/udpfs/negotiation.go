package udpfs

import (
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
	"net"
	"time"
)

func (s *Server) handleControl(st *session.State, dh protocol.DataHeader) {
	st.Mu.Lock()
	defer st.Mu.Unlock()
	st.Touch()
	if dh.Flags&protocol.FlagACK != 0 {
		st.TransmitAcked = dh.AckSequence
		s.pruneAcked(st, dh.AckSequence)
	} else {
		s.retransmit(st, dh.AckSequence)
	}
	select {
	case st.AckEvents <- session.AckEvent{ACK: dh.Flags&protocol.FlagACK != 0, Sequence: dh.AckSequence}:
	default:
	}
}

func (s *Server) handleDiscovery(in inbound, h protocol.Header) {
	if len(in.packet) < 6 {
		return
	}
	dh, err := protocol.ParseDiscoveryHeader(in.packet[2:])
	if err != nil || dh.ServiceID != protocol.ServiceUDPFS {
		return
	}
	w := s.getWorker(in.peer)
	if w == nil {
		s.cfg.Log.Warn("peer limit reached, dropping discovery", map[string]any{"peer": in.peer})
		return
	}
	st := w.state
	st.Mu.Lock()
	defer st.Mu.Unlock()

	quiet := time.Since(st.LastActivity)
	// Both client families may keep broadcasting sequence-zero discovery while
	// an established transfer is active. Reply, but never reset that live stream.
	// A quiet session is treated as a replacement from the same UDP endpoint.
	if st.Streaming && h.Sequence == 0 && quiet < sessionReplaceQuiet {
		s.sendStandardInform(st)
		return
	}

	profile := session.Pending
	if s.cfg.ProtocolMode == session.Standard || s.cfg.ProtocolMode == session.Modulo {
		profile = s.cfg.ProtocolMode
	}
	st.Reset(profile)
	st.DiscoverySequence = h.Sequence
	st.FallbackGeneration++
	generation := st.FallbackGeneration
	st.Touch()

	if s.cfg.ProtocolMode == session.Standard {
		st.ExpectedReceive = 0
		s.sendStandardInform(st)
		return
	}
	if s.cfg.ProtocolMode == session.Modulo {
		st.ExpectedReceive = protocol.Next(h.Sequence)
		st.ResponseSocket = session.DiscoverySocket
		s.sendModuloInform(st)
		return
	}

	// Automatic mode always offers the canonical two-port handshake first.
	st.ResponseSocket = session.DataSocket
	s.sendStandardInform(st)
	go func(key string, gen uint64) {
		timer := time.NewTimer(s.cfg.FallbackDelay)
		defer timer.Stop()
		select {
		case <-timer.C:
		case <-s.closed:
			return
		case <-w.done:
			return
		}
		s.sessionsMu.Lock()
		current := s.sessions[key]
		s.sessionsMu.Unlock()
		if current == nil || current != w {
			return
		}
		state := current.state
		state.Mu.Lock()
		defer state.Mu.Unlock()
		if state.FallbackGeneration != gen || state.Profile != session.Pending || state.Streaming {
			return
		}
		s.sendModuloInform(state)
	}(in.peer.String(), generation)
}

func (s *Server) sendStandardInform(st *session.State) {
	_, data := s.Addr()
	packet := append(protocol.Header{Type: protocol.Inform, Sequence: 1}.Marshal(), protocol.DiscoveryHeader{ServiceID: protocol.ServiceUDPFS, Port: uint16(data.Port)}.Marshal()...)
	s.sendOn(session.DataSocket, packet, st.Peer)
}
func (s *Server) infoPayload() []byte {
	name := []byte(s.cfg.ServerName)
	if len(name) > 31 {
		name = name[:31]
	}
	p := append([]byte{byte(len(name))}, name...)
	p = append(p, 0)
	for len(p)%4 != 0 {
		p = append(p, 0)
	}
	return p
}
func (s *Server) sendModuloInform(st *session.State) {
	packet := append(protocol.Header{Type: protocol.Inform, Sequence: st.TransmitSequence}.Marshal(), protocol.DiscoveryHeader{ServiceID: protocol.ServiceUDPFS, Port: 0}.Marshal()...)
	packet = append(packet, s.infoPayload()...)
	s.sendOn(session.DiscoverySocket, packet, st.Peer)
	st.TransmitAcked = st.TransmitSequence
	st.TransmitSequence = protocol.Next(st.TransmitSequence)
	st.FallbackSent = true
}
func (s *Server) sendOn(which session.Socket, p []byte, peer *net.UDPAddr) {
	conn := s.data
	if which == session.DiscoverySocket {
		conn = s.discovery
	}
	if conn == nil {
		return
	}
	if _, err := conn.WriteToUDP(p, peer); err != nil {
		s.cfg.Log.Debug("UDP send failed", map[string]any{"peer": peer, "socket": which, "error": err})
	}
}

func classify(discovery, first uint16) session.Profile {
	next := protocol.Next(discovery)
	if first == next && (first != 0 || discovery != 0) {
		return session.Modulo
	}
	if discovery == 0 && first == 0 {
		return session.Standard
	}
	if first == next {
		return session.Modulo
	}
	return session.Standard
}
func (s *Server) handleData(st *session.State, in inbound) {
	h, dh, payload, err := protocol.ParseDataPacket(in.packet)
	if err != nil {
		s.cfg.Log.Debug("dropping malformed DATA", map[string]any{"peer": in.peer, "error": err})
		return
	}

	st.Mu.Lock()
	st.Touch()
	if st.Profile == session.Pending {
		st.Profile = classify(st.DiscoverySequence, h.Sequence)
		st.ResponseSocket = in.socket
		if st.Profile == session.Modulo {
			st.ExpectedReceive = h.Sequence
			if !st.FallbackSent {
				// Account for the compatibility INFORM before acknowledging or
				// replying to the first request.
				s.sendModuloInform(st)
			}
		} else {
			st.ExpectedReceive = 0
			if st.FallbackSent {
				// The delayed compatibility INFORM is outside a standard stream.
				// Restore the canonical first DATA sequence.
				st.TransmitSequence = 0
				st.TransmitAcked = protocol.Previous(0)
				st.TxBuffer = nil
				st.FallbackSent = false
			}
		}
		s.cfg.Log.Info("session negotiated", map[string]any{"peer": in.peer, "profile": st.Profile, "response_socket": st.ResponseSocket})
	} else if !st.Streaming {
		// Strict diagnostic modes still tolerate either local endpoint. The mode
		// controls sequence interpretation, not network topology.
		st.ResponseSocket = in.socket
		if st.Profile == session.Modulo {
			st.ExpectedReceive = h.Sequence
		}
	}

	if dh.Flags&protocol.FlagACK != 0 {
		st.TransmitAcked = dh.AckSequence
		s.pruneAcked(st, dh.AckSequence)
	}
	if len(payload) == 0 {
		if dh.Flags&protocol.FlagACK == 0 {
			s.retransmit(st, dh.AckSequence)
		}
		st.Mu.Unlock()
		return
	}

	if h.Sequence != st.ExpectedReceive {
		if h.Sequence == protocol.Previous(st.ExpectedReceive) {
			s.sendACK(st, true)
			if len(st.TxBuffer) > 0 {
				s.retransmit(st, st.TxBuffer[0].Sequence)
			}
			st.Mu.Unlock()
			return
		}
		if h.Sequence == 0 && st.Profile == session.Standard {
			// A standard peer restarted on the same address. Its old handles must
			// not leak into the replacement session.
			st.Reset(session.Standard)
			st.ResponseSocket = in.socket
			st.ExpectedReceive = 0
			st.Touch()
		} else {
			s.sendACK(st, false)
			st.Mu.Unlock()
			return
		}
	}
	st.ExpectedReceive = protocol.Next(st.ExpectedReceive)
	st.Streaming = true
	s.sendACK(st, true)
	st.Mu.Unlock()

	// The per-peer worker serializes file operations. Protocol control ACK/NACK
	// packets bypass this queue, so large transfers can still advance their send
	// window while this request is being served.
	s.handleMessage(st, payload)
}

func (s *Server) responseSocket(st *session.State) session.Socket {
	if s.cfg.SinglePort {
		return session.DiscoverySocket
	}
	return st.ResponseSocket
}
func (s *Server) sendACK(st *session.State, ack bool) {
	flags := protocol.DataFlags(0)
	seq := st.ExpectedReceive
	if ack {
		flags = protocol.FlagACK
		seq = protocol.Previous(st.ExpectedReceive)
	}
	p := append(protocol.Header{Type: protocol.Data, Sequence: st.TransmitSequence}.Marshal(), protocol.DataHeader{AckSequence: seq, Flags: flags}.Marshal()...)
	s.sendOn(s.responseSocket(st), p, st.Peer)
}
