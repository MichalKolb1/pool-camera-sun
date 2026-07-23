"""Manual labeled sample buttons for Pool Camera Sun."""

from typing import override

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    DATA_ENTRIES,
    DOMAIN,
    LABEL_NOT_SUNNY,
    LABEL_SUNNY,
)
from .coordinator import PoolCameraSunCoordinator

_BUTTONS = (
    (LABEL_SUNNY, "sunny", "pool_camera_sun_sviti", "mdi:white-balance-sunny"),
    (
        LABEL_NOT_SUNNY,
        "not_sunny",
        "pool_camera_sun_nesviti",
        "mdi:weather-cloudy",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up labeled sample buttons."""
    coordinator: PoolCameraSunCoordinator = hass.data[DOMAIN][DATA_ENTRIES][
        entry.entry_id
    ]
    async_add_entities(
        [
            PoolCameraSunSampleButton(
                coordinator,
                entry,
                manual_label,
                translation_key,
                object_id,
                icon,
            )
            for manual_label, translation_key, object_id, icon in _BUTTONS
        ]
    )


class PoolCameraSunSampleButton(ButtonEntity):
    """Capture a fresh sample with a fixed manual label."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PoolCameraSunCoordinator,
        entry: ConfigEntry,
        manual_label: str,
        translation_key: str,
        object_id: str,
        icon: str,
    ) -> None:
        """Initialize a labeled sample button."""
        self._coordinator = coordinator
        self._manual_label = manual_label
        self._object_id = object_id
        self._attr_unique_id = f"{entry.entry_id}_sample_{manual_label}"
        self._attr_translation_key = translation_key
        self._attr_icon = icon

    @property
    @override
    def suggested_object_id(self) -> str:
        """Return the stable intended entity object ID."""
        return self._object_id

    async def async_press(self) -> None:
        """Capture and store a fresh manually labeled sample."""
        await self._coordinator.async_capture_labeled_sample(self._manual_label)
