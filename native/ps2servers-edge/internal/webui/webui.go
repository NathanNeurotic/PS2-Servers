package webui

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"fmt"
	"io/fs"
	"net"
	"net/http"
	"strings"
	"time"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
)

// DefaultPort is the web UI's HTTP port.
const DefaultPort = 8082

// DefaultMaxLogClients bounds concurrent /api/logs streams.
const DefaultMaxLogClients = 8

type Config struct {
	Port       int
	Bind       string
	ConfigFile string
	// ConfigFileSet reports whether --config-file was actually passed. See
	// DetectBackend for why the value alone is not enough.
	ConfigFileSet bool
	LogLines      int
	NoBrowse      bool
	// BrowseRoots bounds /api/browse. Empty with BrowseAnywhere false means
	// "derive them from the configuration".
	BrowseRoots []string
	// BrowseAnywhere removes the boundary entirely. Opt-in, and logged loudly.
	BrowseAnywhere bool
	// Username and Password enable HTTP Basic auth. An empty Password means no
	// authentication, which is only allowed on a loopback bind.
	Username string
	Password string
	// AllowInsecure permits a non-loopback bind with no password. Opt-in,
	// because it publishes a management API to the whole LAN.
	AllowInsecure bool
	MaxLogClients int
	Version       string
	Log           *logging.Logger
	LogBuffer     *LogBuffer
}

type Server struct {
	cfg    Config
	server *http.Server

	// generatedPassword is non-empty when Serve invented one, so the caller can
	// print it exactly once.
	generatedPassword string
}

// ErrInsecureBind is returned when the server would publish an unauthenticated
// management API beyond loopback.
var ErrInsecureBind = errors.New(
	"refusing to serve the web UI on a non-loopback address without a password: " +
		"pass --auth-user/--auth-pass, or --insecure to accept the risk")

// isLoopbackBind reports whether an address reaches only this machine.
func isLoopbackBind(bind string) bool {
	if bind == "" {
		return false // empty means all interfaces
	}
	ip := net.ParseIP(bind)
	if ip == nil {
		return strings.EqualFold(bind, "localhost")
	}
	return ip.IsLoopback()
}

func New(cfg Config) (*Server, error) {
	if cfg.LogLines <= 0 {
		cfg.LogLines = 1000
	}
	if cfg.LogBuffer == nil {
		cfg.LogBuffer = NewLogBuffer(cfg.LogLines)
	}
	if cfg.MaxLogClients <= 0 {
		cfg.MaxLogClients = DefaultMaxLogClients
	}
	if cfg.Username == "" {
		cfg.Username = "admin"
	}

	s := &Server{cfg: cfg}

	// The dashboard reconfigures servers, restarts them and reads directories.
	// Publishing that to the LAN with no credential should be a decision, so
	// this either invents one, or refuses, rather than doing it quietly.
	if !isLoopbackBind(cfg.Bind) && cfg.Password == "" {
		if !cfg.AllowInsecure {
			return nil, ErrInsecureBind
		}
	}
	return s, nil
}

// GeneratedPassword reports a password Serve invented, or "".
func (s *Server) GeneratedPassword() string { return s.generatedPassword }

// authorized checks HTTP Basic credentials in constant time.
func (s *Server) authorized(r *http.Request) bool {
	if s.cfg.Password == "" {
		return true
	}
	user, pass, ok := r.BasicAuth()
	if !ok {
		return false
	}
	// Both compared, and both unconditionally, so the reply time does not say
	// which half was wrong.
	userOK := subtle.ConstantTimeCompare([]byte(user), []byte(s.cfg.Username)) == 1
	passOK := subtle.ConstantTimeCompare([]byte(pass), []byte(s.cfg.Password)) == 1
	return userOK && passOK
}

func (s *Server) withAuth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !s.authorized(r) {
			w.Header().Set("WWW-Authenticate", `Basic realm="PS2 Servers Edge", charset="UTF-8"`)
			jsonError(w, http.StatusUnauthorized, "authentication required")
			return
		}
		next.ServeHTTP(w, r)
	})
}

// browseRoots resolves which directories /api/browse may list.
//
// Derived from the configuration rather than defaulting to the filesystem root:
// the browser exists to pick a games folder, and every path worth picking is at
// or under a directory this box already serves.
func browseRoots(cfg Config, backend ConfigBackend) []string {
	if cfg.BrowseAnywhere {
		return nil
	}
	if len(cfg.BrowseRoots) > 0 {
		return cfg.BrowseRoots
	}
	loaded, err := backend.Load()
	if err != nil || loaded == nil {
		return nil
	}
	seen := map[string]bool{}
	var roots []string
	add := func(p string) {
		if p == "" || seen[p] {
			return
		}
		seen[p] = true
		roots = append(roots, p)
	}
	add(loaded.UDPFS.Root)
	// smb.share is NAME=PATH pairs separated by spaces.
	for _, entry := range strings.Fields(loaded.SMB.Share) {
		if _, path, found := strings.Cut(entry, "="); found {
			add(path)
		}
	}
	return roots
}

func (s *Server) Serve(ctx context.Context) error {
	backend := DetectBackend(s.cfg.ConfigFile, s.cfg.ConfigFileSet)
	manager := DetectServiceManager(s.cfg.Log)

	bind := s.cfg.Bind
	if bind == "" {
		bind = "0.0.0.0"
	}

	// Insecure but explicitly allowed: invent a password rather than serve
	// none, so "I accepted the risk of exposing it" does not also mean "anyone
	// who finds it is an administrator".
	if !isLoopbackBind(bind) && s.cfg.Password == "" && s.cfg.AllowInsecure {
		buf := make([]byte, 12)
		if _, err := rand.Read(buf); err != nil {
			return fmt.Errorf("failed to generate a web UI password: %w", err)
		}
		s.cfg.Password = base64.RawURLEncoding.EncodeToString(buf)
		s.generatedPassword = s.cfg.Password
	}

	roots := browseRoots(s.cfg, backend)
	if !s.cfg.NoBrowse && len(roots) == 0 && s.cfg.Log != nil {
		s.cfg.Log.Warn("file browser is not confined to a directory", map[string]any{
			"reason": "no server roots configured (or --browse-anywhere)",
			"effect": "/api/browse can list any absolute path on this device",
		})
	}

	api := &API{
		configBackend: backend,
		manager:       manager,
		logBuffer:     s.cfg.LogBuffer,
		noBrowse:      s.cfg.NoBrowse,
		version:       s.cfg.Version,
		log:           s.cfg.Log,
		browseRoots:   roots,
		maxLogClients: s.cfg.MaxLogClients,
		logClients:    make(chan struct{}, s.cfg.MaxLogClients),
	}

	mux := http.NewServeMux()

	mux.HandleFunc("/api/status", api.HandleStatus)
	mux.HandleFunc("/api/config", api.HandleConfig)
	mux.HandleFunc("/api/config/defaults", api.HandleConfigDefaults)
	mux.HandleFunc("/api/config/reset", api.HandleConfigReset)
	mux.HandleFunc("/api/restart", api.HandleRestart)
	mux.HandleFunc("/api/logs", api.HandleLogs)
	mux.HandleFunc("/api/browse", api.HandleBrowse)
	mux.HandleFunc("/api/info", api.HandleInfo)

	subFS, err := fs.Sub(assets, "assets")
	if err != nil {
		return fmt.Errorf("failed to create sub filesystem: %w", err)
	}
	mux.Handle("/", http.FileServer(http.FS(subFS)))

	s.server = &http.Server{
		Handler:           s.withAuth(mux),
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       120 * time.Second,
	}

	var listener net.Listener

	if s.cfg.Port > 0 {
		l, err := net.Listen("tcp", net.JoinHostPort(bind, fmt.Sprint(s.cfg.Port)))
		if err != nil {
			return fmt.Errorf("failed to bind to user configured WebUI port %d: %w", s.cfg.Port, err)
		}
		listener = l
	} else {
		l, err := net.Listen("tcp", net.JoinHostPort(bind, fmt.Sprint(DefaultPort)))
		if err != nil {
			if s.cfg.Log != nil {
				s.cfg.Log.Warn("Default WebUI port taken, assigning random port",
					map[string]any{"port": DefaultPort, "err": err.Error()})
			}
			l, err = net.Listen("tcp", net.JoinHostPort(bind, "0"))
			if err != nil {
				return fmt.Errorf("failed to bind to any port: %w", err)
			}
		}
		listener = l
	}

	addr, ok := listener.Addr().(*net.TCPAddr)
	if !ok {
		listener.Close()
		return fmt.Errorf("unexpected listener address type %T", listener.Addr())
	}
	if s.cfg.Log != nil {
		s.cfg.Log.Info(fmt.Sprintf("Web UI available at http://%s",
			net.JoinHostPort(bind, fmt.Sprint(addr.Port))), map[string]any{
			"authenticated": s.cfg.Password != "",
			"browse":        !s.cfg.NoBrowse,
			"browse_roots":  roots,
		})
		if s.generatedPassword != "" {
			s.cfg.Log.Info("Web UI login (generated for this run, changes on restart)",
				map[string]any{"user": s.cfg.Username, "password": s.generatedPassword})
		}
	}

	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := s.server.Shutdown(shutdownCtx); err != nil && s.cfg.Log != nil {
			s.cfg.Log.Debug("web UI shutdown", map[string]any{"error": err.Error()})
		}
	}()

	err = s.server.Serve(listener)
	if err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}

func (s *Server) Close() error {
	if s.server != nil {
		return s.server.Close()
	}
	return nil
}
