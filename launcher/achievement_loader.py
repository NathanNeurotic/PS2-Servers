"""Download pinned upstream console loaders with verified provenance."""
import hashlib
import json
from pathlib import Path
import tempfile
import urllib.request

from launcher.game_library import publish_new

LOADERS = {
    "xerabora": {
        "url": "https://github.com/hacan359/xerabora/releases/download/v0.1.0-alpha.16/OPL-RA.ELF",
        "sha256": "0436f8a12fa4e93466687bcb70b00c25d806ecda91ef9669c04b3d5ad40801c9",
        "size": 1400084,
        "source": "https://github.com/hacan359/Open-PS2-Loader/tree/531aad4584d65d1779b744739b81402150c7669c",
        "license": "https://raw.githubusercontent.com/Rian6/caduceus/311f4eabc4ddaad649cbd0d60859f27d0ffcbbfc/vendor/xerabora/LICENSE-OPL.txt",
        "license_blob": "62b2577d2067081dd3b8549b53d4862e60205951",
    },
    "caduceus": {
        "url": "https://github.com/Rian6/caduceus-opl/releases/download/v0.1.1-snapshot.20261006/OPL-RA.ELF",
        "sha256": "6c6f095f2cf466225109a77f4c64ba7014b523370e2cef93646f1af543f964a9",
        "size": 1512132,
        "source": "https://github.com/Rian6/caduceus-opl/tree/7a85103eaf65bf19fc155dba239a86acdaae6a55",
        "license": "https://raw.githubusercontent.com/Rian6/caduceus/311f4eabc4ddaad649cbd0d60859f27d0ffcbbfc/vendor/xerabora/LICENSE-OPL.txt",
        "license_blob": "62b2577d2067081dd3b8549b53d4862e60205951",
    },
}


def blob_id(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def fetch(url, limit):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "PS2-Servers"}), timeout=20) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Upstream loader download exceeded its size limit.")
    return data


def export_loader(mode, directory):
    if mode not in LOADERS:
        raise ValueError("Select xeRAbora or Caduceus.")
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError("Choose an existing destination folder.")
    spec = LOADERS[mode]
    data = fetch(spec["url"], spec["size"])
    digest = hashlib.sha256(data).hexdigest()
    if len(data) != spec["size"] or not data.startswith(b"\x7fELF"):
        raise ValueError("The upstream download is not the expected console ELF.")
    if (spec.get("sha256") and spec["sha256"] != digest) or (spec.get("git_blob") and spec["git_blob"] != blob_id(data)):
        raise ValueError("The console loader checksum does not match the pinned upstream file.")
    license_data = fetch(spec["license"], 20000)
    if blob_id(license_data) != spec["license_blob"]:
        raise ValueError("The loader license does not match the pinned upstream notice.")
    prefix = "OPL-RA-" + mode
    provenance = dict(spec, mode=mode, downloaded_sha256=digest,
                      note="Downloaded directly from upstream; PS2-Servers does not build or modify this loader.")
    files = [(prefix + ".LICENSE.txt", license_data),
             (prefix + ".SOURCE.json", json.dumps(provenance, indent=2).encode("utf-8")),
             (prefix + ".ELF", data)]
    if any((directory / name).exists() for name, _ in files):
        raise FileExistsError("Loader export files already exist; choose another folder to preserve them.")
    for name, contents in files:
        with tempfile.NamedTemporaryFile(dir=directory, suffix=".part", delete=False) as output:
            temporary = Path(output.name)
            output.write(contents)
        try:
            publish_new(temporary, directory / name)
        finally:
            temporary.unlink(missing_ok=True)
    return str(directory / (prefix + ".ELF"))
