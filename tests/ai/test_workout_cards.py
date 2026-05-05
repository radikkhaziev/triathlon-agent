"""Tests for Workout Cards: exercise library + workout composition."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from jinja2 import Environment, FileSystemLoader

# ---------------------------------------------------------------------------
# Helpers — resolve template directory relative to this file
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TEMPLATES_DIR = os.path.join(_PROJECT_ROOT, "templates")


def _make_jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(_TEMPLATES_DIR),
        autoescape=True,
    )


def _make_card(
    exercise_id: str = "clamshell",
    name_ru: str = "Моллюск",
    name_en: str = "Clamshell",
    muscles: str = "ягодичная",
    equipment: str = "Мини-петля",
    group_tag: str = "День А",
    default_sets: int = 3,
    default_reps: int = 15,
    default_duration_sec: int | None = None,
    steps: list | None = None,
    focus: str = "Активация ягодиц",
    breath: str = "Выдох на усилие",
    animation_html: str = '<div class="figure"></div>',
    animation_css: str = ".figure { width: 10px; }",
) -> SimpleNamespace:
    """Return a fake ExerciseCard-like object (SimpleNamespace)."""
    return SimpleNamespace(
        id=exercise_id,
        name_ru=name_ru,
        name_en=name_en,
        muscles=muscles,
        equipment=equipment,
        group_tag=group_tag,
        default_sets=default_sets,
        default_reps=default_reps,
        default_duration_sec=default_duration_sec,
        steps=steps or ["Лечь на бок", "Поднять колено"],
        focus=focus,
        breath=breath,
        animation_html=animation_html,
        animation_css=animation_css,
    )


# ---------------------------------------------------------------------------
# _validate_exercise_id
# ---------------------------------------------------------------------------


class TestValidateExerciseId:
    def _validate(self, eid: str):
        from mcp_server.tools.workout_cards import _validate_exercise_id

        return _validate_exercise_id(eid)

    def test_valid_simple(self):
        assert self._validate("clamshell") is None

    def test_valid_with_hyphen(self):
        assert self._validate("hip-thrust") is None

    def test_valid_with_underscore(self):
        assert self._validate("glute_bridge") is None

    def test_valid_alphanumeric(self):
        assert self._validate("exercise01") is None

    def test_valid_min_length(self):
        # Minimum 2 chars: start and end must be alphanumeric
        assert self._validate("ab") is None

    def test_valid_max_length(self):
        # 50 chars is exactly valid (first + 48 middle + last)
        eid = "a" + "b" * 48 + "c"
        assert len(eid) == 50
        assert self._validate(eid) is None

    def test_invalid_too_short_single_char(self):
        assert self._validate("a") is not None

    def test_invalid_empty(self):
        assert self._validate("") is not None

    def test_invalid_uppercase(self):
        assert self._validate("Clamshell") is not None

    def test_invalid_uppercase_mid(self):
        assert self._validate("clam-Shell") is not None

    def test_invalid_spaces(self):
        assert self._validate("clam shell") is not None

    def test_invalid_path_traversal_dotdot(self):
        assert self._validate("../etc/passwd") is not None

    def test_invalid_path_traversal_slash(self):
        assert self._validate("path/to/exercise") is not None

    def test_invalid_path_traversal_backslash(self):
        assert self._validate("path\\exercise") is not None

    def test_invalid_leading_hyphen(self):
        # Must start with alphanumeric
        assert self._validate("-clamshell") is not None

    def test_invalid_trailing_hyphen(self):
        # Must end with alphanumeric
        assert self._validate("clamshell-") is not None

    def test_invalid_special_chars(self):
        assert self._validate("clam@shell") is not None

    def test_invalid_too_long(self):
        # 51 chars — exceeds 50-char limit
        eid = "a" + "b" * 49 + "c"
        assert len(eid) == 51
        assert self._validate(eid) is not None

    def test_error_message_contains_id(self):
        err = self._validate("BAD_ID")
        assert "BAD_ID" in err


# ---------------------------------------------------------------------------
# _build_card_context
# ---------------------------------------------------------------------------


class TestBuildCardContext:
    def _build(self, card, **kwargs):
        from mcp_server.tools.workout_cards import _build_card_context

        return _build_card_context(card, **kwargs)

    def test_reps_based_no_overrides(self):
        card = _make_card(default_sets=3, default_reps=12, default_duration_sec=None)
        ctx = self._build(card)

        assert ctx["sets_reps"] == "3 x 12"
        assert ctx["sets_reps_label"] == "подходы x повторы"
        # 3 sets * 40s = 120s → 2 min
        assert ctx["duration"] == "~2 мин"

    def test_duration_based_no_overrides(self):
        card = _make_card(default_sets=3, default_reps=15, default_duration_sec=30)
        ctx = self._build(card)

        assert ctx["sets_reps"] == "3 x 30с"
        assert ctx["sets_reps_label"] == "подходы x время"
        # 3 * (30 + 15) = 135s → 2 min (rounded)
        assert ctx["duration"] == "~2 мин"

    def test_overrides_sets_and_reps(self):
        card = _make_card(default_sets=3, default_reps=12)
        ctx = self._build(card, sets=5, reps=20)

        assert ctx["sets_reps"] == "5 x 20"

    def test_overrides_duration_sec(self):
        card = _make_card(default_sets=2, default_duration_sec=45)
        ctx = self._build(card, sets=4, duration_sec=60)

        assert ctx["sets_reps"] == "4 x 60с"
        assert ctx["sets_reps_label"] == "подходы x время"

    def test_fallback_when_default_reps_none(self):
        """When card.default_reps is None, falls back to 15."""
        card = _make_card(default_sets=2, default_reps=None, default_duration_sec=None)
        ctx = self._build(card)

        assert "x 15" in ctx["sets_reps"]

    def test_fallback_when_default_sets_none(self):
        """When card.default_sets is None, falls back to 2."""
        card = _make_card(default_sets=None, default_reps=10, default_duration_sec=None)
        ctx = self._build(card)

        assert ctx["sets_reps"].startswith("2 x")

    def test_duration_minimum_one_minute(self):
        """Very short duration should still report at least 1 min."""
        card = _make_card(default_sets=1, default_reps=1, default_duration_sec=None)
        ctx = self._build(card)

        assert ctx["duration"] == "~1 мин"

    def test_equipment_fallback(self):
        card = _make_card(equipment=None)
        ctx = self._build(card)

        assert ctx["equipment"] == "Без инвентаря"

    def test_all_fields_present(self):
        card = _make_card()
        ctx = self._build(card)

        required_keys = [
            "exercise_id",
            "name_ru",
            "name_en",
            "muscles",
            "equipment",
            "group_tag",
            "sets_reps",
            "sets_reps_label",
            "duration",
            "breath",
            "animation_html",
            "animation_css",
            "steps",
            "focus",
        ]
        for key in required_keys:
            assert key in ctx, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    def _slugify(self, text: str) -> str:
        from mcp_server.tools.workout_cards import _slugify

        return _slugify(text)

    def test_english_text(self):
        result = self._slugify("Morning Warmup")
        assert "morning" in result or "-" in result
        # Must be lowercase ASCII
        assert result == result.lower()
        assert " " not in result

    def test_russian_text(self):
        result = self._slugify("Утренняя зарядка")
        # Cyrillic stripped — only hash remains (or partial ascii)
        # Must not raise, must be non-empty
        assert len(result) > 0
        assert " " not in result

    def test_mixed_text(self):
        result = self._slugify("День А - Day A")
        assert len(result) > 0
        assert " " not in result

    def test_empty_string_returns_hash(self):
        result = self._slugify("")
        # ascii_part is empty → returns pure 8-char md5 hex
        assert len(result) == 8
        assert result.isalnum()

    def test_different_texts_produce_different_slugs(self):
        a = self._slugify("Workout A")
        b = self._slugify("Workout B")
        assert a != b

    def test_same_text_is_deterministic(self):
        assert self._slugify("Morning") == self._slugify("Morning")

    def test_slug_contains_hash_suffix(self):
        result = self._slugify("test")
        # Format: {ascii_part}-{8-char hash}
        parts = result.rsplit("-", 1)
        assert len(parts) == 2
        assert len(parts[1]) == 8

    def test_no_consecutive_hyphens_at_end(self):
        # Special characters only — ascii_part becomes empty after stripping
        result = self._slugify("---")
        assert not result.startswith("-")


# ---------------------------------------------------------------------------
# _render_exercise_html — standalone vs inline
# ---------------------------------------------------------------------------


class TestRenderExerciseHtml:
    def _render(self, ctx: dict, standalone: bool = True) -> str:
        from mcp_server.tools.workout_cards import _render_exercise_html

        return _render_exercise_html(ctx, standalone=standalone)

    def _ctx(self, **overrides) -> dict:
        card = _make_card(**overrides) if overrides else _make_card()
        from mcp_server.tools.workout_cards import _build_card_context

        return _build_card_context(card)

    def test_standalone_produces_doctype(self):
        html = self._render(self._ctx(), standalone=True)
        assert "<!DOCTYPE html>" in html

    def test_standalone_produces_html_tag(self):
        html = self._render(self._ctx(), standalone=True)
        assert "<html" in html

    def test_standalone_produces_body_tag(self):
        html = self._render(self._ctx(), standalone=True)
        assert "<body>" in html

    def test_standalone_produces_closing_tags(self):
        html = self._render(self._ctx(), standalone=True)
        assert "</body>" in html
        assert "</html>" in html

    def test_inline_has_no_doctype(self):
        html = self._render(self._ctx(), standalone=False)
        assert "<!DOCTYPE html>" not in html

    def test_inline_has_no_html_tag(self):
        html = self._render(self._ctx(), standalone=False)
        assert "<html" not in html

    def test_inline_has_no_body_tag(self):
        html = self._render(self._ctx(), standalone=False)
        assert "<body>" not in html

    def test_inline_still_has_style_and_card_div(self):
        html = self._render(self._ctx(), standalone=False)
        assert "<style>" in html
        assert '<div class="card-clamshell">' in html


# ---------------------------------------------------------------------------
# Template rendering — exercise_card.html (via real Jinja env)
# ---------------------------------------------------------------------------


class TestExerciseCardTemplate:
    def _render(self, ctx: dict, standalone: bool = True) -> str:
        env = _make_jinja_env()
        tmpl = env.get_template("exercise_card.html")
        return tmpl.render(standalone=standalone, **ctx)

    def _default_ctx(self) -> dict:
        card = _make_card()
        from mcp_server.tools.workout_cards import _build_card_context

        return _build_card_context(card)

    def test_css_namespace_contains_exercise_id(self):
        ctx = self._default_ctx()
        html = self._render(ctx)
        assert ".card-clamshell" in html

    def test_css_namespace_is_unique_per_id(self):
        card_a = _make_card(exercise_id="hip-thrust")
        from mcp_server.tools.workout_cards import _build_card_context

        ctx_a = _build_card_context(card_a)
        html = self._render(ctx_a)
        assert ".card-hip-thrust" in html
        assert ".card-clamshell" not in html

    def test_animation_html_is_not_escaped(self):
        """animation_html must be rendered as raw HTML (| safe), not escaped."""
        ctx = self._default_ctx()
        ctx["animation_html"] = '<svg class="stick"><circle r="5"/></svg>'
        html = self._render(ctx)
        # Raw tag present, not escaped form &lt;svg
        assert '<svg class="stick">' in html
        assert "&lt;svg" not in html

    def test_animation_css_is_not_escaped(self):
        """animation_css is inserted verbatim into <style> (| safe)."""
        ctx = self._default_ctx()
        ctx["animation_css"] = "@keyframes spin { from { transform: rotate(0deg); } }"
        html = self._render(ctx)
        assert "@keyframes spin" in html
        assert "&commat;" not in html  # not HTML-entity escaped

    def test_name_ru_is_escaped(self):
        """name_ru (user-provided) must be HTML-escaped — no XSS via Jinja autoescape."""
        ctx = self._default_ctx()
        ctx["name_ru"] = "<script>alert('xss')</script>"
        html = self._render(ctx)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_equipment_badge_shown_when_not_default(self):
        card = _make_card(equipment="Мини-петля")
        from mcp_server.tools.workout_cards import _build_card_context

        ctx = _build_card_context(card)
        html = self._render(ctx)
        assert "Мини-петля" in html

    def test_equipment_badge_hidden_when_no_inventory(self):
        card = _make_card(equipment="Без инвентаря")
        from mcp_server.tools.workout_cards import _build_card_context

        ctx = _build_card_context(card)
        html = self._render(ctx)
        # Badge should not appear for default equipment
        assert 'class="badge equip"' not in html

    def test_breath_shown_when_present(self):
        ctx = self._default_ctx()
        ctx["breath"] = "Выдох на усилие"
        html = self._render(ctx)
        assert "Выдох на усилие" in html

    def test_breath_hidden_when_empty(self):
        """When breath is empty the breath indicator div is not rendered.
        Note: the .breath-dot CSS rule is always emitted in the <style> block;
        only the HTML <div class="breath"> is conditionally included."""
        ctx = self._default_ctx()
        ctx["breath"] = ""
        html = self._render(ctx)
        assert '<div class="breath">' not in html

    def test_steps_rendered(self):
        ctx = self._default_ctx()
        html = self._render(ctx)
        for step in ctx["steps"]:
            assert step in html

    def test_breathe_keyframe_uses_exercise_id(self):
        ctx = self._default_ctx()
        html = self._render(ctx)
        assert "@keyframes breathe-clamshell" in html


# ---------------------------------------------------------------------------
# compose_workout — validation (mocked DB)
# ---------------------------------------------------------------------------


class TestComposeWorkoutValidation:
    @pytest.fixture(autouse=True)
    def _set_user_context(self):
        # Restore the previous contextvar value on teardown. ``mcp_user_id``
        # leaks across tests in the same asyncio task — without resetting
        # we'd inherit ``user_id=1`` into every subsequent test that reads
        # ``get_current_user_id()`` (race tests, github tool tests, etc.).
        from mcp_server.context import _current_user_id

        token = _current_user_id.set(1)
        yield
        _current_user_id.reset(token)

    async def test_missing_id_field_returns_error(self):
        """Entry without 'id' key should return an error immediately."""
        from mcp_server.tools.workout_cards import compose_workout

        result = await compose_workout(
            name="Test",
            exercises=[{"sets": 3, "reps": 15}],  # no "id"
        )
        assert "must be a dict with at least an 'id' field" in result

    async def test_non_dict_entry_returns_error(self):
        """Non-dict entry (e.g. a string) should return an error."""
        from mcp_server.tools.workout_cards import compose_workout

        result = await compose_workout(
            name="Test",
            exercises=["clamshell"],  # string, not dict
        )
        assert "must be a dict" in result

    async def test_unknown_exercise_id_returns_error(self):
        """Exercise IDs not in DB should be reported as missing."""
        from mcp_server.tools.workout_cards import compose_workout

        with patch("mcp_server.tools.workout_cards.ExerciseCard.get_by_ids", new=AsyncMock(return_value=[])):
            result = await compose_workout(
                name="Test",
                exercises=[{"id": "nonexistent-exercise"}],
            )
        assert "not found in library" in result
        assert "nonexistent-exercise" in result

    async def test_partial_missing_ids_reported(self):
        """Only the missing IDs (not all) are reported."""
        from mcp_server.tools.workout_cards import compose_workout

        found_card = _make_card(exercise_id="clamshell")
        with patch(
            "mcp_server.tools.workout_cards.ExerciseCard.get_by_ids",
            new=AsyncMock(return_value=[found_card]),
        ):
            result = await compose_workout(
                name="Test",
                exercises=[
                    {"id": "clamshell"},
                    {"id": "missing-exercise"},
                ],
            )
        assert "missing-exercise" in result
        assert "clamshell" not in result  # found card NOT in error

    async def test_empty_exercises_list_resolves(self, tmp_path):
        """Empty exercise list should not crash on validation and should produce a URL."""
        import mcp_server.tools.workout_cards as wc_module
        from mcp_server.tools.workout_cards import compose_workout

        # Redirect static dir to tmp_path so real file I/O works without touching source tree
        original_static = wc_module._STATIC_DIR
        wc_module._STATIC_DIR = str(tmp_path)
        try:
            with (
                patch("mcp_server.tools.workout_cards.ExerciseCard.get_by_ids", new=AsyncMock(return_value=[])),
                patch("mcp_server.tools.workout_cards.WorkoutCard.save", new=AsyncMock()),
                patch("mcp_server.tools.workout_cards.settings") as mock_settings,
            ):
                mock_settings.API_BASE_URL = "https://example.com"
                result = await compose_workout(name="Empty Workout", exercises=[])
        finally:
            wc_module._STATIC_DIR = original_static

        # No missing IDs with empty list — should proceed to generation
        assert "not found in library" not in result
        assert "Empty Workout" in result


# ---------------------------------------------------------------------------
# compose_workout — push-to-Intervals description routing
#
# Regression guard for the bug where the HTML link in the pushed event was
# ONLY visible in Garmin (which reads ``workout_doc.description``) and NOT
# in the Intervals.icu web UI (which reads top-level ``event.description``).
# Fix: mirror prefix into top-level ``description`` for non-Swim sports.
# Swim still goes only into ``workout_doc.description`` because Intervals
# silently drops ``workout_doc.steps`` for Swim when top-level description
# is set (see ``PlannedWorkoutDTO.to_intervals_event`` docstring).
# ---------------------------------------------------------------------------


class TestComposeWorkoutPushDescription:
    @pytest.fixture(autouse=True)
    def _set_user_context(self):
        # Same teardown discipline as ``TestComposeWorkoutValidation`` —
        # see that fixture's comment for the contextvar-leak rationale.
        from mcp_server.context import _current_user_id

        token = _current_user_id.set(1)
        yield
        _current_user_id.reset(token)

    async def _push_and_capture_event(self, tmp_path, *, sport: str):
        """Drive ``compose_workout`` through the push branch and return the
        ``EventExDTO`` handed to ``client.create_event``."""
        import mcp_server.tools.workout_cards as wc_module
        from mcp_server.tools.workout_cards import compose_workout

        captured: dict = {}

        class _FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def create_event(self, event):
                captured["event"] = event
                return SimpleNamespace(id="i_test_event_id")

        card = _make_card(
            exercise_id="dead_bug",
            name_ru="Dead Bug",
            default_sets=2,
            default_reps=10,
            default_duration_sec=30,
        )
        # workout_cards.py reads card.distance_m on the push path — the
        # _make_card helper predates that field and doesn't set it.
        card.distance_m = None

        original_static = wc_module._STATIC_DIR
        wc_module._STATIC_DIR = str(tmp_path)
        try:
            with (
                patch(
                    "mcp_server.tools.workout_cards.ExerciseCard.get_by_ids",
                    new=AsyncMock(return_value=[card]),
                ),
                patch("mcp_server.tools.workout_cards.AiWorkout.save", new=AsyncMock()),
                patch("mcp_server.tools.workout_cards.WorkoutCard.save", new=AsyncMock()),
                patch(
                    "mcp_server.tools.workout_cards.IntervalsAsyncClient.for_user",
                    return_value=_FakeClient(),
                ),
                patch("mcp_server.tools.workout_cards.settings") as mock_settings,
            ):
                mock_settings.API_BASE_URL = "https://bot.example.com"
                await compose_workout(
                    name="Утренняя зарядка",
                    exercises=[{"id": "dead_bug"}],
                    target_date="2026-05-05",
                    push_to_intervals=True,
                    sport=sport,
                )
        finally:
            wc_module._STATIC_DIR = original_static

        assert "event" in captured, "create_event was never called"
        return captured["event"]

    async def test_non_swim_mirrors_url_into_top_level_description(self, tmp_path):
        event = await self._push_and_capture_event(tmp_path, sport="Other")
        # Top-level description carries the URL — visible in Intervals.icu web UI.
        assert event.description is not None
        assert "https://bot.example.com/static/workouts/" in event.description
        # And workout_doc.description still has it for Garmin.
        assert "https://bot.example.com/static/workouts/" in event.workout_doc.get("description", "")

    def test_swim_excluded_from_top_level_desc_allow_list(self):
        """End-to-end push for Swim is hard to drive (compose_workout's card
        builder emits target-less steps which fail PlannedWorkoutDTO
        validation, and pydantic's @model_validator can't be cleanly
        monkeypatched per-test). Pin the allow-list constant directly —
        adding "Swim" (or any future swim-like sport) to it would re-trigger
        the Intervals workout_doc.steps regression on Swim events.

        We assert the explicit set, NOT a derivation from ``VALID_SPORTS`` —
        the whole point of switching from a deny-list to an allow-list is
        that a new sport added to ``VALID_SPORTS`` must NOT auto-include
        here. If you add a new sport that genuinely needs top-level
        description, update this test together with the source constant.
        """
        from mcp_server.tools.workout_cards import _TOP_LEVEL_DESC_SPORTS

        assert _TOP_LEVEL_DESC_SPORTS == frozenset({"Run", "Ride", "Other"})
        assert "Swim" not in _TOP_LEVEL_DESC_SPORTS


# ---------------------------------------------------------------------------
# create_exercise_card — ID validation gates DB call
# ---------------------------------------------------------------------------


class TestCreateExerciseCardValidation:
    @pytest.fixture(autouse=True)
    def _set_user_context(self):
        # ``create_exercise_card`` reads ``get_current_user_id()`` early to
        # gate ownership. Was previously relying on contextvar leakage from
        # ``TestComposeWorkoutValidation`` running first; now that teardown
        # is in place each class needs its own setup.
        from mcp_server.context import _current_user_id

        token = _current_user_id.set(1)
        yield
        _current_user_id.reset(token)

    async def test_path_traversal_blocked(self):
        from mcp_server.tools.workout_cards import create_exercise_card

        with patch("mcp_server.tools.workout_cards.ExerciseCard.save", new=AsyncMock()) as mock_save:
            result = await create_exercise_card(
                exercise_id="../etc/passwd",
                name_ru="Evil",
                name_en="Evil",
                muscles="core",
                equipment="none",
                group_tag="A",
                default_sets=3,
                default_reps=15,
                steps=[],
                focus="bad",
                animation_html="",
                animation_css="",
            )
        # DB should never be called
        mock_save.assert_not_called()
        assert "Invalid exercise_id" in result

    async def test_uppercase_blocked(self):
        from mcp_server.tools.workout_cards import create_exercise_card

        with patch("mcp_server.tools.workout_cards.ExerciseCard.save", new=AsyncMock()) as mock_save:
            result = await create_exercise_card(
                exercise_id="ClamShell",
                name_ru="Моллюск",
                name_en="Clamshell",
                muscles="ягодичная",
                equipment="Мини-петля",
                group_tag="День А",
                default_sets=3,
                default_reps=15,
                steps=[],
                focus="test",
                animation_html="",
                animation_css="",
            )
        mock_save.assert_not_called()
        assert "Invalid exercise_id" in result


# ---------------------------------------------------------------------------
# update_exercise_card — ID validation gates DB call
# ---------------------------------------------------------------------------


class TestUpdateExerciseCardValidation:
    @pytest.fixture(autouse=True)
    def _set_user_context(self):
        # See ``TestCreateExerciseCardValidation._set_user_context`` for the
        # contextvar-leak rationale.
        from mcp_server.context import _current_user_id

        token = _current_user_id.set(1)
        yield
        _current_user_id.reset(token)

    async def test_path_traversal_blocked(self):
        from mcp_server.tools.workout_cards import update_exercise_card

        with patch("mcp_server.tools.workout_cards.ExerciseCard.get", new=AsyncMock()) as mock_get:
            result = await update_exercise_card(exercise_id="../../bad")

        mock_get.assert_not_called()
        assert "Invalid exercise_id" in result

    async def test_valid_id_not_found_returns_message(self):
        from mcp_server.tools.workout_cards import update_exercise_card

        with patch("mcp_server.tools.workout_cards.ExerciseCard.get", new=AsyncMock(return_value=None)):
            result = await update_exercise_card(exercise_id="clamshell", name_ru="Updated")

        assert "not found" in result

    async def test_no_fields_returns_message(self):
        """Providing no fields to update should report that."""
        from mcp_server.tools.workout_cards import update_exercise_card

        with patch("mcp_server.tools.workout_cards.ExerciseCard.get", new=AsyncMock(return_value=_make_card())):
            result = await update_exercise_card(exercise_id="clamshell")

        assert "No fields to update" in result
