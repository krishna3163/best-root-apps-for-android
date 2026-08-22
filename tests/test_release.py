"""Tests for release detection and metadata generation."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from release import (
    extract_version,
    generate_release_body,
    make_mirror_tag,
    make_release_title,
    pick_release,
)


class TestPickRelease(unittest.TestCase):
    def _release(self, tag, draft=False, prerelease=False, release_id=1):
        return {
            "id": release_id,
            "tag_name": tag,
            "name": f"Release {tag}",
            "draft": draft,
            "prerelease": prerelease,
            "assets": [],
        }

    def test_latest_stable(self):
        releases = [
            self._release("v2.0.0", prerelease=True, release_id=2),
            self._release("v1.0.0", release_id=1),
        ]
        result = pick_release(releases, "latest-stable")
        self.assertIsNotNone(result)
        self.assertEqual(result["tag_name"], "v1.0.0")


class TestMirrorTag(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(make_mirror_tag("magisk", "v27.0"), "magisk-v27.0")


if __name__ == "__main__":
    unittest.main()
