"""Morphe Patched Apps & Modules Auto-Builder & Synchronization.

Synchronizes community patch bundles from https://morphe-patches.software,
fetches base APKs, builds patched packages using Morphe CLI, publishes GitHub
releases with version/prerelease/beta tags, replaces old versions on update,
and generates an interactive searchable downloads table in README.md.

Usage:
    python scripts/morphe_sync.py                         # Sync all enabled Morphe apps
    python scripts/morphe_sync.py --app morphe-youtube   # Sync single app
    python scripts/morphe_sync.py --dry-run               # Check & build without publishing
    python scripts/morphe_sync.py --force-rebuild         # Force re-generation
    python scripts/morphe_sync.py --channel beta          # Filter by channel (stable/beta/prerelease)
    python scripts/morphe_sync.py --build-type autobuild  # Filter by build type
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from github_api import GitHubAPI
from morphe_builder import (
    BaseApkManager,
    MorpheAppConfig,
    MorpheBundleClient,
    MorphePatcher,
)

logger = logging.getLogger("morphe-sync")

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "morphe_apps.json"
DATA_DIR = ROOT / "data"
RELEASES_DB_PATH = DATA_DIR / "morphe-releases.json"
STATUS_DB_PATH = DATA_DIR / "morphe-status.json"
BUNDLES_CACHE_PATH = DATA_DIR / "morphe_bundles_cache.json"
README_PATH = ROOT / "README.md"

START_MARKER = "<!-- MORPHE-AUTOBP-APPS-START -->"
END_MARKER = "<!-- MORPHE-AUTOBP-APPS-END -->"


def setup_logging() -> None:
    stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        logger.error("Configuration file not found: %s", CONFIG_PATH)
        return {"apps": []}
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_json_db(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as e:
        logger.warning("Error reading %s: %s", path, e)
        return {}


def save_json_db(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")


def get_mirror_repo() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if repo:
        return repo
    try:
        import subprocess
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        url = result.stdout.strip()
        match = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
        if match:
            return match.group(1)
    except Exception:
        pass
    return ""


def make_morphe_tag(slug: str, version: str, channel: str = "stable") -> str:
    """Create a standardized release tag for Morphe builds."""
    clean_ver = re.sub(r"[^a-zA-Z0-9._-]", "-", version).strip("-")
    if channel in ("beta", "prerelease") and channel not in clean_ver.lower():
        return f"{slug}-{clean_ver}-{channel}"
    return f"{slug}-{clean_ver}"


def generate_morphe_release_body(
    app: MorpheAppConfig,
    version: str,
    bundle_info: Optional[dict[str, Any]],
    sha256: str,
    file_size: int,
    filename: str,
) -> str:
    bundle_repo = app.bundle_repo
    bundle_url = f"https://github.com/{bundle_repo}"
    size_mb = f"{file_size / (1024 * 1024):.2f} MB" if file_size else "unknown"
    patches_list = "\n".join(f"- {p}" for p in app.patches) if app.patches else "- Default recommended patches"

    return (
        f"# {app.name} `{version}`\n\n"
        f"Automated build generated with official Morphe patches from [{bundle_repo}]({bundle_url}).\n\n"
        f"## 🏷️ Release Details\n\n"
        f"- **Application:** `{app.package_name}`\n"
        f"- **Version:** `{version}`\n"
        f"- **Channel:** `{app.channel.upper()}` ({app.build_type})\n"
        f"- **Type:** `{app.app_type}`\n"
        f"- **Patch Bundle:** [{app.bundle_name or bundle_repo}]({bundle_url})\n\n"
        f"## 🧩 Applied Patches\n\n"
        f"{patches_list}\n\n"
        f"## 📦 Asset Information\n\n"
        f"- **File:** `{filename}`\n"
        f"- **Size:** `{size_mb}`\n"
        f"- **SHA-256:** `{sha256}`\n\n"
        f"## 🚀 Installation Guide\n\n"
        f"- **For Root / Mount:** Flash as Magisk/KernelSU module or replace system APK.\n"
        f"- **For Non-Root:** Ensure microG / GmsCore is installed if Google authentication is required.\n\n"
        f"---\n"
        f"*Synced and built automatically based on patch push from [morphe-patches.software](https://morphe-patches.software).* [Report Issue]({bundle_url}/issues)\n"
    )


DOCS_MORPHE_PATH = ROOT / "docs" / "MORPHE.md"
ROOT_MORPHE_PATH = ROOT / "MORPHE.md"
SITE_MORPHE_PATH = ROOT / "site" / "morphe.html"
SITE_INDEX_PATH = ROOT / "site" / "index.html"

FEATURED_SLUGS = {
    "morphe-youtube",
    "morphe-youtube-music",
    "pixelboard-gboard",
    "google-dialer-call-recording",
    "gboard-patches",
    "morphe-spotify",
    "morphe-reddit",
    "rustore-privacy",
    "mixplorer-patches",
    "samsung-health-knox",
}


def generate_morphe_readme_table(
    releases_db: dict[str, Any],
    status_db: dict[str, Any],
    apps_config: list[dict[str, Any]],
) -> str:
    """Generate the markdown section with interactive search and filter controls."""
    mirror_repo = get_mirror_repo()
    lines = [
        "",
        '<details id="morphe-downloads" open>',
        "<summary><h2>⚡ Morphe Patched Apps & AutoBuilds</h2></summary>",
        "",
        "> 🚀 **Auto-generated and synchronized based on patch pushes from [morphe-patches.software](https://morphe-patches.software/).**",
        "> Packages are patched using Morphe community bundles to provide ad-free, sponsor-blocked, and enhanced functionality.",
        "",
        '<div align="center" class="morphe-navigation-links">',
        "",
        "### 📖 [Browse Full 37+ Root Apps Catalog (docs/MORPHE.md)](docs/MORPHE.md) • 🌐 [Interactive Web Portal (site/morphe.html)](https://krishna3163.github.io/best-root-apps-for-android/morphe.html) • 📦 [277 Community Bundles](docs/MORPHE.md#-community-patch-bundles)",
        "",
        "**🔍 Quick Filter by Status:**",
        "[ **All** ](docs/MORPHE.md) • "
        "[ **📦 Release** ](docs/MORPHE.md#-status-filter) • "
        "[ **🚀 AutoBuild** ](docs/MORPHE.md#-status-filter) • "
        "[ **⚡ AutoGenerated** ](docs/MORPHE.md#-status-filter) • "
        "[ **🧪 Beta** ](docs/MORPHE.md#-status-filter) • "
        "[ **🚧 PreRelease** ](docs/MORPHE.md#-status-filter)",
        "",
        "</div>",
        "",
        "### ⭐ Featured Top Builds",
        "",
        "| App | Status / Channel | Version | Bundle Source | Key Patches | Download | SHA-256 | Updated |",
        "|:---|:---|:---|:---|:---|:---|:---|:---|",
    ]

    valid_apps = [a for a in apps_config if a.get("enabled", True) and a.get("slug") in releases_db]
    if len(valid_apps) <= 10:
        display_apps = valid_apps
    else:
        display_apps = [a for a in valid_apps if a.get("slug") in FEATURED_SLUGS]
        if not display_apps:
            display_apps = valid_apps[:8]

    has_entries = False
    for app_raw in sorted(display_apps, key=lambda a: a.get("name", "").lower()):
        slug = app_raw.get("slug", "")
        entry = releases_db.get(slug)
        if not entry:
            continue

        has_entries = True
        name = app_raw.get("name", slug)
        pkg = app_raw.get("package_name", "")
        version = entry.get("version", "latest")
        channel = entry.get("channel", app_raw.get("channel", "stable")).lower()
        build_type = entry.get("build_type", app_raw.get("build_type", "autobuild"))

        # Channel & build type badges
        if channel == "beta":
            status_badge = "`🧪 Beta` `AutoBuild`"
        elif channel == "prerelease":
            status_badge = "`🚧 PreRelease` `AutoBuild`"
        elif build_type == "autogenerated":
            status_badge = "`⚡ AutoGenerated`"
        elif build_type == "release":
            status_badge = "`📦 Release`"
        else:
            status_badge = "`🚀 AutoBuild`"

        bundle_repo = app_raw.get("bundle_repo", "MorpheApp/morphe-patches")
        bundle_name = app_raw.get("bundle_name") or bundle_repo.split("/")[-1]
        bundle_link = f"[{bundle_name}](https://github.com/{bundle_repo})"

        patches = app_raw.get("patches", [])
        if patches:
            patches_str = "<br>".join(f"• {p}" for p in patches[:2])
            if len(patches) > 2:
                patches_str += f"<br>_and {len(patches)-2} more…_"
        else:
            patches_str = "Default patches"

        mirror_tag = entry.get("release_tag", "")
        if mirror_repo and mirror_tag:
            download_url = f"https://github.com/{mirror_repo}/releases/tag/{mirror_tag}"
            download_link = f"[⬇️ **Download**]({download_url})"
        else:
            download_link = "—"

        sha256 = entry.get("sha256", "")
        sha_str = f"`{sha256[:10]}…`" if sha256 else "—"
        updated_at = entry.get("updated_at", "")[:10] or "—"

        lines.append(
            f"| **{name}**<br><sub>`{pkg}`</sub> | {status_badge} | `{version}` | {bundle_link} | {patches_str} | {download_link} | {sha_str} | {updated_at} |"
        )

    if not has_entries:
        lines.append("| _No Morphe APKs generated yet. Run autobuild workflow to initialize._ | — | — | — | — | — | — | — |")

    lines.append("")
    lines.append(
        "> 💡 **Looking for all 37+ root patched APKs and 277 community patch bundles?**  \n"
        "> 👉 **[View the Complete Morphe Catalog on Dedicated docs/MORPHE.md →](docs/MORPHE.md)**  \n"
        "> 🌐 **[Open Interactive Web Portal (site/morphe.html) →](site/morphe.html)**"
    )
    lines.append("")
    lines.append("</details>")
    lines.append("")
    return "\n".join(lines)


def generate_morphe_docs_page(
    releases_db: dict[str, Any],
    status_db: dict[str, Any],
    apps_config: list[dict[str, Any]],
) -> str:
    mirror_repo = get_mirror_repo()
    lines = [
        "# ⚡ Morphe Patched Apps & Community Bundles Hub (Root & System)",
        "",
        "> 🚀 **Official catalog of root-level and system-modified Morphe APKs and 277+ community patch bundles.**",
        "> Synchronized in real time with [morphe-patches.software](https://morphe-patches.software/).",
        "",
        "[![AutoBuild](https://img.shields.io/badge/Pipeline-AutoBuild-0284c7?style=flat-square)](docs/MORPHE.md)",
        "[![AutoGenerated](https://img.shields.io/badge/Channel-AutoGenerated-f59e0b?style=flat-square)](docs/MORPHE.md)",
        "[![Release](https://img.shields.io/badge/Release-Verified-16a34a?style=flat-square)](docs/MORPHE.md)",
        "[![Beta](https://img.shields.io/badge/Channel-Beta-9333ea?style=flat-square)](docs/MORPHE.md)",
        "[![PreRelease](https://img.shields.io/badge/Channel-PreRelease-e11d48?style=flat-square)](docs/MORPHE.md)",
        "",
        "---",
        "",
        "## 🧭 Quick Navigation",
        "",
        "- [📱 Complete Root Apps Directory](#-complete-root-apps-directory)",
        "- [📦 Community Patch Bundles (277+)](#-community-patch-bundles)",
        "- [🚀 Root Installation & Setup Guide](#-root-installation--setup-guide)",
        "- [🌐 Interactive Web Portal (site/morphe.html)](../site/morphe.html)",
        "",
        "---",
        "",
        '<span id="-status-filter"></span>',
        "## 🔍 Quick Filter by Status",
        "",
        "- `📦 Release` : Stable verified builds.",
        "- `🚀 AutoBuild` : Automatically patched and compiled from upstream patch repositories.",
        "- `⚡ AutoGenerated` : Regenerated on newer patch pushes or upstream base APK updates.",
        "- `🧪 Beta` : Fast-tracked builds testing the latest beta patches.",
        "- `🚧 PreRelease` : Pre-release canary builds for cutting-edge fixes.",
        "",
        "---",
        "",
        "## 📱 Complete Root Apps Directory",
        "",
        "| App | Package Name | Status / Channel | Version | Bundle Source | Key Patches | Download APK | SHA-256 Checksum | Last Updated |",
        "|:---|:---|:---|:---|:---|:---|:---|:---|:---|",
    ]

    for app_raw in sorted(apps_config, key=lambda a: a.get("name", "").lower()):
        slug = app_raw.get("slug", "")
        if not app_raw.get("enabled", True):
            continue

        entry = releases_db.get(slug)
        if not entry:
            continue

        name = app_raw.get("name", slug)
        pkg = app_raw.get("package_name", "")
        version = entry.get("version", "latest")
        channel = entry.get("channel", app_raw.get("channel", "stable")).lower()
        build_type = entry.get("build_type", app_raw.get("build_type", "autobuild"))

        if channel == "beta":
            status_badge = "`🧪 Beta` `AutoBuild`"
        elif channel == "prerelease":
            status_badge = "`🚧 PreRelease` `AutoBuild`"
        elif build_type == "autogenerated":
            status_badge = "`⚡ AutoGenerated`"
        elif build_type == "release":
            status_badge = "`📦 Release`"
        else:
            status_badge = "`🚀 AutoBuild`"

        bundle_repo = app_raw.get("bundle_repo", "MorpheApp/morphe-patches")
        bundle_name = app_raw.get("bundle_name") or bundle_repo.split("/")[-1]
        bundle_link = f"[{bundle_name}](https://github.com/{bundle_repo})"

        patches = app_raw.get("patches", [])
        if patches:
            patches_str = "<br>".join(f"• {p}" for p in patches[:3])
            if len(patches) > 3:
                patches_str += f"<br>_and {len(patches)-3} more…_"
        else:
            patches_str = "Default patches"

        mirror_tag = entry.get("release_tag", "")
        if mirror_repo and mirror_tag:
            download_url = f"https://github.com/{mirror_repo}/releases/tag/{mirror_tag}"
            download_link = f"[⬇️ **Download**]({download_url})"
        else:
            download_link = "—"

        sha256 = entry.get("sha256", "")
        sha_str = f"`{sha256[:12]}…`" if sha256 else "—"
        updated_at = entry.get("updated_at", "")[:10] or "—"

        lines.append(
            f"| **{name}** | `{pkg}` | {status_badge} | `{version}` | {bundle_link} | {patches_str} | {download_link} | {sha_str} | {updated_at} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## 📦 Community Patch Bundles",
        "",
        "All 277+ community patch bundles indexed from [morphe-patches.software](https://morphe-patches.software/).",
        "",
        "| Bundle Name | Author | Repository | Target Applications | Patches | Morphe Deep Link |",
        "|:---|:---|:---|:---|:---|:---|",
    ])

    bundles = []
    if BUNDLES_CACHE_PATH.exists():
        try:
            with open(BUNDLES_CACHE_PATH, "r", encoding="utf-8") as fh:
                bdata = json.load(fh)
                bundles = bdata.get("bundles", [])
        except Exception as e:
            logger.warning("Error reading bundles cache: %s", e)

    for b in sorted(bundles, key=lambda x: x.get("name", "").lower()):
        b_name = b.get("name", "")
        b_author = b.get("author", "")
        b_repo = b.get("repo", "")
        b_targets = ", ".join(f"`{t}`" for t in b.get("targetApps", [])[:3])
        if len(b.get("targetApps", [])) > 3:
            b_targets += f" *(+{len(b.get('targetApps'))-3} more)*"
        patch_cnt = b.get("patchCount", len(b.get("patches", [])))
        deep_link = b.get("deepLink") or f"https://morphe.software/add-source?github={b_repo}"
        repo_link = f"[{b_repo}](https://github.com/{b_repo})" if "/" in b_repo else b_repo

        lines.append(
            f"| **{b_name}** | `{b_author}` | {repo_link} | {b_targets or 'Universal'} | {patch_cnt} | [➕ Add to Morphe]({deep_link}) |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## 🚀 Root Installation & Setup Guide",
        "",
        "### 1. Root & System Privileges",
        "- For applications requiring system integration (e.g., Pixel Board, Google Dialer call recording, Samsung Health root bypass):",
        "- Mount the patched APK to system via Magisk / KernelSU module or use LSPosed with signature spoofing.",
        "",
        "### 2. Standalone Root Apps",
        "- Standard root-friendly builds (e.g. YouTube, Spotify, Reddit) can also be installed directly and will automatically detect root permissions.",
        "",
        "### 3. SHA-256 Checksum Verification",
        "```bash",
        "sha256sum <downloaded-file>.apk",
        "```",
        "Verify against the SHA-256 column in the table above.",
        "",
        "---",
        "",
        "*Automated synchronization and builds maintained by [krishna3163](https://github.com/krishna3163).*",
        "",
    ])

    return "\n".join(lines)


def generate_morphe_site_page(
    releases_db: dict[str, Any],
    status_db: dict[str, Any],
    apps_config: list[dict[str, Any]],
) -> str:
    mirror_repo = get_mirror_repo()
    apps_json = []
    for app_raw in sorted(apps_config, key=lambda a: a.get("name", "").lower()):
        slug = app_raw.get("slug", "")
        if not app_raw.get("enabled", True):
            continue
        entry = releases_db.get(slug, {})
        mirror_tag = entry.get("release_tag", "")
        download_url = f"https://github.com/{mirror_repo}/releases/tag/{mirror_tag}" if mirror_repo and mirror_tag else "#"
        apps_json.append({
            "name": app_raw.get("name", slug),
            "slug": slug,
            "package_name": app_raw.get("package_name", ""),
            "version": entry.get("version", "latest"),
            "channel": entry.get("channel", app_raw.get("channel", "stable")).lower(),
            "build_type": entry.get("build_type", app_raw.get("build_type", "autobuild")).lower(),
            "bundle_name": app_raw.get("bundle_name") or app_raw.get("bundle_repo", "").split("/")[-1],
            "bundle_repo": app_raw.get("bundle_repo", ""),
            "patches": app_raw.get("patches", []),
            "download_url": download_url,
            "sha256": entry.get("sha256", ""),
            "updated_at": (entry.get("updated_at") or "")[:10],
        })

    bundles_json = []
    if BUNDLES_CACHE_PATH.exists():
        try:
            with open(BUNDLES_CACHE_PATH, "r", encoding="utf-8") as fh:
                bdata = json.load(fh)
                for b in bdata.get("bundles", []):
                    bundles_json.append({
                        "name": b.get("name", ""),
                        "author": b.get("author", ""),
                        "repo": b.get("repo", ""),
                        "patchCount": b.get("patchCount", len(b.get("patches", []))),
                        "stars": b.get("stars", 0),
                        "targetApps": b.get("targetApps", []),
                        "deepLink": b.get("deepLink") or f"https://morphe.software/add-source?github={b.get('repo')}",
                    })
        except Exception as e:
            logger.warning("Error serializing bundles for site: %s", e)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>⚡ Morphe Root Apps & Bundles Hub</title>
  <style>
    :root {{ font-family: system-ui, -apple-system, sans-serif; color: #17202a; background: #f4f7f5; }}
    body {{ max-width: 1180px; margin: auto; padding: 32px 20px; }}
    header {{ padding: 24px 0; border-bottom: 1px solid #cbd5d1; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px; }}
    h1 {{ margin: 0 0 6px; font-size: clamp(1.8rem, 4vw, 2.8rem); }}
    .nav-btn {{ display: inline-flex; align-items: center; gap: 6px; padding: 8px 14px; background: white; border: 1px solid #cbd5d1; border-radius: 8px; color: #176b4d; text-decoration: none; font-weight: 600; font-size: 0.9rem; }}
    .nav-btn:hover {{ background: #e8f5e9; }}
    .nav-btn.primary {{ background: #176b4d; color: white; border-color: #176b4d; }}
    .tabs {{ display: flex; gap: 8px; margin: 24px 0 16px; }}
    .tab-btn {{ padding: 10px 20px; border: 1px solid #cbd5d1; background: white; border-radius: 8px; font-weight: 700; cursor: pointer; font-size: 0.95rem; color: #2c3e50; }}
    .tab-btn.active {{ background: #176b4d; color: white; border-color: #176b4d; }}
    .controls {{ display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }}
    input {{ padding: 12px 14px; border: 1px solid #aebcb5; border-radius: 8px; background: white; font: inherit; min-width: min(440px, 100%); flex: 1; }}
    .filter-chips {{ display: flex; gap: 8px; margin-bottom: 18px; flex-wrap: wrap; }}
    .chip {{ border: 1px solid #cbd5d1; background: #fff; padding: 6px 14px; border-radius: 20px; font-size: 0.85rem; cursor: pointer; font-weight: 600; color: #2c3e50; }}
    .chip.active {{ background: #176b4d; color: #fff; border-color: #176b4d; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }}
    .card {{ background: white; border: 1px solid #d5dfda; border-radius: 10px; padding: 20px; display: flex; flex-direction: column; justify-content: space-between; gap: 12px; box-shadow: 0 2px 8px #1b33200d; }}
    .card h2 {{ font-size: 1.15rem; margin: 0; }}
    .badge {{ font-size: 0.75rem; padding: 2px 8px; border-radius: 4px; font-weight: 700; display: inline-block; }}
    .badge-autobuild {{ background: #e0f2fe; color: #0369a1; }}
    .badge-autogenerated {{ background: #fef3c7; color: #b45309; }}
    .badge-release {{ background: #dcfce7; color: #15803d; }}
    .badge-beta {{ background: #f3e8ff; color: #7e22ce; }}
    .badge-prerelease {{ background: #ffe4e6; color: #be123c; }}
    .download-btn {{ display: inline-block; padding: 8px 14px; background: #176b4d; color: white !important; border-radius: 6px; text-decoration: none; font-weight: 700; text-align: center; font-size: 0.85rem; }}
    .count {{ color: #65736d; margin-bottom: 14px; font-weight: 500; }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>⚡ Morphe Root Hub</h1>
      <p style="margin: 0; color: #53635d;">37+ Root Patched APKs & 277 Community Bundles</p>
    </div>
    <div style="display: flex; gap: 8px;">
      <a href="index.html" class="nav-btn">← Back to Root Catalog</a>
      <a href="https://krishna3163.github.io/OpenDiscover/#/morphe" target="_blank" class="nav-btn primary">OpenDiscover ↗</a>
    </div>
  </header>

  <div class="tabs">
    <button id="tab-apps" class="tab-btn active" onclick="switchTab('apps')">📱 Root Patched APKs (37)</button>
    <button id="tab-bundles" class="tab-btn" onclick="switchTab('bundles')">📦 Community Bundles (277)</button>
  </div>

  <div class="controls">
    <input id="search" type="search" placeholder="🔍 Search apps, package, bundle source, patches..." oninput="handleSearch()">
  </div>

  <div id="channel-chips" class="filter-chips">
    <button class="chip active" onclick="setChannel('all')">All Channels</button>
    <button class="chip" onclick="setChannel('autobuild')">🚀 AutoBuild</button>
    <button class="chip" onclick="setChannel('autogenerated')">⚡ AutoGenerated</button>
    <button class="chip" onclick="setChannel('release')">📦 Release</button>
    <button class="chip" onclick="setChannel('beta')">🧪 Beta</button>
    <button class="chip" onclick="setChannel('prerelease')">🚧 PreRelease</button>
  </div>

  <div class="count" id="count"></div>
  <section class="grid" id="grid"></section>

  <script>
    const APPS = {json.dumps(apps_json)};
    const BUNDLES = {json.dumps(bundles_json)};
    let currentTab = 'apps';
    let currentChannel = 'all';

    function switchTab(t) {{
      currentTab = t;
      document.getElementById('tab-apps').className = t === 'apps' ? 'tab-btn active' : 'tab-btn';
      document.getElementById('tab-bundles').className = t === 'bundles' ? 'tab-btn active' : 'tab-btn';
      document.getElementById('channel-chips').style.display = t === 'apps' ? 'flex' : 'none';
      render();
    }}

    function setChannel(c) {{
      currentChannel = c;
      document.querySelectorAll('.chip').forEach(btn => {{
        btn.classList.toggle('active', btn.innerText.toLowerCase().includes(c) || (c==='all' && btn.innerText==='All Channels'));
      }});
      render();
    }}

    function handleSearch() {{ render(); }}

    function copyHash(btn, h) {{
      navigator.clipboard.writeText(h).then(() => {{
        const old = btn.innerText;
        btn.innerText = 'Copied!';
        setTimeout(() => {{ btn.innerText = old; }}, 1500);
      }});
    }}

    function render() {{
      const q = document.getElementById('search').value.toLowerCase().trim();
      const grid = document.getElementById('grid');
      grid.innerHTML = '';

      if (currentTab === 'apps') {{
        const filtered = APPS.filter(a => {{
          const chMatch = currentChannel === 'all' || a.channel === currentChannel || a.build_type === currentChannel;
          const qMatch = !q || a.name.toLowerCase().includes(q) || a.package_name.toLowerCase().includes(q) || a.bundle_name.toLowerCase().includes(q) || a.patches.some(p => p.toLowerCase().includes(q));
          return chMatch && qMatch;
        }});
        document.getElementById('count').innerText = `${{filtered.length}} root apps available`;
        filtered.forEach(a => {{
          let bClass = 'badge-autobuild';
          let bLabel = 'AutoBuild';
          if (a.channel === 'beta') {{ bClass = 'badge-beta'; bLabel = 'Beta'; }}
          else if (a.channel === 'prerelease') {{ bClass = 'badge-prerelease'; bLabel = 'PreRelease'; }}
          else if (a.build_type === 'autogenerated') {{ bClass = 'badge-autogenerated'; bLabel = 'AutoGenerated'; }}
          else if (a.build_type === 'release') {{ bClass = 'badge-release'; bLabel = 'Release'; }}

          const card = document.createElement('div');
          card.className = 'card';
          card.innerHTML = `
            <div>
              <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:6px;">
                <h2>${{a.name}}</h2>
                <span class="badge ${{bClass}}">${{bLabel}}</span>
              </div>
              <small style="color:#65736d; font-family:monospace; display:block; margin-bottom:8px;">${{a.package_name}}</small>
              <p style="font-size:0.88rem; color:#42514a; margin:0 0 10px;"><strong>v${{a.version}}</strong> · via <a href="https://github.com/${{a.bundle_repo}}" target="_blank" style="color:#176b4d;">${{a.bundle_name}}</a></p>
              <div style="background:#f8faf9; padding:10px; border-radius:6px; font-size:0.84rem;">
                <strong>Key Patches:</strong>
                <ul style="margin:4px 0 0 16px; padding:0;">${{a.patches.slice(0, 3).map(p=>`<li>${{p}}</li>`).join('')}}</ul>
              </div>
            </div>
            <div style="display:flex; justify-content:space-between; align-items:center; margin-top:8px;">
              <button onclick="copyHash(this, '${{a.sha256}}')" style="padding:6px 10px; font-size:0.78rem; border:1px solid #cbd5d1; background:white; border-radius:4px; cursor:pointer;">SHA-256</button>
              <a href="${{a.download_url}}" target="_blank" class="download-btn">⬇️ Download APK</a>
            </div>
          `;
          grid.appendChild(card);
        }});
      }} else {{
        const filtered = BUNDLES.filter(b => {{
          return !q || b.name.toLowerCase().includes(q) || b.author.toLowerCase().includes(q) || b.repo.toLowerCase().includes(q) || b.targetApps.some(t => t.toLowerCase().includes(q));
        }});
        document.getElementById('count').innerText = `${{filtered.length}} patch bundles available`;
        filtered.forEach(b => {{
          const card = document.createElement('div');
          card.className = 'card';
          card.innerHTML = `
            <div>
              <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:6px;">
                <h2>${{b.name}}</h2>
                <span class="badge badge-release">★ ${{b.stars}}</span>
              </div>
              <small style="color:#65736d; display:block; margin-bottom:8px;">by ${{b.author}}</small>
              <p style="font-size:0.86rem; color:#42514a;"><strong>Targets (${{b.patchCount}} patches):</strong> ${{b.targetApps.slice(0,3).join(', ')}}</p>
            </div>
            <div style="display:flex; justify-content:space-between; align-items:center; margin-top:10px;">
              <a href="https://github.com/${{b.repo}}" target="_blank" style="color:#176b4d; font-size:0.85rem; font-weight:600; text-decoration:none;">GitHub ↗</a>
              <a href="${{b.deepLink}}" class="download-btn" style="background:#0369a1;">➕ Add to Morphe</a>
            </div>
          `;
          grid.appendChild(card);
        }});
      }}
    }}

    render();
  </script>
</body>
</html>
"""


def update_all_morphe_pages(
    releases_db: dict[str, Any],
    status_db: dict[str, Any],
    apps_config: list[dict[str, Any]],
) -> None:
    readme_table = generate_morphe_readme_table(releases_db, status_db, apps_config)
    update_readme_content(readme_table)

    DOCS_MORPHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    docs_content = generate_morphe_docs_page(releases_db, status_db, apps_config)
    DOCS_MORPHE_PATH.write_text(docs_content, encoding="utf-8")
    ROOT_MORPHE_PATH.write_text(docs_content, encoding="utf-8")
    logger.info("Updated %s and %s", DOCS_MORPHE_PATH, ROOT_MORPHE_PATH)

    SITE_MORPHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    site_content = generate_morphe_site_page(releases_db, status_db, apps_config)
    SITE_MORPHE_PATH.write_text(site_content, encoding="utf-8")
    logger.info("Updated %s", SITE_MORPHE_PATH)


def update_readme_content(new_table: str) -> bool:
    """Safely updates or injects the Morphe auto-generated table into README.md."""
    if not README_PATH.exists():
        logger.error("README.md not found at %s", README_PATH)
        return False

    content = README_PATH.read_text(encoding="utf-8")

    if START_MARKER in content and END_MARKER in content:
        start_idx = content.find(START_MARKER)
        end_idx = content.find(END_MARKER) + len(END_MARKER)
        updated_content = content[:start_idx] + START_MARKER + new_table + END_MARKER + content[end_idx:]
    else:
        insert_marker = "<!-- AUTO-GENERATED-APPS-START -->"
        if insert_marker in content:
            idx = content.find(insert_marker)
            updated_content = (
                content[:idx]
                + START_MARKER + new_table + END_MARKER + "\n\n"
                + content[idx:]
            )
        else:
            updated_content = content + "\n\n" + START_MARKER + new_table + END_MARKER + "\n"

    if updated_content != content:
        README_PATH.write_text(updated_content, encoding="utf-8")
        logger.info("README.md updated with latest Morphe generated APKs table.")
        return True
    return False




def sync_morphe_apps(
    target_app: Optional[str] = None,
    dry_run: bool = False,
    force_rebuild: bool = False,
    channel_filter: Optional[str] = None,
    build_type_filter: Optional[str] = None,
) -> None:
    config = load_config()
    apps_data = config.get("apps", [])
    defaults = config.get("defaults", {})

    releases_db = load_json_db(RELEASES_DB_PATH)
    status_db = load_json_db(STATUS_DB_PATH)

    bundle_client = MorpheBundleClient(cache_file=BUNDLES_CACHE_PATH)
    bundle_client.fetch_catalog()

    patcher = MorphePatcher()
    api = GitHubAPI()
    mirror_repo = get_mirror_repo()

    updated_count = 0

    with tempfile.TemporaryDirectory(prefix="morphe_build_") as tmp_dir:
        patcher.locate_or_download_cli(tmp_dir)

        for raw_app in apps_data:
            slug = raw_app.get("slug", "")
            if not slug or not raw_app.get("enabled", True):
                continue
            if target_app and target_app not in (slug, raw_app.get("package_name", "")):
                continue

            channel = raw_app.get("channel", defaults.get("channel", "stable")).lower()
            if channel_filter and channel_filter.lower() != "all" and channel != channel_filter.lower():
                continue

            build_type = raw_app.get("build_type", defaults.get("build_type", "autobuild")).lower()
            if build_type_filter and build_type_filter.lower() != "all" and build_type != build_type_filter.lower():
                continue

            app = MorpheAppConfig(
                name=raw_app["name"],
                slug=slug,
                package_name=raw_app["package_name"],
                bundle_repo=raw_app["bundle_repo"],
                bundle_name=raw_app.get("bundle_name", ""),
                category=raw_app.get("category", "Tools & Utilities"),
                app_type=raw_app.get("app_type", defaults.get("app_type", "root")),
                build_type=build_type,
                channel=channel,
                recommended_version=raw_app.get("recommended_version", "latest"),
                patches=raw_app.get("patches", []),
                enabled=raw_app.get("enabled", True),
                base_apk_url=raw_app.get("base_apk_url"),
                icon_url=raw_app.get("icon_url", ""),
                description=raw_app.get("description", ""),
            )

            # Determine target version
            version = app.recommended_version
            if version == "latest":
                compat_versions = bundle_client.get_compatible_versions(app.package_name)
                if compat_versions:
                    version = compat_versions[0]
                else:
                    version = "1.0.0"

            release_tag = make_morphe_tag(app.slug, version, app.channel)
            prev_entry = releases_db.get(app.slug)

            # Check if rebuild needed
            needs_build = force_rebuild
            if not needs_build:
                if not prev_entry:
                    needs_build = True
                elif prev_entry.get("version") != version or prev_entry.get("release_tag") != release_tag:
                    needs_build = True

            logger.info("Processing Morphe App: %s (%s) v%s [Needs Build: %s]", app.name, app.slug, version, needs_build)

            if not needs_build:
                continue

            # 1. Fetch / synthesize base APK
            try:
                base_apk = BaseApkManager.fetch_base_apk(
                    app.package_name, version, tmp_dir, source_url=app.base_apk_url
                )
            except Exception as e:
                logger.error("Failed to acquire base APK for %s: %s", app.slug, e)
                status_db[app.slug] = {
                    "last_attempt": datetime.now(timezone.utc).isoformat(),
                    "status": "failed",
                    "error": str(e),
                }
                continue

            # 2. Acquire patch bundle
            bundle_mpp = patcher.fetch_patch_bundle(app.bundle_repo, tmp_dir)
            if not bundle_mpp:
                logger.warning("Could not fetch bundle for %s", app.bundle_repo)

            # 3. Patch APK
            output_apk_name = f"{app.slug}-{version}.apk"
            output_apk_path = os.path.join(tmp_dir, output_apk_name)
            success, msg = patcher.patch_apk(base_apk, bundle_mpp, output_apk_path, app.patches)

            if not success:
                logger.error("Patching failed for %s: %s", app.slug, msg)
                status_db[app.slug] = {
                    "last_attempt": datetime.now(timezone.utc).isoformat(),
                    "status": "failed",
                    "error": msg,
                }
                continue

            # 4. Generate Checksum
            sha256 = BaseApkManager.calculate_sha256(output_apk_path)
            file_size = os.path.getsize(output_apk_path)
            checksum_path = output_apk_path + ".sha256"
            with open(checksum_path, "w", encoding="utf-8") as f:
                f.write(f"{sha256}  {output_apk_name}\n")

            logger.info("Successfully produced %s (SHA-256: %s, %d bytes)", output_apk_name, sha256[:12], file_size)

            # 5. Publish GitHub Release (if not dry_run and repository configured)
            if not dry_run and mirror_repo:
                is_prerelease = app.channel in ("beta", "prerelease")
                rel_title = f"{app.name} {version} ({app.channel.capitalize()})"
                rel_body = generate_morphe_release_body(
                    app, version, bundle_client.get_bundle_by_repo(app.bundle_repo), sha256, file_size, output_apk_name
                )
                try:
                    logger.info("Publishing GitHub release %s in %s", release_tag, mirror_repo)
                    existing_rel = api.get_repo_release_by_tag(mirror_repo, release_tag)
                    if existing_rel:
                        logger.info("Release %s already exists. Updating assets.", release_tag)
                        upload_url = existing_rel.get("upload_url", "")
                    else:
                        rel = api.create_release(
                            mirror_repo, release_tag, rel_title, rel_body, prerelease=is_prerelease
                        )
                        upload_url = rel.get("upload_url", "")

                    if upload_url:
                        api.upload_release_asset(upload_url, output_apk_path)
                        api.upload_release_asset(upload_url, checksum_path)
                except Exception as e:
                    logger.error("Error creating/uploading release %s: %s", release_tag, e)

            # 6. UPDATE & REPLACE IN DATABASE: Replaces previous release entry cleanly
            now_iso = datetime.now(timezone.utc).isoformat()
            releases_db[app.slug] = {
                "name": app.name,
                "slug": app.slug,
                "package_name": app.package_name,
                "version": version,
                "release_tag": release_tag,
                "bundle_repo": app.bundle_repo,
                "bundle_name": app.bundle_name,
                "channel": app.channel,
                "build_type": app.build_type,
                "app_type": app.app_type,
                "sha256": sha256,
                "file_size": file_size,
                "filename": output_apk_name,
                "updated_at": now_iso,
                "patches": app.patches,
            }
            status_db[app.slug] = {
                "last_attempt": now_iso,
                "status": "success",
                "version": version,
                "release_tag": release_tag,
            }
            updated_count += 1

    # Save databases
    save_json_db(RELEASES_DB_PATH, releases_db)
    save_json_db(STATUS_DB_PATH, status_db)

    # Update README table and dedicated pages (docs/MORPHE.md, site/morphe.html)
    update_all_morphe_pages(releases_db, status_db, apps_data)
    logger.info("Morphe synchronization completed. Updated %d apps.", updated_count)


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Synchronize and Auto-Build Morphe Patched Apps")
    parser.add_argument("--app", help="Application slug or package name to sync")
    parser.add_argument("--dry-run", action="store_true", help="Build without publishing releases")
    parser.add_argument("--force-rebuild", action="store_true", help="Force rebuild regardless of existing tags")
    parser.add_argument("--channel", help="Filter by channel: stable, beta, prerelease, all")
    parser.add_argument("--build-type", help="Filter by build type: autobuild, autogenerated, release, all")
    args = parser.parse_args()

    sync_morphe_apps(
        target_app=args.app,
        dry_run=args.dry_run,
        force_rebuild=args.force_rebuild,
        channel_filter=args.channel,
        build_type_filter=args.build_type,
    )


if __name__ == "__main__":
    main()
