"""Pool Camera Sun integration."""

import asyncio
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .api import register_api_views
from .const import (
    DATA_API_REGISTERED,
    DATA_CAPTURE_LOCK,
    DATA_ENTRIES,
    DATA_SAMPLE_STORE,
    DOMAIN,
    MAX_SAMPLES_PER_LABEL,
    SAMPLE_LABELS,
)
from .coordinator import PoolCameraSunCoordinator
from .sample_store import SampleStore

PLATFORMS = (Platform.BINARY_SENSOR, Platform.BUTTON)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Pool Camera Sun from a config entry."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    entries = domain_data.setdefault(DATA_ENTRIES, {})
    sample_store = domain_data.setdefault(
        DATA_SAMPLE_STORE,
        SampleStore(
            Path(hass.config.path(".storage", DOMAIN, "samples")),
            SAMPLE_LABELS,
            MAX_SAMPLES_PER_LABEL,
        ),
    )
    capture_lock = domain_data.setdefault(DATA_CAPTURE_LOCK, asyncio.Lock())
    if not domain_data.get(DATA_API_REGISTERED):
        register_api_views(hass)
        domain_data[DATA_API_REGISTERED] = True

    coordinator = PoolCameraSunCoordinator(
        hass, entry, sample_store, capture_lock
    )
    coordinator.async_set_updated_data(
        {
            "is_sunny": False,
            "status": "not_scanned",
            "detection_path": "none",
            "analysis_region": "reference_grass_with_panel_diagnostics",
            "sun_score": None,
            "brightness": None,
            "p10": None,
            "p90": None,
            "contrast": None,
            "panel_brightness": None,
            "panel_contrast": None,
            "threshold": None,
            "camera_entity_id": coordinator.camera_entity_id,
            "sampled_at": None,
        }
    )

    entries[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN][DATA_ENTRIES].pop(entry.entry_id)
    return unloaded
