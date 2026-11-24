#!/usr/bin/env python3
"""Fail if a Windows package needs anything Windows 7 does not have.

    python tools/check_win7_imports.py <file-or-folder> [...]

Reads the import tables of every .exe, .dll and .pyd it is given (folders are
walked) and reports:

* a PE header that asks for a newer Windows than 6.1 (Windows 7) -- the loader
  refuses those before any code runs;
* an import from a DLL that Windows 7 does not ship, such as
  api-ms-win-core-path-l1-1-0.dll or shcore.dll;
* an import of a function that Windows 7's kernel32/user32/advapi32 do not
  export, such as kernel32!PssQuerySnapshot.

Issue #330 is exactly the last two: the 32-bit package was built with Python
3.12, whose python312.dll imports api-ms-win-core-path-l1-1-0.dll and
kernel32!PssQuerySnapshot, so it could not start on Windows 7 at all.

This is a static check. It catches a binary that cannot load on Windows 7; it
does not prove that everything behaves on Windows 7, which only running there
does. The DLL list is Windows 7 SP1's own API sets; the function list is the
Windows 8+ exports most likely to appear in a C runtime, Python or Tk build. It
is a deny list, so an API missing from it passes: extend it when one is found.

Standard library only, and runnable on Python 3.8, because the Windows 7 build
job runs it with the Python it ships.
"""

import os
import struct
import sys

# The API-set DLLs Windows 7 SP1 has in System32. Any other api-ms-win-* name
# (an -l1-2-0 or -l2-1-0 revision, or a set Windows 8 introduced such as
# api-ms-win-core-path-l1-1-0) is not there. api-ms-win-crt-* is the Universal
# C Runtime, which Windows 7 gets from Windows Update (KB2999226) and which the
# Python 3.8 installer itself installs; it is allowed separately below.
WIN7_API_SETS = {
    "api-ms-win-core-console-l1-1-0.dll",
    "api-ms-win-core-datetime-l1-1-0.dll",
    "api-ms-win-core-debug-l1-1-0.dll",
    "api-ms-win-core-delayload-l1-1-0.dll",
    "api-ms-win-core-errorhandling-l1-1-0.dll",
    "api-ms-win-core-fibers-l1-1-0.dll",
    "api-ms-win-core-file-l1-1-0.dll",
    "api-ms-win-core-handle-l1-1-0.dll",
    "api-ms-win-core-heap-l1-1-0.dll",
    "api-ms-win-core-interlocked-l1-1-0.dll",
    "api-ms-win-core-io-l1-1-0.dll",
    "api-ms-win-core-libraryloader-l1-1-0.dll",
    "api-ms-win-core-localization-l1-1-0.dll",
    "api-ms-win-core-localregistry-l1-1-0.dll",
    "api-ms-win-core-memory-l1-1-0.dll",
    "api-ms-win-core-misc-l1-1-0.dll",
    "api-ms-win-core-namedpipe-l1-1-0.dll",
    "api-ms-win-core-processenvironment-l1-1-0.dll",
    "api-ms-win-core-processthreads-l1-1-0.dll",
    "api-ms-win-core-profile-l1-1-0.dll",
    "api-ms-win-core-rtlsupport-l1-1-0.dll",
    "api-ms-win-core-string-l1-1-0.dll",
    "api-ms-win-core-synch-l1-1-0.dll",
    "api-ms-win-core-sysinfo-l1-1-0.dll",
    "api-ms-win-core-threadpool-l1-1-0.dll",
    "api-ms-win-core-util-l1-1-0.dll",
    "api-ms-win-core-xstate-l1-1-0.dll",
    "api-ms-win-security-base-l1-1-0.dll",
    "api-ms-win-security-lsalookup-l1-1-0.dll",
    "api-ms-win-security-sddl-l1-1-0.dll",
    "api-ms-win-service-core-l1-1-0.dll",
    "api-ms-win-service-management-l1-1-0.dll",
    "api-ms-win-service-management-l2-1-0.dll",
    "api-ms-win-service-winsvc-l1-1-0.dll",
}

# Whole DLLs that first shipped after Windows 7.
POST_WIN7_DLLS = {
    "shcore.dll",                 # Windows 8.1: SetProcessDpiAwareness, GetDpiForMonitor
    "bcp47mrm.dll",
    "d3d11_1.dll",
    "dcomp.dll",                  # DirectComposition, Windows 8
    "mfplat_vista.dll",
    "windows.storage.dll",
    "combase.dll",                # split out of ole32 in Windows 8
    "kernel.appcore.dll",
    "dxgi1_2.dll",
}

# Functions documented as "Minimum supported client: Windows 8" or later,
# keyed by the DLL a program imports them from.
POST_WIN7_FUNCTIONS = {
    "kernel32.dll": {
        "CopyFile2", "CreateFile2", "CreateFileMappingFromApp",
        "DiscardVirtualMemory", "GetCurrentPackageFamilyName",
        "GetCurrentPackageFullName", "GetCurrentPackageId",
        "GetCurrentPackagePath", "GetCurrentThreadStackLimits",
        "GetFirmwareType", "GetMemoryErrorHandlingCapabilities",
        "GetOverlappedResultEx", "GetPackageFamilyName", "GetPackageFullName",
        "GetPackagePath", "GetProcessInformation", "GetProcessMitigationPolicy",
        "GetSystemCpuSetInformation", "GetSystemTimePreciseAsFileTime",
        "GetThreadDescription", "GetThreadInformation", "MapViewOfFileFromApp",
        "OfferVirtualMemory", "PrefetchVirtualMemory", "PssCaptureSnapshot",
        "PssDuplicateSnapshot", "PssFreeSnapshot", "PssQuerySnapshot",
        "PssWalkMarkerCreate", "PssWalkMarkerFree", "PssWalkMarkerGetPosition",
        "PssWalkMarkerSeekToBeginning", "PssWalkMarkerSetPosition",
        "PssWalkSnapshot", "QueryUnbiasedInterruptTimePrecise",
        "ReclaimVirtualMemory", "SetProcessInformation",
        "SetProcessMitigationPolicy", "SetThreadDescription",
        "SetThreadInformation", "VirtualAlloc2", "MapViewOfFile3",
        "GetFileInformationByName", "CompareObjectHandles",
        "WaitOnAddress", "WakeByAddressAll", "WakeByAddressSingle",
        "CreateRemoteThreadEx2", "GetSystemTimes2",
    },
    "user32.dll": {
        "AdjustWindowRectExForDpi", "EnableNonClientDpiScaling",
        "GetAwarenessFromDpiAwarenessContext", "GetDpiForSystem",
        "GetDpiForWindow", "GetSystemMetricsForDpi",
        "GetThreadDpiAwarenessContext", "GetWindowDpiAwarenessContext",
        "SetProcessDpiAwarenessContext", "SetThreadDpiAwarenessContext",
        "SystemParametersInfoForDpi", "GetPointerType", "GetPointerInfo",
        "EnableMouseInPointer", "SetCoalescableTimer",
        "GetDisplayAutoRotationPreferences", "SetDisplayAutoRotationPreferences",
    },
    "advapi32.dll": {
        "CveEventWrite", "EventSetInformation",
    },
    "ws2_32.dll": {
        "GetAddrInfoExCancel", "GetAddrInfoExOverlappedResult",
    },
}

IMAGE_FILE_EXTENSIONS = (".exe", ".dll", ".pyd")
WIN7_VERSION = (6, 1)


class PEError(Exception):
    pass


def _read_cstr(data, offset):
    end = data.find(b"\0", offset)
    if end < 0:
        raise PEError("unterminated string at 0x%x" % offset)
    return data[offset:end].decode("ascii", "replace")


class PE:
    def __init__(self, data):
        self.data = data
        if data[:2] != b"MZ":
            raise PEError("not an MZ image")
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            raise PEError("no PE signature")
        coff = pe + 4
        (self.machine, nsections, _, _, _, opt_size, _) = struct.unpack_from(
            "<HHIIIHH", data, coff)
        opt = coff + 20
        magic = struct.unpack_from("<H", data, opt)[0]
        if magic == 0x10B:
            self.pe32plus = False
            ndir_off, dir_off = opt + 92, opt + 96
        elif magic == 0x20B:
            self.pe32plus = True
            ndir_off, dir_off = opt + 108, opt + 112
        else:
            raise PEError("unknown optional header magic 0x%x" % magic)
        self.os_version = struct.unpack_from("<HH", data, opt + 40)
        self.subsystem_version = struct.unpack_from("<HH", data, opt + 48)
        ndirs = struct.unpack_from("<I", data, ndir_off)[0]
        self.dirs = [struct.unpack_from("<II", data, dir_off + 8 * i)
                     for i in range(min(ndirs, 16))]
        sec = opt + opt_size
        self.sections = []
        for i in range(nsections):
            (_, vsize, va, rsize, rptr) = struct.unpack_from(
                "<8sIIII", data, sec + 40 * i)
            self.sections.append((va, max(vsize, rsize), rptr, rsize))

    def off(self, rva):
        for va, size, rptr, rsize in self.sections:
            if va <= rva < va + size:
                if rva - va >= rsize:
                    raise PEError("RVA 0x%x is in uninitialised data" % rva)
                return rptr + (rva - va)
        raise PEError("RVA 0x%x is outside every section" % rva)

    def _thunk_names(self, rva):
        size = 8 if self.pe32plus else 4
        fmt = "<Q" if self.pe32plus else "<I"
        flag = 1 << (63 if self.pe32plus else 31)
        names = []
        pos = self.off(rva)
        while True:
            value = struct.unpack_from(fmt, self.data, pos)[0]
            if value == 0:
                return names
            if value & flag:
                names.append("#%d" % (value & 0xFFFF))
            else:
                names.append(_read_cstr(self.data, self.off(value & 0x7FFFFFFF) + 2))
            pos += size

    def imports(self):
        """Yield (dll, [functions], delayed) for both import tables."""
        if len(self.dirs) > 1 and self.dirs[1][0]:
            pos = self.off(self.dirs[1][0])
            while True:
                oft, _, _, name, ft = struct.unpack_from("<IIIII", self.data, pos)
                if not (oft or name or ft):
                    break
                yield (_read_cstr(self.data, self.off(name)),
                       self._thunk_names(oft or ft), False)
                pos += 20
        if len(self.dirs) > 13 and self.dirs[13][0]:
            pos = self.off(self.dirs[13][0])
            while True:
                attrs, name, _, iat, int_ = struct.unpack_from("<IIIII", self.data, pos)
                if not (name or iat or int_):
                    break
                if not attrs & 1:
                    # Pre-VC7 delay descriptors hold VAs, not RVAs. Nothing
                    # built in this century emits them; refuse rather than guess.
                    raise PEError("VA-based delay import descriptor")
                yield (_read_cstr(self.data, self.off(name)),
                       self._thunk_names(int_), True)
                pos += 32


def check_image(path):
    """Return a list of problems with one image; empty means Windows 7 can load it."""
    with open(path, "rb") as handle:
        data = handle.read()
    try:
        pe = PE(data)
    except (PEError, struct.error) as exc:
        return ["cannot parse: %s" % exc]
    problems = []
    for label, version in (("operating system", pe.os_version),
                           ("subsystem", pe.subsystem_version)):
        if tuple(version) > WIN7_VERSION:
            problems.append("PE header asks for %s version %d.%d (Windows 7 is 6.1)"
                            % (label, version[0], version[1]))
    try:
        imported = list(pe.imports())
    except (PEError, struct.error) as exc:
        return problems + ["cannot read import table: %s" % exc]
    for dll, functions, delayed in imported:
        kind = "delay-loads" if delayed else "imports"
        low = dll.lower()
        if low.startswith("api-ms-win-crt-"):
            pass
        elif low.startswith("api-ms-win-") or low.startswith("ext-ms-win-"):
            if low not in WIN7_API_SETS:
                problems.append("%s %s, which Windows 7 does not have" % (kind, dll))
        elif low in POST_WIN7_DLLS:
            problems.append("%s %s, which Windows 7 does not have" % (kind, dll))
        missing = sorted(set(functions) & POST_WIN7_FUNCTIONS.get(low, set()))
        for function in missing:
            problems.append("%s %s!%s, which Windows 7 does not export"
                            % (kind, dll, function))
    return problems


def iter_images(paths):
    for path in paths:
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                dirs.sort()
                for name in sorted(files):
                    if name.lower().endswith(IMAGE_FILE_EXTENSIONS):
                        yield os.path.join(root, name)
        else:
            yield path


def main(argv):
    if not argv:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    checked = 0
    failed = 0
    for path in iter_images(argv):
        checked += 1
        problems = check_image(path)
        if problems:
            failed += 1
            for problem in problems:
                print("%s: %s" % (path, problem))
    if checked == 0:
        print("no .exe, .dll or .pyd files found in: %s" % " ".join(argv))
        return 2
    print("%d of %d Windows image(s) would not load on Windows 7" % (failed, checked)
          if failed else "%d Windows image(s) checked: all load on Windows 7" % checked)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
