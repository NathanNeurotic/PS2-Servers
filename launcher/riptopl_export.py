"""A window that shows a server card's settings as RiptOPL settings.

It only reads the cards. Copy puts the text on the clipboard; Save writes one
text file, wherever the user picks, and never a RiptOPL config file itself.
"""
import os
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import riptopl_profile

CARD_ORDER = (("smbv1", "SMBv1"), ("udpfs", "UDPFS"), ("http", "HTTP"), ("udpbd", "UDPBD"),
              ("smbv2", "SMBv2"), ("smbv3", "SMBv3"))


def profile_text(server_key, values, pc_ip):
    """The text for one card, or a plain explanation when it cannot be exported."""
    try:
        return riptopl_profile.render(riptopl_profile.build(server_key, values, pc_ip))
    except ValueError as exc:
        return "Not exported: {}\n".format(exc)


def write_text(path, text):
    """Write atomically, so a failed save never leaves half a file."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".riptopl-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)  # only the file this call created
        except OSError:
            pass
        raise


class RiptoplExport(tk.Toplevel):
    """card_values maps a server key to that card's current values."""

    def __init__(self, parent, card_values, pc_ip):
        super().__init__(parent)
        self.title("RiptOPL settings")
        self.transient(parent.winfo_toplevel())
        self.minsize(520, 380)
        self.card_values = dict(card_values)
        self.pc_ip = pc_ip
        keys = [k for k, _label in CARD_ORDER if k in self.card_values]
        self.labels = dict(CARD_ORDER)
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        top = ttk.Frame(frame)
        top.pack(fill="x")
        ttk.Label(top, text="Server card:").pack(side="left")
        self.choice = tk.StringVar(value=self.labels[keys[0]] if keys else "")
        self.picker = ttk.Combobox(top, state="readonly", textvariable=self.choice,
                                   values=[self.labels[k] for k in keys], width=10)
        self.picker.pack(side="left", padx=(6, 0))
        self.picker.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        self.text = tk.Text(frame, height=22, wrap="word")
        self.text.pack(fill="both", expand=True, pady=(8, 8))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Save as…", command=self.save_as).pack(side="right", padx=(0, 8))
        ttk.Button(buttons, text="Copy", command=self.copy).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda _e: self.destroy())
        self.refresh()

    def update_source(self, card_values, pc_ip):
        """Take the cards' current values, so Copy and Save never use stale ones."""
        self.card_values = dict(card_values)
        self.pc_ip = pc_ip
        self.refresh()

    def selected_key(self):
        wanted = self.choice.get()
        return next((k for k, label in CARD_ORDER if label == wanted), "")

    def current_text(self):
        key = self.selected_key()
        if not key:
            return "No server card to export.\n"
        return profile_text(key, self.card_values.get(key, {}), self.pc_ip)

    def refresh(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", self.current_text())
        self.text.configure(state="disabled")

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.current_text())

    def save_as(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="Save RiptOPL settings", defaultextension=".txt",
            initialfile="riptopl-{}-settings.txt".format(self.selected_key() or "server"),
            filetypes=[("Text", "*.txt")])
        if not path:
            return
        try:
            write_text(path, self.current_text())
        except OSError as exc:
            messagebox.showerror("Not saved", str(exc), parent=self)
