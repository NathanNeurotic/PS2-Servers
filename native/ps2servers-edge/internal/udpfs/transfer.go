package udpfs

import (
	"encoding/binary"
	"errors"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/filesystem"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
	"os"
	"time"
)

func (s *Server) pruneAcked(st *session.State, ack uint16) {
	kept := st.TxBuffer[:0]
	for _, p := range st.TxBuffer {
		if ((p.Sequence - ack - 1) & protocol.SequenceMask) < 2048 {
			kept = append(kept, p)
		}
	}
	st.TxBuffer = kept
}
func (s *Server) retransmit(st *session.State, from uint16) {
	var packets [][]byte
	for _, p := range st.TxBuffer {
		if ((p.Sequence - from) & protocol.SequenceMask) < 2048 {
			packets = append(packets, p.Bytes)
		}
	}
	s.sendPackets(s.responseSocket(st), packets, st.Peer)
}
func pad4(b []byte) []byte {
	for len(b)%4 != 0 {
		b = append(b, 0)
	}
	return b
}
func ackCovers(ack, target uint16) bool {
	return ((ack - target) & protocol.SequenceMask) < 2048
}

// waitForAck blocks until the console's cumulative ACK covers target, the
// quiet timeout elapses, or the server closes. It also reports whether any
// ACK-channel traffic arrived at all: the reference server treats any peer
// packet as liveness and resets its retransmit budget, so a console that
// keeps polling cannot be abandoned mid-reply.
func (s *Server) waitForAck(st *session.State, target uint16) (covered, activity bool) {
	timer := time.NewTimer(s.cfg.WindowAckTimeout)
	defer timer.Stop()
	for {
		st.Mu.Lock()
		covered := ackCovers(st.TransmitAcked, target)
		st.Mu.Unlock()
		if covered {
			return true, activity
		}
		select {
		case <-st.AckEvents:
			activity = true
			continue
		case <-timer.C:
			return false, activity
		case <-s.closed:
			return false, activity
		}
	}
}

func (s *Server) confirmWindow(st *session.State, target uint16) bool {
	quiet := 0
	for {
		st.Mu.Lock()
		acked, head := st.TransmitAcked, uint16(0)
		if len(st.TxBuffer) > 0 {
			head = st.TxBuffer[0].Sequence
		}
		st.Mu.Unlock()

		covered, activity := s.waitForAck(st, target)
		if covered {
			return true
		}

		st.Mu.Lock()
		if len(st.TxBuffer) == 0 {
			st.Mu.Unlock()
			return true
		}
		from := st.TxBuffer[0].Sequence
		s.retransmit(st, from)
		alive := activity || st.TransmitAcked != acked || st.TxBuffer[0].Sequence != head
		st.Mu.Unlock()

		if alive {
			quiet = 0
		} else {
			quiet++
		}
		if quiet >= s.cfg.MaxWindowRetries {
			break
		}
		s.cfg.Log.Debug("ACK timeout; retransmitting window", map[string]any{"peer": st.Peer, "from_sequence": from, "quiet_attempts": quiet})
	}
	s.cfg.Log.Warn("transfer aborted after ACK retries", map[string]any{"peer": st.Peer, "target_sequence": target})
	return false
}

// sendTransfer sends one reply, header first, and reports whether the console
// acknowledged all of it. It also feeds the SESSION summary: every reply
// carries its result in bytes 4..8, so one place sees every error, and the
// bytes of a READ/BREAD count only once the console confirmed them.
func (s *Server) sendTransfer(st *session.State, header, data []byte) bool {
	confirmed := s.sendTransferPackets(st, header, data)
	if s.summary != nil {
		reply := header
		if len(reply) == 0 {
			reply = data
		}
		if len(reply) >= 8 {
			kind := protocol.MessageType(reply[0])
			result := int32(binary.LittleEndian.Uint32(reply[4:8]))
			switch {
			case result < 0:
				// A path the console looked for and did not find is not an
				// error: OPL probes for optional files on every launch.
				probe := (kind == protocol.OpenReply || kind == protocol.GetStatReply) && result == -eNOENT
				if !probe {
					s.sessionRecord(st, "errors", 1)
				}
			case kind == protocol.ResultReply && result > 0 && confirmed:
				s.sessionRecord(st, "bytes", int64(result))
			}
		}
	}
	return confirmed
}

func (s *Server) sendTransferPackets(st *session.State, header, data []byte) bool {
	if len(header)%4 != 0 || len(header) > 60 {
		s.cfg.Log.Error("invalid application response header", map[string]any{"bytes": len(header)})
		return false
	}
	st.Mu.Lock()
	st.TxBuffer = nil
	st.Mu.Unlock()

	batching := s.cfg.UDPBatch && s.cfg.TxDelay == 0
	var pending [][]byte
	flush := func() {
		if len(pending) > 0 {
			s.sendPackets(s.responseSocket(st), pending, st.Peer)
			pending = nil
		}
	}

	first := true
	windowPackets := 0
	var finalSequence uint16
	for first || len(data) > 0 {
		hdrBytes := []byte(nil)
		if first {
			hdrBytes = header
		}
		capacity := protocol.MaxPayload - len(hdrBytes)
		if capacity < 0 {
			s.cfg.Log.Error("response header exceeds max payload", map[string]any{"peer": st.Peer, "header_bytes": len(hdrBytes)})
			return false
		}
		n := len(data)
		if n > capacity {
			n = capacity
		}
		paddedData := pad4(append([]byte(nil), data[:n]...))
		data = data[n:]
		payload := append(append([]byte(nil), hdrBytes...), paddedData...)
		flags := protocol.FlagACK
		if len(data) == 0 {
			flags |= protocol.FlagFIN
		}

		st.Mu.Lock()
		sequence := st.TransmitSequence
		dh := protocol.DataHeader{AckSequence: protocol.Previous(st.ExpectedReceive), Flags: flags, HeaderWords: uint8(len(hdrBytes) / 4), DataBytes: uint16(len(paddedData))}
		packet := append(protocol.Header{Type: protocol.Data, Sequence: sequence}.Marshal(), dh.Marshal()...)
		packet = append(packet, payload...)
		st.TxBuffer = append(st.TxBuffer, session.BufferedPacket{Sequence: sequence, Bytes: packet})
		if batching {
			pending = append(pending, packet)
		} else {
			s.sendOn(s.responseSocket(st), packet, st.Peer)
		}
		st.TransmitSequence = protocol.Next(st.TransmitSequence)
		st.Mu.Unlock()

		finalSequence = sequence
		windowPackets++
		first = false
		if len(data) > 0 && windowPackets >= windowSize {
			flush()
			if !s.confirmWindow(st, finalSequence) {
				return false
			}
			// Progress on a slow link: keep the console's summary alive, or
			// a transfer longer than the idle timeout is summarised before
			// its bytes are counted.
			if s.summary != nil && st.Peer != nil {
				s.summary.Touch(st.Peer.IP.String())
			}
			windowPackets = 0
		}
	}
	flush()
	return s.confirmWindow(st, finalSequence)
}

func (s *Server) sendSimple(st *session.State, payload []byte) { s.sendTransfer(st, nil, payload) }
func errno(err error) int32 {
	if err == nil {
		return 0
	}
	if errors.Is(err, os.ErrNotExist) {
		return -eNOENT
	}
	if errors.Is(err, os.ErrPermission) || errors.Is(err, filesystem.ErrEscape) {
		return -eACCES
	}
	return -eIO
}

func result8(t protocol.MessageType, result int32) []byte {
	b := make([]byte, 8)
	b[0] = byte(t)
	binary.LittleEndian.PutUint32(b[4:], uint32(result))
	return b
}
