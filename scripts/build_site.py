"""Build a small searchable catalog website from README app tables."""

from __future__ import annotations

import html
import re
from pathlib import Path

from readme import README, read_catalog

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "site"


def build() -> None:
    content, entries = read_catalog()
    headings = list(re.finditer(r"^### (.+)$", content, re.MULTILINE))
    cursor = 0
    current_category = "Root Apps"
    categorized = []
    for entry in entries:
        position = content.find(entry["name"], cursor)
        while headings and headings[0].start() < position:
            current_category = headings.pop(0).group(1).replace("**", "")
        cursor = position + len(entry["name"])
        categorized.append((current_category, entry))

    cards = []
    for category, entry in categorized:
        cards.append(
            f'<article class="app" data-category="{html.escape(category)}">'
            f'<h2><a href="{html.escape(entry["url"])}">{html.escape(entry["name"])}</a></h2>'
            f'<p>{entry["description"]}</p>'
            f'<small>{html.escape(category)} · {html.escape(entry["license"])}</small>'
            f'<a class="source" href="{html.escape(entry["url"])}">Open source</a>'
            "</article>"
        )

    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT / "index.html").write_text(
        """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Best Root Apps for Android</title><style>
:root{font-family:system-ui,sans-serif;color:#17202a;background:#f4f7f5}body{max-width:1180px;margin:auto;padding:32px 20px}header{padding:28px 0;border-bottom:1px solid #cbd5d1}h1{margin:0 0 8px;font-size:clamp(2rem,5vw,4rem)}header p{max-width:720px;color:#53635d}.controls{display:flex;gap:12px;margin:24px 0;flex-wrap:wrap}input,select{padding:12px 14px;border:1px solid #aebcb5;border-radius:6px;background:white;font:inherit}input{min-width:min(440px,100%);flex:1}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:14px}.app{background:white;border:1px solid #d5dfda;border-radius:6px;padding:18px;display:flex;flex-direction:column;gap:10px;box-shadow:0 2px 8px #1b33200d}.app h2{font-size:1.05rem;margin:0}.app a{color:#176b4d}.app p{margin:0;line-height:1.5;color:#42514a}.app small{color:#65736d}.source{margin-top:auto;font-weight:600;text-decoration:none}.count{color:#65736d;margin-bottom:14px}@media(max-width:600px){body{padding:20px 14px}}
</style></head><body><header><h1>Best Root Apps for Android</h1><p>Search 500+ root apps, modules, privacy tools, system utilities and no-root Shizuku-compatible resources.</p></header>
<main><div class="controls"><input id="search" type="search" placeholder="Search apps, tools, or features"><select id="category"><option value="">All categories</option></select></div><div class="count" id="count"></div><section class="grid" id="apps">"""
        + "\n".join(cards)
        + """</section></main><script>
const cards=[...document.querySelectorAll('.app')],search=document.querySelector('#search'),category=document.querySelector('#category'),count=document.querySelector('#count');
[...new Set(cards.map(c=>c.dataset.category))].sort().forEach(x=>category.add(new Option(x,x)));
function filter(){let q=search.value.toLowerCase(),c=category.value,n=0;cards.forEach(x=>{let ok=(!c||x.dataset.category===c)&&x.textContent.toLowerCase().includes(q);x.hidden=!ok;if(ok)n++});count.textContent=`${n} apps shown`}
search.oninput=filter;category.onchange=filter;filter();</script></body></html>""",
        encoding="utf-8",
    )
    print(f"Built {len(entries)} app cards in {OUTPUT / 'index.html'}")


if __name__ == "__main__":
    build()