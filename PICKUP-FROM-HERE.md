# PICKUP FROM HERE

Handoff note written 2026-07-29 at the end of a long session. Read this before
starting work; it will save you rediscovering things the hard way.

---

## Where everything is

| | |
|---|---|
| **Local repo** | `C:\Users\natha\Github\PS2-Servers` |
| Main clone's checked-out branch | `feat/ps2-servers-edge` (stale; don't assume it's current) |
| **Worktree used this session** | `C:\Users\natha\Github\PS2-Servers\.claude\worktrees\sleepy-euclid-f7a6f0` |
| GitHub | `NathanNeurotic/PS2-Servers` |
| Memory | `C:\Users\natha\.claude\projects\C--Users-natha-Github-PS2-Servers\memory\` |

Other worktrees exist (`git worktree list`) — several are stale. **`main` is the
source of truth**, not whatever a worktree has checked out.

## State right now

> Updated 2026-08-09 for the v0.5.0 cut. The facts in *this* section are current;
> the "Older state" subsection below it, and every section after it, are from the
> 2026-07-29 session and are kept for their reasoning, not their numbers.

- **Version is `0.5.1`** in `launcher/release_metadata.py` — the single source;
  `tools/version_source.py` reads it and `release.yml` refuses a tag that
  disagrees. Both v0.5.0 and v0.5.1 were cut on 2026-08-09.
- **v0.5.0 is the release that introduces PS2 Servers Edge.** Everything from
  #119 onward landed after the v0.4.9 tag: 43 PRs, ~30k lines, Edge in Go across
  12 targets serving UDPFS/UDPBD/SMB plus a web dashboard, the SMB2/SMB3 server,
  the router status protocol, and systemd + OpenWrt packaging.
- **v0.5.1 carries the completed audit work**: the UDPFS session cap
  (`--max-sessions`), the OpenWrt web-UI privilege model (`webui.run_as_root`,
  default off), real per-service logs in the dashboard, and `.gitattributes`.
- Full suite green: **515 Python tests**, Go race- and vet-clean, gofmt gated,
  and CI now runs the *whole* suite — it ran 1 of 26 modules until v0.5.0.
- Edge mipsle binary: **6.19 MiB in CI** (was 3.31 MiB before the web GUI; a
  local Go 1.26 build is ~1 MiB larger). CI fails above 9 MiB per target — read
  the comment in `edge-build.yml` before raising it.
- **`AUDIT-2026-08-09.md` is fully closed.** Every finding has a status; nothing
  is outstanding in it. Read it before re-deriving anything about the web UI,
  the packaging, or the transfer limits.

### How a release is cut here

1. Bump `PRODUCT_VERSION` in `launcher/release_metadata.py`, alone, in a commit
   named `Release vX.Y.Z`. Make it the **last** commit on the branch.
2. Open a PR and let CI go green.
3. **Rebase-merge** (not squash) so that commit survives on `main` and the tag
   shows only the bump, as every past release tag does.
4. `git tag -a vX.Y.Z <bump-commit> -m "PS2 Servers vX.Y.Z"` and push the tag.
   That is the entire publish step; `release.yml` and `edge.yml` do the rest.

`.github/release-notes-common.md` is appended to every published release. If a
capability changes, it changes in the same PR — it has gone stale twice.

### Older state (2026-07-29, stale)

- **`main` = `4010459`**, zero open PRs, working tree clean.
- Latest rolling build: `main-138-4010459`.
- Full suite green: **316 Python tests**, **7 Go packages under `-race`**.
- Edge mipsle binary: **3.31 MB**.

## What shipped this session (6 PRs, all merged)

| PR | What |
|---|---|
| [#143](https://github.com/NathanNeurotic/PS2-Servers/pull/143) | Edge sized for 32 MB routers |
| [#144](https://github.com/NathanNeurotic/PS2-Servers/pull/144) | Router status handshake + launcher consumer |
| [#145](https://github.com/NathanNeurotic/PS2-Servers/pull/145) | systemd units for the Python servers |
| [#146](https://github.com/NathanNeurotic/PS2-Servers/pull/146) | **Edge SMBv1 server** |
| [#147](https://github.com/NathanNeurotic/PS2-Servers/pull/147) | udpfs shutdown data race |
| [#148](https://github.com/NathanNeurotic/PS2-Servers/pull/148) | Edge packaging can start smb/udpbd; smb+udpbd answer status |

Headline: **Edge now serves all three protocols** (`udpfs`, `udpbd`, `smb`) as
subcommands on one binary, and its OpenWrt/systemd packaging can start each.

## THE ONE THING THAT MATTERS NEXT

**None of it has run on a real console.** Everything since `a0f432c` is
measurement and arithmetic on a dev machine. The release has been blocked on
hardware testing, not code, for over a week.

Two testers are live — the first real router testers this project has had:

- **FatBaldDad** — building [PS2-EtherDrive](https://github.com/FatBaldDad/PS2-EtherDrive)
  hardware around the **A5-V11, HLK-7628N and MT7621A**. All three take the
  **same `linux-mipsle` build**. Point him at **listing a folder of CSOs**
  specifically — that was the OOM path #143 fixed — and at SMB, which has never
  touched a console.
- **V3ditata / venao** — TP-Link Archer AX72, stock firmware, so he *cannot*
  run Edge. His SMB got slow for the PS2 only. Diagnosis: **OPL's SMB reads
  8 KB at a time, serially**, so throughput is round-trip latency, not
  bandwidth — which is why his laptop is unaffected. Suspect the router's
  media/DLNA indexer or fragmentation after he extracted a 7z onto the share.
  His fix today is running PS2-Servers on his notebook, ideally **UDPFS not
  SMB** (UDPFS pipelines 8 in flight; OPL's SMB does one at a time).

**When reports come back clean, v0.5.0 is a version bump and a tag. No code.**

## Suggested next work, in order

1. **Wait for hardware results.** Everything below is speculative until then,
   and could be wasted if the reports are bad.
2. `feat/smb2-smb3` — parked, **local only, never pushed**. Two commits: a path
   guard and an NTLMv2 auth core, nothing wired together. Deprioritised
   deliberately: Edge SMBv1 covers OPL/POPSTARTER, which was the actual demand.
   SMB2/3 serves modern Windows clients browsing the share — real, but nobody
   has asked.
3. `internal/udpbd`'s `TestReadsMatchTheImageAcrossBlockRegimes` fails under
   `-count=3` **on main**, passes isolated. CI runs `-count=1` so it never
   sees it. Symptom is a UDP timeout, not a race — likely a fixed port or a
   timing assumption. Noted in #147, not fixed.

## Local branches — all merged content, safe to delete

`feat/router-status`, `fix/edge-memory-32mb`, `feat/systemd-python-servers`,
`feat/edge-smb`, `feat/edge-service-modes` were **squash-merged**, so
`git log main..<branch>` still shows commits even though the content is in.
Diff them against `origin/main` to confirm before deleting.

**`feat/smb2-smb3` is the exception** — genuinely unmerged and unpushed.

---

## Hard-won lessons — please read

**A green CI badge can mean nothing was reviewed.** CodeRabbit was
*rate-limited* on #146 and #147 and the check still reported "pass". I nearly
merged 900 lines of protocol code on the strength of it. An independent review
then found a blocker. **Check whether the bot actually ran.**

**Port from `smbv1_server/smbserver_opl.py`, never from the SMB spec.** That
file is what consoles have actually been tested against. Its comments record
real hardware findings. Examples that are load-bearing, not style:
- NEGOTIATE must return **exactly 17 words** or OPL retries forever.
- READ_ANDX `DataOffset` must be **59**; OPL reads from there unconditionally.
- FIND_NEXT2's params are **8 bytes with no leading SID**; FIND_FIRST2's are 10.
- The header NTSTATUS is **not** a LE uint32 at offset 5 — it's the legacy
  ErrorClass/ErrorCode split, and it is *lossy*. A test pins the lossy
  behaviour, because the "obvious" fix changes the wire.

**Python slicing clamps; Go slicing panics.** The reference leans on that.
Every ported offset goes through a bounds-checked helper.

**`int` is 32 bits on the targets that matter** (mipsle/mips/arm/386). A
`int(lo)|int(hi)<<16` wrapped negative and produced a silent short read with
`STATUS_SUCCESS`. Reproduced with `GOARCH=386 go test`. Use that.

**`MaxMessage` bounds a message, not what a handler remembers.** Three
per-connection maps were unbounded; 2000 requests grew the heap 80 MiB.

**Check that tests catch what they claim.** Three of mine matched too narrowly
and passed against the very bug they existed for. Mutate the code and watch the
test fail before trusting it.

**Read the reference before designing.** Twice, a design question I raised had
its answer already in the code, and the evidence pointed at the *simpler*
option — reads are already clamped to 64 KiB, and the port question was about
Linux privilege, not Windows.

## Conventions

- Squash-merge PRs; commit messages explain **why**, at length, in prose.
- `docs/ROUTER-STATUS.md` is the status protocol contract — outside projects
  are invited to implement it.
- Edge SMB defaults to **port 1111, not 445** (445 needs root on Linux too, and
  both deployments run unprivileged). A test fails if it moves below 1024.
- Read `MEMORY.md` in the memory directory first — it indexes everything above
  in more detail.
