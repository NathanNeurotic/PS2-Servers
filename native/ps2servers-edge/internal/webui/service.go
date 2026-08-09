package webui

import (
	"fmt"
	"net"
	"os"
	"os/exec"
	"sort"
	"time"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
)

// Services the dashboard knows about, in the order the cards appear.
var dashboardServices = []string{"udpfs", "smb", "udpbd"}

// defaultStatusPort is the UDPFS discovery port, which is also the standard
// status port every subcommand resolves --status-port -1 to.
const defaultStatusPort = 0xF5F6

// serviceFlag maps a dashboard service to the capability bit a reply sets when
// that service is the one answering.
//
// This is the discriminator, not the reply's name. Every subcommand reports the
// same display name ("PS2 Servers Edge"), so the substring match this used to do
// -- looking for "udpfs"/"smb"/"udpbd" inside "PS2 Servers Edge" -- could never
// match, and the dashboard showed all three services as unknown in every
// configuration, including with a healthy server answering on the same port.
var serviceFlag = map[string]uint16{
	"udpfs": protocol.FlagServesUDPFS,
	"smb":   protocol.FlagServesSMB,
	"udpbd": protocol.FlagServesUDPBD,
}

// restartTargets is the set HandleRestart accepts. An unvalidated value reaches
// exec.Command as a systemd instance name, which lets a caller start units this
// server never meant to offer.
var restartTargets = map[string]bool{
	"all": true, "udpfs": true, "smb": true, "udpbd": true,
}

// ServiceManager abstracts how the web UI restarts Edge services and queries
// their status.  Three implementations are auto-detected: OpenWrt (procd),
// systemd, and a generic fallback that logs instructions.
type ServiceManager interface {
	// Restart returns the set of services actually restarted, which is not
	// always the set asked for -- see openwrtManager.
	Restart(service string) ([]string, error)
	Status(ports map[string]int) (map[string]ServiceStatus, error)
}

// ServiceStatus is returned by the /api/status endpoint and consumed by the
// dashboard cards.
type ServiceStatus struct {
	Running  bool   `json:"running"`
	State    string `json:"state"`
	Sessions int    `json:"sessions"`
	Uptime   uint32 `json:"uptime"`
}

func unknownStatus() ServiceStatus {
	return ServiceStatus{Running: false, State: "unknown"}
}

// DetectServiceManager checks the host environment and returns the most
// specific manager available.
func DetectServiceManager(log *logging.Logger) ServiceManager {
	if _, err := os.Stat("/etc/init.d/ps2servers-edge"); err == nil {
		return &openwrtManager{log: log}
	}
	if _, err := exec.LookPath("systemctl"); err == nil {
		return &systemdManager{log: log}
	}
	return &genericManager{log: log}
}

// runCmd reports a failed command with its stderr attached.
//
// exec.Cmd.Run alone yields "exit status 1", which is what the API used to
// surface: enough to know something broke and nothing about what, which matters
// most here because the likely cause is a permission the service user does not
// have.
func runCmd(name string, args ...string) error {
	cmd := exec.Command(name, args...)
	out, err := cmd.CombinedOutput()
	if err == nil {
		return nil
	}
	if len(out) == 0 {
		return err
	}
	return fmt.Errorf("%w: %s", err, string(out))
}

// --- OpenWrt (procd) ---

type openwrtManager struct {
	log *logging.Logger
}

// Restart restarts every enabled instance, whatever was asked for.
//
// The init script is the only per-package control procd exposes here, and it
// covers all four instances. Restarting one is possible -- SIGTERM the instance
// over ubus and let respawn bring it back -- but that has never run on a real
// router, and a restart button that silently does nothing is worse than one
// that does too much. So this reports what it actually did and the UI says so.
func (m *openwrtManager) Restart(service string) ([]string, error) {
	if err := runCmd("/etc/init.d/ps2servers-edge", "restart"); err != nil {
		return nil, err
	}
	if m.log != nil {
		m.log.Info("restarted all Edge services", map[string]any{"requested": service})
	}
	return append([]string(nil), dashboardServices...), nil
}

func (m *openwrtManager) Status(ports map[string]int) (map[string]ServiceStatus, error) {
	return queryAllStatus(ports), nil
}

// --- systemd ---

type systemdManager struct {
	log *logging.Logger
}

func (m *systemdManager) Restart(service string) ([]string, error) {
	targets := []string{service}
	if service == "all" {
		targets = append([]string(nil), dashboardServices...)
	}
	var firstErr error
	var done []string
	for _, s := range targets {
		if err := runCmd("systemctl", "restart", "ps2servers-edge@"+s); err != nil {
			if firstErr == nil {
				firstErr = err
			}
			continue
		}
		done = append(done, s)
	}
	return done, firstErr
}

func (m *systemdManager) Status(ports map[string]int) (map[string]ServiceStatus, error) {
	return queryAllStatus(ports), nil
}

// --- Generic fallback ---

type genericManager struct {
	log *logging.Logger
}

func (m *genericManager) Restart(service string) ([]string, error) {
	m.log.Info(fmt.Sprintf(
		"Web UI received restart request for %s — running in generic mode, "+
			"please restart the process manually", service), nil)
	return nil, nil
}

func (m *genericManager) Status(ports map[string]int) (map[string]ServiceStatus, error) {
	return queryAllStatus(ports), nil
}

// --- Router status protocol query ---

// statusPorts resolves the UDP port to ask about each service.
//
// udpfs answers on its own discovery socket and so is always reachable. smb and
// udpbd only answer if they were given a --status-port: 0 means off (nothing is
// listening, so asking is a guaranteed timeout), and a negative value means the
// standard port.
func statusPorts(cfg *EdgeConfig) map[string]int {
	ports := map[string]int{"udpfs": defaultStatusPort}
	if cfg == nil {
		return ports
	}
	if cfg.UDPFS.Port > 0 {
		ports["udpfs"] = cfg.UDPFS.Port
	}
	for name, configured := range map[string]int{
		"smb":   cfg.SMB.StatusPort,
		"udpbd": cfg.UDPBD.StatusPort,
	} {
		switch {
		case configured > 0:
			ports[name] = configured
		case configured < 0:
			ports[name] = defaultStatusPort
		}
	}
	return ports
}

// queryAllStatus asks each distinct port once and attributes the reply by its
// capability flags.
//
// One query per port, not one per service: the standard port is shared, so
// asking it three times used to mean three round trips -- and, since only one
// service can hold it, two guaranteed 200ms timeouts on every 2-second poll.
func queryAllStatus(ports map[string]int) map[string]ServiceStatus {
	result := make(map[string]ServiceStatus, len(dashboardServices))
	for _, name := range dashboardServices {
		result[name] = unknownStatus()
	}

	byPort := map[int][]string{}
	for name, port := range ports {
		if _, known := serviceFlag[name]; known && port > 0 {
			byPort[port] = append(byPort[port], name)
		}
	}

	// Deterministic order so a failure is reproducible.
	distinct := make([]int, 0, len(byPort))
	for port := range byPort {
		distinct = append(distinct, port)
	}
	sort.Ints(distinct)

	for _, port := range distinct {
		reply, ok := queryStatus(port)
		if !ok {
			continue
		}
		for _, name := range byPort[port] {
			if reply.Flags&serviceFlag[name] == 0 {
				// Something answered, but not this service. Common and
				// expected: udpfs owns the shared port, so a dashboard that
				// also lists smb there gets udpfs's reply.
				continue
			}
			result[name] = ServiceStatus{
				Running:  true,
				State:    reply.State.String(),
				Sessions: int(reply.Sessions),
				Uptime:   reply.Uptime,
			}
		}
	}
	return result
}

// queryStatus sends one status query to 127.0.0.1:port and decodes the reply.
//
// Both directions go through internal/protocol rather than through hand-written
// offsets. The hand-written copy that used to live here had already drifted --
// it never read the capability flags, which is the only field that says which
// service answered.
func queryStatus(port int) (protocol.StatusReply, bool) {
	conn, err := net.Dial("udp", fmt.Sprintf("127.0.0.1:%d", port))
	if err != nil {
		return protocol.StatusReply{}, false
	}
	defer conn.Close()

	if err := conn.SetDeadline(time.Now().Add(200 * time.Millisecond)); err != nil {
		return protocol.StatusReply{}, false
	}
	if _, err := conn.Write(protocol.MarshalStatusQuery()); err != nil {
		return protocol.StatusReply{}, false
	}

	// A reply is StatusFixedLen plus a name of at most 255 bytes.
	buf := make([]byte, protocol.StatusFixedLen+255)
	n, err := conn.Read(buf)
	if err != nil {
		return protocol.StatusReply{}, false
	}
	reply, err := protocol.ParseStatusReply(buf[:n])
	if err != nil {
		return protocol.StatusReply{}, false
	}
	return reply, true
}
