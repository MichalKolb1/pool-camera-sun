# Pool Camera Sun

Pool Camera Sun is a Home Assistant custom integration that analyzes a camera
snapshot to determine whether direct sunlight is reaching a pool solar-heating
area. It creates a light-class binary sensor intended for use in automations
that decide when solar heating is useful.

## Privacy and on-demand behavior

Image analysis runs locally in Home Assistant. The integration does not upload
camera images or analysis data to an external service.

The camera is not sampled on a schedule. A snapshot is requested and analyzed
only when the binary sensor is explicitly refreshed. Until the first refresh,
the sensor reports an initial `not_scanned` status.

## Installation with HACS

This repository is a HACS custom repository:

1. In HACS, open **Integrations**.
2. Open the menu and choose **Custom repositories**.
3. Add `https://github.com/MichalKolb1/pool-camera-sun` with category
   **Integration**.
4. Find and download **Pool Camera Sun**.
5. Restart Home Assistant.

## Configuration

1. Open **Settings > Devices & services**.
2. Select **Add integration**.
3. Search for **Pool Camera Sun**.
4. Select the camera that overlooks the pool solar-heating area.

Each camera can be configured once. The integration creates
`binary_sensor.bazen_prime_slunce` by default. Its state is on when direct
sunlight is detected and off otherwise.

The image regions used for analysis are calibrated for the camera view this
integration was designed for. A different camera position may require changes
to `GRASS_POLYGON` and `PANEL_POLYGON` in
[`const.py`](custom_components/pool_camera_sun/const.py).

## Refreshing the sensor

Call `homeassistant.update_entity` whenever a fresh camera analysis is needed:

```yaml
action: homeassistant.update_entity
target:
  entity_id: binary_sensor.bazen_prime_slunce
```

Because there is no periodic polling, automations control exactly when a
snapshot is captured. For example, refresh the entity before checking whether
solar heating should run.

## Attributes

The binary sensor exposes diagnostic attributes:

| Attribute | Description |
| --- | --- |
| `status` | `not_scanned` before the first refresh, then `analyzed` |
| `sun_score` | Combined brightness and contrast score from 0 to 1 |
| `threshold` | Decision threshold, including hysteresis |
| `brightness`, `p10`, `p90`, `contrast` | Metrics for the reference grass region |
| `panel_brightness`, `panel_contrast` | Diagnostic metrics for the panel region |
| `analysis_region` | Analysis strategy identifier |
| `camera_entity_id` | Camera selected during configuration |
| `sampled_at` | UTC timestamp of the latest successful analysis |

## Releases and updates

Releases use tags in the form `pool-camera-sun-vX.Y.Z`. The release workflow
checks that the tag matches the integration version, builds
`pool_camera_sun.zip`, and publishes it as a GitHub Release asset. HACS detects
new releases and offers the matching integration update.

Issues are tracked in the
[repository issue tracker](https://github.com/MichalKolb1/pool-camera-sun/issues).
