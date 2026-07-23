# Pool Camera Sun

Pool Camera Sun is a Home Assistant custom integration that analyzes a camera
snapshot to determine whether direct sunlight is reaching a pool solar-heating
area. It creates a light-class binary sensor intended for use in automations
that decide when solar heating is useful.

## Privacy, on-demand behavior, and labeled samples

Image analysis runs locally in Home Assistant. The integration does not upload
camera images or analysis data to an external service. Labeled samples are
stored privately below Home Assistant's `.storage/pool_camera_sun/samples`
directory. They are never written to `/www`, and the integration does not log
image bytes, authentication tokens, or private camera URLs.

The camera is not sampled on a schedule. A snapshot is requested and analyzed
only when the binary sensor is explicitly refreshed. Until the first refresh,
the sensor reports an initial `not_scanned` status.

The integration also creates two manual sample buttons intended to use these
stable entity IDs:

- `button.pool_camera_sun_sviti` (`Svítí` / `Sunny`) saves label `sunny`.
- `button.pool_camera_sun_nesviti` (`Nesvítí` / `Not sunny`) saves label
  `not_sunny`.

Press a button only after visually deciding the correct label. Each press
fetches a fresh camera snapshot at that moment, runs the current production
classifier on that exact image, and stores the original image with its UTC
timestamp, manual label, algorithm prediction and decision path, threshold,
and all brightness/contrast metrics. Captures are serialized. Retention is
limited globally to the newest 100 complete image/metadata pairs per label;
older pairs are removed automatically.

Samples provide calibration evidence only. The production classifier never
trains, changes its thresholds, or otherwise self-modifies from collected
labels.

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
| `detection_path` | Decision path: `shadow_contrast` or `none` |
| `sun_score` | Combined brightness and contrast score from 0 to 1 |
| `threshold` | Decision threshold, including hysteresis |
| `brightness`, `p10`, `p90`, `contrast` | Metrics for the reference grass region |
| `panel_brightness`, `panel_contrast` | Diagnostic metrics for the panel region |
| `analysis_region` | Analysis strategy identifier |
| `camera_entity_id` | Camera selected during configuration |
| `sampled_at` | UTC timestamp of the latest successful analysis |

## Authenticated sample API

A minimal read-only API is available for a future authorized analysis agent:

| Method and path | Result |
| --- | --- |
| `GET /api/pool_camera_sun/samples` | Lists retained metadata; never returns image bytes |
| `GET /api/pool_camera_sun/samples/{sample_id}/image` | Returns one retained image |

Both endpoints require normal Home Assistant authentication, such as an
authorized long-lived access token using standard bearer-token authentication.
There is no unauthenticated export route. Image IDs must be exactly 32
lowercase hexadecimal characters, and stored filenames are validated before
access to prevent path traversal. API image responses disable caching.

## Refining the analysis from labeled samples

Tell Copilot **“Spusť přesnější analýzu Pool Camera Sun”**. In a local clone of
this repository, that workflow means setting the Home Assistant URL and a
long-lived access token in environment variables, then running:

```powershell
python -m pip install -r requirements-tools.txt
$env:POOL_CAMERA_SUN_HA_URL = "https://home-assistant.example"
$env:POOL_CAMERA_SUN_HA_TOKEN = Read-Host -MaskInput "Home Assistant token"
python tools\refine_pool_camera_sun.py --format json
```

The command accepts connection details only from those explicit environment
variables. It validates the authenticated API manifest and every image
response, enforces request, count, retention, media-type, and download-size
limits, requires HTTPS except for loopback URLs, and uses the production
feature extractor. Images exist only in an automatically removed system
temporary directory outside the repository.
Tokens, URLs, camera IDs, sample IDs, timestamps, filenames, paths, metadata,
and image content are never included in output.

At least 10 samples for each label are required before parameters are
evaluated. Candidate brightness/contrast weights and thresholds must show a
stable held-out improvement under deterministic stratified five-fold
evaluation at both production threshold states (nominal and hysteresis) before
the tool recommends a change. Its output is aggregate guidance only: it never
edits, commits, or releases classifier settings automatically.

## Releases and updates

Releases use tags in the form `pool-camera-sun-vX.Y.Z`. The release workflow
checks that the tag matches the integration version, builds
`pool_camera_sun.zip`, and publishes it as a GitHub Release asset. HACS detects
new releases and offers the matching integration update.

Issues are tracked in the
[repository issue tracker](https://github.com/MichalKolb1/pool-camera-sun/issues).
