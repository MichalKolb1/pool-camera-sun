"""Constants for Pool Camera Sun."""

from typing import Final

DOMAIN: Final = "pool_camera_sun"

CONF_CAMERA_ENTITY_ID: Final = "camera_entity_id"

DEFAULT_SUN_THRESHOLD: Final = 0.62
SUN_HYSTERESIS: Final = 0.10
BRIGHT_GRASS_P90_MIN: Final = 150
BRIGHT_GRASS_MEAN_MIN: Final = 130
BRIGHT_PANEL_CONTRAST_MIN: Final = 100

ENTITY_NAME: Final = "Bazén – přímé slunce"
ENTITY_OBJECT_ID: Final = "bazen_prime_slunce"

# Grass determines direct sunlight; panel metrics are retained for diagnostics.
GRASS_POLYGON: Final = (
    (0.369, 0.281),
    (0.574, 0.211),
    (0.553, 0.307),
    (0.366, 0.365),
)

PANEL_POLYGON: Final = (
    (0.366, 0.365),
    (0.553, 0.307),
    (0.565, 0.365),
    (0.356, 0.397),
)
