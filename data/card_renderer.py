"""Workout card PNG renderer (Pillow-based).

Generates Instagram story cards (1080x1920, transparent background)
with GPS polyline track, key metrics, AI insight, and endurai.me branding.

Also generates square (1080x1080) post-race recap cards that double as the
seed surface for V1 video sharing — the V0 PNG and the future video render
both compose against the same dataclass (``RaceRecapCardData``) so swapping
render targets later does not require re-deriving the layout.
"""

import logging
import re
from dataclasses import dataclass, field
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
    latlng: list[tuple[float | None, float | None]] | None = None


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
    latlng: list[tuple[float | None, float | None]],
    bbox: tuple[int, int, int, int],
    color: str,
    line_width: int = 4,
) -> None:
    """Draw GPS track on the image within the given bounding box.

    Tolerant to ``None`` values inside ``latlng`` points — GPS dropouts
    (tunnels, indoor segments, Intervals.icu sentinel samples) may produce
    ``[None, None]`` or ``[lat, None]`` entries. Mixing them with floats
    crashed ``min()`` / ``max()`` before (issue #249); now we filter to
    fully-populated points before the bounding-box math.
    """
    clean: list[tuple[float, float]] = [(lat, lng) for lat, lng in latlng if lat is not None and lng is not None]
    if len(clean) < 2:
        return

    lats = [p[0] for p in clean]
    lngs = [p[1] for p in clean]
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
    for lat, lng in clean:
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
    # Count non-None points at the caller so an all-None ``latlng`` (issue
    # #249: GPS dropouts that produced only ``(None, None)`` samples) falls
    # through to the emoji fallback below instead of silently rendering an
    # empty track area.
    valid_points = sum(1 for lat, lng in data.latlng if lat is not None and lng is not None) if data.latlng else 0
    if valid_points >= 2:
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


# ---------------------------------------------------------------------------
#  Race recap card (square 1080x1080)
#
#  Triggered post-race on ``Activity.is_race == True``. Acceptance is set in
#  END-65: readable on phone screen at 1080x1080. We keep the renderer pure
#  (dataclass in → bytes out) so the same composition can later be swapped
#  to a video render target without touching the upstream actor.
# ---------------------------------------------------------------------------

# Result-vs-goal coloring. Green if at/under goal, red if over. Conservative
# palette — bright Strava-green and a desaturated red so the delta still
# reads on Telegram light + dark themes without screaming.
DELTA_GOOD = "#00D26A"
DELTA_BAD = "#FF6B6B"

# HR drift bar: cool → warm gradient, brightest segment is the highest
# quartile so the eye lands on whichever quarter spiked. The four-step
# palette is hand-tuned for additive-light displays — phones, not print.
HR_BAR_COLORS = ("#4ADE80", "#FACC15", "#FB923C", "#F87171")


@dataclass
class RaceSplit:
    """One row in the race recap splits panel.

    Tri legs use ``label`` ∈ {"Swim", "T1", "Bike", "T2", "Run"}; single-sport
    races use km/lap labels (e.g. "K1".."K5"). ``distance_m`` is optional —
    transitions (T1/T2) and time-only laps render without a distance column.
    """

    label: str
    time_sec: int
    distance_m: float | None = None


@dataclass
class RaceRecapCardData:
    race_name: str
    sport_type: str
    finish_time_sec: int | None = None
    goal_time_sec: int | None = None
    distance_m: float | None = None
    splits: list[RaceSplit] = field(default_factory=list)
    avg_hr_quarters: list[int | None] | None = None  # exactly 4 entries when present
    rpe: int | None = None  # Borg CR-10 (1..10)
    race_day_tsb: float | None = None
    race_day_recovery_score: float | None = None
    ai_text: str | None = None


def render_race_recap_card(data: RaceRecapCardData) -> bytes:
    """Render a 1080x1080 race recap card and return PNG bytes.

    Layout (top → bottom):
      * Race name (Bold, wrapped to 2 lines max)
      * Finish time (Black) + goal-delta strip (green/red)
      * Splits panel (up to 6 rows)
      * HR drift quartile bar (when ``avg_hr_quarters`` has ≥1 value)
      * Stat tiles (RPE / TSB / Recovery)
      * AI 2-sentence narrative
      * ``endurai.me`` footer
    """
    return _render_race_recap(data)


def _render_race_recap(data: RaceRecapCardData) -> bytes:
    W, H = 1080, 1080

    # Solid-dark background — the card is sent as a Telegram document, not a
    # Story overlay, so a transparent PNG would composite onto the user's
    # chat theme and hurt readability. Near-black (#0B1020) keeps the
    # green/red deltas saturated and matches the workout-card palette when
    # both are previewed side by side.
    img = Image.new("RGBA", (W, H), (11, 16, 32, 255))
    draw = ImageDraw.Draw(img)

    pad = 60
    cursor = pad

    # 1) Race name — Bold 56, wrapped to 2 lines max so an Ironman 70.3
    # mouthful doesn't shove the layout downward unpredictably.
    name_font = _font("Bold", 56)
    name_lines = _wrap_to_lines(draw, data.race_name or "Race", name_font, W - 2 * pad, max_lines=2)
    for line in name_lines:
        draw.text((W // 2, cursor), line, font=name_font, fill=TEXT_WHITE, anchor="mt")
        cursor += 64
    cursor += 12

    # 2) Finish time — Inter Black 132 monospace-feel, value dominates.
    finish_font = _font("Black", 132)
    finish_text = _format_duration(data.finish_time_sec) if data.finish_time_sec else "—"
    draw.text((W // 2, cursor), finish_text, font=finish_font, fill=TEXT_WHITE, anchor="mt")
    cursor += 144

    # 3) Goal delta — vs goal (green/red) on its own row.
    delta_font = _font("Medium", 36)
    delta_text, delta_color = _goal_delta(data.finish_time_sec, data.goal_time_sec)
    if delta_text:
        draw.text((W // 2, cursor), delta_text, font=delta_font, fill=delta_color, anchor="mt")
    cursor += 56

    # 4) Splits panel — rendered as a centered table. We cap at 6 rows so
    # an interval-rich Ironman run doesn't blow past the stat tiles below.
    splits = list(data.splits or [])
    if splits:
        if len(splits) > 6:
            splits = splits[:5] + splits[-1:]
        cursor = _draw_splits_panel(draw, splits, top=cursor + 12, w=W, pad=pad)

    # 5) HR drift bar — 4 quartile segments + per-segment bpm label. Skipped
    # entirely when no quartile has a value (indoor pool, missing HR strap).
    quarters = list(data.avg_hr_quarters or [])
    if any(q is not None for q in quarters):
        cursor = _draw_hr_drift(draw, quarters, top=cursor + 16, w=W, pad=pad)

    # 6) Stat tiles — RPE / TSB / Recovery as a 3-column row. Renders only
    # the columns we actually have data for, so an unrated race still looks
    # intentional rather than gappy.
    cursor = _draw_recap_stats(draw, data, top=cursor + 24, w=W, pad=pad)

    # 7) AI narrative — coach tone, plain text (markdown stripped same as
    # the workout card).
    if data.ai_text:
        narrative_top = cursor + 28
        _draw_wrapped_text(
            draw,
            _strip_markdown(data.ai_text),
            (pad, narrative_top, W - pad),
            _font("Regular", 30),
            TEXT_LIGHT,
            line_spacing=14,
            max_lines=3,
        )

    # 8) Footer — wordmark URL, never moves; positioned absolutely so it
    # ignores cursor drift from optional sections above.
    footer_font = _font("Medium", 28)
    draw.text((W // 2, H - 50), "endurai.me", font=footer_font, fill=TEXT_LABEL, anchor="mm")

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _goal_delta(finish_sec: int | None, goal_sec: int | None) -> tuple[str, str]:
    """Format the delta-vs-goal subtitle and pick its color.

    Returns ``("", TEXT_LIGHT)`` when either side is missing — the caller
    skips the row entirely. The Russian/English split lives upstream
    (the AI narrative); this string is intentionally English-only.
    """
    if not finish_sec or not goal_sec:
        return "", TEXT_LIGHT
    delta = finish_sec - goal_sec
    if delta == 0:
        return "On goal", DELTA_GOOD
    sign = "−" if delta < 0 else "+"
    color = DELTA_GOOD if delta < 0 else DELTA_BAD
    return f"{sign}{_format_duration(abs(delta))} vs goal", color


def _draw_splits_panel(
    draw: ImageDraw.ImageDraw,
    splits: list[RaceSplit],
    *,
    top: int,
    w: int,
    pad: int,
) -> int:
    """Render the splits stack and return the y cursor below the panel."""
    label_font = _font("Medium", 28)
    value_font = _font("Bold", 36)
    row_h = 48
    inner_w = w - 2 * pad

    # Two-column layout: label left, time right. Distance (when present)
    # sits inline with the label as a faint suffix so the eye still scans
    # a single column for legs.
    for i, split in enumerate(splits):
        y = top + i * row_h
        label = split.label
        if split.distance_m and split.distance_m >= 100:
            label = f"{label}  ·  {_format_distance(split.distance_m)}"
        draw.text((pad, y), label, font=label_font, fill=TEXT_LIGHT, anchor="lm")
        time_text = _format_duration(int(split.time_sec))
        draw.text((pad + inner_w, y), time_text, font=value_font, fill=TEXT_WHITE, anchor="rm")

    return top + len(splits) * row_h


def _draw_hr_drift(
    draw: ImageDraw.ImageDraw,
    quarters: list[int | None],
    *,
    top: int,
    w: int,
    pad: int,
) -> int:
    """Render the HR-by-quarter strip and return the y cursor below it.

    Behavior contract:
      * Always allocates four equal-width slots, even if some quarters are
        ``None`` — a missing third quarter still shows Q4 in its rightmost
        slot rather than reflowing left, which would mis-align the time
        axis with the splits panel above.
      * The brightest color (``HR_BAR_COLORS[-1]``) is reused for whichever
        quartile is highest, not always Q4 — drift can plateau or recover
        late, and we want the visual emphasis on the actual peak.
    """
    inner_w = w - 2 * pad
    label_font = _font("Medium", 22)
    value_font = _font("Bold", 28)

    label_y = top
    bar_y = top + 26
    bar_h = 14
    value_y = bar_y + bar_h + 10

    # Header row
    draw.text((pad, label_y), "HR drift (avg bpm by quarter)", font=label_font, fill=TEXT_LABEL, anchor="lm")

    # Pad to exactly 4 entries so the layout math doesn't divide-by-zero
    # on a partially-filled stream.
    qs = (quarters + [None, None, None, None])[:4]
    valid = [q for q in qs if q is not None]
    if not valid:
        return value_y + 28
    peak = max(valid)

    seg_w = inner_w / 4
    for idx, q in enumerate(qs):
        x0 = pad + int(seg_w * idx) + 4
        x1 = pad + int(seg_w * (idx + 1)) - 4
        # Empty quartile → muted slot; full quartile → palette color, with
        # the peak slot upgraded to the warmest tone.
        if q is None:
            color = "#1F2937"
        elif q == peak:
            color = HR_BAR_COLORS[3]
        else:
            color = HR_BAR_COLORS[idx]
        draw.rounded_rectangle((x0, bar_y, x1, bar_y + bar_h), radius=6, fill=color)

        cx = pad + int(seg_w * idx + seg_w / 2)
        text = f"{q}" if q is not None else "—"
        draw.text((cx, value_y), text, font=value_font, fill=TEXT_WHITE, anchor="mt")

    return value_y + 36


def _draw_recap_stats(
    draw: ImageDraw.ImageDraw,
    data: RaceRecapCardData,
    *,
    top: int,
    w: int,
    pad: int,
) -> int:
    """Render the RPE / TSB / Recovery tiles and return the y cursor below."""
    tiles: list[tuple[str, str]] = []
    if data.rpe is not None:
        tiles.append(("RPE", f"{data.rpe}/10"))
    if data.race_day_tsb is not None:
        tiles.append(("TSB", f"{data.race_day_tsb:+.0f}"))
    if data.race_day_recovery_score is not None:
        # Recovery score arrives 0..1 from HRV/wellness; display as a
        # percentage so the tile reads at a glance ("82") without forcing
        # the reader to mentally normalize. Out-of-range values are clamped
        # so a stale 1.4 from a buggy HRV ingest does not surface as 140.
        score = max(0.0, min(1.0, float(data.race_day_recovery_score)))
        tiles.append(("Recovery", f"{int(round(score * 100))}"))

    if not tiles:
        return top

    label_font = _font("Medium", 24)
    value_font = _font("Bold", 56)
    inner_w = w - 2 * pad
    col_w = inner_w / len(tiles)
    label_y = top
    value_y = top + 30
    for i, (label, value) in enumerate(tiles):
        cx = pad + int(col_w * i + col_w / 2)
        draw.text((cx, label_y), label.upper(), font=label_font, fill=TEXT_LABEL, anchor="mt")
        draw.text((cx, value_y), value, font=value_font, fill=TEXT_WHITE, anchor="mt")
    return value_y + 70


def _wrap_to_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    max_lines: int = 2,
) -> list[str]:
    """Greedy word-wrap into at most ``max_lines`` lines (last line ellipsized)."""
    if not text:
        return [""]
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    if not lines:
        return [text[: max(1, max_lines)]]
    if len(lines) == max_lines and len(" ".join(lines).split()) < len(words):
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > max_width:
            last = last[:-1]
        lines[-1] = (last + "…") if last else "…"
    return lines
