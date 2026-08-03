package webui

import (
	"encoding/binary"
	"fmt"
	"net"
	"os"
	"os/exec"
	"time"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
)

// ServiceManager abstracts how the web UI restarts Edge services and queries
// their status.  Three implementations are auto-detected: OpenWrt (procd),
// systemd, and a generic fallback that logs instructions.
type ServiceManager interface {
	Restart(service string) error
	Status() (map[string]ServiceStatus, error)
}

// ServiceStatus is returned by the /api/status endpoint and consumed by the
// dashboard cards.
type ServiceStatus struct {
	Running  bool   `json:"running"`
	State    string `json:"state"`
	Sessions int    `json:"sessions"`
	Uptime   int    `json:"uptime"`
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

// --- OpenWrt (procd) ---

type openwrtManager struct {
	log *logging.Logger
}

func (m *openwrtManager) Restart(service string) error {
	// The init script handles all three services; restarting it restarts
	// whichever instances are enabled in UCI.
	return exec.Command("/etc/init.d/ps2servers-edge", "restart").Run()
}

func (m *openwrtManager) Status() (map[string]ServiceStatus, error) {
	return queryAllStatus()
}

// --- systemd ---

type systemdManager struct {
	log *logging.Logger
}

func (m *systemdManager) Restart(service string) error {
	if service == "all" {
		for _, s := range []string{"udpfs", "smb", "udpbd"} {
			exec.Command("systemctl", "restart", "ps2servers-edge@"+s).Run()
		}
		return nil
	}
	return exec.Command("systemctl", "restart", "ps2servers-edge@"+service).Run()
}

func (m *systemdManager) Status() (map[string]ServiceStatus, error) {
	return queryAllStatus()
}

// --- Generic fallback ---

type genericManager struct {
	log *logging.Logger
}

func (m *genericManager) Restart(service string) error {
	m.log.Info(fmt.Sprintf(
		"Web UI received restart request for %s — running in generic mode, "+
			"please restart the process manually", service), nil)
	return nil
}

func (m *genericManager) Status() (map[string]ServiceStatus, error) {
	return queryAllStatus()
}

// --- Router status protocol query ---

// queryAllStatus sends a status query to the well-known discovery port on
// localhost and parses the response.  UDPFS listens on port 62966 (0xF5F6) by
// default; SMB and UDPBD use the same status listener port when configured.
func queryAllStatus() (map[string]ServiceStatus, error) {
	res := map[string]ServiceStatus{
		"udpfs": queryStatus(62966),
		"smb":   queryStatus(62966),
		"udpbd": queryStatus(62966),
	}
	return res, nil
}

// queryStatus sends a 10-byte UDPRDMA status query (service 0xF5F7) to
// 127.0.0.1:port and decodes the reply per docs/ROUTER-STATUS.md.
func queryStatus(port int) ServiceStatus {
	unknown := ServiceStatus{Running: false, State: "unknown", Sessions: 0, Uptime: 0}

	// Build the 10-byte query:
	//   [0:2] header — type 0 (DISCOVERY), sequence 0  → 0x0000 LE
	//   [2:4] service ID — 0xF5F7 LE
	//   [4:6] port — 0x0000 LE (unused; reply goes to query's UDP source)
	//   [6:10] magic — "PS2S"
	req := make([]byte, 10)
	binary.LittleEndian.PutUint16(req[0:2], 0x0000)
	binary.LittleEndian.PutUint16(req[2:4], 0xF5F7)
	binary.LittleEndian.PutUint16(req[4:6], 0x0000)
	copy(req[6:10], "PS2S")

	conn, err := net.DialTimeout("udp", fmt.Sprintf("127.0.0.1:%d", port), 200*time.Millisecond)
	if err != nil {
		return unknown
	}
	defer conn.Close()

	conn.SetDeadline(time.Now().Add(200 * time.Millisecond))

	if _, err := conn.Write(req); err != nil {
		return unknown
	}

	buf := make([]byte, 256)
	n, err := conn.Read(buf)
	if err != nil || n < 19 {
		return unknown
	}

	// Reply layout (docs/ROUTER-STATUS.md):
	//   [0:2]  header — type 1 (INFORM), sequence 0 → low nibble is 1
	//   [2:4]  service ID — 0xF5F7  (echoed)
	//   [4:8]  magic — "PS2S"       (echoed)
	//   [8]    payload version — 1
	//   [9]    state
	//   [10:12] service flags
	//   [12:14] active sessions
	//   [14:18] uptime (seconds)
	//   [18]   name length
	//   [19:]  name (UTF-8)

	// Validate service ID and magic.
	if binary.LittleEndian.Uint16(buf[2:4]) != 0xF5F7 {
		return unknown
	}
	if string(buf[4:8]) != "PS2S" {
		return unknown
	}
	// Validate header type: low nibble must be 1 (INFORM).
	if buf[0]&0x0F != 1 {
		return unknown
	}

	stateByte := buf[9]
	stateStr := "unknown"
	switch stateByte {
	case 0:
		stateStr = "starting"
	case 1:
		stateStr = "ready"
	case 2:
		stateStr = "busy"
	case 3:
		stateStr = "degraded"
	case 4:
		stateStr = "stopping"
	}

	sessions := int(binary.LittleEndian.Uint16(buf[12:14]))
	uptime := int(binary.LittleEndian.Uint32(buf[14:18]))

	return ServiceStatus{
		Running:  true,
		State:    stateStr,
		Sessions: sessions,
		Uptime:   uptime,
	}
}
