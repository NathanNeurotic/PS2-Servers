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
The separate **Inspect disc IDs** action below can inspect plain ISO boot identity;
it does not infer CD versus DVD from that identity.

SQLite keeps the existing games schema and user edits; the independent achievements
catalogue is not migrated or merged. JSON exports can round-trip UNKNOWN media
in this version. Older builds may reject those new rows on JSON import; retain
your SQLite backup and previous exports when moving between builds.

**Indexed does not mean launchable.** Existing SMB/UDPFS loaders may require the
CD/DVD layout. This change does not add recursive console listings, path-aware
HTTP routes, larger console catalogue buffers, or compressed-format support to
a loader. Verify the exact client and server path contract before changing a
working game layout. No console-hardware compatibility claim follows from an
indexing test.

## Inspect disc IDs

After scanning, choose **Inspect disc IDs** to inspect the entries already indexed
under the selected games root. This is a separate, opt-in background operation:
ordinary scans still read file metadata only. It needs no server, network access,
RetroAchievements account or native achievement engine. It opens game images
read-only and preserves manual IDs, custom titles, notes, media hints and server
configuration.

The inspector supports **cooked plain ISO images with 2048-byte ISO9660 logical
blocks**. It finds the primary volume descriptor, root `SYSTEM.CNF`, exactly one
`BOOT2 = cdrom0:\\...;1` setting, and the referenced executable. An **Inspected ID**
requires a canonical `AAAA_123.45` boot filename and an ELF signature at its
validated extent. Filename prefixes are comparison hints only. An ELF signature
is not a full executable validation, content hash, retail-disc authenticity check,
or evidence that the console can launch/read the game.

The table keeps **Manual ID**, **Inspected ID**, and **ID status** separate.
Double-click a row or press Enter for the boot path and explanation. Statuses distinguish:

- **verified**: boot serial and referenced ELF signature were inspected;
- **conflict**: a manual ID or serial prefix in the filename disagrees with that
  boot serial; both values remain available for the user to review;
- **unknown**: no supported `SYSTEM.CNF`/`BOOT2` identity, or a homebrew ELF with
  no canonical serial; PS1 `BOOT` is not interpreted as PS2 `BOOT2`;
- **invalid**: malformed/truncated metadata, an ambiguous ISO9660 path, or a
  missing executable/ELF signature;
- **unsupported**: a compressed image or a layout/resource limit outside the
  inspector's contract;
- **unavailable / changed**: filesystem access failed or the file changed while
  being inspected; another inspection retries it;
- **not inspected**: no current inspector result, including after a rescan
  invalidates a changed image's cache entry.

The artwork import prompt can suggest an inspected ID when no manual ID is set
and the status is verified. Conflicting automatic IDs are not suggested. The
prompt still asks for the exact identity and preserves existing artwork.
Images with the same basename or serial at different paths remain distinct rows.

Each UI run considers at most **1,000 indexed entries**, including cached and
unsupported entries, and reports when that limit is reached. The Python
`inspect_identities(root, max_entries=...)` API accepts 1 through 10,000. Select a
smaller root to inspect a subset of a larger catalogue, or raise that limit via
the API. Repeating a limited run does not advance a durable cursor. **Cancel scan**
also cancels inspection between sparse reads/files; completed results remain for
the next invocation. A blocked filesystem call cannot be interrupted by this flag.

Each ISO inspection has these independent limits: image size at most 16 GiB,
32 volume descriptors, 1 MiB per directory, 8,192 directory records in total,
16 KiB `SYSTEM.CNF`, eight boot-path components, and 4 MiB of logical reads.
Both byte-order copies of sizes/offsets must agree, and extents must lie inside
the image and declared volume. Extended attributes, interleaving, multi-extent
boot metadata, raw CD sector images, non-2048-byte logical blocks and alternate
file versions are outside the supported contract. The ISO9660 field layout
follows [ECMA-119](https://ecma-international.org/publications-and-standards/standards/ecma-119/).

CSO/ZSO/CHD remain available to existing indexing, serving and achievement hashing.
This action reports them as **unsupported** without invoking a decoder. Inspection
of malformed compressed images needs a bounded decompression contract before it
can safely join the batch inspector; existing decoder availability alone does
not establish that contract.

Results are cached in a separate `disc_identity` table in `catalog.sqlite3`.
The existing `games` table/schema and the achievements database remain unchanged.
The cache uses the inspector version and a filesystem fingerprint (device/inode,
size, modification time, plus change time on POSIX). The pathname and open handle
are checked for replacement/mutation around inspection. This is not a content
checksum and cannot detect a modification that preserves all observed metadata.
Displayed results describe the last observation; run inspection/rescan again
after changing images. The table view does not open every image to refresh it.

Rescans invalidate changed fingerprints, and unavailable/changed results are
retried. SQLite backups include the cache and can still be opened by older builds,
which ignore the extra table. JSON exports include the observed result for review,
but JSON import deliberately ignores inspected-ID claims: inspect the local image
again to establish its identity. A forged JSON field cannot mark a file verified.

This implements recursive discovery and the plain-ISO identity increment of
[companion issue #249](https://github.com/NathanNeurotic/PS2-Servers/issues/249).
Shared RA/general descriptors, bounded compressed identity inspection, artwork
providers, server-profile export and path-aware HTTP remain separate work.
