"""The SESSIONS tab: one table of every ``SESSION`` line any server printed.

Each server that has Session summary on (an advanced setting on its card)
prints one ``SESSION`` line per console when the console goes quiet for a
minute or the server stops. Those lines go to the TERMINAL tab with everything
else, where they are easy to miss among the start-up chatter. This tab picks
them out of the same stream and shows them as rows, newest first, so a
question like "did the console actually read anything" is answered by a glance
rather than a scroll.

The parsing and bounding live in :class:`SessionLog`, which has no Tk in it and
is what the tests exercise. :class:`SessionPanel` is the thin widget on top.

Nothing here runs in a server. A server that never prints a ``SESSION`` line
costs this tab nothing but one failed prefix check per log line.
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

from . import session_stats

MAX_ROWS = 200

# Column key, heading, width in pixels. The sum stays well under the widest
# existing tab so the table never widens the window; see test_gui_layout.
COLUMNS = (
    ("when", "When", 70),
    ("service", "Service", 60),
    ("client", "Console", 110),
    ("seconds", "Seconds", 65),
    ("connections", "Conn", 50),
    ("list", "List", 50),
    ("open", "Open", 50),
    ("read", "Read", 60),
    ("bytes", "Bytes", 80),
    ("write", "Write", 55),
    ("close", "Close", 50),
    ("errors", "Errors", 55),
    ("ended", "Ended", 65),
)

EMPTY_HINT = ("No session summaries yet. Turn on \"Session summary\" in a server's "
              "advanced settings; each console then gets one row here when it goes "
              "quiet for a minute or the server stops.")


def format_bytes(count):
    """41943040 -> '40.0 MB'. Whole numbers below a kilobyte stay as they are."""
    try:
        value = float(count)
    except (TypeError, ValueError):
        return str(count)
    if value < 1024:
        return "{:d}".format(int(value))
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024.0
        if value < 1024 or unit == "TB":
            return "{:.1f} {}".format(value, unit)
    return str(count)


class SessionLog:
    """The rows behind the table: parsed SESSION lines, newest first, bounded."""

    def __init__(self, max_rows=MAX_ROWS, clock=time.time):
        if max_rows < 1:
            raise ValueError("max_rows must be at least 1")
        self.max_rows = max_rows
        self._clock = clock
        self._rows = []

    def add(self, source, text):
        """Feed a chunk of one server's output. Returns the rows it produced.

        source is the launcher's key for the server (``udpfs``, ``smbv3`` ...),
        kept so a row still says where it came from if a service name is ever
        shared. text may hold several lines; lines that are not SESSION lines
        are ignored.
        """
        added = []
        for line in str(text).splitlines():
            fields = session_stats.parse_line(line)
            if fields is None:
                continue
            row = dict(fields)
            row["source"] = source
            row["at"] = self._clock()
            added.append(row)
        if added:
            self._rows[:0] = reversed(added)  # newest first, in arrival order
            del self._rows[self.max_rows:]
        return added

    def rows(self):
        return list(self._rows)

    def clear(self):
        self._rows = []

    def __len__(self):
        return len(self._rows)


def row_values(row):
    """The cells for one row, in COLUMNS order, as strings."""
    out = []
    for key, _heading, _width in COLUMNS:
        if key == "when":
            out.append(time.strftime("%H:%M:%S", time.localtime(row.get("at", 0))))
        elif key == "bytes":
            out.append(format_bytes(row.get("bytes", 0)))
        else:
            out.append(str(row.get(key, "")))
    return out


class SessionPanel(ttk.Frame):
    """The SESSIONS tab. ``feed(key, text)`` takes the same chunks the terminal gets."""

    def __init__(self, master, log=None):
        super().__init__(master)
        self.log = log if log is not None else SessionLog()
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        head = ttk.Frame(self)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 4))
        head.columnconfigure(0, weight=1)
        self.hint = tk.StringVar(value=EMPTY_HINT)
        # wraplength keeps the hint from being the widest thing on the page.
        ttk.Label(head, textvariable=self.hint, wraplength=700, justify="left").grid(
            row=0, column=0, sticky="w")
        ttk.Button(head, text="Clear", command=self.clear).grid(row=0, column=1, sticky="e")

        cols = tuple(key for key, _h, _w in COLUMNS)
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=8,
                                 selectmode="browse")
        for key, heading, width in COLUMNS:
            self.tree.heading(key, text=heading)
            self.tree.column(key, width=width, minwidth=width,
                             stretch=(key == "client"),
                             anchor="e" if key not in ("when", "service", "client", "ended") else "w")
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=(0, 8))
        scroll.grid(row=1, column=1, sticky="ns", padx=(0, 8), pady=(0, 8))

    def feed(self, source, text):
        """Called from the GUI thread with each chunk of a server's output."""
        added = self.log.add(source, text)
        if not added:
            return
        for row in added:
            self.tree.insert("", 0, values=row_values(row))
        children = self.tree.get_children()
        if len(children) > self.log.max_rows:
            self.tree.delete(*children[self.log.max_rows:])
        self._refresh_hint()

    def clear(self):
        self.log.clear()
        self.tree.delete(*self.tree.get_children())
        self._refresh_hint()

    def _refresh_hint(self):
        count = len(self.log)
        if count == 0:
            self.hint.set(EMPTY_HINT)
        else:
            self.hint.set("{} session{}, newest first. Each row is one console's totals "
                          "for one visit. What counts as an error varies by service: "
                          "HTTP counts every 404, SMB and UDPFS do not count a probe for "
                          "an optional file.".format(count, "" if count == 1 else "s"))
