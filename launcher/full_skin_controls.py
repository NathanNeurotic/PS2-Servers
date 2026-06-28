"""Second-pass GUI controls for the PS2 Servers skin.

Adds explicit per-page Apply and Revert to Default actions without changing the
server launch flow. Apply persists the current page settings immediately. Revert
restores the field defaults for that server page and persists them immediately.
"""


def install(app, gui):
    """Install per-server page action controls once before cards are built."""
    if getattr(gui, "_ps2_full_skin_controls_patched", False):
        return

    ServerCard = gui.ServerCard
    LauncherApp = gui.LauncherApp
    original_build = ServerCard._build

    def default_value(card, field):
        if field.kind == "bool":
            return bool(field.default)
        if field.kind == "port":
            return card.server.port_display()
        return "" if field.default is None else str(field.default)

    def reset_to_defaults(card):
        for field in card.server.fields:
            var = card.vars.get(field.key)
            if var is not None:
                var.set(default_value(card, field))
        card.refresh_status(card.app.is_running(card.server.key))

    def add_page_actions(card):
        rows = []
        for child in card.grid_slaves():
            info = child.grid_info()
            if "row" in info:
                try:
                    rows.append(int(info["row"]))
                except (TypeError, ValueError):
                    pass
        row = max(rows or [0]) + 1

        actions = gui.ttk.Frame(card, style="PageActions.TFrame")
        actions.grid(row=row, column=0, columnspan=3, sticky="ew",
                     padx=4, pady=(8, 0))
        actions.columnconfigure(0, weight=1)

        gui.ttk.Label(
            actions,
            text="Page settings",
            style="PageActions.TLabel",
            font=("", 9, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(6, 0))

        gui.ttk.Button(
            actions,
            text="Revert to Default",
            command=lambda c=card: c.revert_to_defaults(),
        ).grid(row=0, column=1, sticky="e", padx=(8, 0), pady=(6, 0))

        gui.ttk.Button(
            actions,
            text="Apply",
            style="Accent.TButton",
            command=lambda c=card: c.apply_page_settings(),
        ).grid(row=0, column=2, sticky="e", padx=(8, 0), pady=(6, 0))

    def build_with_page_actions(card):
        original_build(card)
        add_page_actions(card)

    def apply_page_settings(card):
        card.app.apply_server_settings(card.server.key)

    def revert_to_defaults(card):
        if card.app.is_running(card.server.key):
            if not gui.messagebox.askyesno(
                    "Revert running server page?",
                    "This will reset the saved settings for this page. The running server keeps using its current launch settings until you stop and start it again.\n\nContinue?"):
                return
        reset_to_defaults(card)
        card.app.revert_server_defaults(card.server.key)

    def apply_server_settings(app_obj, key):
        card = app_obj.cards.get(key)
        if not card:
            return
        app_obj._save()
        card.refresh_status(app_obj.is_running(key))
        label = gui.TAB_TITLES.get(key, key.upper())
        app_obj._append_log("setup", "[settings] applied {} page settings\n".format(label))
        if app_obj.is_running(key):
            app_obj._append_log(
                "setup",
                "[settings] {} is already running; restart this server to use changed launch settings\n".format(label),
            )

    def revert_server_defaults(app_obj, key):
        app_obj._save()
        card = app_obj.cards.get(key)
        if card:
            card.refresh_status(app_obj.is_running(key))
        label = gui.TAB_TITLES.get(key, key.upper())
        app_obj._append_log("setup", "[settings] reverted {} page to defaults\n".format(label))
        if app_obj.is_running(key):
            app_obj._append_log(
                "setup",
                "[settings] {} is already running; restart this server to use default launch settings\n".format(label),
            )

    ServerCard._build = build_with_page_actions
    ServerCard.reset_to_defaults = reset_to_defaults
    ServerCard.apply_page_settings = apply_page_settings
    ServerCard.revert_to_defaults = revert_to_defaults
    LauncherApp.apply_server_settings = apply_server_settings
    LauncherApp.revert_server_defaults = revert_server_defaults
    gui._ps2_full_skin_controls_patched = True
