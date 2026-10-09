"""Start the actual desktop package with a fresh profile; reject early exits."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def check(executable, seconds=12):
    executable = Path(executable).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="ps2-desktop-smoke-") as temporary:
        directory = Path(temporary)
        env = os.environ.copy()
        for key in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
            env[key] = str(directory / key.lower())
        with (directory / "startup.log").open("w+b") as output:
            process = subprocess.Popen([str(executable)], cwd=directory, env=env,
                                       stdout=output, stderr=subprocess.STDOUT)
            try:
                try:
                    code = process.wait(timeout=seconds)
                except subprocess.TimeoutExpired:
                    code = None
                output.seek(0)
                log = output.read().decode("utf-8", errors="replace")
                if code is not None or "Traceback (most recent call last)" in log:
                    raise RuntimeError("Desktop startup failed (exit {}):\n{}".format(code, log))
            finally:
                if process.poll() is None:
                    # End the isolated GUI and any onefile child, never user servers.
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                    else:
                        process.terminate()
                    process.wait(timeout=10)
    print("Desktop package stayed open on a fresh profile without a startup traceback.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("executable")
    args = parser.parse_args()
    check(args.executable)
