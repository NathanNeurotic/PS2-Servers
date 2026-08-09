package webui

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/logging"
)

// API groups all HTTP handlers for the web UI.
type API struct {
	configBackend ConfigBackend
	manager       ServiceManager
	logBuffer     *LogBuffer
	noBrowse      bool
	version       string
	log           *logging.Logger

	// browseRoots bounds /api/browse. Empty means "anything absolute", which
	// is only reachable with --browse-anywhere.
	browseRoots []string

	// maxLogClients caps concurrent /api/logs streams.
	maxLogClients int
	logClients    chan struct{}
	// logLines is how many service-log lines one connect fetches.
	logLines int
}

func jsonResponse(w http.ResponseWriter, status int, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	// Nothing here is cacheable and some of it is host configuration; a proxy
	// or a browser holding on to it is at best confusing.
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(data); err != nil && status == http.StatusOK {
		// The header is already written, so this cannot become an error
		// response -- but a truncated body must not look like a clean 200.
		fmt.Fprintf(os.Stderr, "webui: failed to encode response: %v\n", err)
	}
}

func jsonError(w http.ResponseWriter, status int, msg string) {
	jsonResponse(w, status, map[string]string{"error": msg})
}

// requireMethod rejects anything but the method a handler implements.
func requireMethod(w http.ResponseWriter, r *http.Request, method string) bool {
	if r.Method == method {
		return true
	}
	jsonError(w, http.StatusMethodNotAllowed, "Method not allowed")
	return false
}

// sanitizeSSE makes one log line safe to put in a server-sent event.
//
// The framing is line-based: a newline ends a field and a blank line ends the
// event, so a log line containing either would let whatever produced it inject
// extra events into the stream. Log lines here come from journalctl and
// logread, which carry text the servers were given -- filenames and peer
// addresses among it.
func sanitizeSSE(line string) string {
	return strings.NewReplacer("\r", " ", "\n", " ").Replace(line)
}

// maxRequestBody bounds a decoded JSON body.
//
// json.Decoder reads until EOF, so a single string field of arbitrary length is
// allocated whole -- on a 32 MB router that is a denial of service costing one
// POST, and on a loopback bind there is no password in front of it. 64 KiB is
// comfortably above a full EdgeConfig (the shipped defaults encode to well under
// 2 KiB) and far below anything that hurts.
const maxRequestBody = 64 << 10

// requireJSONWrite guards every state-changing handler.
//
// Two separate things, both needed:
//
//   - Content-Type must be application/json. A cross-origin <form> can only
//     send url-encoded, multipart or text/plain bodies, and json.Decoder used
//     to accept all three happily -- so any page the operator happened to visit
//     could POST here and the browser would attach nothing to stop it.
//   - Sec-Fetch-Site, where the browser sends it, must not be cross-site.
//
// Neither is a substitute for authentication (see Config.Auth); they are what
// stops a request the operator's own browser was tricked into making, which
// authentication alone does not.
func requireJSONWrite(w http.ResponseWriter, r *http.Request) bool {
	if !requireMethod(w, r, http.MethodPost) {
		return false
	}
	ct := r.Header.Get("Content-Type")
	if i := strings.IndexByte(ct, ';'); i >= 0 {
		ct = ct[:i]
	}
	if !strings.EqualFold(strings.TrimSpace(ct), "application/json") {
		jsonError(w, http.StatusUnsupportedMediaType,
			"Content-Type must be application/json")
		return false
	}
	switch r.Header.Get("Sec-Fetch-Site") {
	case "cross-site", "same-site":
		jsonError(w, http.StatusForbidden, "cross-origin request refused")
		return false
	}
	return true
}

func (a *API) HandleStatus(w http.ResponseWriter, r *http.Request) {
	if !requireMethod(w, r, http.MethodGet) {
		return
	}

	// Which port each service answers on is configuration, so read it rather
	// than assuming every service is on the shared one. A service with
	// status_port 0 has no listener at all and is skipped instead of being
	// given a guaranteed timeout on every poll.
	cfg, err := a.configBackend.Load()
	if err != nil {
		// Status must still answer: a config the UI cannot read is exactly when
		// someone wants to know whether the servers are alive.
		cfg = nil
	}

	status, err := a.manager.Status(statusPorts(cfg))
	if err != nil {
		jsonError(w, http.StatusInternalServerError, err.Error())
		return
	}

	result := map[string]interface{}{
		"udpfs": status["udpfs"],
		"smb":   status["smb"],
		"udpbd": status["udpbd"],
		"system": map[string]string{
			"version":  a.version,
			"platform": runtime.GOOS,
			"arch":     runtime.GOARCH,
		},
	}
	jsonResponse(w, http.StatusOK, result)
}

func (a *API) HandleConfig(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet {
		cfg, err := a.configBackend.Load()
		if err != nil {
			jsonError(w, http.StatusInternalServerError, err.Error())
			return
		}
		jsonResponse(w, http.StatusOK, cfg)
		return
	}

	if !requireJSONWrite(w, r) {
		return
	}

	var cfg EdgeConfig
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxRequestBody)).Decode(&cfg); err != nil {
		jsonError(w, http.StatusBadRequest, "Invalid JSON")
		return
	}
	// Validated before it reaches a backend. The UCI backend writes these
	// values into a command stream, so a newline in any of them used to be an
	// arbitrary uci command; and a port of 70000 produces a server that will
	// not start, reported as nothing at all.
	if err := ValidateConfig(&cfg); err != nil {
		jsonError(w, http.StatusBadRequest, err.Error())
		return
	}
	if err := a.configBackend.Save(&cfg); err != nil {
		jsonError(w, http.StatusInternalServerError, err.Error())
		return
	}
	jsonResponse(w, http.StatusOK, map[string]bool{"ok": true})
}

func (a *API) HandleConfigDefaults(w http.ResponseWriter, r *http.Request) {
	if !requireMethod(w, r, http.MethodGet) {
		return
	}
	jsonResponse(w, http.StatusOK, DefaultConfig())
}

func (a *API) HandleConfigReset(w http.ResponseWriter, r *http.Request) {
	if !requireJSONWrite(w, r) {
		return
	}
	if err := a.configBackend.Save(DefaultConfig()); err != nil {
		jsonError(w, http.StatusInternalServerError, err.Error())
		return
	}
	jsonResponse(w, http.StatusOK, map[string]bool{"ok": true})
}

func (a *API) HandleRestart(w http.ResponseWriter, r *http.Request) {
	if !requireJSONWrite(w, r) {
		return
	}

	var req struct {
		Service string `json:"service"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxRequestBody)).Decode(&req); err != nil {
		jsonError(w, http.StatusBadRequest, "Invalid JSON")
		return
	}

	// Allowlisted here rather than in the managers, so no backend can ever be
	// reached with an arbitrary name. The systemd manager concatenates this
	// into a unit name, which without this check lets a caller choose which
	// ps2servers-edge@<instance> systemd starts.
	if !restartTargets[req.Service] {
		jsonError(w, http.StatusBadRequest,
			"service must be one of: all, udpfs, smb, udpbd")
		return
	}

	restarted, err := a.manager.Restart(req.Service)
	if err != nil {
		jsonError(w, http.StatusInternalServerError, err.Error())
		return
	}

	// Reported rather than assumed: on OpenWrt the init script is the only
	// per-package control, so asking for one service restarts them all, and
	// the operator should see that rather than infer it.
	jsonResponse(w, http.StatusOK, map[string]interface{}{
		"ok":        true,
		"requested": req.Service,
		"restarted": restarted,
	})
}

func (a *API) HandleLogs(w http.ResponseWriter, r *http.Request) {
	if !requireMethod(w, r, http.MethodGet) {
		return
	}

	// Bounded: each stream is a goroutine plus a buffered channel, and on a
	// 32 MB router an unbounded count is a denial of service that costs one
	// browser tab per unit.
	select {
	case a.logClients <- struct{}{}:
		defer func() { <-a.logClients }()
	default:
		jsonError(w, http.StatusServiceUnavailable,
			fmt.Sprintf("too many log streams (limit %d)", a.maxLogClients))
		return
	}

	flusher, ok := w.(http.Flusher)
	if !ok {
		jsonError(w, http.StatusInternalServerError, "Streaming unsupported")
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Content-Type-Options", "nosniff")

	// Subscribe before replaying the backlog, so a line written between the
	// two is delivered late rather than lost.
	ch, unsub := a.logBuffer.Subscribe()
	defer unsub()

	// The servers' own output first. udpfs, smb and udpbd are separate
	// processes, so this is the part an operator actually came looking for --
	// and until now the pane could not show it at all, because the only thing
	// this process can see live is itself.
	//
	// A snapshot taken at connect. Following logread/journalctl would mean a
	// child process per open browser tab, which on a 32 MB router is a cost
	// with no ceiling; the page re-connects to refresh.
	if serviceLines, err := a.manager.ServiceLogs(a.logLines); err != nil {
		fmt.Fprintf(w, "data: [service logs unavailable: %s]\n\n",
			sanitizeSSE(err.Error()))
	} else if len(serviceLines) == 0 {
		fmt.Fprint(w, "data: [no service log lines yet]\n\n")
	} else {
		fmt.Fprintf(w, "data: [%d line(s) from the servers' own log]\n\n",
			len(serviceLines))
		for _, line := range serviceLines {
			fmt.Fprintf(w, "data: %s\n\n", sanitizeSSE(line))
		}
	}
	fmt.Fprint(w, "data: [--- web UI log follows, live ---]\n\n")

	for _, line := range a.logBuffer.Lines() {
		fmt.Fprintf(w, "data: %s\n\n", sanitizeSSE(line))
	}
	flusher.Flush()

	ctx := r.Context()
	for {
		select {
		case <-ctx.Done():
			return
		case line, ok := <-ch:
			if !ok {
				// Dropped for falling behind. Say so: the alternative is a
				// stream that just stops, and a log with a silent hole in it.
				fmt.Fprint(w, "data: [log stream fell behind and was dropped; reconnecting]\n\n")
				flusher.Flush()
				return
			}
			fmt.Fprintf(w, "data: %s\n\n", line)
			flusher.Flush()
		}
	}
}

// browseEntry is a single item returned by the file browser.
type browseEntry struct {
	Name  string `json:"name"`
	IsDir bool   `json:"is_dir"`
	Size  int64  `json:"size"`
}

// browseResponse is the structured response for the file browser.
type browseResponse struct {
	Path   string        `json:"path"`
	Parent string        `json:"parent,omitempty"`
	Items  []browseEntry `json:"items"`
	Roots  []string      `json:"roots,omitempty"`
}

// withinBrowseRoots reports whether path is inside one of the allowed roots.
//
// Component-wise, via filepath.Rel, rather than a string prefix: "/srv/ps2" is
// a prefix of "/srv/ps2-private" but not a parent of it.
//
// And on the REAL path, not the lexical one. filepath.Rel does not follow
// symbolic links, so with a games folder containing `elsewhere -> /etc`, the
// lexical test approved /mnt/games/elsewhere and os.ReadDir then listed /etc --
// one click at a time, all the way down. That is precisely the defect this same
// change fixed in smbv1_server, whose resolver now says "the Edge server and the
// UDPFS path guard already refuse symlink escapes"; for /api/browse it did not.
//
// The returned root is the one the path is under, so a caller can tell "this is
// the root itself" from "this is below it".
func withinBrowseRoots(roots []string, path string) (string, bool) {
	if len(roots) == 0 {
		return "", true // --browse-anywhere
	}
	// EvalSymlinks fails on a path that does not exist; fall back to the
	// lexical name so a missing directory is refused by the containment test
	// rather than by an error that says nothing about why.
	if real, err := filepath.EvalSymlinks(path); err == nil {
		path = real
	}
	for _, root := range roots {
		// The root itself may legitimately be reached through a symlink
		// (/srv/ps2 -> /mnt/disk2/ps2 is ordinary). Resolve it too, or every
		// path under such a root fails containment against an unresolved name.
		if realRoot, err := filepath.EvalSymlinks(root); err == nil {
			root = realRoot
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			continue
		}
		if rel == "." || (!strings.HasPrefix(rel, ".."+string(os.PathSeparator)) && rel != "..") {
			return root, true
		}
	}
	return "", false
}

func (a *API) HandleBrowse(w http.ResponseWriter, r *http.Request) {
	if !requireMethod(w, r, http.MethodGet) {
		return
	}
	if a.noBrowse {
		jsonError(w, http.StatusForbidden, "File browsing is disabled")
		return
	}

	path := r.URL.Query().Get("path")
	if path == "" {
		// No path means "where may I start", which is the roots themselves.
		// Previously this defaulted to "/" and listed the whole filesystem.
		if len(a.browseRoots) == 1 {
			path = a.browseRoots[0]
		} else if len(a.browseRoots) > 1 {
			jsonResponse(w, http.StatusOK, browseResponse{Roots: a.browseRoots})
			return
		} else {
			path = string(os.PathSeparator)
		}
	}

	if !filepath.IsAbs(path) {
		jsonError(w, http.StatusBadRequest, "Invalid path")
		return
	}
	// Cleaned before the containment check, never after: "/srv/ps2/../../etc"
	// only becomes "/etc" once it is normalised, and checking the raw string
	// would approve it.
	path = filepath.Clean(path)

	root, ok := withinBrowseRoots(a.browseRoots, path)
	if !ok {
		jsonError(w, http.StatusForbidden,
			"path is outside the configured server directories")
		return
	}

	entries, err := os.ReadDir(path)
	if err != nil {
		// Not err.Error(): that embeds the requested path, which is attacker
		// controlled and ends up rendered by the dashboard.
		jsonError(w, http.StatusNotFound, "cannot list that directory")
		return
	}

	items := make([]browseEntry, 0, len(entries))
	for _, e := range entries {
		info, err := e.Info()
		if err != nil {
			continue
		}
		items = append(items, browseEntry{
			Name:  e.Name(),
			IsDir: e.IsDir(),
			Size:  info.Size(),
		})
	}

	resp := browseResponse{Path: path, Items: items, Roots: a.browseRoots}

	// Offer a parent only while it stays inside a root, so "Up" cannot walk out
	// of the share one click at a time.
	if parent := filepath.Dir(path); parent != path && path != root {
		if _, ok := withinBrowseRoots(a.browseRoots, parent); ok {
			resp.Parent = parent
		}
	}

	jsonResponse(w, http.StatusOK, resp)
}

func (a *API) HandleInfo(w http.ResponseWriter, r *http.Request) {
	if !requireMethod(w, r, http.MethodGet) {
		return
	}
	jsonResponse(w, http.StatusOK, map[string]string{
		"version":  a.version,
		"platform": runtime.GOOS,
		"arch":     runtime.GOARCH,
	})
}
