"""Discover new Android root projects through the GitHub Search API."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
START = "<!-- AUTO-DISCOVERED-ROOT-APPS:START -->"
END = "<!-- AUTO-DISCOVERED-ROOT-APPS:END -->"
QUERIES = (
    "android root app",
    "android magisk module",
    "android kernelsu module",
    "android shizuku app",
    "android lsposed module",
)


def github_search(query: str) -> list[dict[str, str]]:
    params = urlencode({"q": query, "sort": "updated", "order": "desc", "per_page": 20})
    request = Request(
        f"https://api.github.com/search/repositories?{params}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "best-root-apps-discovery/1.0",
            **({"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"} if os.environ.get("GITHUB_TOKEN") else {}),
        },
    )
    with urlopen(request, timeout=20) as response:
        return json.load(response).get("items", [])


def existing_urls(content: str) -> set[str]:
    return set(re.findall(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", content.lower()))


def markdown_cell(value: str) -> str:
    return re.sub(r"\|", r"\\|", value.replace("\r", "").replace("\n", " ")).strip()


def discover() -> list[dict[str, str]]:
    content = README.read_text(encoding="utf-8")
    known = existing_urls(content)
    found: dict[str, dict[str, str]] = {}
    for query in QUERIES:
        for item in github_search(query):
            url = item.get("html_url", "").rstrip("/").lower()
            if not url or url in known or item.get("fork") or item.get("archived"):
                continue
            name = item.get("name", "").strip()
            description = item.get("description") or f"GitHub project discovered by the {query} scanner."
            if not name or not item.get("owner", {}).get("login"):
                continue
            found[url] = {"name": name, "description": description, "url": url}
    return sorted(found.values(), key=lambda entry: entry["name"].lower())


def update_readme(entries: list[dict[str, str]]) -> None:
    content = README.read_text(encoding="utf-8")
    start = content.index(START) + len(START)
    end = content.index(END, start)
    old_rows = [
        line.strip()
        for line in content[start:end].splitlines()
        if line.strip().startswith("| **[")
    ]
    rows = [
        "| App | Description | License | Links |",
        "|:---|:---|:---|:---|",
    ]
    rows.extend(old_rows)
    if entries:
        rows.extend(
            f"| **[{markdown_cell(entry['name'])}]({entry['url']})** | {markdown_cell(entry['description'])} | See project | [GitHub]({entry['url']}) |"
            for entry in entries
        )
    else:
        rows.append("| _No new projects discovered yet._ | The daily scanner will add matching GitHub projects here. | — | — |")
    replacement = "\n" + "\n".join(rows) + "\n"
    README.write_text(content[:start] + replacement + content[end:], encoding="utf-8")


if __name__ == "__main__":
    entries = discover()
    update_readme(entries)
    print(f"Discovered {len(entries)} new GitHub projects")