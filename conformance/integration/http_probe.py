#!/usr/bin/env python3
"""Socket-level HTTP probe speaking OPL's exact client bytes.

Points at a running PS2 Servers HTTP server and replays what Open PS2 Loader's
HTTP mode actually sends -- the httpclient game-list fetch, then the IOP
driver's keep-alive Range reads -- checking the framing rules that fail silently
on a console rather than loudly on the wire.

Derived from Docmine17/Open-PS2-Loader-HTTP at commit 6fced11a:
  modules/network/httpclient/httpclient.c   (game list)
  modules/iopcore/cdvdman/http.c            (in-game streaming)
  src/ethsupport.c                          (CSV format, URI shapes)

Usage:
  python conformance/integration/http_probe.py --host 127.0.0.1 --port 1100
  python conformance/integration/http_probe.py --port 1100 --game "SLUS_201.74.Rumble Racing.iso"
"""
import argparse
import socket
import sys

#: The driver's response-header buffer. Overflowing it returns -3.
HEADER_BUDGET = 512
#: The game-list receive buffer in src/ethsupport.c.
GAMES_CSV_MAX = 8192
#: HTTP_MAX_CHUNK_SIZE in modules/iopcore/cdvdman/http.h.
CHUNK = 8192

FAILURES = []


class ProtocolError(Exception):
    """Framing broke badly enough that we cannot keep reading."""


def check(ok, label, detail=""):
    print("{}  {}{}".format("PASS" if ok else "FAIL", label,
                            "" if ok else "  -- " + detail))
    if not ok:
        FAILURES.append(label)
    return ok


def read_response(sock):
    """(header_bytes, body_bytes), framed on Content-Length like the client."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(65536)
        if not chunk:
            raise ProtocolError("connection closed before headers arrived")
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    header = head + b"\r\n\r\n"
    length = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            raw = line.split(b":", 1)[1].strip()
            try:
                length = int(raw)
            except ValueError:
                raise ProtocolError(
                    "unparseable Content-Length: {!r}".format(raw))
    while len(rest) < length:
        chunk = sock.recv(65536)
        if not chunk:
            break
        rest += chunk
    # Surplus is a failure, not something to trim. Slicing to length would
    # hide the exact violation this probe exists to catch: a server that
    # sends more than it declared still satisfies a len(body) == CHUNK
    # check while leaving the keep-alive stream desynchronised.
    if len(rest) > length:
        raise ProtocolError(
            "body exceeded Content-Length ({} declared, {} received); "
            "the surplus desynchronises every later read"
            .format(length, len(rest)))
    return header, rest


def url_encode(name):
    """The driver percent-encodes spaces and nothing else."""
    return name.replace(" ", "%20")


def probe_game_list(host, port):
    """What modules/network/httpclient sends, byte for byte."""
    print("\n-- game list --")
    sock = socket.create_connection((host, port), timeout=10)
    try:
        sock.sendall(
            ("GET /games.csv HTTP/1.1\r\n"
             "Accept: text/html, */*\r\n"
             "User-Agent: OPL/1.2\r\n"
             "Host: {}\r\n\r\n".format(host)).encode("ascii"))
        header, body = read_response(sock)
    finally:
        sock.close()

    status = header.split(b"\r\n")[0]
    check(status.startswith(b"HTTP/1.1 200") or status.startswith(b"HTTP/1.1 206"),
          "game list answers 200 or 206", status.decode(errors="replace"))
    check(b"content-length" in header.lower(),
          "game list carries Content-Length",
          "the client cannot frame a response without it")
    check(b"transfer-encoding" not in header.lower(),
          "game list is not chunked",
          "the client's chunked branch is commented out")
    check(len(body) <= GAMES_CSV_MAX,
          "game list fits the console's {}-byte buffer".format(GAMES_CSV_MAX),
          "{} bytes; the console would clip the last line mid-field"
          .format(len(body)))

    rows = [line for line in body.decode("utf-8", "replace").splitlines()
            if line.strip() and not line.startswith("#")]
    check(bool(rows), "game list is not empty", "no games indexed")
    bad = [r for r in rows if len(r.split(",")) != 3]
    check(not bad, "every row is STARTUP,FILENAME,MEDIA",
          "not the four columns the fork's README documents; first bad row: "
          + (bad[0] if bad else ""))
    long_ids = [r for r in rows if len(r.split(",")[0]) > 12]
    check(not long_ids, "every STARTUP fits GAME_STARTUP_MAX (12)",
          long_ids[0] if long_ids else "")
    return rows


def probe_stream(host, port, name):
    """What modules/iopcore/cdvdman/http.c sends, on one keep-alive socket."""
    print("\n-- streaming {} --".format(name))
    encoded = url_encode(name)
    sock = socket.create_connection((host, port), timeout=10)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    try:
        for index, start in enumerate((0, CHUNK, CHUNK * 2)):
            sock.sendall(
                ("GET /{} HTTP/1.1\r\n"
                 "Host: {}:{}\r\n"
                 "Range: bytes={}-{}\r\n"
                 "Connection: keep-alive\r\n\r\n"
                 .format(encoded, host, port, start, start + CHUNK - 1))
                .encode("ascii"))
            header, body = read_response(sock)
            status = header.split(b"\r\n")[0]

            check(status.startswith(b"HTTP/1.1 206"),
                  "read {} is 206".format(index),
                  "a 200 is a hard failure (-5) on the console: "
                  + status.decode(errors="replace"))
            check(len(header) < HEADER_BUDGET,
                  "read {} headers fit {} bytes".format(index, HEADER_BUDGET),
                  "{} bytes; the driver returns -3".format(len(header)))
            check(len(body) == CHUNK,
                  "read {} returned exactly {} bytes".format(index, CHUNK),
                  "got {}; the driver reads a fixed count, so this "
                  "desynchronises every later read".format(len(body)))

        # Out of range must fail cleanly rather than return a short body: the
        # driver blocks waiting for bytes that would never arrive.
        sock.sendall(
            ("GET /{} HTTP/1.1\r\nHost: {}:{}\r\n"
             "Range: bytes=1000000000000-1000000008191\r\n"
             "Connection: keep-alive\r\n\r\n".format(encoded, host, port))
            .encode("ascii"))
        header, _body = read_response(sock)
        status = header.split(b"\r\n")[0]
        check(status.startswith(b"HTTP/1.1 416"),
              "an unsatisfiable range is 416",
              status.decode(errors="replace"))
    finally:
        sock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1100)
    parser.add_argument("--game", default=None,
                        help="Filename to stream (default: the first listed)")
    args = parser.parse_args()

    try:
        rows = probe_game_list(args.host, args.port)
    except OSError as exc:
        print("FAIL  cannot reach {}:{} -- {}".format(args.host, args.port, exc))
        return 2
    except ProtocolError as exc:
        # A nonconforming server should produce a reported failure, not a
        # traceback -- diagnosing one is the whole point of this script.
        print("FAIL  game list framing  -- {}".format(exc))
        return 1

    game = args.game
    if game is None:
        # Only stream a row that actually parsed. Indexing [1] of a malformed
        # row would raise here and hide the CSV failure already reported above.
        valid = [row.split(",") for row in rows if len(row.split(",")) == 3]
        if valid:
            game = valid[0][1]
    if game:
        try:
            probe_stream(args.host, args.port, game)
        except (OSError, ProtocolError) as exc:
            print("FAIL  streaming framing  -- {}".format(exc))
            FAILURES.append("streaming framing")
    else:
        print("SKIP  streaming: no valid row to read")

    print("\n{} check(s) failed".format(len(FAILURES)) if FAILURES
          else "\nall checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
