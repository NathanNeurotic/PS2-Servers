# HTTP mode

HTTP mode serves a games folder over plain HTTP for
[Docmine17's Open-PS2-Loader-HTTP](https://github.com/Docmine17/Open-PS2-Loader-HTTP),
an OPL fork that streams ISOs with HTTP Range requests instead of SMB. There are no
shares, no logins, and no SMB dialects to negotiate.

> **Status: experimental.** The OPL fork is young and prototype-stage, and nothing
> here has been validated against a real console yet. The pinned client's range-read
> functions and CSV parser are tested on a PC; PS2 networking, timing and gameplay
> still need hardware testing. If you test it on hardware, please report back.

## Setting it up

1. Start the **HTTP** card and point it at your games folder — the one holding
   `DVD/` and `CD/`.
2. On the console, go to **Network settings** and set:

   | Field | Value |
   |---|---|
   | Protocol | `HTTP` |
   | Address type | IP |
   | PC IP address | the address the card shows |
   | Port | **the same number as the card, e.g. 1100** |
   | Share | anything, or blank |

3. Save and reload the game list.

**Set the Port explicitly.** If OPL's Port is left at `0`, its game-list code falls
back to 8080 while its in-game driver falls back to 1100 — so the list loads from one
port and the games stream from another, and the symptom is a game list that appears
and then a black screen on launch. This is a quirk of the client
(`gPCPort ? gPCPort : 8080` vs `gPCPort ? gPCPort : 1100`), not of this server.

The Share field is only used to build the game-list path, and this server answers on
any share name, so it does not matter what you put there.

## What it serves

The server builds a flat index across the games folder root, `DVD/` and `CD/`, then
serves two things:

- **`games.csv`** — generated for you, at both `/games.csv` and
  `/<anything>/games.csv`. You do not write or maintain this file.
- **the games themselves**, by bare filename at the web root.

That flattening is required rather than a convenience: the in-game driver hardcodes
its base path to `/`, so it asks for `GET /SLUS_201.74.Rumble Racing.iso` no matter
which subfolder the file actually lives in.

Only files in the index are reachable. There is no directory listing, and a URL is
never turned into a filesystem path, so nothing outside the games folder is exposed.

### Naming your games

Use OPL's usual convention so the console gets the right startup id:

```
DVD/SLUS_201.74.Rumble Racing.iso
CD/SCUS_971.24.Some Game.iso
```

The part before the title (`SLUS_201.74`) becomes the STARTUP id in `games.csv`. Art,
per-game settings and cheats are all keyed off that id, so a file that does not follow
the convention still appears in the list but gets a guessed id — and the server names
those files in its log at startup so you can see what to rename.

Media type comes from the folder (`CD/` → CD, `DVD/` → DVD), falling back to file size
when a game sits loose in the root.

### Compressed images

| Format | Handling |
|---|---|
| `.iso` | streamed as-is |
| `.zso`, `.ziso` | streamed as-is — the console decodes ZSO itself, so this stays compressed on the wire and works even without `lz4` installed on the PC. `.ziso` is advertised as `.zso` because OPL's parser only accepts that spelling. |
| `.chd`, `.cso`, `.ciso` | decompressed on the fly and advertised as `.iso` |

CHD and CSO support is something a stock web server cannot offer: the console asks for
byte ranges of a raw file, so those only work if the server decodes them. Untick
**Decompress CHD/CSO** to serve only `.iso` and `.zso`.

## Limits

- **Read-only.** The client has no PUT or WebDAV, so there is no VMC over HTTP —
  saves still need a physical or USB memory card.
- **No authentication.** The client does not support any, so treat this as a LAN-only
  server and do not port-forward it.
- **Dual-layer DVD9 is unlikely to work.** The client hardcodes `layer1_start = 0`,
  so layer 1 offsets are wrong. Nothing this server can do about it.
- **The game list is capped at 8192 bytes** by the console's receive buffer — roughly
  200 games depending on name length. Past that the server drops whole entries and
  logs how many, rather than letting the console truncate the last line mid-field.
- Filenames must stay under 160 characters; longer ones are skipped, because the
  console truncates them and the resulting request would 404 with no clue why.

## Direct PS2-to-PC link

HTTP mode works over a direct cable exactly as it does on a LAN — tick
**PS2 is plugged directly into this PC** and use the address the DIRECT tab reports.
The direct-link helper works at the DHCP level and does not care which protocol you
then run over it.

## The wire contract

Recorded here because the client is much stricter than "any web server with Range
support", and because **its README does not match its own parser**. Derived from
reading the source at commit `6fced11a`.

**Game list** (`modules/network/httpclient`, via `src/ethsupport.c`)

- `GET /games.csv`, or `GET /<Share>/games.csv` when OPL's Share field is set.
- Accepts 200 or 206. Requires `Content-Length`; **chunked encoding is not
  implemented** in the client, so a chunked list is an empty list.
- The response buffer is 8192 bytes and oversized bodies are silently clipped.

**CSV format** — three columns, per the parser:

```
STARTUP,FILENAME.iso[,MEDIA]
```

`STARTUP` is capped at 12 characters. `FILENAME` carries its extension, and only
`.iso` and `.zso` are recognised. `MEDIA` containing `CD` selects CD media, anything
else DVD. Blank lines and `#` comments are skipped.

> The fork's README documents four columns as `STARTUP,TITLE,MEDIA,FILENAME.iso`.
> That is not what `src/ethsupport.c` parses. Building a list from the README
> produces a game list OPL cannot read.

**In-game streaming** (`modules/iopcore/cdvdman/http.c`)

- `GET /<filename>` at the flat web root, with `Host: <ip>:<port>`,
  `Range: bytes=<start>-<end>` (inclusive) and `Connection: keep-alive`.
- Only spaces are percent-encoded; everything else is sent raw.
- **Must be answered 206.** A 200 is a hard failure (`-5`) on the console.
- The whole response header block must fit a **512-byte** buffer, or the read
  fails (`-3`).
- The body must be **exactly** `end - start + 1` bytes. The driver ignores
  `Content-Length` and `Content-Range` and reads precisely what it asked for, so one
  byte too many desynchronises every later read on that socket — which looks like a
  game that loads and then corrupts, not like a network error.
- Reads are at most 8192 bytes each; on any failure the driver reconnects and retries
  once.

An out-of-range request is answered `416` rather than clamped, deliberately: a short
body would leave the driver blocked waiting for bytes that never arrive, whereas a
416 fails a read it can retry.

## Reproduce the upstream-client test

From the repository root, with Python 3 and GCC on PATH:

```
python conformance/integration/opl_http/run.py
```

Use `--cc /path/to/gcc` to select a compiler. Windows GCC must support Winsock;
Linux uses the system socket library. Internet access to GitHub is required.
CI runs the same command on Linux.

The runner fetches source from `Docmine17/Open-PS2-Loader-HTTP` at
`6fced11a6afafe20c52b8d1a090067e3e1889b99` and verifies each file's SHA256.
It injects unchanged upstream `SendData`, `RecvData`, `url_encode`, `u64_to_str`,
`http_ReadRangeInternal`, `http_ReadRange`, and the CSV parsing block into PC
scaffolding. Socket creation/connection and the PS2 data types are host shims;
the CSV arrives on stdin after Python fetches it over HTTP. This does not exercise
the console's game-list HTTP client, IOP semaphores or connection retry timing.

Every run creates fresh temporary sources, binaries and deterministic fixtures.
A download, hash, extraction, compilation, timeout or assertion failure exits
nonzero. No previously built executable can be used after a failed compilation.
Generated upstream source is not committed or packaged with PS2 Servers.

Checks cover exact parsed startup IDs, filenames and CD/DVD media; sequential and
unaligned reads of ISO, an ampersand filename and CSO served as virtual ISO;
the expected `-5` result for a range beyond EOF followed by byte-exact recovery;
and a rewritten ISO's size after a game-list refresh. The high-offset check tests
decimal formatting above 4 GB, not actual reads from a DVD9 image. CHD is not
covered by this harness.

Replacing an image is detected on the next game-list fetch after the five-second
rescan interval. Finish copying before refreshing the list; this is not support
for replacing an image while the console is playing it.

## PS2 hardware handoff

1. Record the tested PS2 Servers commit (for a packaged build, verify its build
   identity), the OPL ELF's source commit and hash, PS2 model, and network topology.
   The PC test above is pinned to OPL commit
   `6fced11a6afafe20c52b8d1a090067e3e1889b99`; other client revisions need separate
   verification. Run the server from the checkout with `python ps2servers.py`.
2. Start HTTP with a known-good plain ISO in `DVD/`. Set the console's HTTP port
   explicitly to the displayed server port. Reload the list, confirm the startup
   ID/title, boot, and exercise gameplay and loading transitions. Record the
   deepest successful stage and server errors if it stops.
3. Repeat with a filename containing `&`, then a CSO of a known-good game.
   Treat CHD and ZSO as additional hardware cases, not as established by this test.
4. With gameplay stopped, replace an ISO under the same name with a completed,
   known-good image of a different size. Wait at least five seconds, reload the
   game list, and launch again without restarting the server.

Passing the PC test is a prerequisite for this handoff, not evidence that a game
has booted on a PS2. Keep HTTP experimental until hardware results are recorded.
