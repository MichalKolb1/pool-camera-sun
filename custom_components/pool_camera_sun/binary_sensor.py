"""Binary sensor for Pool Camera Sun."""

from typing import Any, override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ENTITY_NAME, ENTITY_OBJECT_ID
from .coordinator import PoolCameraSunCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sunlight binary sensor."""
    coordinator: PoolCameraSunCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PoolCameraSunBinarySensor(coordinator, entry)])


class PoolCameraSunBinarySensor(
    CoordinatorEntity[PoolCameraSunCoordinator], BinarySensorEntity
):
    """Indicate whether the camera image contains direct sunlight."""

    _attr_device_class = BinarySensorDeviceClass.LIGHT
    _attr_name = ENTITY_NAME

    def __init__(
        self, coordinator: PoolCameraSunCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_direct_sun"

    @property
    @override
    def suggested_object_id(self) -> str:
        """Return the approved entity object ID."""
        return ENTITY_OBJECT_ID

    @property
    def is_on(self) -> bool:
        """Return true when direct sunlight is detected."""
        return bool(self.coordinator.data["is_sunny"])

    @property
    def icon(self) -> str:
        """Return an icon matching the detected condition."""
        return (
            "mdi:white-balance-sunny"
            if self.is_on
            else "mdi:weather-partly-cloudy"
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return analysis details used for calibration."""
        data = self.coordinator.data
        return {
            "status": data["status"],
            "analysis_region": data["analysis_region"],
            "sun_score": data["sun_score"],
            "brightness": data["brightness"],
            "contrast": data["contrast"],
            "panel_brightness": data["panel_brightness"],
            "panel_contrast": data["panel_contrast"],
            "p10": data["p10"],
            "p90": data["p90"],
            "threshold": data["threshold"],
            "camera_entity_id": data["camera_entity_id"],
            "sampled_at": data["sampled_at"],
        }
