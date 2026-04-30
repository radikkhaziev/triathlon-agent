"""Render a ``generate_race_plan`` payload for Telegram (Markdown + PNG card).

The MCP tool returns a dict with shape::

    {
        "id": int | None,
        "dry_run": bool,
        "preliminary": bool,
        "model_version": str,
        "payload": {
            "plan": { "warmup", "legs", "fueling", "transitions", "contingencies", "headline" },
            "race": { "name", "date", "days_to_race", "discipline", "preliminary", ... },
            "preliminary": bool,
            "generated_at": str,
            "model_version": str,
        },
        "note": str | None,
    }

We render two surfaces:

1. ``render_plan_markdown`` — Telegram message body, escaped for the legacy
   ``Markdown`` parse mode. We pick legacy Markdown (not ``MarkdownV2``) because
   the rest of the bot already does — keeps fallback behaviour consistent.
2. ``render_race_plan_card`` — printable PNG card (1080x1350 portrait) with key
   splits the athlete can save to their phone. Same Inter font family as
   ``data/card_renderer`` so the brand stays coherent.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_FONT_DIR = _STATIC_DIR / "fonts"


# ---------------------------------------------------------------------------
# Markdown renderer (Telegram legacy Markdown)
# ---------------------------------------------------------------------------


# Telegram legacy Markdown special chars. Escaping is intentionally minimal —
# we wrap the user-controlled text values, not headings, so the bot's own
# bold/italic still works.
_MD_ESCAPE = str.maketrans({"_": r"\_", "*": r"\*", "[": r"\[", "`": r"\`"})


def _md_escape(s: Any) -> str:
    if s is None:
        return ""
    return str(s).translate(_MD_ESCAPE)


def _format_pacing(pacing: dict[str, Any] | None) -> str:
    if not isinstance(pacing, dict):
        return ""
    low = _md_escape(pacing.get("low") or "")
    target = _md_escape(pacing.get("target") or "")
    cap = _md_escape(pacing.get("cap") or "")
    parts = [p for p in (low, target, cap) if p]
    if len(parts) == 3:
        return f"{low} → *{target}* → {cap}"
    return " / ".join(parts)


def _race_header(race: dict[str, Any], preliminary: bool) -> str:
    name = _md_escape(race.get("name") or "Race")
    date = _md_escape(race.get("date") or "")
    days = race.get("days_to_race")
    discipline = _md_escape(race.get("discipline") or "")

    sub_bits: list[str] = []
    if date:
        sub_bits.append(date)
    if isinstance(days, int):
        sub_bits.append(f"D-{days}")
    if discipline:
        sub_bits.append(discipline)
    sub = " · ".join(sub_bits)

    head = f"🏁 *Race plan — {name}*"
    if sub:
        head = f"{head}\n_{sub}_"
    if preliminary:
        # Athletes need to know the corridor will tighten closer to race day.
        head = f"{head}\n⚠️ _preliminary — will sharpen closer to race day_"
    return head


def _render_legs(legs: list[dict[str, Any]]) -> str:
    lines: list[str] = ["*Legs*"]
    for leg in legs or []:
        name = _md_escape(leg.get("leg") or "leg")
        dist = leg.get("distance")
        pacing = _format_pacing(leg.get("pacing"))
        hr = leg.get("hr_ceiling_bpm")
        notes = leg.get("notes")

        head_bits = [f"*{name}*"]
        if dist:
            head_bits.append(_md_escape(dist))
        head = " · ".join(head_bits)

        line = head
        if pacing:
            line += f"\n  pace: {pacing}"
        if isinstance(hr, int):
            line += f"\n  HR cap: {hr} bpm"
        if notes:
            line += f"\n  _{_md_escape(notes)}_"
        lines.append(line)
    return "\n".join(lines)


def _render_fueling(fueling: dict[str, Any] | None) -> str:
    if not isinstance(fueling, dict):
        return ""
    parts: list[str] = ["*Fueling*"]
    carbs = fueling.get("carbs_g_per_hour")
    fluid = fueling.get("fluid_ml_per_hour")
    sodium = fueling.get("sodium_mg_per_hour")
    notes = fueling.get("notes")

    metric_bits: list[str] = []
    if isinstance(carbs, int):
        metric_bits.append(f"{carbs} g/hr carbs")
    if isinstance(fluid, int):
        metric_bits.append(f"{fluid} ml/hr fluid")
    if isinstance(sodium, int):
        metric_bits.append(f"{sodium} mg/hr Na")
    if metric_bits:
        parts.append(" · ".join(metric_bits))
    if notes:
        parts.append(f"_{_md_escape(notes)}_")
    return "\n".join(parts)


def _render_transitions(transitions: list[dict[str, Any]] | None) -> str:
    if not transitions:
        return ""
    lines: list[str] = ["*Transitions*"]
    for t in transitions:
        name = _md_escape(t.get("name") or "T")
        target = t.get("target_time_sec")
        head = f"*{name}*"
        if isinstance(target, int):
            mm, ss = divmod(target, 60)
            head = f"{head} (target {mm}:{ss:02d})"
        lines.append(head)
        for item in t.get("checklist") or []:
            lines.append(f"  • {_md_escape(item)}")
    return "\n".join(lines)


def _render_contingencies(contingencies: list[dict[str, Any]] | None) -> str:
    if not contingencies:
        return ""
    lines: list[str] = ["*Contingencies*"]
    for c in contingencies:
        scenario = _md_escape(c.get("scenario") or "")
        plan = _md_escape(c.get("plan") or "")
        if scenario and plan:
            lines.append(f"• *{scenario}* — {plan}")
        elif scenario or plan:
            lines.append(f"• {scenario or plan}")
    return "\n".join(lines)


def render_plan_markdown(result: dict[str, Any]) -> str:
    """Render the MCP ``generate_race_plan`` result as Telegram Markdown.

    Tolerant of partial payloads — missing sections are silently dropped so the
    function never raises on an unexpected shape, only returns a shorter
    message. Caller is expected to fall back to plain text if the Markdown
    parse fails on Telegram's side (some user-supplied race names contain
    underscores/brackets that survive escaping but still trip Telegram).
    """
    payload = (result or {}).get("payload") or {}
    plan = payload.get("plan") or {}
    race = payload.get("race") or {}
    preliminary = bool(payload.get("preliminary") or result.get("preliminary"))

    sections: list[str] = [_race_header(race, preliminary)]
    headline = plan.get("headline")
    if headline:
        sections.append(f"_{_md_escape(headline)}_")

    warmup = plan.get("warmup")
    if warmup:
        sections.append(f"*Warmup*\n{_md_escape(warmup)}")

    legs = plan.get("legs") or []
    if legs:
        sections.append(_render_legs(legs))

    fueling = _render_fueling(plan.get("fueling"))
    if fueling:
        sections.append(fueling)

    transitions = _render_transitions(plan.get("transitions"))
    if transitions:
        sections.append(transitions)

    contingencies = _render_contingencies(plan.get("contingencies"))
    if contingencies:
        sections.append(contingencies)

    return "\n\n".join(s for s in sections if s)


# ---------------------------------------------------------------------------
# PNG card renderer (1080x1350 portrait; printable + phone-friendly)
# ---------------------------------------------------------------------------


_BG = "#0B1120"
_FG = "#F8FAFC"
_DIM = "#94A3B8"
_ACCENT = "#22D3EE"
_ACCENT_WARM = "#FB923C"


@lru_cache(maxsize=20)
def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    """Inter font with graceful default fallback (mirrors data/card_renderer)."""
    path = _FONT_DIR / f"Inter-{weight}.ttf"
    if not path.exists():
        logger.warning("Font not found: %s, falling back to default", path)
        return ImageFont.load_default()
    return ImageFont.truetype(str(path), size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Greedy word-wrap that respects ``draw.textlength`` (font metrics)."""
    if not text:
        return []
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        candidate = f"{cur} {w}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            cur = candidate
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _split_summary(leg: dict[str, Any]) -> str:
    """One-line summary of a leg's pacing corridor for the card.

    Falls back to empty string when ``pacing`` is missing — caller decides
    whether to skip the leg row.
    """
    pacing = leg.get("pacing")
    if not isinstance(pacing, dict):
        return ""
    target = pacing.get("target")
    cap = pacing.get("cap")
    bits: list[str] = []
    if target:
        bits.append(f"target {target}")
    if cap:
        bits.append(f"cap {cap}")
    hr = leg.get("hr_ceiling_bpm")
    if isinstance(hr, int):
        bits.append(f"HR≤{hr}")
    return "  ·  ".join(bits)


def render_race_plan_card(result: dict[str, Any]) -> bytes:
    """Render a printable race-plan PNG. Returns raw PNG bytes.

    Layout — 1080x1350 portrait, dark navy background:
      1. Header band: race name, date, D-N
      2. Headline mantra (italic-style with Medium weight)
      3. Splits table: leg | corridor | HR cap
      4. Fueling line: carbs g/hr + (fluid / sodium if present)
      5. Footer: "endurai.me · race plan"
    """
    payload = (result or {}).get("payload") or {}
    plan = payload.get("plan") or {}
    race = payload.get("race") or {}
    preliminary = bool(payload.get("preliminary") or result.get("preliminary"))

    W, H = 1080, 1350
    img = Image.new("RGB", (W, H), _BG)
    draw = ImageDraw.Draw(img)

    margin_x = 64
    y = 80

    # Accent bar
    draw.rectangle([margin_x, y, margin_x + 80, y + 8], fill=_ACCENT)
    y += 28

    # Race name
    name = str(race.get("name") or "Race plan")
    name_font = _font("Bold", 64)
    name_lines = _wrap(draw, name, name_font, W - 2 * margin_x)
    for line in name_lines[:2]:  # cap at 2 lines so layout doesn't blow up
        draw.text((margin_x, y), line, fill=_FG, font=name_font)
        y += 76

    # Sub: date · D-N · discipline
    sub_font = _font("Medium", 30)
    sub_bits: list[str] = []
    if race.get("date"):
        sub_bits.append(str(race["date"]))
    days = race.get("days_to_race")
    if isinstance(days, int):
        sub_bits.append(f"D-{days}")
    if race.get("discipline"):
        sub_bits.append(str(race["discipline"]))
    if sub_bits:
        draw.text((margin_x, y), " · ".join(sub_bits), fill=_DIM, font=sub_font)
        y += 50

    if preliminary:
        prelim_font = _font("Medium", 24)
        draw.text((margin_x, y), "preliminary — corridor will sharpen closer to race day", fill=_ACCENT_WARM, font=prelim_font)
        y += 40

    # Headline mantra
    headline = plan.get("headline")
    if headline:
        y += 16
        head_font = _font("Medium", 36)
        for line in _wrap(draw, str(headline), head_font, W - 2 * margin_x)[:3]:
            draw.text((margin_x, y), line, fill=_FG, font=head_font)
            y += 46

    # Splits section
    y += 32
    section_font = _font("Bold", 28)
    draw.text((margin_x, y), "SPLITS", fill=_ACCENT, font=section_font)
    y += 44

    leg_label_font = _font("Bold", 32)
    leg_detail_font = _font("Regular", 26)

    legs = plan.get("legs") or []
    # Cap at 6 legs — covers a tri (swim/T1/bike/T2/run plus a contingency line)
    # without overflowing the printable area.
    for leg in legs[:6]:
        leg_name = str(leg.get("leg") or "leg")
        dist = leg.get("distance")
        head = leg_name if not dist else f"{leg_name}  ·  {dist}"
        draw.text((margin_x, y), head, fill=_FG, font=leg_label_font)
        y += 38
        summary = _split_summary(leg)
        if summary:
            draw.text((margin_x, y), summary, fill=_DIM, font=leg_detail_font)
            y += 36
        else:
            y += 8
        y += 8

    # Fueling section
    fueling = plan.get("fueling") or {}
    if fueling:
        y += 16
        draw.text((margin_x, y), "FUELING", fill=_ACCENT, font=section_font)
        y += 44

        carbs = fueling.get("carbs_g_per_hour")
        fluid = fueling.get("fluid_ml_per_hour")
        sodium = fueling.get("sodium_mg_per_hour")
        bits: list[str] = []
        if isinstance(carbs, int):
            bits.append(f"{carbs} g/hr carbs")
        if isinstance(fluid, int):
            bits.append(f"{fluid} ml/hr fluid")
        if isinstance(sodium, int):
            bits.append(f"{sodium} mg/hr Na")
        if bits:
            draw.text((margin_x, y), "  ·  ".join(bits), fill=_FG, font=leg_label_font)
            y += 42

    # Footer
    footer_font = _font("Medium", 22)
    footer = "endurai.me  ·  race plan"
    draw.text((margin_x, H - 60), footer, fill=_DIM, font=footer_font)

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
