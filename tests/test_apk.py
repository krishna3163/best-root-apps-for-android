"""Tests for APK and ZIP detection, filtering, validation, and checksum utilities."""

import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from apk import (
    calculate_sha256,
    create_checksum_file,
    detect_architecture,
    filter_assets,
    is_valid_asset,
    normalize_filename,
    select_best_assets,
    validate_apk,
)


class TestIsValidAsset(unittest.TestCase):
    def test_valid_assets(self):
        self.assertTrue(is_valid_asset("app.apk"))
        self.assertTrue(is_valid_asset("module.zip"))
        self.assertTrue(is_valid_asset("magisk-v27.0.apk"))
        self.assertTrue(is_valid_asset("zygisk-next.zip"))

    def test_invalid_extensions(self):
        self.assertFalse(is_valid_asset("source.tar.gz"))
        self.assertFalse(is_valid_asset("module.json"))
        self.assertFalse(is_valid_asset("checksum.sha256"))


class TestFilterAssets(unittest.TestCase):
    def _asset(self, name):
        return {"name": name, "id": 1, "url": "https://example.com"}

    def test_basic_filter(self):
        assets = [self._asset("app.apk"), self._asset("module.zip"), self._asset("source.tar.gz")]
        result = filter_assets(assets)
        self.assertEqual(len(result), 2)

    def test_exclude_patterns(self):
        assets = [self._asset("app-release.apk"), self._asset("app-debug.apk")]
        result = filter_assets(assets)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "app-release.apk")


class TestValidation(unittest.TestCase):
    def test_valid_apk(self):
        with tempfile.NamedTemporaryFile(suffix=".apk", delete=False) as f:
            path = f.name
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("AndroidManifest.xml", "<manifest/>")
        try:
            valid, msg = validate_apk(path)
            self.assertTrue(valid)
        finally:
            os.unlink(path)

    def test_valid_zip_module(self):
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            path = f.name
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("module.prop", "id=test\n")
        try:
            valid, msg = validate_apk(path)
            self.assertTrue(valid)
        finally:
            os.unlink(path)


class TestChecksum(unittest.TestCase):
    def test_sha256(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"root content")
            path = f.name
        try:
            h = calculate_sha256(path)
            self.assertEqual(len(h), 64)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
