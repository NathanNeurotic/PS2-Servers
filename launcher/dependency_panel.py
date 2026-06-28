"""Small Tkinter panel for optional compression dependency status.

This module intentionally keeps native dependency setup conservative. Python lz4
can be installed in source mode. Packaged releases bundle Python dependencies
instead of requiring users to install Python. Native libchdr is detected and
explained, but release builds should bundle vetted native binaries instead of
downloading them at runtime.
"""

import threading

from . import optional_deps


LZ4_SOURCE_BUTTON = "Install ZSO/LZ4 support"
LZ4_PACKAGED_BUTTON = "ZSO/LZ4 bundled in release"


def add_panel(app, gui):
    frame = gui.ttk.Frame(app.root, style="Admin.TFrame")
    frame.pack(fill="x", padx=10, pady=(0, 4))

    gui.ttk.Label(frame, text="Compression support:", style="Admin.TLabel",
                  font=("", 9, "bold")).pack(side="left", padx=(8, 8), pady=6)
    app._ps2_compression_var = gui.tk.StringVar(value="Checking…")
    gui.ttk.Label(frame, textvariable=app._ps2_compression_var,
                  style="Admin.TLabel").pack(side="left", padx=(0, 8), pady=6)

    gui.ttk.Button(frame, text="Check",
                   command=lambda: show_status(app, gui)).pack(side="right", padx=(4, 8), pady=5)
    gui.ttk.Button(frame, text="CHD/libchdr details",
                   command=lambda: show_libchdr_details(gui)).pack(side="right", padx=(4, 0), pady=5)
    install_text = LZ4_PACKAGED_BUTTON if optional_deps.is_frozen_app() else LZ4_SOURCE_BUTTON
    install = gui.ttk.Button(frame, text=install_text,
                             command=lambda: install_lz4(app, gui))
    install.pack(side="right", padx=(4, 0), pady=5)
    if optional_deps.is_frozen_app():
        install.config(state="disabled")
    app._ps2_lz4_button = install
    app._ps2_compression_frame = frame
    refresh_status_async(app)


def _status_bits(statuses):
    bits = []
    for status in statuses:
        label = "ZSO/LZ4" if status.key == "lz4" else "CHD"
        bits.append("{} {}".format(label, "OK" if status.available else "missing"))
    return "; ".join(bits)


def refresh_status(app):
    statuses = optional_deps.check_all()
    app._ps2_compression_var.set(_status_bits(statuses))
    return statuses


def refresh_status_async(app):
    def worker():
        try:
            text = _status_bits(optional_deps.check_all())
        except Exception as e:
            text = "Compression check failed: {}".format(e)
        try:
            app.root.after(0, app._ps2_compression_var.set, text)
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()


def show_status(app, gui):
    statuses = refresh_status(app)
    gui.messagebox.showinfo("Optional compression support",
                            optional_deps.format_statuses(statuses))


def show_libchdr_details(gui):
    gui.messagebox.showinfo("CHD/libchdr support", optional_deps.libchdr_setup_text())


def install_lz4(app, gui):
    if optional_deps.is_frozen_app():
        gui.messagebox.showinfo(
            "Packaged app",
            "Packaged PS2 Servers releases do not need a user-installed Python just for ZSO/LZ4.\n\n"
            "The release build should bundle lz4. If ZSO/LZ4 still reports missing, that is a release packaging problem, not something the user should fix with pip."
        )
        return

    command = " ".join(optional_deps.lz4_install_command())
    if not gui.messagebox.askyesno(
            "Install ZSO/LZ4 support?",
            "Install the optional Python lz4 package now?\n\n"
            "Command:\n{}\n\n"
            "This affects the current Python environment only.".format(command)):
        return

    button = getattr(app, "_ps2_lz4_button", None)
    if button:
        button.config(state="disabled")
    app._append_log("setup", "[deps] installing optional lz4 package\n")

    def log_line(line):
        app.root.after(0, app._append_log, "setup", "[deps] {}\n".format(line))

    def worker():
        try:
            optional_deps.install_lz4(log=log_line)
        except Exception as e:
            app.root.after(0, finish_install, app, gui, False, str(e))
            return
        app.root.after(0, finish_install, app, gui, True, "lz4 installed.")

    threading.Thread(target=worker, daemon=True).start()


def finish_install(app, gui, success, detail):
    button = getattr(app, "_ps2_lz4_button", None)
    if button and not optional_deps.is_frozen_app():
        button.config(state="normal")
    refresh_status_async(app)
    if success:
        app._append_log("setup", "[deps] lz4 install finished\n")
        gui.messagebox.showinfo("ZSO/LZ4 support", detail)
    else:
        app._append_log("setup", "[deps] lz4 install failed: {}\n".format(detail))
        gui.messagebox.showerror("ZSO/LZ4 install failed", detail)
