"""Run focused quality checks for the Android app catalog."""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
APP_ROW = re.compile(
    r"^\| \*\*\[([^\]]+)\]\((https?://[^)]+)\)\*\* \| (.*?) \| (.*?) \| (.*?) \|\s*$"
)
BAD_URLS = ("file+.vscode-resource", "vscode-resource", "bit.ly/", "tinyurl.com/")


def data() -> tuple[str, list[dict[str, str]]]:
    content = README.read_text(encoding="utf-8")
    entries = []
    for line in content.splitlines():
        match = APP_ROW.match(line)
        if match:
            entries.append(dict(name=match.group(1), url=match.group(2), description=match.group(3), license=match.group(4), links=match.group(5)))
    return content, entries


def run(task: str) -> None:
    content, entries = data()
    names = [entry["name"].lower() for entry in entries]
    urls = [entry["url"] for entry in entries]

    if task == "links":
        if any(marker in content for marker in BAD_URLS):
            raise ValueError("README contains a local, shortened, or VS Code-only URL")
    elif task == "duplicates":
        duplicates = [name for name, count in Counter(names).items() if count > 1]
        if duplicates:
            raise ValueError(f"Duplicate app names: {', '.join(duplicates)}")
        print(f"Checked {len(set(urls))} unique source URLs")
    elif task == "toc":
        anchors = set(re.findall(r"\]\(#([^)]+)\)", content))
        headings = {re.sub(r"\s+", "-", re.sub(r"[^a-z0-9 -]", "", h.lower()).strip()) for h in re.findall(r"^#{1,6} (.+)$", content, re.MULTILINE)}
        headings.update(re.findall(r'<h[1-6] id="([^"]+)">', content))
        missing = sorted(anchors - headings)
        if missing:
            raise ValueError(f"Missing TOC anchors: {', '.join(missing)}")
    elif task == "tables":
        if not entries or content.count("| App | Description | License | Links |") == 0:
            raise ValueError("No app tables found")
        print(f"Parsed {len(entries)} app rows")
    elif task == "badges":
        valid = {"[M]", "[K]", "[A]", "[LSP]"}
        for entry in entries:
            found = set(re.findall(r"\[[^\]]+\]", entry["license"]))
            invalid = found - valid
            if invalid:
                raise ValueError(f"Invalid badge on {entry['name']}: {', '.join(invalid)}")
    elif task == "licenses":
        missing = [entry["name"] for entry in entries if not re.search(r"\b(?:FOSS|Proprietary|See project)\b", entry["license"])]
        if missing:
            raise ValueError(f"Missing licenses: {', '.join(missing)}")
    elif task == "labels":
        for entry in entries:
            host = urlparse(entry["url"]).netloc
            expected = "GitHub" if "github.com" in host else "GitLab" if "gitlab.com" in host else "Codeberg" if "codeberg.org" in host else "Google Play" if "play.google.com" in host else "Website"
            if f"[{expected}]" not in entry["links"]:
                raise ValueError(f"Wrong source label on {entry['name']}: expected {expected}")
    elif task == "featured":
        featured = [entry for entry in entries if "⭐" in entry["name"]]
        if any(not entry["description"].strip() for entry in featured):
            raise ValueError("Featured apps must have descriptions")
        print(f"Checked {len(featured)} featured apps")
    elif task == "stats":
        print(f"Apps: {len(entries)}")
        print(f"Categories: {len(re.findall(r'^### ', content, re.MULTILINE))}")
        print(f"External links: {len(re.findall(r'https?://', content))}")
    elif task == "shizuku":
        if "best_shizuku_apps_for_android_no_root" not in content or "Shizuku" not in content:
            raise ValueError("No-root Shizuku repository link or section is missing")
    elif task == "security":
        suspicious = [url for url in re.findall(r"https?://[^)\s<>]+", content) if any(marker in url.lower() for marker in ("javascript:", "data:", "bit.ly/", "tinyurl.com/"))]
        if suspicious:
            raise ValueError(f"Suspicious URLs: {', '.join(suspicious)}")
    elif task == "sort":
        print("Alphabetical ordering check is informational; featured entries may appear first.")
    elif task == "issue-form":
        form = ROOT / ".github/ISSUE_TEMPLATE/app-suggestion.yml"
        if not form.exists():
            raise ValueError("App suggestion issue form is missing")
    elif task == "workflow":
        workflows = list((ROOT / ".github/workflows").glob("*.yml"))
        if not workflows:
            raise ValueError("No GitHub Actions workflows found")
        print(f"Found {len(workflows)} workflows")
    elif task == "site":
        print("Searchable site source is present: scripts/build_site.py")
        if not (ROOT / "scripts/build_site.py").exists():
            raise ValueError("Site generator is missing")
    else:
        raise ValueError(f"Unknown task: {task}")
    print(f"PASS: {task}")


parser = argparse.ArgumentParser()
parser.add_argument("--task", required=True)
args = parser.parse_args()
try:
    run(args.task)
except ValueError as error:
    print(f"FAIL: {error}")
    sys.exit(1)