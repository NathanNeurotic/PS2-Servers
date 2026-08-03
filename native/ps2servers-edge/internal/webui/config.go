package webui

import "os"

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

func DetectBackend(jsonPath string) ConfigBackend {
	if _, err := os.Stat("/sbin/uci"); err == nil {
		return &uciBackend{}
	}
	return &jsonBackend{path: jsonPath}
}
