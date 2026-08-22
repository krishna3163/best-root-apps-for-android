"""Tests for configuration loading and validation in best-root-apps-for-android."""

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


class TestConfigLoading(unittest.TestCase):
    CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "apps.json"

    def test_config_exists(self):
        self.assertTrue(self.CONFIG_PATH.exists(), "config/apps.json must exist")

    def test_config_valid_json(self):
        with open(self.CONFIG_PATH, encoding="utf-8") as fh:
            config = json.load(fh)
        self.assertIsInstance(config, dict)

    def test_apps_list_present(self):
        with open(self.CONFIG_PATH, encoding="utf-8") as fh:
            config = json.load(fh)
        self.assertIn("apps", config)
        self.assertIsInstance(config["apps"], list)
        self.assertGreater(len(config["apps"]), 0)

    def test_required_fields(self):
        with open(self.CONFIG_PATH, encoding="utf-8") as fh:
            config = json.load(fh)
        for app in config["apps"]:
            self.assertIn("name", app, f"Missing 'name' in app: {app}")
            self.assertIn("slug", app, f"Missing 'slug' in app: {app}")
            self.assertIn("repository", app, f"Missing 'repository' in app: {app}")
            self.assertTrue("/" in app["repository"], f"Invalid repo format: {app['repository']}")

    def test_unique_slugs(self):
        with open(self.CONFIG_PATH, encoding="utf-8") as fh:
            config = json.load(fh)
        slugs = [app["slug"] for app in config["apps"]]
        self.assertEqual(len(slugs), len(set(slugs)), "Slugs must be unique")

    def test_defaults_present(self):
        with open(self.CONFIG_PATH, encoding="utf-8") as fh:
            config = json.load(fh)
        self.assertIn("defaults", config)


if __name__ == "__main__":
    unittest.main()
