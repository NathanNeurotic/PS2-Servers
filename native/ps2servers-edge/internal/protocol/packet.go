// Package protocol implements the wire encoding shared by PS2 Servers Edge's
// UDPFS transport. All sequence fields are 12-bit and wrap at 4096.
package protocol

import (
	"encoding/binary"
	"errors"
	"fmt"
)

const (
	DefaultPort  uint16 = 0xF5F6
	ServiceUDPFS uint16 = 0xF5F5
	MaxPayload          = 1408
	SequenceMask uint16 = 0x0FFF
)

type PacketType uint8

const (
	Discovery PacketType = 0
	Inform    PacketType = 1
	Data      PacketType = 2
)

type DataFlags uint8

const (
	FlagACK DataFlags = 1
	FlagFIN DataFlags = 2
)

type MessageType uint8

const (
	OpenRequest    MessageType = 0x10
	OpenReply      MessageType = 0x11
	CloseRequest   MessageType = 0x12
	CloseReply     MessageType = 0x13
	ReadRequest    MessageType = 0x14
	SeekRequest    MessageType = 0x1A
	SeekReply      MessageType = 0x1B
	DReadRequest   MessageType = 0x1C
	DReadReply     MessageType = 0x1D
	GetStatRequest MessageType = 0x1E
	GetStatReply   MessageType = 0x1F
	ResultReply    MessageType = 0x26
)

type Header struct {
	Type     PacketType
	Sequence uint16
}

func (h Header) Marshal() []byte {
	b := make([]byte, 2)
	binary.LittleEndian.PutUint16(b, uint16(h.Type)&0xF|(h.Sequence&SequenceMask)<<4)
	return b
}
func ParseHeader(b []byte) (Header, error) {
	if len(b) < 2 {
		return Header{}, errors.New("truncated UDPRDMA header")
	}
	v := binary.LittleEndian.Uint16(b)
	return Header{Type: PacketType(v & 0xF), Sequence: (v >> 4) & SequenceMask}, nil
}

type DiscoveryHeader struct {
	ServiceID uint16
	Port      uint16
}

func (h DiscoveryHeader) Marshal() []byte {
	b := make([]byte, 4)
	binary.LittleEndian.PutUint16(b, h.ServiceID)
	binary.LittleEndian.PutUint16(b[2:], h.Port)
	return b
}
func ParseDiscoveryHeader(b []byte) (DiscoveryHeader, error) {
	if len(b) < 4 {
		return DiscoveryHeader{}, errors.New("truncated discovery header")
	}
	return DiscoveryHeader{binary.LittleEndian.Uint16(b), binary.LittleEndian.Uint16(b[2:])}, nil
}

type DataHeader struct {
	AckSequence uint16
	Flags       DataFlags
	HeaderWords uint8
	DataBytes   uint16
}

func (h DataHeader) Marshal() []byte {
	b := make([]byte, 4)
	v := uint32(h.AckSequence&SequenceMask) | uint32(h.Flags&3)<<12 | uint32(h.HeaderWords&0xF)<<14 | uint32(h.DataBytes&0x3FFF)<<18
	binary.LittleEndian.PutUint32(b, v)
	return b
}
func ParseDataHeader(b []byte) (DataHeader, error) {
	if len(b) < 4 {
		return DataHeader{}, errors.New("truncated data header")
	}
	v := binary.LittleEndian.Uint32(b)
	return DataHeader{uint16(v) & SequenceMask, DataFlags((v >> 12) & 3), uint8((v >> 14) & 0xF), uint16((v >> 18) & 0x3FFF)}, nil
}

func ParseDataPacket(packet []byte) (Header, DataHeader, []byte, error) {
	h, err := ParseHeader(packet)
	if err != nil {
		return Header{}, DataHeader{}, nil, err
	}
	if h.Type != Data {
		return Header{}, DataHeader{}, nil, fmt.Errorf("unexpected packet type %d", h.Type)
	}
	if len(packet) < 6 {
		return Header{}, DataHeader{}, nil, errors.New("truncated DATA packet")
	}
	dh, err := ParseDataHeader(packet[2:6])
	if err != nil {
		return Header{}, DataHeader{}, nil, err
	}
	n := int(dh.HeaderWords)*4 + int(dh.DataBytes)
	if n < 0 || n > len(packet)-6 {
		return Header{}, DataHeader{}, nil, errors.New("DATA lengths exceed datagram")
	}
	return h, dh, packet[6 : 6+n], nil
}

func Next(seq uint16) uint16     { return (seq + 1) & SequenceMask }
func Previous(seq uint16) uint16 { return (seq - 1) & SequenceMask }
