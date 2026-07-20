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
    try:
        return theme_assets.photo_fit(gui, name, owner=app, max_width=56, max_height=28)
    except gui.tk.TclError:
        return None


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
    # Share ONE palette with main.py (launcher/theme.py). This layer runs last
    # and wins for the styles below, so sourcing the same colours here is what
    # keeps the two theme layers from drifting into two different-looking skins.
    from . import theme
    p = theme.PALETTE
    palette = {
        "bg": p["bg"],
        "surface": p["panel"],
        "surface2": p["panel2"],
        "panel3": p["panel3"],
        "edge": p["edge"],
        "text": p["text"],
        "muted": p["muted"],
        "accent": p["accent"],
        "accent_hover": p["accent_hover"],
        "accent2": p["accent2"],
        "warn": p["warn"],
        "ok": p["ok"],
        "danger": p["error"],
        "disabled": p["disabled"],
    }

    root.configure(background=palette["bg"])
    style.configure("TFrame", background=palette["bg"])
    style.configure("Header.TFrame", background=palette["bg"])
    style.configure("TLabel", background=palette["bg"], foreground=palette["text"])
    style.configure("Muted.TLabel", background=palette["bg"], foreground=palette["muted"])
    style.configure("TopStrip.TFrame", background=palette["surface"], relief="flat")
    style.configure("TopStripTitle.TLabel", background=palette["surface"],
                    foreground=palette["text"])
    style.configure("TopStripHint.TLabel", background=palette["surface"],
                    foreground=palette["muted"])
    style.configure("Footer.TFrame", background=palette["surface"], relief="flat")

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
    style.configure("Card.TLabelframe", background=palette["surface"],
                    foreground=palette["text"], bordercolor=palette["edge"],
                    lightcolor=palette["edge"], darkcolor=palette["surface"],
                    relief="solid", borderwidth=1)
    style.configure("Card.TLabelframe.Label", background=palette["bg"],
                    foreground=palette["accent2"], font=("", 10, "bold"))
    style.configure("Card.TFrame", background=palette["surface"])
    style.configure("Card.TLabel", background=palette["surface"], foreground=palette["text"])
    style.configure("CardMuted.TLabel", background=palette["surface"], foreground=palette["muted"])
    style.configure("CardHelp.TLabel", background=palette["surface"], foreground=palette["muted"])
    style.configure("CardHint.TLabel", background=palette["surface"], foreground=palette["accent2"])
    style.configure("CardStatus.TLabel", background=palette["surface"], foreground=palette["muted"])
    style.configure("Card.TCheckbutton", background=palette["surface"], foreground=palette["text"])
    style.map("Card.TCheckbutton", background=[("active", palette["surface2"])])
    style.configure("PageActions.TFrame", background=palette["surface"], relief="flat")
    style.configure("PageActions.TLabel", background=palette["surface"], foreground=palette["muted"])

    # Secondary buttons: flat surface with a subtle slate edge; hover lifts one
    # elevation step rather than jumping to a bright fill. The solid accent fill
    # is reserved for Accent.TButton (the primary action).
    style.configure("TButton", background=palette["surface"], foreground=palette["text"],
                    padding=(12, 6), borderwidth=1, focusthickness=1,
                    focuscolor=palette["accent"], bordercolor=palette["edge"],
                    lightcolor=palette["edge"], darkcolor=palette["surface"])
    # State order matters: a pressed widget is ALSO active, and ttk uses the
    # first matching statespec -- so pressed must come before active or the
    # click feedback (panel3) never shows.
    style.map("TButton",
              background=[("pressed", palette["panel3"]), ("active", palette["surface2"])],
              foreground=[("disabled", palette["disabled"]), ("pressed", "#ffffff")])
    style.configure("Accent.TButton", background=palette["accent"], foreground="#ffffff",
                    padding=(12, 6), borderwidth=0)
    # Hover/press DARKEN to accent_hover -- white text stays well above WCAG AA on
    # both. (The light accent2 is a text colour for dark surfaces, not a fill for
    # white text, so it is deliberately not used here.)
    style.map("Accent.TButton",
              background=[("disabled", palette["surface2"]), ("pressed", palette["accent_hover"]),
                          ("active", palette["accent_hover"])],
              foreground=[("disabled", palette["disabled"]), ("!disabled", "#ffffff")])

    # NOTE: the notebook tab bar is deliberately NOT restyled here -- main.py's
    # apply_theme is the single owner of Server.TNotebook / .Tab, so the tabs
    # have exactly one definition to reason about.


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
