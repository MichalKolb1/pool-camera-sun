"""Focused standard-library tests for private sample storage."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "pool_camera_sun"
    / "sample_store.py"
)
SPEC = importlib.util.spec_from_file_location(
    "pool_camera_sun_sample_store", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
sample_store = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sample_store)


class SampleStoreTest(unittest.TestCase):
    """Verify bounded pair storage and safe retrieval."""

    def setUp(self) -> None:
        """Create an isolated private storage root."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.store = sample_store.SampleStore(
            self.root, ("sunny", "not_sunny"), 2
        )

    def test_store_list_retrieve_and_prune_pairs(self) -> None:
        """Keep only the newest configured number of image/metadata pairs."""
        stored = []
        for index in range(3):
            stored.append(
                self.store.store_sample(
                    "sunny",
                    f"image-{index}".encode(),
                    "image/jpeg",
                    {
                        "captured_at": f"2026-07-23T09:00:0{index}+00:00",
                        "algorithm": {"metrics": {"sun_score": index / 10}},
                    },
                )
            )

        listed = self.store.list_samples()
        self.assertEqual(
            [item["id"] for item in listed],
            [stored[2]["id"], stored[1]["id"]],
        )
        self.assertFalse((self.root / "sunny" / f"{stored[0]['id']}.json").exists())
        self.assertFalse((self.root / "sunny" / f"{stored[0]['id']}.jpg").exists())
        image, content_type = self.store.get_image(stored[2]["id"])
        self.assertEqual(image, b"image-2")
        self.assertEqual(content_type, "image/jpeg")

    def test_rejects_path_traversal_sample_id(self) -> None:
        """Never turn an untrusted route value into a filesystem path."""
        with self.assertRaises(sample_store.InvalidSampleId):
            self.store.get_image("../../secrets")

    def test_rejects_tampered_image_filename(self) -> None:
        """Reject traversal introduced through corrupted metadata."""
        stored = self.store.store_sample(
            "not_sunny",
            b"image",
            "image/png",
            {"captured_at": "2026-07-23T09:00:00+00:00"},
        )
        metadata_path = self.root / "not_sunny" / f"{stored['id']}.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["image"]["filename"] = "../private.png"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

        with self.assertRaises(sample_store.SampleStorageError):
            self.store.get_image(stored["id"])

    def test_rolls_back_new_pair_when_retention_fails(self) -> None:
        """Never exceed the hard limit after a retention error."""
        self.store.store_sample(
            "sunny",
            b"first",
            "image/jpeg",
            {"captured_at": "2026-07-23T09:00:00+00:00"},
        )
        with (
            patch.object(
                self.store,
                "_prune_label",
                side_effect=sample_store.SampleStorageError("retention failed"),
            ),
            self.assertRaises(sample_store.SampleStorageError),
        ):
            self.store.store_sample(
                "sunny",
                b"second",
                "image/jpeg",
                {"captured_at": "2026-07-23T09:00:01+00:00"},
            )

        self.assertEqual(len(self.store.list_samples()), 1)
        self.assertEqual(len(list((self.root / "sunny").glob("*.jpg"))), 1)


if __name__ == "__main__":
    unittest.main()
