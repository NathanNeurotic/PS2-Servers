package webui

import (
	"bytes"
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/protocol"
)

func testAPI(t *testing.T, mutate func(*API)) *API {
	t.Helper()
	dir := t.TempDir()
	logger := logging.New(os.Stdout, "text", true, false)
	api := &API{
		configBackend: &jsonBackend{path: filepath.Join(dir, "config.json")},
		manager:       &genericManager{log: logger},
		logBuffer:     NewLogBuffer(10),
		version:       "0.5.0-test",
		log:           logger,
		maxLogClients: 2,
		logClients:    make(chan struct{}, 2),
	}
	if mutate != nil {
		mutate(api)
	}
	return api
}

func postJSON(path string, body any) *http.Request {
	buf, _ := json.Marshal(body)
	r := httptest.NewRequest("POST", path, bytes.NewReader(buf))
	r.Header.Set("Content-Type", "application/json")
	return r
}

// Escaped, because these paths deliberately contain spaces and angle brackets.
func browseRequest(path string) *http.Request {
	return httptest.NewRequest("GET", "/api/browse?path="+url.QueryEscape(path), nil)
}

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
	api := testAPI(t, nil)

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

	newCfg := DefaultConfig()
	newCfg.UDPFS.Port = 54321
	rec = httptest.NewRecorder()
	api.HandleConfig(rec, postJSON("/api/config", newCfg))
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 on config post, got %d (%s)", rec.Code, rec.Body)
	}

	req = httptest.NewRequest("GET", "/api/config", nil)
	rec = httptest.NewRecorder()
	api.HandleConfig(rec, req)

	var fetched EdgeConfig
	json.NewDecoder(rec.Body).Decode(&fetched)
	if fetched.UDPFS.Port != 54321 {
		t.Fatalf("expected updated port 54321, got %d", fetched.UDPFS.Port)
	}
}

// A cross-origin form post can only carry url-encoded, multipart or text/plain
// bodies. Requiring application/json is what stops any page the operator opens
// from reconfiguring the box behind them.
func TestWritesRequireJSONContentType(t *testing.T) {
	api := testAPI(t, nil)
	body, _ := json.Marshal(DefaultConfig())

	for _, ct := range []string{"", "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"} {
		r := httptest.NewRequest("POST", "/api/config", bytes.NewReader(body))
		if ct != "" {
			r.Header.Set("Content-Type", ct)
		}
		rec := httptest.NewRecorder()
		api.HandleConfig(rec, r)
		if rec.Code != http.StatusUnsupportedMediaType {
			t.Fatalf("Content-Type %q: expected 415, got %d", ct, rec.Code)
		}
	}

	// The charset parameter must not defeat the check.
	r := httptest.NewRequest("POST", "/api/config", bytes.NewReader(body))
	r.Header.Set("Content-Type", "application/json; charset=utf-8")
	rec := httptest.NewRecorder()
	api.HandleConfig(rec, r)
	if rec.Code != http.StatusOK {
		t.Fatalf("application/json with charset should be accepted, got %d", rec.Code)
	}
}

func TestWritesRefuseCrossSiteFetches(t *testing.T) {
	api := testAPI(t, nil)
	r := postJSON("/api/config", DefaultConfig())
	r.Header.Set("Sec-Fetch-Site", "cross-site")
	rec := httptest.NewRecorder()
	api.HandleConfig(rec, r)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for a cross-site write, got %d", rec.Code)
	}
}

// The systemd manager concatenates this into a unit name.
func TestRestartRejectsUnknownServices(t *testing.T) {
	api := testAPI(t, nil)
	for _, name := range []string{"", "sshd", "../../etc", "udpfs evil", "UDPFS"} {
		rec := httptest.NewRecorder()
		api.HandleRestart(rec, postJSON("/api/restart", map[string]string{"service": name}))
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("service %q: expected 400, got %d", name, rec.Code)
		}
	}
	for _, name := range []string{"all", "udpfs", "smb", "udpbd"} {
		rec := httptest.NewRecorder()
		api.HandleRestart(rec, postJSON("/api/restart", map[string]string{"service": name}))
		if rec.Code != http.StatusOK {
			t.Fatalf("service %q: expected 200, got %d", name, rec.Code)
		}
	}
}

func TestConfigValidationRejectsInjectionAndNonsense(t *testing.T) {
	// A newline used to become a second uci command on the router.
	withRoot := DefaultConfig()
	withRoot.UDPFS.Root = "/mnt/games\nset firewall.@zone[0].input='ACCEPT'"

	badPort := DefaultConfig()
	badPort.SMB.Port = 70000

	badProto := DefaultConfig()
	badProto.UDPFS.Protocol = "whatever"

	badFormat := DefaultConfig()
	badFormat.UDPBD.LogFormat = "yaml"

	badSector := DefaultConfig()
	badSector.UDPFS.SectorSize = 0

	for name, cfg := range map[string]*EdgeConfig{
		"newline in root":   withRoot,
		"port out of range": badPort,
		"unknown protocol":  badProto,
		"unknown format":    badFormat,
		"zero sector size":  badSector,
	} {
		if err := ValidateConfig(cfg); err == nil {
			t.Fatalf("%s: expected rejection", name)
		}
	}

	// A path with a space is legitimate and must survive: the old uci batch
	// writer broke it, which is a plain bug for anyone with "My Games".
	spaced := DefaultConfig()
	spaced.UDPFS.Root = "/mnt/My Games"
	if err := ValidateConfig(spaced); err != nil {
		t.Fatalf("a path with a space must be accepted: %v", err)
	}
	if err := ValidateConfig(DefaultConfig()); err != nil {
		t.Fatalf("the shipped defaults must validate: %v", err)
	}
}

func TestBrowseStaysInsideItsRoots(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "games"), 0o755); err != nil {
		t.Fatal(err)
	}
	outside := t.TempDir()

	api := testAPI(t, func(a *API) { a.browseRoots = []string{root} })

	// Inside is fine.
	rec := httptest.NewRecorder()
	api.HandleBrowse(rec, browseRequest(filepath.Join(root, "games")))
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 inside the root, got %d (%s)", rec.Code, rec.Body)
	}

	// Outside is not -- including via a traversal that only escapes once
	// normalised, which is why Clean has to run before the check.
	for _, p := range []string{
		outside,
		filepath.Join(root, "..", ".."),
		filepath.Join(root, "games", "..", "..", ".."),
		root + "-sibling", // prefix of the root, not a child of it
	} {
		rec := httptest.NewRecorder()
		api.HandleBrowse(rec, browseRequest(p))
		if rec.Code != http.StatusForbidden {
			t.Fatalf("path %q: expected 403, got %d (%s)", p, rec.Code, rec.Body)
		}
	}
}

func TestBrowseOffersNoParentOutOfTheRoot(t *testing.T) {
	root := t.TempDir()
	api := testAPI(t, func(a *API) { a.browseRoots = []string{root} })

	rec := httptest.NewRecorder()
	api.HandleBrowse(rec, browseRequest(root))
	// Checked before decoding: an error body also decodes into browseResponse
	// with an empty Parent, so without this the test would report "Up is
	// correctly suppressed" when browsing was in fact refused outright.
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 listing the root, got %d (%s)", rec.Code, rec.Body)
	}
	var resp browseResponse
	if err := json.NewDecoder(rec.Body).Decode(&resp); err != nil {
		t.Fatal(err)
	}
	if resp.Parent != "" {
		t.Fatalf("the root must offer no parent, got %q -- Up would walk out of the share", resp.Parent)
	}
}

// The lexical containment test approved this: filepath.Rel does not follow
// links, so a symlink inside the root produced a name under the root while
// os.ReadDir opened a directory outside it.
func TestBrowseRefusesASymlinkOutOfTheRoot(t *testing.T) {
	root := t.TempDir()
	outside := t.TempDir()
	if err := os.WriteFile(filepath.Join(outside, "secret"), []byte("no"), 0o600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "elsewhere")
	if err := os.Symlink(outside, link); err != nil {
		t.Skipf("cannot create a symlink here: %v", err)
	}

	api := testAPI(t, func(a *API) { a.browseRoots = []string{root} })
	rec := httptest.NewRecorder()
	api.HandleBrowse(rec, browseRequest(link))
	if rec.Code != http.StatusForbidden {
		t.Fatalf("a symlink out of the share listed a directory outside it: "+
			"got %d (%s)", rec.Code, rec.Body)
	}
}

// The other half: a root reached through a symlink is ordinary
// (/srv/ps2 -> /mnt/disk2/ps2), and resolving the path without also resolving
// the root would make every such share fail its own containment test.
func TestBrowseServesASymlinkedRoot(t *testing.T) {
	real := t.TempDir()
	if err := os.MkdirAll(filepath.Join(real, "games"), 0o755); err != nil {
		t.Fatal(err)
	}
	linkRoot := filepath.Join(t.TempDir(), "linked")
	if err := os.Symlink(real, linkRoot); err != nil {
		t.Skipf("cannot create a symlink here: %v", err)
	}

	api := testAPI(t, func(a *API) { a.browseRoots = []string{linkRoot} })
	rec := httptest.NewRecorder()
	api.HandleBrowse(rec, browseRequest(filepath.Join(linkRoot, "games")))
	if rec.Code != http.StatusOK {
		t.Fatalf("a share reached through a symlink must serve its own files: "+
			"got %d (%s)", rec.Code, rec.Body)
	}
}

func TestBrowseErrorDoesNotEchoTheRequestedPath(t *testing.T) {
	root := t.TempDir()
	api := testAPI(t, func(a *API) { a.browseRoots = []string{root} })

	probe := filepath.Join(root, "<img src=x onerror=alert(1)>")
	rec := httptest.NewRecorder()
	api.HandleBrowse(rec, browseRequest(probe))
	if strings.Contains(rec.Body.String(), "onerror") {
		t.Fatalf("the error echoes the requested path back to the dashboard: %s", rec.Body)
	}
}

func TestBrowseRootsComeFromTheConfiguration(t *testing.T) {
	dir := t.TempDir()
	backend := &jsonBackend{path: filepath.Join(dir, "config.json")}
	cfg := DefaultConfig()
	cfg.UDPFS.Root = "/srv/ps2"
	cfg.SMB.Share = "games=/srv/ps2 extra=/mnt/disk2"
	if err := backend.Save(cfg); err != nil {
		t.Fatal(err)
	}

	roots := browseRoots(Config{}, backend)
	want := []string{"/srv/ps2", "/mnt/disk2"}
	if len(roots) != len(want) {
		t.Fatalf("expected %v, got %v", want, roots)
	}
	for i := range want {
		if roots[i] != want[i] {
			t.Fatalf("expected %v, got %v", want, roots)
		}
	}
}

// The bug this replaces: every server reports the same display name, so a
// substring match on it could never identify which one answered.
func TestStatusIdentifiesTheServiceByFlags(t *testing.T) {
	conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	port := conn.LocalAddr().(*net.UDPAddr).Port

	go func() {
		buf := make([]byte, 64)
		for {
			n, peer, err := conn.ReadFromUDP(buf)
			if err != nil {
				return
			}
			if !protocol.IsStatusQuery(buf[:n]) {
				continue
			}
			reply := protocol.StatusReply{
				Version:  protocol.StatusVersion,
				State:    protocol.StateReady,
				Flags:    protocol.FlagServesUDPFS | protocol.FlagDecompresses,
				Sessions: 3,
				Uptime:   42,
				// Deliberately the real name: it names no service, which is the
				// whole point.
				Name: "PS2 Servers Edge",
			}
			conn.WriteToUDP(reply.Marshal(), peer)
		}
	}()

	got := queryAllStatus(map[string]int{"udpfs": port, "smb": port, "udpbd": port})

	if !got["udpfs"].Running {
		t.Fatal("udpfs answered and set its flag, but was reported as not running")
	}
	if got["udpfs"].State != "ready" || got["udpfs"].Sessions != 3 || got["udpfs"].Uptime != 42 {
		t.Fatalf("udpfs status not decoded: %+v", got["udpfs"])
	}
	// smb and udpbd share the port but did not answer; a reply that is not
	// theirs must not be attributed to them.
	if got["smb"].Running || got["udpbd"].Running {
		t.Fatalf("udpfs's reply was attributed to another service: %+v", got)
	}
}

func TestStatusPortsSkipDisabledListeners(t *testing.T) {
	cfg := DefaultConfig()
	cfg.SMB.StatusPort = 0    // off
	cfg.UDPBD.StatusPort = -1 // the shared port
	ports := statusPorts(cfg)

	if _, ok := ports["smb"]; ok {
		t.Fatal("smb has no status listener; querying it is a guaranteed timeout every poll")
	}
	if ports["udpbd"] != defaultStatusPort {
		t.Fatalf("-1 means the standard port, got %d", ports["udpbd"])
	}
	if ports["udpfs"] != cfg.UDPFS.Port {
		t.Fatalf("udpfs answers on its own discovery port, got %d", ports["udpfs"])
	}
}

func TestNonLoopbackBindNeedsAPasswordOrConsent(t *testing.T) {
	base := Config{Port: 0, Version: "test", Log: logging.New(os.Stdout, "text", true, false)}

	for _, bind := range []string{"0.0.0.0", "192.168.1.10", ""} {
		cfg := base
		cfg.Bind = bind
		if _, err := New(cfg); err == nil {
			t.Fatalf("bind %q with no password should be refused", bind)
		}
	}
	for _, bind := range []string{"127.0.0.1", "127.0.0.5", "localhost"} {
		cfg := base
		cfg.Bind = bind
		if _, err := New(cfg); err != nil {
			t.Fatalf("bind %q is loopback and should need no password: %v", bind, err)
		}
	}

	withPass := base
	withPass.Bind = "0.0.0.0"
	withPass.Password = "hunter2"
	if _, err := New(withPass); err != nil {
		t.Fatalf("a password should permit a LAN bind: %v", err)
	}

	consented := base
	consented.Bind = "0.0.0.0"
	consented.AllowInsecure = true
	if _, err := New(consented); err != nil {
		t.Fatalf("--insecure should permit a LAN bind: %v", err)
	}
}

func TestAuthRejectsBadCredentials(t *testing.T) {
	s := &Server{cfg: Config{Username: "admin", Password: "hunter2"}}
	handler := s.withAuth(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, httptest.NewRequest("GET", "/api/config", nil))
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 without credentials, got %d", rec.Code)
	}
	if rec.Header().Get("WWW-Authenticate") == "" {
		t.Fatal("a 401 without WWW-Authenticate gives the browser no way to ask")
	}

	r := httptest.NewRequest("GET", "/api/config", nil)
	r.SetBasicAuth("admin", "wrong")
	rec = httptest.NewRecorder()
	handler.ServeHTTP(rec, r)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 for a wrong password, got %d", rec.Code)
	}

	r = httptest.NewRequest("GET", "/api/config", nil)
	r.SetBasicAuth("admin", "hunter2")
	rec = httptest.NewRecorder()
	handler.ServeHTTP(rec, r)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 with the right password, got %d", rec.Code)
	}
}

func TestLogStreamsAreCapped(t *testing.T) {
	api := testAPI(t, nil) // cap of 2
	api.logClients <- struct{}{}
	api.logClients <- struct{}{}

	rec := httptest.NewRecorder()
	api.HandleLogs(rec, httptest.NewRequest("GET", "/api/logs", nil))
	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503 past the stream cap, got %d", rec.Code)
	}
}

func TestDetectBackendHonoursAnExplicitPath(t *testing.T) {
	// The shipped systemd env file passes exactly this, and comparing the
	// value against the default used to discard it.
	b := DetectBackend(defaultJSONConfigPath, true)
	jb, ok := b.(*jsonBackend)
	if !ok {
		t.Fatalf("an explicit --config-file must select the JSON backend, got %T", b)
	}
	if jb.path != defaultJSONConfigPath {
		t.Fatalf("expected %q, got %q", defaultJSONConfigPath, jb.path)
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
