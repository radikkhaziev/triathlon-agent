"""Tests for Strava signature helpers in tasks/actors/activities.py.

Covers the pure helpers used by actor_rename_activity:
- _sport_emoji
- _compose_description, _parse_signature_json
- _already_signed
- _fallback_signature (Russian, no Strava duplicates)
- _render_comparison_markers, _generate_signature_prompt
"""

from types import SimpleNamespace

import pytest

from tasks.actors.activities import (
    _already_signed,
    _compose_description,
    _fallback_signature,
    _generate_signature_prompt,
    _parse_signature_json,
    _render_comparison_markers,
    _render_form_context,
    _sport_emoji,
)


def _activity(**kw):
    defaults = dict(type="Run", moving_time=3600, average_hr=140, icu_training_load=80.0)
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _wellness(**kw):
    defaults = dict(recovery_score=85.0, recovery_category="good", ctl=20.0, atl=30.0)
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _comparison(markers, **kw):
    defaults = dict(available=True, pool_n=7, window_days=120, markers=markers)
    defaults.update(kw)
    return defaults


class TestSportEmoji:
    def test_known_sports(self):
        assert _sport_emoji("Run") == "🏃"
        assert _sport_emoji("Swim") == "🏊"
        assert _sport_emoji("Ride") == "🚴"
        assert _sport_emoji("VirtualRide") == "🚴"
        assert _sport_emoji("MountainBikeRide") == "🚵"
        assert _sport_emoji("Walk") == "🚶"

    def test_unknown_sport_falls_back(self):
        assert _sport_emoji("Kitesurfing") == "💪"

    def test_none_falls_back(self):
        assert _sport_emoji(None) == "💪"


class TestComposeDescription:
    def test_appends_signature_link(self):
        out = _compose_description(["Line A.", "Line B."])
        assert out.endswith("→ endurai.me")
        assert "Line A." in out
        assert "Line B." in out

    def test_inserts_blank_line_before_link(self):
        out = _compose_description(["body"])
        assert out == "body\n\n→ endurai.me"

    def test_empty_strings_skipped(self):
        out = _compose_description(["", "x", ""])
        assert out == "x\n\n→ endurai.me"


class TestParseSignatureJson:
    def test_plain_json(self):
        out = _parse_signature_json('{"title": "T", "description": "D"}')
        assert out == {"title": "T", "description": "D"}

    def test_json_fenced(self):
        out = _parse_signature_json('```json\n{"title": "T", "description": "D"}\n```')
        assert out == {"title": "T", "description": "D"}

    def test_plain_fence_no_language(self):
        out = _parse_signature_json('```\n{"title": "T", "description": "D"}\n```')
        assert out == {"title": "T", "description": "D"}

    def test_surrounding_prose(self):
        out = _parse_signature_json('Sure, here you go:\n{"title": "T", "description": "D"}\nHope that helps.')
        assert out == {"title": "T", "description": "D"}

    def test_no_json_raises(self):
        with pytest.raises(ValueError):
            _parse_signature_json("no json here")

    def test_trailing_text_with_braces_ignored(self):
        # raw_decode stops at the end of the first complete JSON object,
        # so trailing `{note}` must not confuse the parser.
        out = _parse_signature_json('{"title": "T", "description": "D"} trailing {note}')
        assert out == {"title": "T", "description": "D"}

    def test_non_object_json_raises(self):
        with pytest.raises(ValueError):
            _parse_signature_json('["not", "an", "object"]')


class TestAlreadySigned:
    def test_detects_endurai_link(self):
        assert _already_signed("🏃 Easy Run 10k", "some text\n\n→ endurai.me")

    def test_detects_legacy_readiness_marker(self):
        assert _already_signed("Run · Readiness 85/100", "")

    def test_unsigned_returns_false(self):
        assert not _already_signed("Morning Run", "Felt good")


class TestFallbackSignature:
    def test_title_has_russian_sport_and_tss(self):
        descriptor, _ = _fallback_signature(_activity(type="Run", icu_training_load=80), None)
        assert descriptor == "Бег · 80 TSS"

    def test_swim_russian(self):
        descriptor, _ = _fallback_signature(_activity(type="Swim", icu_training_load=19), None)
        assert descriptor == "Плавание · 19 TSS"

    def test_no_tss_falls_back_to_sport(self):
        descriptor, _ = _fallback_signature(_activity(type="Ride", icu_training_load=None), None)
        assert descriptor == "Вело"

    def test_no_strava_duplicates_in_body(self):
        # Distance/time/pace are Strava's job — must not leak into the description.
        # Exercise the rich branch (TSS + CTL + recovery), not just the generic one.
        _, body = _fallback_signature(_activity(icu_training_load=80), _wellness(ctl=42, recovery_score=92))
        for token in ("km", "/km", "min session", "avg HR", "10.0"):
            assert token not in body

    def test_tss_summary(self):
        _, body = _fallback_signature(_activity(icu_training_load=80), None)
        assert "80 TSS в копилку." in body

    def test_ctl_and_recovery_lines(self):
        _, body = _fallback_signature(_activity(), _wellness(ctl=42, recovery_score=92))
        assert "Форма (CTL) 42." in body
        assert "Восстановление 92/100." in body

    def test_signature_link_always_present(self):
        _, body = _fallback_signature(_activity(), None)
        assert body.endswith("→ endurai.me")

    def test_empty_metrics_uses_generic_summary(self):
        _, body = _fallback_signature(_activity(type="Ride", icu_training_load=None), None)
        first_line = body.splitlines()[0]
        assert "сессия записана" in first_line


class TestRenderFormContext:
    def test_tss_ctl_recovery_tsb_lines(self):
        lines = _render_form_context(_activity(icu_training_load=80), _wellness(ctl=42, atl=50, recovery_score=92))
        joined = "\n".join(lines)
        assert "TSS: 80" in joined
        assert "CTL (форма): 42" in joined
        assert "Recovery: 92/100" in joined
        assert "TSB: -8" in joined

    def test_zero_ctl_still_emits_tsb(self):
        # Regression: truthiness check dropped TSB when ctl/atl == 0 (new athletes).
        lines = _render_form_context(_activity(), _wellness(ctl=0, atl=0, recovery_score=None))
        joined = "\n".join(lines)
        assert "CTL (форма): 0" in joined
        assert "TSB: +0" in joined

    def test_no_wellness_only_tss(self):
        lines = _render_form_context(_activity(icu_training_load=55), None)
        assert lines == ["TSS: 55"]


class TestRenderComparisonMarkers:
    def test_unavailable_returns_empty(self):
        assert _render_comparison_markers({"available": False}) == []
        assert _render_comparison_markers({}) == []

    def test_available_no_markers_returns_empty(self):
        assert _render_comparison_markers(_comparison([])) == []

    def test_header_has_pool_and_window(self):
        out = _render_comparison_markers(
            _comparison([{"key": "ef", "value": 1.23, "norm_median": 1.20, "band": "better"}])
        )
        assert "n=7" in out[0]
        assert "120" in out[0]

    def test_marker_keys_render_with_verdict(self):
        markers = [
            {"key": "decoupling", "value": 3.8, "norm_median": 5.2, "band": "better"},
            {"key": "ef", "value": 1.23, "norm_median": 1.20, "band": "better"},
            {"key": "np", "value": 169, "norm_median": 178, "band": "worse"},
            {"key": "avg_hr", "value": 137, "norm_median": 148, "band": "neutral"},
            {"key": "vi", "value": 1.12, "norm_median": 1.07, "band": "neutral"},
        ]
        out = "\n".join(_render_comparison_markers(_comparison(markers)))
        assert "Decoupling" in out and "3.8%" in out
        assert "EF" in out and "лучше нормы" in out
        assert "Норм. мощность" in out and "хуже нормы" in out
        assert "Средний пульс" in out and "на уровне нормы" in out
        assert "VI" in out

    def test_pace_marker_formats_min_per_km(self):
        out = "\n".join(
            _render_comparison_markers(
                _comparison([{"key": "pace", "value": 312, "norm_median": 330, "band": "better"}])
            )
        )
        assert "5:12/км" in out
        assert "5:30/км" in out


class TestGenerateSignaturePrompt:
    def test_russian_third_person_and_no_strava_metrics(self):
        prompt = _generate_signature_prompt(_activity(), _wellness())
        assert "третьем лице" in prompt
        assert "НЕ упоминай дистанцию, время, темп" in prompt
        # The instruction itself names these words, but no formatted distance/pace value leaks.
        assert "10.0km" not in prompt

    def test_includes_comparison_when_available(self):
        cmp = _comparison([{"key": "ef", "value": 1.23, "norm_median": 1.20, "band": "better"}])
        prompt = _generate_signature_prompt(_activity(), _wellness(), cmp)
        assert "Сравнение с похожими сессиями" in prompt
        assert "выбери 2-3" in prompt

    def test_fallback_instruction_when_no_comparison(self):
        prompt = _generate_signature_prompt(_activity(), _wellness(), None)
        assert "Сравнения с нормой нет" in prompt
