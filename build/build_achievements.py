"""Build the pinned MIT xeRAbora/rcheevos engine bundled by PS2-Servers."""
import os
import platform
import re
import shlex
import shutil
import subprocess
import hashlib
import urllib.request
import zipfile
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "retroachievements"

def build():
    system = platform.system()
    compiler = os.environ.get("RA_CC")
    if system == "Windows" and not compiler:
        # One pinned x64-host toolchain can emit both Windows release targets,
        # including when the packager itself uses 32-bit Python.
        tools = ROOT / "build" / ".ra-tools"
        zig = tools / "zig-windows-x86_64-0.13.0" / "zig.exe"
        if not zig.is_file():
            tools.mkdir(parents=True, exist_ok=True)
            archive = tools / "zig.zip"
            urllib.request.urlretrieve("https://ziglang.org/download/0.13.0/zig-windows-x86_64-0.13.0.zip", archive)
            if hashlib.sha256(archive.read_bytes()).hexdigest() != "d859994725ef9402381e557c60bb57497215682e355204d754ee3df75ee3c158":
                raise RuntimeError("Zig toolchain checksum mismatch")
            with zipfile.ZipFile(archive) as source:
                source.extractall(tools)
            archive.unlink()
        arch = os.environ.get("WINDOWS_TARGET_ARCH") or ("x86" if sys.maxsize <= 2**31-1 else "x64")
        if arch not in ("x86", "x64"):
            raise ValueError("WINDOWS_TARGET_ARCH must be x86 or x64")
        cc = [str(zig), "cc", "-target", "x86-windows-gnu" if arch == "x86" else "x86_64-windows-gnu"]
    else:
        cc = shlex.split(compiler or "cc")
    if not shutil.which(cc[0]):
        raise RuntimeError("RetroAchievements engine requires a C compiler (RA_CC)")
    client = VENDOR / "xerabora" / "client"
    rc = VENDOR / "rcheevos"
    makefile = (client / "Makefile").read_text()
    sources = re.search(r"RC_SRC := (.*?)\n\nSRC :=", makefile, re.S).group(1)
    sources = [rc / x for x in re.findall(r"\$\(RC\)/([^\s\\]+\.c)", sources)]
    sources += [path for path in sorted((rc / "src/rhash").glob("*.c")) if path not in sources]
    sources += [client / x for x in re.findall(r"src/[a-z_]+\.c", re.search(r"\nSRC := (.*?)\n\nall:", makefile, re.S).group(1))]
    sources += [client / "src" / ("http_winhttp.c" if system == "Windows" else "http_curl.c")]
    target = ROOT / "build" / "native" / ("ps2ra.exe" if system == "Windows" else "ps2ra")
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = cc + ["-O2", "-std=gnu99", "-DXERABORA_VERSION=\"0.1.0-alpha.16+ps2servers\""]
    for include in [rc / "include", rc / "src", rc / "src/rcheevos", rc / "src/rapi", VENDOR / "xerabora/protocol"]:
        cmd += ["-I", str(include)]
    arch = os.environ.get("MACOS_TARGET_ARCH")
    if system == "Darwin" and arch:
        cmd += ["-arch", arch]
    if system == "Windows":
        cmd += ["-D_WIN32_WINNT=0x0601", "-static"]
    cmd += ["-o", str(target)] + list(map(str, sources))
    cmd += (["-lws2_32", "-lwinhttp", "-lwinmm", "-lshell32", "-lcrypt32", "-lm"] if system == "Windows" else ["-lcurl", "-lm"])
    environment = os.environ.copy()
    environment["ZIG_GLOBAL_CACHE_DIR"] = str(ROOT / "build/.ra-tools/cache")
    temporary = ROOT / "build/.ra-tools/tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    environment["TMP"] = environment["TEMP"] = str(temporary)
    subprocess.run(cmd, check=True, env=environment)
    for source, name in [(VENDOR / "xerabora/client/LICENSE", "LICENSE-xerabora.txt"),
                         (rc / "LICENSE", "LICENSE-rcheevos.txt"),
                         (VENDOR / "sources.json", "RETROACHIEVEMENTS-SOURCES.json")]:
        shutil.copy2(source, target.parent / name)
    for license_file in (client / "ui/vendor").glob("LICENSE.*"):
        shutil.copy2(license_file, target.parent / license_file.name)
    return target

if __name__ == "__main__":
    print(build())
