package webui

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

// API groups all HTTP handlers for the web UI.
type API struct {
	configBackend ConfigBackend
	manager       ServiceManager
	logBuffer     *LogBuffer
	noBrowse      bool
	version       string
}

func jsonResponse(w http.ResponseWriter, status int, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(data)
}

func jsonError(w http.ResponseWriter, status int, msg string) {
	jsonResponse(w, status, map[string]string{"error": msg})
}

func (a *API) HandleStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		jsonError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}
	status, err := a.manager.Status()
	if err != nil {
		jsonError(w, http.StatusInternalServerError, err.Error())
		return
	}

	// Build the combined dashboard response that the frontend expects.
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

	if r.Method == http.MethodPost {
		var cfg EdgeConfig
		if err := json.NewDecoder(r.Body).Decode(&cfg); err != nil {
			jsonError(w, http.StatusBadRequest, "Invalid JSON")
			return
		}
		if err := a.configBackend.Save(&cfg); err != nil {
			jsonError(w, http.StatusInternalServerError, err.Error())
			return
		}
		jsonResponse(w, http.StatusOK, map[string]bool{"ok": true})
		return
	}

	jsonError(w, http.StatusMethodNotAllowed, "Method not allowed")
}

func (a *API) HandleConfigDefaults(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		jsonError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}
	jsonResponse(w, http.StatusOK, DefaultConfig())
}

func (a *API) HandleConfigReset(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		jsonError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}
	if err := a.configBackend.Save(DefaultConfig()); err != nil {
		jsonError(w, http.StatusInternalServerError, err.Error())
		return
	}
	jsonResponse(w, http.StatusOK, map[string]bool{"ok": true})
}

func (a *API) HandleRestart(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		jsonError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}

	var req struct {
		Service string `json:"service"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		jsonError(w, http.StatusBadRequest, "Invalid JSON")
		return
	}

	if err := a.manager.Restart(req.Service); err != nil {
		jsonError(w, http.StatusInternalServerError, err.Error())
		return
	}

	jsonResponse(w, http.StatusOK, map[string]bool{"ok": true})
}

func (a *API) HandleLogs(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "Streaming unsupported", http.StatusInternalServerError)
		return
	}

	lines := a.logBuffer.Lines()
	for _, line := range lines {
		fmt.Fprintf(w, "data: %s\n\n", line)
	}
	flusher.Flush()

	ch, unsub := a.logBuffer.Subscribe()
	defer unsub()

	ctx := r.Context()
	for {
		select {
		case <-ctx.Done():
			return
		case line, ok := <-ch:
			if !ok {
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
}

func (a *API) HandleBrowse(w http.ResponseWriter, r *http.Request) {
	if a.noBrowse {
		jsonError(w, http.StatusForbidden, "File browsing is disabled")
		return
	}

	path := r.URL.Query().Get("path")
	if path == "" {
		path = "/"
	}

	if !filepath.IsAbs(path) || strings.Contains(path, "..") {
		jsonError(w, http.StatusBadRequest, "Invalid path")
		return
	}

	// Resolve to a canonical path.
	path = filepath.Clean(path)

	entries, err := os.ReadDir(path)
	if err != nil {
		jsonError(w, http.StatusInternalServerError, err.Error())
		return
	}

	var items []browseEntry
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

	resp := browseResponse{
		Path:  path,
		Items: items,
	}

	// Compute parent directory, unless we are at a filesystem root.
	parent := filepath.Dir(path)
	if parent != path {
		resp.Parent = parent
	}

	jsonResponse(w, http.StatusOK, resp)
}

func (a *API) HandleInfo(w http.ResponseWriter, r *http.Request) {
	jsonResponse(w, http.StatusOK, map[string]string{
		"version":  a.version,
		"platform": runtime.GOOS,
		"arch":     runtime.GOARCH,
	})
}
