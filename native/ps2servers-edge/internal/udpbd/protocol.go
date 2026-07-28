// Package udpbd implements the UDPBD v2 block-device protocol for PS2 Servers
// Edge, so Edge is not a downgrade from the Desktop/Core server for people who
// serve a disk image rather than a folder.
//
// UDPBD is a different level of abstraction from UDPFS. UDPFS is a network file
// share -- the console asks for a named file and a byte range. UDPBD is a
// network hard drive: the console asks for numbered 512-byte sectors and the
// server has no idea what is inside them, because the filesystem lives in the
// image and only the PS2 interprets it.
//
// The wire format here is ported from this repository's hardware-validated
// Python implementation (udpbd_server/udpbd_server.py), which is itself an
// independent reimplementation of Rick Gaiser's published protocol. No upstream
// source is copied; only the wire format is reproduced so PS2 clients
// interoperate. The bit packing and the RDMA block-size table below have to
// match the Python server byte for byte -- udpbd_server/selftest.py asserts the
// optimizer against the same table.
package udpbd

import "encoding/binary"

const (
	// Port is fixed: the PS2 broadcasts here to find a server.
	Port = 0xBDBD

	CmdInfo      = 0x00 // client -> server
	CmdInfoReply = 0x01 // server -> client
	CmdRead      = 0x02 // client -> server
	CmdReadRDMA  = 0x03 // server -> client
	CmdWrite     = 0x04 // client -> server
	CmdWriteRDMA = 0x05 // client -> server
	CmdWriteDone = 0x06 // server -> client

	SectorSize = 512
	RecvBufLen = 2048

	// Header (2 bytes) + block_type (4 bytes) = 6 bytes of RDMA overhead.
	rdmaMaxPayload = 1472 - 2 - 4 // 1466
)

// packHeader builds the 16-bit little-endian header bitfield:
// cmd:5, cmdid:3, cmdpkt:8.
func packHeader(cmd, cmdid, cmdpkt uint8) []byte {
	v := uint16(cmd&0x1F) | uint16(cmdid&0x07)<<5 | uint16(cmdpkt)<<8
	b := make([]byte, 2)
	binary.LittleEndian.PutUint16(b, v)
	return b
}

// headerCmd reads just the command nibble, for dispatch.
func headerCmd(p []byte) uint8 { return p[0] & 0x1F }

// unpackHeader returns cmd, cmdid, cmdpkt.
func unpackHeader(p []byte) (cmd, cmdid, cmdpkt uint8) {
	v := binary.LittleEndian.Uint16(p)
	return uint8(v & 0x1F), uint8((v >> 5) & 0x07), uint8(v >> 8)
}

// packBlockType builds the 32-bit little-endian block descriptor:
// block_shift:4, block_count:9.
func packBlockType(blockShift uint8, blockCount uint16) []byte {
	v := uint32(blockShift&0x0F) | uint32(blockCount&0x1FF)<<4
	b := make([]byte, 4)
	binary.LittleEndian.PutUint32(b, v)
	return b
}

// unpackBlockType reads the block descriptor at the given offset.
func unpackBlockType(p []byte, offset int) (blockShift uint8, blockCount uint16) {
	v := binary.LittleEndian.Uint32(p[offset:])
	return uint8(v & 0x0F), uint16((v >> 4) & 0x1FF)
}

// ceilDiv is integer ceiling division for non-negative values.
func ceilDiv(a, b int64) int64 { return (a + b - 1) / b }

// blockShiftForSectors picks the largest block size that still yields the
// minimum packet count: fewest packets is least overhead, and larger blocks are
// faster for the PS2 to consume.
//
// The 1440 / 1024 / 1280 / 1408 thresholds and the 7 / 6 / 5 / 3 shifts are not
// arbitrary -- they reproduce upstream's table exactly, and selftest.py pins
// them. Changing one without the other silently degrades throughput on real
// hardware rather than failing visibly.
func blockShiftForSectors(sectors int64) uint8 {
	size := sectors * SectorSize
	packetsMin := ceilDiv(size, 1440)
	switch {
	case ceilDiv(size, 1024) == packetsMin:
		return 7 // 512-byte blocks
	case ceilDiv(size, 1280) == packetsMin:
		return 6 // 256-byte blocks
	case ceilDiv(size, 1408) == packetsMin:
		return 5 // 128-byte blocks
	default:
		return 3 // 32-byte blocks
	}
}

// blockGeometry derives the sizes that follow from a shift.
func blockGeometry(shift uint8) (blockSize, blocksPerPacket, blocksPerSector int) {
	blockSize = 1 << (shift + 2)
	return blockSize, rdmaMaxPayload / blockSize, SectorSize / blockSize
}
