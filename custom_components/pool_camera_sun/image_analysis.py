"""Pure image feature extraction and scoring shared by production and tools."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw, ImageOps

Polygon = tuple[tuple[float, float], ...]

BRIGHTNESS_FLOOR = 115
BRIGHTNESS_RANGE = 50
CONTRAST_FLOOR = 45
CONTRAST_RANGE = 35
DEFAULT_BRIGHTNESS_WEIGHT = 0.25


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
    grayscale: Image.Image, polygon: Polygon
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


def extract_image_features(
    content: bytes,
    grass_polygon: Polygon,
    panel_polygon: Polygon,
) -> dict[str, float]:
    """Extract production brightness and contrast features from image bytes."""
    with Image.open(BytesIO(content)) as source:
        grayscale = ImageOps.grayscale(source)
        grayscale.thumbnail((640, 360))
        grass = _region_metrics(grayscale, grass_polygon)
        panel = _region_metrics(grayscale, panel_polygon)

    return {
        **grass,
        "panel_brightness": panel["brightness"],
        "panel_contrast": panel["contrast"],
    }


def calculate_sun_score(
    metrics: dict[str, float],
    brightness_weight: float = DEFAULT_BRIGHTNESS_WEIGHT,
) -> float:
    """Calculate the sunlight score from extracted production features."""
    if not 0.0 <= brightness_weight <= 1.0:
        raise ValueError("Brightness weight must be between zero and one")
    brightness_score = _clamp(
        (metrics["p90"] - BRIGHTNESS_FLOOR) / BRIGHTNESS_RANGE
    )
    contrast_score = _clamp(
        (metrics["contrast"] - CONTRAST_FLOOR) / CONTRAST_RANGE
    )
    return round(
        brightness_weight * brightness_score
        + (1.0 - brightness_weight) * contrast_score,
        3,
    )


def analyze_image(
    content: bytes,
    grass_polygon: Polygon,
    panel_polygon: Polygon,
) -> dict[str, float]:
    """Extract features and calculate the production sunlight score."""
    metrics = extract_image_features(content, grass_polygon, panel_polygon)
    return {
        **metrics,
        "sun_score": calculate_sun_score(metrics),
    }
