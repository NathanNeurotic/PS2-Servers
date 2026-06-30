# Antivirus notice — read this if your security software flags PS2Servers.exe

**Short version:** `PS2Servers.exe` is an unsigned, open-source PlayStation 2
homebrew tool that runs small local-network servers. Some antivirus engines flag
the single-file build with *generic, machine-learning* warnings. This is a
**false positive** caused by how the file is packaged — not by anything the
program does. If you'd rather not see the warning at all, use the **folder
build** (see below); it is the same app and comes up clean.

## What you might see

On VirusTotal or in your antivirus, the single-file `PS2Servers.exe` may be
flagged by several engines with names like:

- `Trojan:Win32/Wacatac.B!ml` — Microsoft; the `!ml` means "machine-learning guess"
- `Gen:Variant.Mikey` / `Gen:Variant.Midie` — BitDefender's generic cluster names (reused by several other products)
- `Win64:MalwareX-gen`, `malicious_confidence_60%`, `Malicious (high confidence)`

**None of these name a real, specific malware family** — they are all "generic",
"gen", "heuristic", "ml", or "confidence-score" labels. And several of the hits
are the *same* scanning engine sold under different brand names, so the count
looks larger than the number of independent opinions behind it.

## Why it gets flagged

It is flagged for what it *looks like*, not what it *does*:

1. **It is unsigned.** There is no paid code-signing certificate, so Windows
   SmartScreen and AV clouds have no reputation for it and treat it as "unknown".
2. **The single-file build self-extracts.** It is built with Nuitka; the one-file
   `.exe` unpacks itself to a temporary folder and launches the real program.
   That "extract-then-run" shape is a generic packer heuristic that lots of
   legitimate software also trips.
3. **It opens local network ports** (the whole purpose — serving files to your
   PS2 over your LAN) and can ask Windows Firewall to allow them.

All three are normal for this kind of utility. None of them is malware.

## Why it's a false positive

- It is **open source** — every line is public:
  https://github.com/NathanNeurotic/PS2-Servers
- The release is **built by GitHub Actions from that public source**, with a
  build attestation you can verify.
- You can **rebuild this exact program yourself** from the source and read it.
- It does **not** contain or perform any of: data/credential theft,
  persistence/autostart, adware, browser modification, crypto-mining, or remote
  control. The only network activity is the LAN server(s) *you* choose to start.

## If you'd rather be cautious — use the folder build

The release also includes a **folder build** named
`PS2Servers-windows-x64-folder.zip`. It is the **same application**, just laid out
as an ordinary folder of files instead of one self-extracting `.exe`. Because it
has no self-extraction step, it does **not** trip the heuristics above and comes
up **clean** on antivirus.

1. Download `PS2Servers-windows-x64-folder.zip` from the release page.
2. Unzip it and run `PS2Servers.exe` from inside the folder.

(You can also skip the prebuilt binaries entirely and run from the Python source.)

## How to verify this download

- Compare the file hash against `SHA256SUMS.txt` on the release page.
- Verify build provenance:
  `gh attestation verify PS2Servers-windows-x64.zip -R NathanNeurotic/PS2-Servers`
- Or rebuild from source:
  `python -m pip install -r requirements-build.txt && python build/build.py`

## What it would take to make the warnings go away

The one thing that reliably stops these heuristic flags on the single-file `.exe`
is an **Authenticode code-signing certificate** from a certificate authority
(OV or EV). That is a recurring paid cost — and EV also needs a hardware token —
which is out of scope for a free hobby project. A standard certificate still has
to *earn* SmartScreen reputation over time. Until then, the **folder build**
avoids the problem today, and reporting the false positive to your vendor helps
clear it for everyone.

## Reporting the false positive (optional, but it helps)

Submit the file (with its SHA-256 from `SHA256SUMS.txt`) to your vendor's
false-positive form:

- Microsoft Defender: https://www.microsoft.com/en-us/wdsi/filesubmission
- BitDefender: https://www.bitdefender.com/consumer/support/answer/29358/
- Avast / AVG: https://www.avast.com/false-positive-file-form.php
- Other vendors: https://github.com/NathanNeurotic/PS2-Servers/blob/main/docs/antivirus-transparency.md

---

PS2 Servers is provided "as is", without warranty, under the Academic Free
License 3.0. See the repository's `LICENSE` and `NOTICE.md`.
