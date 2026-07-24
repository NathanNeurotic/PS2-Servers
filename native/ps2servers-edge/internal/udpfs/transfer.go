package udpfs

import (
	"encoding/binary"
	"errors"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/filesystem"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
	"os"
	"syscall"
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
	for _, p := range st.TxBuffer {
		if ((p.Sequence - from) & protocol.SequenceMask) < 2048 {
			s.sendOn(s.responseSocket(st), p.Bytes, st.Peer)
		}
	}
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

func (s *Server) waitForAck(st *session.State, target uint16) bool {
	timer := time.NewTimer(ackTimeout)
	defer timer.Stop()
	for {
		st.Mu.Lock()
		covered := ackCovers(st.TransmitAcked, target)
		st.Mu.Unlock()
		if covered {
			return true
		}
		select {
		case <-st.AckEvents:
			continue
		case <-timer.C:
			return false
		case <-s.closed:
			return false
		}
	}
}

func (s *Server) confirmWindow(st *session.State, target uint16) bool {
	for attempt := 0; attempt <= maxWindowRetries; attempt++ {
		if s.waitForAck(st, target) {
			return true
		}
		st.Mu.Lock()
		if len(st.TxBuffer) == 0 {
			st.Mu.Unlock()
			return true
		}
		from := st.TxBuffer[0].Sequence
		s.retransmit(st, from)
		st.Mu.Unlock()
		s.cfg.Log.Debug("ACK timeout; retransmitting window", map[string]any{"peer": st.Peer, "from_sequence": from, "attempt": attempt + 1})
	}
	s.cfg.Log.Warn("transfer aborted after ACK retries", map[string]any{"peer": st.Peer, "target_sequence": target})
	return false
}

func (s *Server) sendTransfer(st *session.State, header, data []byte) {
	if len(header)%4 != 0 || len(header) > 60 {
		s.cfg.Log.Error("invalid application response header", map[string]any{"bytes": len(header)})
		return
	}
	st.Mu.Lock()
	st.TxBuffer = nil
	st.Mu.Unlock()

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
			return
		}
		n := len(data)
		if n > capacity {
			n = capacity
		}
		chunk := append(append([]byte(nil), hdrBytes...), data[:n]...)
		data = data[n:]
		paddedData := pad4(append([]byte(nil), chunk[len(hdrBytes):]...))
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
		s.sendOn(s.responseSocket(st), packet, st.Peer)
		st.TransmitSequence = protocol.Next(st.TransmitSequence)
		st.Mu.Unlock()

		finalSequence = sequence
		windowPackets++
		first = false
		if len(data) > 0 && windowPackets >= windowSize {
			if !s.confirmWindow(st, finalSequence) {
				return
			}
			windowPackets = 0
		}
	}
	_ = s.confirmWindow(st, finalSequence)
}

func (s *Server) sendSimple(st *session.State, payload []byte) { s.sendTransfer(st, nil, payload) }
func errno(err error) int32 {
	if err == nil {
		return 0
	}
	if errors.Is(err, os.ErrNotExist) {
		return -int32(syscall.ENOENT)
	}
	if errors.Is(err, os.ErrPermission) || errors.Is(err, filesystem.ErrEscape) {
		return -int32(syscall.EACCES)
	}
	return -int32(syscall.EIO)
}
func statPayload(mode uint32, size uint64) []byte {
	b := make([]byte, 4+4+4+8+8)
	binary.LittleEndian.PutUint32(b, mode)
	binary.LittleEndian.PutUint32(b[4:], uint32(size))
	binary.LittleEndian.PutUint32(b[8:], uint32(size>>32))
	return b
}
func result8(t protocol.MessageType, result int32) []byte {
	b := make([]byte, 8)
	b[0] = byte(t)
	binary.LittleEndian.PutUint32(b[4:], uint32(result))
	return b
}
