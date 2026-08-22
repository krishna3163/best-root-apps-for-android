"""Asset detection, selection, validation, and checksum utilities for root apps & modules."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import zipfile
from typing import Any, Optional

logger = logging.getLogger("apk-sync")

ARCH_PRIORITY = ["universal", "arm64-v8a", "arm64", "armeabi-v7a", "armeabi", "x86_64", "x86"]
DEFAULT_EXCLUDE_PATTERNS = [r"(?i).*debug.*", r"(?i).*unsigned.*", r"(?i).*test.*", r"(?i).*source.*"]
REJECT_EXTENSIONS = {".tar.gz", ".gz", ".jar", ".txt", ".json", ".sha256", ".asc", ".sig", ".md5", ".pem", ".aar"}


def is_valid_asset(filename: str) -> bool:
    """Return True if filename is an APK or ZIP archive candidate."""
    lower = filename.lower()
    return lower.endswith(".apk") or lower.endswith(".zip")


def matches_patterns(filename: str, patterns: list[str]) -> bool:
    """Return True if filename matches any pattern."""
    for pattern in patterns:
        if re.search(pattern, filename, re.IGNORECASE):
            return True
    return False


def detect_architecture(filename: str) -> str:
    """Attempt to detect architecture from the filename."""
    lower = filename.lower()
    for arch in ARCH_PRIORITY:
        if arch in lower:
            return arch
    return "universal" if filename.lower().endswith(".zip") else "unknown"


def filter_assets(
    assets: list[dict[str, Any]],
    asset_patterns: Optional[list[str]] = None,
    exclude_patterns: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Filter release assets to only valid APK / ZIP candidate files."""
    if asset_patterns is None:
        asset_patterns = [r"(?i).*\.(apk|zip)$"]
    if exclude_patterns is None:
        exclude_patterns = list(DEFAULT_EXCLUDE_PATTERNS)

    result: list[dict[str, Any]] = []
    for asset in assets:
        name: str = asset.get("name", "")
        if not matches_patterns(name, asset_patterns):
            continue
        if matches_patterns(name, exclude_patterns):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext in REJECT_EXTENSIONS:
            continue
        if not is_valid_asset(name):
            continue
        result.append(asset)

    return result


def select_best_assets(
    assets: list[dict[str, Any]],
    preferred_architectures: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Select appropriate assets for publication."""
    return assets


def normalize_filename(filename: str, slug: str, version: str) -> str:
    """Produce a safe, normalized filename."""
    safe_chars = set("abcdefghijklmnopqrstuvwxyz0123456789-_.")
    if all(c in safe_chars for c in filename.lower()):
        return filename

    base, ext = os.path.splitext(filename)
    normalized = base.lower()
    normalized = re.sub(r"[^\w\-.]", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized)
    normalized = normalized.strip("-")
    return f"{normalized}{ext.lower()}"


def validate_apk(filepath: str) -> tuple[bool, str]:
    """Validate a downloaded APK or Magisk/KernelSU ZIP module."""
    if not os.path.exists(filepath):
        return False, "File does not exist"

    size = os.path.getsize(filepath)
    if size == 0:
        return False, "File is empty"

    lower = filepath.lower()
    if not (lower.endswith(".apk") or lower.endswith(".zip")):
        return False, "File does not have .apk or .zip extension"

    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            names = zf.namelist()
            if lower.endswith(".apk"):
                if "AndroidManifest.xml" not in names:
                    return False, "No AndroidManifest.xml found (not a valid APK)"
            elif lower.endswith(".zip"):
                # For flashable root/magisk/kernelsu modules
                has_module_prop = any(n in names for n in ("module.prop", "META-INF/com/google/android/update-binary", "system.prop"))
                # Even if general zip, it is a valid zip archive
    except zipfile.BadZipFile:
        return False, "Not a valid ZIP/APK archive"
    except Exception as exc:
        return False, f"Error reading archive: {exc}"

    return True, f"Valid archive ({size:,} bytes)"


def calculate_sha256(filepath: str) -> str:
    """Calculate SHA-256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def create_checksum_file(apk_path: str, sha256_hash: str) -> str:
    """Create a .sha256 checksum sidecar file."""
    checksum_path = apk_path + ".sha256"
    filename = os.path.basename(apk_path)
    with open(checksum_path, "w", encoding="utf-8") as fh:
        fh.write(f"{sha256_hash}  {filename}\n")
    return checksum_path


def extract_apk_metadata(filepath: str) -> dict[str, Any]:
    """Extract basic metadata from an APK or ZIP."""
    metadata: dict[str, Any] = {
        "file_size": os.path.getsize(filepath),
        "sha256": calculate_sha256(filepath),
    }

    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            names = zf.namelist()
            lib_archs = set()
            for name in names:
                if name.startswith("lib/") and "/" in name[4:]:
                    arch = name.split("/")[1]
                    if arch:
                        lib_archs.add(arch)
            if lib_archs:
                metadata["native_architectures"] = sorted(lib_archs)

            if "module.prop" in names:
                metadata["is_root_module"] = True
    except Exception:
        pass

    return metadata
