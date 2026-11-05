//go:build linux

// Linux batch writer adapted from pcm720/udpfsd at ee94414.
// Copyright 2026 Ivan V (pcm720). See THIRD_PARTY_NOTICES.md for the BSD license.
package udpfs

import (
	"net"

	"golang.org/x/net/ipv4"
)

func newUDPBatchWriter(conn *net.UDPConn) udpBatchWriter {
	pc := ipv4.NewPacketConn(conn)
	return func(addr *net.UDPAddr, packets [][]byte) (int, error) {
		messages := make([]ipv4.Message, len(packets))
		for i, packet := range packets {
			messages[i].Buffers = [][]byte{packet}
			messages[i].Addr = addr
		}
		n, err := pc.WriteBatch(messages, 0)
		if n < 0 {
			n = 0
		}
		return n, err
	}
}
