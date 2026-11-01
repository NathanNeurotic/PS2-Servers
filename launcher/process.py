"""Subprocess supervisor.

Each server runs as its own child process; this wraps one with start/stop and
captures its merged stdout/stderr into a rolling buffer (and an optional live
callback) so the GUI can show logs without a console window.
"""

import os
import platform
import subprocess
import threading
import time
from collections import deque

# Set in a child's environment when the launcher holds the write end of the
# child's stdin. Closing that pipe asks the child to stop the way Ctrl+C does
# (see launcher/serve.py), which also works on Windows, where terminate() is
# TerminateProcess and no handler in the child can run.
STOP_PIPE_ENV = "PS2SERVERS_STOP_PIPE"
STOP_GRACE_SECONDS = 3.0


class ServerProcess:
    def __init__(self, key, command, cwd=None, on_output=None, max_lines=2000,
                 cooperative_stop=False, stop_grace=STOP_GRACE_SECONDS):
        self.key = key
        self.command = command
        self.cwd = cwd
        self.on_output = on_output
        # Only for children that run launcher/serve.py: anything else ignores
        # its stdin, and stop() would wait out the grace period for nothing.
        self.cooperative_stop = cooperative_stop
        self.stop_grace = float(stop_grace)
        self._stop_requested_at = None
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
        startupinfo = None
        if platform.system() == "Windows":
            # Do not flash or leave a console window for bundled server children.
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0

        # Unbuffered child stdout so Python servers' logs arrive line-by-line.
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        if self.cooperative_stop:
            env[STOP_PIPE_ENV] = "1"
        else:
            env.pop(STOP_PIPE_ENV, None)
        self._stop_requested_at = None

        try:
            self._proc = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE if self.cooperative_stop else subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=env,
                creationflags=creationflags,
                startupinfo=startupinfo,
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
        finally:
            # close the pipe on EOF too (a server that exits on its own never
            # reaches stop(), which early-returns once the process is gone)
            try:
                self._proc.stdout.close()
            except OSError:
                pass

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

    def request_stop(self):
        """Ask a cooperative child to stop, without waiting. Idempotent.

        Returns True if a request was sent (or had already been sent), so a
        caller stopping several servers can ask them all before waiting on any.
        """
        if not self.cooperative_stop or not self.is_running():
            return False
        if self._stop_requested_at is None:
            self._stop_requested_at = time.monotonic()
            stdin = self._proc.stdin
            if stdin is not None:
                try:
                    stdin.close()  # EOF on the child's stdin = stop
                except OSError:
                    pass
        return True

    def stop(self, timeout=5):
        if not self.is_running():
            # Exited on its own: the reader closes stdout at EOF; only our
            # end of a stop pipe is left to release.
            if self._proc is not None and self._proc.stdin:
                try:
                    self._proc.stdin.close()
                except OSError:
                    pass
            return
        if self.request_stop():
            # The grace period counts from the request, so stopping several
            # servers that were all asked first takes one grace, not one each.
            remaining = self.stop_grace - (time.monotonic() - self._stop_requested_at)
            try:
                self._proc.wait(timeout=max(0.0, remaining))
            except subprocess.TimeoutExpired:
                self._emit("[launcher] {} did not stop within {:g}s; ending it".format(
                    self.key, self.stop_grace))
        try:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait()
        finally:
            self._close_pipes()

    def _close_pipes(self):
        if self._proc is None:
            return
        # Let the reader drain what the child printed on its way out (the
        # final SESSION and stats lines) before the pipe is closed under it.
        # Bounded: a grandchild still holding the pipe must not hang Stop.
        reader = self._reader
        if (reader is not None and reader is not threading.current_thread()
                and self._proc.poll() is not None):
            reader.join(timeout=1.0)
        # release the pipe fds and unblock the reader thread
        for stream in (self._proc.stdin, self._proc.stdout):
            if stream:
                try:
                    stream.close()
                except OSError:
                    pass
