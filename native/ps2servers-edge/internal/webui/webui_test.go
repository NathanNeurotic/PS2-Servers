package webui

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
)

func TestLogBuffer(t *testing.T) {
	lb := NewLogBuffer(3)

	lb.Write([]byte("line 1\nline 2\n"))
	lines := lb.Lines()
	if len(lines) != 2 {
		t.Fatalf("expected 2 lines, got %d", len(lines))
	}
	if lines[0] != "line 1" || lines[1] != "line 2" {
		t.Fatalf("unexpected lines: %v", lines)
	}

	// Test ring buffer wrapping
	lb.Write([]byte("line 3\nline 4\n"))
	lines = lb.Lines()
	if len(lines) != 3 {
		t.Fatalf("expected 3 lines after overflow, got %d", len(lines))
	}
	if lines[0] != "line 2" || lines[1] != "line 3" || lines[2] != "line 4" {
		t.Fatalf("unexpected lines after overflow: %v", lines)
	}

	// Test Subscribe
	ch, unsub := lb.Subscribe()
	defer unsub()

	lb.Write([]byte("line 5\n"))

	select {
	case msg := <-ch:
		if msg != "line 5" {
			t.Fatalf("expected 'line 5', got %q", msg)
		}
	case <-time.After(1 * time.Second):
		t.Fatal("timed out waiting for subscription line")
	}
}

func TestJSONBackend(t *testing.T) {
	dir := t.TempDir()
	cfgPath := filepath.Join(dir, "config.json")

	backend := &jsonBackend{path: cfgPath}

	// Load non-existent -> returns default
	cfg, err := backend.Load()
	if err != nil {
		t.Fatalf("Load failed: %v", err)
	}
	if cfg.UDPFS.Port != 62966 {
		t.Fatalf("expected default UDPFS port 62966, got %d", cfg.UDPFS.Port)
	}

	// Modify and save
	cfg.UDPFS.Port = 12345
	cfg.SMB.Enabled = true
	if err := backend.Save(cfg); err != nil {
		t.Fatalf("Save failed: %v", err)
	}

	// Reload and verify persistence
	loaded, err := backend.Load()
	if err != nil {
		t.Fatalf("Load after save failed: %v", err)
	}
	if loaded.UDPFS.Port != 12345 {
		t.Fatalf("expected UDPFS port 12345, got %d", loaded.UDPFS.Port)
	}
	if !loaded.SMB.Enabled {
		t.Fatal("expected SMB enabled true")
	}
}

func TestAPIEndpoints(t *testing.T) {
	dir := t.TempDir()
	cfgPath := filepath.Join(dir, "config.json")
	backend := &jsonBackend{path: cfgPath}
	lb := NewLogBuffer(10)
	logger := logging.New(os.Stdout, "text", true, false)

	api := &API{
		configBackend: backend,
		manager:       &genericManager{log: logger},
		logBuffer:     lb,
		noBrowse:      false,
		version:       "0.5.0-test",
	}

	// Test GET /api/info
	req := httptest.NewRequest("GET", "/api/info", nil)
	rec := httptest.NewRecorder()
	api.HandleInfo(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
	var info map[string]string
	if err := json.NewDecoder(rec.Body).Decode(&info); err != nil {
		t.Fatalf("failed to decode info JSON: %v", err)
	}
	if info["version"] != "0.5.0-test" {
		t.Fatalf("expected version 0.5.0-test, got %s", info["version"])
	}

	// Test GET /api/config/defaults
	req = httptest.NewRequest("GET", "/api/config/defaults", nil)
	rec = httptest.NewRecorder()
	api.HandleConfigDefaults(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
	var defaults EdgeConfig
	if err := json.NewDecoder(rec.Body).Decode(&defaults); err != nil {
		t.Fatalf("failed to decode defaults: %v", err)
	}
	if defaults.UDPFS.Port != 62966 {
		t.Fatalf("expected default port 62966, got %d", defaults.UDPFS.Port)
	}

	// Test POST /api/config
	newCfg := DefaultConfig()
	newCfg.UDPFS.Port = 54321
	body, _ := json.Marshal(newCfg)

	req = httptest.NewRequest("POST", "/api/config", bytes.NewReader(body))
	rec = httptest.NewRecorder()
	api.HandleConfig(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 on config post, got %d", rec.Code)
	}

	// Test GET /api/config
	req = httptest.NewRequest("GET", "/api/config", nil)
	rec = httptest.NewRecorder()
	api.HandleConfig(rec, req)

	var fetched EdgeConfig
	json.NewDecoder(rec.Body).Decode(&fetched)
	if fetched.UDPFS.Port != 54321 {
		t.Fatalf("expected updated port 54321, got %d", fetched.UDPFS.Port)
	}
}

func TestServerPortFallback(t *testing.T) {
	logger := logging.New(os.Stdout, "text", true, false)
	dir := t.TempDir()

	srv, err := New(Config{
		Port:       0, // trigger default / fallback logic
		Bind:       "127.0.0.1",
		ConfigFile: filepath.Join(dir, "config.json"),
		Version:    "test",
		Log:        logger,
	})
	if err != nil {
		t.Fatalf("New failed: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	errCh := make(chan error, 1)
	go func() {
		errCh <- srv.Serve(ctx)
	}()

	time.Sleep(100 * time.Millisecond)
	cancel()

	err = <-errCh
	if err != nil {
		t.Fatalf("Serve returned error: %v", err)
	}
}
