package protocol

import (
	"encoding/binary"
	"errors"
)

// The router status protocol. See docs/ROUTER-STATUS.md for the wire format and
// for why it is shaped this way; the short version is that a query is a
// DISCOVERY-shaped packet with a service ID that is not UDPFS, sent to the
// discovery port that is already open. Every server shipped before this feature
// -- including udpfsd upstream -- drops such a packet without replying, so a
// status query is invisible to them and the client simply reports "unknown".
//
// Nothing here touches the DISCOVERY/INFORM exchange the console uses. That
// reply is six bytes with no spare field and its exact shape is load-bearing
// for real clients, so it is left alone.

// ServiceStatus is the service ID claimed for status queries.
//
// Adjacent to ServiceUDPFS (0xF5F5) and the default port (0xF5F6), which is
// upstream's numbering range -- hence ParseStatusReply checking both the echoed
// service ID and the version byte rather than trusting that any reply to this
// query is a status reply.
const ServiceStatus uint16 = 0xF5F7

// StatusMagic identifies this protocol independently of the service ID.
//
// The number alone is not enough. 0xF5F5 is UDPFS and 0xF5F6 is the default
// port, so upstream is allocating consecutively and 0xF5F7 is the next one it
// would reach for. Validating a reply protects our client from that, but it
// does nothing about the other direction: without this tag a server here would
// answer ANY discovery packet carrying 0xF5F7, including a future udpfsd
// client asking about something else entirely. Requiring the magic means this
// server can only ever answer a question it actually understands.
//
// Safe to append because a longer packet with a service ID a server does not
// recognise is still dropped on the guard it already has -- verified against
// both implementations before this was added.
var StatusMagic = [4]byte{'P', 'S', '2', 'S'}

// StatusVersion is the payload version. A reply's first ten bytes -- header,
// service ID, magic, version -- are frozen across versions, because they are
// what a client needs in order to decide whether the rest is addressed to it
// and whether it understands the layout. Everything after may change.
const StatusVersion uint8 = 1

// StatusQueryLen is the exact length of a query.
const StatusQueryLen = 10

// StatusFixedLen is the reply length before the variable-length name.
const StatusFixedLen = 19

type ServerState uint8

const (
	StateStarting ServerState = 0
	StateReady    ServerState = 1
	// StateBusy means a transfer is in flight. It is the signal a launcher
	// uses to tell someone not to cut power to a board wired to console power,
	// which is the whole reason this protocol carries a state rather than just
	// answering "yes I am here".
	StateBusy     ServerState = 2
	StateDegraded ServerState = 3
	StateStopping ServerState = 4
)

func (s ServerState) String() string {
	switch s {
	case StateStarting:
		return "starting"
	case StateReady:
		return "ready"
	case StateBusy:
		return "busy"
	case StateDegraded:
		return "degraded"
	case StateStopping:
		return "stopping"
	default:
		// Not "unknown": an unrecognised state must read as a reason for
		// caution, never as something safe. See ParseStatusReply.
		return "degraded"
	}
}

// Service flags. Unassigned bits are zero and must be ignored, so a bit can be
// added without a version bump.
const (
	FlagServesUDPFS  uint16 = 1 << 0
	FlagServesUDPBD  uint16 = 1 << 1
	FlagServesSMB    uint16 = 1 << 2
	FlagReadOnly     uint16 = 1 << 3
	FlagDecompresses uint16 = 1 << 4
)

// StatusReply is the decoded payload.
type StatusReply struct {
	Version  uint8
	State    ServerState
	Flags    uint16
	Sessions uint16
	Uptime   uint32
	Name     string
}

// IsStatusQuery reports whether a received packet is a status query.
//
// Checked before the UDPFS service-ID guard in the discovery handler, and
// deliberately strict about length: a six-byte packet whose service ID is
// 0xF5F7 is the only thing that qualifies.
func IsStatusQuery(packet []byte) bool {
	if len(packet) < StatusQueryLen {
		return false
	}
	h, err := ParseHeader(packet)
	if err != nil || h.Type != Discovery {
		return false
	}
	dh, err := ParseDiscoveryHeader(packet[2:])
	if err != nil || dh.ServiceID != ServiceStatus {
		return false
	}
	// The magic, not just the number. See StatusMagic.
	return [4]byte(packet[6:10]) == StatusMagic
}

// MarshalStatusQuery builds the query.
func MarshalStatusQuery() []byte {
	q := append(Header{Type: Discovery, Sequence: 0}.Marshal(),
		DiscoveryHeader{ServiceID: ServiceStatus, Port: 0}.Marshal()...)
	return append(q, StatusMagic[:]...)
}

// Marshal encodes a status reply.
//
// The name is truncated rather than rejected: a display string is never a
// reason to fail a health check, and the length field caps it at 255 bytes.
func (r StatusReply) Marshal() []byte {
	name := []byte(r.Name)
	if len(name) > 255 {
		// Truncated on a byte boundary that may split a UTF-8 rune. The field
		// is for display and the length is authoritative, so a client renders
		// a replacement character at worst; validating runes here would mean
		// this could fail, which it must not.
		name = name[:255]
	}
	b := make([]byte, StatusFixedLen, StatusFixedLen+len(name))
	copy(b, Header{Type: Inform, Sequence: 0}.Marshal())
	binary.LittleEndian.PutUint16(b[2:], ServiceStatus)
	copy(b[4:8], StatusMagic[:])
	b[8] = StatusVersion
	b[9] = uint8(r.State)
	binary.LittleEndian.PutUint16(b[10:], r.Flags)
	binary.LittleEndian.PutUint16(b[12:], r.Sessions)
	binary.LittleEndian.PutUint32(b[14:], r.Uptime)
	b[18] = uint8(len(name))
	return append(b, name...)
}

var (
	ErrNotStatusReply  = errors.New("not a status reply")
	ErrStatusVersion   = errors.New("unsupported status version")
	ErrStatusTruncated = errors.New("truncated status reply")
)

// ParseStatusReply decodes and validates a reply.
//
// Rejects rather than guesses on every axis, because this runs against a
// service ID in a range this project does not own: a foreign reply must not be
// mistaken for a healthy server.
func ParseStatusReply(b []byte) (StatusReply, error) {
	if len(b) < StatusFixedLen {
		return StatusReply{}, ErrStatusTruncated
	}
	h, err := ParseHeader(b)
	if err != nil || h.Type != Inform {
		return StatusReply{}, ErrNotStatusReply
	}
	if binary.LittleEndian.Uint16(b[2:]) != ServiceStatus {
		return StatusReply{}, ErrNotStatusReply
	}
	if [4]byte(b[4:8]) != StatusMagic {
		return StatusReply{}, ErrNotStatusReply
	}
	if b[8] != StatusVersion {
		return StatusReply{}, ErrStatusVersion
	}
	r := StatusReply{
		Version:  b[8],
		State:    ServerState(b[9]),
		Flags:    binary.LittleEndian.Uint16(b[10:]),
		Sessions: binary.LittleEndian.Uint16(b[12:]),
		Uptime:   binary.LittleEndian.Uint32(b[14:]),
	}
	// An unrecognised state degrades rather than passing through. A client
	// taught only these five must not read a future "6" as something benign.
	if r.State > StateStopping {
		r.State = StateDegraded
	}
	nameLen := int(b[18])
	if nameLen > 0 {
		if len(b) < StatusFixedLen+nameLen {
			return StatusReply{}, ErrStatusTruncated
		}
		r.Name = string(b[StatusFixedLen : StatusFixedLen+nameLen])
	}
	return r, nil
}
