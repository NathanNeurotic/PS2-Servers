"""Non-destructive first-run PS2 network and library setup guide.

All steps are read-only: the guide never modifies NICs, server ports, disks,
firewall rules, user credentials or PS2 settings.
"""
import tkinter as tk
from tkinter import ttk

STEPS = (
    ("Choose your server",
     "Select the server protocol supported by your console loader.\n\n"
     "SMBv1: use a compatible OPL/RiptOPL SMB client.\n"
     "UDPFS: use RiptOPL, Neutrino or another UDPFS-compatible loader.\n"
     "UDPBD: use a supported block-device client.\n"
     "HTTP: requires a compatible HTTP game-stream client.\n\n"
     "The server cannot add a protocol to the console loader."),
    ("Connect the hardware",
     "Connect the console and server to the same network, preferably by cable.\n\n"
     "A fat PS2 needs a network adapter with Ethernet.\n"
     "For a direct PS2-to-PC connection, use the DIRECT setup in PS2-Servers; "
     "a normal router LAN does not require the direct-link helper."),
    ("Prepare the game directory",
     "Choose an existing, accessible folder containing your game images.\n\n"
     "For standard OPL-compatible shares use DVD/ and CD/ subdirectories "
     "with compatible images. Check Access reports host filesystem access, "
     "not guaranteed console launch success.\n\n"
     "Do not move or delete a game directory while a console is playing."),
    ("Configure the console",
     "Start the desired server card and copy its displayed connection values "
     "into the matching PS2 loader settings.\n\n"
     "SMB: enter the PC LAN IP, exact port and share name. On a default "
     "Desktop SMBv1 card these are typically TCP 1025, share games, "
     "user guest and an empty password.\n\n"
     "UDPFS: use the UDPFS device in a supported console loader. "
     "UDPBD: use the UDPBD device, not a DVD/CD directory listing.\n\n"
     "Always use the actual values displayed in PS2-Servers."),
    ("Test without changing storage",
     "For your first test, begin with one known-good, uncompressed PS2 ISO.\n\n"
     "Confirm the game list appears, launch once, and observe server logs. "
     "If UDPFS lists games but launch fails, set its data bind address "
     "to the server's PS2-facing LAN IP and restart that server.\n\n"
     "No successful connection check proves VMC writes or every game works."),
    ("Optional RetroAchievements",
     "RetroAchievements is a separate optional PC engine and a compatible "
     "console-loader build, not a feature of SMB/UDPFS itself.\n\n"
     "Use the RetroAchievements card to open the account view, then select "
     "a matching supported loader/client. Keep account credentials on "
     "the PC. Achievements during SMB game streaming remain a known "
     "compatibility risk. Test with local storage first."),
)


class SetupGuide(tk.Toplevel):
    """A single-instance-friendly modal with explicit Previous/Next controls."""

    def __init__(self, parent, ip="", on_close=None):
        super().__init__(parent)
        self._on_close = on_close
        self.title("PS2 connection setup guide")
        self.transient(parent.winfo_toplevel())
        self.resizable(True, True)
        self.minsize(480, 340)
        self.position = 0
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)
        self.progress = ttk.Label(frame)
        self.progress.pack(anchor="w")
        self.heading = ttk.Label(frame, font=("", 13, "bold"))
        self.heading.pack(anchor="w", pady=(12, 10))
        self.details = tk.Text(frame, height=13, wrap="word", relief="flat",
                               borderwidth=0, highlightthickness=0)
        self.details.pack(fill="both", expand=True)
        self.details.configure(state="disabled")
        safe_ip = str(ip).strip() or "Select the PC LAN IP in SETUP"
        self.ip_hint = ttk.Label(frame, text="PC LAN IP selected: " + safe_ip)
        self.ip_hint.pack(anchor="w", pady=(8, 8))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        self.prev = ttk.Button(buttons, text="Previous", command=self.previous)
        self.prev.pack(side="left")
        self.next = ttk.Button(buttons, text="Next", command=self.advance)
        self.next.pack(side="right")
        ttk.Button(buttons, text="Close", command=self.close).pack(
            side="right", padx=(0, 8))
        self.bind("<Escape>", lambda _event: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.show_step()

    def show_step(self):
        title, body = STEPS[self.position]
        self.progress.configure(text=f"Step {self.position + 1} of {len(STEPS)}")
        self.heading.configure(text=title)
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", body)
        self.details.configure(state="disabled")
        self.prev.configure(state="normal" if self.position else "disabled")
        self.next.configure(text="Finish" if self.position == len(STEPS) - 1 else "Next")

    def previous(self):
        if self.position:
            self.position -= 1
            self.show_step()

    def close(self, completed=False):
        callback = self._on_close
        self._on_close = None  # A close can only report completion once.
        self.destroy()
        if callback is not None:
            callback(bool(completed))

    def advance(self):
        if self.position >= len(STEPS) - 1:
            self.close(completed=True)
        else:
            self.position += 1
            self.show_step()
