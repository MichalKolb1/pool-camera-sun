"""Image analysis coordinator for Pool Camera Sun."""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
import logging
from typing import Any

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from homeassistant.components import camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    BRIGHT_GRASS_MEAN_MIN,
    BRIGHT_GRASS_P90_MIN,
    BRIGHT_PANEL_CONTRAST_MIN,
    CONF_CAMERA_ENTITY_ID,
    DEFAULT_SUN_THRESHOLD,
    DOMAIN,
    GRASS_POLYGON,
    PANEL_POLYGON,
    SUN_HYSTERESIS,
)

_LOGGER = logging.getLogger(__name__)


def _percentile(histogram: list[int], percentile: float) -> int:
    """Return a grayscale histogram percentile."""
    target = sum(histogram) * percentile
    cumulative = 0
    for value, count in enumerate(histogram):
        cumulative += count
        if cumulative >= target:
            return value
    return 255


def _clamp(value: float) -> float:
    """Clamp a value to the zero-to-one range."""
    return max(0.0, min(1.0, value))


def _region_metrics(
    grayscale: Image.Image, polygon: tuple[tuple[float, float], ...]
) -> dict[str, float]:
    """Calculate grayscale metrics inside a normalized polygon."""
    width, height = grayscale.size
    mask = Image.new("L", grayscale.size, 0)
    ImageDraw.Draw(mask).polygon(
        [(round(width * x), round(height * y)) for x, y in polygon],
        fill=255,
    )
    histogram = grayscale.histogram(mask=mask)
    pixels = sum(histogram)
    mean = sum(value * count for value, count in enumerate(histogram)) / pixels
    p10 = _percentile(histogram, 0.10)
    p90 = _percentile(histogram, 0.90)
    return {
        "brightness": round(mean, 1),
        "p10": p10,
        "p90": p90,
        "contrast": p90 - p10,
    }


def _analyze_image(content: bytes) -> dict[str, float]:
    """Analyze direct sunlight using grass shadows beside the panels."""
    with Image.open(BytesIO(content)) as source:
        grayscale = ImageOps.grayscale(source)
        grayscale.thumbnail((640, 360))
        grass = _region_metrics(grayscale, GRASS_POLYGON)
        panel = _region_metrics(grayscale, PANEL_POLYGON)

    # Diffuse light can be bright but does not create pronounced shadows.
    brightness_score = _clamp((grass["p90"] - 115) / 50)
    contrast_score = _clamp((grass["contrast"] - 45) / 35)
    sun_score = 0.25 * brightness_score + 0.75 * contrast_score
    return {
        **grass,
        "panel_brightness": panel["brightness"],
        "panel_contrast": panel["contrast"],
        "sun_score": round(sun_score, 3),
    }


def _detect_direct_sun(
    metrics: dict[str, float], threshold: float
) -> tuple[bool, str]:
    """Detect direct sun and identify the successful decision path."""
    if metrics["sun_score"] >= threshold:
        return True, "shadow_contrast"
    if (
        metrics["p90"] >= BRIGHT_GRASS_P90_MIN
        and metrics["brightness"] >= BRIGHT_GRASS_MEAN_MIN
        and metrics["panel_contrast"] >= BRIGHT_PANEL_CONTRAST_MIN
    ):
        return True, "bright_grass_panel_confirmed"
    return False, "none"


class PoolCameraSunCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch and analyze camera snapshots only when explicitly requested."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=None,
            config_entry=entry,
        )
        self.camera_entity_id: str = entry.data[CONF_CAMERA_ENTITY_ID]

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch a snapshot and calculate whether direct sun is present."""
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
        ) as err:
            raise UpdateFailed(f"Unable to analyze camera image: {err}") from err

        previous_is_sunny = bool(self.data and self.data.get("is_sunny"))
        threshold = (
            DEFAULT_SUN_THRESHOLD - SUN_HYSTERESIS
            if previous_is_sunny
            else DEFAULT_SUN_THRESHOLD
        )
        is_sunny, detection_path = _detect_direct_sun(metrics, threshold)

        return {
            **metrics,
            "is_sunny": is_sunny,
            "detection_path": detection_path,
            "status": "analyzed",
            "analysis_region": "reference_grass_with_panel_diagnostics",
            "threshold": round(threshold, 2),
            "camera_entity_id": self.camera_entity_id,
            "sampled_at": datetime.now(UTC).isoformat(),
        }
