# Dependency panel

The optional compression dependency panel lives in `launcher/dependency_panel.py`.

`launcher/__init__.py` mounts it when `launcher.gui` loads. The hook is limited to
`launcher.gui`, patches only once, and leaves the GUI unchanged if the panel cannot
be imported.

This keeps the dependency UI isolated while avoiding changes to the larger GUI
entry file. A future refactor can replace the hook with a direct call from the GUI
build path.
