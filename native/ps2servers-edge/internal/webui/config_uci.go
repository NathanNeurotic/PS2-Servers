package webui

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
)

type uciBackend struct{}

func (b *uciBackend) Load() (*EdgeConfig, error) {
	cfg := DefaultConfig()

	cmd := exec.Command("/sbin/uci", "show", "ps2servers-edge")
	out, err := cmd.Output()
	if err != nil {
		// "Not found" is the one failure that legitimately means defaults: the
		// package is installed but no config has been written yet.
		//
		// Everything else -- uci missing, permission denied, a corrupt file --
		// used to land here too, so the UI displayed defaults as if they were
		// live and the next Save wrote them over a working configuration. With
		// the web UI running unprivileged, permission denied is a realistic
		// trigger, not a theoretical one.
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) && isUCINotFound(exitErr.Stderr) {
			return cfg, nil
		}
		return nil, fmt.Errorf("uci show ps2servers-edge failed: %w%s",
			err, stderrSuffix(err))
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

// isUCINotFound distinguishes "no such config" from every other uci failure.
func isUCINotFound(stderr []byte) bool {
	s := strings.ToLower(string(stderr))
	return strings.Contains(s, "not found") || strings.Contains(s, "entry not found")
}

// stderrSuffix appends a command's stderr to an error, when there is any.
//
// Without it the API reports "exit status 1", which says something broke and
// nothing about what -- and the likely cause here is a permission the service
// user does not have, which the message would name outright.
func stderrSuffix(err error) string {
	var exitErr *exec.ExitError
	if errors.As(err, &exitErr) && len(exitErr.Stderr) > 0 {
		return ": " + strings.TrimSpace(string(exitErr.Stderr))
	}
	return ""
}

func (b *uciBackend) Save(cfg *EdgeConfig) error {
	// Defence in depth. The API validates before calling any backend, but this
	// is the one backend where a bad value is more than a bad value, so it does
	// not rely on being called correctly.
	if err := ValidateConfig(cfg); err != nil {
		return err
	}

	type uciSet struct{ key, value string }
	var cmds []uciSet

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
		case int:
			sval = strconv.Itoa(v)
		case float64:
			sval = strconv.FormatFloat(v, 'g', -1, 64)
		default:
			// Silently writing an empty value for an unhandled type is how a
			// setting becomes decorative without anyone noticing.
			panic(fmt.Sprintf("uciBackend.Save: unhandled type %T for %s", val, key))
		}
		cmds = append(cmds, uciSet{key: key, value: sval})
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

	// One exec per option, with the value as its own argv element.
	//
	// NOT `uci batch` over stdin. That is a line-oriented command stream, so a
	// value containing a newline injected an arbitrary uci command -- against
	// any package, not just this one -- and a value containing a space silently
	// changed how the line parsed, which broke every share path with a space in
	// it. Passing the value as a separate argument removes the text channel
	// entirely: there is no line for it to break out of.
	//
	// The cost is one process per option instead of one for the lot. That is
	// roughly forty short-lived execs on a save, which is not free on a router
	// but happens only when someone clicks Save.
	// A failed set must not leave the earlier ones staged.
	//
	// uci set writes to the staging area under /tmp/.uci and only lands on a
	// commit. Returning on the first failure without discarding the rest means
	// the operator sees an error, assumes nothing changed, and then the staged
	// half is applied by the NEXT successful save's commit -- a partial
	// configuration arriving later, with nothing to connect it to the error.
	// Permission denied part-way through is the realistic trigger; the web UI
	// runs unprivileged and the init script's own comment says so.
	revert := func() {
		if err := runCmd("/sbin/uci", "revert", "ps2servers-edge"); err != nil {
			// Nothing better to do than say so: the caller is already
			// returning a failure, and this is the cleanup for it.
			fmt.Fprintf(os.Stderr,
				"webui: uci revert after a failed save also failed: %v\n", err)
		}
	}

	for _, c := range cmds {
		if err := runCmd("/sbin/uci", "set",
			"ps2servers-edge."+c.key+"="+c.value); err != nil {
			revert()
			return fmt.Errorf("uci set %s failed: %w", c.key, err)
		}
	}
	if err := runCmd("/sbin/uci", "commit", "ps2servers-edge"); err != nil {
		revert()
		return fmt.Errorf("uci commit failed: %w", err)
	}

	return nil
}
