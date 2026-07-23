#!/usr/bin/env python3
"""Safely evaluate Pool Camera Sun parameters from private labeled samples."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, BinaryIO, Callable, ContextManager
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = REPOSITORY_ROOT / "custom_components" / "pool_camera_sun"
sys.path.insert(0, str(COMPONENT_ROOT))

from const import (  # noqa: E402
    API_SAMPLES_PATH,
    DEFAULT_SUN_THRESHOLD,
    GRASS_POLYGON,
    LABEL_NOT_SUNNY,
    LABEL_SUNNY,
    MAX_SAMPLES_PER_LABEL,
    PANEL_POLYGON,
    SAMPLE_LABELS,
    SUN_HYSTERESIS,
)
from image_analysis import (  # noqa: E402
    DEFAULT_BRIGHTNESS_WEIGHT,
    calculate_sun_score,
    extract_image_features,
)

BASE_URL_ENV = "POOL_CAMERA_SUN_HA_URL"
TOKEN_ENV = "POOL_CAMERA_SUN_HA_TOKEN"
REQUEST_TIMEOUT_SECONDS = 20
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 256 * 1024 * 1024
MIN_SAMPLES_PER_LABEL = 10
CV_FOLDS = 5
MIN_STABLE_FOLDS = 4
MIN_BALANCED_ACCURACY = 0.70
MIN_BALANCED_ACCURACY_GAIN = 0.03
_SAMPLE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_IMAGE_CONTENT_TYPES = frozenset(
    {
        "application/octet-stream",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)

ResponseOpener = Callable[..., ContextManager[BinaryIO]]


class RefinementError(Exception):
    """Raised for a sanitized, user-actionable refinement failure."""


class _NoRedirectHandler(HTTPRedirectHandler):
    """Reject redirects so credentials never leave the configured origin."""

    def redirect_request(
        self,
        request: Request,
        file_pointer: BinaryIO,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        """Disable automatic redirects for authenticated requests."""
        return None


@dataclass(frozen=True)
class SampleDescriptor:
    """Validated private sample information used only in memory."""

    sample_id: str
    label: str
    content_type: str


@dataclass(frozen=True)
class LabeledFeatures:
    """Production image features paired with a manual label."""

    label: str
    metrics: dict[str, float]


@dataclass(frozen=True, order=True)
class Candidate:
    """Classifier parameters evaluated against the labels."""

    brightness_weight: float
    threshold: float

    @property
    def contrast_weight(self) -> float:
        """Return the complementary contrast weight."""
        return round(1.0 - self.brightness_weight, 2)


BASELINE = Candidate(DEFAULT_BRIGHTNESS_WEIGHT, DEFAULT_SUN_THRESHOLD)
CANDIDATES = tuple(
    Candidate(round(weight / 100, 2), round(threshold / 100, 2))
    for weight in range(0, 51, 5)
    for threshold in range(40, 81, 2)
)


def _normalized_content_type(value: str | None) -> str:
    """Return a lowercase media type without parameters."""
    return value.split(";", 1)[0].strip().lower() if value else ""


def _read_bounded(stream: BinaryIO, limit: int, error: str) -> bytes:
    """Read at most limit bytes and fail before retaining oversized content."""
    content = stream.read(limit + 1)
    if len(content) > limit:
        raise RefinementError(error)
    return content


class HomeAssistantSampleClient:
    """Minimal authenticated client that never exposes request secrets."""

    def __init__(
        self,
        base_url: str,
        token: str,
        opener: ResponseOpener | None = None,
    ) -> None:
        """Validate explicit connection settings."""
        try:
            parsed = urlsplit(base_url)
            hostname = parsed.hostname
        except ValueError:
            raise RefinementError(
                f"{BASE_URL_ENV} must be a secure Home Assistant base URL"
            ) from None
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise RefinementError(
                f"{BASE_URL_ENV} must be an HTTP(S) Home Assistant base URL"
            )
        if parsed.scheme == "http" and hostname not in {
            "127.0.0.1",
            "::1",
            "localhost",
        }:
            raise RefinementError(
                f"{BASE_URL_ENV} must use HTTPS unless Home Assistant is loopback"
            )
        if not token.strip():
            raise RefinementError(f"{TOKEN_ENV} must not be empty")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._opener = opener or build_opener(_NoRedirectHandler()).open

    def _request(self, path: str) -> ContextManager[BinaryIO]:
        """Open one authenticated request with sanitized error handling."""
        request = Request(
            f"{self._base_url}{path}",
            headers={
                "Accept": "application/json, image/*",
                "Authorization": f"Bearer {self._token}",
                "Cache-Control": "no-store",
            },
            method="GET",
        )
        try:
            return self._opener(request, timeout=REQUEST_TIMEOUT_SECONDS)
        except HTTPError as err:
            if err.code in {401, 403}:
                raise RefinementError(
                    "Home Assistant rejected sample API authentication"
                ) from None
            raise RefinementError(
                f"Sample API request failed with HTTP status {err.code}"
            ) from None
        except (HTTPException, URLError, TimeoutError, OSError):
            raise RefinementError(
                "Unable to reach the configured Home Assistant sample API"
            ) from None

    @staticmethod
    def _response_content_type(response: BinaryIO) -> str:
        """Read a response media type without depending on an HTTP library."""
        headers = getattr(response, "headers", None)
        value = headers.get("Content-Type") if headers is not None else None
        return _normalized_content_type(value)

    @staticmethod
    def _validate_status(response: BinaryIO) -> None:
        """Reject non-success responses from custom test or HTTP openers."""
        status = getattr(response, "status", 200)
        if status != 200:
            raise RefinementError(
                f"Sample API request failed with HTTP status {status}"
            )

    def fetch_manifest(self) -> list[SampleDescriptor]:
        """Fetch and validate retained sample metadata."""
        try:
            with self._request(API_SAMPLES_PATH) as response:
                self._validate_status(response)
                if self._response_content_type(response) != "application/json":
                    raise RefinementError(
                        "Sample metadata response must use application/json"
                    )
                raw = _read_bounded(
                    response,
                    MAX_MANIFEST_BYTES,
                    "Sample metadata response exceeds the size limit",
                )
        except RefinementError:
            raise
        except (HTTPException, URLError, TimeoutError, OSError):
            raise RefinementError(
                "Unable to read the sample metadata response"
            ) from None
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RefinementError(
                "Sample metadata response is not valid JSON"
            ) from None
        return validate_manifest(payload)

    def fetch_image(self, descriptor: SampleDescriptor) -> bytes:
        """Fetch one bounded image after metadata validation."""
        path = f"{API_SAMPLES_PATH}/{descriptor.sample_id}/image"
        try:
            with self._request(path) as response:
                self._validate_status(response)
                content_type = self._response_content_type(response)
                if (
                    content_type not in _IMAGE_CONTENT_TYPES
                    or content_type != descriptor.content_type
                ):
                    raise RefinementError(
                        "Sample image response has an unexpected content type"
                    )
                return _read_bounded(
                    response,
                    MAX_IMAGE_BYTES,
                    "A sample image exceeds the size limit",
                )
        except RefinementError:
            raise
        except (HTTPException, URLError, TimeoutError, OSError):
            raise RefinementError(
                "Unable to read a sample image response"
            ) from None


def validate_manifest(payload: Any) -> list[SampleDescriptor]:
    """Strictly validate sample count, retention, IDs, labels, and media types."""
    if not isinstance(payload, dict):
        raise RefinementError("Sample metadata response must be an object")
    count = payload.get("count")
    retention = payload.get("retention_per_label")
    samples = payload.get("samples")
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        or not isinstance(retention, int)
        or isinstance(retention, bool)
        or not 1 <= retention <= MAX_SAMPLES_PER_LABEL
        or not isinstance(samples, list)
        or count != len(samples)
        or count > retention * len(SAMPLE_LABELS)
    ):
        raise RefinementError(
            "Sample metadata count or retention is inconsistent"
        )

    descriptors: list[SampleDescriptor] = []
    seen_ids: set[str] = set()
    label_counts: Counter[str] = Counter()
    for item in samples:
        if not isinstance(item, dict):
            raise RefinementError("Sample metadata contains an invalid item")
        sample_id = item.get("id")
        label = item.get("manual_label")
        image = item.get("image")
        if (
            not isinstance(sample_id, str)
            or _SAMPLE_ID_PATTERN.fullmatch(sample_id) is None
            or sample_id in seen_ids
            or not isinstance(label, str)
            or label not in SAMPLE_LABELS
            or not isinstance(image, dict)
        ):
            raise RefinementError("Sample metadata contains an invalid item")
        content_type = image.get("content_type")
        if (
            not isinstance(content_type, str)
            or content_type not in _IMAGE_CONTENT_TYPES
        ):
            raise RefinementError(
                "Sample metadata contains an invalid image content type"
            )
        seen_ids.add(sample_id)
        label_counts[label] += 1
        descriptors.append(SampleDescriptor(sample_id, label, content_type))

    if any(label_counts[label] > retention for label in SAMPLE_LABELS):
        raise RefinementError(
            "Sample metadata exceeds the advertised per-label retention"
        )
    return descriptors


def _safe_temp_parent() -> Path:
    """Choose a temporary parent that cannot be inside the repository."""
    parent = Path(tempfile.gettempdir()).resolve()
    if parent == REPOSITORY_ROOT or parent.is_relative_to(REPOSITORY_ROOT):
        return REPOSITORY_ROOT.parent
    return parent


def collect_labeled_features(
    client: HomeAssistantSampleClient,
    descriptors: list[SampleDescriptor],
    temporary_directory_factory: Callable[..., ContextManager[str]] = (
        tempfile.TemporaryDirectory
    ),
) -> list[LabeledFeatures]:
    """Download, analyze, and automatically remove all private image files."""
    total_bytes = 0
    features: list[LabeledFeatures] = []
    try:
        with temporary_directory_factory(
            prefix="pool-camera-sun-private-",
            dir=_safe_temp_parent(),
        ) as directory:
            root = Path(directory).resolve()
            if root == REPOSITORY_ROOT or root.is_relative_to(REPOSITORY_ROOT):
                raise RefinementError(
                    "Temporary sample storage must be outside the repository"
                )
            for index, descriptor in enumerate(descriptors):
                content = client.fetch_image(descriptor)
                total_bytes += len(content)
                if total_bytes > MAX_TOTAL_IMAGE_BYTES:
                    raise RefinementError(
                        "Downloaded samples exceed the total size limit"
                    )
                image_path = root / f"sample-{index:03d}.img"
                try:
                    image_path.write_bytes(content)
                    metrics = extract_image_features(
                        image_path.read_bytes(),
                        GRASS_POLYGON,
                        PANEL_POLYGON,
                    )
                except (
                    Image.DecompressionBombError,
                    OSError,
                    ValueError,
                ):
                    raise RefinementError(
                        "A downloaded sample image could not be analyzed"
                    ) from None
                features.append(LabeledFeatures(descriptor.label, metrics))
    except RefinementError:
        raise
    except OSError:
        raise RefinementError(
            "Unable to use temporary sample storage"
        ) from None
    return features


def _predict(
    sample: LabeledFeatures,
    candidate: Candidate,
    threshold_reduction: float = 0.0,
) -> bool:
    """Return a candidate prediction for one labeled sample."""
    return (
        calculate_sun_score(sample.metrics, candidate.brightness_weight)
        >= candidate.threshold - threshold_reduction
    )


def _aggregate_metrics(
    samples: list[LabeledFeatures],
    candidate: Candidate,
    threshold_reduction: float = 0.0,
) -> dict[str, float]:
    """Return aggregate binary-classification metrics."""
    true_positive = true_negative = false_positive = false_negative = 0
    for sample in samples:
        predicted_sunny = _predict(
            sample,
            candidate,
            threshold_reduction,
        )
        actual_sunny = sample.label == LABEL_SUNNY
        if predicted_sunny and actual_sunny:
            true_positive += 1
        elif predicted_sunny:
            false_positive += 1
        elif actual_sunny:
            false_negative += 1
        else:
            true_negative += 1

    sunny_recall = true_positive / (true_positive + false_negative)
    not_sunny_recall = true_negative / (true_negative + false_positive)
    return {
        "accuracy": round(
            (true_positive + true_negative) / len(samples),
            3,
        ),
        "balanced_accuracy": round(
            (sunny_recall + not_sunny_recall) / 2,
            3,
        ),
        "sunny_recall": round(sunny_recall, 3),
        "not_sunny_recall": round(not_sunny_recall, 3),
    }


def _candidate_rank(
    samples: list[LabeledFeatures], candidate: Candidate
) -> tuple[float, float, float, float, float, float]:
    """Rank candidates deterministically, preferring smaller baseline changes."""
    nominal = _aggregate_metrics(samples, candidate)
    hysteresis = _aggregate_metrics(samples, candidate, SUN_HYSTERESIS)
    distance = abs(candidate.brightness_weight - BASELINE.brightness_weight)
    distance += abs(candidate.threshold - BASELINE.threshold)
    return (
        min(
            nominal["balanced_accuracy"],
            hysteresis["balanced_accuracy"],
        ),
        round(
            (
                nominal["balanced_accuracy"]
                + hysteresis["balanced_accuracy"]
            )
            / 2,
            4,
        ),
        min(nominal["accuracy"], hysteresis["accuracy"]),
        -round(distance, 4),
        -candidate.brightness_weight,
        -candidate.threshold,
    )


def _candidate_stability_rank(
    candidate: Candidate,
) -> tuple[float, float, float]:
    """Prefer stable candidates closest to production without label leakage."""
    distance = abs(candidate.brightness_weight - BASELINE.brightness_weight)
    distance += abs(candidate.threshold - BASELINE.threshold)
    return (
        -round(distance, 4),
        -candidate.brightness_weight,
        -candidate.threshold,
    )


def _stratified_folds(
    samples: list[LabeledFeatures],
) -> list[list[LabeledFeatures]]:
    """Create deterministic folds without using private metadata identifiers."""
    folds: list[list[LabeledFeatures]] = [[] for _ in range(CV_FOLDS)]
    for label in SAMPLE_LABELS:
        labeled = sorted(
            (sample for sample in samples if sample.label == label),
            key=lambda sample: (
                sample.metrics["p90"],
                sample.metrics["contrast"],
                sample.metrics["brightness"],
                sample.metrics["panel_brightness"],
                sample.metrics["panel_contrast"],
            ),
        )
        for index, sample in enumerate(labeled):
            folds[index % CV_FOLDS].append(sample)
    return folds


def evaluate_samples(samples: list[LabeledFeatures]) -> dict[str, Any]:
    """Evaluate candidates and return a sanitized aggregate recommendation."""
    counts = Counter(sample.label for sample in samples)
    count_summary = {
        LABEL_SUNNY: counts[LABEL_SUNNY],
        LABEL_NOT_SUNNY: counts[LABEL_NOT_SUNNY],
        "total": len(samples),
    }
    if any(counts[label] < MIN_SAMPLES_PER_LABEL for label in SAMPLE_LABELS):
        return {
            "status": "insufficient_data",
            "sample_counts": count_summary,
            "requirement": {
                "minimum_per_label": MIN_SAMPLES_PER_LABEL,
            },
            "recommendation": {
                "change_recommended": False,
                "reason": "minimum_per_label_not_met",
            },
        }

    folds = _stratified_folds(samples)
    selected: list[Candidate] = []
    for validation_fold in folds:
        validation_ids = {id(sample) for sample in validation_fold}
        training = [
            sample for sample in samples if id(sample) not in validation_ids
        ]
        selected.append(
            max(CANDIDATES, key=lambda item: _candidate_rank(training, item))
        )

    selection_counts = Counter(selected)
    candidate = max(
        selection_counts,
        key=lambda item: (
            selection_counts[item],
            _candidate_stability_rank(item),
        ),
    )
    baseline_nominal = _aggregate_metrics(samples, BASELINE)
    baseline_hysteresis = _aggregate_metrics(
        samples,
        BASELINE,
        SUN_HYSTERESIS,
    )
    candidate_nominal = _aggregate_metrics(samples, candidate)
    candidate_hysteresis = _aggregate_metrics(
        samples,
        candidate,
        SUN_HYSTERESIS,
    )
    stable_folds = selection_counts[candidate]
    held_out_confirmed_folds = sum(
        selected[index] == candidate
        and _aggregate_metrics(fold, candidate)["balanced_accuracy"]
        > _aggregate_metrics(fold, BASELINE)["balanced_accuracy"]
        and _aggregate_metrics(
            fold,
            candidate,
            SUN_HYSTERESIS,
        )["balanced_accuracy"]
        > _aggregate_metrics(
            fold,
            BASELINE,
            SUN_HYSTERESIS,
        )["balanced_accuracy"]
        for index, fold in enumerate(folds)
    )
    baseline_worst_accuracy = min(
        baseline_nominal["balanced_accuracy"],
        baseline_hysteresis["balanced_accuracy"],
    )
    candidate_worst_accuracy = min(
        candidate_nominal["balanced_accuracy"],
        candidate_hysteresis["balanced_accuracy"],
    )
    gain = round(
        candidate_worst_accuracy - baseline_worst_accuracy,
        3,
    )
    recommend = (
        candidate != BASELINE
        and stable_folds >= MIN_STABLE_FOLDS
        and held_out_confirmed_folds >= MIN_STABLE_FOLDS
        and candidate_worst_accuracy >= MIN_BALANCED_ACCURACY
        and gain >= MIN_BALANCED_ACCURACY_GAIN
    )

    reason = "candidate_meets_safety_gates"
    if candidate == BASELINE:
        reason = "baseline_is_best"
    elif stable_folds < MIN_STABLE_FOLDS:
        reason = "candidate_not_stable_across_folds"
    elif held_out_confirmed_folds < MIN_STABLE_FOLDS:
        reason = "improvement_not_confirmed_on_held_out_folds"
    elif candidate_worst_accuracy < MIN_BALANCED_ACCURACY:
        reason = "candidate_accuracy_too_low"
    elif gain < MIN_BALANCED_ACCURACY_GAIN:
        reason = "improvement_too_small"

    return {
        "status": "recommendation" if recommend else "no_change",
        "sample_counts": count_summary,
        "evaluation": {
            "method": (
                "deterministic_stratified_5_fold_with_hysteresis"
            ),
            "baseline": {
                "brightness_weight": BASELINE.brightness_weight,
                "contrast_weight": BASELINE.contrast_weight,
                "threshold": BASELINE.threshold,
                "hysteresis": SUN_HYSTERESIS,
                "worst_case_balanced_accuracy": baseline_worst_accuracy,
                "nominal": baseline_nominal,
                "hysteresis_active": baseline_hysteresis,
            },
            "candidate": {
                "brightness_weight": candidate.brightness_weight,
                "contrast_weight": candidate.contrast_weight,
                "threshold": candidate.threshold,
                "hysteresis": SUN_HYSTERESIS,
                "stable_folds": stable_folds,
                "held_out_confirmed_folds": held_out_confirmed_folds,
                "balanced_accuracy_gain": gain,
                "worst_case_balanced_accuracy": candidate_worst_accuracy,
                "nominal": candidate_nominal,
                "hysteresis_active": candidate_hysteresis,
            },
        },
        "recommendation": {
            "change_recommended": recommend,
            "reason": reason,
        },
    }


def run_refinement(
    base_url: str,
    token: str,
    opener: ResponseOpener | None = None,
    temporary_directory_factory: Callable[..., ContextManager[str]] = (
        tempfile.TemporaryDirectory
    ),
) -> dict[str, Any]:
    """Fetch private samples and return only sanitized aggregate results."""
    client = HomeAssistantSampleClient(base_url, token, opener)
    descriptors = client.fetch_manifest()
    features = collect_labeled_features(
        client,
        descriptors,
        temporary_directory_factory,
    )
    return evaluate_samples(features)


def _format_text(summary: dict[str, Any]) -> str:
    """Render an aggregate-only terminal summary."""
    counts = summary["sample_counts"]
    lines = [
        f"Status: {summary['status']}",
        (
            "Samples: "
            f"sunny={counts[LABEL_SUNNY]}, "
            f"not_sunny={counts[LABEL_NOT_SUNNY]}, "
            f"total={counts['total']}"
        ),
    ]
    evaluation = summary.get("evaluation")
    if evaluation:
        baseline = evaluation["baseline"]
        candidate = evaluation["candidate"]
        lines.extend(
            [
                (
                    "Baseline: "
                    "worst_case_balanced_accuracy="
                    f"{baseline['worst_case_balanced_accuracy']:.3f}"
                ),
                (
                    "Candidate: "
                    f"brightness_weight={candidate['brightness_weight']:.2f}, "
                    f"contrast_weight={candidate['contrast_weight']:.2f}, "
                    f"threshold={candidate['threshold']:.2f}, "
                    "worst_case_balanced_accuracy="
                    f"{candidate['worst_case_balanced_accuracy']:.3f}, "
                    f"stable_folds={candidate['stable_folds']}/{CV_FOLDS}, "
                    "held_out_confirmed_folds="
                    f"{candidate['held_out_confirmed_folds']}/{CV_FOLDS}"
                ),
            ]
        )
    lines.append(
        "Recommendation: "
        f"{summary['recommendation']['reason']}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the command-line workflow."""
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate Pool Camera Sun parameters from private labeled samples "
            "without retaining images or private metadata."
        )
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="sanitized aggregate output format",
    )
    args = parser.parse_args(argv)
    base_url = os.environ.get(BASE_URL_ENV)
    token = os.environ.get(TOKEN_ENV)
    if base_url is None or token is None:
        missing = BASE_URL_ENV if base_url is None else TOKEN_ENV
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": f"Required environment variable {missing} is not set",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    try:
        summary = run_refinement(base_url, token)
    except RefinementError as err:
        print(
            json.dumps(
                {"status": "error", "error": str(err)},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    if args.format == "json":
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(_format_text(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
