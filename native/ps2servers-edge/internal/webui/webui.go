package webui

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"net"
	"net/http"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
)

type Config struct {
	Port       int
	Bind       string
	ConfigFile string
	LogLines   int
	NoBrowse   bool
	Version    string
	Log        *logging.Logger
	LogBuffer  *LogBuffer
}

type Server struct {
	cfg    Config
	server *http.Server
}

func New(cfg Config) (*Server, error) {
	if cfg.LogLines <= 0 {
		cfg.LogLines = 1000
	}
	if cfg.LogBuffer == nil {
		cfg.LogBuffer = NewLogBuffer(cfg.LogLines)
	}

	return &Server{cfg: cfg}, nil
}

func (s *Server) Serve(ctx context.Context) error {
	backend := DetectBackend(s.cfg.ConfigFile)
	manager := DetectServiceManager(s.cfg.Log)

	api := &API{
		configBackend: backend,
		manager:       manager,
		logBuffer:     s.cfg.LogBuffer,
		noBrowse:      s.cfg.NoBrowse,
		version:       s.cfg.Version,
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
		Handler: mux,
	}

	bind := s.cfg.Bind
	if bind == "" {
		bind = "0.0.0.0"
	}

	var listener net.Listener

	if s.cfg.Port > 0 {
		l, err := net.Listen("tcp", fmt.Sprintf("%s:%d", bind, s.cfg.Port))
		if err != nil {
			s.cfg.Log.Warn("Failed to bind to user configured WebUI port", map[string]any{"port": s.cfg.Port, "err": err.Error()})
			return nil
		}
		listener = l
	} else {
		l, err := net.Listen("tcp", fmt.Sprintf("%s:8082", bind))
		if err != nil {
			s.cfg.Log.Warn("Default WebUI port 8082 taken, assigning random port", map[string]any{"err": err.Error()})
			l, err = net.Listen("tcp", fmt.Sprintf("%s:0", bind))
			if err != nil {
				return fmt.Errorf("failed to bind to any port: %w", err)
			}
		}
		listener = l
	}

	addr := listener.Addr().(*net.TCPAddr)
	s.cfg.Log.Info(fmt.Sprintf("Web UI available at http://%s:%d", bind, addr.Port), nil)

	go func() {
		<-ctx.Done()
		s.server.Shutdown(context.Background())
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
