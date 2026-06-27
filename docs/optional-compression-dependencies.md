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

## ZSO/LZ4

ZSO/LZ4 support uses the Python `lz4` package.

When running from source, the GUI can run the current Python environment's pip
install command after user confirmation.

Packaged releases cannot pip-install into themselves. Release builds should
bundle `lz4` when always-on ZSO/LZ4 support is desired.

## CHD/libchdr

CHD support uses native `libchdr`, not a Python package.

The launcher detects whether libchdr is loadable and explains the platform path:

- Windows: release builds should bundle a vetted libchdr DLL. The launcher does
  not download native DLLs at runtime.
- macOS: install libchdr through Homebrew or another trusted package manager.
- Linux: install the distribution package that provides `libchdr.so`.

Runtime native-library installation should only be added after there is a vetted,
pinned, checksum-verified source for each platform.
