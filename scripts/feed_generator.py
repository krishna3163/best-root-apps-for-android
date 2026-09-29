"""Obtainium & RSS / Atom Feed Generator for Root Apps.

Generates site/releases.atom and site/obtainium.json for tracking mirrored Root apps and modules.
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("feed-generator")
ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "config" / "apps.json"
SITE_DIR = ROOT / "site"


def build_obtainium_export(apps_data: list[dict[str, Any]]) -> dict[str, Any]:
    obtainium_apps = []
    for app in apps_data:
        slug = app.get("slug", "")
        name = app.get("name", slug)
        repo = app.get("source_repo", "")
        if not repo:
            continue
        obtainium_apps.append({
            "id": slug,
            "url": f"https://github.com/{repo}",
            "author": repo.split("/")[0],
            "name": name,
            "preferredApkFilter": app.get("asset_patterns", [".*\\.apk$"])[0] if app.get("asset_patterns") else ".*\\.apk$",
            "filterByPrerelease": False,
            "versionExtraction": "latest-stable",
        })

    return {
        "formatVersion": 1,
        "name": "Best Root Apps & Modules for Android",
        "description": "Auto-sync and direct update feed for Android Root, Magisk, KernelSU, and LSPosed apps",
        "source": "https://github.com/krishna3163/best-root-apps-for-android",
        "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "apps": obtainium_apps,
    }


def build_atom_feed(apps_data: list[dict[str, Any]]) -> str:
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    feed_id = "tag:github.com,2026:krishna3163/best-root-apps-for-android"
    feed_url = "https://github.com/krishna3163/best-root-apps-for-android"

    entries_xml = []
    for item in apps_data:
        title = html.escape(item.get("name") or "Root Module")
        slug = item.get("slug", "app")
        repo = item.get("source_repo", "krishna3163/best-root-apps-for-android")
        item_url = f"https://github.com/{repo}/releases"
        description = html.escape(item.get("description") or f"Mirrored update for {title}")

        entry = f"""  <entry>
    <title>{title} latest</title>
    <link href="{item_url}"/>
    <id>{feed_id}:{slug}:latest</id>
    <updated>{now_iso}</updated>
    <summary type="text">{description}</summary>
    <author>
      <name>{html.escape(repo.split('/')[0])}</name>
    </author>
  </entry>"""
        entries_xml.append(entry)

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Best Root Apps &amp; Modules for Android - Releases Feed</title>
  <subtitle>Latest mirrored APK releases and Root/Magisk/KernelSU updates</subtitle>
  <link href="{feed_url}/releases.atom" rel="self"/>
  <link href="{feed_url}"/>
  <id>{feed_id}</id>
  <updated>{now_iso}</updated>
  <author>
    <name>krishna3163</name>
    <uri>{feed_url}</uri>
  </author>
{chr(10).join(entries_xml)}
</feed>
"""
    return xml


def generate_feeds(output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    apps = []
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            apps = data.get("apps", [])
        except Exception as exc:
            logger.warning("Could not read apps.json: %s", exc)

    obtainium_data = build_obtainium_export(apps)
    obtainium_path = output_dir / "obtainium.json"
    obtainium_path.write_text(json.dumps(obtainium_data, indent=2), encoding="utf-8")

    atom_xml = build_atom_feed(apps)
    atom_path = output_dir / "releases.atom"
    atom_path.write_text(atom_xml, encoding="utf-8")

    return obtainium_path, atom_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Obtainium and Atom feeds for root apps.")
    parser.add_argument("--output-dir", default=str(SITE_DIR), help="Output directory")
    args = parser.parse_args()

    p_obt, p_atom = generate_feeds(Path(args.output_dir))
    print(f"✅ Generated {p_obt} and {p_atom}")
    return 0


if __name__ == "__main__":
    main()
