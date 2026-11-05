//go:build !linux

package udpfs

import "net"

func newUDPBatchWriter(conn *net.UDPConn) udpBatchWriter {
	return func(addr *net.UDPAddr, packets [][]byte) (int, error) {
		return 0, errUDPBatchUnsupported
	}
}
