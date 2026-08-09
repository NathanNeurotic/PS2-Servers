package webui

import (
	"fmt"
	"os"
	"strings"
)

type UDPFSConfig struct {
	Enabled              bool    `json:"enabled"`
	Root                 string  `json:"root"`
	Bind                 string  `json:"bind"`
	Port                 int     `json:"port"`
	DataPort             int     `json:"data_port"`
	Protocol             string  `json:"protocol"`
	SinglePort           bool    `json:"single_port"`
	ReadOnly             bool    `json:"read_only"`
	PeerTimeout          string  `json:"peer_timeout"`
	TxDelayMs            float64 `json:"tx_delay_ms"`
	LogFormat            string  `json:"log_format"`
	Verbose              bool    `json:"verbose"`
	BlockDevice          string  `json:"block_device"`
	SectorSize           int     `json:"sector_size"`
	NoCompression        bool    `json:"no_compression"`
	CompressionCacheSize int     `json:"compression_cache_size"`
	Metrics              bool    `json:"metrics"`
	MetricsPeriod        string  `json:"metrics_period"`
}

type SMBConfig struct {
	Enabled    bool   `json:"enabled"`
	Share      string `json:"share"`
	Bind       string `json:"bind"`
	Port       int    `json:"port"`
	ReadOnly   bool   `json:"read_only"`
	StatusPort int    `json:"status_port"`
	LogFormat  string `json:"log_format"`
	Verbose    bool   `json:"verbose"`
}

type UDPBDConfig struct {
	Enabled    bool   `json:"enabled"`
	Image      string `json:"image"`
	Bind       string `json:"bind"`
	Port       int    `json:"port"`
	ReadOnly   bool   `json:"read_only"`
	StatusPort int    `json:"status_port"`
	LogFormat  string `json:"log_format"`
	Verbose    bool   `json:"verbose"`
}

type WebUIConfig struct {
	Enabled bool   `json:"enabled"`
	Port    int    `json:"port"`
	Bind    string `json:"bind"`
}

type EdgeConfig struct {
	UDPFS UDPFSConfig `json:"udpfs"`
	SMB   SMBConfig   `json:"smb"`
	UDPBD UDPBDConfig `json:"udpbd"`
	WebUI WebUIConfig `json:"webui"`
}

func DefaultConfig() *EdgeConfig {
	return &EdgeConfig{
		UDPFS: UDPFSConfig{
			Enabled:              true,
			Root:                 "/mnt/sda1/PS2",
			Bind:                 "0.0.0.0",
			Port:                 62966,
			DataPort:             0,
			Protocol:             "auto",
			SinglePort:           false,
			ReadOnly:             true,
			PeerTimeout:          "1h",
			TxDelayMs:            0,
			LogFormat:            "text",
			Verbose:              false,
			BlockDevice:          "",
			SectorSize:           512,
			NoCompression:        false,
			CompressionCacheSize: 32,
			Metrics:              false,
			MetricsPeriod:        "1m",
		},
		SMB: SMBConfig{
			Enabled:    false,
			Share:      "games=/mnt/sda1/PS2",
			Bind:       "0.0.0.0",
			Port:       1111,
			ReadOnly:   true,
			StatusPort: 0,
			LogFormat:  "text",
			Verbose:    false,
		},
		UDPBD: UDPBDConfig{
			Enabled:    false,
			Image:      "",
			Bind:       "0.0.0.0",
			Port:       48573,
			ReadOnly:   true,
			StatusPort: 0,
			LogFormat:  "text",
			Verbose:    false,
		},
		WebUI: WebUIConfig{
			Enabled: false,
			Port:    8082,
			Bind:    "0.0.0.0",
		},
	}
}

type ConfigBackend interface {
	Load() (*EdgeConfig, error)
	Save(cfg *EdgeConfig) error
}

const defaultJSONConfigPath = "/etc/ps2servers-edge/config.json"

// DetectBackend picks where configuration is read from and written to.
//
// explicitPath reports whether the caller actually passed --config-file, rather
// than comparing its value against the default. Those are different intents and
// only the flag's set-ness separates them: the shipped systemd env file passes
// the default path verbatim, and comparing values silently discarded it on any
// box that also had /sbin/uci.
func DetectBackend(jsonPath string, explicitPath bool) ConfigBackend {
	if explicitPath && jsonPath != "" {
		return &jsonBackend{path: jsonPath}
	}
	if _, err := os.Stat("/sbin/uci"); err == nil {
		return &uciBackend{}
	}
	if jsonPath == "" {
		jsonPath = defaultJSONConfigPath
	}
	return &jsonBackend{path: jsonPath}
}

// validPortRange allows 0 (meaning "the server's own default") through 65535.
func validPort(field string, port int) error {
	if port < 0 || port > 65535 {
		return fmt.Errorf("%s: port %d is out of range (0-65535)", field, port)
	}
	return nil
}

// validStatusPort additionally allows -1, which means the standard port.
func validStatusPort(field string, port int) error {
	if port == -1 {
		return nil
	}
	return validPort(field, port)
}

// noControlChars refuses values that would break out of a line-oriented sink.
//
// The UCI backend writes these into a command stream where a newline starts a
// new uci command, so this is the difference between "a setting" and "an
// arbitrary write to any package's configuration". Enforced here rather than
// only in that backend so the rule does not depend on which platform is
// running, and so a value that is illegal on a router is not silently legal in
// the JSON file that a router might later import.
func noControlChars(field, value string) error {
	for _, r := range value {
		if r == '\n' || r == '\r' || r == 0 || (r < 0x20 && r != '\t') {
			return fmt.Errorf("%s: control characters are not allowed", field)
		}
	}
	return nil
}

func oneOf(field, value string, allowed ...string) error {
	for _, a := range allowed {
		if value == a {
			return nil
		}
	}
	return fmt.Errorf("%s: %q is not one of %s", field, value, strings.Join(allowed, ", "))
}

// ValidateConfig checks a configuration submitted over the API.
//
// Rejecting is the whole point: everything here arrives as JSON from the
// network, and the two things that go wrong are a value that escapes the sink
// it is written to, and a value that produces a server which refuses to start
// with no visible reason.
func ValidateConfig(cfg *EdgeConfig) error {
	if cfg == nil {
		return fmt.Errorf("no configuration supplied")
	}

	strFields := map[string]string{
		"udpfs.root":           cfg.UDPFS.Root,
		"udpfs.bind":           cfg.UDPFS.Bind,
		"udpfs.peer_timeout":   cfg.UDPFS.PeerTimeout,
		"udpfs.block_device":   cfg.UDPFS.BlockDevice,
		"udpfs.metrics_period": cfg.UDPFS.MetricsPeriod,
		"smb.share":            cfg.SMB.Share,
		"smb.bind":             cfg.SMB.Bind,
		"udpbd.image":          cfg.UDPBD.Image,
		"udpbd.bind":           cfg.UDPBD.Bind,
		"webui.bind":           cfg.WebUI.Bind,
	}
	for field, value := range strFields {
		if err := noControlChars(field, value); err != nil {
			return err
		}
	}

	for field, port := range map[string]int{
		"udpfs.port":      cfg.UDPFS.Port,
		"udpfs.data_port": cfg.UDPFS.DataPort,
		"smb.port":        cfg.SMB.Port,
		"udpbd.port":      cfg.UDPBD.Port,
		"webui.port":      cfg.WebUI.Port,
	} {
		if err := validPort(field, port); err != nil {
			return err
		}
	}
	for field, port := range map[string]int{
		"smb.status_port":   cfg.SMB.StatusPort,
		"udpbd.status_port": cfg.UDPBD.StatusPort,
	} {
		if err := validStatusPort(field, port); err != nil {
			return err
		}
	}

	if err := oneOf("udpfs.protocol", cfg.UDPFS.Protocol, "auto", "standard", "modulo"); err != nil {
		return err
	}
	for field, value := range map[string]string{
		"udpfs.log_format": cfg.UDPFS.LogFormat,
		"smb.log_format":   cfg.SMB.LogFormat,
		"udpbd.log_format": cfg.UDPBD.LogFormat,
	} {
		if err := oneOf(field, value, "text", "json"); err != nil {
			return err
		}
	}

	if cfg.UDPFS.SectorSize <= 0 || cfg.UDPFS.SectorSize > 1<<16 {
		return fmt.Errorf("udpfs.sector_size: %d is out of range (1-65536)", cfg.UDPFS.SectorSize)
	}
	if cfg.UDPFS.CompressionCacheSize < 0 {
		return fmt.Errorf("udpfs.compression_cache_size: must not be negative")
	}
	if cfg.UDPFS.TxDelayMs < 0 {
		return fmt.Errorf("udpfs.tx_delay_ms: must not be negative")
	}
	return nil
}
