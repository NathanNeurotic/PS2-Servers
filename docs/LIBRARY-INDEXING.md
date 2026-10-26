# Library metadata indexing

The Desktop Library tab stores a local metadata catalogue. Scanning does not
copy, rename or delete games, artwork, VMCs or saves, configure servers, or
advertise new paths to a console. Existing game-serving paths do not read this
SQLite database.

## Choose the scope

By default, **Scan CD/DVD** indexes immediate images inside `CD/` and `DVD/`.
Select **Include root and subfolders** to include root images and nested trees.
The maximum depth defaults to four directory levels below the selected root;
the UI accepts 0 through 8. Depth zero includes only root files in recursive mode.
For example, `Games/RPG/Subfolder/Game.iso` needs depth three.

Each run inspects at most 10,000 directory entries, including directories and
unsupported files. This bounds work on wide trees as well as deep ones. The
result distinguishes completion, cancellation, and entry-limit termination.
The Python `scan_detailed()` API accepts an explicit entry limit of 1–100,000.
The existing `scan(root)` API retains an integer count and the CD/DVD scope,
using the same default work limit.

Depth-limited folders, symlinks/reparse points, unsupported entries, empty or
otherwise invalid images, and filesystem errors have separate counts. Up to
20 filesystem error details are retained in the API result; the UI status shows
the first issue. An unreadable folder is not reported as a successful empty scan.
ISO/CSO/ZSO/CHD suffixes are indexed case-insensitively. This scan reads file
metadata only: a nonempty malformed image is not validated by being indexed.

The selected root resolves to its canonical directory. Descendant symlinks,
Windows junctions and other reparse points are skipped, including links that
stay inside the root. Containment is rechecked before indexing. Do not change
mounts or rearrange the tree while scanning; these checks are not a filesystem
snapshot or an OS sandbox against concurrent adversarial filesystem changes.

## Cancellation and rescanning

The scan runs in the existing background library worker. **Cancel scan** asks
it to stop between entries; it cannot interrupt an operating-system call already
blocked on a slow or unavailable network filesystem. Progress updates are
coalesced into one pending UI update instead of accumulating every file event.

Already indexed rows remain after cancellation. Rescan safely to refresh the
same paths: custom title, disc ID and notes are retained. There is no durable
cursor; a rescan starts traversal again. If a run reaches its entry limit,
choose a smaller root or raise the limit through the API. Merely repeating a
limited scan does not guarantee discovery of entries beyond that limit.

Missing files are not removed from the catalogue. Full canonical file paths are
the existing database identity, so `DVD/One/Game.iso` and `DVD/Two/Game.iso`
remain separate rows even with the same basename or manually entered disc ID.
Moving a file creates a different identity; this increment does not implement
content hashes or automatic moved-file reconciliation.

## Media and client compatibility

Images beneath a top-level `CD/` or `DVD/` folder inherit that layout hint.
Images elsewhere are marked **UNKNOWN**. A directory name is not verified disc
identity, and arbitrary-root images are not guessed as DVD from size or filename.
A rescan with unknown media does not erase a previously known CD/DVD value.
Actual `SYSTEM.CNF` identity extraction is separate follow-up work.

SQLite keeps its existing schema and user edits; the independent achievements
catalogue is not migrated or merged. JSON exports can round-trip UNKNOWN media
in this version. Older builds may reject those new rows on JSON import; retain
your SQLite backup and previous exports when moving between builds.

**Indexed does not mean launchable.** Existing SMB/UDPFS loaders may require the
CD/DVD layout. This change does not add recursive console listings, path-aware
HTTP routes, larger console catalogue buffers, or compressed-format support to
a loader. Verify the exact client and server path contract before changing a
working game layout. No console-hardware compatibility claim follows from an
indexing test.

This is the recursive-discovery increment of
[companion issue #249](https://github.com/NathanNeurotic/PS2-Servers/issues/249).
Shared RA/general descriptors, authoritative disc IDs, artwork providers,
server-profile export and path-aware HTTP remain separate work.
