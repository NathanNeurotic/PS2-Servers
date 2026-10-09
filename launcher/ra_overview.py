"""Read-only native RetroAchievements overview using the managed local engine.

No credentials, network requests, or game-file probes on Tk's UI thread.
The embedded upstream page remains available for sign-in and full features.
"""
import json
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import urllib.error
import urllib.request
import webbrowser


def short(value, limit=120):
    return " ".join(str(value or "").split())[:limit]


def count(value):
    return max(0, value) if type(value) is int else 0


def filter_achievements(rows, query="", status="All"):
    """Pure local search/filter; never makes RA Web API requests."""
    needle = str(query or "").strip().casefold()[:120]
    return [row for row in rows
            if (status == "All" or row[2] == status)
            and (not needle or needle in row[0].casefold()
                 or needle in row[1].casefold())]


def view_model(state, verified=None):
    """Pure adapter for the upstream /state JSON, with no false 'playing' claim."""
    if not isinstance(state, dict):
        return {"account": "Engine not reachable", "console": "No live telemetry",
                "game": "No achievement set loaded", "rows": [], "unlocks": [],
                "tracking": [], "playing": False}
    login = state.get("login") if isinstance(state.get("login"), dict) else {}
    console = state.get("console") if isinstance(state.get("console"), dict) else {}
    game = state.get("game") if isinstance(state.get("game"), dict) else {}
    user = short(login.get("user"), 64)
    account = ("Signed in: " + user if login.get("ok") and user else
               "Signed in" if login.get("ok") else "Not signed in")
    if login.get("webapi") is False:
        account += " · Web API key not configured"
    numbers = ("{} frames · {} packets · {} gaps · {} duplicates · {} torn".format(
        count(console.get("frames")), count(console.get("packets")),
        count(console.get("gaps")), count(console.get("dupes")),
        count(console.get("torn"))))
    connection = ("Console linked · " + numbers if console.get("connected") is True
                  else "Waiting for PS2")
    if isinstance(verified, dict) and verified.get("state") == "stalled":
        connection = "Console telemetry stalled · " + numbers
    title = short(game.get("title"), 100)
    serial = short(game.get("serial"), 24)
    # /state may contain a previously checked game, or a 'follow my play'
    # game from another emulator. Only the separate, counter-validated
    # session tracker can certify the current physical PS2 game.
    playing = bool(isinstance(verified, dict)
                   and verified.get("state") == "playing"
                   and serial and title and verified.get("title") == title)
    summary = (("Playing on PS2: " if playing else "Loaded set (not verified playing): ")
               + title if title else "No achievement set loaded")
    achievements = game.get("achievements") if isinstance(game.get("achievements"), list) else []
    rows = []
    for value in achievements[:400]:
        if not isinstance(value, dict) or type(value.get("id")) is not int:
            continue
        status = "Unlocked" if value.get("state") == 2 else "Not unlocked"
        measured = short(value.get("measured"), 60)
        progress = measured or ("{}%".format(round(value["percent"], 1))
                                if isinstance(value.get("percent"), (int, float))
                                and not isinstance(value["percent"], bool) else "")
        rows.append((str(value["id"]), short(value.get("title"), 100),
                     status, progress, str(count(value.get("points")))))
    unlocks = []
    events = state.get("unlocks") if isinstance(state.get("unlocks"), list) else []
    for value in events[:16]:
        if isinstance(value, dict):
            unlocks.append("{}  (+{} pts)".format(
                short(value.get("title"), 90), count(value.get("points"))))
    tracking = []
    for value in (game.get("tracking") if isinstance(game.get("tracking"), list) else [])[:20]:
        if isinstance(value, dict):
            tracking.append("{}: {}".format(short(value.get("title"), 95),
                                            short(value.get("value"), 60)))
    return {"account": account, "console": connection, "game": summary,
            "rows": rows, "unlocks": unlocks, "tracking": tracking, "playing": playing}


def fetch_engine_state():
    from launcher.achievements import account_url
    with urllib.request.urlopen(account_url() + "state", timeout=2) as response:
        body = response.read(1024 * 1024 + 1)
    if len(body) > 1024 * 1024:
        raise ValueError("Achievement state reply exceeds 1 MiB.")
    return json.loads(body)


class OverviewWindow(tk.Toplevel):
    """UI reads cached worker results, never performs a synchronous network call."""
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.title("PS2-Servers — Live RetroAchievements")
        self.geometry("930x680")
        self.minsize(630, 430)
        self._stop = threading.Event()
        self._queue = queue.Queue(maxsize=2)
        self._last_rows = {}
        self._all_rows = []
        self.search = tk.StringVar(value="")
        self.achievement_state = tk.StringVar(value="All")
        self.achievement_count = tk.StringVar(value="No achievements loaded")
        self.account = tk.StringVar(value="Loading local achievement status…")
        self.connection = tk.StringVar(value="")
        self.game = tk.StringVar(value="")
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._worker = threading.Thread(target=self._run, daemon=True,
                                        name="PS2-Servers RA overview")
        self._worker.start()
        self.after(200, self._drain)

    def _build(self):
        head = ttk.Frame(self, padding=12)
        head.pack(fill="x")
        for var in (self.account, self.connection, self.game):
            ttk.Label(head, textvariable=var, wraplength=840).pack(
                anchor="w", fill="x", pady=2)
        bar = ttk.Frame(self, padding=(12, 0, 12, 8))
        bar.pack(fill="x")
        ttk.Button(bar, text="Open full account and leaderboards",
                   command=self._open_account).pack(side="left")
        ttk.Label(bar, text="Read-only live view · no credentials exposed",
                  padding=(16, 0)).pack(side="left")
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        panel = ttk.Frame(notebook)
        notebook.add(panel, text="Achievements")
        panel.rowconfigure(1, weight=1)
        panel.columnconfigure(0, weight=1)
        filters = ttk.Frame(panel, padding=(0, 0, 0, 8))
        filters.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(filters, text="Find").pack(side="left", padx=(0, 6))
        ttk.Entry(filters, textvariable=self.search, width=28).pack(side="left")
        ttk.Combobox(filters, textvariable=self.achievement_state, width=15,
                     state="readonly", values=("All", "Not unlocked", "Unlocked")).pack(
                         side="left", padx=8)
        ttk.Label(filters, textvariable=self.achievement_count).pack(side="right")
        self.search.trace_add("write", self._filter_changed)
        self.achievement_state.trace_add("write", self._filter_changed)
        self.rows = ttk.Treeview(panel, columns=("title", "status", "progress", "points"),
                                 show="headings", selectmode="browse")
        for key, name, width in (("title", "Achievement", 370),
                                 ("status", "State", 110),
                                 ("progress", "Progress", 200),
                                 ("points", "Points", 75)):
            self.rows.heading(key, text=name)
            self.rows.column(key, width=width, stretch=(key != "points"))
        self.rows.grid(row=1, column=0, sticky="nsew")
        scroller = ttk.Scrollbar(panel, command=self.rows.yview)
        scroller.grid(row=1, column=1, sticky="ns")
        self.rows.configure(yscrollcommand=scroller.set)
        for title, attr in (("Recent engine unlock events", "unlocks"),
                            ("Live leaderboard trackers", "tracking")):
            tab = ttk.Frame(notebook, padding=10)
            notebook.add(tab, text=title.replace("Recent engine ", "").replace("Live ", ""))
            label = tk.Text(tab, wrap="word", state="disabled", height=8)
            label.pack(fill="both", expand=True)
            setattr(self, attr + "_view", label)

    def _run(self):
        while not self._stop.is_set():
            try:
                # This panel must never display the state of another client's
                # listener if our managed achievement service has stopped.
                state = fetch_engine_state() if self.app.is_running("retroachievements") else None
            except (OSError, ValueError, TypeError, urllib.error.URLError):
                state = None
            try:
                self._queue.put_nowait(state)
            except queue.Full:
                pass
            self._stop.wait(2)

    def _drain(self):
        if self._stop.is_set():
            return
        latest = None
        received = False
        try:
            while True:
                latest = self._queue.get_nowait()
                received = True
        except queue.Empty:
            pass
        if received:
            tracker = getattr(self.app, "_ra_session", None)
            verified = tracker.snapshot() if tracker is not None else None
            model = view_model(latest, verified)
            self.account.set(model["account"])
            self.connection.set(model["console"])
            self.game.set(model["game"])
            self._all_rows = model["rows"]
            self._filter_changed()
            for attr in ("unlocks", "tracking"):
                lines = model[attr] or ["Nothing to display."]
                widget = getattr(self, attr + "_view")
                body = "\n".join(lines)
                if widget.get("1.0", "end-1c") != body:
                    widget.configure(state="normal")
                    widget.delete("1.0", "end")
                    widget.insert("1.0", body)
                    widget.configure(state="disabled")
        self.after(200, self._drain)

    def _filter_changed(self, *_unused):
        all_rows = self._all_rows
        result = filter_achievements(all_rows, self.search.get(), self.achievement_state.get())
        unlocked = sum(row[2] == "Unlocked" for row in all_rows)
        self.achievement_count.set("{} shown / {} total · {} unlocked".format(
            len(result), len(all_rows), unlocked))
        self._render_rows(result)

    def _render_rows(self, rows):
        # Update only changed rows. Clearing/rebuilding hundreds every two
        # seconds causes avoidable UI flicker and selection loss.
        desired = {key: vals for key, *vals in rows}
        for key in set(self._last_rows) - set(desired):
            self.rows.delete(key)
        for key, vals in desired.items():
            if key not in self._last_rows:
                self.rows.insert("", "end", iid=key, values=vals)
            elif self._last_rows[key] != vals:
                self.rows.item(key, values=vals)
        self._last_rows = desired

    def _open_account(self):
        if not self.app.is_running("retroachievements"):
            messagebox.showinfo("RetroAchievements", "Start RetroAchievements first.",
                                parent=self)
            return
        from launcher.achievements import account_url
        webbrowser.open_new_tab(account_url())

    def close(self):
        self._stop.set()
        self.destroy()
