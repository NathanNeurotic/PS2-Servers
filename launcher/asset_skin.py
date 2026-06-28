"""Runtime hooks for the asset-driven PS2 skin.

This keeps the actual GUI code lightweight while letting us mount supplied theme
assets onto the existing Tk/ttk UI. The first pass focuses on the supplied server
icon set and softer visual defaults; larger banner/background assets can be added
through the same registry without changing the GUI flow again.
"""

from . import theme_assets

_TAB_ICON_BY_TEXT = {
    "SMBV1": "ICON_SMB",
    "UDPFS": "ICON_UDPFS",
    "UDPBD": "ICON_UDPBD",
}


def _photo(app, gui, name):
    data = theme_assets.ASSETS.get(name)
    if not data:
        return None
    try:
        image = gui.tk.PhotoImage(data=data)
    except Exception:
        return None
    photos = getattr(app, "_ps2_theme_photos", [])
    photos.append(image)
    app._ps2_theme_photos = photos
    return image


def _install_tab_icons(app, gui):
    icons = {}
    for key in ("ICON_SMB", "ICON_UDPFS", "ICON_UDPBD"):
        image = _photo(app, gui, key)
        if image:
            icons[key] = image
    if not icons:
        return

    # Store images on this root so their lifetime matches this Tk instance.
    # The patched Notebook.add below looks them up dynamically from the notebook's
    # toplevel instead of closing over the first app instance's images.
    app.root._ps2_tab_icons = icons

    if getattr(gui, "_ps2_asset_tab_icons_patched", False):
        return

    original_add = gui.ttk.Notebook.add

    def add_with_icons(self, child, **kwargs):
        raw_text = kwargs.get("text", "")
        icon_key = _TAB_ICON_BY_TEXT.get(raw_text.strip().upper())
        if icon_key:
            root = self.winfo_toplevel()
            root_icons = getattr(root, "_ps2_tab_icons", {})
            if icon_key in root_icons:
                kwargs.setdefault("image", root_icons[icon_key])
                kwargs.setdefault("compound", "left")
        return original_add(self, child, **kwargs)

    gui.ttk.Notebook.add = add_with_icons
    gui._ps2_asset_tab_icons_patched = True


def _soften_styles(root, gui):
    style = gui.ttk.Style(root)
    palette = {
        "bg": "#040915",
        "surface": "#081427",
        "surface2": "#0c1b33",
        "edge": "#15365f",
        "text": "#e7f2ff",
        "muted": "#b4c6df",
        "accent": "#009cff",
        "accent2": "#46d9ff",
        "warn": "#ffd45f",
        "ok": "#5ff0bc",
        "danger": "#ff5676",
    }

    root.configure(background=palette["bg"])
    style.configure("TFrame", background=palette["bg"])
    style.configure("Header.TFrame", background=palette["bg"])
    style.configure("TLabel", background=palette["bg"], foreground=palette["text"])
    style.configure("Muted.TLabel", background=palette["bg"], foreground=palette["muted"])

    # Utility cards: softer border, brighter secondary text.
    style.configure("Admin.TFrame", background=palette["surface"], relief="flat", borderwidth=0)
    style.configure("Admin.TLabel", background=palette["surface"], foreground=palette["muted"])
    style.configure("AdminYes.TLabel", background=palette["surface"], foreground=palette["ok"])
    style.configure("AdminNo.TLabel", background=palette["surface"], foreground=palette["warn"])

    # Server cards: reduce harsh neon boxing and improve text contrast.
    style.configure("TLabelframe", background=palette["surface"], foreground=palette["text"],
                    bordercolor=palette["edge"], lightcolor=palette["edge"],
                    darkcolor=palette["surface"], relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=palette["bg"], foreground=palette["accent2"],
                    font=("", 10, "bold"))

    style.configure("TButton", background=palette["surface2"], foreground=palette["text"],
                    padding=(10, 5), borderwidth=1, focusthickness=1,
                    focuscolor=palette["accent"])
    style.map("TButton",
              background=[("active", "#12305a"), ("pressed", palette["accent"])],
              foreground=[("disabled", "#6f7f98"), ("pressed", "#ffffff")])
    style.configure("Accent.TButton", background=palette["accent"], foreground="#ffffff",
                    padding=(12, 6), borderwidth=0)

    style.configure("Server.TNotebook", background=palette["bg"], borderwidth=0, tabmargins=(8, 8, 8, 0))
    style.configure("Server.TNotebook.Tab", padding=(14, 8), font=("", 10, "bold"),
                    background=palette["surface"], foreground=palette["muted"], borderwidth=1)
    style.map("Server.TNotebook.Tab",
              background=[("selected", "#102b53"), ("active", palette["surface2"]),
                          ("!selected", palette["surface"])],
              foreground=[("selected", palette["accent2"]), ("active", palette["text"]),
                          ("!selected", palette["muted"])])


def _install_page_controls(app, gui):
    try:
        from . import full_skin_controls
    except ImportError:
        return
    full_skin_controls.install(app, gui)


def install(app, gui):
    """Install lightweight asset-driven visual hooks for this app instance."""
    _soften_styles(app.root, gui)
    _install_page_controls(app, gui)
    _install_tab_icons(app, gui)
