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
            # The FIELD's own default, never server.port_display() -- that returns
            # ServerDef.default_port, so Revert handed every port field the server's
            # main port and set Data port to the discovery port, which the server
            # refuses to start on. A falsy default means "auto" and renders blank.
            if not field.default:
                return ""
            return ("0x%04X" % field.default if card.server.port_is_hex
                    else str(field.default))
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

    def normalized_port(value):
        """A port as a number, so 0xF5F6 and 62966 stop looking like a change.

        base=0 reads both the hex the field prefills and the decimal someone may
        retype. Anything unparseable falls back to its text: a port field holding
        junk is the user's business, not a reason to raise here.
        """
        if not value:
            return ""
        try:
            return int(str(value).strip(), 0)
        except (TypeError, ValueError):
            return str(value).strip()

    def pending_launch_changes(app_obj, card, key):
        """Field labels whose value differs from what the running server started on.

        ServerCard._active_values is the snapshot taken at launch and cleared at
        stop, so it is the only honest answer to "what is this process actually
        using" -- the widgets have moved on. Ports compare as numbers and flags as
        bools, so a port retyped 62966 after launching as 0xF5F6 is not a change:
        prompting to restart over how a number is spelled would teach people to
        click through the prompt, which is how a confirm stops protecting anything.
        """
        was = getattr(card, "_active_values", None)
        if not was:
            return []
        now = card.values()
        changed = []
        for field in card.server.fields:
            before, after = was.get(field.key), now.get(field.key)
            if field.kind == "bool" or isinstance(before, bool) or isinstance(after, bool):
                same = bool(before) == bool(after)
            elif field.kind == "port":
                same = normalized_port(before) == normalized_port(after)
            else:
                same = str(before or "") == str(after or "")
            if not same:
                changed.append(field.label)
        return changed

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
        if not app_obj.is_running(key):
            return

        # Settings only reach a server through its command line, so a running one
        # keeps whatever it launched with -- ticking "Modulo" mid-session changes
        # nothing until it restarts, and nothing said so except a log line nobody
        # reads. Offer the restart instead. Never do it silently: a restart drops
        # whatever the console is doing, and a game mid-load is a bad thing to end
        # on someone's behalf without asking.
        changed = pending_launch_changes(app_obj, card, key)
        if not changed:
            app_obj._append_log(
                "setup",
                "[settings] {} is already running; nothing it launched with has changed\n".format(label),
            )
            return

        if not gui.messagebox.askyesno(
                "Restart {} to apply?".format(label),
                "{} is running, and these settings only take effect at start:\n\n"
                "{}\n\n"
                "Restart it now? Anything the PS2 is currently loading will stop.".format(
                    label, "\n".join("  • " + c for c in changed))):
            app_obj._append_log(
                "setup",
                "[settings] {} left running; restart it to use the changed settings\n".format(label),
            )
            return

        app_obj._append_log("setup", "[settings] restarting {} to apply\n".format(label))
        app_obj.stop_server(key)
        app_obj.start_server(key)

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
