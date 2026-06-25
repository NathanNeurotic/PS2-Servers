#!/bin/sh
# Launch the PS2 Servers GUI from source (Linux/macOS).
# The packaged binary will not need Python; this script is for running from source.
cd "$(dirname "$0")" || exit 1
exec python3 -m launcher
