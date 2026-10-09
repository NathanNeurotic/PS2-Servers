"""Native game-library window; long operations never run on the Tk thread."""
import queue
from pathlib import Path
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import urllib.parse

from launcher import config
from launcher.game_library import Compatibility, Library


class LibraryWindow(tk.Toplevel):
    def __init__(self, parent, folder=""):
        super().__init__(parent)
        self.title("PS2-Servers — Game library")
        self.geometry("960x650")
        self.minsize(720, 440)
        self.library = Library()
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.folder = tk.StringVar(value=folder or config.load().get("game_library_folder", ""))
        self.query = tk.StringVar()
        self.view = tk.StringVar(value="Installed images")
        self.kind = tk.StringVar(value="DVD")
        self.status = tk.StringVar(value="Choose the same OPL folder used by your game server.")
        self.rows = {}
        self.actions = []
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        header = ttk.Frame(self, padding=10)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="OPL games folder").grid(row=0, column=0, padx=4)
        ttk.Entry(header, textvariable=self.folder).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(header, text="Browse…", command=self.choose_folder).grid(row=0, column=2, padx=4)
        ttk.Label(header, text="Image destination").grid(row=1, column=0, padx=4, pady=6)
        ttk.Combobox(header, textvariable=self.kind, values=("DVD", "CD"), state="readonly", width=8).grid(row=1, column=1, sticky="w", padx=4)

        search = ttk.Frame(self, padding=(10, 0))
        search.grid(row=1, column=0, sticky="ew")
        search.columnconfigure(1, weight=1)
        choice = ttk.Combobox(search, textvariable=self.view,
                             values=("Installed images", "Recognized achievement sets", "Unmatched ISO hashes", "Catalogue"), state="readonly", width=26)
        choice.grid(row=0, column=0, padx=4)
        choice.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        ttk.Entry(search, textvariable=self.query).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(search, text="Search / refresh", command=self.refresh).grid(row=0, column=2, padx=4)

        tools = ttk.Frame(self, padding=10)
        tools.grid(row=2, column=0, sticky="ew")
        for column, (label, action) in enumerate((
                ("Add / edit title", self.edit_title), ("Import image…", self.import_image),
                ("Download image…", self.download_image), ("Check RA compatibility", self.scan))):
            button = ttk.Button(tools, text=label, command=action)
            button.grid(row=0, column=column, padx=3, pady=3)
            self.actions.append(button)

        button = ttk.Button(tools, text="Repair selected covers", command=self.repair_covers)
        button.grid(row=2, column=0, padx=3, pady=3)
        self.actions.append(button)
        button = ttk.Button(tools, text="Export console loader…", command=self.export_loader)
        button.grid(row=2, column=1, padx=3, pady=3)
        self.actions.append(button)
        for column, (label, action) in enumerate((
                ("Import catalogue…", self.import_catalog), ("Back up catalogue…", self.backup),
                ("Remove catalogue entry", self.remove_title), ("Setup guide", self.guide))):
            button = ttk.Button(tools, text=label, command=action)
            button.grid(row=1, column=column, padx=3, pady=3)
            self.actions.append(button)

        content = ttk.Frame(self, padding=(10, 0))
        content.grid(row=3, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(content, columns=("title", "details", "ra"), show="headings", selectmode="extended")
        for column, label, width in (("title", "Title / image", 380), ("details", "Details", 220), ("ra", "Achievements", 200)):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(content, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-1>", lambda _event: self.edit_title() if self.view.get() == "Catalogue" else None)
        self.tree.bind("<<TreeviewSelect>>", self.show_cover)

        footer = ttk.Frame(self, padding=10)
        footer.grid(row=4, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status, wraplength=760).grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="Cancel transfer", command=self.cancel.set).grid(row=0, column=1, padx=6)
        self.cover = ttk.Label(footer)
        self.cover.grid(row=1, column=0, sticky="w", pady=4)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        self.after(100, self.poll)

    def choose_folder(self):
        if self.busy:
            return
        folder = filedialog.askdirectory(parent=self, title="Existing OPL games folder")
        if folder:
            self.folder.set(folder)
            settings = config.load()
            settings["game_library_folder"] = folder
            config.save(settings)
            self.refresh()

    def game_folder(self):
        folder = Path(self.folder.get().strip())
        if not self.folder.get().strip() or not folder.is_dir():
            raise ValueError("Choose an existing OPL games folder first.")
        return folder

    def selected(self):
        return [self.rows[key] for key in self.tree.selection() if key in self.rows]

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        try:
            if self.view.get() == "Catalogue":
                rows = self.library.games(self.query.get(), limit=500)
                for row in rows:
                    key = "catalog-" + str(row["id"])
                    self.rows[key] = row
                    self.tree.insert("", "end", iid=key, values=(row["title"], row["game_id"], ""))
            elif self.folder.get().strip():
                with self.library.connect() as db:
                    states = {row["path"]: dict(row) for row in db.execute("SELECT * FROM image_status")}
                for row in self.library.installed(self.game_folder()):
                    if self.query.get().casefold() not in row["name"].casefold():
                        continue
                    key = "image-" + str(len(self.rows))
                    self.rows[key] = row
                    state = states.get(str(Path(row["path"]).resolve()), {})
                    if state.get("stamp") != row["stamp"]:
                        state = {}
                    if self.view.get() == "Recognized achievement sets" and state.get("status") != "compatible":
                        continue
                    if self.view.get() == "Unmatched ISO hashes" and state.get("status") != "unmatched":
                        continue
                    label = state.get("status", "Not checked")
                    if label == "compatible":
                        label = "{} achievements".format(state["count"])
                    self.tree.insert("", "end", iid=key, values=(row["name"], "{} · {:.1f} GB".format(row["kind"], row["size"] / 1e9), label))
        except (OSError, ValueError) as error:
            self.status.set(str(error))

    def run(self, operation):
        if self.busy:
            return
        self.busy = True
        self.cancel.clear()
        for button in self.actions:
            button.configure(state="disabled")
        self.status.set("Working…")

        def worker():
            try:
                self.events.put(("done", operation()))
            except Exception as error:
                # Network exception text can contain a private download URL.
                from urllib.error import URLError
                text = "Network request failed; check the connection and link." if isinstance(error, URLError) else str(error)
                self.events.put(("error", text))

        threading.Thread(target=worker, daemon=True).start()

    def progress(self, done, total):
        now = time.monotonic()
        if now - getattr(self, "last_progress", 0) > 0.2:
            self.last_progress = now
            self.events.put(("progress", "Transferred {:.1f} MB{}".format(done / 1e6, " of {:.1f} MB".format(total / 1e6) if total else "")))

    def poll(self):
        while True:
            try:
                kind, text = self.events.get_nowait()
            except queue.Empty:
                break
            self.status.set(str(text))
            if kind in ("done", "error"):
                self.busy = False
                for button in self.actions:
                    button.configure(state="normal")
                self.refresh()
                if kind == "error":
                    messagebox.showerror("Game library", text, parent=self)
        self.after(100, self.poll)

    def import_image(self):
        try:
            folder = self.game_folder()
        except ValueError as error:
            messagebox.showerror("Import image", str(error), parent=self)
            return
        source = filedialog.askopenfilename(parent=self, title="Import a game image", filetypes=[("PS2 images", "*.iso *.chd *.cso *.zso")])
        if source:
            kind = self.kind.get()
            self.run(lambda: "Installed " + self.library.install(source, folder, kind, cancel=self.cancel, progress=self.progress))

    def download_image(self):
        try:
            folder = self.game_folder()
        except ValueError as error:
            messagebox.showerror("Download image", str(error), parent=self)
            return
        selected = self.selected()
        default = selected[0].get("download_url", "") if selected else ""
        url = simpledialog.askstring("Download image", "Link to an image you are authorized to download:", initialvalue=default, parent=self)
        if not url:
            return
        name = simpledialog.askstring("Save image", "File name, including .iso, .chd, .cso or .zso:",
                                      initialvalue=Path(urllib.parse.unquote(urllib.parse.urlsplit(url).path)).name, parent=self)
        if name:
            kind = self.kind.get()
            self.run(lambda: "Installed " + self.library.download(url, folder, kind, name, self.cancel, self.progress))

    def import_catalog(self):
        source = filedialog.askopenfilename(parent=self, title="Import Caduceus catalogue", filetypes=[("Catalogue", "*.json *.sqlite *.sqlite3 *.db"), ("All files", "*")])
        if source:
            def operation():
                added, skipped, backup = self.library.import_catalog(source)
                return "Added {}; kept {} existing entries. Pre-import backup: {}".format(added, skipped, backup)
            self.run(operation)

    def backup(self):
        destination = filedialog.asksaveasfilename(parent=self, title="Back up catalogue", defaultextension=".sqlite3", filetypes=[("SQLite catalogue", "*.sqlite3")])
        if destination:
            self.run(lambda: "Catalogue backup saved: " + self.library.backup(destination))

    def edit_title(self):
        selected = self.selected()
        original = selected[0] if selected and "id" in selected[0] else {}
        editor = tk.Toplevel(self)
        editor.title("Edit catalogue title" if original else "Add catalogue title")
        editor.columnconfigure(1, weight=1)
        fields = {}
        for index, (key, label) in enumerate((("title", "Title"), ("game_id", "Game serial"), ("original_name", "Original image name"),
                                               ("icon", "Cover image URL"), ("download_url", "Download URL"))):
            fields[key] = tk.StringVar(value=original.get(key, ""))
            ttk.Label(editor, text=label).grid(row=index, column=0, padx=8, pady=5)
            ttk.Entry(editor, textvariable=fields[key], width=60).grid(row=index, column=1, padx=8, pady=5, sticky="ew")

        def save():
            try:
                row = dict(original, **{key: value.get() for key, value in fields.items()})
                self.library.save_game(row, original.get("id"))
                editor.destroy()
                self.view.set("Catalogue")
                self.refresh()
            except ValueError as error:
                messagebox.showerror("Catalogue entry", str(error), parent=editor)

        ttk.Button(editor, text="Save", command=save).grid(row=len(fields), column=1, sticky="e", padx=8, pady=8)

    def remove_title(self):
        selected = [row for row in self.selected() if "id" in row]
        if selected and messagebox.askyesno("Remove catalogue entries", "Remove {} catalogue entries? Images and saves are preserved.".format(len(selected)), parent=self):
            for row in selected:
                self.library.remove_game(row["id"])
            self.refresh()

    def repair_covers(self):
        rows = [row for row in self.selected() if "id" in row]
        if not rows:
            messagebox.showinfo("Cover artwork", "Select catalogue titles with cover URLs first.", parent=self)
            return
        folder = self.folder.get().strip()

        def operation():
            done = 0
            for row in rows:
                if self.cancel.is_set():
                    return "Cover repair cancelled."
                self.library.repair_cover(row, folder or None)
                done += 1
                self.events.put(("progress", "Repaired cover for " + row["title"]))
            return "Repaired {} covers; existing console artwork was preserved.".format(done)
        self.run(operation)

    def show_cover(self, _event=None):
        from PIL import Image, ImageTk
        rows = self.selected()
        self.cover.configure(image="", text="")
        self.cover_image = None
        if rows and "id" in rows[0]:
            path = self.library.cover_path(rows[0]["id"])
            if path.is_file():
                try:
                    with Image.open(path) as image:
                        image.thumbnail((100, 100))
                        self.cover_image = ImageTk.PhotoImage(image)
                    self.cover.configure(image=self.cover_image)
                except OSError:
                    self.cover.configure(text="Cover needs repair")

    def scan(self):
        try:
            images = [row for row in self.selected() if "path" in row] or self.library.installed(self.game_folder())
        except ValueError as error:
            messagebox.showerror("Compatibility", str(error), parent=self)
            return

        def operation():
            scanner = Compatibility(self.library)
            count = 0
            for index, row in enumerate(images):
                if self.cancel.is_set():
                    return "Compatibility scan cancelled."
                result = scanner.check(row["path"])
                count += result["status"] == "compatible"
                self.events.put(("progress", "Checked {}/{}: {}".format(index + 1, len(images), row["name"])))
            return "Checked {} images; {} have recognized achievement sets.".format(len(images), count)
        self.run(operation)

    def guide(self):
        messagebox.showinfo("Game and achievement setup",
            "1. Choose the OPL folder you also selected in your game-server tab.\n\n"
            "2. Import images into CD or DVD, or import a Caduceus JSON/SQLite catalogue. Catalogue backups contain metadata, not images or saves.\n\n"
            "3. Start your game server and RetroAchievements. Sign in and enter your Web API key on the account page.\n\n"
            "4. Use the matching achievements-enabled OPL on the PS2. Test the PC connection and check game support before launch.\n\n"
            "5. Optional LAN viewing is read-only. Set an OBS export folder for stream labels.\n\n"
            "Virtual exFAT keeps a frozen inventory: restart it after adding images or artwork. Live achievements remain experimental and softcore-only.", parent=self)

    def export_loader(self):
        from launcher.achievement_loader import export_loader
        mode = simpledialog.askstring("Console loader", "Choose the console loader: xerabora or caduceus", initialvalue="xerabora", parent=self)
        if not mode:
            return
        mode = mode.strip().lower()
        if mode not in ("xerabora", "caduceus"):
            messagebox.showerror("Console loader", "Choose xerabora or caduceus.", parent=self)
            return
        folder = filedialog.askdirectory(parent=self, title="Export official console loader, license and source reference")
        if folder:
            self.run(lambda: "Verified upstream loader exported: " + export_loader(mode, folder))

    def close(self):
        if self.busy:
            self.cancel.set()
            self.status.set("Cancelling; wait for the current operation before closing.")
            return
        self.destroy()
