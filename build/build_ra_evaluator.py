"""Build the owned evaluator's pinned rcheevos library, without desktop sources."""
import json
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def library_name():
    """Return the platform's shared-library filename."""
    return {"Windows": "rcheevos-owned.dll", "Darwin": "librcheevos-owned.dylib"}.get(
        platform.system(), "librcheevos-owned.so")


def build(output=None):
    """Compile rcheevos and our public-API watch adapter; return the output path."""
    rc = ROOT / "vendor/retroachievements/rcheevos"
    target = Path(output) if output else ROOT / "build/native" / library_name()
    target.parent.mkdir(parents=True, exist_ok=True)
    if __package__:
        from .build_achievements import compiler_command
    else:
        from build_achievements import compiler_command
    compiler = compiler_command()
    command = compiler + ["-O2", "-std=gnu99", "-DRC_SHARED"]
    command += ["-dynamiclib"] if platform.system() == "Darwin" else ["-shared"]
    if os.name != "nt":
        command += ["-fPIC"]
    else:
        command += ["-static-libgcc"]
    if platform.system() == "Darwin" and os.environ.get("MACOS_TARGET_ARCH"):
        command += ["-arch", os.environ["MACOS_TARGET_ARCH"]]
    for directory in (rc / "include", rc / "src", rc / "src/rcheevos"):
        command += ["-I", str(directory)]
    sources = sorted((rc / "src/rcheevos").glob("*.c"))
    sources += [rc / "src/rc_util.c", rc / "src/rc_compat.c", rc / "src/rhash/md5.c"]
    sources += [ROOT / "native/ra_watch_probe.c"]
    subprocess.run(command + list(map(str, sources)) + ["-o", str(target), "-lm"],
                   check=True, timeout=180)
    shutil.copy2(rc / "LICENSE", target.parent / "LICENSE-rcheevos.txt")
    provenance = json.loads((ROOT / "vendor/retroachievements/sources.json").read_text())
    (target.parent / "OWNED-EVALUATOR-SOURCES.json").write_text(
        json.dumps({"rcheevos": provenance["rcheevos"],
                    "owned_sources": {"native/ra_watch_probe.c": hashlib.sha256(
                        (ROOT / "native/ra_watch_probe.c").read_bytes()).hexdigest()},
                    "interface": "public rc_runtime API; no xeRAbora desktop sources"},
                   indent=2) + "\n", encoding="utf-8")
    return target


if __name__ == "__main__":
    print(build())
