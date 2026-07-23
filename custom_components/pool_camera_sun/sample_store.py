"""Private bounded storage for labeled camera samples."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from threading import Lock
from typing import Any
from uuid import uuid4

_SAMPLE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_CONTENT_TYPE_EXTENSIONS = {
    "image/gif": "gif",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
_ALLOWED_EXTENSIONS = frozenset(_CONTENT_TYPE_EXTENSIONS.values())
_ALLOWED_CONTENT_TYPES = frozenset(_CONTENT_TYPE_EXTENSIONS) | {
    "application/octet-stream"
}


class SampleStorageError(Exception):
    """Raised when private sample storage cannot be read or updated."""


class InvalidSampleId(ValueError):
    """Raised when an API sample ID is not in the expected format."""


class SampleNotFound(LookupError):
    """Raised when a sample does not exist."""


def is_valid_sample_id(sample_id: str) -> bool:
    """Return whether a sample ID is safe to use for lookup."""
    return _SAMPLE_ID_PATTERN.fullmatch(sample_id) is not None


class SampleStore:
    """Store image and metadata pairs outside Home Assistant's public paths."""

    def __init__(
        self,
        root: Path,
        labels: tuple[str, ...],
        max_samples_per_label: int,
    ) -> None:
        """Initialize private storage."""
        if max_samples_per_label < 1:
            raise ValueError("Sample retention must be at least one")
        self._root = root
        self._labels = labels
        self._max_samples_per_label = max_samples_per_label
        self._lock = Lock()

    @property
    def max_samples_per_label(self) -> int:
        """Return the configured per-label retention limit."""
        return self._max_samples_per_label

    def store_sample(
        self,
        label: str,
        image: bytes,
        content_type: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Serialize storing and retention with API readers."""
        with self._lock:
            return self._store_sample(label, image, content_type, metadata)

    def _store_sample(
        self,
        label: str,
        image: bytes,
        content_type: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically store a labeled image and its metadata, then prune."""
        if label not in self._labels:
            raise ValueError(f"Unsupported sample label: {label}")
        normalized_content_type = (
            content_type
            if content_type in _CONTENT_TYPE_EXTENSIONS
            else "application/octet-stream"
        )
        extension = _CONTENT_TYPE_EXTENSIONS.get(normalized_content_type, "img")
        sample_id = uuid4().hex
        label_dir = self._root / label
        image_filename = f"{sample_id}.{extension}"
        metadata_filename = f"{sample_id}.json"
        stored_metadata = {
            **metadata,
            "id": sample_id,
            "manual_label": label,
            "image": {
                "content_type": normalized_content_type,
                "filename": image_filename,
            },
        }

        image_path = label_dir / image_filename
        metadata_path = label_dir / metadata_filename
        image_temp = label_dir / f".{image_filename}.tmp"
        metadata_temp = label_dir / f".{metadata_filename}.tmp"
        try:
            label_dir.mkdir(parents=True, exist_ok=True)
            with image_temp.open("xb") as image_file:
                image_file.write(image)
            with metadata_temp.open("x", encoding="utf-8") as metadata_file:
                json.dump(
                    stored_metadata,
                    metadata_file,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            os.replace(image_temp, image_path)
            try:
                os.replace(metadata_temp, metadata_path)
            except OSError:
                image_path.unlink(missing_ok=True)
                raise
            try:
                self._prune_label(label_dir)
            except SampleStorageError:
                try:
                    metadata_path.unlink(missing_ok=True)
                    image_path.unlink(missing_ok=True)
                except OSError as err:
                    raise SampleStorageError(
                        "Unable to roll back a sample after retention failed"
                    ) from err
                raise
        except OSError as err:
            image_temp.unlink(missing_ok=True)
            metadata_temp.unlink(missing_ok=True)
            raise SampleStorageError("Unable to store labeled camera sample") from err

        return stored_metadata

    def list_samples(self) -> list[dict[str, Any]]:
        """Return all retained metadata, newest first."""
        with self._lock:
            return self._list_samples()

    def _list_samples(self) -> list[dict[str, Any]]:
        """Read retained metadata while holding the storage lock."""
        if not self._root.exists():
            return []
        samples: list[dict[str, Any]] = []
        try:
            for label in self._labels:
                label_dir = self._root / label
                if not label_dir.exists():
                    continue
                for metadata_path in label_dir.glob("*.json"):
                    samples.append(self._read_metadata(metadata_path))
        except OSError as err:
            raise SampleStorageError("Unable to list labeled camera samples") from err
        return sorted(
            samples,
            key=lambda item: (str(item.get("captured_at", "")), str(item["id"])),
            reverse=True,
        )

    def get_image(self, sample_id: str) -> tuple[bytes, str]:
        """Return image bytes and content type for a strictly validated ID."""
        with self._lock:
            return self._get_image(sample_id)

    def _get_image(self, sample_id: str) -> tuple[bytes, str]:
        """Read one image while holding the storage lock."""
        if not is_valid_sample_id(sample_id):
            raise InvalidSampleId(
                "Sample ID must be 32 lowercase hexadecimal characters"
            )

        for label in self._labels:
            label_dir = self._root / label
            metadata_path = label_dir / f"{sample_id}.json"
            if not metadata_path.is_file():
                continue
            metadata = self._read_metadata(metadata_path)
            image_data = metadata.get("image")
            if not isinstance(image_data, dict):
                raise SampleStorageError("Stored sample image metadata is invalid")
            filename = image_data.get("filename")
            content_type = image_data.get("content_type")
            if (
                not isinstance(filename, str)
                or not isinstance(content_type, str)
                or content_type not in _ALLOWED_CONTENT_TYPES
            ):
                raise SampleStorageError("Stored sample image metadata is invalid")
            expected_prefix = f"{sample_id}."
            extension = filename.removeprefix(expected_prefix)
            if (
                not filename.startswith(expected_prefix)
                or extension not in _ALLOWED_EXTENSIONS | {"img"}
                or Path(filename).name != filename
            ):
                raise SampleStorageError("Stored sample image filename is invalid")
            image_path = label_dir / filename
            try:
                resolved_label_dir = label_dir.resolve(strict=True)
                resolved_image_path = image_path.resolve(strict=True)
                if not resolved_image_path.is_relative_to(resolved_label_dir):
                    raise SampleStorageError(
                        "Stored sample image path is outside private storage"
                    )
                return resolved_image_path.read_bytes(), content_type
            except OSError as err:
                raise SampleStorageError("Unable to read labeled camera image") from err
        raise SampleNotFound(sample_id)

    def _prune_label(self, label_dir: Path) -> None:
        """Delete oldest complete pairs until the label is within retention."""
        metadata_items = [
            (self._read_metadata(path), path)
            for path in label_dir.glob("*.json")
        ]
        metadata_items.sort(
            key=lambda item: (
                str(item[0].get("captured_at", "")),
                str(item[0]["id"]),
            )
        )
        for metadata, metadata_path in metadata_items[
            : -self._max_samples_per_label
        ]:
            image_data = metadata.get("image")
            filename = (
                image_data.get("filename")
                if isinstance(image_data, dict)
                else None
            )
            if not isinstance(filename, str) or Path(filename).name != filename:
                raise SampleStorageError("Stored sample image filename is invalid")
            image_path = label_dir / filename
            deleting_metadata = metadata_path.with_suffix(".json.deleting")
            deleting_image = image_path.with_name(f"{image_path.name}.deleting")
            try:
                os.replace(metadata_path, deleting_metadata)
                try:
                    os.replace(image_path, deleting_image)
                except OSError:
                    os.replace(deleting_metadata, metadata_path)
                    raise
                deleting_metadata.unlink()
                deleting_image.unlink()
            except OSError as err:
                raise SampleStorageError("Unable to enforce sample retention") from err

    @staticmethod
    def _read_metadata(path: Path) -> dict[str, Any]:
        """Read and minimally validate one metadata document."""
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            raise SampleStorageError("Stored sample metadata is invalid") from err
        if (
            not isinstance(metadata, dict)
            or not isinstance(metadata.get("id"), str)
            or not is_valid_sample_id(metadata["id"])
            or path.stem != metadata["id"]
        ):
            raise SampleStorageError("Stored sample metadata is invalid")
        return metadata
