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
install Python just to use the launcher, ZSO/LZ4 support, or CHD support.

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

Release builds build libchdr from source during GitHub Actions packaging and
stage it into the packaged app's `native/` directory. The CHD loader searches
that bundled directory before falling back to system libraries.

Source users can still provide libchdr through their platform's trusted package
mechanism:

- Windows: use the packaged release for bundled CHD support.
- macOS: `brew install libchdr`.
- Linux: install the distribution package that provides `libchdr.so`.

Runtime native-library downloads are intentionally avoided. If runtime native
installation is ever added, it must use a vetted, pinned, checksum-verified source
for each platform.
