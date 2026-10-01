#!/usr/bin/env python3
"""Make sure docs/index.html has the Nationwide / Toss-up races tab bar.

The nationwide job regenerates index.html from its own template, which can drop
the tab bar. This puts it back. Safe to run any number of times.
"""
import sys
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "docs" / "index.html"
NAV = ('<nav class="tabs" aria-label="Tracker sections"><a href="index.html" class="active" '
       'aria-current="page">Nationwide</a><a href="races.html">Toss-up races</a></nav>')
CSS = ('<style id="dp-tabs">.tabs{display:flex;border-bottom:1px solid var(--ink);margin:0 0 28px}'
       '.tabs a{padding:10px 16px;color:var(--ink);text-decoration:none;font-weight:700;font-size:14px;'
       'letter-spacing:.04em;border:1px solid transparent;border-bottom:none;margin-bottom:-1px}'
       '.tabs a.active{background:var(--surface);border-color:var(--ink)}'
       '.tabs a:not(.active):hover{text-decoration:underline}'
       '@media(max-width:560px){.tabs a{padding:9px 10px;font-size:13px}}</style>')


def main():
    if not INDEX.exists():
        sys.exit(f"{INDEX} not found")
    html = INDEX.read_text()
    changed = False
    if 'class="tabs"' not in html:
        if "<main>" not in html:
            sys.exit("index.html has no <main> tag; tab bar not added.")
        html = html.replace("<main>", "<main>" + NAV, 1)
        changed = True
    if ".tabs{" not in html:
        if "</head>" not in html:
            sys.exit("index.html has no </head> tag; tab styles not added.")
        html = html.replace("</head>", CSS + "</head>", 1)
        changed = True
    if changed:
        INDEX.write_text(html)
        print("Restored the tab bar in docs/index.html")
    else:
        print("Tab bar already present in docs/index.html")


if __name__ == "__main__":
    main()
