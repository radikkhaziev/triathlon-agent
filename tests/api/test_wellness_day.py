"""Contract tests for GET /api/wellness-day (Halo redesign BE-2 / BE-2b).

The Halo design fixture (`design-package/.../sample-data.json`) once carried
`hrv.status: "balanced"` and `banister_recovery: 0.68`. Both are wrong vs the
real serializer:

  * BE-2  — `hrv.status` / `rhr.status` are the HRV/RHR analysis verdict
            (`green | yellow | red | insufficient_data`, see
            `data/db/hrv.py` + `data/metrics.py`). `"balanced"` is the
            *recovery readiness_level*, a SEPARATE field. The serializer must
            never leak readiness vocabulary into `*.status`.
  * BE-2b — `stress.banister_recovery` is a 0–100 percentage
            (`calculate_banister_recovery` clamps `max(0, min(100, r))`;
            `combined_recovery_score` clamps `min(100, …)`), not a 0–1
            fraction.

These tests lock that contract so a future serializer change can't silently
reintroduce the fixture's mistakes.
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.deps import require_viewer
from api.routers.wellness import router as wellness_router
from data.db import HrvAnalysis, RhrAnalysis, Wellness, get_session

# A clearly-past date so the endpoint's `target > today → today` clamp never
# fires regardless of the real wall clock.
_SEED_DATE = "2026-01-15"

_STATUS_ENUM = {"green", "yellow", "red", "insufficient_data"}


@pytest.fixture
def client():
    test_app = FastAPI()
    test_app.include_router(wellness_router)

    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.role = "owner"
    mock_user.is_active = True
    mock_user.language = "ru"
    test_app.dependency_overrides[require_viewer] = lambda: mock_user
    return AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")


async def _seed_wellness(
    *,
    readiness_level: str | None = None,
    recovery_category: str | None = "moderate",
    banister_recovery: float | None = None,
) -> None:
    """Insert a wellness row directly (mirrors test_dashboard._seed_wellness —
    `recovery_*` is computed locally, not via the Intervals DTO)."""
    async with get_session() as session:
        session.add(
            Wellness(
                user_id=1,
                date=_SEED_DATE,
                ctl=60.0,
                atl=50.0,
                recovery_score=72.0,
                recovery_category=recovery_category,
                readiness_score=62,
                readiness_level=readiness_level,
                banister_recovery=banister_recovery,
                hrv=52.0,
                updated=datetime.now(timezone.utc),
            )
        )
        await session.commit()


async def _seed_analysis(
    *,
    hrv_status: str,
    rhr_status: str,
    rmssd_60d: float | None = None,
    rhr_today: float | None = None,
    rhr_30d: float | None = None,
) -> None:
    async with get_session() as session:
        session.add(
            HrvAnalysis(
                user_id=1,
                date=_SEED_DATE,
                algorithm="flatt_esco",
                status=hrv_status,
                days_available=42,
                rmssd_60d=rmssd_60d,
            )
        )
        session.add(
            RhrAnalysis(
                user_id=1,
                date=_SEED_DATE,
                status=rhr_status,
                days_available=45,
                rhr_today=rhr_today,
                rhr_30d=rhr_30d,
            )
        )
        await session.commit()


async def _seed_streak_history(
    *,
    days: int,
    hrv_above: bool,
    rhr_below: bool,
    rmssd_60d: float = 45.0,
    rhr_30d: float = 60.0,
) -> None:
    """Seed `days` consecutive prior days ending the day BEFORE `_SEED_DATE`.

    Each day gets a wellness row (with `hrv` either above or at-or-below the
    baseline) plus matching HrvAnalysis/RhrAnalysis rows. Used to set up
    streaks longer than 1 — the streak helpers walk from `target_date`
    backwards, so today's row (`_SEED_DATE`) is seeded by `_seed_wellness` +
    `_seed_analysis` separately.
    """
    end = date.fromisoformat(_SEED_DATE) - timedelta(days=1)
    async with get_session() as session:
        for i in range(days):
            d = (end - timedelta(days=i)).isoformat()
            session.add(
                Wellness(
                    user_id=1,
                    date=d,
                    ctl=60.0,
                    atl=50.0,
                    hrv=(rmssd_60d + 5.0) if hrv_above else (rmssd_60d - 5.0),
                    updated=datetime.now(timezone.utc),
                )
            )
            session.add(
                HrvAnalysis(
                    user_id=1,
                    date=d,
                    algorithm="flatt_esco",
                    status="green" if hrv_above else "yellow",
                    days_available=42,
                    rmssd_60d=rmssd_60d,
                )
            )
            session.add(
                RhrAnalysis(
                    user_id=1,
                    date=d,
                    status="green" if rhr_below else "yellow",
                    days_available=45,
                    rhr_today=(rhr_30d - 3.0) if rhr_below else (rhr_30d + 3.0),
                    rhr_30d=rhr_30d,
                )
            )
        await session.commit()


async def _get(client) -> dict:
    async with client as c:
        resp = await c.get(f"/api/wellness-day?date={_SEED_DATE}")
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestStatusEnum:
    async def test_no_analysis_rows_yields_insufficient_data(self, client):
        """No HrvAnalysis/RhrAnalysis row → both statuses are the explicit
        `insufficient_data` sentinel, still inside the enum."""
        await _seed_wellness()
        data = await _get(client)

        assert data["hrv"]["status"] == "insufficient_data"
        assert data["rhr"]["status"] == "insufficient_data"
        assert data["hrv"]["status"] in _STATUS_ENUM
        assert data["rhr"]["status"] in _STATUS_ENUM

    async def test_status_passthrough_stays_in_enum(self, client):
        """Analysis rows present → status passes through verbatim and is a
        valid traffic-light value."""
        await _seed_wellness()
        await _seed_analysis(hrv_status="green", rhr_status="yellow")
        data = await _get(client)

        assert data["hrv"]["status"] == "green"
        assert data["rhr"]["status"] == "yellow"
        assert data["hrv"]["status"] in _STATUS_ENUM
        assert data["rhr"]["status"] in _STATUS_ENUM

    async def test_readiness_level_never_leaks_into_status(self, client):
        """The BE-2 guard: even when the seeded wellness row has a
        `readiness_level="balanced"`, the hrv/rhr `status` fields must stay
        on their own enum — that was the Halo fixture's bug."""
        await _seed_wellness(readiness_level="balanced")
        await _seed_analysis(hrv_status="green", rhr_status="red")
        data = await _get(client)

        assert data["hrv"]["status"] == "green"
        assert data["rhr"]["status"] == "red"
        # Readiness vocabulary must never reach a *.status field.
        assert data["hrv"]["status"] != "balanced"
        assert data["rhr"]["status"] != "balanced"
        assert data["hrv"]["status"] in _STATUS_ENUM
        assert data["rhr"]["status"] in _STATUS_ENUM


class TestBanisterScale:
    async def test_banister_recovery_is_0_100_not_fraction(self, client):
        """BE-2b: banister_recovery is a 0–100 percentage. A typical value
        (68) must pass through unscaled — the fixture's 0.68 is wrong."""
        await _seed_wellness(banister_recovery=68.0)
        data = await _get(client)

        b = data["stress"]["banister_recovery"]
        assert b == 68.0
        assert 0.0 <= b <= 100.0
        # Guards the 0–1-fraction mistake: a real percentage is > 1.
        assert b > 1.0

    async def test_banister_recovery_high_value_within_range(self, client):
        await _seed_wellness(banister_recovery=80.6)
        data = await _get(client)

        b = data["stress"]["banister_recovery"]
        assert 0.0 <= b <= 100.0
        assert b == 80.6


class TestMeaningTemplates:
    """Pure-function tests for `_hrv_meaning` / `_rhr_meaning` / Russian plural.

    Locks the «что это значит» card contract: status × streak → exactly one
    sentence, pre-localized server-side, NEVER an empty string for any of the
    four canonical statuses. A regression where (say) `red` quietly returned
    `None` would leave the card blank on the most actionable day.
    """

    def test_morning_word_ru_plural_forms(self):
        """1 → утро · 2-4 → утра · 5-20 → утр · teens (11-14) always plural-gen."""
        from api.routers.wellness import _morning_word_ru

        # singular
        assert _morning_word_ru(1) == "утро"
        assert _morning_word_ru(21) == "утро"
        # 2-4 (and 22-24)
        assert _morning_word_ru(2) == "утра"
        assert _morning_word_ru(3) == "утра"
        assert _morning_word_ru(22) == "утра"
        # 5-20 (and 25+) — teens included
        assert _morning_word_ru(5) == "утр"
        assert _morning_word_ru(11) == "утр"
        assert _morning_word_ru(14) == "утр"
        assert _morning_word_ru(25) == "утр"

    def test_hrv_meaning_green_streak_uses_count(self):
        """3-day green streak surfaces «3 утра» in the message (matches design)."""
        from api.routers.wellness import _hrv_meaning

        msg = _hrv_meaning("green", 3, "ru")
        assert msg is not None
        assert "3 утра подряд" in msg
        assert "rMSSD выше базы" in msg

    def test_hrv_meaning_green_no_streak_drops_count(self):
        """Streak<2 → no «N утра подряд» prefix, just the canned phrase."""
        from api.routers.wellness import _hrv_meaning

        msg_zero = _hrv_meaning("green", 0, "ru")
        msg_one = _hrv_meaning("green", 1, "ru")
        # A bare-streak day is the «base» phrasing (no count prefix), so neither
        # message should contain a leading digit. Both should equal each other.
        assert msg_zero is not None and msg_one is not None
        assert msg_zero == msg_one
        assert not msg_zero[:3].strip().isdigit()
        assert "rMSSD выше базы" in msg_zero

    def test_hrv_meaning_all_statuses_non_empty(self):
        """Every canonical status produces non-empty text in both languages."""
        from api.routers.wellness import _hrv_meaning

        for status in ("green", "yellow", "red", "insufficient_data"):
            for lang in ("ru", "en"):
                msg = _hrv_meaning(status, 0, lang)
                assert msg, f"empty meaning for {status} / {lang}"

    def test_hrv_meaning_unknown_status_returns_none(self):
        """Defensive: garbage status (shouldn't happen post-serializer) → None,
        which the frontend hides as «no card» rather than rendering «undefined»."""
        from api.routers.wellness import _hrv_meaning

        assert _hrv_meaning("garbage", 0, "ru") is None

    def test_rhr_meaning_green_streak_uses_count(self):
        """RHR streak is the inverted direction: `below` baseline = good."""
        from api.routers.wellness import _rhr_meaning

        msg = _rhr_meaning("green", 4, "ru")
        assert msg is not None
        assert "4 утра подряд" in msg
        assert "RHR ниже базы" in msg

    def test_rhr_meaning_yellow_warns_about_load(self):
        """Inverted axis: high RHR = elevated fatigue / illness signal."""
        from api.routers.wellness import _rhr_meaning

        msg_ru = _rhr_meaning("yellow", 0, "ru")
        msg_en = _rhr_meaning("yellow", 0, "en")
        assert msg_ru is not None and "RHR выше" in msg_ru
        assert msg_en is not None and "RHR above" in msg_en

    def test_meaning_en_streak_pluralizes(self):
        """English: 1 morning vs N mornings."""
        from api.routers.wellness import _hrv_meaning

        # streak=1 → no prefix (matches RU «base» phrasing)
        msg_one = _hrv_meaning("green", 1, "en")
        assert msg_one is not None
        assert "morning" not in msg_one.split(" ")[:3]  # no «1 morning» prefix
        # streak=2 → «2 mornings»
        msg_two = _hrv_meaning("green", 2, "en")
        assert msg_two is not None
        assert "2 mornings in a row" in msg_two


class TestMeaningInResponse:
    """End-to-end: `meaning` field reaches the API response intact."""

    async def test_meaning_present_for_green_hrv(self, client):
        await _seed_wellness()
        await _seed_analysis(hrv_status="green", rhr_status="green")
        data = await _get(client)

        assert data["hrv"]["meaning"] is not None
        assert "rMSSD" in data["hrv"]["meaning"]
        assert data["rhr"]["meaning"] is not None
        assert "RHR" in data["rhr"]["meaning"]

    async def test_meaning_present_for_insufficient_data(self, client):
        """No analysis row → `insufficient_data` branch → calibration message
        (not None). The card is the user's only signal when other fields are
        also null; a missing message would leave it blank."""
        await _seed_wellness()
        data = await _get(client)

        assert data["hrv"]["status"] == "insufficient_data"
        assert data["rhr"]["status"] == "insufficient_data"
        assert data["hrv"]["meaning"] is not None
        assert data["rhr"]["meaning"] is not None
        assert "14" in data["hrv"]["meaning"]  # «Меньше 14 дней»

    async def test_streak_field_is_zero_for_red_hrv(self, client):
        """Streak is the positive-direction count only — for `red` (below
        baseline) it's 0 and the message is about reducing load."""
        await _seed_wellness()
        # hrv=52.0 from `_seed_wellness`; HrvAnalysis row carries no rmssd_60d
        # so the join finds NULL baseline → streak breaks at 0.
        await _seed_analysis(hrv_status="red", rhr_status="red")
        data = await _get(client)

        assert data["hrv"]["streak_above_baseline"] == 0
        assert data["rhr"]["streak_below_baseline"] == 0
        assert data["hrv"]["meaning"] is not None
        # red HRV message instructs Z1/rest — must NOT contain the green
        # «restored» wording.
        assert "восстановлена" not in data["hrv"]["meaning"]


class TestStreakCounts:
    """End-to-end coverage that positive streaks actually reach the response.

    Without this, every other test in the file seeds NULL baselines and the
    streak helpers silently return 0 — regression where the helpers stop
    counting would go unnoticed. Locks both the count AND the «N утра подряд»
    prefix that the count drives.
    """

    async def test_three_day_hrv_streak_surfaces_in_response(self, client):
        """today + 2 prior days all above baseline → streak == 3, prefix in meaning."""
        await _seed_wellness()  # today: hrv=52.0
        await _seed_analysis(hrv_status="green", rhr_status="green", rmssd_60d=45.0)
        await _seed_streak_history(days=2, hrv_above=True, rhr_below=False, rmssd_60d=45.0)
        data = await _get(client)

        assert data["hrv"]["streak_above_baseline"] == 3
        assert data["hrv"]["meaning"] is not None
        assert "3 утра подряд" in data["hrv"]["meaning"]

    async def test_three_day_rhr_streak_surfaces_in_response(self, client):
        """RHR mirror: today + 2 prior days all below baseline → streak == 3."""
        await _seed_wellness()
        await _seed_analysis(hrv_status="green", rhr_status="green", rhr_today=57.0, rhr_30d=60.0)
        await _seed_streak_history(days=2, hrv_above=False, rhr_below=True, rhr_30d=60.0)
        data = await _get(client)

        assert data["rhr"]["streak_below_baseline"] == 3
        assert data["rhr"]["meaning"] is not None
        assert "3 утра подряд" in data["rhr"]["meaning"]

    async def test_hrv_streak_caps_at_14_day_window(self, client):
        """Streak count is truncated at `_STREAK_WINDOW_DAYS=14` — a longer
        real streak still reports as exactly 14, never more. Matches the HRV
        baseline calibration window: the streak can't be more trustworthy
        than the baseline it's measured against."""
        await _seed_wellness()
        await _seed_analysis(hrv_status="green", rhr_status="green", rmssd_60d=45.0)
        # Seed 20 prior days — 13 fit in the [today−13, today] window beyond
        # today, so the cap of 14 is what we expect.
        await _seed_streak_history(days=20, hrv_above=True, rhr_below=False, rmssd_60d=45.0)
        data = await _get(client)

        assert data["hrv"]["streak_above_baseline"] == 14
        assert "14 утр подряд" in data["hrv"]["meaning"]

    async def test_streak_breaks_on_first_below_baseline_day(self, client):
        """3 prior days above + today above + 1 break day in between → walking
        backwards stops at the break, streak counts only the contiguous tail."""
        await _seed_wellness()
        await _seed_analysis(hrv_status="green", rhr_status="green", rmssd_60d=45.0)
        # Day-1: above. Day-2: below (break). Day-3+: above (no longer counted).
        target = date.fromisoformat(_SEED_DATE)
        async with get_session() as session:
            session.add(
                Wellness(
                    user_id=1,
                    date=(target - timedelta(days=1)).isoformat(),
                    hrv=50.0,
                    updated=datetime.now(timezone.utc),
                )
            )
            session.add(
                HrvAnalysis(
                    user_id=1,
                    date=(target - timedelta(days=1)).isoformat(),
                    algorithm="flatt_esco",
                    status="green",
                    days_available=42,
                    rmssd_60d=45.0,
                )
            )
            session.add(
                Wellness(
                    user_id=1,
                    date=(target - timedelta(days=2)).isoformat(),
                    hrv=40.0,
                    updated=datetime.now(timezone.utc),
                )
            )
            session.add(
                HrvAnalysis(
                    user_id=1,
                    date=(target - timedelta(days=2)).isoformat(),
                    algorithm="flatt_esco",
                    status="yellow",
                    days_available=42,
                    rmssd_60d=45.0,
                )
            )
            await session.commit()
        data = await _get(client)

        # today + day-1 above, day-2 breaks → streak == 2
        assert data["hrv"]["streak_above_baseline"] == 2
        assert "2 утра подряд" in data["hrv"]["meaning"]
