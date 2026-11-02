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

// ErrThroughLink refuses a deletion whose path passes through a symlinked
// folder; see Root.RealFolders.
var ErrThroughLink = errors.New("path passes through a symlinked folder")

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

// Resolve maps a client path to the host path it reaches, which must lie
// inside the root.
//
// allowMissing is for a caller that may create the path: a last component
// that does not exist yet is joined to its resolved parent. One that does
// exist is resolved like any other path, so a symlink there is followed and
// its target must be inside the root too. Opening or creating through a link
// reaches its target; returning the link unchecked let an O_CREAT open or an
// SMB create read, truncate and write a file outside the root through a link
// inside it, and create one through a dangling link.
func (r *Root) Resolve(client string, allowMissing bool) (string, error) {
	if client == "" || client == "." || client == "/" || client == "\\" {
		return r.canonical, nil
	}
	candidate, err := r.candidate(client)
	if err != nil {
		return "", err
	}
	if allowMissing {
		entry, err := r.entry(candidate)
		if err != nil {
			return "", err
		}
		if _, err := os.Lstat(entry); err != nil {
			if errors.Is(err, fs.ErrNotExist) {
				return entry, nil
			}
			return "", err
		}
		// It exists, if only as a link that may dangle: resolve the whole
		// path below. A dangling link fails there rather than being created
		// through.
		candidate = entry
	}
	resolved, err := filepath.EvalSymlinks(candidate)
	if err != nil {
		return "", err
	}
	rel, err := filepath.Rel(r.canonical, resolved)
	if err != nil {
		return "", err
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", ErrEscape
	}
	return resolved, nil
}

// Entry resolves client to the directory entry it names, for an operation on
// the entry itself rather than on what it points at: MKDIR, REMOVE and RMDIR,
// which like mkdir(2), unlink(2) and rmdir(2) never follow a symlink in the
// last component. The parent is resolved and must lie inside the root, as in
// Resolve; the last component is joined unresolved, so a symlink there is
// the entry acted on even when it points out of the root, and what it points
// at is never reached.
//
// isRoot reports that client names the root itself: "", ".", "/" or "./".
func (r *Root) Entry(client string) (path string, isRoot bool, err error) {
	if namesRoot(client) {
		return r.canonical, true, nil
	}
	candidate, err := r.candidate(client)
	if err != nil {
		return "", false, err
	}
	entry, err := r.entry(candidate)
	if err != nil {
		return "", false, err
	}
	// The OS must act on exactly the entry checked here. Windows strips
	// trailing dots and spaces from a last component, so "..." reaches its
	// parent -- for a top-level name the root itself, which an RMDIR would
	// then delete. Abs applies the same rules, so a name it changes is one
	// the OS would reinterpret.
	if abs, err := filepath.Abs(entry); err != nil || abs != entry {
		return "", false, ErrEscape
	}
	return entry, entry == r.canonical, nil
}

// RealFolders reports ErrThroughLink when a folder that client passes through
// on the way to its last component is a symlink or a Windows junction rather
// than a real folder. Deleting through one deletes what is in the folder it
// points at, and List shows a link to a folder as a folder, so a console
// deleting a folder recursively would go on to empty one it never named.
// REMOVE and RMDIR call it after Entry has accepted client.
func (r *Root) RealFolders(client string) error {
	if namesRoot(client) {
		return nil
	}
	candidate, err := r.candidate(client)
	if err != nil {
		return err
	}
	rel, err := filepath.Rel(r.canonical, filepath.Dir(candidate))
	if err != nil {
		return err
	}
	if rel == "." {
		return nil
	}
	dir := r.canonical
	for _, part := range strings.Split(rel, string(filepath.Separator)) {
		dir = filepath.Join(dir, part)
		info, err := os.Lstat(dir)
		if err != nil {
			return err
		}
		switch {
		case info.IsDir():
			// A real folder. On Windows Lstat leaves the directory bit off
			// only for a symlink or a junction, so a reparse-point folder
			// that is neither, such as a cloud placeholder, passes too.
		case info.Mode()&(fs.ModeSymlink|fs.ModeIrregular) != 0:
			// A symlink, or a junction, which Go reports as a symlink before
			// 1.23 and as irregular since.
			return ErrThroughLink
		default:
			// A file used as a folder: the operation answers as the host
			// does, ENOTDIR (ENOENT on Windows).
			return nil
		}
	}
	return nil
}

// namesRoot reports that client names the root itself: nothing but
// separators and "." components.
func namesRoot(client string) bool {
	for _, part := range strings.FieldsFunc(client, func(c rune) bool { return c == '/' || c == '\\' }) {
		if part != "." {
			return false
		}
	}
	return true
}

// candidate refuses any client path that could name a place outside the root
// -- "..", absolute and drive-qualified paths -- and joins the rest to it.
func (r *Root) candidate(client string) (string, error) {
	normalized := strings.ReplaceAll(client, "\\", "/")
	windowsDrive := len(normalized) >= 2 && ((normalized[0] >= 'A' && normalized[0] <= 'Z') || (normalized[0] >= 'a' && normalized[0] <= 'z')) && normalized[1] == ':'
	if filepath.IsAbs(client) || strings.HasPrefix(normalized, "/") || windowsDrive || filepath.VolumeName(client) != "" || hasDotDot(client) {
		return "", ErrEscape
	}
	clean := filepath.Clean(filepath.FromSlash(normalized))
	if clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
		return "", ErrEscape
	}
	return filepath.Join(r.canonical, clean), nil
}

// entry resolves the parent of candidate, which must exist inside the root,
// and joins the last component to it unresolved.
func (r *Root) entry(candidate string) (string, error) {
	real, err := filepath.EvalSymlinks(filepath.Dir(candidate))
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
	return filepath.Join(real, filepath.Base(candidate)), nil
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
