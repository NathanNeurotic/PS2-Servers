package webui

import (
	"bytes"
	"fmt"
	"os/exec"
	"strconv"
	"strings"
)

type uciBackend struct{}

func (b *uciBackend) Load() (*EdgeConfig, error) {
	cfg := DefaultConfig()

	out, err := exec.Command("/sbin/uci", "show", "ps2servers-edge").Output()
	if err != nil {
		// If the config doesn't exist yet, return defaults
		return cfg, nil
	}

	lines := strings.Split(string(out), "\n")
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}

		parts := strings.SplitN(line, "=", 2)
		if len(parts) != 2 {
			continue
		}

		key := parts[0]
		val := strings.Trim(parts[1], "'\"")

		switch key {
		// UDPFS (main section)
		case "ps2servers-edge.main.enabled":
			cfg.UDPFS.Enabled = val == "1" || val == "true"
		case "ps2servers-edge.main.root":
			cfg.UDPFS.Root = val
		case "ps2servers-edge.main.bind":
			cfg.UDPFS.Bind = val
		case "ps2servers-edge.main.port":
			if p, err := strconv.Atoi(val); err == nil {
				cfg.UDPFS.Port = p
			}
		case "ps2servers-edge.main.data_port":
			if p, err := strconv.Atoi(val); err == nil {
				cfg.UDPFS.DataPort = p
			}
		case "ps2servers-edge.main.protocol":
			cfg.UDPFS.Protocol = val
		case "ps2servers-edge.main.single_port":
			cfg.UDPFS.SinglePort = val == "1" || val == "true"
		case "ps2servers-edge.main.read_only":
			cfg.UDPFS.ReadOnly = val == "1" || val == "true"
		case "ps2servers-edge.main.peer_timeout":
			cfg.UDPFS.PeerTimeout = val
		case "ps2servers-edge.main.tx_delay_ms":
			if d, err := strconv.ParseFloat(val, 64); err == nil {
				cfg.UDPFS.TxDelayMs = d
			}
		case "ps2servers-edge.main.log_format":
			cfg.UDPFS.LogFormat = val
		case "ps2servers-edge.main.verbose":
			cfg.UDPFS.Verbose = val == "1" || val == "true"
		case "ps2servers-edge.main.block_device":
			cfg.UDPFS.BlockDevice = val
		case "ps2servers-edge.main.sector_size":
			if s, err := strconv.Atoi(val); err == nil {
				cfg.UDPFS.SectorSize = s
			}
		case "ps2servers-edge.main.no_compression":
			cfg.UDPFS.NoCompression = val == "1" || val == "true"
		case "ps2servers-edge.main.compression_cache_size":
			if s, err := strconv.Atoi(val); err == nil {
				cfg.UDPFS.CompressionCacheSize = s
			}
		case "ps2servers-edge.main.metrics":
			cfg.UDPFS.Metrics = val == "1" || val == "true"
		case "ps2servers-edge.main.metrics_period":
			cfg.UDPFS.MetricsPeriod = val

		// SMB section
		case "ps2servers-edge.smb.enabled":
			cfg.SMB.Enabled = val == "1" || val == "true"
		case "ps2servers-edge.smb.share":
			cfg.SMB.Share = val
		case "ps2servers-edge.smb.bind":
			cfg.SMB.Bind = val
		case "ps2servers-edge.smb.port":
			if p, err := strconv.Atoi(val); err == nil {
				cfg.SMB.Port = p
			}
		case "ps2servers-edge.smb.read_only":
			cfg.SMB.ReadOnly = val == "1" || val == "true"
		case "ps2servers-edge.smb.status_port":
			if p, err := strconv.Atoi(val); err == nil {
				cfg.SMB.StatusPort = p
			}
		case "ps2servers-edge.smb.log_format":
			cfg.SMB.LogFormat = val
		case "ps2servers-edge.smb.verbose":
			cfg.SMB.Verbose = val == "1" || val == "true"

		// UDPBD section
		case "ps2servers-edge.udpbd.enabled":
			cfg.UDPBD.Enabled = val == "1" || val == "true"
		case "ps2servers-edge.udpbd.image":
			cfg.UDPBD.Image = val
		case "ps2servers-edge.udpbd.bind":
			cfg.UDPBD.Bind = val
		case "ps2servers-edge.udpbd.port":
			if p, err := strconv.Atoi(val); err == nil {
				cfg.UDPBD.Port = p
			}
		case "ps2servers-edge.udpbd.read_only":
			cfg.UDPBD.ReadOnly = val == "1" || val == "true"
		case "ps2servers-edge.udpbd.status_port":
			if p, err := strconv.Atoi(val); err == nil {
				cfg.UDPBD.StatusPort = p
			}
		case "ps2servers-edge.udpbd.log_format":
			cfg.UDPBD.LogFormat = val
		case "ps2servers-edge.udpbd.verbose":
			cfg.UDPBD.Verbose = val == "1" || val == "true"

		// WebUI section
		case "ps2servers-edge.webui.enabled":
			cfg.WebUI.Enabled = val == "1" || val == "true"
		case "ps2servers-edge.webui.port":
			if p, err := strconv.Atoi(val); err == nil {
				cfg.WebUI.Port = p
			}
		case "ps2servers-edge.webui.bind":
			cfg.WebUI.Bind = val
		}
	}

	return cfg, nil
}

func (b *uciBackend) Save(cfg *EdgeConfig) error {
	var cmds [][]string

	addCmd := func(key string, val interface{}) {
		var sval string
		switch v := val.(type) {
		case bool:
			if v {
				sval = "1"
			} else {
				sval = "0"
			}
		case string:
			sval = v
		case int, float64:
			sval = fmt.Sprintf("%v", v)
		}
		cmds = append(cmds, []string{"set", fmt.Sprintf("ps2servers-edge.%s=%s", key, sval)})
	}

	// Set basic sections if they don't exist (assuming default init script creates them, but to be safe we'd just set values)

	// UDPFS
	addCmd("main.enabled", cfg.UDPFS.Enabled)
	addCmd("main.root", cfg.UDPFS.Root)
	addCmd("main.bind", cfg.UDPFS.Bind)
	addCmd("main.port", cfg.UDPFS.Port)
	addCmd("main.data_port", cfg.UDPFS.DataPort)
	addCmd("main.protocol", cfg.UDPFS.Protocol)
	addCmd("main.single_port", cfg.UDPFS.SinglePort)
	addCmd("main.read_only", cfg.UDPFS.ReadOnly)
	addCmd("main.peer_timeout", cfg.UDPFS.PeerTimeout)
	addCmd("main.tx_delay_ms", cfg.UDPFS.TxDelayMs)
	addCmd("main.log_format", cfg.UDPFS.LogFormat)
	addCmd("main.verbose", cfg.UDPFS.Verbose)
	addCmd("main.block_device", cfg.UDPFS.BlockDevice)
	addCmd("main.sector_size", cfg.UDPFS.SectorSize)
	addCmd("main.no_compression", cfg.UDPFS.NoCompression)
	addCmd("main.compression_cache_size", cfg.UDPFS.CompressionCacheSize)
	addCmd("main.metrics", cfg.UDPFS.Metrics)
	addCmd("main.metrics_period", cfg.UDPFS.MetricsPeriod)

	// SMB
	addCmd("smb.enabled", cfg.SMB.Enabled)
	addCmd("smb.share", cfg.SMB.Share)
	addCmd("smb.bind", cfg.SMB.Bind)
	addCmd("smb.port", cfg.SMB.Port)
	addCmd("smb.read_only", cfg.SMB.ReadOnly)
	addCmd("smb.status_port", cfg.SMB.StatusPort)
	addCmd("smb.log_format", cfg.SMB.LogFormat)
	addCmd("smb.verbose", cfg.SMB.Verbose)

	// UDPBD
	addCmd("udpbd.enabled", cfg.UDPBD.Enabled)
	addCmd("udpbd.image", cfg.UDPBD.Image)
	addCmd("udpbd.bind", cfg.UDPBD.Bind)
	addCmd("udpbd.port", cfg.UDPBD.Port)
	addCmd("udpbd.read_only", cfg.UDPBD.ReadOnly)
	addCmd("udpbd.status_port", cfg.UDPBD.StatusPort)
	addCmd("udpbd.log_format", cfg.UDPBD.LogFormat)
	addCmd("udpbd.verbose", cfg.UDPBD.Verbose)

	// WebUI
	addCmd("webui.enabled", cfg.WebUI.Enabled)
	addCmd("webui.port", cfg.WebUI.Port)
	addCmd("webui.bind", cfg.WebUI.Bind)

	// Execute all set commands via batch or sequentially
	var buf bytes.Buffer
	for _, c := range cmds {
		buf.WriteString(strings.Join(c, " ") + "\n")
	}
	buf.WriteString("commit ps2servers-edge\n")

	cmd := exec.Command("/sbin/uci", "batch")
	cmd.Stdin = &buf
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("uci batch failed: %w", err)
	}

	return nil
}
