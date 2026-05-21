"""Telegram-friendly renderer for race plans.

Keeps the actor (``tasks/actors/race_plan.py``) thin — Markdown formatting +
i18n + length budgeting all live here. Tested as a pure function (no DB,
no Telegram).

Constraints we work within:
- Telegram message limit: 4096 chars. We aim for <2500 to stay scannable on a
  phone screen and leave room for the inline keyboard. legs.notes are already
  capped at 200 chars per leg by the JSON schema (PR1 spec §3).
- No images, no headers, no long lists (per CLAUDE.md system prompt rules
  for Telegram-bound text).
- Markdown: bold + italic only. No tables (the bot's own system prompt
  already enforces this — see bot/prompts.py:330+).
"""

from __future__ import annotations

from typing import Any

from bot.i18n import _


def _section_header(title: str) -> str:
    """Bold section title.

    Uses ``**X**`` (CommonMark/Claude-style double-asterisk) — the renderer
    output is downstream-converted by ``markdown_to_telegram_html`` in
    ``tasks/tools.py``, which ONLY recognises ``**bold**`` / ``__bold__``.
    Single-asterisk Telegram-Markdown-V1 syntax would pass through as a
    literal asterisk on the athlete's screen. See review H1 (2026-05-09)."""
    return f"**{title}**"


def _format_pacing_corridor(low: str, target: str, cap: str) -> str:
    """``low → **target** → cap`` with the target bolded — matches the web
    LegRow corridor rendering so the athlete sees the same shape on both
    surfaces (web preview the night before, Telegram on race morning)."""
    return f"{low} → **{target}** → {cap}"


def _format_leg(leg: dict[str, Any]) -> list[str]:
    """One leg → 1-3 short lines.

    Notes use plain text, not italic — ``markdown_to_telegram_html`` doesn't
    handle ``_italic_`` and would render the underscores literally (review H1).
    Bold-as-emphasis is also avoided for notes to keep the visual hierarchy
    clean: leg name + corridor target are the only bold elements."""
    name = (leg.get("leg") or "?").capitalize()
    distance = leg.get("distance")
    pacing = leg.get("pacing") or {}
    hr = leg.get("hr_ceiling_bpm")
    notes = leg.get("notes")

    header_bits = [f"**{name}**"]
    if distance:
        header_bits.append(distance)
    lines = [" · ".join(header_bits)]

    if pacing.get("low") and pacing.get("target") and pacing.get("cap"):
        corridor = _format_pacing_corridor(pacing["low"], pacing["target"], pacing["cap"])
        if hr:
            corridor += f"  ({_('cap')} {hr} bpm)"
        lines.append(corridor)
    if notes:
        lines.append(notes)  # plain text — see docstring H1 note
    return lines


def _format_fueling(fueling: dict[str, Any]) -> list[str]:
    """Single line: rate + cadence note. Notes plain text (see _format_leg)."""
    if not fueling:
        return []
    parts: list[str] = []
    carbs = fueling.get("carbs_g_per_hour")
    if carbs:
        parts.append(f"{carbs}{_('g carbs/hr')}")
    fluid = fueling.get("fluid_ml_per_hour")
    if fluid:
        parts.append(f"{fluid}{_(' ml/hr')}")
    sodium = fueling.get("sodium_mg_per_hour")
    if sodium:
        parts.append(f"{sodium}{_(' mg sodium/hr')}")
    lines = [" · ".join(parts)] if parts else []
    notes = fueling.get("notes")
    if notes:
        lines.append(notes)
    return lines


def _format_contingency(c: dict[str, Any]) -> str:
    scenario = (c.get("scenario") or "?").capitalize()
    plan = c.get("plan") or ""
    return f"**{scenario}:** {plan}"


def render_race_plan_for_telegram(payload: dict[str, Any], event_name: str) -> str:
    """Render a RacePlan payload as a Markdown Telegram message.

    ``payload`` is the JSONB ``RacePlan.payload`` shape (see
    ``data/race_plan_service.py:_RACE_PLAN_SCHEMA``); ``event_name`` is the
    goal's ``event_name`` (``payload.race.name`` is the inline snapshot but
    we accept it explicitly so the caller can clamp/sanitise once at the
    actor boundary).

    Output is intentionally compact — full plan details live in the webapp;
    the Telegram push is a race-morning recall surface, not the canonical
    document.
    """
    plan = payload.get("plan") or {}
    headline = plan.get("headline")
    warmup = plan.get("warmup")
    legs = plan.get("legs") or []
    fueling = plan.get("fueling") or {}
    transitions = plan.get("transitions") or []
    contingencies = plan.get("contingencies") or []

    lines: list[str] = []
    lines.append(f"🏁 **{_('Tomorrow:')} {event_name}**")
    if headline:
        # Plain text — see _format_leg docstring on H1 italic constraint.
        lines.append(headline)
    lines.append("")  # blank line

    if warmup:
        lines.append(_section_header(_("Warmup")))
        lines.append(warmup)
        lines.append("")

    if legs:
        lines.append(_section_header(_("Legs")))
        for leg in legs:
            lines.extend(_format_leg(leg))
            lines.append("")  # blank line between legs

    if fueling:
        lines.append(_section_header(_("Fueling")))
        lines.extend(_format_fueling(fueling))
        lines.append("")

    if transitions:
        lines.append(_section_header(_("Transitions")))
        for t in transitions:
            name = t.get("name") or "?"
            checklist = t.get("checklist") or []
            lines.append(f"**{name}:** " + " · ".join(checklist))
        lines.append("")

    if contingencies:
        lines.append(_section_header(_("Contingencies")))
        for c in contingencies:
            lines.append(_format_contingency(c))
        lines.append("")

    # Drop trailing blank lines for clean message tail.
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def build_open_in_webapp_keyboard(api_base_url: str) -> dict[str, Any]:
    """Inline keyboard with a single ``Open in webapp`` button.

    Uses Telegram's ``web_app`` button type (Mini App launch) rather than a
    plain URL — keeps the athlete inside the Telegram client on race morning
    instead of bouncing to the system browser.
    """
    return {
        "inline_keyboard": [
            [
                {
                    "text": _("📊 Open full plan"),
                    "web_app": {"url": f"{api_base_url.rstrip('/')}/trends"},
                }
            ]
        ]
    }
