// Package statuslistener answers router status queries for a server that has
// no UDP socket of its own.
//
// UDPFS handles these inline, because the query arrives on the discovery
// socket it already owns. SMB is TCP and UDPBD listens on its own port, so
// without this they answer nothing -- and a router serving SMB is exactly the
// case the status protocol was asked for: the launcher goes dark on the one
// service the board exists to provide.
//
// See docs/ROUTER-STATUS.md for the wire format.
package statuslistener

import (
	"net"
	"time"

	edgelog "github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
)

// State is what the server reports when asked. Called per query, so it must be
// cheap and safe to call from another goroutine.
type State func() protocol.StatusReply

// Listener answers status queries on one UDP port.
type Listener struct {
	conn  *net.UDPConn
	state State
	log   *edgelog.Logger
	done  chan struct{}
}

// Start binds the port and begins answering.
//
// A bind failure is returned rather than swallowed, but callers are expected
// to treat it as non-fatal: the standard status port is the UDPFS discovery
// port, so on a box already running Edge's udpfs the address is legitimately
// taken. Refusing to start SMB because a status listener could not bind would
// take the actual service down over a diagnostic one.
func Start(bind string, port int, state State, log *edgelog.Logger) (*Listener, error) {
	ip := net.ParseIP(bind)
	conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: ip, Port: port})
	if err != nil {
		return nil, err
	}
	l := &Listener{conn: conn, state: state, log: log, done: make(chan struct{})}
	go l.serve()
	return l, nil
}

// Addr reports the bound address.
func (l *Listener) Addr() *net.UDPAddr {
	if l == nil || l.conn == nil {
		return nil
	}
	return l.conn.LocalAddr().(*net.UDPAddr)
}

// Close stops the listener. Safe on a nil receiver, so a caller that treated a
// bind failure as non-fatal can defer Close unconditionally.
func (l *Listener) Close() error {
	if l == nil || l.conn == nil {
		return nil
	}
	select {
	case <-l.done:
	default:
		close(l.done)
	}
	return l.conn.Close()
}

func (l *Listener) serve() {
	// Small: a status query is ten bytes, and anything larger is not one.
	// Sizing this to the protocol rather than to a datagram means a flood
	// cannot make the server allocate.
	buf := make([]byte, 64)
	for {
		n, peer, err := l.conn.ReadFromUDP(buf)
		if err != nil {
			select {
			case <-l.done:
				return
			default:
			}
			// A read error on a UDP socket is usually one bad datagram, not a
			// dead socket -- on Windows an ICMP port-unreachable from a peer
			// that has gone away surfaces here. Keep serving.
			continue
		}
		if !protocol.IsStatusQuery(buf[:n]) {
			continue
		}
		reply := l.state()
		if _, err := l.conn.WriteToUDP(reply.Marshal(), peer); err != nil && l.log != nil {
			l.log.Debug("status reply failed", map[string]any{
				"peer": peer.String(), "error": err.Error()})
		}
	}
}

// Uptime is a convenience for building a reply's uptime field.
func Uptime(started time.Time) uint32 {
	if started.IsZero() {
		return 0
	}
	return uint32(time.Since(started).Seconds())
}
