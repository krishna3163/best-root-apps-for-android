"""Tests for metadata tracking in best-root-apps-for-android."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from metadata import (
    get_synced_asset_names,
    is_already_synced,
    update_release_entry,
    update_status,
)


class TestMetadata(unittest.TestCase):
    def test_duplicate_detection(self):
        db = {"magisk": {"source_release_id": 123}}
        self.assertTrue(is_already_synced(db, "magisk", 123))
        self.assertFalse(is_already_synced(db, "magisk", 999))


if __name__ == "__main__":
    unittest.main()
