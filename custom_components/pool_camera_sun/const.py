"""Constants for Pool Camera Sun."""

from typing import Final

DOMAIN: Final = "pool_camera_sun"

CONF_CAMERA_ENTITY_ID: Final = "camera_entity_id"

DEFAULT_SUN_THRESHOLD: Final = 0.62
SUN_HYSTERESIS: Final = 0.10
ALGORITHM_VERSION: Final = "shadow_contrast_v1"

ENTITY_NAME: Final = "Bazén – přímé slunce"
ENTITY_OBJECT_ID: Final = "bazen_prime_slunce"

DATA_API_REGISTERED: Final = "api_registered"
DATA_CAPTURE_LOCK: Final = "capture_lock"
DATA_ENTRIES: Final = "entries"
DATA_SAMPLE_STORE: Final = "sample_store"

LABEL_SUNNY: Final = "sunny"
LABEL_NOT_SUNNY: Final = "not_sunny"
SAMPLE_LABELS: Final = (LABEL_SUNNY, LABEL_NOT_SUNNY)
MAX_SAMPLES_PER_LABEL: Final = 100

API_SAMPLES_PATH: Final = "/api/pool_camera_sun/samples"
API_SAMPLE_IMAGE_PATH: Final = (
    "/api/pool_camera_sun/samples/{sample_id}/image"
)

# Grass determines direct sunlight; panel metrics are retained for diagnostics.
GRASS_POLYGON: Final = (
    (0.300, 0.140),
    (0.550, 0.105),
    (0.550, 0.235),
    (0.300, 0.265),
)

PANEL_POLYGON: Final = (
    (0.366, 0.365),
    (0.553, 0.307),
    (0.565, 0.365),
    (0.356, 0.397),
)
