"""Tests for secure labeled-sample parameter refinement."""

from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.error import URLError

from PIL import Image

MODULE_PATH = (
    Path(__file__).parents[1] / "tools" / "refine_pool_camera_sun.py"
)
SPEC = importlib.util.spec_from_file_location(
    "refine_pool_camera_sun", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
refinement = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = refinement
SPEC.loader.exec_module(refinement)


def _metrics(p90: float, contrast: float) -> dict[str, float]:
    """Build the production feature shape used by candidate evaluation."""
    return {
        "brightness": p90,
        "p10": p90 - contrast,
        "p90": p90,
        "contrast": contrast,
        "panel_brightness": 100.0,
        "panel_contrast": 20.0,
    }


class _ImageClient:
    """Return one in-memory image without network access."""

    def __init__(self, content: bytes) -> None:
        self._content = content

    def fetch_image(
        self, descriptor: refinement.SampleDescriptor
    ) -> bytes:
        return self._content


class RefinementTest(unittest.TestCase):
    """Verify privacy controls and deterministic safety gates."""

    def test_network_failure_redacts_private_request_data(self) -> None:
        """Never include token, URL, or sample ID in a network error."""
        token = "private-token-value"
        base_url = "https://private-home.example:8123"
        sample_id = "a" * 32
        authorization_headers: list[str | None] = []

        def failing_opener(request: object, **kwargs: object) -> object:
            authorization_headers.append(
                request.get_header("Authorization")
            )
            raise URLError(
                f"{request.full_url} {request.headers} {sample_id}"
            )

        client = refinement.HomeAssistantSampleClient(
            base_url, token, failing_opener
        )
        descriptor = refinement.SampleDescriptor(
            sample_id, "sunny", "image/jpeg"
        )

        with self.assertRaises(refinement.RefinementError) as raised:
            client.fetch_image(descriptor)

        message = str(raised.exception)
        self.assertNotIn(token, message)
        self.assertNotIn(base_url, message)
        self.assertNotIn(sample_id, message)
        self.assertEqual(authorization_headers, ["Bearer " + token])

    def test_rejects_plaintext_token_transport_off_loopback(self) -> None:
        """Require HTTPS before sending a token to a non-loopback host."""
        with self.assertRaises(refinement.RefinementError):
            refinement.HomeAssistantSampleClient(
                "http://home-assistant.example:8123",
                "private-token-value",
            )

    def test_rejects_invalid_api_data(self) -> None:
        """Validate counts, retention, IDs, labels, and content types."""
        valid_item = {
            "id": "a" * 32,
            "manual_label": "sunny",
            "image": {"content_type": "image/jpeg"},
        }
        invalid_payloads = (
            {
                "count": 2,
                "retention_per_label": 100,
                "samples": [valid_item],
            },
            {
                "count": 1,
                "retention_per_label": 101,
                "samples": [valid_item],
            },
            {
                "count": 1,
                "retention_per_label": 100,
                "samples": [{**valid_item, "id": "../../private"}],
            },
            {
                "count": 1,
                "retention_per_label": 100,
                "samples": [{**valid_item, "manual_label": "unknown"}],
            },
            {
                "count": 1,
                "retention_per_label": 100,
                "samples": [
                    {
                        **valid_item,
                        "image": {"content_type": "text/plain"},
                    }
                ],
            },
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(refinement.RefinementError):
                    refinement.validate_manifest(payload)

    def test_returns_insufficient_data_without_parameter_evaluation(self) -> None:
        """Do not recommend classifier changes from small labeled sets."""
        samples = [
            refinement.LabeledFeatures("sunny", _metrics(165, 45))
            for _ in range(refinement.MIN_SAMPLES_PER_LABEL - 1)
        ]
        samples.extend(
            refinement.LabeledFeatures("not_sunny", _metrics(115, 70))
            for _ in range(refinement.MIN_SAMPLES_PER_LABEL)
        )

        summary = refinement.evaluate_samples(samples)

        self.assertEqual(summary["status"], "insufficient_data")
        self.assertFalse(summary["recommendation"]["change_recommended"])
        self.assertNotIn("evaluation", summary)

    def test_recommendation_is_deterministic_and_aggregate_only(self) -> None:
        """Select the same stable candidate regardless of input ordering."""
        private_id = "b" * 32
        samples = [
            refinement.LabeledFeatures("sunny", _metrics(165, 45))
            for _ in range(15)
        ]
        samples.extend(
            refinement.LabeledFeatures("not_sunny", _metrics(115, 70))
            for _ in range(15)
        )

        first = refinement.evaluate_samples(samples)
        second = refinement.evaluate_samples(list(reversed(samples)))

        self.assertEqual(first, second)
        self.assertEqual(first["status"], "recommendation")
        self.assertTrue(first["recommendation"]["change_recommended"])
        self.assertEqual(
            first["evaluation"]["candidate"]["stable_folds"],
            refinement.CV_FOLDS,
        )
        self.assertEqual(
            first["evaluation"]["candidate"]["held_out_confirmed_folds"],
            refinement.CV_FOLDS,
        )
        self.assertNotIn(private_id, str(first))
        self.assertNotIn("sample_id", str(first))
        self.assertNotIn("path", str(first))

    def test_temporary_images_are_cleaned_after_analysis_failure(self) -> None:
        """Remove the private temporary directory even when Pillow rejects data."""
        directories: list[Path] = []

        @contextmanager
        def tracking_directory(**kwargs: object):
            with tempfile.TemporaryDirectory(**kwargs) as directory:
                directories.append(Path(directory))
                yield directory

        descriptor = refinement.SampleDescriptor(
            "c" * 32, "sunny", "image/jpeg"
        )
        with self.assertRaises(refinement.RefinementError):
            refinement.collect_labeled_features(
                _ImageClient(b"not-an-image"),
                [descriptor],
                tracking_directory,
            )

        self.assertEqual(len(directories), 1)
        self.assertFalse(directories[0].exists())
        self.assertFalse(
            directories[0].is_relative_to(refinement.REPOSITORY_ROOT)
        )

    def test_collects_features_with_production_extractor(self) -> None:
        """Analyze a downloaded file using the shared production feature code."""
        image_bytes = BytesIO()
        Image.new("RGB", (100, 100), "white").save(
            image_bytes, format="JPEG"
        )
        descriptor = refinement.SampleDescriptor(
            "d" * 32, "not_sunny", "image/jpeg"
        )

        features = refinement.collect_labeled_features(
            _ImageClient(image_bytes.getvalue()),
            [descriptor],
        )

        self.assertEqual(len(features), 1)
        self.assertEqual(features[0].label, "not_sunny")
        self.assertIn("contrast", features[0].metrics)


if __name__ == "__main__":
    unittest.main()
