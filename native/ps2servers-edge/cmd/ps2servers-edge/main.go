package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"math"
	"os"
	"os/signal"
	"runtime/debug"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/compression"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/filesystem"
	edgelog "github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/memory"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/smb"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/udpbd"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/udpfs"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/webui"
)

var version = "dev"

func usage() {
	fmt.Fprintf(os.Stderr, "PS2 Servers Edge %s\n\nUsage:\n"+
		"  ps2servers-edge udpfs --root /games [options]\n"+
		"  ps2servers-edge udpbd --image /path/ps2.img [options]\n"+
		"  ps2servers-edge smb --share games=/games [options]\n"+
		"  ps2servers-edge webui [options]\n"+
		"  ps2servers-edge --version\n"+
		"  ps2servers-edge --dump-schema\n\n"+
		"  udpfs serves a folder as a network file share.\n"+
		"  udpbd serves one disk image as a network hard drive.\n"+
		"  smb   serves folders over SMBv1, for OPL's game list and POPSTARTER.\n"+
		"  webui serves a web dashboard to configure and manage Edge servers.\n\n", version)
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

// applyMemoryLimit gives the collector a ceiling on machines small enough for
// it to matter.
//
// A backstop, not the fix: a soft limit makes the GC work harder as the heap
// approaches it, but it does not make an allocation fail, so it cannot on its
// own stop one oversized buffer from taking the process down. That is the job
// of the transfer cap in internal/memory. What this adds is that a heap which
// has already grown past what the board can hold gets collected hard rather
// than drifting up until the kernel's OOM killer picks a victim -- which on a
// router is as likely to be something else as it is to be us.
func applyMemoryLimit() {
	if limit := memory.Detect().SoftLimit(); limit > 0 {
		debug.SetMemoryLimit(limit)
	}
}

func main() {
	if len(os.Args) == 2 && (os.Args[1] == "--version" || os.Args[1] == "version") {
		fmt.Printf("ps2servers-edge %s\n", version)
		return
	}
	// The UCI/JSON config contract as JSON, for firmware and tooling that
	// generates the config and needs to validate against this exact binary.
	if len(os.Args) == 2 && (os.Args[1] == "--dump-schema" || os.Args[1] == "dump-schema") {
		if err := webui.WriteSchema(os.Stdout, version); err != nil {
			fmt.Fprintln(os.Stderr, "error:", err)
			os.Exit(1)
		}
		return
	}
	// Before either server starts, so both are covered.
	applyMemoryLimit()
	if len(os.Args) >= 2 && os.Args[1] == "udpbd" {
		runUDPBD()
		return
	}
	if len(os.Args) >= 2 && os.Args[1] == "smb" {
		runSMB()
		return
	}
	if len(os.Args) >= 2 && os.Args[1] == "webui" {
		runWebUI()
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
	bdStatusPort := fs.Int("status-port", envInt("STATUS_PORT", -1),
		"UDP port answering router status queries; 0 disables")
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
		Image: *image, Bind: *bind, Port: *port, ReadOnly: *readOnly,
		StatusPort: *bdStatusPort, Log: logger,
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

// runSMB serves SMBv1 to Open-PS2-Loader.
//
// Unified with the other servers as a subcommand on the same binary rather
// than a separate download, matching how the desktop build ships. Edge served
// only UDPFS and UDPBD before this, so a router could not answer OPL's network
// game list or POPSTARTER at all.
func runSMB() {
	fs := flag.NewFlagSet("smb", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	var shares shareList
	fs.Var(&shares, "share", "a share as NAME=PATH (repeatable)")
	root := fs.String("root", env("FSROOT", ""), "shorthand for --share games=PATH")
	bind := fs.String("bind", env("BIND", "0.0.0.0"), "IPv4 bind address")
	// Not 445. Ports below 1024 are privileged on Linux, and both the OpenWrt
	// init and the systemd unit run Edge unprivileged. See smb.DefaultPort.
	port := fs.Int("port", envInt("SMB_PORT", smb.DefaultPort), "TCP port (set OPL's SMB Port to match)")
	readOnly := fs.Bool("read-only", envBool("RO", false), "serve the shares read-only")
	// Negative means the standard discovery port. A box already running Edge's
	// udpfs owns that address, so the bind failure is logged and ignored --
	// see smb.Config.StatusPort.
	statusPort := fs.Int("status-port", envInt("STATUS_PORT", -1),
		"UDP port answering router status queries; 0 disables")
	logFormat := fs.String("log-format", env("LOG_FORMAT", "text"), "text or json")
	verbose := fs.Bool("verbose", envBool("VERBOSE", false), "verbose protocol logging")
	quiet := fs.Bool("quiet", false, "suppress informational logs")
	if err := fs.Parse(os.Args[2:]); err != nil {
		os.Exit(2)
	}
	if *root != "" {
		shares = append(shares, "games="+*root)
	}
	if len(shares) == 0 {
		fmt.Fprintln(os.Stderr, "error: --share NAME=PATH (or --root PATH) is required")
		os.Exit(2)
	}
	if *logFormat != "text" && *logFormat != "json" {
		fmt.Fprintln(os.Stderr, "error: --log-format must be text or json")
		os.Exit(2)
	}
	logger := edgelog.New(os.Stdout, *logFormat, *quiet, *verbose)

	var parsed []smb.Share
	for _, spec := range shares {
		name, path, ok := strings.Cut(spec, "=")
		if !ok || name == "" || path == "" {
			fmt.Fprintf(os.Stderr, "error: --share %q is not NAME=PATH\n", spec)
			os.Exit(2)
		}
		r, err := filesystem.Open(path)
		if err != nil {
			fmt.Fprintf(os.Stderr, "error: share %q: %v\n", name, err)
			os.Exit(1)
		}
		parsed = append(parsed, smb.Share{Name: name, Root: r})
	}

	server, err := smb.New(smb.Config{
		Shares: parsed, Bind: *bind, Port: *port, ReadOnly: *readOnly,
		StatusPort: *statusPort, Log: logger,
	})
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
	if err := server.Listen(); err != nil {
		// Naming the privileged-port case explicitly: it is the single most
		// likely reason this fails, and "permission denied" alone sends people
		// looking at file permissions rather than at the port number.
		if *port < 1024 {
			fmt.Fprintf(os.Stderr,
				"error: could not bind port %d: %v\n"+
					"Ports below 1024 need root or CAP_NET_BIND_SERVICE. "+
					"Use --port %d and set OPL's SMB Port to match.\n",
				*port, err, smb.DefaultPort)
		} else {
			fmt.Fprintln(os.Stderr, "error:", err)
		}
		os.Exit(1)
	}
	addr := server.Addr()
	logger.Info("SMB listening", map[string]any{
		"addr": addr.String(), "shares": len(parsed), "read_only": *readOnly})
	for _, sh := range parsed {
		logger.Info("SMB share", map[string]any{"name": sh.Name, "root": sh.Root.Path()})
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	go func() {
		<-ctx.Done()
		_ = server.Close()
	}()
	if err := server.Serve(); err != nil {
		logger.Error("server stopped", map[string]any{"error": err})
		os.Exit(1)
	}
}

// shareList collects repeated --share flags.
type shareList []string

func (s *shareList) String() string     { return strings.Join(*s, ",") }
func (s *shareList) Set(v string) error { *s = append(*s, v); return nil }

// runWebUI serves the embedded web dashboard and API for managing Edge.
func runWebUI() {
	fs := flag.NewFlagSet("webui", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	// Loopback, not 0.0.0.0. This serves a management API that rewrites
	// configuration, restarts services and lists directories; reaching it from
	// another machine should be something the operator asked for.
	bind := fs.String("bind", env("WEBUI_BIND", "127.0.0.1"), "IPv4 bind address (non-loopback needs --auth-pass or --insecure)")
	port := fs.Int("webui-port", envInt("WEBUI_PORT", webui.DefaultPort), "HTTP port (0 uses 8082 or OS assigned)")
	configFile := fs.String("config-file", env("CONFIG_FILE", "/etc/ps2servers-edge/config.json"), "path to config file")
	logLines := fs.Int("log-lines", envInt("LOG_LINES", 1000), "number of log lines to buffer")
	noBrowse := fs.Bool("no-browse", envBool("NO_BROWSE", false), "disable file browser API endpoint")
	browseAnywhere := fs.Bool("browse-anywhere", envBool("BROWSE_ANYWHERE", false),
		"let the file browser leave the configured server directories")
	authUser := fs.String("auth-user", env("WEBUI_USER", "admin"), "HTTP basic auth username")
	authPass := fs.String("auth-pass", env("WEBUI_PASS", ""), "HTTP basic auth password (empty disables auth; only allowed on loopback)")
	insecure := fs.Bool("insecure", envBool("WEBUI_INSECURE", false),
		"allow a non-loopback bind without a password; one is generated and logged")
	maxLogClients := fs.Int("max-log-clients", envInt("MAX_LOG_CLIENTS", webui.DefaultMaxLogClients),
		"maximum concurrent /api/logs streams")
	logFormat := fs.String("log-format", env("LOG_FORMAT", "text"), "text or json")
	verbose := fs.Bool("verbose", envBool("VERBOSE", false), "verbose logging")
	quiet := fs.Bool("quiet", false, "suppress informational logs")

	if err := fs.Parse(os.Args[2:]); err != nil {
		os.Exit(2)
	}

	// Whether --config-file was passed, not what it was set to. The shipped
	// systemd env file passes the default path verbatim, and comparing the
	// value against the default silently discarded it on any box with uci.
	configFileSet := false
	fs.Visit(func(f *flag.Flag) {
		if f.Name == "config-file" {
			configFileSet = true
		}
	})
	if os.Getenv("CONFIG_FILE") != "" {
		configFileSet = true
	}

	logBuffer := webui.NewLogBuffer(*logLines)
	// Tee into the ring buffer, or the dashboard's log view has no producer at
	// all: LogBuffer.Write was constructed and handed over, and then nothing
	// ever called it, so /api/logs streamed an empty buffer forever.
	//
	// This still only carries the web UI's own output. udpfs, smb and udpbd run
	// as separate processes under both procd and systemd, so their logs are in
	// logread/journalctl and are not ours to tee.
	logger := edgelog.New(io.MultiWriter(os.Stdout, logBuffer), *logFormat, *quiet, *verbose)

	srv, err := webui.New(webui.Config{
		Port:           *port,
		Bind:           *bind,
		ConfigFile:     *configFile,
		ConfigFileSet:  configFileSet,
		LogLines:       *logLines,
		NoBrowse:       *noBrowse,
		BrowseAnywhere: *browseAnywhere,
		Username:       *authUser,
		Password:       *authPass,
		AllowInsecure:  *insecure,
		MaxLogClients:  *maxLogClients,
		Version:        version,
		Log:            logger,
		LogBuffer:      logBuffer,
	})
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	if err := srv.Serve(ctx); err != nil {
		logger.Error("webui server stopped", map[string]any{"error": err})
		os.Exit(1)
	}
}
