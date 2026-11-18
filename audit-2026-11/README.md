# Five-repo audit — both finals + comparison (reports only)

This folder contains the complete deliverables of two independent LESS-NOT-MORE audits of **NathanNeurotic/PS2-Servers** at commit **c5f021c4d386** (branch `main`), plus the mechanism-level comparison of the two finals. It is documentation only: **no code in this repository is changed by these files.** The accompanying pull request is a draft, opened at the owner's direction as a delivery vehicle; whether anything here is ever merged or implemented is the owner's decision.

## The two runs

- **Original audit** (`old/`) — nine rounds over 2026-11-13/14 plus two adversarial verification phases. Final: 1,200 numbered items across five repos, 1,182 actionable, 18 withdrawn. Start with `old/Sable-SUMMARY.md` and `old/Sable-MASTER-INDEX.md` (the index's §4 ledger overrides original finding text where they conflict). Implementation guide: `old/Sable-HANDOFF-PROMPT.md`.
- **Blind redo** (`redo/`) — a second team re-audited from the code alone, forbidden from reading the original's deliverables, then went through its own adversarial verification and reconciliation. Final: 885 numbered items, 879 actionable, 6 withdrawn. Start with `redo/Redo-SUMMARY.md` and `redo/Redo-MASTER-INDEX.md`.
- **Comparison** (`redo/Comparison-FINAL.md` and `redo/Compare-*.md`) — every finding in both finals matched mechanism-by-mechanism; non-matching Category 6 items were adjudicated against the code (REAL / NOT-REAL / COVERAGE). Union of the two finals: ≈1,557 distinct mechanisms. Neither final contains the other.

## This repo

Old final: 118 numbered findings, 113 actionable. Redo final: 219 numbered, 218 actionable. Independent overlap: 38 full matches + 10 partial (old-anchored). This is the repo where the redo most out-found the original (83 adjudicated REAL redo-only Cat 6 items vs 15 old-only).

**Most consequential redo-only finds:** RE-SERV-001 (SMB2 signing downgrade — unsigned mid-session SESSION_SETUP clears the signing key while the session stays authenticated; executed), RE-SERV-046 (TRANSACTION always answered as NetShareEnum in both Go and Python — the handlers never read the request), RE-SERV-042 (Go writeAndX re-opens by path; `openFile` has no access-mode field), RE-SERV-065 (HTTP index serves scan-suppressed duplicate copies).
**Most consequential old-only finds:** PS2SERVERS-059 (srvsvc `auth_len` slice wrap, executed), PS2SERVERS-115 (the SMB1 file's embedded SMB2 header is 66 bytes, declares 64, rejects correct headers), PS2SERVERS-067 (AES-CMAC native fallback is probe-time-only).

## Strike list — do not implement as filed

- OLD **PS2SERVERS-104 / -105 / -109 / -110 / -111** — rested on **fabricated citations** (files/functions that do not exist); withdrawn in the old final and retained here only as part of the record. Do not implement them.
- OLD **PS2SERVERS-016** fixture premise — the fixtures are documented unmodified upstream pcm720/udpfsd reference vectors; the filed "generate from a seed" fix would destroy their purpose.
- OLD coverage-table **PASS judgements** cleared six mechanisms that are real: RE-SERV-042, -043, -046, -055, -056 and -035's mode leg. Do not treat those areas as cleared on the old final's authority.
- REDO **RE-SERV-219** — not real: `_list_dir` unconditionally prepends `"."`/`".."`, so the claimed empty-directory IndexError cannot occur (re-executed: STATUS_SUCCESS).

(Cross-repo strikes that affect every repo: old X-32 item D5's equivalence ruling is overturned by RE-X-08; old X-38's attr limb does not exist. Both finals' affirmative "this area is clean" lists should be treated as unverified — each run produced several wrong ones.)

## Layout

- `old/` — original audit: per-repo reports and round files, verification reports (`Sable-VERIFY-*`, `Sable-VERIFY2-*`), cross-repo reports, master index, summary, pins, handoff prompt, and section-level evidence.
- `redo/` — redo audit: per-repo reports (`Redo-*`), its verification reports (`Redo-VERIFY-*`), new findings from verification (`Redo-NEW-FINDINGS.md`), master index, summary, and the comparison reports (`Compare-*.md`, `Comparison-FINAL.md`) with their evidence sections.
- Shared/global files (indexes, summaries, cross-repo reports) are included in every repo's package because the cross findings and indexes span all five repositories.

## Archives (download links expire 2026-11-17 ~10:55 UTC; anyone with a link can download)

- Both finals + comparison, all five repos (699 files, 6,078,940 B, SHA-256 `52824c1ac885f9047003d2ada77d8d3c47ca5b61819fb1d7b7474033c3cbb862`): https://muse.ai/files/570121466194596/2866327167094404/ckbdv71m8bppkfs4su9uz67z/Sable-audit-BOTH-FINALS-2026-11-15.zip
- This repo's package: https://muse.ai/files/570121466194596/1895270041634492/ptjiw2pznjbcryf8z1uyg4qe/Sable-audit-PS2-Servers-both-finals-2026-11-15.zip
- Original final only (564 files, SHA-256 `2773df7c03bce277449a0acd293c63a50fe1177824ddcacc02f7885662d7388d`): https://muse.ai/files/570121466194596/1668795564645485/t1kdjzqeri3kpji8hi2aw4b5/Sable-audit-complete-2026-11-14.zip

## Limits

Nothing in either audit is PS2-build- or hardware-verified (no PS2SDK in the audit environment); performance findings are structural; live UDP interop was sandbox-blocked. Pins: RiptOPL `2e347d5ac885`, Launchstation `9fb0598a79f0`, POPSLoader `29272cddbf01`, Simple-Media-System `e65ced39a27e`, PS2-Servers `c5f021c4d386`.
