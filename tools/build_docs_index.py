#!/usr/bin/env python3
"""
Build search-index.json for PS2 Servers Documentation.
Extracts pages, sections (h2, h3), text content, and categorizes them
into docs/data/search-index.json for instant client-side search.
"""

import os
import re
import json
from html.parser import HTMLParser

DOCS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "docs"))
OUTPUT_JSON = os.path.join(DOCS_DIR, "data", "search-index.json")

PAGE_CATEGORIES = {
    "index.html": "overview",
    "getting-started.html": "start",
    "editions.html": "start",
    "releases.html": "start",
    "udpfs.html": "protocols",
    "smb.html": "protocols",
    "udpbd.html": "protocols",
    "http.html": "protocols",
    "edge.html": "edge",
    "edge-builds.html": "edge",
    "edge-manual.html": "edge",
    "openwrt.html": "edge",
    "router-status.html": "edge",
    "cli.html": "operations",
    "compression.html": "operations",
    "protocol-compatibility.html": "operations",
    "udpfs-diagnostics.html": "operations",
    "antivirus.html": "operations",
    "nbd-ps2client.html": "operations",
    "credits.html": "reference",
}

class SectionHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_main = False
        self.in_heading = False
        self.heading_tag = ""
        self.current_heading_text = ""
        self.current_heading_id = ""
        self.page_title = ""
        self.in_title = False
        self.sections = [] # list of (id, title, text)
        self.current_text = []

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        if tag == "title":
            self.in_title = True
        elif tag == "main":
            self.in_main = True
        elif self.in_main and tag in ("h1", "h2", "h3"):
            self.in_heading = True
            self.heading_tag = tag
            self.current_heading_text = ""
            self.current_heading_id = attr_dict.get("id", "")
        elif tag == "aside" and attr_dict.get("class") == "toc":
            self.in_main = False

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        elif tag == "main":
            self.in_main = False
        elif self.in_main and tag in ("h1", "h2", "h3"):
            self.in_heading = False
            title = re.sub(r"\s+", " ", self.current_heading_text).strip()
            # Clean icon names or trailing arrows
            title = re.sub(r"^[^\w\s]+", "", title).strip()
            title = title.replace("→", "").replace("↗", "").strip()
            if self.current_text and self.sections:
                # Append text to previous section
                prev_id, prev_title, prev_txt = self.sections[-1]
                self.sections[-1] = (prev_id, prev_title, " ".join(self.current_text).strip())
                self.current_text = []
            self.sections.append((self.current_heading_id, title, ""))

    def handle_data(self, data):
        if self.in_title:
            self.page_title += data
        elif self.in_heading:
            self.current_heading_text += data
        elif self.in_main:
            clean = re.sub(r"\s+", " ", data).strip()
            if clean:
                self.current_text.append(clean)

    def finalize(self):
        if self.current_text and self.sections:
            prev_id, prev_title, prev_txt = self.sections[-1]
            self.sections[-1] = (prev_id, prev_title, " ".join(self.current_text).strip())
        # Clean page title
        title = self.page_title.split("·")[0].strip()
        return title, self.sections

def build_index():
    index_entries = []
    
    for filename, cat in PAGE_CATEGORIES.items():
        filepath = os.path.join(DOCS_DIR, filename)
        if not os.path.exists(filepath):
            continue
        
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        parser = SectionHTMLParser()
        parser.feed(content)
        page_title, sections = parser.finalize()

        # Add whole-page entry
        full_page_text = []
        for s_id, s_title, s_text in sections:
            full_page_text.append(f"{s_title} {s_text}")

        all_text = " ".join(full_page_text)
        index_entries.append({
            "title": page_title,
            "cat": cat,
            "text": all_text[:2500],
            "url": filename
        })

        # Add granular section entries for h2/h3
        for s_id, s_title, s_text in sections:
            if not s_title or s_title.lower() == page_title.lower():
                continue
            sec_url = f"{filename}#{s_id}" if s_id else filename
            sec_text = f"{s_title}. {s_text[:1200]}"
            index_entries.append({
                "title": f"{page_title} — {s_title}",
                "cat": cat,
                "text": sec_text,
                "url": sec_url
            })

    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(index_entries, f, indent=2, ensure_ascii=False)

    print(f"Generated {len(index_entries)} search index entries in {OUTPUT_JSON}")

if __name__ == "__main__":
    build_index()
