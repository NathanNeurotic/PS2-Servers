package main

import (
	"context"
	"flag"
	"fmt"
	"math"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/compression"
	edgelog "github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/udpbd"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/udpfs"
)

var version = "dev"

func usage() {
	fmt.Fprintf(os.Stderr, "PS2 Servers Edge %s\n\nUsage:\n"+
		"  ps2servers-edge udpfs --root /games [options]\n"+
		"  ps2servers-edge udpbd --image /path/ps2.img [options]\n"+
		"  ps2servers-edge --version\n\n"+
		"  udpfs serves a folder as a network file share.\n"+
		"  udpbd serves one disk image as a network hard drive.\n\n", version)
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
	if len(os.Args) >= 2 && os.Args[1] == "udpbd" {
		runUDPBD()
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
	txDelayMs := fs.Float64("tx-delay-ms", envFloat("TX_DELAY_MS", 0.0), "inter-packet transmit delay in milliseconds")
	// Writable by default, matching the Desktop/Core server and udpfsd. UDPFS
	// is a two-way protocol in practice -- a console that loads a game off the
	// share usually wants to write its saves back to it -- so a read-only
	// default makes Edge quietly less capable than its siblings for the normal
	// case. Pass --read-only (or RO=1) to serve read-only.
	//
	// The OpenWrt package still starts read-only unless the operator opts in,
	// because a router share is often an entire attached disk. See
	// packaging/openwrt/files/ps2servers-edge.config.
	readOnly := fs.Bool("read-only", envBool("RO", false), "serve files read-only; omit to allow the console to write saves")
	// Names match the Desktop/Core server so a command line or compose file
	// written for one works on the other.
	blockDevice := fs.String("block-device", env("BDPATH", ""), "also serve this disk image over UDPFS block access")
	sectorSize := fs.Int("sector-size", envInt("SECTOR_SIZE", 512), "sector size for --block-device")
	noCompression := fs.Bool("no-compression", envBool("NO_COMPRESSION", false), "serve CSO/ZSO as raw bytes instead of decompressing")
	compressionCache := fs.Int("compression-cache-size", envInt("COMPRESSION_CACHE_SIZE", 32), "decompressed blocks cached per open image")
	metrics := fs.Bool("metrics", envBool("METRICS", false), "log periodic transfer statistics")
	metricsPeriod := fs.String("metrics-period", env("METRICS_PERIOD", "1m"), "how often to log statistics")
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
	if *sectorSize <= 0 || *sectorSize > 65536 || *sectorSize%512 != 0 {
		// A sector size that is not a multiple of 512 cannot describe a PS2
		// image, and an unbounded one would let a small sector count request a
		// huge read.
		fmt.Fprintln(os.Stderr, "error: --sector-size must be a multiple of 512 and at most 65536")
		os.Exit(2)
	}
	// Bounded, not merely non-negative: the value becomes the initial capacity
	// of the per-image block map, so a mistyped --compression-cache-size would
	// force a huge allocation the moment a compressed image is opened -- on the
	// low-memory routers this build targets, that is an OOM rather than a
	// slowdown.
	if *compressionCache < 0 || *compressionCache > compression.MaxCacheBlocks {
		fmt.Fprintf(os.Stderr, "error: --compression-cache-size must be between 0 and %d\n",
			compression.MaxCacheBlocks)
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
	// Rejected rather than ignored. envFloat already scrubs negative, NaN and
	// Inf out of TX_DELAY_MS, but the flag reached the `> 0` test unchecked, so
	// --tx-delay-ms -1 was silently treated as "no pacing" -- the two paths
	// disagreed about the same value. On OpenWrt that is a UCI option the
	// operator set, watched do nothing, and got no error about.
	if *txDelayMs < 0 || math.IsNaN(*txDelayMs) || math.IsInf(*txDelayMs, 0) {
		fmt.Fprintln(os.Stderr, "error: --tx-delay-ms must be zero or a positive number of milliseconds")
		os.Exit(2)
	}
	var txDelay time.Duration
	if *txDelayMs > 0 {
		txDelay = time.Duration(*txDelayMs * float64(time.Millisecond))
	}
	var metricsEvery time.Duration
	if *metrics {
		metricsEvery, err = duration(*metricsPeriod)
		if err != nil || metricsEvery < time.Second || metricsEvery > 24*time.Hour {
			fmt.Fprintln(os.Stderr, "error: --metrics-period must be between 1s and 24h")
			os.Exit(2)
		}
	}
	logger := edgelog.New(os.Stdout, *logFormat, *quiet, *verbose)
	server, err := udpfs.New(udpfs.Config{Root: *root, Bind: *bind, Port: *port, DataPort: *dataPort, SinglePort: *singlePort, ProtocolMode: profile, PeerTimeout: timeout, TxDelay: txDelay, ReadOnly: *readOnly, MetricsPeriod: metricsEvery, BlockDevice: *blockDevice, SectorSize: *sectorSize, NoCompression: *noCompression, CompressionCache: *compressionCache, Log: logger, ServerName: "PS2 Servers Edge"})
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
func envFloat(name string, def float64) float64 {
	v := os.Getenv(name)
	if v == "" {
		return def
	}
	n, err := strconv.ParseFloat(v, 64)
	if err != nil || math.IsNaN(n) || math.IsInf(n, 0) || n < 0 {
		return def
	}
	return n
}

// runUDPBD serves one disk image as a network block device.
//
// UDPBD is a separate protocol from UDPFS on its own fixed port, so it gets its
// own subcommand rather than a flag: a server serves either a folder of files
// or one image as a raw drive, never both from the same invocation.
func runUDPBD() {
	fs := flag.NewFlagSet("udpbd", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	image := fs.String("image", env("BDPATH", ""), "disk image to serve as a block device")
	bind := fs.String("bind", env("BIND", "0.0.0.0"), "IPv4 bind address")
	port := fs.Int("port", envInt("UDPBD_PORT", udpbd.Port), "UDP port (default 0xBDBD)")
	readOnly := fs.Bool("read-only", envBool("RO", false), "serve the image read-only")
	logFormat := fs.String("log-format", env("LOG_FORMAT", "text"), "text or json")
	verbose := fs.Bool("verbose", envBool("VERBOSE", false), "verbose protocol logging")
	quiet := fs.Bool("quiet", false, "suppress informational logs")
	if err := fs.Parse(os.Args[2:]); err != nil {
		os.Exit(2)
	}
	// Accept a bare positional image too, matching how the Python UDPBD server
	// is invoked (`udpbd_server.py <image>`).
	if *image == "" && fs.NArg() > 0 {
		*image = fs.Arg(0)
	}
	if *image == "" {
		fmt.Fprintln(os.Stderr, "error: --image is required")
		os.Exit(2)
	}
	if *logFormat != "text" && *logFormat != "json" {
		fmt.Fprintln(os.Stderr, "error: --log-format must be text or json")
		os.Exit(2)
	}
	logger := edgelog.New(os.Stdout, *logFormat, *quiet, *verbose)
	server, err := udpbd.New(udpbd.Config{
		Image: *image, Bind: *bind, Port: *port, ReadOnly: *readOnly, Log: logger,
	})
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
	defer server.Close()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err = server.Serve(ctx); err != nil {
		logger.Error("server stopped", map[string]any{"error": err})
		os.Exit(1)
	}
}
