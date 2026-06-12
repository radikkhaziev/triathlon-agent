import zoneinfo
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_data_user_id, is_demo, require_viewer
from bot.formatter import STATUS_EMOJI
from config import settings
from data.db import HrvAnalysis, RhrAnalysis, User, Wellness, get_session
from data.metrics import recompute_today_loads, recompute_today_ramp
from data.utils import extract_sport_ctl

router = APIRouter()


_CV_VERDICTS = {
    "ru": {"high": "высокая", "normal": "нормальная", "unstable": "нестабильная"},
    "en": {"high": "high", "normal": "normal", "unstable": "unstable"},
}

_SWC_VERDICTS = {
    "ru": {"noise": "в пределах шума", "improvement": "значимое улучшение", "decline": "значимое снижение"},
    "en": {"noise": "within noise", "improvement": "significant improvement", "decline": "significant decline"},
}


# «Что это значит» — per-metric deterministic interpretation on the
# `/wellness/:metric` detail screen. Pre-localized server-side (same pattern
# as `_cv_verdict` / `_swc_verdict` / `_category_display`); the client renders
# the string verbatim, no extra i18n round-trip. This is NOT AI: it's a small
# template-by-status lookup plus an optional streak prefix. See
# `docs/WEBAPP_HALO_REDESIGN_SPEC.md` decisions log entry 2026-05-23 «реверс
# G3=(b) для per-metric meaning» — one-voice (the AI prose) still lives on
# `/coach`; this card is the factual side.
_HRV_MEANING_TPL = {
    "ru": {
        "green_streak": "{n} {morning} подряд rMSSD выше базы — парасимпатика восстановлена. Можно по плану.",
        "green": "rMSSD выше базы — парасимпатика восстановлена. Можно по плану.",
        "yellow": (
            "rMSSD ниже привычного диапазона. Парасимпатика под нагрузкой — " "следи за самочувствием, плановая Z2 OK."
        ),
        "red": "rMSSD значительно ниже базы. Снизь нагрузку — Z1 или отдых.",
        "insufficient_data": "Меньше 14 дней данных — базовая линия ещё калибруется.",
    },
    "en": {
        "green_streak": (
            "{n} {morning} in a row rMSSD above baseline — " "parasympathetic system recovered. Train as planned."
        ),
        "green": "rMSSD above baseline — parasympathetic system recovered. Train as planned.",
        "yellow": (
            "rMSSD below the usual range. Parasympathetic system under load — "
            "watch how you feel, planned Z2 is fine."
        ),
        "red": "rMSSD significantly below baseline. Reduce load — Z1 or rest.",
        "insufficient_data": "Less than 14 days of data — baseline still calibrating.",
    },
}

_RHR_MEANING_TPL = {
    "ru": {
        "green_streak": "{n} {morning} подряд RHR ниже базы — сердце экономно работает в покое.",
        "green": "RHR ниже привычного — сердце экономно работает в покое.",
        "yellow": "RHR выше привычного — возможно, копится усталость или микро-болезнь.",
        "red": "RHR заметно выше базы — лёгкая нагрузка или отдых.",
        "insufficient_data": "Меньше 14 дней данных — базовая линия ещё калибруется.",
    },
    "en": {
        "green_streak": "{n} {morning} in a row RHR below baseline — heart resting efficiently.",
        "green": "RHR below the usual range — heart resting efficiently.",
        "yellow": "RHR above the usual range — fatigue or a mild infection may be building up.",
        "red": "RHR notably above baseline — light training or rest.",
        "insufficient_data": "Less than 14 days of data — baseline still calibrating.",
    },
}


def _morning_word_ru(n: int) -> str:
    """Russian plural form of «утро» in the '{N} {утро/утра/утр} подряд' frame.

    1, 21, 31 → "утро" · 2-4, 22-24 → "утра" · 5-20, 25-30 → "утр". Standard
    Russian count agreement; teens (11-14) always take the plural-genitive.
    """
    n_abs = abs(n) % 100
    if 11 <= n_abs <= 14:
        return "утр"
    last = n_abs % 10
    if last == 1:
        return "утро"
    if 2 <= last <= 4:
        return "утра"
    return "утр"


def _morning_word_en(n: int) -> str:
    """English: 'morning' for n==1, 'mornings' otherwise."""
    return "morning" if n == 1 else "mornings"


def _hrv_meaning(status: str, streak_above_60d: int, language: str = "ru") -> str | None:
    """One-sentence factual interpretation of today's HRV.

    Inputs are the HrvAnalysis traffic-light verdict plus the positive-direction
    streak (today and previous days where wellness.hrv > rmssd_60d). Streak is
    surfaced only for `green` — for `yellow`/`red` the message is about reducing
    load, not how long the slump has lasted (a negative streak isn't actionable
    in the same way).
    """
    tpl = _HRV_MEANING_TPL.get(language, _HRV_MEANING_TPL["en"])
    if status == "insufficient_data":
        return tpl["insufficient_data"]
    if status == "green":
        if streak_above_60d >= 2:
            morning = _morning_word_ru(streak_above_60d) if language == "ru" else _morning_word_en(streak_above_60d)
            return tpl["green_streak"].format(n=streak_above_60d, morning=morning)
        return tpl["green"]
    if status in ("yellow", "red"):
        return tpl[status]
    return None


def _rhr_meaning(status: str, streak_below_30d: int, language: str = "ru") -> str | None:
    """Same shape as `_hrv_meaning`, but the «good» direction is RHR *below*
    the 30d baseline (RHR is inverted: lower at rest = stronger heart)."""
    tpl = _RHR_MEANING_TPL.get(language, _RHR_MEANING_TPL["en"])
    if status == "insufficient_data":
        return tpl["insufficient_data"]
    if status == "green":
        if streak_below_30d >= 2:
            morning = _morning_word_ru(streak_below_30d) if language == "ru" else _morning_word_en(streak_below_30d)
            return tpl["green_streak"].format(n=streak_below_30d, morning=morning)
        return tpl["green"]
    if status in ("yellow", "red"):
        return tpl[status]
    return None


_STREAK_WINDOW_DAYS = 14
# Cap on how far back we walk to count a positive streak. Matches the HRV
# baseline calibration window (`insufficient_data` clears at 14d), so a streak
# can credibly run as long as the baseline itself is trusted. A user whose
# actual streak exceeds 14 days will still see «14 утр подряд» — the count is
# truncated, not lying.


async def _hrv_streak_above_60d(user_id: int, target_date: date, session: AsyncSession) -> int:
    """Consecutive days ending at `target_date` where wellness.hrv > rmssd_60d.

    Walks the last `_STREAK_WINDOW_DAYS` desc; first row that fails the
    condition (missing HrvAnalysis row, missing baseline, today's HRV
    at-or-below baseline) breaks the streak. Outer-joined on
    `(user_id, date, algorithm='flatt_esco')`: a wellness row without an
    analysis row still appears, with NULL baseline → comparison is false →
    streak ends, which is the desired semantics (analysis was insufficient
    that day → don't count it).

    The algorithm filter sits in the JOIN's ON-clause, not WHERE — that's
    intentional. WHERE would drop wellness rows that lack an analysis row
    entirely; the ON-clause lets them through with NULL baseline, which is
    what the streak loop expects. If a second algorithm ever gets written
    alongside flatt_esco, this still pins to flatt_esco only.
    """
    cutoff = (target_date - timedelta(days=_STREAK_WINDOW_DAYS - 1)).isoformat()
    result = await session.execute(
        select(Wellness.hrv, HrvAnalysis.rmssd_60d)
        .outerjoin(
            HrvAnalysis,
            (HrvAnalysis.user_id == Wellness.user_id)
            & (HrvAnalysis.date == Wellness.date)
            & (HrvAnalysis.algorithm == "flatt_esco"),
        )
        .where(
            Wellness.user_id == user_id,
            Wellness.date <= str(target_date),
            Wellness.date >= cutoff,
        )
        .order_by(Wellness.date.desc())
    )
    streak = 0
    for hrv_val, base_60 in result:
        if hrv_val is None or base_60 is None or float(hrv_val) <= base_60:
            break
        streak += 1
    return streak


async def _rhr_streak_below_30d(user_id: int, target_date: date, session: AsyncSession) -> int:
    """Consecutive days ending at `target_date` where rhr_today < rhr_30d.

    Single-table walk (RhrAnalysis carries `rhr_today` on the row, unlike HRV
    which keeps today's value on the wellness row). Same `_STREAK_WINDOW_DAYS`
    cap as the HRV streak.
    """
    cutoff = (target_date - timedelta(days=_STREAK_WINDOW_DAYS - 1)).isoformat()
    result = await session.execute(
        select(RhrAnalysis.rhr_today, RhrAnalysis.rhr_30d)
        .where(
            RhrAnalysis.user_id == user_id,
            RhrAnalysis.date <= str(target_date),
            RhrAnalysis.date >= cutoff,
        )
        .order_by(RhrAnalysis.date.desc())
    )
    streak = 0
    for rhr_today, base_30 in result:
        if rhr_today is None or base_30 is None or rhr_today >= base_30:
            break
        streak += 1
    return streak


def _cv_verdict(cv: float | None, language: str = "ru") -> str | None:
    if cv is None:
        return None
    v = _CV_VERDICTS.get(language, _CV_VERDICTS["en"])
    if cv < 5:
        return v["high"]
    if cv < 10:
        return v["normal"]
    return v["unstable"]


def _swc_verdict(
    today_val: float | None, baseline_60d: float | None, swc: float | None, language: str = "ru"
) -> str | None:
    if today_val is None or baseline_60d is None or swc is None:
        return None
    v = _SWC_VERDICTS.get(language, _SWC_VERDICTS["en"])
    delta = today_val - baseline_60d
    if abs(delta) < swc:
        return v["noise"]
    if delta > 0:
        return v["improvement"]
    return v["decline"]


def _format_sleep_duration(secs: int | None, language: str = "ru") -> str | None:
    if not secs:
        return None
    h, m = divmod(secs // 60, 60)
    if language == "en":
        return f"{h}h {m}m" if h else f"{m}m"
    return f"{h}ч {m}м" if h else f"{m}м"


def _hrv_block(hrv_row, hrv_today: float | None, streak_above_60d: int, language: str = "ru") -> dict:
    if not hrv_row:
        return {
            "status": "insufficient_data",
            "status_emoji": "⚪",
            "meaning": _hrv_meaning("insufficient_data", 0, language),
        }

    delta_pct = None
    if hrv_today and hrv_row.rmssd_7d and hrv_row.rmssd_7d > 0:
        delta_pct = round((hrv_today - hrv_row.rmssd_7d) / hrv_row.rmssd_7d * 100, 1)

    return {
        "status": hrv_row.status,
        "status_emoji": STATUS_EMOJI.get(hrv_row.status, "⚪"),
        "today": hrv_today,
        "mean_7d": hrv_row.rmssd_7d,
        "sd_7d": hrv_row.rmssd_sd_7d,
        "mean_60d": hrv_row.rmssd_60d,
        "sd_60d": hrv_row.rmssd_sd_60d,
        "delta_pct": delta_pct,
        "lower_bound": hrv_row.lower_bound,
        "upper_bound": hrv_row.upper_bound,
        "swc_verdict": _swc_verdict(hrv_today, hrv_row.rmssd_60d, hrv_row.swc, language),
        "cv_7d": hrv_row.cv_7d,
        "cv_verdict": _cv_verdict(hrv_row.cv_7d, language),
        "trend": (
            {
                "direction": hrv_row.trend_direction,
                "r_squared": hrv_row.trend_r_squared,
            }
            if hrv_row.trend_direction
            else None
        ),
        "streak_above_baseline": streak_above_60d,
        "meaning": _hrv_meaning(hrv_row.status, streak_above_60d, language),
    }


def _rhr_block(rhr_row, streak_below_30d: int, language: str = "ru") -> dict:
    if not rhr_row:
        return {
            "status": "insufficient_data",
            "status_emoji": "⚪",
            "meaning": _rhr_meaning("insufficient_data", 0, language),
        }

    delta_30d = None
    if rhr_row.rhr_today and rhr_row.rhr_30d:
        delta_30d = round(rhr_row.rhr_today - rhr_row.rhr_30d, 1)

    return {
        "status": rhr_row.status,
        "status_emoji": STATUS_EMOJI.get(rhr_row.status, "⚪"),
        "today": rhr_row.rhr_today,
        "mean_7d": rhr_row.rhr_7d,
        "sd_7d": rhr_row.rhr_sd_7d,
        "mean_30d": rhr_row.rhr_30d,
        "sd_30d": rhr_row.rhr_sd_30d,
        "mean_60d": rhr_row.rhr_60d,
        "sd_60d": rhr_row.rhr_sd_60d,
        "delta_30d": delta_30d,
        "lower_bound": rhr_row.lower_bound,
        "upper_bound": rhr_row.upper_bound,
        "cv_7d": rhr_row.cv_7d,
        "cv_verdict": _cv_verdict(rhr_row.cv_7d, language),
        "trend": (
            {
                "direction": rhr_row.trend_direction,
                "r_squared": rhr_row.trend_r_squared,
            }
            if rhr_row.trend_direction
            else None
        ),
        "streak_below_baseline": streak_below_30d,
        "meaning": _rhr_meaning(rhr_row.status, streak_below_30d, language),
    }


async def _build_wellness_response(row, target_date: date, user_id: int, language: str = "ru") -> dict:
    target_str = str(target_date)

    hrv_today = float(row.hrv) if row.hrv else None
    tsb = round(row.ctl - row.atl, 1) if row.ctl is not None and row.atl is not None else None
    sport_ctl = extract_sport_ctl(row.sport_info)

    # Single session for the whole response — analyses, streaks, sleep series,
    # and the two latest-* fallbacks all funnel through here. Without this the
    # endpoint opens ~7 separate sessions per call, which adds up on the Halo
    # wellness page that polls after RefreshButton.
    async with get_session() as session:
        hrv_flatt = await HrvAnalysis.get(user_id=user_id, dt=target_str, algorithm="flatt_esco", session=session)
        rhr_row = await RhrAnalysis.get(user_id=user_id, dt=target_str, session=session)
        hrv_streak = await _hrv_streak_above_60d(user_id, target_date, session)
        rhr_streak = await _rhr_streak_below_30d(user_id, target_date, session)
        sleep_series = await Wellness.get_sleep_series(user_id, target_str, 7, session=session)
        weight = row.weight if row.weight is not None else await Wellness.get_latest_weight(user_id, session=session)
        vo2max = row.vo2max if row.vo2max is not None else await Wellness.get_latest_vo2max(user_id, session=session)

    return {
        "date": target_str,
        "has_data": True,
        "recovery": {
            "score": row.recovery_score,
        },
        "hrv": _hrv_block(hrv_flatt, hrv_today, hrv_streak, language),
        "rhr": _rhr_block(rhr_row, rhr_streak, language),
        "sleep": {
            "score": row.sleep_score,
            "duration": _format_sleep_duration(row.sleep_secs, language),
            "duration_secs": row.sleep_secs,
            # Last 7 nights for the Sleep card bar-strip. Oldest→newest, the
            # target date is the last element; missing nights are None so the
            # frontend renders an "empty" bar without shifting the calendar.
            "last_7_nights": sleep_series,
        },
        "training_load": {
            "ctl": row.ctl,
            "atl": row.atl,
            "tsb": tsb,
            "ramp_rate": row.ramp_rate,
            "sport_ctl": sport_ctl,
        },
        "body": {
            # Weight + VO2max don't arrive in every Intervals.icu wellness row
            # (weigh-ins are sporadic, VO₂max rare). When the current row has
            # none, fall back to the last known value via get_latest_* —
            # otherwise the Body card shows "--" permanently for active users.
            # Same "last known" semantics as auth_me profile.*.
            "weight": weight,
            "body_fat": row.body_fat,
            "vo2max": vo2max,
            "steps": row.steps,
        },
        "stress": {
            "banister_recovery": row.banister_recovery,
        },
        "ai_recommendation": row.ai_recommendation,
        "updated_at": row.updated.isoformat() if row.updated else None,
    }


async def _wellness_has_prev(target_date: date, user_id: int) -> bool:
    target_str = str(target_date)
    async with get_session() as session:
        result = await session.execute(select(exists().where(Wellness.date < target_str, Wellness.user_id == user_id)))
        return result.scalar_one()


@router.get("/api/wellness-day")
async def wellness_day(
    dt: str = Query(default="", alias="date", description="Date YYYY-MM-DD, default=today"),
    user: User = Depends(require_viewer),
) -> dict:
    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    today = datetime.now(tz).date()

    if dt:
        try:
            target = date.fromisoformat(dt)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    else:
        target = today

    if target > today:
        target = today

    target_str = str(target)
    uid = get_data_user_id(user)
    row = await Wellness.get(uid, target)
    has_prev = await _wellness_has_prev(target, uid)
    has_next = target < today

    if row is None:
        return {
            "date": target_str,
            "has_data": False,
            "is_today": target == today,
            "has_prev": has_prev,
            "has_next": has_next,
        }

    # Demo gets English for the deterministic verdict/meaning strings (the
    # owner's row says "ru"); the AI free-text is stubbed out below.
    language = "en" if is_demo(user) else (user.language or "ru")
    result = await _build_wellness_response(row, target, uid, language=language)
    # Intervals.icu bakes today's planned workouts into ctl/atl/rampRate, so the
    # morning view looks as if the day's session is already done. Recompute
    # from yesterday's loads + actually-completed activities; de-plan ramp to match.
    if target == today:
        recomputed = await recompute_today_loads(uid)
        if recomputed is not None:
            ctl, atl, tsb = recomputed
            result["training_load"]["ctl"] = ctl
            result["training_load"]["atl"] = atl
            result["training_load"]["tsb"] = tsb
            projected_ramp = await recompute_today_ramp(uid, ctl)
            if projected_ramp is not None:
                result["training_load"]["ramp_rate"] = projected_ramp
    result["is_today"] = target == today
    result["has_prev"] = has_prev
    result["has_next"] = has_next
    if is_demo(user):
        # AI free-text is generated from mood check-ins / IQOS / user_facts and
        # routinely interpolates intimate content — never serialize it to demo.
        # Frontend renders a canned English sample off `demo_stub` instead.
        # See docs/DEMO_PUBLIC_ACCESS_SPEC.md Phase 2.
        result["ai_recommendation"] = None
        result["demo_stub"] = True
    return result
