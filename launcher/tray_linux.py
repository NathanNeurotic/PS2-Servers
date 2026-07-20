"""Linux system-tray backend (pystray), matching the Windows tray contract.

The Windows tray in ``tray.py`` is a pure-ctypes Shell_NotifyIcon. Linux has no
single tray API, so this backend leans on ``pystray`` -- a small, mature library
that speaks the modern StatusNotifierItem/AppIndicator protocol (KDE, XFCE,
Cinnamon, MATE, Budgie, GNOME-with-extension) and falls back to the legacy XEMBED
tray (which XFCE/MATE still accept). pystray is an OPTIONAL dependency: the
packaged AppImage bundles it, and everywhere it is absent this module simply
reports itself unavailable and the launcher keeps its normal close-to-quit
behaviour.

The public surface mirrors ``tray.SystemTray`` exactly -- ``SystemTray(tooltip,
on_open, on_quit)`` with ``start() -> bool`` and ``stop()`` -- so ``gui.py`` wires
the Linux tray through the identical code path as Windows. ``on_open`` /
``on_quit`` fire on pystray's own thread and MUST only hand off to the GUI thread
(the launcher gives them callbacks that just push to a queue the Tk loop drains).

Safety: ``start()`` returns True only after pystray reports the icon set up. That
is still weaker than Win32's synchronous NIM_ADD result -- a tray host that is
absent cannot always be detected -- so the launcher additionally defaults
close/minimize-to-tray OFF on Linux, making the user opt in only once they can
see the icon actually appears. That combination means a missing tray can never
trap the user with a hidden, unrecoverable window.
"""

import importlib.util
import os
import platform
import threading


def _has_module(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _has_session_bus():
    """A tray only means anything with a session message bus. Without one
    (headless, some minimal/CI environments) there is nothing to register with,
    so report unavailable rather than spawn a pointless icon thread."""
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return True
    uid = getattr(os, "getuid", lambda: None)()
    if uid is not None and os.path.exists("/run/user/{}/bus".format(uid)):
        return True
    return False


def available():
    """True only if a Linux tray could plausibly work here: Linux, pystray and
    Pillow importable, and a session bus present. This gates ``tray.AVAILABLE``,
    so a machine without the pieces shows no tray options at all."""
    return (platform.system() == "Linux"
            and _has_session_bus()
            and _has_module("pystray")
            and _has_module("PIL"))


def _icon_image():
    """A PIL image for the tray icon. Prefer the shipped LOGO.png; fall back to a
    small generated PS2-blue badge so the tray still has an icon in source trees
    without the theme assets."""
    try:
        from PIL import Image
    except Exception:
        return None

    here = os.path.dirname(os.path.abspath(__file__))
    logo = os.path.join(here, "assets", "theme", "LOGO.png")
    if os.path.exists(logo):
        try:
            return Image.open(logo).convert("RGBA").resize((64, 64))
        except Exception:
            pass

    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (64, 64), (13, 20, 32, 255))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([5, 5, 58, 58], radius=10,
                               outline=(63, 140, 255, 255), width=3)
        draw.text((15, 24), "PS2", fill=(116, 182, 255, 255))
        return img
    except Exception:
        return None


class SystemTray:
    """A single tray icon with an Open / Quit menu, pystray-backed.

    Same contract as ``tray.SystemTray`` on Windows: callbacks fire on the tray's
    own thread and should only marshal to the GUI thread.
    """

    def __init__(self, tooltip, on_open, on_quit):
        self.tooltip = tooltip
        self.on_open = on_open
        self.on_quit = on_quit
        self._icon = None
        self._thread = None
        self._ready = threading.Event()
        self._ok = False

    def start(self):
        try:
            import pystray
        except Exception:
            return False
        image = _icon_image()
        if image is None:
            return False

        def _open(icon=None, item=None):
            self._safe(self.on_open)

        def _quit(icon=None, item=None):
            self._safe(self.on_quit)

        # default=True makes a left click (or double click) invoke Open, matching
        # the Windows tray where left-click restores the window.
        menu = pystray.Menu(
            pystray.MenuItem("Open PS2 Servers", _open, default=True),
            pystray.MenuItem("Quit", _quit),
        )
        try:
            self._icon = pystray.Icon("ps2servers", image, self.tooltip, menu)
        except Exception:
            return False

        def _setup(icon):
            # Runs on pystray's loop thread once the backend is up. Marking the
            # icon visible here is the closest signal we get that it registered.
            try:
                icon.visible = True
                self._ok = True
            except Exception:
                self._ok = False
            finally:
                self._ready.set()

        def _run():
            try:
                self._icon.run(setup=_setup)
            except Exception:
                self._ok = False
                self._ready.set()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        # Give the backend a moment to come up; if it never signals, treat the
        # tray as failed so the caller falls back to close-to-quit.
        self._ready.wait(timeout=4.0)
        return bool(self._ok)

    @staticmethod
    def _safe(fn):
        try:
            fn()
        except Exception:
            pass

    def stop(self):
        icon = self._icon
        if icon is not None:
            try:
                icon.visible = False
            except Exception:
                pass
            try:
                icon.stop()
            except Exception:
                pass
        self._icon = None
