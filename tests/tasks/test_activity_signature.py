"""Tests for Strava signature helpers in tasks/actors/activities.py.

Covers the pure helpers used by actor_rename_activity:
- _sport_emoji, _format_distance, _format_pace
- _compose_description, _parse_signature_json
- _already_signed, _fallback_signature
"""

from types import SimpleNamespace

import pytest

from tasks.actors.activities import (
    _already_signed,
    _compose_description,
    _fallback_signature,
    _format_distance,
    _format_pace,
    _parse_signature_json,
    _sport_emoji,
)


def _activity(**kw):
    defaults = dict(type="Run", moving_time=3600, average_hr=140, icu_training_load=80.0)
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _detail(**kw):
    defaults = dict(distance=10_000.0, pace=3.3, avg_power=None, elevation_gain=50.0)
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _wellness(**kw):
    defaults = dict(recovery_score=85.0, recovery_category="good", ctl=20.0, atl=30.0)
    defaults.update(kw)
    return SimpleNamespace(**defaults)


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


class TestFormatDistance:
    def test_run_formats_kilometers(self):
        assert _format_distance("Run", 9_940) == "9.9km"

    def test_swim_formats_meters(self):
        assert _format_distance("Swim", 1_800) == "1800m"

    def test_zero_returns_none(self):
        assert _format_distance("Run", 0) is None

    def test_none_returns_none(self):
        assert _format_distance("Run", None) is None


class TestFormatPace:
    def test_run_pace_per_km(self):
        # 3.33 m/s ≈ 5:00/km
        assert _format_pace("Run", 3.333) == "5:00/km"

    def test_swim_pace_per_100m(self):
        # 0.74 m/s ≈ 2:15/100m
        assert _format_pace("Swim", 0.7407) == "2:15/100m"

    def test_zero_returns_none(self):
        assert _format_pace("Run", 0) is None

    def test_none_returns_none(self):
        assert _format_pace("Swim", None) is None


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


class TestAlreadySigned:
    def test_detects_endurai_link(self):
        assert _already_signed("🏃 Easy Run 10k", "some text\n\n→ endurai.me")

    def test_detects_legacy_readiness_marker(self):
        assert _already_signed("Run · Readiness 85/100", "")

    def test_unsigned_returns_false(self):
        assert not _already_signed("Morning Run", "Felt good")


class TestFallbackSignature:
    def test_title_has_sport_and_distance(self):
        descriptor, _ = _fallback_signature(_activity(type="Run"), _detail(distance=9_940), None)
        assert descriptor == "Run · 9.9km"

    def test_swim_uses_meters(self):
        descriptor, _ = _fallback_signature(_activity(type="Swim"), _detail(distance=1_800, pace=0.74), None)
        assert descriptor == "Swim · 1800m"

    def test_no_detail_falls_back_to_sport(self):
        descriptor, _ = _fallback_signature(_activity(type="Ride"), None, None)
        assert descriptor == "Ride"

    def test_summary_preserves_avg_hr_casing(self):
        # Regression: previously .capitalize() lowercased mid-string tokens, giving "avg hr".
        _, body = _fallback_signature(_activity(average_hr=140), _detail(), None)
        assert "avg HR 140" in body
        assert "avg hr" not in body

    def test_summary_not_lowercased(self):
        # Regression guard: build an activity where the first desc bit is a word
        # (average_hr=None, pace=None) so the summary starts with "... session"
        # — verify the leading letter wasn't lowercased by .capitalize().
        _, body = _fallback_signature(
            _activity(moving_time=3600, average_hr=None),
            _detail(pace=None),
            None,
        )
        first_line = body.splitlines()[0]
        # Must start uppercase or digit; never lowercase.
        assert not first_line[0].islower()

    def test_readiness_appended_when_wellness_present(self):
        _, body = _fallback_signature(_activity(), _detail(), _wellness(recovery_score=92))
        assert "Readiness 92/100." in body

    def test_signature_link_always_present(self):
        _, body = _fallback_signature(_activity(), _detail(), None)
        assert body.endswith("→ endurai.me")

    def test_empty_metrics_uses_generic_summary(self):
        _, body = _fallback_signature(
            _activity(moving_time=0, average_hr=None),
            _detail(distance=0, pace=0),
            None,
        )
        first_line = body.splitlines()[0]
        assert "session logged" in first_line
