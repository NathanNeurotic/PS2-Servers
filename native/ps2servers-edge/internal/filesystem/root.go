package filesystem

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge/internal/session"
)

var ErrEscape = errors.New("path escapes configured root")

type Root struct {
	path      string
	canonical string
}

func Open(path string) (*Root, error) {
	if path == "" {
		return nil, errors.New("root is required")
	}
	abs, err := filepath.Abs(path)
	if err != nil {
		return nil, err
	}
	st, err := os.Stat(abs)
	if err != nil {
		return nil, err
	}
	if !st.IsDir() {
		return nil, fmt.Errorf("root is not a directory: %s", path)
	}
	real, err := filepath.EvalSymlinks(abs)
	if err != nil {
		return nil, err
	}
	return &Root{path: abs, canonical: filepath.Clean(real)}, nil
}
func (r *Root) Path() string { return r.path }
func hasDotDot(p string) bool {
	for _, part := range strings.FieldsFunc(p, func(x rune) bool { return x == '/' || x == '\\' }) {
		if part == ".." {
			return true
		}
	}
	return false
}
func (r *Root) Resolve(client string, allowMissing bool) (string, error) {
	if client == "" || client == "." || client == "/" || client == "\\" {
		return r.canonical, nil
	}
	normalized := strings.ReplaceAll(client, "\\", "/")
	windowsDrive := len(normalized) >= 2 && ((normalized[0] >= 'A' && normalized[0] <= 'Z') || (normalized[0] >= 'a' && normalized[0] <= 'z')) && normalized[1] == ':'
	if filepath.IsAbs(client) || strings.HasPrefix(normalized, "/") || windowsDrive || filepath.VolumeName(client) != "" || hasDotDot(client) {
		return "", ErrEscape
	}
	clean := filepath.Clean(filepath.FromSlash(strings.ReplaceAll(client, "\\", "/")))
	if clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
		return "", ErrEscape
	}
	candidate := filepath.Join(r.canonical, clean)
	parent := candidate
	if allowMissing {
		parent = filepath.Dir(candidate)
	}
	real, err := filepath.EvalSymlinks(parent)
	if err != nil {
		return "", err
	}
	rel, err := filepath.Rel(r.canonical, real)
	if err != nil {
		return "", err
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", ErrEscape
	}
	if allowMissing {
		return filepath.Join(real, filepath.Base(candidate)), nil
	}
	resolved, err := filepath.EvalSymlinks(candidate)
	if err != nil {
		return "", err
	}
	rel, err = filepath.Rel(r.canonical, resolved)
	if err != nil {
		return "", err
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", ErrEscape
	}
	return resolved, nil
}
func Mode(info fs.FileInfo) uint32 {
	if info.IsDir() {
		return 0x1000
	}
	if info.Mode().IsRegular() {
		return 0x2000
	}
	return 0
}
func (r *Root) List(client string) ([]session.DirEntry, error) {
	p, err := r.Resolve(client, false)
	if err != nil {
		return nil, err
	}
	entries, err := os.ReadDir(p)
	if err != nil {
		return nil, err
	}
	out := make([]session.DirEntry, 0, len(entries))
	for _, e := range entries {
		child := filepath.ToSlash(filepath.Join(client, e.Name()))
		resolved, err := r.Resolve(child, false)
		if err != nil {
			// Do not reveal or inspect symlinks that leave the configured root.
			continue
		}
		info, err := os.Stat(resolved)
		if err != nil {
			continue
		}
		out = append(out, session.DirEntry{Name: e.Name(), SourcePath: resolved, Mode: Mode(info), Size: uint64(info.Size()), ModTime: info.ModTime()})
	}
	return out, nil
}
