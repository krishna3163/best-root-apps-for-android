"""Main Root Apps & Modules synchronization script.

Usage:
    python scripts/sync.py                         # Sync all enabled apps
    python scripts/sync.py --app magisk            # Sync single app
    python scripts/sync.py --dry-run               # Check without publishing
    python scripts/sync.py --app magisk --dry-run   # Dry-run single app
    python scripts/sync.py --force-resync          # Force re-evaluation
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from github_api import GitHubAPI
from apk import (
    calculate_sha256,
    create_checksum_file,
    detect_architecture,
    extract_apk_metadata,
    filter_assets,
    normalize_filename,
    select_best_assets,
    validate_apk,
)
from release import (
    extract_version,
    generate_release_body,
    make_mirror_tag,
    make_release_title,
    pick_release,
)
from metadata import (
    get_synced_asset_names,
    is_already_synced,
    load_releases_db,
    load_status_db,
    save_releases_db,
    save_status_db,
    update_release_entry,
    update_status,
)
from sync_readme import update_readme

logger = logging.getLogger("apk-sync")


def setup_logging() -> None:
    """Configure structured logging with UTF-8 support."""
    stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "apps.json"


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        logger.error("Configuration file not found: %s", CONFIG_PATH)
        sys.exit(1)
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def get_app_config(app_entry: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    merged.update({k: v for k, v in app_entry.items() if v is not None})
    return merged


def get_mirror_repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY", "")


def sync_app(
    app: dict[str, Any],
    api: GitHubAPI,
    releases_db: dict[str, Any],
    status_db: dict[str, Any],
    dry_run: bool = False,
    force_resync: bool = False,
) -> str:
    slug = app["slug"]
    name = app["name"]
    repo = app["repository"]
    strategy = app.get("release_strategy", "latest-stable")

    logger.info("=" * 56)
    logger.info("Synchronizing: %s", name)
    logger.info("Repository: %s", repo)
    logger.info("=" * 56)

    if not app.get("enabled", True):
        logger.info("⏭️  App is disabled. Skipping.")
        update_status(status_db, slug, "disabled")
        return "disabled"

    # 1. Fetch releases
    logger.info("Checking releases (strategy: %s)...", strategy)
    try:
        releases = api.get_releases(repo)
    except Exception as exc:
        logger.error("❌ Failed to fetch releases for %s: %s", repo, exc)
        update_status(status_db, slug, "failed", error=str(exc))
        return "failed"

    if not releases:
        logger.warning("⚠️  No releases found for %s", repo)
        update_status(status_db, slug, "no-apk")
        return "no-apk"

    # 2. Pick release
    release = pick_release(releases, strategy)
    if not release:
        logger.warning("⚠️  No matching release for strategy '%s'", strategy)
        update_status(status_db, slug, "no-apk")
        return "no-apk"

    source_release_id = release["id"]
    source_tag = release.get("tag_name", "")
    version = extract_version(release)
    published_at = release.get("published_at", "")

    logger.info("Latest release: %s (ID: %d)", version, source_release_id)

    # 3. Check duplicate / partial sync
    if not force_resync and is_already_synced(releases_db, slug, source_release_id):
        existing_assets = get_synced_asset_names(releases_db, slug)
        release_assets = release.get("assets", [])
        matched_assets = filter_assets(
            release_assets,
            app.get("asset_patterns"),
            app.get("exclude_patterns"),
        )
        matched_assets = select_best_assets(matched_assets, app.get("preferred_architectures"))
        missing = [a for a in matched_assets if a["name"] not in existing_assets]

        if not missing:
            logger.info("✅ Already synchronized. No updates needed.")
            update_status(status_db, slug, "no-update", latest_version=version)
            return "no-update"
        else:
            logger.info("🔧 Partial sync detected. %d missing asset(s).", len(missing))
    else:
        missing = None

    # 4. Filter assets
    release_assets = release.get("assets", [])
    logger.info("Found %d release assets.", len(release_assets))

    valid_assets = filter_assets(
        release_assets,
        app.get("asset_patterns"),
        app.get("exclude_patterns"),
    )

    if not valid_assets:
        logger.warning("⚠️  No APK / ZIP assets found in release %s", version)
        update_status(status_db, slug, "no-apk", latest_version=version)
        return "no-apk"

    logger.info("Found %d candidate asset(s).", len(valid_assets))

    # 5. Select assets
    selected = select_best_assets(valid_assets, app.get("preferred_architectures"))
    if missing is not None:
        selected = [a for a in selected if a["name"] not in get_synced_asset_names(releases_db, slug)]

    if not selected:
        logger.info("✅ All assets already uploaded.")
        update_status(status_db, slug, "no-update", latest_version=version)
        return "no-update"

    for asset in selected:
        logger.info("  📦 %s", asset["name"])

    # 6. Download, validate, checksum
    mirror_tag = make_mirror_tag(slug, version)
    mirror_repo = get_mirror_repo()
    tmp_dir = tempfile.mkdtemp(prefix="root-sync-")
    uploaded_assets: list[dict[str, Any]] = []

    try:
        for asset in selected:
            asset_name = asset["name"]
            asset_id = asset.get("id", 0)

            logger.info("")
            logger.info("Downloading: %s", asset_name)

            final_name = normalize_filename(asset_name, slug, version)
            dest_path = os.path.join(tmp_dir, final_name)

            try:
                download_url = asset.get("browser_download_url") or asset.get("url", "")
                api.download_asset(download_url, dest_path)
            except Exception as exc:
                logger.error("❌ Download failed for %s: %s", asset_name, exc)
                continue

            logger.info("Download complete.")
            logger.info("Validating package...")
            is_valid, msg = validate_apk(dest_path)
            if not is_valid:
                logger.error("❌ Validation failed: %s", msg)
                continue
            logger.info("✅ %s", msg)

            sha256 = calculate_sha256(dest_path)
            logger.info("SHA256: %s", sha256)
            checksum_path = create_checksum_file(dest_path, sha256)

            apk_meta = extract_apk_metadata(dest_path)
            architecture = detect_architecture(asset_name)
            if apk_meta.get("native_architectures"):
                architecture = ", ".join(apk_meta["native_architectures"])

            asset_info = {
                "source_asset_id": asset_id,
                "filename": final_name,
                "original_filename": asset_name,
                "sha256": sha256,
                "file_size": os.path.getsize(dest_path),
                "architecture": architecture,
            }
            uploaded_assets.append(asset_info)

            if dry_run:
                logger.info("")
                logger.info("[DRY RUN] %s", name)
                logger.info("  Source: %s", repo)
                logger.info("  Version: %s", version)
                logger.info("  New asset detected: %s", final_name)
                logger.info("  SHA256: %s", sha256)
                logger.info("  Architecture: %s", architecture)
                logger.info("  Would create release: %s", mirror_tag)
                logger.info("  Would upload: %s", final_name)
                logger.info("  Would upload: %s", os.path.basename(checksum_path))
                continue

        if dry_run:
            update_status(status_db, slug, "no-update", latest_version=version)
            return "updated"

        if not uploaded_assets:
            logger.error("❌ No assets were successfully processed for %s", name)
            update_status(status_db, slug, "failed", latest_version=version, error="All asset downloads/validations failed")
            return "failed"

        # 7. Create or find mirror release
        if not mirror_repo:
            logger.warning("⚠️  GITHUB_REPOSITORY not set. Skipping release creation.")
            update_release_entry(
                releases_db, slug, repo, source_release_id, source_tag,
                published_at, uploaded_assets, mirror_tag,
            )
            update_status(status_db, slug, "synced", latest_version=version)
            return "updated"

        logger.info("")
        logger.info("Checking mirror release: %s", mirror_tag)
        existing_release = api.get_repo_release_by_tag(mirror_repo, mirror_tag)
        release_url = ""

        if existing_release:
            logger.info("Mirror release already exists. Uploading missing assets.")
            upload_url = existing_release["upload_url"]
            release_url = existing_release.get("html_url", "")
            existing_asset_names = {a["name"] for a in existing_release.get("assets", [])}
        else:
            logger.info("Creating release: %s", mirror_tag)
            title = make_release_title(name, version)
            license_info = ""
            try:
                repo_info = api.get_repo_info(repo)
                if repo_info and repo_info.get("license"):
                    license_info = repo_info["license"].get("name", "")
            except Exception:
                pass

            body = generate_release_body(
                name, version, repo, source_tag, uploaded_assets, license_info
            )

            try:
                new_release = api.create_release(mirror_repo, mirror_tag, title, body)
                upload_url = new_release["upload_url"]
                release_url = new_release.get("html_url", "")
                logger.info("✅ Release created: %s", release_url)
            except Exception as exc:
                logger.error("❌ Failed to create release: %s", exc)
                update_status(status_db, slug, "failed", latest_version=version, error=str(exc))
                return "failed"

            existing_asset_names = set()

        # 8. Upload assets
        for asset_info in uploaded_assets:
            fname = asset_info["filename"]
            fpath = os.path.join(tmp_dir, fname)
            checksum_file = fpath + ".sha256"

            if fname not in existing_asset_names:
                logger.info("Uploading asset: %s", fname)
                try:
                    api.upload_release_asset(upload_url, fpath)
                    logger.info("✅ Asset uploaded.")
                except Exception as exc:
                    logger.error("❌ Failed to upload %s: %s", fname, exc)

            checksum_name = os.path.basename(checksum_file)
            if checksum_name not in existing_asset_names and os.path.exists(checksum_file):
                logger.info("Uploading checksum: %s", checksum_name)
                try:
                    api.upload_release_asset(upload_url, checksum_file, content_type="text/plain")
                    logger.info("✅ Checksum uploaded.")
                except Exception as exc:
                    logger.error("❌ Failed to upload checksum: %s", exc)

        # 9. Update metadata
        update_release_entry(
            releases_db, slug, repo, source_release_id, source_tag,
            published_at, uploaded_assets, mirror_tag, release_url,
        )
        update_status(status_db, slug, "synced", latest_version=version)

        logger.info("")
        logger.info("✅ SUCCESS: %s %s", name, version)
        return "updated"

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> int:
    setup_logging()

    parser = argparse.ArgumentParser(description="Root Apps & Modules Auto-Sync")
    parser.add_argument("--app", type=str, default="", help="Sync only this app slug")
    parser.add_argument("--dry-run", action="store_true", help="Check without publishing")
    parser.add_argument("--force-resync", action="store_true", help="Force re-evaluation")
    args = parser.parse_args()

    logger.info("")
    logger.info("🚀 Best Root Apps & Modules Auto-Sync")
    logger.info("=" * 56)
    if args.dry_run:
        logger.info("🔍 DRY RUN MODE — no changes will be made")
    logger.info("")

    config = load_config()
    defaults = config.get("defaults", {})
    apps = config.get("apps", [])

    if not apps:
        logger.error("No apps configured in %s", CONFIG_PATH)
        return 1

    if args.app:
        apps = [a for a in apps if a.get("slug") == args.app]
        if not apps:
            logger.error("App '%s' not found in configuration.", args.app)
            return 1
        logger.info("Syncing single app: %s", args.app)

    releases_db = load_releases_db()
    status_db = load_status_db()
    api = GitHubAPI()

    results = {"updated": 0, "no-update": 0, "no-apk": 0, "failed": 0, "disabled": 0}
    total = len(apps)

    for i, app_entry in enumerate(apps, 1):
        app = get_app_config(app_entry, defaults)
        if not app.get("slug") or not app.get("repository"):
            logger.warning("⚠️  Skipping app with missing slug or repository")
            results["failed"] += 1
            continue

        logger.info("")
        logger.info("[%d/%d] Processing: %s", i, total, app.get("name", app["slug"]))

        try:
            result = sync_app(
                app, api, releases_db, status_db,
                dry_run=args.dry_run,
                force_resync=args.force_resync,
            )
            results[result] = results.get(result, 0) + 1
        except Exception as exc:
            logger.error("❌ Unexpected error for %s: %s", app.get("name", "?"), exc)
            update_status(status_db, app["slug"], "failed", error=str(exc))
            results["failed"] += 1

    if not args.dry_run:
        save_releases_db(releases_db)
        save_status_db(status_db)
        logger.info("")
        logger.info("💾 Metadata saved.")

        apps_for_readme = [get_app_config(a, defaults) for a in config.get("apps", [])]
        readme_changed = update_readme(releases_db, status_db, apps_for_readme)
        if readme_changed:
            logger.info("📝 README.md updated.")

    logger.info("")
    logger.info("=" * 56)
    logger.info("SYNC SUMMARY")
    logger.info("=" * 56)
    logger.info("Packages checked:    %d", total)
    logger.info("Updated:             %d", results.get("updated", 0))
    logger.info("Already up-to-date:  %d", results.get("no-update", 0))
    logger.info("No Package found:    %d", results.get("no-apk", 0))
    logger.info("Failed:              %d", results.get("failed", 0))
    logger.info("Disabled:            %d", results.get("disabled", 0))
    logger.info("=" * 56)

    if results.get("failed", 0) > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
