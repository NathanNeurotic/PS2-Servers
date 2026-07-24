package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	edgelog "github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/udpfs"
)

var version = "dev"

func usage() {
	fmt.Fprintf(os.Stderr, "PS2 Servers Edge %s\n\nUsage:\n  ps2servers-edge udpfs --root /games [options]\n  ps2servers-edge --version\n\n", version)
}
func duration(v string) (time.Duration, error) {
	if v == "" {
		return 0, nil
	}
	if d, err := time.ParseDuration(v); err == nil {
		return d, nil
	}
	seconds, err := strconv.ParseFloat(v, 64)
	if err != nil {
		return 0, fmt.Errorf("expected Go duration (1h, 30m) or seconds")
	}
	return time.Duration(seconds * float64(time.Second)), nil
}
func main() {
	if len(os.Args) == 2 && (os.Args[1] == "--version" || os.Args[1] == "version") {
		fmt.Printf("ps2servers-edge %s\n", version)
		return
	}
	if len(os.Args) < 2 || os.Args[1] != "udpfs" {
		usage()
		os.Exit(2)
	}
	fs := flag.NewFlagSet("udpfs", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	root := fs.String("root", os.Getenv("FSROOT"), "game root directory")
	bind := fs.String("bind", env("BIND", "0.0.0.0"), "IPv4 bind address")
	port := fs.Int("port", envInt("PORT", 0xF5F6), "discovery UDP port")
	dataPort := fs.Int("data-port", envInt("DATA_PORT", 0), "fixed data UDP port; 0 chooses automatically")
	protocolMode := fs.String("protocol-mode", env("PROTOCOL_MODE", "auto"), "auto, standard, or modulo")
	moduloAlias := fs.Bool("modulo-mode", false, "deprecated alias for --protocol-mode modulo")
	singlePort := fs.Bool("single-port", envBool("SINGLE_PORT", false), "use discovery port for all traffic")
	timeoutText := fs.String("peer-timeout", env("PEER_TIMEOUT", "1h"), "idle session timeout")
	readOnly := fs.Bool("read-only", true, "serve files read-only (currently mandatory)")
	logFormat := fs.String("log-format", env("LOG_FORMAT", "text"), "text or json")
	verbose := fs.Bool("verbose", envBool("VERBOSE", false), "verbose protocol logging")
	quiet := fs.Bool("quiet", false, "suppress informational logs")
	showVersion := fs.Bool("version", false, "show version")
	if err := fs.Parse(os.Args[2:]); err != nil {
		os.Exit(2)
	}
	if *showVersion {
		fmt.Printf("ps2servers-edge %s\n", version)
		return
	}
	if *root == "" {
		fmt.Fprintln(os.Stderr, "error: --root is required")
		os.Exit(2)
	}
	if !*readOnly {
		fmt.Fprintln(os.Stderr, "error: PS2 Servers Edge currently supports read-only UDPFS only")
		os.Exit(2)
	}
	if *logFormat != "text" && *logFormat != "json" {
		fmt.Fprintln(os.Stderr, "error: --log-format must be text or json")
		os.Exit(2)
	}
	mode := strings.ToLower(strings.TrimSpace(*protocolMode))
	if *moduloAlias {
		if mode != "auto" && mode != "modulo" {
			fmt.Fprintln(os.Stderr, "error: --modulo-mode conflicts with --protocol-mode")
			os.Exit(2)
		}
		mode = "modulo"
		fmt.Fprintln(os.Stderr, "warning: --modulo-mode is deprecated; use --protocol-mode modulo")
	}
	var profile session.Profile
	switch mode {
	case "auto":
		profile = session.Pending
	case "standard":
		profile = session.Standard
	case "modulo":
		profile = session.Modulo
	default:
		fmt.Fprintln(os.Stderr, "error: --protocol-mode must be auto, standard, or modulo")
		os.Exit(2)
	}
	timeout, err := duration(*timeoutText)
	if err != nil || timeout < time.Minute || timeout > 24*time.Hour {
		fmt.Fprintln(os.Stderr, "error: --peer-timeout must be between 1m and 24h")
		os.Exit(2)
	}
	logger := edgelog.New(os.Stdout, *logFormat, *quiet, *verbose)
	server, err := udpfs.New(udpfs.Config{Root: *root, Bind: *bind, Port: *port, DataPort: *dataPort, SinglePort: *singlePort, ProtocolMode: profile, PeerTimeout: timeout, ReadOnly: true, Log: logger, ServerName: "PS2 Servers Edge"})
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err = server.Serve(ctx); err != nil {
		logger.Error("server stopped", map[string]any{"error": err})
		os.Exit(1)
	}
}
func env(name, def string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return def
}
func envInt(name string, def int) int {
	v := os.Getenv(name)
	if v == "" {
		return def
	}
	n, err := strconv.ParseInt(v, 0, 32)
	if err != nil {
		return def
	}
	return int(n)
}
func envBool(name string, def bool) bool {
	v := strings.ToLower(strings.TrimSpace(os.Getenv(name)))
	if v == "" {
		return def
	}
	return v == "1" || v == "true" || v == "yes" || v == "on"
}
