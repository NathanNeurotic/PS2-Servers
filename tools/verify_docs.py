import re
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"


def verify_docs(docs_dir=DOCS_DIR):
    docs = Path(docs_dir)
    html_files = [path.name for path in docs.iterdir() if path.suffix == ".html"]

    anchors = {}
    for hf in html_files:
        c = (docs / hf).read_text(encoding="utf-8")
        anchors[hf] = set(re.findall(r'id=["\']([^"\']+)["\']', c))

    errors = 0
    for hf in html_files:
        c = (docs / hf).read_text(encoding="utf-8")
        links = re.findall(r'href=["\']([^"\']+)["\']', c)
        srcs = re.findall(r'src=["\']([^"\']+)["\']', c)

        for link in links:
            if link.startswith(("http://", "https://", "mailto:", "data:")):
                continue
            if link.startswith("#"):
                aid = link[1:]
                if aid not in anchors[hf]:
                    print(f"{hf}: Broken local anchor #{aid}")
                    errors += 1
            elif "#" in link:
                tgt, aid = link.split("#", 1)
                if tgt not in anchors:
                    print(f"{hf}: Target file {tgt} not found for link {link}")
                    errors += 1
                elif aid not in anchors[tgt]:
                    print(f"{hf}: Anchor #{aid} not found in {tgt}")
                    errors += 1
            else:
                if link.endswith((".css", ".svg", ".png", ".ico", ".js", ".json")):
                    if not (docs / link).exists():
                        print(f"{hf}: Asset {link} not found")
                        errors += 1
                elif link not in anchors:
                    print(f"{hf}: Target file {link} not found")
                    errors += 1

        for src in srcs:
            if src.startswith(("http://", "https://", "data:")):
                continue
            if not (docs / src).exists():
                print(f"{hf}: Asset {src} not found")
                errors += 1

    print(f"Verification finished. Total errors: {errors}")
    return errors


if __name__ == "__main__":
    raise SystemExit(verify_docs())
