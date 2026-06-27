# Dependency panel status

The dependency panel branch is focused on noob-friendly compressed-image support.

Packaged releases should include Python `lz4` and a native `libchdr` built during
release packaging, so normal users do not need Python, pip, Homebrew, or DLL
hunting just to use compressed games.

Source-mode users can still use the panel to check dependency status and install
`lz4` into the active Python environment.
