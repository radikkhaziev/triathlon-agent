"""Workout card PNG renderer (Pillow-based).

Generates Instagram story cards (1080x1920, transparent background)
with GPS polyline track, key metrics, AI insight, and endurai.me branding.
"""

import logging
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_FONT_DIR = _STATIC_DIR / "fonts"
_LOGO_PATH = Path(__file__).resolve().parent.parent / "webapp" / "public" / "android-chrome-512x512.png"

SPORT_COLORS = {
    "Swim": "#00BFFF",
    "Ride": "#FF6B35",
    "Run": "#00D26A",
    "Other": "#A855F7",
}

SPORT_LABEL = {
    "Swim": "SWIM",
    "Ride": "RIDE",
    "Run": "RUN",
    "Other": "WORKOUT",
}

TEXT_WHITE = "#FFFFFF"
TEXT_DIM = "#6B7280"
TEXT_LIGHT = "#D1D5DB"


@dataclass
class WorkoutCardData:
    sport_type: str
    distance_m: float | None = None
    duration_sec: int | None = None
    avg_pace_sec_per_km: float | None = None
    avg_power: int | None = None
    avg_hr: int | None = None
    elevation_gain: float | None = None
    ai_text: str | None = None
    latlng: list[tuple[float, float]] | None = None


# ---------------------------------------------------------------------------
#  Font helpers
# ---------------------------------------------------------------------------


@lru_cache(maxsize=20)
def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    path = _FONT_DIR / f"Inter-{weight}.ttf"
    if not path.exists():
        logger.warning("Font not found: %s, falling back to default", path)
        return ImageFont.load_default()
    return ImageFont.truetype(str(path), size)


@lru_cache(maxsize=1)
def _load_logo(size: int = 40) -> Image.Image | None:
    if not _LOGO_PATH.exists():
        return None
    with Image.open(_LOGO_PATH) as img:
        return img.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)


# ---------------------------------------------------------------------------
#  Formatting
# ---------------------------------------------------------------------------


def _format_distance(meters: float, *, force_meters: bool = False) -> str:
    if force_meters or meters < 1000:
        return f"{int(meters)} m"
    km = meters / 1000
    if km == int(km):
        return f"{int(km)} km"
    return f"{km:.2f} km"


def _format_duration(seconds: int) -> str:
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _format_pace(sec_per_km: float) -> str:
    m, s = divmod(int(sec_per_km), 60)
    return f"{m}:{s:02d} /km"


def _format_swim_pace(sec_per_100m: float) -> str:
    m, s = divmod(int(sec_per_100m), 60)
    return f"{m}:{s:02d} /100m"


# ---------------------------------------------------------------------------
#  GPS polyline rendering
# ---------------------------------------------------------------------------


def _render_polyline(
    draw: ImageDraw.ImageDraw,
    latlng: list[tuple[float, float]],
    bbox: tuple[int, int, int, int],
    color: str,
    line_width: int = 4,
) -> None:
    """Draw GPS track on the image within the given bounding box."""
    if len(latlng) < 2:
        return

    lats = [p[0] for p in latlng]
    lngs = [p[1] for p in latlng]
    min_lat, max_lat = min(lats), max(lats)
    min_lng, max_lng = min(lngs), max(lngs)

    lat_range = max_lat - min_lat
    lng_range = max_lng - min_lng
    if lat_range == 0:
        lat_range = 0.001
    if lng_range == 0:
        lng_range = 0.001

    x0, y0, x1, y1 = bbox
    w = x1 - x0
    h = y1 - y0

    # Maintain aspect ratio with padding
    padding = 40
    draw_w = w - 2 * padding
    draw_h = h - 2 * padding

    # Scale to fit while preserving aspect ratio
    scale_x = draw_w / lng_range
    scale_y = draw_h / lat_range
    scale = min(scale_x, scale_y)

    # Center the track
    track_w = lng_range * scale
    track_h = lat_range * scale
    offset_x = x0 + padding + (draw_w - track_w) / 2
    offset_y = y0 + padding + (draw_h - track_h) / 2

    points = []
    for lat, lng in latlng:
        px = offset_x + (lng - min_lng) * scale
        py = offset_y + (max_lat - lat) * scale  # flip Y axis
        points.append((px, py))

    # Draw track with rounded joints
    draw.line(points, fill=color, width=line_width, joint="curve")

    # Draw start/end dots
    if len(points) >= 2:
        r = line_width + 2
        sx, sy = points[0]
        draw.ellipse([sx - r, sy - r, sx + r, sy + r], fill=color)
        ex, ey = points[-1]
        draw.ellipse([ex - r, ey - r, ex + r, ey + r], fill=color)


# ---------------------------------------------------------------------------
#  Card rendering
# ---------------------------------------------------------------------------


def render_workout_card(data: WorkoutCardData) -> bytes:
    """Render a 1080x1920 story workout card and return PNG bytes."""
    return _render_story(data)


def _get_sport_color(sport: str) -> str:
    return SPORT_COLORS.get(sport, SPORT_COLORS["Other"])


def _draw_brand(img: Image.Image, draw: ImageDraw.ImageDraw, y: int, logo_size: int, font_size: int) -> None:
    """Draw centered logo + ENDURAI.ME text."""
    logo = _load_logo(logo_size)
    brand_font = _font("Bold", font_size)
    brand_text = "ENDURAI.ME"

    text_bbox = draw.textbbox((0, 0), brand_text, font=brand_font)
    text_w = text_bbox[2] - text_bbox[0]
    logo_w = logo.size[0] if logo else 0
    gap = 16
    total_w = logo_w + gap + text_w if logo else text_w
    start_x = (img.width - total_w) // 2

    if logo:
        img.paste(logo, (start_x, y - logo.size[1] // 2), logo)
        draw.text((start_x + logo_w + gap, y), brand_text, font=brand_font, fill=TEXT_WHITE, anchor="lm")
    else:
        draw.text((img.width // 2, y), brand_text, font=brand_font, fill=TEXT_WHITE, anchor="mm")


def _draw_metrics(draw: ImageDraw.ImageDraw, data: WorkoutCardData, y: int, w: int, label_size: int, value_size: int):
    """Draw metrics row centered."""
    metrics = _build_metrics(data)
    if not metrics:
        return
    col_w = w // len(metrics)
    for i, (label, value) in enumerate(metrics):
        cx = col_w * i + col_w // 2
        draw.text((cx, y), label.upper(), font=_font("Bold", label_size), fill=TEXT_DIM, anchor="mt")
        draw.text((cx, y + label_size + 12), value, font=_font("Bold", value_size), fill=TEXT_WHITE, anchor="mt")


def _render_story(data: WorkoutCardData) -> bytes:
    """Render 1080x1920 story card."""
    W, H = 1080, 1920
    accent = _get_sport_color(data.sport_type)
    label = SPORT_LABEL.get(data.sport_type, "WORKOUT")

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # GPS track area — always brand blue, thicker line
    track_color = "#4A90D9"
    track_top = 120
    track_bottom = 1050
    if data.latlng and len(data.latlng) >= 2:
        _render_polyline(draw, data.latlng, (40, track_top, W - 40, track_bottom), track_color, line_width=9)
    else:
        big_font = _font("Bold", 120)
        draw.text((W // 2, (track_top + track_bottom) // 2), label, font=big_font, fill=accent, anchor="mm")

    # Brand
    _draw_brand(img, draw, 1150, logo_size=72, font_size=42)

    # Metrics (labels uppercase)
    _draw_metrics(draw, data, 1290, W, label_size=28, value_size=62)

    # AI text
    if data.ai_text:
        _draw_wrapped_text(
            draw,
            data.ai_text,
            (80, 1480, W - 80),
            _font("Regular", 36),
            TEXT_LIGHT,
            line_spacing=24,
            max_lines=5,
        )

    # Slogan at bottom
    draw.text((W // 2, H - 70), "Reads your body, not data", font=_font("Bold", 34), fill=TEXT_WHITE, anchor="mm")

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------


def _build_metrics(data: WorkoutCardData) -> list[tuple[str, str]]:
    """Build list of (label, value) metric pairs."""
    is_swim = data.sport_type == "Swim"
    metrics = []
    if data.distance_m is not None:
        metrics.append(("Distance", _format_distance(data.distance_m, force_meters=is_swim)))
    # Middle metric: power for bike, pace for run/swim
    is_bike = data.sport_type == "Ride"
    if is_bike and data.avg_power is not None:
        metrics.append(("Power", f"{data.avg_power} W"))
    elif is_swim and data.avg_pace_sec_per_km is not None:
        sec_per_100m = data.avg_pace_sec_per_km / 10
        metrics.append(("Pace", _format_swim_pace(sec_per_100m)))
    elif data.avg_pace_sec_per_km is not None:
        metrics.append(("Pace", _format_pace(data.avg_pace_sec_per_km)))
    elif data.avg_power is not None:
        metrics.append(("Power", f"{data.avg_power} W"))
    if data.duration_sec is not None:
        metrics.append(("Time", _format_duration(data.duration_sec)))
    return metrics


def _draw_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    bbox: tuple[int, int, int],
    font: ImageFont.FreeTypeFont,
    color: str,
    line_spacing: int = 6,
    max_lines: int = 4,
) -> None:
    """Draw text wrapped to fit within bbox width."""
    x, y, max_x = bbox
    max_width = max_x - x
    words = text.split()
    lines: list[str] = []
    current_line = ""

    for word in words:
        test_line = f"{current_line} {word}".strip()
        w = draw.textlength(test_line, font=font)
        if w <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)

    for i, line in enumerate(lines[:max_lines]):
        if i == max_lines - 1 and i < len(lines) - 1:
            line = line[:-3] + "..." if len(line) > 3 else "..."
        text_bbox = draw.textbbox((0, 0), line, font=font)
        line_h = text_bbox[3] - text_bbox[1]
        # Center horizontally
        line_w = draw.textlength(line, font=font)
        lx = x + (max_width - line_w) / 2
        draw.text((lx, y + i * (line_h + line_spacing)), line, font=font, fill=color)
