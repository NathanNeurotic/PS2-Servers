// Package smb implements the SMBv1 dialect Open-PS2-Loader speaks.
//
// A port of smbv1_server/smbserver_opl.py, which is the implementation that has
// actually been validated against consoles. Where the two could differ, this
// follows the Python rather than the SMB specification -- the specification is
// not what the client was tested against.
//
// It exists because Edge previously served only UDPFS and UDPBD, so anyone
// running a router as their server could not use OPL's network game list or
// POPSTARTER at all. Both need SMB.
package smb

import (
	"encoding/binary"
	"errors"
	"io"
)

// Magic opens every SMB message.
var Magic = [4]byte{0xFF, 'S', 'M', 'B'}

// Command codes, and only the ones this server answers. An unlisted command is
// refused rather than guessed at.
const (
	ComClose                = 0x04
	ComCheckDirectory       = 0x10
	ComTransaction          = 0x25
	ComEcho                 = 0x2B
	ComOpenAndX             = 0x2D
	ComReadAndX             = 0x2E
	ComWriteAndX            = 0x2F
	ComTrans2               = 0x32
	ComTreeDisconnect       = 0x71
	ComNegotiate            = 0x72
	ComSessionSetupAndX     = 0x73
	ComLogoffAndX           = 0x74
	ComTreeConnectAndX      = 0x75
	ComNTCreateAndX         = 0xA2
	ComQueryInformationDisk = 0x80

	// ComNone terminates an AndX chain. This server never chains replies, so
	// every AndX response carries it.
	ComNone = 0xFF
)

// NT status codes used in replies.
const (
	StatusSuccess            uint32 = 0x00000000
	StatusNotImplemented     uint32 = 0xC0000002
	StatusAccessDenied       uint32 = 0xC0000022
	StatusObjectNameNotFound uint32 = 0xC0000034
)

const (
	// Flags1Reply is SERVER_TO_REDIR: this message is a response.
	Flags1Reply = 0x80
	// Flags2 is sent on every reply. 0x4001 = LONG_NAMES | NT_STATUS.
	Flags2 = 0x4001
)

// HeaderLen is the fixed SMB header size.
const HeaderLen = 32

// DefaultPort matches the desktop build rather than SMB's standard 445.
//
// 445 was considered and rejected. The appeal was that a router has no Windows
// LanmanServer holding the port, so OPL would need no port change at all --
// but Windows was never the only obstacle. Ports below 1024 are privileged on
// Linux too, and both shipped deployments run Edge unprivileged: the OpenWrt
// init drops to the ps2edge user, and so does the systemd unit. Defaulting to
// 445 would therefore mean running SMB as root, or adding libcap to a package
// whose whole point is fitting a 16 MB flash board.
//
// So it would not remove an explanation, it would replace "set OPL's SMB port
// to 1111" with "grant a capability or run as root" -- harder to explain, and
// it fails at bind time with EACCES rather than working. 1111 works
// unprivileged on every platform Edge runs on and matches what every existing
// document and tester already expects.
//
// An operator who wants 445 can still have it with --port 445, running that
// instance privileged. That is documented rather than defaulted.
const DefaultPort = 1111

// MaxMessage bounds one SMB message, and with it this server's whole
// per-connection memory cost.
//
// The framing length field is 24 bits, so a peer could otherwise announce 16
// MiB and have the server allocate it before a byte is validated -- the same
// shape of problem as the transfer caps in internal/udpfs.
//
// 128 KiB is chosen against what the protocol actually does rather than
// against the field width. The reference server clamps a READ_ANDX to
// min(maxcount, 0xFFFF), so no reply can exceed 64 KiB of file data; and a
// WRITE_ANDX payload is a slice of the message already received, not a fresh
// allocation sized from the client's declared count. So one message is the
// only thing a connection holds, and twice the largest real one is ample.
//
// This is deliberately a constant rather than being derived from MemTotal the
// way the UDPFS caps are. Those had to scale because a single request could
// reserve 8 MiB eagerly; here the worst case is MaxConnections * MaxMessage =
// 2 MiB, which no board this runs on will notice. A number that small is
// better fixed and easy to reason about than correct-looking arithmetic.
const MaxMessage = 128 << 10

// MaxConnections bounds concurrent SMB sessions.
//
// The reference server spawns an unbounded thread per accepted socket, which
// is fine on a desktop and poor hygiene on a 32 MB router. This is insurance
// against that, not a tuned figure: a console opens one connection, POPSTARTER
// another, so sixteen is far above any real use while keeping the worst case
// bounded at MaxConnections * MaxMessage.
//
// Goroutines themselves are not the concern -- they start on a 2 KiB stack, so
// even at this limit the stacks total 32 KiB. It is the message buffers that
// are worth bounding.
const MaxConnections = 16

// Per-connection caps on state a client can accumulate without ever closing
// anything.
//
// MaxMessage bounds one message; it does not bound what a handler REMEMBERS.
// Each of these is state the protocol only frees on an explicit request the
// client is free never to send, so without a ceiling a peer grows them until
// the board dies -- measured at tens of megabytes within a second of ordinary
// traffic on one socket. The reference has the same lifecycles and no ceilings,
// which is survivable on a desktop and is not here.
//
// Each is enforced by evicting the oldest rather than by refusing: a console
// that legitimately exceeds one of these should keep working, and the values
// are far above what any real client uses.
const (
	// MaxSearches bounds live directory listings. A search is freed only by a
	// FIND_NEXT2 issued AFTER the one that reported EndOfSearch -- a call a
	// well-behaved client has no reason to make -- so even OPL leaks one per
	// directory it browses.
	MaxSearches = 16
	// MaxOpenFiles bounds handles, each of which holds an open OS file
	// descriptor. Matches the udpfs limit.
	MaxOpenFiles = 64
	// MaxTrees bounds connected trees. Freed only by TREE_DISCONNECT.
	MaxTrees = 16
)

var (
	// ErrKeepAlive reports a session keep-alive, which carries no SMB message
	// and must not be treated as a short read or as EOF.
	ErrKeepAlive = errors.New("smb: session keep-alive")
	// ErrOversize reports a length field beyond MaxMessage.
	ErrOversize = errors.New("smb: message too large")
)

// WriteMessage frames one SMB message onto a direct-TCP connection.
//
// NetBIOS-over-TCP session framing: byte 0 is 0x00 for a session message and
// bytes 1-3 are a 24-bit length. That length is the ONLY big-endian field in
// the entire protocol; everything inside the message is little-endian.
func WriteMessage(w io.Writer, msg []byte) error {
	if len(msg) > 0xFFFFFF {
		return ErrOversize
	}
	var hdr [4]byte
	hdr[0] = 0x00
	hdr[1] = byte(len(msg) >> 16)
	hdr[2] = byte(len(msg) >> 8)
	hdr[3] = byte(len(msg))
	if _, err := w.Write(hdr[:]); err != nil {
		return err
	}
	_, err := w.Write(msg)
	return err
}

// ReadMessage reads the next SMB message.
//
// Returns ErrKeepAlive for a keep-alive or an empty session message, so a
// caller can tell "nothing to do, connection still good" from io.EOF. Getting
// that distinction wrong drops a console mid-session, since OPL keeps the
// connection open across a whole game load.
//
// A non-zero body on a non-session-message type is consumed and discarded
// rather than left in the stream, which would desynchronise every subsequent
// read.
func ReadMessage(r io.Reader) ([]byte, error) {
	var hdr [4]byte
	if _, err := io.ReadFull(r, hdr[:]); err != nil {
		return nil, err
	}
	length := int(hdr[1])<<16 | int(hdr[2])<<8 | int(hdr[3])
	if length > MaxMessage {
		return nil, ErrOversize
	}
	if hdr[0] != 0x00 {
		if length > 0 {
			if _, err := io.CopyN(io.Discard, r, int64(length)); err != nil {
				return nil, err
			}
		}
		return nil, ErrKeepAlive
	}
	if length == 0 {
		return nil, ErrKeepAlive
	}
	msg := make([]byte, length)
	if _, err := io.ReadFull(r, msg); err != nil {
		return nil, err
	}
	return msg, nil
}

// Header is the parsed 32-byte SMB header.
type Header struct {
	Command uint8
	Status  uint32
	TID     uint16
	PID     uint16
	UID     uint16
	MID     uint16
}

// PackHeader builds a reply header.
//
// The NTSTATUS is split across the legacy DOS-error fields rather than written
// as one 32-bit value: ErrorClass at byte 5 takes the low byte, byte 6 is
// reserved, and ErrorCode at bytes 7-8 takes the HIGH half. That is what the
// Python server does and what OPL was tested against, so it is reproduced
// exactly -- writing the status as a little-endian uint32 at offset 5, which is
// the obvious reading of the spec, puts different bytes on the wire.
func PackHeader(command uint8, status uint32, tid, pid, uid, mid uint16) []byte {
	h := make([]byte, HeaderLen)
	copy(h[0:4], Magic[:])
	h[4] = command
	h[5] = byte(status & 0xFF)
	h[6] = 0
	binary.LittleEndian.PutUint16(h[7:], uint16((status>>16)&0xFFFF))
	h[9] = Flags1Reply
	binary.LittleEndian.PutUint16(h[10:], Flags2)
	// 12..23 are security features / reserved and stay zero.
	binary.LittleEndian.PutUint16(h[24:], tid)
	binary.LittleEndian.PutUint16(h[26:], pid)
	binary.LittleEndian.PutUint16(h[28:], uid)
	binary.LittleEndian.PutUint16(h[30:], mid)
	return h
}

// ParseHeader decodes a request header.
func ParseHeader(msg []byte) (Header, error) {
	if len(msg) < HeaderLen {
		return Header{}, errors.New("smb: short header")
	}
	if msg[0] != Magic[0] || msg[1] != Magic[1] || msg[2] != Magic[2] || msg[3] != Magic[3] {
		return Header{}, errors.New("smb: bad magic")
	}
	return Header{
		Command: msg[4],
		Status:  uint32(msg[5]) | uint32(binary.LittleEndian.Uint16(msg[7:]))<<16,
		TID:     binary.LittleEndian.Uint16(msg[24:]),
		PID:     binary.LittleEndian.Uint16(msg[26:]),
		UID:     binary.LittleEndian.Uint16(msg[28:]),
		MID:     binary.LittleEndian.Uint16(msg[30:]),
	}, nil
}
