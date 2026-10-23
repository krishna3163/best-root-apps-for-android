"""Tests for Morphe patch bundle client, patcher, and sync automation."""

import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from morphe_builder import (
    BaseApkManager,
    MorpheAppConfig,
    MorpheBundleClient,
    MorphePatcher,
)
from morphe_sync import (
    generate_morphe_readme_table,
    make_morphe_tag,
    update_readme_content,
)


class TestMorpheBundleClient(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        dummy_data = {
            "bundles": [
                {
                    "source": "github",
                    "repo": "test/morphe-patches",
                    "name": "TestBundle",
                    "targetApps": ["com.example.test"],
                    "patchCount": 5,
                }
            ],
            "store": {
                "com.example.test": {
                    "name": "Test App",
                    "category": "Tools",
                    "description": "A test application",
                }
            },
            "compatibilities": [
                {
                    "packageName": "com.example.test",
                    "targets": [{"version": "2.1.0"}, {"version": "2.0.0"}],
                }
            ],
        }
        with open(self.tmp.name, "w", encoding="utf-8") as f:
            json.dump(dummy_data, f)
        self.client = MorpheBundleClient(cache_file=Path(self.tmp.name))

    def tearDown(self):
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_fetch_catalog_from_cache(self):
        data = self.client.fetch_catalog()
        self.assertIn("bundles", data)
        self.assertEqual(len(data["bundles"]), 1)

    def test_get_bundle_by_repo(self):
        bundle = self.client.get_bundle_by_repo("test/morphe-patches")
        self.assertIsNotNone(bundle)
        self.assertEqual(bundle["name"], "TestBundle")

    def test_get_app_store_info(self):
        info = self.client.get_app_store_info("com.example.test")
        self.assertIsNotNone(info)
        self.assertEqual(info["name"], "Test App")

    def test_get_compatible_versions(self):
        versions = self.client.get_compatible_versions("com.example.test")
        self.assertEqual(versions, ["2.1.0", "2.0.0"])


class TestBaseApkManager(unittest.TestCase):
    def test_validate_apk_valid(self):
        with tempfile.NamedTemporaryFile(suffix=".apk", delete=False) as f:
            path = f.name
        try:
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("AndroidManifest.xml", "<manifest/>")
            valid, msg = BaseApkManager.validate_apk(path)
            self.assertTrue(valid, msg)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_validate_apk_invalid(self):
        with tempfile.NamedTemporaryFile(suffix=".apk", delete=False) as f:
            path = f.name
        try:
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("test.txt", "not manifest")
            valid, msg = BaseApkManager.validate_apk(path)
            self.assertFalse(valid)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_fetch_base_apk_synthesis(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            apk_path = BaseApkManager.fetch_base_apk("com.test.app", "1.0.0", tmp_dir)
            self.assertTrue(os.path.exists(apk_path))
            valid, msg = BaseApkManager.validate_apk(apk_path)
            self.assertTrue(valid, msg)


class TestMorphePatcher(unittest.TestCase):
    def test_patch_apk_internal_engine(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_apk = BaseApkManager.fetch_base_apk("com.test.app", "1.0.0", tmp_dir)
            bundle_mpp = os.path.join(tmp_dir, "test.mpp")
            with open(bundle_mpp, "wb") as f:
                f.write(b"MPP_TEST_BUNDLE")
            out_apk = os.path.join(tmp_dir, "patched.apk")

            patcher = MorphePatcher()
            success, msg = patcher.patch_apk(base_apk, bundle_mpp, out_apk, ["Hide ads"])
            self.assertTrue(success, msg)
            self.assertTrue(os.path.exists(out_apk))

            # Verify injected patch metadata
            with zipfile.ZipFile(out_apk, "r") as zf:
                self.assertIn("assets/morphe_patch_info.json", zf.namelist())


class TestMorpheSyncTagsAndTable(unittest.TestCase):
    def test_make_morphe_tag(self):
        self.assertEqual(make_morphe_tag("morphe-youtube", "19.16.39", "stable"), "morphe-youtube-19.16.39")
        self.assertEqual(make_morphe_tag("morphe-twitter", "10.0.0", "beta"), "morphe-twitter-10.0.0-beta")
        self.assertEqual(make_morphe_tag("morphe-insta", "5.0.0", "prerelease"), "morphe-insta-5.0.0-prerelease")

    def test_generate_morphe_readme_table(self):
        apps_config = [
            {
                "name": "YouTube (Morphe)",
                "slug": "morphe-youtube",
                "package_name": "com.google.android.youtube",
                "bundle_repo": "MorpheApp/morphe-patches",
                "channel": "stable",
                "build_type": "autobuild",
                "enabled": True,
            }
        ]
        releases_db = {
            "morphe-youtube": {
                "version": "19.16.39",
                "release_tag": "morphe-youtube-19.16.39",
                "channel": "stable",
                "build_type": "autobuild",
                "sha256": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
                "updated_at": "2026-10-23T20:00:00Z",
            }
        }
        table = generate_morphe_readme_table(releases_db, {}, apps_config)
        self.assertIn("Morphe Patched Apps & AutoBuilds", table)
        self.assertIn("YouTube (Morphe)", table)
        self.assertIn("morphe-youtube-19.16.39", table)
        self.assertIn("Quick Filter by Status", table)
        self.assertIn("AutoBuild", table)


if __name__ == "__main__":
    unittest.main()
