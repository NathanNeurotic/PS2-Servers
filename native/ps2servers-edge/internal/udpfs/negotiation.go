package udpfs

import (
	"errors"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
	"net"
	"time"
)

var errUDPBatchUnsupported = errors.New("UDP batching is unsupported on this platform")

func (s *Server) handleControl(st *session.State, dh protocol.DataHeader) {
	st.Mu.Lock()
	defer st.Mu.Unlock()
	st.Touch()
	if dh.Flags&protocol.FlagACK != 0 {
		if protocol.Between(st.TransmitAcked, dh.AckSequence, protocol.Previous(st.TransmitSequence)) {
			st.TransmitAcked = dh.AckSequence
			s.pruneAcked(st, dh.AckSequence)
		}
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
		s.stats.malformedDatagrams.Add(1)
		s.cfg.Log.Debug("dropping invalid DISCOVERY", map[string]any{
			"peer": inboundPeerString(in.peer), "socket": in.socket, "bytes": len(in.packet), "sequence": h.Sequence,
			"reason": "truncated discovery header",
		})
		return
	}
	dh, err := protocol.ParseDiscoveryHeader(in.packet[2:])
	if err != nil {
		s.stats.malformedDatagrams.Add(1)
		s.cfg.Log.Debug("dropping invalid DISCOVERY", map[string]any{
			"peer": inboundPeerString(in.peer), "socket": in.socket, "bytes": len(in.packet), "sequence": h.Sequence,
			"reason": "malformed discovery header", "error": err,
		})
		return
	}
	if dh.ServiceID != protocol.ServiceUDPFS {
		s.cfg.Log.Debug("dropping invalid DISCOVERY", map[string]any{
			"peer": inboundPeerString(in.peer), "socket": in.socket, "bytes": len(in.packet), "sequence": h.Sequence,
			"service_id": dh.ServiceID, "service_port": dh.Port, "reason": "unsupported service",
		})
		return
	}
	// First contact from a console is the most useful line this server emits.
	// It proves packets are arriving and names the address they came from,
	// which separates "the PS2 never reached us" -- wrong subnet, firewall,
	// wrong interface -- from "it reached us but the reply went astray".
	// Without it a misconfigured setup is silent on both sides.
	//
	// Only the first discovery per peer is announced at Info: both client
	// families keep broadcasting discovery for the life of a transfer, so
	// logging every one would bury the transfer log. --verbose shows them all.
	s.stats.discovery.Add(1)

	s.sessionsMu.Lock()
	_, known := s.sessions[in.peer.String()]
	s.sessionsMu.Unlock()

	w := s.getWorker(in.peer)
	if w == nil {
		return
	}
	if !known {
		s.cfg.Log.Info("DISCOVERY received, console found this server",
			map[string]any{
				"peer": in.peer.String(), "socket": in.socket, "sequence": h.Sequence,
				"service_id": dh.ServiceID, "service_port": dh.Port,
			})
	} else {
		// Keep seq as the legacy alias for existing structured-log consumers.
		s.cfg.Log.Debug("DISCOVERY", map[string]any{
			"peer": in.peer.String(), "socket": in.socket, "seq": h.Sequence, "sequence": h.Sequence,
			"service_id": dh.ServiceID, "service_port": dh.Port,
		})
	}

	st := w.state
	st.Mu.Lock()
	defer st.Mu.Unlock()

	quiet := time.Since(st.LastActivity)
	s.cfg.Log.Debug("discovery negotiation input", map[string]any{
		"peer": in.peer.String(), "socket": in.socket, "sequence": h.Sequence,
		"profile": st.Profile, "streaming": st.Streaming, "quiet": quiet.String(), "known": known,
	})
	// Discovery never proves that an established stream ended. Record a
	// possible replacement sequence and resolve it on subsequent payload DATA.
	if st.Streaming {
		st.Touch()
		if h.Sequence == 0 {
			st.PendingZeroDiscovery = time.Now()
			st.PendingDiscoverySequence = nil
		} else {
			sequence := h.Sequence
			st.PendingDiscoverySequence = &sequence
			st.PendingZeroDiscovery = time.Time{}
		}
		// A console that keeps broadcasting discovery mid-reply is alive. Surface
		// the validated packet as liveness without acknowledging TX progress.
		select {
		case st.AckEvents <- session.AckEvent{ACK: false, Sequence: h.Sequence}:
		default:
		}
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
	st.Touch()

	switch s.cfg.ProtocolMode {
	case session.Standard:
		st.Profile = session.Standard
		s.sendStandardInform(st)
	case session.Modulo:
		st.Profile = session.Modulo
		s.sendModuloInform(st)
	case session.Pending:
		// A sequence-zero discovery used to commit straight to Standard and
		// skip the fallback. That silently broke the shared conformance case
		// "modulo-fresh" (discovery 0, first data 1): a fresh Modulo client
		// waits for the compatibility INFORM before sending anything, so it
		// never got one, and because Profile was already Standard rather than
		// Pending the classify() path in handleData never ran either -- the
		// session stayed misclassified for its whole life. classify() itself
		// was correct, which is why the unit tests passed while a real client
		// timed out.
		//
		// Staying Pending is safe for standard clients: scheduleFallback bails
		// when the generation moved on, when Streaming, or when the first DATA
		// has already resolved Profile away from Pending -- which is exactly
		// what a standard client does. Re-broadcast during a live transfer is
		// handled by the Streaming guard above, before this switch.
		st.Profile = session.Pending
		s.sendStandardInform(st)
		s.scheduleFallback(st)
	}
}

func (s *Server) scheduleFallback(st *session.State) {
	generation := st.FallbackGeneration
	delay := s.cfg.FallbackDelay
	if delay == 0 {
		delay = defaultFallback
	}
	go func(peerStr string, gen uint64) {
		time.Sleep(delay)
		s.sessionsMu.Lock()
		w := s.sessions[peerStr]
		s.sessionsMu.Unlock()
		if w == nil {
			return
		}
		state := w.state
		state.Mu.Lock()
		defer state.Mu.Unlock()
		if state.FallbackGeneration != gen || state.Profile != session.Pending || state.Streaming {
			return
		}
		s.sendModuloInform(state)
	}(inboundPeerString(st.Peer), generation)
}

func inboundPeerString(p *net.UDPAddr) string {
	if p == nil {
		return ""
	}
	return p.String()
}

func (s *Server) sendStandardInform(st *session.State) {
	_, data := s.Addr()
	if data == nil {
		return
	}
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
	if s.cfg.TxDelay > 0 {
		time.Sleep(s.cfg.TxDelay)
	}
	n, err := conn.WriteToUDP(p, peer)
	if err != nil {
		if s.cfg.MetricsPeriod > 0 {
			s.stats.txSendErrors.Add(1)
		}
		fields := datagramTraceFields(conn, which, peer, p, len(p))
		fields["written_bytes"] = n
		fields["error"] = err
		s.cfg.Log.Debug("UDP send failed", fields)
		return
	}
	s.recordSendSuccess(conn, which, peer, p, n)
}

func (s *Server) recordSendSuccess(conn *net.UDPConn, which session.Socket, peer *net.UDPAddr, p []byte, n int) {
	if s.cfg.MetricsPeriod > 0 {
		s.stats.recordTX(which, n)
	}
	if s.cfg.Log.Verbose {
		s.cfg.Log.Debug("UDP TX", datagramTraceFields(conn, which, peer, p, n))
	}
}

func (s *Server) sendPackets(which session.Socket, packets [][]byte, peer *net.UDPAddr) {
	if len(packets) == 0 {
		return
	}
	if len(packets) == 1 || !s.cfg.UDPBatch || s.cfg.TxDelay > 0 {
		for _, packet := range packets {
			s.sendOn(which, packet, peer)
		}
		return
	}
	conn := s.data
	if which == session.DiscoverySocket {
		conn = s.discovery
	}
	if conn == nil {
		return
	}
	s.batchWriteMu.Lock()
	if s.batchWrite == nil || (s.batchWriteConn != nil && s.batchWriteConn != conn) {
		s.batchWrite = newUDPBatchWriter(conn)
		s.batchWriteConn = conn
	}
	writeBatch := s.batchWrite
	s.batchWriteMu.Unlock()
	n, err := writeBatch(peer, packets)
	if n < 0 {
		n = 0
	}
	if n > len(packets) {
		n = len(packets)
	}
	for _, packet := range packets[:n] {
		s.recordSendSuccess(conn, which, peer, packet, len(packet))
	}
	if err == errUDPBatchUnsupported {
		for _, packet := range packets[n:] {
			s.sendOn(which, packet, peer)
		}
		return
	}
	if err != nil || n < len(packets) {
		if err != nil && s.cfg.MetricsPeriod > 0 {
			s.stats.txSendErrors.Add(1)
		}
		s.cfg.Log.Debug("UDP batch send incomplete", map[string]any{
			"peer": peer.String(), "socket": which, "packets": len(packets), "written_packets": n, "error": err,
		})
		for _, packet := range packets[n:] {
			s.sendOn(which, packet, peer)
		}
	}
}

func classify(discovery, first uint16) session.Profile {
	next := protocol.Next(discovery)
	if first == next && (first != 0 || discovery != 0) {
		return session.Modulo
	}
	return session.Standard
}
func (s *Server) handleData(st *session.State, in inbound) {
	h, dh, payload, err := protocol.ParseDataPacket(in.packet)
	if err != nil {
		s.stats.malformedDatagrams.Add(1)
		fields := map[string]any{
			"peer": inboundPeerString(in.peer), "socket": in.socket, "bytes": len(in.packet), "error": err,
		}
		if parsed, headerErr := protocol.ParseHeader(in.packet); headerErr == nil {
			fields["type"] = parsed.Type
			fields["sequence"] = parsed.Sequence
		}
		s.cfg.Log.Debug("dropping malformed DATA", fields)
		return
	}

	st.Mu.Lock()
	st.Touch()

	// Resolve an ambiguous sequence-zero discovery only when payload DATA
	// arrives. Continuing at the old expected sequence proves it was background
	// rediscovery and keeps the live stream intact. Starting at 0 (Standard) or
	// 1 (Modulo) while the old stream expected something else proves a new
	// loader replaced the client on the same UDP endpoint. Reset before the old
	// ExpectedReceive can NACK the replacement forever. Hardware reproduction:
	// RiptOPL expected 2213, Neutrino DISCOVERY 0, first DATA 1.
	if len(payload) > 0 && !st.PendingZeroDiscovery.IsZero() {
		switch {
		case h.Sequence == st.ExpectedReceive:
			st.PendingZeroDiscovery = time.Time{}
			s.cfg.Log.Debug("seq0 discovery resolved to existing stream", map[string]any{
				"peer": inboundPeerString(in.peer), "socket": in.socket,
				"sequence": h.Sequence, "profile": st.Profile,
			})
		case h.Sequence == 0 || h.Sequence == 1:
			expected := st.ExpectedReceive
			profile := session.Pending
			if s.cfg.ProtocolMode == session.Standard || s.cfg.ProtocolMode == session.Modulo {
				profile = s.cfg.ProtocolMode
			}
			// Hot same-endpoint loader replacement: reset transport only.
			// Neutrino's UDPFS FILEID backend carries the already-open game
			// handle across this transition and will BREAD it after negotiation.
			// The kept handles are marked inherited, so the new loader can
			// reopen for writing a file the old one was writing (openForWrite).
			st.HandOff(profile)
			st.DiscoverySequence = 0
			st.FallbackGeneration++
			st.ResponseSocket = in.socket
			st.Touch()
			s.stats.sequenceResets.Add(1)
			s.cfg.Log.Info("seq0 discovery resolved to replacement session", map[string]any{
				"peer": inboundPeerString(in.peer), "socket": in.socket,
				"old_expected": expected, "first_data_sequence": h.Sequence,
				"profile": profile,
			})
		}
	}

	if len(payload) > 0 && st.PendingDiscoverySequence != nil {
		discovery := *st.PendingDiscoverySequence
		switch {
		case h.Sequence == st.ExpectedReceive:
			st.PendingDiscoverySequence = nil
		case h.Sequence == protocol.Next(discovery):
			expected := st.ExpectedReceive
			profile := session.Pending
			if s.cfg.ProtocolMode == session.Standard || s.cfg.ProtocolMode == session.Modulo {
				profile = s.cfg.ProtocolMode
			}
			st.HandOff(profile)
			st.DiscoverySequence = discovery
			st.FallbackGeneration++
			st.ResponseSocket = in.socket
			st.Touch()
			s.stats.sequenceResets.Add(1)
			s.cfg.Log.Info("nonzero discovery resolved to replacement session", map[string]any{
				"peer": inboundPeerString(in.peer), "socket": in.socket,
				"old_expected": expected, "first_data_sequence": h.Sequence,
				"profile": profile,
			})
		}
	}

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
		fields := map[string]any{
			"peer": inboundPeerString(in.peer), "profile": st.Profile, "response_socket": st.ResponseSocket,
			"discovery_sequence": st.DiscoverySequence, "first_data_sequence": h.Sequence,
			"ack": dh.AckSequence, "flags": dh.Flags, "data_bytes": dh.DataBytes,
		}
		if len(payload) > 0 {
			fields["opcode"] = payload[0]
		}
		s.cfg.Log.Info("session negotiated", fields)
	} else if !st.Streaming {
		// Strict diagnostic modes still tolerate either local endpoint. The mode
		// controls sequence interpretation, not network topology.
		st.ResponseSocket = in.socket
		if st.Profile == session.Modulo {
			st.ExpectedReceive = h.Sequence
		}
	}

	if dh.Flags&protocol.FlagACK != 0 {
		if protocol.Between(st.TransmitAcked, dh.AckSequence, protocol.Previous(st.TransmitSequence)) {
			st.TransmitAcked = dh.AckSequence
			s.pruneAcked(st, dh.AckSequence)
		}
	}
	if len(payload) == 0 {
		if dh.Flags&protocol.FlagACK == 0 {
			s.retransmit(st, dh.AckSequence)
		}
		st.Mu.Unlock()
		return
	}

	if h.Sequence != st.ExpectedReceive {
		expected := st.ExpectedReceive
		s.stats.sequenceMismatches.Add(1)
		if h.Sequence == protocol.Previous(st.ExpectedReceive) {
			s.cfg.Log.Debug("peer sequence mismatch", map[string]any{
				"peer": inboundPeerString(in.peer), "socket": in.socket, "profile": st.Profile,
				"expected": expected, "received": h.Sequence, "ack": dh.AckSequence,
				"flags": dh.Flags, "outcome": "duplicate_reack",
			})
			s.sendACK(st, true)
			if len(st.TxBuffer) > 0 {
				s.retransmit(st, st.TxBuffer[0].Sequence)
			}
			st.Mu.Unlock()
			return
		}
		if h.Sequence == 0 {
			// A DATA-first restart still owns inherited FILEID handles. Reset only
			// transport and mark those handles for the replacement loader.
			prof := st.Profile
			if prof == "" || prof == session.Pending {
				prof = session.Standard
			}
			s.cfg.Log.Debug("peer sequence mismatch", map[string]any{
				"peer": inboundPeerString(in.peer), "socket": in.socket, "profile": prof,
				"expected": expected, "received": h.Sequence, "ack": dh.AckSequence,
				"flags": dh.Flags, "outcome": "session_reset",
			})
			st.HandOff(prof)
			st.ResponseSocket = in.socket
			st.ExpectedReceive = 0
			st.Touch()
			s.stats.sequenceResets.Add(1)
			s.cfg.Log.Info("peer sequence reset (seq 0 received)", map[string]any{
				"peer": inboundPeerString(in.peer), "socket": in.socket, "profile": prof,
				"expected": expected, "received": h.Sequence, "outcome": "session_reset",
			})
		} else {
			s.cfg.Log.Debug("peer sequence mismatch", map[string]any{
				"peer": inboundPeerString(in.peer), "socket": in.socket, "profile": st.Profile,
				"expected": expected, "received": h.Sequence, "ack": dh.AckSequence,
				"flags": dh.Flags, "outcome": "nack",
			})
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
