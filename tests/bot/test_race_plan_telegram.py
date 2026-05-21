"""Tests for the race-plan Telegram renderer (PR2.6).

Pure-function tests — no DB, no Telegram. Verifies the Markdown layout, the
section ordering, the inline keyboard shape, and that empty payload sections
don't leak empty headers."""

from bot.race_plan_telegram import build_open_in_webapp_keyboard, render_race_plan_for_telegram


def _full_payload() -> dict:
    """Realistic payload covering all sections."""
    return {
        "plan": {
            "headline": "Steady to km 16, then race.",
            "warmup": "10 min easy + 4×30s strides.",
            "legs": [
                {
                    "leg": "swim",
                    "distance": "1.9 km",
                    "pacing": {"low": "2:00/100m", "target": "1:55/100m", "cap": "1:50/100m"},
                    "hr_ceiling_bpm": 155,
                    "notes": "Hold form first 500m.",
                },
                {
                    "leg": "bike",
                    "distance": "90 km",
                    "pacing": {"low": "180W", "target": "210W", "cap": "240W"},
                    "hr_ceiling_bpm": 165,
                },
                {
                    "leg": "run",
                    "distance": "21.1 km",
                    "pacing": {"low": "5:30/km", "target": "5:10/km", "cap": "4:50/km"},
                },
            ],
            "fueling": {
                "carbs_g_per_hour": 75,
                "fluid_ml_per_hour": 600,
                "sodium_mg_per_hour": 500,
                "notes": "Gel every 25 min.",
            },
            "transitions": [
                {"name": "T1", "checklist": ["helmet on", "shoes"], "target_time_sec": 75},
            ],
            "contingencies": [
                {"scenario": "heat", "plan": "Slow target 5%."},
                {"scenario": "cramp", "plan": "Walk + salt."},
                {"scenario": "off-pace", "plan": "Drop to low pacing."},
            ],
        },
        "race": {"id": 1, "name": "Ironman 70.3 Drina"},
        "confidence_tier": "final",
    }


class TestRenderRacePlanForTelegram:
    def test_full_payload_includes_all_sections(self):
        out = render_race_plan_for_telegram(_full_payload(), event_name="Ironman 70.3 Drina")

        # Header line
        assert "Ironman 70.3 Drina" in out
        # Headline plain text — italic was dropped per H1 (markdown_to_telegram_html
        # doesn't recognise single-`_` so it would render as a literal underscore)
        assert "Steady to km 16, then race." in out
        # Sections (translated via _() — under default ru locale, but our
        # tests run with default English fallback when no .mo loaded)
        assert "Warmup" in out or "Разминка" in out
        assert "Legs" in out or "Этапы" in out
        assert "Fueling" in out or "Питание" in out
        assert "Transitions" in out or "Транзиты" in out
        assert "Contingencies" in out or "План Б" in out
        # Per-leg formatting: target bolded inside arrows. CommonMark
        # double-asterisk so the downstream HTML converter (`markdown_to_telegram_html`
        # in tasks/tools.py) actually emits `<b>...</b>` and not literal `*`.
        assert "1:55/100m" in out
        assert "**1:55/100m**" in out
        # HR ceiling rendered when present
        assert "155 bpm" in out
        assert "165 bpm" in out
        # Run leg has no HR ceiling → no spurious bpm in its line
        # (we just verify the run pacing made it through)
        assert "5:10/km" in out
        # Fueling line
        assert "75" in out  # carbs
        assert "600" in out  # fluid
        # Transitions
        assert "T1" in out
        # Contingencies
        assert "Slow target 5%" in out

    def test_omits_empty_sections(self):
        """Plan with only required fields → no empty section headers leaked."""
        minimal = {
            "plan": {
                "warmup": "Easy 10 min.",
                "legs": [
                    {
                        "leg": "run",
                        "distance": "10 km",
                        "pacing": {"low": "5:00/km", "target": "4:40/km", "cap": "4:20/km"},
                    },
                ],
                "fueling": {"carbs_g_per_hour": 60},
                "contingencies": [
                    {"scenario": "heat", "plan": "Slow 5%."},
                    {"scenario": "cramp", "plan": "Walk."},
                    {"scenario": "off-pace", "plan": "Hold low."},
                ],
            },
            "race": {"id": 1, "name": "Park 10K"},
            "confidence_tier": "mid",
        }
        out = render_race_plan_for_telegram(minimal, event_name="Park 10K")
        # No transitions section
        assert "Transitions" not in out and "Транзиты" not in out
        # No headline italic line
        assert "_" not in out.split("\n")[1] if len(out.split("\n")) > 1 else True
        # Telegram message length budget — well under 4096
        assert len(out) < 2500

    def test_telegram_length_under_budget(self):
        """Even a maximally-loaded plan stays under 2.5k chars (well below 4096)."""
        payload = _full_payload()
        # Bloat notes and contingencies to exercise the upper bound.
        for leg in payload["plan"]["legs"]:
            leg["notes"] = "X" * 200  # max per-leg note (PR1 schema cap)
        payload["plan"]["contingencies"] = [
            {"scenario": f"scenario-{i}", "plan": "Y" * 300} for i in range(5)  # max contingencies (PR2 schema cap)
        ]
        out = render_race_plan_for_telegram(payload, event_name="Some Race")
        assert len(out) < 4096, f"message length {len(out)} exceeds Telegram 4096-char limit"

    def test_renders_cleanly_through_telegram_html_pipeline(self):
        """L2: actor calls ``tg.send_message(markdown=True)`` which routes through
        ``markdown_to_telegram_html``. That converter ONLY handles ``**bold**`` /
        ``__bold__``. Single-asterisk / single-underscore would pass through as
        literal characters on the athlete's screen — exactly the H1 regression
        this test pins. Verify the converted HTML has NO leftover unbalanced
        asterisks or underscores in a position where they'd render literally."""
        from tasks.tools import markdown_to_telegram_html

        out = render_race_plan_for_telegram(_full_payload(), event_name="Ironman 70.3 Drina")
        html = markdown_to_telegram_html(out)

        # No raw ``**`` left after conversion (the converter should have eaten
        # all bold markers and emitted ``<b>...</b>``).
        assert "**" not in html, f"Unconverted bold marker in HTML output:\n{html}"
        # No literal single ``*`` lingering as content (would render as literal).
        # Allow ``*`` only inside HTML tags (none expected here).
        assert "*" not in html, f"Stray asterisk leaked through pipeline:\n{html}"
        # Bold tags actually present
        assert "<b>" in html and "</b>" in html
        # Headline survives as text (no leading/trailing markers)
        assert "Steady to km 16, then race." in html


class TestOpenInWebappKeyboard:
    def test_inline_keyboard_shape(self):
        kb = build_open_in_webapp_keyboard("https://bot.endurai.me")
        assert "inline_keyboard" in kb
        rows = kb["inline_keyboard"]
        assert len(rows) == 1 and len(rows[0]) == 1
        button = rows[0][0]
        # Mini-App button (web_app), not URL — keeps athlete inside Telegram client
        assert "web_app" in button
        assert button["web_app"]["url"] == "https://bot.endurai.me/trends"
        assert "text" in button

    def test_handles_trailing_slash_in_base_url(self):
        kb = build_open_in_webapp_keyboard("https://bot.endurai.me/")
        assert kb["inline_keyboard"][0][0]["web_app"]["url"] == "https://bot.endurai.me/trends"
