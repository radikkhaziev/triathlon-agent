"""MCP tools for workout exercise cards library and workout composition."""

import hashlib
import logging
import os
import re
from datetime import date

from jinja2 import Environment, FileSystemLoader

from config import settings
from data.database import ExerciseCardRow, WorkoutCardRow
from data.intervals_client import IntervalsClient
from mcp_server.app import mcp

logger = logging.getLogger(__name__)

VALID_SPORTS = frozenset({"Swim", "Ride", "Run", "Other"})

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TEMPLATES_DIR = os.path.join(_PROJECT_ROOT, "templates")
_STATIC_DIR = os.path.join(_PROJECT_ROOT, "static")

_jinja_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=True,
)

_EXERCISE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}[a-z0-9]$")


def _ensure_dirs():
    os.makedirs(os.path.join(_STATIC_DIR, "exercises"), exist_ok=True)
    os.makedirs(os.path.join(_STATIC_DIR, "workouts"), exist_ok=True)


def _build_card_context(
    card, *, sets: int | None = None, reps: int | None = None, duration_sec: int | None = None
) -> dict:
    """Build Jinja template context from an ExerciseCardRow with optional overrides."""
    actual_sets = sets or card.default_sets or 2
    actual_reps = reps or card.default_reps or 15
    actual_duration = duration_sec or card.default_duration_sec

    if actual_duration:
        sets_reps = f"{actual_sets} x {actual_duration}с"
        sets_reps_label = "подходы x время"
        total_sec = actual_sets * (actual_duration + 15)  # +15s rest between sets
    else:
        sets_reps = f"{actual_sets} x {actual_reps}"
        sets_reps_label = "подходы x повторы"
        total_sec = actual_sets * 40  # ~40s per set with rest

    duration_min = max(1, round(total_sec / 60))

    return {
        "exercise_id": card.id,
        "name_ru": card.name_ru,
        "name_en": card.name_en or "",
        "muscles": card.muscles or "",
        "equipment": card.equipment or "Без инвентаря",
        "group_tag": card.group_tag or "",
        "sets_reps": sets_reps,
        "sets_reps_label": sets_reps_label,
        "duration": f"~{duration_min} мин",
        "breath": card.breath or "",
        "animation_html": card.animation_html,
        "animation_css": card.animation_css,
        "steps": card.steps or [],
        "focus": card.focus or "",
    }


def _render_exercise_html(ctx: dict, *, standalone: bool = True) -> str:
    tmpl = _jinja_env.get_template("exercise_card.html")
    return tmpl.render(standalone=standalone, **ctx)


def _validate_exercise_id(exercise_id: str) -> str | None:
    """Validate exercise_id against allowed pattern. Returns error message or None."""
    if not _EXERCISE_ID_RE.match(exercise_id):
        return (
            f"Invalid exercise_id '{exercise_id}': must be 2-50 chars, "
            "lowercase alphanumeric with hyphens/underscores, no path separators"
        )
    return None


def _slugify(text: str) -> str:
    h = hashlib.md5(text.encode()).hexdigest()[:8]
    ascii_part = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:30]
    return f"{ascii_part}-{h}" if ascii_part else h


@mcp.tool()
async def create_exercise_card(
    exercise_id: str,
    name_ru: str,
    name_en: str,
    muscles: str,
    equipment: str,
    group_tag: str,
    default_sets: int,
    default_reps: int,
    steps: list[str],
    focus: str,
    animation_html: str,
    animation_css: str,
    breath: str = "",
    default_duration_sec: int | None = None,
) -> str:
    """Create an exercise card in the library.

    Provide metadata + unique animation (HTML + CSS for stick figure).
    Server renders full HTML from Jinja template with light theme.

    animation_html: HTML markup of the stick figure (~10-20 lines).
    Use exercise_id as CSS class prefix for all elements to avoid collisions.
    animation_css: CSS @keyframes and positioning (~30-50 lines).
    Prefix all selectors with .card-{exercise_id} for namespace isolation.

    See the clamshell example in docs/WORKOUT_CARDS.md for reference.
    """
    err = _validate_exercise_id(exercise_id)
    if err:
        return err

    _ensure_dirs()

    card = await ExerciseCardRow.save(
        exercise_id=exercise_id,
        name_ru=name_ru,
        name_en=name_en,
        muscles=muscles,
        equipment=equipment,
        group_tag=group_tag,
        default_sets=default_sets,
        default_reps=default_reps,
        default_duration_sec=default_duration_sec,
        steps=steps,
        focus=focus,
        breath=breath,
        animation_html=animation_html,
        animation_css=animation_css,
    )

    ctx = _build_card_context(card)
    html = _render_exercise_html(ctx, standalone=True)

    html_path = os.path.join(_STATIC_DIR, "exercises", f"{exercise_id}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    url = f"{settings.API_BASE_URL}/static/exercises/{exercise_id}.html"
    return f"Exercise card created: {name_ru} ({name_en})\nURL: {url}"


@mcp.tool()
async def update_exercise_card(
    exercise_id: str,
    name_ru: str | None = None,
    name_en: str | None = None,
    muscles: str | None = None,
    equipment: str | None = None,
    group_tag: str | None = None,
    default_sets: int | None = None,
    default_reps: int | None = None,
    default_duration_sec: int | None = None,
    steps: list[str] | None = None,
    focus: str | None = None,
    breath: str | None = None,
    animation_html: str | None = None,
    animation_css: str | None = None,
) -> str:
    """Update an existing exercise card.

    Only provided fields are updated. HTML file is re-rendered after update.
    """
    err = _validate_exercise_id(exercise_id)
    if err:
        return err

    existing = await ExerciseCardRow.get(exercise_id)
    if not existing:
        return f"Exercise card '{exercise_id}' not found in library."

    kwargs = {}
    for field, value in [
        ("name_ru", name_ru),
        ("name_en", name_en),
        ("muscles", muscles),
        ("equipment", equipment),
        ("group_tag", group_tag),
        ("default_sets", default_sets),
        ("default_reps", default_reps),
        ("default_duration_sec", default_duration_sec),
        ("steps", steps),
        ("focus", focus),
        ("breath", breath),
        ("animation_html", animation_html),
        ("animation_css", animation_css),
    ]:
        if value is not None:
            kwargs[field] = value

    if not kwargs:
        return "No fields to update."

    card = await ExerciseCardRow.update_fields(exercise_id, **kwargs)

    _ensure_dirs()
    ctx = _build_card_context(card)
    html = _render_exercise_html(ctx, standalone=True)

    html_path = os.path.join(_STATIC_DIR, "exercises", f"{exercise_id}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    url = f"{settings.API_BASE_URL}/static/exercises/{exercise_id}.html"
    return f"Exercise card updated: {card.name_ru}\nURL: {url}"


@mcp.tool()
async def list_exercise_cards(
    equipment: str | None = None,
    group_tag: str | None = None,
    muscles: str | None = None,
) -> dict:
    """List available exercise cards in the library.

    Returns exercise metadata (id, name, muscles, equipment, default reps).
    Use this to see what exercises are available before composing a workout.

    Optional filters: equipment ("Мини-петля"), group_tag ("День А"), muscles ("ягодичная").
    """
    cards = await ExerciseCardRow.get_list(equipment=equipment, group_tag=group_tag, muscles=muscles)
    return {
        "count": len(cards),
        "exercises": [
            {
                "id": c.id,
                "name_ru": c.name_ru,
                "name_en": c.name_en,
                "muscles": c.muscles,
                "equipment": c.equipment,
                "group_tag": c.group_tag,
                "default_sets": c.default_sets,
                "default_reps": c.default_reps,
                "default_duration_sec": c.default_duration_sec,
            }
            for c in cards
        ],
    }


@mcp.tool()
async def compose_workout(
    name: str,
    exercises: list[dict],
    target_date: str | None = None,
    push_to_intervals: bool = False,
    sport: str = "Other",
) -> str:
    """Compose a workout from exercise library cards.

    Each exercise entry: {"id": "exercise_id", "sets": N, "reps": N}
    or {"id": "exercise_id", "sets": N, "duration_sec": N} for timed exercises.
    Optional "note" field for per-exercise comments.

    Validates all exercise IDs before generation.
    Generates a single HTML page with all exercise cards inline.
    Returns URL to the workout page.

    If push_to_intervals=True, also creates a WORKOUT event in Intervals.icu.

    Args:
        name: Workout name (e.g. "Утренняя зарядка -- День Б").
        exercises: List of exercise entries with custom sets/reps.
        target_date: Date in YYYY-MM-DD format. Default: today.
        push_to_intervals: Create event in Intervals.icu calendar.
        sport: Sport type for Intervals.icu — "Swim", "Ride", "Run", "Other". Default: "Other".
    """
    if sport not in VALID_SPORTS:
        return f"Invalid sport '{sport}'. Must be one of: {', '.join(sorted(VALID_SPORTS))}"

    dt = date.fromisoformat(target_date) if target_date else date.today()
    date_str = str(dt)

    # Validate exercises structure
    for i, ex in enumerate(exercises):
        if not isinstance(ex, dict) or "id" not in ex:
            return f"Exercise entry #{i + 1} must be a dict with at least an 'id' field."

    # Validate all exercise IDs
    requested_ids = [e["id"] for e in exercises]
    found_cards = await ExerciseCardRow.get_by_ids(requested_ids)
    found_ids = {c.id for c in found_cards}
    missing = [eid for eid in requested_ids if eid not in found_ids]
    if missing:
        return f"Exercise IDs not found in library: {', '.join(missing)}"

    cards_by_id = {c.id: c for c in found_cards}

    # Render each card inline
    _ensure_dirs()
    cards_html = []
    cards_css = []
    total_duration_sec = 0
    equipment_set = set()

    for ex in exercises:
        card = cards_by_id[ex["id"]]
        ctx = _build_card_context(
            card,
            sets=ex.get("sets"),
            reps=ex.get("reps"),
            duration_sec=ex.get("duration_sec"),
        )
        # Render card without HTML/body wrappers
        tmpl = _jinja_env.get_template("exercise_card.html")
        rendered = tmpl.render(standalone=False, **ctx)

        # Split rendered into style and body parts
        style_match = re.search(r"<style>(.*?)</style>", rendered, re.DOTALL)
        if style_match:
            css_content = style_match.group(1)
            html_content = rendered[style_match.end() :]
            cards_css.append(css_content)
            cards_html.append(html_content.strip())
        else:
            cards_html.append(rendered.strip())

        # Duration estimation
        sets = ex.get("sets") or card.default_sets or 2
        dur_sec = ex.get("duration_sec") or card.default_duration_sec
        if dur_sec:
            total_duration_sec += sets * (dur_sec + 15)
        else:
            total_duration_sec += sets * 40

        if card.equipment and card.equipment != "Без инвентаря":
            equipment_set.add(card.equipment)

    total_duration_min = max(1, round(total_duration_sec / 60))
    equipment_summary = ", ".join(sorted(equipment_set)) if equipment_set else None

    # Render workout page
    workout_tmpl = _jinja_env.get_template("workout_page.html")
    workout_html = workout_tmpl.render(
        name=name,
        exercise_count=len(exercises),
        total_duration=total_duration_min,
        equipment_summary=equipment_summary,
        cards_html=cards_html,
        cards_css=cards_css,
    )

    slug = _slugify(name) or date_str
    filename = f"{date_str}-{slug}.html"
    html_path = os.path.join(_STATIC_DIR, "workouts", filename)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(workout_html)

    url = f"{settings.API_BASE_URL}/static/workouts/{filename}"

    # Push to Intervals.icu if requested
    intervals_id = None
    if push_to_intervals:
        # Build workout_doc steps from exercises
        workout_steps = []
        for i, ex in enumerate(exercises):
            card = cards_by_id[ex["id"]]
            sets = ex.get("sets") or card.default_sets or 2
            reps = ex.get("reps") or card.default_reps or 15
            dur_sec = ex.get("duration_sec") or card.default_duration_sec
            is_last = i == len(exercises) - 1

            if dur_sec:
                work_sec = dur_sec
            else:
                work_sec = max(15, reps * 3)

            sub_steps = [{"text": "Работа", "duration": work_sec}]
            if not is_last:
                sub_steps.append({"text": "Отдых", "duration": 15})

            workout_steps.append(
                {
                    "text": card.name_ru,
                    "reps": sets,
                    "steps": sub_steps,
                }
            )

        try:
            client = IntervalsClient()
            event = {
                "category": "WORKOUT",
                "start_date_local": f"{date_str}T06:00:00",
                "name": name,
                "type": sport,
                "moving_time": total_duration_sec,
                "description": f"Exercises: {len(exercises)}, ~{total_duration_min} min\n{url}",
                "external_id": f"workout-card:{date_str}:{slug}",
                "workout_doc": {
                    "steps": workout_steps,
                },
            }
            result = await client.create_event(event)
            intervals_id = result.get("id")
        except Exception as e:
            resp_body = getattr(getattr(e, "response", None), "text", "")
            logger.error("Failed to push workout to Intervals.icu: %s | body: %s", e, resp_body)
            return f"HTML generated: {url}\nError pushing to Intervals.icu: {e}\nResponse: {resp_body}"

    # Save to DB
    await WorkoutCardRow.save(
        date_str=date_str,
        name=name,
        sport=sport,
        exercises=exercises,
        total_duration_min=total_duration_min,
        equipment_summary=equipment_summary,
        intervals_id=intervals_id,
    )

    parts = [f"Workout created: {name}", f"Exercises: {len(exercises)}, ~{total_duration_min} min", f"URL: {url}"]
    if intervals_id:
        parts.append(f"Pushed to Intervals.icu (event #{intervals_id})")
    return "\n".join(parts)


@mcp.tool()
async def remove_workout_card(card_id: int) -> str:
    """Remove a composed workout (зарядка) by its ID.

    Deletes from local DB and from Intervals.icu calendar (if it was pushed there).
    Use list_workout_cards to find the card_id.

    Args:
        card_id: Workout card ID (from list_workout_cards).
    """
    row = await WorkoutCardRow.get_by_id(card_id)
    if not row:
        return f"Workout card #{card_id} not found."

    # Delete from Intervals.icu if pushed
    intervals_warning = ""
    if row.intervals_id:
        try:
            client = IntervalsClient()
            await client.delete_event(row.intervals_id)
        except Exception as e:
            logger.warning("Failed to delete event %s from Intervals.icu: %s", row.intervals_id, e)
            intervals_warning = f" (warning: failed to remove from Intervals.icu, event ID {row.intervals_id})"

    name = row.name
    await WorkoutCardRow.delete(card_id)
    return f"Removed workout card #{card_id}: {name}{intervals_warning}"


@mcp.tool()
async def list_workout_cards(days_back: int = 30) -> dict:
    """List composed workouts (зарядки) for the last N days.

    Returns workout name, date, exercise count, duration, and Intervals.icu event ID.

    Args:
        days_back: Number of days to look back (default: 30).
    """
    rows = await WorkoutCardRow.get_list(days_back=days_back)
    return {
        "count": len(rows),
        "workouts": [
            {
                "id": r.id,
                "date": r.date,
                "name": r.name,
                "sport": r.sport,
                "exercises": r.exercises,
                "total_duration_min": r.total_duration_min,
                "equipment_summary": r.equipment_summary,
                "intervals_id": r.intervals_id,
            }
            for r in rows
        ],
    }
