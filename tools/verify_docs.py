import os
import re

docs = r"c:\Users\natha\Github\PS2-Servers\docs"
html_files = [f for f in os.listdir(docs) if f.endswith(".html")]

anchors = {}
for hf in html_files:
    path = os.path.join(docs, hf)
    with open(path, "r", encoding="utf-8") as f:
        c = f.read()
    found_ids = set(re.findall(r'id=["\']([^"\']+)["\']', c))
    anchors[hf] = found_ids

errors = 0
for hf in html_files:
    path = os.path.join(docs, hf)
    with open(path, "r", encoding="utf-8") as f:
        c = f.read()
    links = re.findall(r'href=["\']([^"\']+)["\']', c)
    srcs = re.findall(r'src=["\']([^"\']+)["\']', c)
    
    for l in links:
        if l.startswith(("http://", "https://", "mailto:", "data:")):
            continue
        if l.startswith("#"):
            aid = l[1:]
            if aid not in anchors[hf]:
                print(f"{hf}: Broken local anchor #{aid}")
                errors += 1
        elif "#" in l:
            tgt, aid = l.split("#", 1)
            if tgt not in anchors:
                print(f"{hf}: Target file {tgt} not found for link {l}")
                errors += 1
            elif aid not in anchors[tgt]:
                print(f"{hf}: Anchor #{aid} not found in {tgt}")
                errors += 1
        else:
            if l.endswith((".css", ".svg", ".png", ".ico", ".js", ".json")):
                sp = os.path.join(docs, l.replace("/", os.sep))
                if not os.path.exists(sp):
                    print(f"{hf}: Asset {l} not found")
                    errors += 1
            elif l not in anchors:
                print(f"{hf}: Target file {l} not found")
                errors += 1
    
    for s in srcs:
        if s.startswith(("http://", "https://", "data:")):
            continue
        sp = os.path.join(docs, s.replace("/", os.sep))
        if not os.path.exists(sp):
            print(f"{hf}: Asset {s} not found")
            errors += 1

print(f"Verification finished. Total errors: {errors}")
