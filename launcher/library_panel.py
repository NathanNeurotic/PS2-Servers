"""Library tab for PS2-Servers Desktop. Optional metadata only, never a server backend."""
from __future__ import annotations

from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from . import library_catalog
from .library_index import IndexSnapshot
from .config import config_dir


GUIDE = (
    ("1. Connect hardware", "Attach the PS2 Ethernet adapter/port to the same LAN as this computer. "
     "Fat PS2 models need an Ethernet-capable network adapter. Prefer wired Ethernet."),
    ("2. Start a matching server", "For RiptOPL choose SMBv1 or another loader-supported protocol. "
     "SMBv1 Desktop defaults to the 'games' share, TCP 1025. UDPFS uses UDP 62966. "
     "A server mode does not add client support to a PS2 loader."),
    ("3. Enter the PC address on PS2", "In the loader's network configuration enter the LAN IP "
     "shown by PS2-Servers. Use the exact share, port and credentials reported on its server card."),
    ("4. Prepare the game layout", "Choose a games root containing DVD/ and CD/ folders. "
     "Library can scan the folder, import your own ISO images and prepare ART/ PNG covers. "
     "Do not import while a title is reading from the same root."),
    ("5. Check the connection", "Start a server, refresh the console's game list and launch a known-good "
     "uncompressed ISO first. Set up RetroAchievements separately if using an RA-enabled ELF."),
)


class LibraryPanel(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self._results = queue.Queue()
        self._busy = False
        self._scan_active = False
        self._scan_cancel = threading.Event()
        self._scan_progress = queue.Queue(maxsize=1)
        self._display_rows = {}
        self._buttons = []
        self.root_file = Path(config_dir()) / "library" / "games_root.txt"
        self.guide_marker = Path(config_dir()) / "library" / "first_run_complete"
        self.path = tk.StringVar(value=self._load_root())
        self.search = tk.StringVar()
        self.recursive = tk.BooleanVar(value=False)
        self.scan_depth = tk.IntVar(value=4)
        self.status = tk.StringVar(value="Catalogue stores metadata; no ISO or save is modified by scanning.")
        self._build()
        self.bind("<Destroy>", lambda _event: self._scan_cancel.set(), add="+")
        self._drain()
        self._run(lambda db: db.list_games(), "Loading catalogue")
        # First-run tutorial is voluntary and never configures network or starts a server.
        # The integrated desktop offers one shared guide; standalone panels
        # retain their own first-run guide.
        if not hasattr(app, "_open_ps2_setup_guide"):
            self.after_idle(self._show_first_run_guide)

    def _load_root(self):
        try:
            return self.root_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _button(self, parent, title, command):
        button = ttk.Button(parent, text=title, command=command)
        button.pack(side="left", padx=(0, 6), pady=4)
        self._buttons.append(button)
        return button

    def _build(self):
        info = ttk.Label(self, text="GAME LIBRARY · OPTIONAL COMPANION", font=("", 12, "bold"))
        info.pack(anchor="w", padx=12, pady=(10, 3))
        ttk.Label(self, text="Keep serving games through the existing SMB / UDPFS / UDPBD / HTTP cards. "
                  "This local catalogue does not change server settings or start a transfer.",
                  wraplength=740).pack(fill="x", padx=12, pady=(0, 10))
        folder = ttk.Frame(self)
        folder.pack(fill="x", padx=12)
        ttk.Label(folder, text="Games root").pack(side="left")
        ttk.Entry(folder, textvariable=self.path).pack(side="left", fill="x", expand=True, padx=8)
        self._button(folder, "Browse", self._browse_root)
        controls = ttk.Frame(self)
        controls.pack(fill="x", padx=12, pady=5)
        self._button(controls, "Scan CD/DVD", self._scan)
        self._button(controls, "Import images", self._import_image)
        self._button(controls, "Import cover", self._import_art)
        self._button(controls, "Repair ART", self._repair_art)
        self._button(controls, "Edit metadata", self._edit)
        self._button(controls, "Setup guide", self._guide)
        scan_options = ttk.Frame(self)
        scan_options.pack(fill="x", padx=12, pady=(0, 4))
        recursive = ttk.Checkbutton(scan_options, text="Include root and subfolders",
                                    variable=self.recursive)
        recursive.pack(side="left")
        self._buttons.append(recursive)
        ttk.Label(scan_options, text="Maximum depth").pack(side="left", padx=(12, 4))
        depth = ttk.Spinbox(scan_options, from_=0, to=library_catalog.MAX_SCAN_DEPTH,
                            textvariable=self.scan_depth, width=3)
        depth.pack(side="left")
        self._buttons.append(depth)
        self.cancel_scan = ttk.Button(scan_options, text="Cancel scan", command=self._scan_cancel.set)
        self.cancel_scan.pack(side="left", padx=8)
        self.cancel_scan.state(["disabled"])
        self._button(scan_options, "Inspect disc IDs", self._inspect_ids)
        ttk.Label(self, text="Recursive scan indexes metadata only (10,000 entries per run). "
                  "Nested paths may not launch on your console client. Media outside CD/DVD is unknown. "
                  "Inspect disc IDs reads images separately; double-click a row or press Enter for results.",
                  wraplength=740).pack(fill="x", padx=12)
        searchrow = ttk.Frame(self)
        searchrow.pack(fill="x", padx=12, pady=5)
        ttk.Label(searchrow, text="Search").pack(side="left")
        ttk.Entry(searchrow, textvariable=self.search).pack(side="left", fill="x", expand=True, padx=8)
        self._button(searchrow, "Find", lambda: self._run(
            lambda db: db.list_games(self.search.get()), "Searching catalogue"))
        cols = ("title", "disc_id", "detected_id", "identity_status", "media", "bytes", "file")
        table = ttk.Frame(self)
        table.pack(fill="both", expand=True, padx=12, pady=(4, 5))
        self.tree = ttk.Treeview(table, columns=cols, show="headings", height=10, selectmode="browse")
        widths = {"title": 180, "disc_id": 100, "detected_id": 100, "identity_status": 90,
                  "media": 65, "bytes": 80, "file": 250}
        for col in cols:
            label = {"disc_id": "Manual ID", "detected_id": "Inspected ID", "identity_status": "ID status"}.get(
                col, col.replace("_", " ").title())
            self.tree.heading(col, text=label)
            self.tree.column(col, width=widths[col], stretch=col in ("title", "file"))
        scroll = ttk.Scrollbar(table, command=self.tree.yview)
        horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.configure(xscrollcommand=horizontal.set)
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", self._show_identity)
        self.tree.bind("<Return>", self._show_identity)
        scroll.pack(side="right", fill="y")
        horizontal.pack(fill="x", padx=12)
        exports = ttk.Frame(self)
        exports.pack(fill="x", padx=12, pady=5)
        self._button(exports, "Backup SQLite", self._backup)
        self._button(exports, "Export JSON", self._export)
        self._button(exports, "Import JSON", self._restore)
        self._button(exports, "Review indexed paths", self._review_index)
        self.progress = ttk.Progressbar(exports, mode="indeterminate", length=105)
        self.progress.pack(side="right", padx=4)
        ttk.Label(self, textvariable=self.status, wraplength=740).pack(
            anchor="w", padx=12, pady=(3, 10))

    def _browse_root(self):
        root = filedialog.askdirectory(title="Choose games root")
        if root:
            self.path.set(root)
            self.root_file.parent.mkdir(parents=True, exist_ok=True)
            self.root_file.write_text(root, encoding="utf-8")
            self._scan()

    def _games_root(self):
        root = library_catalog.require_directory(self.path.get().strip())
        self.root_file.parent.mkdir(parents=True, exist_ok=True)
        self.root_file.write_text(str(root), encoding="utf-8")
        return root

    def _run(self, action, message, refresh=False):
        if self._busy:
            return
        self._busy = True
        self.status.set(message + "…")
        self.progress.start(12)
        for btn in self._buttons:
            btn.state(["disabled"])

        search_term = self.search.get()
        def work():
            try:
                with library_catalog.Catalog() as db:
                    value = action(db)
                    rows = db.list_games(search_term) if refresh else (
                        value if isinstance(value, list) else None)
                self._results.put((True, value, rows))
            except Exception as exc:
                self._results.put((False, str(exc), None))
        threading.Thread(target=work, daemon=True, name="ps2-library-operation").start()

    def _drain(self):
        try:
            progress = self._scan_progress.get_nowait()
            if self._scan_active:
                if isinstance(progress, library_catalog.IdentityScanResult):
                    self.status.set(f"Inspecting IDs: {progress.visited} entries, {progress.cached} cached, "
                                    f"{progress.conflicts} conflicts, {progress.errors} errors.")
                else:
                    self.status.set(f"Scanning: {progress.visited} inspected, {progress.indexed} indexed; "
                                    f"{progress.errors} errors. Cancel keeps indexed metadata.")
        except queue.Empty:
            pass
        try:
            while True:
                ok, value, rows = self._results.get_nowait()
                self._busy = False
                self._scan_active = False
                self.cancel_scan.state(["disabled"])
                self.progress.stop()
                for btn in self._buttons:
                    btn.state(["!disabled"])
                if ok:
                    if isinstance(value, IndexSnapshot):
                        window = tk.Toplevel(self)
                        window.title("Indexed paths and duplicates")
                        window.geometry("800x480")
                        output = ScrolledText(window, wrap="word")
                        output.pack(fill="both", expand=True, padx=10, pady=10)
                        output.insert("1.0", value.report())
                        output.configure(state="disabled")
                    if rows is not None:
                        self.tree.delete(*self.tree.get_children())
                        self._display_rows = {str(r["id"]): r for r in rows[:2000]}
                        for record in rows[:2000]:
                            self.tree.insert("", "end", iid=str(record["id"]),
                                             values=(record["title"], record["disc_id"],
                                                     record["detected_disc_id"], record["identity_status"],
                                                     record["media"], "{:.1f} MiB".format(record["image_bytes"] / 1048576),
                                                     record["image_path"]))
                    self.status.set("{} · {} visible entries (max 2000 displayed).".format(
                        value if not isinstance(value, list) else "Catalogue loaded",
                        len(self.tree.get_children())))
                else:
                    self.status.set("Operation failed: {}".format(value))
                    messagebox.showerror("PS2 game library", str(value))
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(125, self._drain)

    def _scan(self):
        if self._busy:
            return
        try:
            root = self._games_root()
            recursive = self.recursive.get()
            depth = self.scan_depth.get()
            if not 0 <= depth <= library_catalog.MAX_SCAN_DEPTH:
                raise ValueError(f"Maximum depth must be 0 through {library_catalog.MAX_SCAN_DEPTH}.")
        except (OSError, ValueError, tk.TclError) as exc:
            messagebox.showerror("Games root", str(exc))
            return
        self._scan_cancel.clear()
        self._scan_active = True

        self._run(lambda db: db.scan_detailed(
            root, recursive=recursive, max_depth=depth,
            cancel=self._scan_cancel, progress=self._queue_scan_progress),
            "Scanning root and subfolders" if recursive else "Scanning top-level DVD and CD folders", True)
        self.cancel_scan.state(["!disabled"])

    def _queue_scan_progress(self, value):
        try:
            self._scan_progress.get_nowait()
        except queue.Empty:
            pass
        self._scan_progress.put_nowait(value)

    def _inspect_ids(self):
        if self._busy:
            return
        try:
            root = self._games_root()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Games root", str(exc))
            return
        self._scan_cancel.clear()
        self._scan_active = True
        self._run(lambda db: db.inspect_identities(
            root, cancel=self._scan_cancel, progress=self._queue_scan_progress),
            "Inspecting disc IDs (up to 1,000 indexed entries)", True)
        self.cancel_scan.state(["!disabled"])

    def _show_identity(self, _event=None):
        selected = self.tree.selection()
        row = self._display_rows.get(selected[0]) if selected else None
        if row:
            messagebox.showinfo("Disc ID inspection", "{}\n\nStatus: {}\nManual ID: {}\n"
                                "Inspected ID: {}\nBOOT2: {}\n\n{}".format(
                                    row["image_path"], row["identity_status"], row["disc_id"] or "(empty)",
                                    row["detected_disc_id"] or "(unknown)", row["boot_path"] or "(unknown)",
                                    row["identity_detail"] or "Use Inspect disc IDs after scanning the library."))

    def _review_index(self):
        if self._busy:
            return
        try:
            root = self._games_root()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Games root", str(exc))
            return
        self._scan_cancel.clear()
        self._scan_active = True
        self._run(lambda db: db.indexed_games(root, cancel=self._scan_cancel),
                  "Reviewing shared indexed paths", False)
        self.cancel_scan.state(["!disabled"])

    def _import_image(self):
        try:
            root = self._games_root()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Games root", str(exc))
            return
        sources = filedialog.askopenfilenames(
            title="Select one or more local ISO/CSO/ZSO/CHD images",
            filetypes=[("PS2 disc images", "*.iso *.cso *.zso *.chd"), ("All files", "*.*")])
        if not sources:
            return
        media = simpledialog.askstring("Game type", "DVD or CD?", initialvalue="DVD", parent=self)
        if media is None:
            return
        media = media.upper().strip()
        if media not in library_catalog.MEDIA:
            messagebox.showerror("Game type", "Choose DVD or CD.")
            return
        if len(sources) > 1000:
            messagebox.showerror("Import images", "Select at most 1000 image files per batch.")
            return
        if not messagebox.askokcancel("Copy images",
                "Copy {} selected image(s) to {}/{}?\n\n"
                "No existing file will be overwritten. Stop active game reads "
                "from this directory until the import is complete.".format(len(sources), root, media)):
            return
        def import_selected(db):
            result = db.import_images(sources, root, media)
            return "{} imported, {} skipped.{}".format(
                result["imported"], result["skipped"],
                " First error: " + result["errors"][0][1] if result["errors"] else "")
        self._run(import_selected, "Importing selected image batch", True)

    def _repair_art(self):
        try:
            root = self._games_root()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Games root", str(exc))
            return
        if not messagebox.askokcancel("Repair legacy artwork",
                "Convert supported JPG/WEBP/BMP files in {}/ART to indexed PNG?\n\n"
                "Original images and existing PNGs are never replaced or removed."
                .format(root)):
            return
        self._run(lambda db: "Artwork: {}".format(db.migrate_legacy_art(root)),
                  "Converting legacy artwork", False)

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Game library", "Select a game first.")
            return None
        return int(sel[0])

    def _edit(self):
        selected = self._selected()
        if selected is None:
            return
        values = self.tree.item(str(selected), "values")
        title = simpledialog.askstring("Game title", "Title", initialvalue=values[0], parent=self)
        if title is None:
            return
        serial = simpledialog.askstring("Disc ID", "PS2 Game ID (optional)",
                                        initialvalue=values[1], parent=self)
        if serial is None:
            return
        self._run(lambda db: db.edit(selected, title, serial), "Saving metadata", True)

    def _import_art(self):
        try:
            root = self._games_root()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Games root", str(exc))
            return
        source = filedialog.askopenfilename(title="Choose artwork image",
                    filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.webp"), ("All files", "*.*")])
        if not source:
            return
        selected = self.tree.selection()
        row = self._display_rows.get(selected[0], {}) if selected else {}
        if selected:
            # The table can predate a change to the image; only suggest an
            # inspected ID that still matches it.
            try:
                with library_catalog.Catalog() as db:
                    row = db.get_game(int(selected[0])) or {}
            except Exception:
                row = dict(row, detected_disc_id="")
        identifier = row.get("disc_id", "") or (
            row.get("detected_disc_id", "") if row.get("identity_status") == "verified" else "")
        key = simpledialog.askstring("Artwork identity",
             "Enter exact identity: PS2 disc ID, PS1 VCD filename or ELF filename.\n"
             "No automatic renaming of your games is performed.",
             initialvalue=identifier, parent=self)
        if not key:
            return
        art_type = simpledialog.askstring("Artwork type", "COV / ICO / LAB / COV3",
                                          initialvalue="COV", parent=self)
        if art_type is None:
            return
        self._run(lambda db: str(db.write_art(source, root, key.strip(), art_type.strip().upper())),
                  "Converting to 8-bit PNG", False)

    def _backup(self):
        path = filedialog.asksaveasfilename(title="New SQLite backup", defaultextension=".sqlite3",
                        filetypes=[("SQLite database", "*.sqlite3")])
        if path:
            self._run(lambda db: str(db.backup_sqlite(path)), "Saving SQLite snapshot")

    def _export(self):
        path = filedialog.asksaveasfilename(title="New JSON metadata backup", defaultextension=".json",
                        filetypes=[("JSON", "*.json")])
        if path:
            self._run(lambda db: str(db.export_json(path)), "Exporting catalogue metadata")

    def _restore(self):
        path = filedialog.askopenfilename(title="Import catalogue metadata",
                        filetypes=[("JSON", "*.json")])
        if path and messagebox.askokcancel("Import metadata",
                "Import/merge metadata entries? No ISO, cover or save will be copied or deleted. "
                "Existing catalogue entries with matching image paths will have metadata updated."):
            self._run(lambda db: db.import_json(path), "Importing catalogue metadata", True)

    def _show_first_run_guide(self):
        if not self.guide_marker.exists() and self.winfo_exists():
            self._guide()

    def _guide(self):
        window = tk.Toplevel(self)
        window.title("PS2-Servers: Getting started")
        window.transient(self.winfo_toplevel())
        window.geometry("560x290")
        index = [0]
        title = ttk.Label(window, font=("", 13, "bold"))
        title.pack(anchor="w", padx=20, pady=(18, 8))
        detail = ttk.Label(window, wraplength=490, justify="left")
        detail.pack(fill="both", expand=True, padx=20, pady=8)
        controls = ttk.Frame(window)
        controls.pack(fill="x", padx=20, pady=(0, 16))
        back = ttk.Button(controls, text="Previous")
        back.pack(side="left")
        next_button = ttk.Button(controls, text="Next")
        next_button.pack(side="right")

        def render():
            name, body = GUIDE[index[0]]
            title.config(text=name + " ({}/{})".format(index[0]+1, len(GUIDE)))
            address = self.app.ip_var.get() if index[0] == 2 else ""
            detail.config(text=body + ("\n\nCurrent LAN address hint: " + address if address else ""))
            back.state(["disabled"] if index[0] == 0 else ["!disabled"])
            next_button.config(text="Finish" if index[0] == len(GUIDE)-1 else "Next")

        def prev():
            index[0] = max(0, index[0]-1)
            render()

        def nxt():
            if index[0] == len(GUIDE)-1:
                try:
                    self.guide_marker.parent.mkdir(parents=True, exist_ok=True)
                    self.guide_marker.write_text("complete\n", encoding="utf-8")
                except OSError:
                    pass  # Read-only settings should not break the launcher.
                window.destroy()
            else:
                index[0] += 1
                render()
        back.config(command=prev)
        next_button.config(command=nxt)
        render()
