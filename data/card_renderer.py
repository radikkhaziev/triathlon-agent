"""Workout card PNG renderer (Pillow-based).

Generates Instagram story cards (1080x1920, transparent background)
with GPS polyline track, key metrics, AI insight, and endurai.me branding.
"""

import logging
import re
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

# Monochrome emoji fallback for the track area when GPS data is absent
# (indoor trainer, pool swim, gym). Rendered white via NotoEmoji-Regular,
# same visual weight as the rest of the card typography.
SPORT_EMOJI = {
    "Swim": "\U0001f3ca",  # 🏊 swimmer
    "Ride": "\U0001f6b4",  # 🚴 cyclist
    "Run": "\U0001f3c3",  # 🏃 runner
    "Other": "\U0001f4aa",  # 💪 flexed biceps
}

TEXT_WHITE = "#FFFFFF"
TEXT_DIM = "#6B7280"  # legacy — kept for any non-label dimmed text
TEXT_LIGHT = "#D1D5DB"
# Metric labels (DISTANCE / PACE / TIME) use a brighter gray than TEXT_DIM.
# The previous #6B7280 (zinc-500) clipped below WCAG AA on mid-range phone
# screens with Stories' photo backgrounds behind. #A1A1AA is zinc-400 —
# subdued enough to keep the value typography dominant, bright enough to
# survive bright/busy backgrounds.
TEXT_LABEL = "#A1A1AA"


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


@lru_cache(maxsize=4)
def _emoji_font(size: int) -> ImageFont.FreeTypeFont | None:
    """NotoEmoji-Regular (monochrome) — loaded lazily, cached by size.

    Returns ``None`` if the font file is missing so callers can skip
    emoji rendering rather than crash. Used only for the no-GPS
    track-area fallback.
    """
    path = _FONT_DIR / "NotoEmoji-Regular.ttf"
    if not path.exists():
        logger.warning("NotoEmoji font not found: %s, skipping emoji fallback", path)
        return None
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


def _tracked_text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, tracking: int) -> int:
    """Advance width of ``text`` rendered with per-glyph letter-spacing.

    Pillow's ``draw.text`` has no tracking parameter, so display-type headings
    that want extended spacing (Druk Wide / Maison Neue Extended style) must
    be composed glyph-by-glyph. This helper predicts the final block width
    so we can center-align the composed row.

    The last glyph's advance does NOT include trailing tracking — we measure
    the glyph's actual advance, not ``textlength + tracking`` repeated — so
    centered blocks don't drift right by one ``tracking`` worth of padding.
    """
    if not text:
        return 0
    total = 0
    for i, ch in enumerate(text):
        total += int(draw.textlength(ch, font=font))
        if i != len(text) - 1:
            total += tracking
    return total


def _draw_tracked_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: str,
    tracking: int,
    anchor: str = "lm",
) -> None:
    """Draw ``text`` with per-glyph tracking at ``xy``.

    ``anchor`` supports the subset we actually use:
      * ``lm`` — x = left edge, y = vertical center (default)
      * ``mm`` — x = horizontal center, y = vertical center
      * ``mt`` — x = horizontal center, y = top edge (for metric labels
                 where ``y`` is the row's top line)
    Other anchors raise — safer than silently mis-rendering at an
    unexpected anchor mode.
    """
    if anchor not in ("lm", "mm", "mt"):
        raise ValueError(f"_draw_tracked_text: unsupported anchor {anchor!r}")
    if not text:
        return

    total_w = _tracked_text_width(draw, text, font, tracking)
    x, y = xy
    if anchor == "lm":
        cursor, glyph_anchor = x, "lm"
    elif anchor == "mm":
        cursor, glyph_anchor = x - total_w // 2, "lm"
    else:  # "mt"
        cursor, glyph_anchor = x - total_w // 2, "lt"
    for ch in text:
        draw.text((cursor, y), ch, font=font, fill=fill, anchor=glyph_anchor)
        cursor += int(draw.textlength(ch, font=font)) + tracking


def _draw_brand(img: Image.Image, draw: ImageDraw.ImageDraw, y: int, font_size: int) -> None:
    """Draw the centered "ENDURAI" wordmark.

    Wordmark-only treatment (no app icon): the Black-weight display
    typography stands alone — the previous rounded-square app icon from
    ``webapp/public/android-chrome-512x512.png`` sat in a different visual
    language next to heavy display type, so we dropped it to match the
    Maison Neue Extended / Druk Wide aesthetic the designer called for.

    If ``Inter-Black.ttf`` is missing, ``_font`` logs and falls back to the
    Pillow default — card still renders, just without the heavy treatment.
    """
    brand_font = _font("Black", font_size)
    tracking = max(4, font_size * 6 // 100)  # ~6 % of the em, floor at 4 px
    _draw_tracked_text(draw, (img.width // 2, y), "ENDURAI", brand_font, TEXT_WHITE, tracking, anchor="mm")


# ---------------------------------------------------------------------------
#  Instagram/TikTok Story safe zones (9:16 canvas = 1080 × 1920)
#
#  The top ~250 px hide behind the app's UI chrome (username, timestamp,
#  close button) and the bottom ~220 px are eaten by the reply bar + like/
#  share controls. We lay everything out inside SAFE_TOP..SAFE_BOTTOM so
#  the card still reads correctly when consumed as a Story. Outside that
#  band the canvas stays transparent — it's fine if our track bleeds a bit
#  under the UI chrome visually, but text + metrics must not.
# ---------------------------------------------------------------------------
SAFE_TOP = 250
SAFE_BOTTOM = 1700


def _draw_metrics(draw: ImageDraw.ImageDraw, data: WorkoutCardData, y: int, w: int, label_size: int, value_size: int):
    """Draw metrics row centered. Label Medium/500, value Bold/700.

    Labels are rendered with letter-spacing (~10 % of the em) so they read
    as a UI header group rather than a run-on caption — the classic
    metric-card pattern (Strava / Nike / Garmin Connect all do this).
    Combined with the brighter ``TEXT_LABEL`` gray, this makes the label
    row survive busy Story backgrounds and mid-range phone screens.

    Gap between label bottom and value top is 15 px, per the layout spec:
    ``label_y + label_size + 15 == value_y``.
    """
    metrics = _build_metrics(data)
    if not metrics:
        return
    col_w = w // len(metrics)
    label_font = _font("Medium", label_size)
    label_tracking = max(2, label_size * 10 // 100)  # ~10 % of em, floor at 2 px
    value_font = _font("Bold", value_size)
    for i, (label, value) in enumerate(metrics):
        cx = col_w * i + col_w // 2
        _draw_tracked_text(draw, (cx, y), label.upper(), label_font, TEXT_LABEL, label_tracking, anchor="mt")
        draw.text((cx, y + label_size + 15), value, font=value_font, fill=TEXT_WHITE, anchor="mt")


def _render_story(data: WorkoutCardData) -> bytes:
    """Render 1080x1920 story card, laid out inside the Story safe zones."""
    W, H = 1080, 1920

    # Transparent background — the card composites onto the user's Story
    # photo (or the solid Telegram chat background when sent as a document).
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Vertical rhythm:
    #   Track bottom → Logo top: 40 px
    #   Logo bottom → Labels top: 70 px  (measured from TAGLINE bottom, since the
    #                                     tagline is part of the brand lockup)
    #   Labels bottom → Values top: 15 px  (baked into _draw_metrics)
    #   Values bottom → AI-blurb top: 40 px
    #
    # Track extended +40 px downward vs the original 1040 bound (track now
    # 330..1080 = 750 px tall) to add visual mass to the upper half — a pure
    # GPS polyline is too sparse to balance the dense labels+values+blurb
    # stack below. To keep the blurb from overflowing the bottom safe zone
    # after that shift, the values→blurb gap was tightened from 80 to 40.
    # Everything else below the track shifts +40 px; the bottom blurb ends
    # up at the same y as before (values pushed +40, gap pulled -40 = 0).
    track_color = "#4A90D9"
    track_top = 330
    track_bottom = 1080
    if data.latlng and len(data.latlng) >= 2:
        _render_polyline(draw, data.latlng, (40, track_top, W - 40, track_bottom), track_color, line_width=9)
    else:
        # No GPS (indoor trainer, pool swim, gym) — fill the track area with a
        # big white sport emoji so the upper half of the card has visual mass
        # that balances the metrics + blurb stack below. Monochrome NotoEmoji
        # rendered via ``fill=TEXT_WHITE`` keeps it consistent with the rest
        # of the typography; if the font file is missing we skip silently and
        # the track area stays empty (same as before).
        emoji_char = SPORT_EMOJI.get(data.sport_type, SPORT_EMOJI["Other"])
        emoji_font = _emoji_font(400)
        if emoji_font is not None:
            draw.text(
                (W // 2, (track_top + track_bottom) // 2),
                emoji_char,
                font=emoji_font,
                fill=TEXT_WHITE,
                anchor="mm",
            )

    # Brand wordmark — "ENDURAI" at 96 px Inter Black (900) with tracking.
    # Wordmark-only (no app icon) — see `_draw_brand` for rationale.
    #
    # wordmark_top = track_bottom + 40 = 1120 (spec'd "Track → Logo: 40 px")
    # wordmark center = wordmark_top + 96/2 = 1168
    # wordmark_bottom = wordmark_top + 96 = 1216
    wordmark_size = 96
    wordmark_top = track_bottom + 40
    _draw_brand(img, draw, wordmark_top + wordmark_size // 2, font_size=wordmark_size)
    wordmark_bottom = wordmark_top + wordmark_size

    # Tagline — "Reads your body, not data" as a promise bound to the wordmark
    # (lockup), not a floating caption above the track. Inter Regular 32 sits
    # ~18 px below the wordmark so it reads as the same block.
    #
    # Regular (not Medium) gives a BLACK / Regular weight contrast that's a
    # classic "premium" pairing — the 96 px Black header stays the anchor,
    # and the thinner stroke of Regular at this size reads as editorial
    # support rather than a second heading. White (not dimmed) keeps the
    # claim confident; the weight drop handles the visual hierarchy.
    #
    # tagline_top = wordmark_bottom + 18 = 1234
    # tagline_bottom = tagline_top + 32 = 1266
    tagline_size = 32
    tagline_top = wordmark_bottom + 18
    draw.text(
        (W // 2, tagline_top),
        "Reads your body, not data",
        font=_font("Regular", tagline_size),
        fill=TEXT_WHITE,
        anchor="mt",
    )
    tagline_bottom = tagline_top + tagline_size

    # Metrics row — labels_top = tagline_bottom + 70 = 1266 + 70 = 1336.
    # Label 30 Medium + 15 gap + value 58 Bold → row spans 1336..1439.
    labels_top = tagline_bottom + 70
    label_size = 30
    value_size = 58
    _draw_metrics(draw, data, labels_top, W, label_size=label_size, value_size=value_size)
    values_bottom = labels_top + label_size + 15 + value_size

    # AI text — 40 px gap from values bottom (tightened from 80 to offset the
    # track's +40 px extension at the top — see the rhythm block above).
    # Strip markdown before rendering (Pillow has no markdown parser, so a
    # stray ``**bold**`` from Claude would render as literal asterisks).
    # The prompt in ``tasks/actors/card.py`` tells Claude "plain text only";
    # the stripper is belt-and-suspenders against model drift.
    #
    # White fill so the blurb pops on transparent Story backgrounds.
    # 3 lines max × (36 font + 24 spacing) = 180 px. blurb_top = 1479 →
    # blurb_bottom = 1659, safely under SAFE_BOTTOM=1700. Shrunk from the
    # prior 4-line budget after adding the tagline — Claude's prompt asks
    # for 2–3 sentences anyway, so 3 wrapped lines fit fine.
    if data.ai_text:
        blurb_top = values_bottom + 40
        _draw_wrapped_text(
            draw,
            _strip_markdown(data.ai_text),
            (80, blurb_top, W - 80),
            _font("Regular", 36),
            TEXT_WHITE,
            line_spacing=24,
            max_lines=3,
        )

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


# Regexes applied in order. Order matters: links before bare brackets, code
# fences before inline code, emphasis before bold so we don't eat the bold
# asterisks with the italic pattern. Each regex is anchored conservatively —
# we only strip markers we're confident about, never the surrounding text.
_MD_CODE_FENCE_RE = re.compile(r"```[a-zA-Z]*\n?(.*?)```", re.DOTALL)
_MD_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((?:[^)]+)\)")
_MD_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*|__([^_\n]+)__")
_MD_ITALIC_RE = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?!\*)|(?<![_\w])_([^_\n]+)_(?!_)")
_MD_HEADING_RE = re.compile(r"^\s*#{1,6}\s+", re.MULTILINE)
_MD_BULLET_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_MD_BLOCKQUOTE_RE = re.compile(r"^\s*>\s?", re.MULTILINE)


def _strip_markdown(text: str) -> str:
    """Remove Markdown markers Claude sometimes emits, keeping inner text.

    Covers the patterns we've seen leak into generated card copy:
    ``**bold**`` / ``__bold__``, ``*italic*`` / ``_italic_``, ``` `code` ```,
    ``[label](url)`` / ``![alt](url)``, list bullets, headings, blockquotes.
    Non-goals: HTML escapes, nested Markdown, math/LaTeX — if those show up
    we'll extend this rather than reach for a full Markdown parser (the card
    is a 2-3 sentence blurb, so a dependency like ``mistune`` isn't worth it).
    """
    if not text:
        return text
    text = _MD_CODE_FENCE_RE.sub(lambda m: m.group(1), text)
    text = _MD_INLINE_CODE_RE.sub(lambda m: m.group(1), text)
    text = _MD_IMAGE_RE.sub(lambda m: m.group(1), text)
    text = _MD_LINK_RE.sub(lambda m: m.group(1), text)
    text = _MD_BOLD_RE.sub(lambda m: m.group(1) or m.group(2), text)
    text = _MD_ITALIC_RE.sub(lambda m: m.group(1) or m.group(2), text)
    text = _MD_HEADING_RE.sub("", text)
    text = _MD_BULLET_RE.sub("", text)
    text = _MD_BLOCKQUOTE_RE.sub("", text)
    # Collapse the whitespace we may have introduced at line boundaries.
    return re.sub(r"[ \t]+", " ", text).strip()


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
