"""Pool Camera Sun integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import PoolCameraSunCoordinator

PLATFORMS = (Platform.BINARY_SENSOR,)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Pool Camera Sun from a config entry."""
    coordinator = PoolCameraSunCoordinator(hass, entry)
    coordinator.async_set_updated_data(
        {
            "is_sunny": False,
            "status": "not_scanned",
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

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
