"""Subprocess supervisor.

Each server runs as its own child process; this wraps one with start/stop and
captures its merged stdout/stderr into a rolling buffer (and an optional live
callback) so the GUI can show logs without a console window.
"""

import os
import platform
import subprocess
import threading
from collections import deque


class ServerProcess:
    def __init__(self, key, command, cwd=None, on_output=None, max_lines=2000):
        self.key = key
        self.command = command
        self.cwd = cwd
        self.on_output = on_output
        self.lines = deque(maxlen=max_lines)
        self.error = None
        self._proc = None
        self._reader = None

    def start(self):
        if self.is_running():
            return
        self.lines.clear()
        self.error = None

        creationflags = 0
        if platform.system() == "Windows":
            # Don't pop a console window for the child.
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        # Unbuffered child stdout so Python servers' logs arrive line-by-line.
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        try:
            self._proc = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=env,
                creationflags=creationflags,
            )
        except OSError as e:
            self.error = str(e)
            self._emit("[launcher] failed to start: {}".format(e))
            raise

        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self):
        try:
            for line in self._proc.stdout:
                self._emit(line.rstrip("\n"))
        except (ValueError, OSError):
            pass  # stream closed during shutdown

    def _emit(self, line):
        self.lines.append(line)
        if self.on_output:
            try:
                self.on_output(self.key, line)
            except Exception:
                pass

    def is_running(self):
        return self._proc is not None and self._proc.poll() is None

    @property
    def returncode(self):
        return self._proc.poll() if self._proc else None

    def stop(self, timeout=5):
        if not self.is_running():
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        finally:
            # release the pipe fd and unblock the reader thread
            if self._proc.stdout:
                try:
                    self._proc.stdout.close()
                except OSError:
                    pass
