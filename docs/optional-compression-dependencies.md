# Optional compression dependencies

UDPFS can expose compressed images as `.iso` files when optional compression
support is available.

## GUI behavior

The launcher shows a **Compression support** panel. It can:

- check whether ZSO/LZ4 support is available;
- check whether CHD/libchdr support is available;
- install the Python `lz4` package when running from source;
- explain native `libchdr` setup without requiring users to open a terminal just
  to understand what is missing.

## Packaged releases versus source mode

Normal users should use the packaged PS2 Servers release. They should not need to
install Python just to use the launcher or ZSO/LZ4 support.

Source-mode users already have Python, so the GUI can offer to install missing
Python packages into that Python environment. Packaged releases cannot pip-install
into themselves; Python dependencies must be bundled at build time.

## ZSO/LZ4

ZSO/LZ4 support uses the Python `lz4` package.

When running from source, the GUI can run the current Python environment's pip
install command after user confirmation.

Packaged release builds bundle `lz4` so users without Python can still get
ZSO/LZ4 support.

## CHD/libchdr

CHD support uses native `libchdr`, not a Python package.

The launcher detects whether libchdr is loadable and explains the platform path:

- Windows: release builds should bundle a vetted libchdr DLL. The launcher does
  not download native DLLs at runtime.
- macOS: install libchdr through Homebrew or another trusted package manager.
- Linux: install the distribution package that provides `libchdr.so`.

Runtime native-library installation should only be added after there is a vetted,
pinned, checksum-verified source for each platform.
