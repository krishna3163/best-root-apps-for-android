"""Validate and inspect the Android root app catalog README."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
APP_ROW = re.compile(
    r"^\| \*\*\[([^\]]+)\]\((https?://[^)]+)\)\*\* \| (.*?) \| (.*?) \| (.*?) \|\s*$"
)
URL_PATTERN = re.compile(r"https?://[^)\s<>]+")


def slug(text: str) -> str:
    text = re.sub(r"[^a-z0-9 -]", "", text.lower()).strip()
    return re.sub(r"\s+", "-", text)


def read_catalog() -> tuple[str, list[dict[str, str]]]:
    content = README.read_text(encoding="utf-8")
    entries = []
    for line in content.splitlines():
        match = APP_ROW.match(line)
        if match:
            entries.append(
                {
                    "name": match.group(1),
                    "url": match.group(2),
                    "description": match.group(3),
                    "license": match.group(4),
                    "links": match.group(5),
                }
            )
    return content, entries


def validate(content: str, entries: list[dict[str, str]]) -> list[str]:
    errors = []
    required_sections = (
        "# Root Apps and Modules",
        "## No-Root Shizuku Alternative",
        "## Legal and Safety",
    )
    errors.extend(f"Missing section: {section}" for section in required_sections if section not in content)

    if "vscode-resource" in content:
        errors.append("README contains a VS Code-only resource URL")
    if not entries:
        errors.append("No app table rows found")

    names = [entry["name"].lower() for entry in entries]
    urls = [entry["url"] for entry in entries]
    errors.extend(f"Duplicate app name: {name}" for name, count in Counter(names).items() if count > 1)
    duplicate_urls = [url for url, count in Counter(urls).items() if count > 1]
    for url in duplicate_urls:
        print(f"WARN shared app URL: {url}")

    for entry in entries:
        if not re.search(r"\b(?:FOSS|Proprietary|See project)\b", entry["license"]):
            errors.append(f"Missing license: {entry['name']}")
        valid_badges = {"[M]", "[K]", "[A]", "[LSP]"}
        badges = set(re.findall(r"\[[^\]]+\]", entry["license"]))
        invalid_badges = badges - valid_badges
        if invalid_badges:
            errors.append(
                f"Invalid framework badge for {entry['name']}: {', '.join(sorted(invalid_badges))}"
            )

    local_anchors = set(re.findall(r"\]\(#([^)]+)\)", content))
    headings = {
        slug(heading)
        for heading in re.findall(r"^#{1,6} (.+)$", content, re.MULTILINE)
    }
    errors.extend(f"Missing local anchor: #{anchor}" for anchor in sorted(local_anchors - headings))
    return errors


def check_links(entries: list[dict[str, str]]) -> int:
    urls = sorted(
        {
            url
            for entry in entries
            for url in URL_PATTERN.findall(entry["links"])
            if "shields.io" not in url
        }
    )
    def check(url: str) -> tuple[str, int | None]:
        request = Request(url, method="HEAD", headers={"User-Agent": "README-link-checker/1.0"})
        try:
            with urlopen(request, timeout=8) as response:
                status = response.status
        except HTTPError as error:
            status = error.code
        except (URLError, TimeoutError):
            return url, None
        return url, status

    failures = 0
    with ThreadPoolExecutor(max_workers=16) as executor:
        results = executor.map(check, urls)
    for url, status in results:
        if status is None:
            print(f"WARN link unavailable: {url}")
            continue
        if status in (404, 410):
            failures += 1
            print(f"FAIL {status}: {url}")
        else:
            print(f"OK {status}: {url}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-links", action="store_true", help="Check catalog source links")
    args = parser.parse_args()

    content, entries = read_catalog()
    errors = validate(content, entries)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    print(f"README validation passed: {len(entries)} app rows")
    print(f"Licenses: {dict(Counter(entry['license'].split()[0] for entry in entries))}")
    print(f"Framework badges: {dict(Counter(badge for entry in entries for badge in re.findall(r'\[(?:M|K|A|LSP)\]', entry['license'])))}")
    if args.check_links and check_links(entries):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())