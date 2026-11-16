"""Offline comparison of current managed-legacy/upstream-v2 compilers; no account or sockets."""
import argparse
from pathlib import Path
import struct
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True,
                        help="Local hacan359/xerabora v2.0.0 source checkout")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    upstream = args.upstream.resolve()
    rc = root / "vendor/retroachievements/rcheevos"
    source = (root / "conformance/ra_dependency/watchlist.c").read_text()
    start = source.index("    if(i==0) {")
    end = source.index("    size=watchlist_serialize", start)
    source = source[:start] + source[end:]
    # The upstream parser deliberately accepts direct-only pointer snapshots;
    # its historical behavior is not our stricter production receiver contract.
    start = source.index("    if(i==4 || i==5) {")
    end = source.index('    printf("%u %d %d %d ', start)
    source = source[:start] + source[end:]
    source = source.replace(
        "if(!watchlist_build(&client)) return 3;",
        'if(!watchlist_build(&client)) {printf("%u BUILD-REFUSED\\n",i); '
        'rc_runtime_destroy(&game.runtime); continue;}')
    source = source.replace("int main(void)", "void diag_reset(void) {}\nint main(void)")
    source = source.replace(
        '"I:0xX001000_I:0xX000004_0xH000008=1"',
        '"I:0xX001000_I:0xX000004_0xH000008=1",\n'
        '    "I:0xH001000*4_0xH080000=1",\n'
        '    "I:0xU001000*4_0xH080000=1",\n'
        '    "I:0xM001000*4_0xH080000=1",\n'
        '    "I:4_0xH080000=1"')
    sources = sorted((rc / "src/rcheevos").glob("*.c"))
    sources += [rc / path for path in ("src/rc_util.c", "src/rc_compat.c", "src/rhash/md5.c")]
    outputs = {}
    with tempfile.TemporaryDirectory(prefix="xerabora-v2-audit-") as directory:
        temp = Path(directory)
        probe = temp / "probe.c"
        probe.write_text(source, encoding="utf-8")
        for name, base in (("managed-legacy", root / "vendor/retroachievements/xerabora"),
                           ("v2", upstream)):
            client = base / "client/src"
            command = ["gcc", "-O1", "-std=gnu99"]
            for inc in (rc / "include", rc / "src", rc / "src/rcheevos", client, base / "protocol"):
                command += ["-I", str(inc)]
            exe = temp / (name + (".exe" if sys.platform == "win32" else ""))
            command += list(map(str, sources + [client / "watchlist.c", client / "snapshot.c", probe]))
            subprocess.run(command + ["-o", str(exe), "-lm"], check=True)
            result = subprocess.check_output([str(exe)], text=True)
            outputs[name] = result.splitlines()
            for line in outputs[name]:
                fields = line.split()
                if len(fields) < 5:
                    print(name, line)
                    continue
                wire = bytes.fromhex(fields[4])
                count = struct.unpack_from("<I", wire, 8)[0]
                print(name, "case/count/nodes/snapshot:", " ".join(fields[:4]),
                      "tail:", wire[16 + count * 4:].hex())
    sys.path.insert(0, str(root))
    from launcher.ra_watchlist import validate_watchlist
    for name, lines in outputs.items():
        wire = bytes.fromhex(lines[4].split()[4])
        try:
            validate_watchlist(wire)
            print("owned validator", name, "accepted")
        except ValueError as error:
            print("owned validator", name, "rejected:", error)


if __name__ == "__main__":
    main()
