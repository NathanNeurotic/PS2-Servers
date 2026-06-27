#!/usr/bin/env python3
"""Build and stage libchdr for packaged PS2 Servers releases.

This intentionally builds libchdr from source during CI instead of downloading
unverified native binaries. The staged output is copied to build/native/, which
build/build.py includes as the app's bundled native library directory.
"""

import glob
import os
import platform
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_ROOT = os.path.join(ROOT, "build")
SRC_DIR = os.path.join(BUILD_ROOT, "libchdr-src")
CMAKE_DIR = os.path.join(BUILD_ROOT, "libchdr-cmake")
NATIVE_DIR = os.path.join(BUILD_ROOT, "native")

LIBCHDR_REPO = "https://github.com/rtissera/libchdr.git"
LIBCHDR_COMMIT = "04a177ee3cea055d93da2d5839d3413168837c6f"


def run(cmd, cwd=None):
    print("Running:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=cwd or ROOT)


def reset_dir(path):
    if os.path.isdir(path):
        shutil.rmtree(path)
    os.makedirs(path, exist_ok=True)


def clone_source():
    if os.path.isdir(SRC_DIR):
        shutil.rmtree(SRC_DIR)
    run(["git", "clone", "--depth", "1", LIBCHDR_REPO, SRC_DIR])
    # The pinned commit is currently the default-branch head. Keep checkout explicit
    # so future source drift does not silently alter released native libraries.
    run(["git", "fetch", "--depth", "1", "origin", LIBCHDR_COMMIT], cwd=SRC_DIR)
    run(["git", "checkout", "--detach", LIBCHDR_COMMIT], cwd=SRC_DIR)


def configure_and_build():
    reset_dir(CMAKE_DIR)
    cmd = [
        "cmake", "-S", SRC_DIR, "-B", CMAKE_DIR,
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_SHARED_LIBS=ON",
        "-DCHDR_WANT_RAW_DATA_SECTOR=ON",
        "-DCHDR_WANT_SUBCODE=ON",
    ]

    system = platform.system()
    if system == "Windows":
        # Prefer a self-contained runtime for the DLL where supported by CMake/MSVC.
        cmd.extend([
            "-DCMAKE_POLICY_DEFAULT_CMP0091=NEW",
            "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded$<$<CONFIG:Debug>:Debug>",
        ])
    elif system == "Darwin":
        arch = os.environ.get("MACOS_TARGET_ARCH")
        if arch:
            cmd.append("-DCMAKE_OSX_ARCHITECTURES=" + arch)
        cmd.append("-DCMAKE_OSX_DEPLOYMENT_TARGET=11.0")

    run(cmd)
    run(["cmake", "--build", CMAKE_DIR, "--config", "Release", "--target", "chdr"])


def find_outputs():
    system = platform.system()
    patterns = []
    if system == "Windows":
        patterns = ["**/chdr.dll", "**/libchdr.dll", "**/libchdr-0.dll"]
    elif system == "Darwin":
        patterns = ["**/libchdr*.dylib"]
    else:
        patterns = ["**/libchdr.so*", "**/libchdr*.so*"]

    found = []
    for pattern in patterns:
        found.extend(glob.glob(os.path.join(CMAKE_DIR, pattern), recursive=True))

    files = []
    for path in found:
        if os.path.isfile(path) and path not in files:
            files.append(path)
    return files


def stage_outputs(files):
    reset_dir(NATIVE_DIR)
    if not files:
        raise RuntimeError("libchdr build produced no native library files")

    for src in files:
        dest = os.path.join(NATIVE_DIR, os.path.basename(src))
        print("Staging", src, "->", dest)
        shutil.copy2(src, dest)

    with open(os.path.join(NATIVE_DIR, "LIBCHDR_SOURCE.txt"), "w", encoding="utf-8") as f:
        f.write("libchdr bundled for PS2 Servers releases\n")
        f.write("Repository: {}\n".format(LIBCHDR_REPO))
        f.write("Commit: {}\n".format(LIBCHDR_COMMIT))
        f.write("Built by: build/build_libchdr.py\n")


def main():
    clone_source()
    configure_and_build()
    files = find_outputs()
    for path in files:
        print("Found native library:", path)
    stage_outputs(files)
    return 0


if __name__ == "__main__":
    sys.exit(main())
