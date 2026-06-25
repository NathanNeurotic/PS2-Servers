"""--serve dispatch: run one Python server inside this process.

Invoked as `<launcher> --serve <key> [server args...]`. We import the target
server module by file path (adding its own directory to sys.path so its sibling
imports, e.g. udpfs's `compressed_iso` package, resolve), then hand control to
the server's own `main()` exactly as if it had been run directly.
"""

import importlib.util
import os
import sys


def _load_module(path):
    name = "_ps2srv_" + os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load server module: {}".format(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run_serve(key, server_args):
    from .servers import REGISTRY

    # Stream the server's prints line-by-line to our captured pipe instead of
    # letting Python block-buffer them (works in both source and frozen builds).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):
            pass

    server = REGISTRY.get(key)
    if server is None:
        raise SystemExit("unknown server: {}".format(key))
    if server.runtime != "python":
        raise SystemExit("{} is a native server; run its binary directly".format(key))

    if server.module_dir and server.module_dir not in sys.path:
        sys.path.insert(0, server.module_dir)

    module = _load_module(server.module_file)

    # Present the args as if the server had been launched on its own.
    sys.argv = [key] + list(server_args)
    main = getattr(module, "main", None)
    if main is None:
        raise SystemExit("server {} has no main()".format(key))
    return main()  # propagate the server's exit code (e.g. 1 on bind/open failure)
