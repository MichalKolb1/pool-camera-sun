"""Image analysis coordinator for Pool Camera Sun."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
from typing import Any

from PIL import UnidentifiedImageError

from homeassistant.components import camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ALGORITHM_VERSION,
    CONF_CAMERA_ENTITY_ID,
    DEFAULT_SUN_THRESHOLD,
    DOMAIN,
    GRASS_POLYGON,
    PANEL_POLYGON,
    SUN_HYSTERESIS,
)
from .image_analysis import analyze_image
from .sample_store import SampleStorageError, SampleStore

_LOGGER = logging.getLogger(__name__)


def _analyze_image(content: bytes) -> dict[str, float]:
    """Analyze direct sunlight using grass shadows beside the panels."""
    return analyze_image(content, GRASS_POLYGON, PANEL_POLYGON)


def _detect_direct_sun(
    metrics: dict[str, float], threshold: float
) -> tuple[bool, str]:
    """Detect direct sun and identify the successful decision path."""
    if metrics["sun_score"] >= threshold:
        return True, "shadow_contrast"
    return False, "none"


class PoolCameraSunCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch and analyze camera snapshots only when explicitly requested."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        sample_store: SampleStore,
        capture_lock: asyncio.Lock,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=None,
            config_entry=entry,
        )
        self.camera_entity_id: str = entry.data[CONF_CAMERA_ENTITY_ID]
        self._entry_id = entry.entry_id
        self._sample_store = sample_store
        self._capture_lock = capture_lock

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch a snapshot and calculate whether direct sun is present."""
        async with self._capture_lock:
            _, _, analysis = await self._async_fetch_and_analyze()
        return analysis

    async def async_capture_labeled_sample(
        self, manual_label: str
    ) -> dict[str, Any]:
        """Capture, analyze, and privately store one manually labeled sample."""
        async with self._capture_lock:
            image_content, content_type, analysis = (
                await self._async_fetch_and_analyze()
            )
            metadata = {
                "captured_at": analysis["sampled_at"],
                "config_entry_id": self._entry_id,
                "camera_entity_id": self.camera_entity_id,
                "algorithm": {
                    "version": ALGORITHM_VERSION,
                    "prediction": (
                        "sunny" if analysis["is_sunny"] else "not_sunny"
                    ),
                    "is_sunny": analysis["is_sunny"],
                    "detection_path": analysis["detection_path"],
                    "analysis_region": analysis["analysis_region"],
                    "threshold": analysis["threshold"],
                    "metrics": {
                        "sun_score": analysis["sun_score"],
                        "brightness": analysis["brightness"],
                        "p10": analysis["p10"],
                        "p90": analysis["p90"],
                        "contrast": analysis["contrast"],
                        "panel_brightness": analysis["panel_brightness"],
                        "panel_contrast": analysis["panel_contrast"],
                    },
                },
            }
            try:
                stored = await self.hass.async_add_executor_job(
                    self._sample_store.store_sample,
                    manual_label,
                    image_content,
                    content_type,
                    metadata,
                )
            except (SampleStorageError, ValueError) as err:
                raise HomeAssistantError(
                    "Unable to save labeled camera sample"
                ) from err
            self.async_set_updated_data(analysis)
            return stored

    async def _async_fetch_and_analyze(
        self,
    ) -> tuple[bytes, str | None, dict[str, Any]]:
        """Fetch one fresh snapshot and analyze that exact image."""
        try:
            image = await camera.async_get_image(
                self.hass,
                self.camera_entity_id,
                timeout=20,
                width=1280,
                height=720,
            )
            metrics = await self.hass.async_add_executor_job(
                _analyze_image, image.content
            )
        except (
            HomeAssistantError,
            TimeoutError,
            OSError,
            UnidentifiedImageError,
        ):
            raise UpdateFailed(
                "Unable to fetch or analyze the camera image"
            ) from None

        previous_is_sunny = bool(self.data and self.data.get("is_sunny"))
        threshold = (
            DEFAULT_SUN_THRESHOLD - SUN_HYSTERESIS
            if previous_is_sunny
            else DEFAULT_SUN_THRESHOLD
        )
        is_sunny, detection_path = _detect_direct_sun(metrics, threshold)

        analysis = {
            **metrics,
            "is_sunny": is_sunny,
            "detection_path": detection_path,
            "status": "analyzed",
            "analysis_region": "reference_grass_with_panel_diagnostics",
            "threshold": round(threshold, 2),
            "camera_entity_id": self.camera_entity_id,
            "sampled_at": datetime.now(UTC).isoformat(),
        }
        return image.content, image.content_type, analysis
