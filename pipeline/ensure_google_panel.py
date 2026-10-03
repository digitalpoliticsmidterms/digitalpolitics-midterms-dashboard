#!/usr/bin/env python3
"""Add a small Google political-ad panel to the generated nationwide page.

The Meta builder owns site/index.html, so this post-processing step mirrors
ensure_tabs.py: it is safe to run after every site copy and never alters the
toss-up tab markup.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "docs" / "index.html"
MARKER = '<section class="panel method">'
PANEL = '''<section class="panel" id="dp-google-panel" hidden><p class="eyebrow">Google political advertising</p><h2>US Google campaign spending</h2><div id="dp-google-content"></div></section>'''
SCRIPT = '''<script id="dp-google-script">fetch('google_data.json').then(r=>r.ok?r.json():null).then(g=>{if(!g)return;const n=g.national,p=document.querySelector('#dp-google-panel'),f=v=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(v),d=n.change_since_prior_snapshot_usd;document.querySelector('#dp-google-content').innerHTML=`<p><strong>${f(n.reported_cumulative_spend_usd)}</strong> reported cumulative US spend by verified Google political advertisers.</p><p class="note">${d==null?'First snapshot; daily change will appear after the next collection.':`${d>=0?'+':''}${f(d)} since ${n.prior_snapshot_date}.`} Google and Meta are reported separately and are not automatically combined.</p><p class="note">${g.scope}</p>`;p.hidden=false;}).catch(()=>{});</script>'''


def main():
    if not INDEX.exists():
        raise SystemExit(f"{INDEX} not found")
    html = INDEX.read_text(encoding="utf-8")
    changed = False
    if 'id="dp-google-panel"' not in html:
        if MARKER not in html:
            raise SystemExit("Could not find methodology panel; Google panel not added.")
        html = html.replace(MARKER, PANEL + MARKER, 1)
        changed = True
    if 'id="dp-google-script"' not in html:
        html = html.replace("</body>", SCRIPT + "</body>", 1)
        changed = True
    if changed:
        INDEX.write_text(html, encoding="utf-8")
        print("Ensured Google nationwide panel in docs/index.html")
    else:
        print("Google nationwide panel already present in docs/index.html")


if __name__ == "__main__":
    main()
