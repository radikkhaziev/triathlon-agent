"""Renderer tests for ``bot.raceplan_render`` (END-64).

These are independent of the Telegram handler — they exercise the Markdown +
PNG card output for a representative ``generate_race_plan`` payload, and a
few partial-payload edge cases the handler might pass through (missing legs,
missing fueling, no headline).
"""

from __future__ import annotations

from bot.raceplan_render import render_plan_markdown, render_race_plan_card


def _full_payload() -> dict:
    return {
        "id": 42,
        "dry_run": False,
        "preliminary": True,
        "model_version": "v0",
        "payload": {
            "preliminary": True,
            "race": {"id": 7, "name": "Drina Trail", "date": "2026-06-15", "days_to_race": 46, "discipline": "Run"},
            "plan": {
                "headline": "Patient first half, finish strong.",
                "warmup": "20 min easy + 4 strides 20 sec.",
                "legs": [
                    {
                        "leg": "first 10k",
                        "distance": "10 km",
                        "pacing": {"low": "5:30/km", "target": "5:15/km", "cap": "5:00/km"},
                        "hr_ceiling_bpm": 162,
                        "notes": "Sit. Don't chase the start.",
                    },
                    {
                        "leg": "10-21k",
                        "distance": "11 km",
                        "pacing": {"low": "5:25/km", "target": "5:10/km", "cap": "4:55/km"},
                        "hr_ceiling_bpm": 168,
                    },
                ],
                "fueling": {
                    "carbs_g_per_hour": 70,
                    "fluid_ml_per_hour": 500,
                    "sodium_mg_per_hour": 600,
                    "notes": "Gel every 25 min.",
                },
                "transitions": [],
                "contingencies": [
                    {"scenario": "heat", "plan": "Slow target 10s/km, double fluid."},
                    {"scenario": "cramp", "plan": "Walk 2 min, salt cap."},
                    {"scenario": "off-pace", "plan": "Hold cap not target."},
                ],
            },
        },
    }


class TestMarkdown:
    def test_full_payload_includes_all_sections(self):
        md = render_plan_markdown(_full_payload())
        assert "Drina Trail" in md
        assert "Warmup" in md
        assert "Legs" in md
        assert "Fueling" in md
        assert "Contingencies" in md
        # Pacing corridor renders low → target → cap
        assert "5:30/km" in md and "5:15/km" in md and "5:00/km" in md
        # HR ceiling rendered
        assert "162 bpm" in md
        # Preliminary warning rendered
        assert "preliminary" in md.lower()

    def test_partial_payload_drops_missing_sections(self):
        partial = {
            "payload": {
                "race": {"name": "X", "date": "2026-05-01", "days_to_race": 1},
                "plan": {"warmup": "5 min", "legs": [], "contingencies": []},
            }
        }
        md = render_plan_markdown(partial)
        assert "X" in md
        assert "Warmup" in md
        # Empty sections should be silently dropped
        assert "Fueling" not in md
        assert "Contingencies" not in md

    def test_escape_protects_underscores_in_race_name(self):
        # User-controlled race name with markdown metachars must be escaped so
        # the Markdown parser doesn't treat _foo_ as italics.
        payload = _full_payload()
        payload["payload"]["race"]["name"] = "Race_Underscore_Name"
        md = render_plan_markdown(payload)
        # Backslash-escaped underscores in the rendered name
        assert r"Race\_Underscore\_Name" in md

    def test_handles_missing_payload_gracefully(self):
        md = render_plan_markdown({})
        # Should not raise; returns at least the header (with empty name)
        assert isinstance(md, str)


class TestCard:
    def test_renders_png_bytes(self):
        png = render_race_plan_card(_full_payload())
        assert isinstance(png, bytes)
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        # Sanity: a non-trivially-sized image
        assert len(png) > 5_000

    def test_card_with_minimal_payload_does_not_raise(self):
        minimal = {
            "payload": {
                "race": {"name": "X"},
                "plan": {"legs": []},
            }
        }
        png = render_race_plan_card(minimal)
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
