"""Message formatting for dramatiq actors — morning, evening, post-activity reports."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bot.i18n import _, get_language
from config import settings
from data.db.dto import DRIFT_FTP_WATTS, DRIFT_LTHR_BPM, DRIFT_PACE_SEC_PER_KM, DRIFT_R2_HIGH, DRIFT_R2_MEDIUM
from tasks.dto import local_today

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from data.db import Activity, ActivityAchievement, ActivityDetail, ActivityHrv, ActivityWeather, Race, Wellness

# ---------------------------------------------------------------------------
# Shared constants (language-aware via _())
# ---------------------------------------------------------------------------


def _category_display() -> dict:
    return {
        "excellent": ("🟢", _("ОТЛИЧНОЕ ВОССТАНОВЛЕНИЕ")),
        "good": ("🟢", _("ГОТОВ К НАГРУЗКЕ")),
        "moderate": ("🟡", _("УМЕРЕННАЯ НАГРУЗКА")),
        "low": ("🔴", _("РЕКОМЕНДОВАН ОТДЫХ")),
    }


STATUS_EMOJI = {"green": "🟢", "yellow": "🟡", "red": "🔴", "insufficient_data": "⚪"}


_MONTHS = {
    "ru": {
        1: "января",
        2: "февраля",
        3: "марта",
        4: "апреля",
        5: "мая",
        6: "июня",
        7: "июля",
        8: "августа",
        9: "сентября",
        10: "октября",
        11: "ноября",
        12: "декабря",
    },
    "en": {
        1: "Jan",
        2: "Feb",
        3: "Mar",
        4: "Apr",
        5: "May",
        6: "Jun",
        7: "Jul",
        8: "Aug",
        9: "Sep",
        10: "Oct",
        11: "Nov",
        12: "Dec",
    },
}


def _get_months() -> dict:
    return _MONTHS.get(get_language(), _MONTHS["ru"])


def format_duration(seconds: int | None) -> str:
    """Format seconds as 'Xh Ym' or 'Ym'."""
    if not seconds:
        return "—"
    h, remainder = divmod(seconds, 3600)
    m = remainder // 60
    if h > 0:
        return f"{h}h{m:02d}m"
    return f"{m}m"


def sport_emoji(activity_type: str | None) -> str:
    """Return sport emoji based on canonical activity type."""
    if not activity_type:
        return "🏋️"
    _EMOJI = {"Ride": "🚴", "Run": "🏃", "Swim": "🏊"}
    return _EMOJI.get(activity_type, "🏋️")


def format_pace(sec_per_km: float | None) -> str | None:
    """Render s/km value as ``M:SS/km``. Returns ``None`` on invalid input.

    Uses ``round`` (not truncate) so 290.6 → 4:51, not 4:50. Three call sites:
      - Run summary in ``_build_summary_line`` — derived from ``moving_time /
        distance``, fractional second is meaningful.
      - Race summary in ``_build_post_race_message`` (``race.avg_pace_sec_km``,
        Float column) — old behaviour truncated; switching to ``round`` can
        flip the displayed pace by 1s on the ``.5+`` boundary. Acceptable.
      - Ramp-test messages — already feed an int from ``parse_pace_to_sec``,
        so rounding is a no-op on that path.
    """
    if not sec_per_km or sec_per_km <= 0:
        return None
    m, s = divmod(int(round(sec_per_km)), 60)
    return f"{m}:{s:02d}/km"


def _format_hms(seconds: int | None) -> str | None:
    if not seconds:
        return None
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


_ZONE_LABELS = ("Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "Z7")

# Decoupling traffic-light thresholds — abs() grading per docs/knowledge/decoupling.md.
# Negative drift with abs < 5% is the «normal warm-up artefact» case (green); large
# magnitudes (positive or negative) flag instability or hardware glitches (red).
_DECOUPLING_GREEN_PCT = 5.0
_DECOUPLING_YELLOW_PCT = 10.0

# Weather noise gates — below these the data is too quiet to surface.
_WIND_MIN_MPS = 0.5  # ~1.8 km/h — below ambient detection floor
_HEADWIND_MIN_PCT = 25.0  # surface "headwind X%" only when ≥ this share of the ride


def _format_pace_sec_per_100m(sec_per_km: float | None) -> str | None:
    """Render swim pace ``M:SS/100m`` from sec/km (≠ /km — same divmod template)."""
    if not sec_per_km or sec_per_km <= 0:
        return None
    sec_per_100m = sec_per_km / 10
    m, s = divmod(int(round(sec_per_100m)), 60)
    return f"{m}:{s:02d}/100m"


def _build_header_line(activity: Activity, detail: ActivityDetail | None) -> str:
    """First line: emoji + sport + duration + distance + elevation + TSS."""
    emoji = sport_emoji(activity.type)
    dur = format_duration(activity.moving_time)
    parts: list[str] = [f"{emoji} {activity.type or '?'} {dur}"]

    if detail and detail.distance and detail.distance > 0:
        km = detail.distance / 1000
        # Sub-km activities (warm-up jogs, pool sessions logged in m): use meters
        parts.append(f"{km:.2f} km" if km >= 1 else f"{int(detail.distance)} m")

    if detail and detail.elevation_gain and detail.elevation_gain >= 10:
        parts.append(f"↑{int(round(detail.elevation_gain))} m")

    header = " · ".join(parts)
    if activity.icu_training_load:
        header += f" | TSS {activity.icu_training_load:.0f}"
    return header


def _build_summary_line(activity: Activity, detail: ActivityDetail | None) -> str | None:
    """HR / pace / power summary line. Sport-specific."""
    sport = activity.type
    bits: list[str] = []

    if activity.average_hr:
        hr_part = f"💓 {activity.average_hr:.0f}"
        if detail and detail.max_hr:
            hr_part += f"–{detail.max_hr}"
        bits.append(hr_part)

    if sport == "Ride" and detail:
        if detail.avg_power and detail.normalized_power and detail.normalized_power != detail.avg_power:
            bits.append(f"⚡ {detail.avg_power}W (NP {detail.normalized_power}W)")
        elif detail.normalized_power:
            bits.append(f"⚡ {detail.normalized_power}W")
        elif detail.avg_power:
            bits.append(f"⚡ {detail.avg_power}W")

    if sport == "Run" and detail and detail.distance and activity.moving_time:
        # Derive pace from moving_time/distance — same logic as webapp Activity page.
        pace_sec_per_km = activity.moving_time / (detail.distance / 1000)
        formatted = format_pace(pace_sec_per_km)
        if formatted:
            bits.append(f"🏃 {formatted}")

    if sport == "Swim" and detail and detail.distance and activity.moving_time:
        pace_sec_per_km = activity.moving_time / (detail.distance / 1000)
        formatted = _format_pace_sec_per_100m(pace_sec_per_km)
        if formatted:
            bits.append(f"🏊 {formatted}")

    return " · ".join(bits) if bits else None


def _build_efficiency_line(detail: ActivityDetail | None) -> str | None:
    """EF / Decoupling / VI — classic durability metrics."""
    if not detail:
        return None
    bits: list[str] = []
    if detail.efficiency_factor:
        bits.append(f"EF {detail.efficiency_factor:.2f}")
    if detail.decoupling is not None:
        # abs() grading per docs/knowledge/decoupling.md — negative drift with
        # small magnitude is the normal warm-up artefact (green); large magnitudes
        # in either direction flag instability and surface as red.
        abs_drift = abs(detail.decoupling)
        if abs_drift > _DECOUPLING_YELLOW_PCT:
            drift_emoji = "🔴"
        elif abs_drift >= _DECOUPLING_GREEN_PCT:
            drift_emoji = "🟡"
        else:
            drift_emoji = "🟢"
        bits.append(f"Drift {detail.decoupling:.1f}% {drift_emoji}")
    if detail.variability_index:
        bits.append(f"VI {detail.variability_index:.2f}")
    return " · ".join(bits) if bits else None


def _build_fitness_snapshot_line(detail: ActivityDetail | None) -> str | None:
    """CTL / ATL / TSB snapshot at the moment of the activity."""
    if not detail or detail.ctl_snapshot is None or detail.atl_snapshot is None:
        return None
    ctl = detail.ctl_snapshot
    atl = detail.atl_snapshot
    tsb = ctl - atl
    return f"📊 CTL {ctl:.0f} · ATL {atl:.0f} · TSB {tsb:+.0f}"


# Compass octants — 8-way wind direction. Source matches Intervals.icu prevailing_wind_deg
# convention (0° = North, clockwise).
_WIND_OCTANTS_RU = ("С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ")
_WIND_OCTANTS_EN = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _wind_direction(deg: int | None) -> str | None:
    if deg is None:
        return None
    table = _WIND_OCTANTS_EN if get_language() == "en" else _WIND_OCTANTS_RU
    idx = int(round((deg % 360) / 45)) % 8
    return table[idx]


def _build_weather_line(weather: ActivityWeather | None) -> str | None:
    """Weather block — temperature, feels-like, wind, precipitation."""
    if not weather:
        return None
    bits: list[str] = []

    if weather.avg_temp_c is not None:
        temp = f"🌡 {weather.avg_temp_c:.0f}°C"
        if weather.avg_feels_like_c is not None and abs(weather.avg_feels_like_c - weather.avg_temp_c) >= 1:
            temp += f" ({_('ощущается')} {weather.avg_feels_like_c:.0f})"
        bits.append(temp)

    if weather.avg_wind_speed_mps is not None and weather.avg_wind_speed_mps >= _WIND_MIN_MPS:
        # Intervals.icu stores wind in m/s; convert to km/h for legibility.
        wind_kmh = weather.avg_wind_speed_mps * 3.6
        wind = f"💨 {wind_kmh:.0f} km/h"
        direction = _wind_direction(weather.prevailing_wind_deg)
        if direction:
            wind += f" {direction}"
        # Headwind % from Intervals.icu — share of activity spent into the wind.
        if weather.headwind_pct is not None and weather.headwind_pct >= _HEADWIND_MIN_PCT:
            wind += f" ({_('встречный')} {weather.headwind_pct:.0f}%)"
        bits.append(wind)

    if weather.max_rain_mm and weather.max_rain_mm > 0:
        bits.append(f"🌧 {weather.max_rain_mm:.1f} mm")
    if weather.max_snow_mm and weather.max_snow_mm > 0:
        bits.append(f"❄️ {weather.max_snow_mm:.1f} mm")

    return " · ".join(bits) if bits else None


def _build_polarization_line(activity: Activity, detail: ActivityDetail | None) -> str | None:
    """Polarization index — useful for workouts ≥60 min."""
    if not detail or detail.polarization_index is None:
        return None
    if not activity.moving_time or activity.moving_time < 3600:
        return None
    return f"PI {detail.polarization_index:.2f}"


def _format_achievement(ach: ActivityAchievement) -> str | None:
    """Render one ActivityAchievement row as a short string. None to skip."""
    if ach.type == "FTP_CHANGE":
        # extra={"delta": int}. Synthesised by ActivityAchievement.save_bulk.
        delta = (ach.extra or {}).get("delta")
        ftp = ach.ftp_at_time
        if delta is None or ftp is None:
            return None
        sign = "+" if delta > 0 else ""
        return f"⚡ FTP {sign}{delta} W → {ftp} W"

    if ach.type == "BEST_POWER":
        # 5s/10s/30s/60s/5min/etc. — value=watts, secs=window length.
        if not ach.value or not ach.secs:
            return None
        secs = int(ach.secs)
        if secs < 60:
            window = f"{secs}s"
        elif secs % 60 == 0:
            window = f"{secs // 60}m"
        else:
            window = f"{secs}s"
        return f"🏆 {window} PR {int(round(ach.value))} W"

    return None


def _build_achievements_block(achievements: list[ActivityAchievement] | None) -> list[str]:
    """Returns 0+ lines — one per significant achievement, capped at 4.

    Sort priority (instead of DB insertion order which has same-tick ``created_at``
    on bulk-inserts → tie broken by ``id``, arbitrary): FTP_CHANGE first
    (semantically big news, sometimes drowned by power PRs), then BEST_POWER by
    watts descending so the headline number leads. Cap is intentional — Telegram
    notification, not an audit log.
    """
    if not achievements:
        return []

    def _priority(ach: ActivityAchievement) -> tuple[int, float]:
        # FTP_CHANGE → group 0 (top); BEST_POWER → group 1; everything else → group 2.
        # Within a group, sort by watts desc (negate for ascending sort).
        if ach.type == "FTP_CHANGE":
            return (0, -(ach.value or 0))
        if ach.type == "BEST_POWER":
            return (1, -(ach.value or 0))
        return (2, -(ach.value or 0))

    ordered = sorted(achievements, key=_priority)
    lines: list[str] = []
    for ach in ordered:
        rendered = _format_achievement(ach)
        if rendered:
            lines.append(rendered)
        if len(lines) >= 4:
            break
    return lines


_BAR_WIDTH = 18  # block-bar width in chars; fits 3-line group on mobile Telegram


def _format_zone_bar(times_sec: list[int] | None, label: str) -> list[str]:
    """Two-line zone block: visual bar (proportional) + per-zone minutes.

    Returns ``[]`` when no time data, otherwise two lines:
        Label  ████████░░░░░░░░░░
               Z1 32m · Z2 14m · Z3 8m

    Bar uses 1/8-block characters so a zone gets a sliver of width even when
    it would round to 0 whole chars (e.g. 4% of 18 chars = 0.72 chars → 6
    eighths → ``▋``). Floor-division accumulates rounding loss per zone, so the
    summed bar can be slightly shorter than ``_BAR_WIDTH * 8`` eighths — the
    post-loop ``░`` padding handles that and also addresses the user-visible
    "не во всю длину" complaint.
    """
    if not times_sec or not any(t and t > 0 for t in times_sec):
        return []

    total = sum(t or 0 for t in times_sec)
    if total <= 0:
        return []

    fill_blocks = ("░", "▏", "▎", "▍", "▌", "▋", "▊", "▉", "█")

    # Each zone's width in eighths-of-a-char; floor-divide loses at most 7
    # eighths per zone, bounded by the post-loop slice + pad below.
    eighths = [(t or 0) * _BAR_WIDTH * 8 // total for t in times_sec]
    bar_chars: list[str] = []
    for e in eighths:
        full, rem = divmod(e, 8)
        bar_chars.append("█" * full)
        if rem:
            bar_chars.append(fill_blocks[rem])

    bar = "".join(bar_chars)
    # Pad to full width — addresses user's «не во всю длину» complaint. Total
    # eighths can't exceed ``_BAR_WIDTH * 8`` (floor-division upstream), so
    # the truncation branch is dead but kept as belt-and-braces against future
    # math drift.
    if len(bar) < _BAR_WIDTH:
        bar = bar + "░" * (_BAR_WIDTH - len(bar))

    label_bits: list[str] = []
    for i, t in enumerate(times_sec):
        if not t or t <= 0:
            continue
        zone_label = _ZONE_LABELS[i] if i < len(_ZONE_LABELS) else f"Z{i + 1}"
        mins = int(round(t / 60))
        label_bits.append(f"{zone_label} {mins}m")

    # Indent the label row to sit under the bar, not under the label. Computed
    # from ``label`` length + the 2-space separator so longer labels (e.g.
    # «Power») don't break alignment if added later.
    label_indent = " " * (len(label) + 2)
    return [f"{label}  {bar}", label_indent + " · ".join(label_bits)]


def _build_zone_bars(activity: Activity, detail: ActivityDetail | None) -> list[str]:
    """Per-sport zone bar group. Empty when no zone data."""
    if not detail:
        return []

    sport = activity.type
    lines: list[str] = []

    hr_label = "HR  "
    hr_lines = _format_zone_bar(detail.hr_zone_times, hr_label)
    if hr_lines:
        lines.extend(hr_lines)

    if sport == "Ride":
        pwr_lines = _format_zone_bar(detail.power_zone_times, "Pwr ")
        if pwr_lines:
            lines.extend(pwr_lines)
    elif sport == "Run":
        pace_lines = _format_zone_bar(detail.pace_zone_times, "Pace")
        if pace_lines:
            lines.extend(pace_lines)

    return lines


def build_post_activity_message(
    activity: Activity,
    hrv: ActivityHrv,
    race: Race | None = None,
    *,
    detail: ActivityDetail | None = None,
    weather: ActivityWeather | None = None,
    achievements: list[ActivityAchievement] | None = None,
) -> str:
    """Build post-activity notification.

    Layered rendering — each block self-gates on data presence. Minimal inputs
    (Activity + empty ActivityHrv sentinel) still produce a usable header line
    so non-HRV-eligible sports (Swim, Walk, ...) never get a blank message.

    Race-specific format when ``race`` is given.

    Optional ``detail`` / ``weather`` / ``achievements`` extend the message with
    distance, EF/decoupling, weather, CTL/TSB snapshot, zone bars, and PRs. All
    optional — kept defaultable so callers that don't load those rows (legacy
    tests, simple synthesis paths) keep working without a refactor.
    """
    if race is not None:
        return _build_post_race_message(activity, race)

    lines: list[str] = [_build_header_line(activity, detail)]

    summary = _build_summary_line(activity, detail)
    if summary:
        lines.append(summary)

    efficiency = _build_efficiency_line(detail)
    if efficiency:
        lines.append(efficiency)

    if hrv.dfa_a1_warmup is not None or hrv.dfa_a1_mean is not None:
        parts = []
        if hrv.dfa_a1_warmup is not None:
            parts.append(f"{hrv.dfa_a1_warmup:.2f} (warmup)")
        if hrv.dfa_a1_mean is not None:
            parts.append(f"{hrv.dfa_a1_mean:.2f} (avg)")
        lines.append(f"DFA a1: {' → '.join(parts)}")

    if hrv.ra_pct is not None:
        ra_emoji = "✅" if hrv.ra_pct > -5 else "⚠️"
        lines.append(f"Ra: {hrv.ra_pct:+.1f}% {ra_emoji}")

    if hrv.hrvt1_hr is not None:
        hrvt1 = f"HRVT1: {hrv.hrvt1_hr:.0f} bpm"
        if hrv.hrvt1_power is not None:
            hrvt1 += f" / {hrv.hrvt1_power:.0f}W"
        if hrv.hrvt1_pace is not None:
            hrvt1 += f" / {hrv.hrvt1_pace}"
        lines.append(hrvt1)

    if hrv.da_pct is not None and activity.moving_time and activity.moving_time >= 2400:
        lines.append(f"Da: {hrv.da_pct:+.1f}%")

    weather_line = _build_weather_line(weather)
    if weather_line:
        lines.append(weather_line)

    polarization = _build_polarization_line(activity, detail)
    if polarization:
        lines.append(polarization)

    snapshot = _build_fitness_snapshot_line(detail)
    if snapshot:
        lines.append(snapshot)

    achievement_lines = _build_achievements_block(achievements)
    if achievement_lines:
        lines.append("")  # visual separator before PRs
        lines.extend(achievement_lines)

    zone_lines = _build_zone_bars(activity, detail)
    if zone_lines:
        lines.append("")  # separator before zone bars
        lines.extend(zone_lines)

    return "\n".join(lines)


def _ramp_failure_text(reason: dict) -> str:
    """Localize a diagnose_hrv_thresholds code dict into a user message."""
    code = reason.get("code")
    if code == "too_few_points":
        return _("слишком мало валидных точек ({count} < 20)").format(count=reason.get("count", 0))
    if code == "a1_range_high":
        return _("DFA a1 не достиг лёгкой зоны (max {max_a1} < 0.9)").format(max_a1=reason.get("max_a1"))
    if code == "a1_range_low":
        return _("последняя ступень слишком лёгкая — DFA a1 не пересёк порог (min {min_a1} > 0.80)").format(
            min_a1=reason.get("min_a1")
        )
    if code == "positive_slope":
        return _("DFA a1 не падает с ростом HR (slope={slope})").format(slope=reason.get("slope"))
    if code == "noisy_fit":
        return _("линейный фит слишком шумный (R²={r_squared} < 0.5)").format(r_squared=reason.get("r_squared"))
    if code == "out_of_range":
        return _("интерполяция вне физиологического диапазона (HRVT1={hrvt1}, HRVT2={hrvt2})").format(
            hrvt1=reason.get("hrvt1"), hrvt2=reason.get("hrvt2")
        )
    return _("неизвестная причина")


def _ramp_failure_advice(reason: dict) -> str:
    """Actionable next-step guidance for a diagnose_hrv_thresholds code.

    The diagnostic code tells *what went wrong*; this tells *what to do about it*.
    Empty string for unknown codes (no false confidence).
    """
    code = reason.get("code")
    if code == "too_few_points":
        return _(
            "Тренировка короткая — нужна work-фаза 30+ минут. " "Проверь, что ramp-protocol сгенерирован полностью."
        )
    if code == "a1_range_high":
        return _(
            "На вершине HR не поднялся достаточно. Добавь финальный шаг с "
            "более жёстким темпом или беги последний шаг до отказа."
        )
    if code == "a1_range_low":
        return _("Финальный шаг слишком лёгкий — DFA a1 не дошёл до порога. " "Бери выше темп на последних 2-3 шагах.")
    if code == "positive_slope":
        return _(
            "HR-данные подозрительные (DFA растёт вместе с HR). "
            "Проверь chest strap — оптический датчик не подходит для DFA."
        )
    if code == "noisy_fit":
        return _(
            "Слишком много шума в данных. Возможные причины: outdoor против "
            "ветра/холмов, нестабильный темп. Попробуй на тредмилле."
        )
    if code == "out_of_range":
        return _(
            "Threshold в Intervals.icu сильно расходится с реальностью. "
            "Обнови LTHR или threshold pace вручную, потом перетестируй."
        )
    # Unknown code — surfaces no advice line (caller skips on empty string),
    # but log so a new diagnose code added in `data/hrv_activity.py` doesn't
    # silently ship a UX regression. Caught in monitoring before users complain.
    logger.warning("ramp failure advice: unknown diagnose code %r — add to _ramp_failure_advice", code)
    return ""


_DRIFT_GATE_BY_METRIC = {
    "LTHR": DRIFT_LTHR_BPM,
    "PACE": DRIFT_PACE_SEC_PER_KM,
    "FTP": DRIFT_FTP_WATTS,
}


def _drift_button_status(metric: str, measured: float, config: float, r2: float | None) -> tuple[bool, str | None, str]:
    """Decide whether to surface the «Update zones» button + which hint + R² tier.

    Returns ``(button_visible, hint_text, r2_tier)`` where ``r2_tier`` is
    one of ``"high"`` / ``"medium"`` / ``"low"`` / ``"none"``:

      - ``high`` (R² ≥ 0.85): caller may auto-update without user confirmation;
        ``button_visible`` is False (no need to show button) but ``hint_text``
        announces the auto-update.
      - ``medium`` (0.70 ≤ R² < 0.85): show the button (current default UX).
      - ``low``    (R² < 0.70): no button, soft hint asks for retest.
      - ``none``   (drift below absolute threshold): no button, no hint.

    Absolute drift gate per metric (RAMP_TEST_BIKE_SPEC §8). The UI mirror MUST
    match ``data/db/user.py`` — both pull from ``data.db.dto`` constants AND
    apply the same ``round(measured)`` to the float HRVT2 reading. Without the
    round, half-bpm boundary cases (e.g. hrvt2_hr=152.6, config=150) would
    flip backend (round → 153 → Δ=3 fires) but not UI (raw 2.6 → silent),
    creating «zones updated but no button shown» UX bugs.
    """
    delta = round(measured) - config
    if metric not in _DRIFT_GATE_BY_METRIC:
        # Caller bug: typo or new metric added to backend without the UI mirror.
        # Silently using the LTHR gate would apply the wrong unit semantics
        # (3 bpm vs 5 W vs 5 s/km) and silently mis-render the message — log
        # loud, show nothing.
        logger.warning("_drift_button_status: unknown metric %r — UI suppressed", metric)
        return False, None, "none"
    gate = _DRIFT_GATE_BY_METRIC[metric]
    if abs(delta) < gate:
        return False, None, "none"
    if r2 is None or r2 < DRIFT_R2_MEDIUM:
        return False, _("низкое R² — повтори ramp test для обновления зон"), "low"
    if r2 >= DRIFT_R2_HIGH:
        # Phrased as «in-flight», not «completed» — the actor dispatch
        # happens AFTER this message is sent (see tasks/actors/activities.py),
        # so a delayed/failed actor leaves the user with an inaccurate hint
        # if we claim the action is done.
        return False, _("Запущено авто-обновление зон (high confidence)"), "high"
    return True, _("Рекомендуем обновить зоны"), "medium"


def build_ramp_test_message(
    activity: Activity,
    hrv: ActivityHrv,
    config_lthr: int | None,
    failure_reason: dict | None = None,
    *,
    config_threshold_pace: float | None = None,
    hrvt2_pace_sec: int | None = None,
    config_ftp: int | None = None,
) -> tuple[str, bool, bool]:
    """Build ramp-test-specific notification.

    Returns ``(message, show_update_zones_button, auto_update_fired)``:

    - ``show_update_zones_button``: the inline «Обновить зоны» button surfaces
      on **medium** R² confidence (0.70 ≤ R² < 0.85) with absolute drift past
      the per-metric gate (3 bpm / 5 s/km / 5 W).
    - ``auto_update_fired``: any metric crossed both the drift gate AND the
      **high** R² floor (R² ≥ 0.85). Caller is expected to dispatch
      ``actor_update_zones`` automatically; the message text already announces
      the auto-update inline.

    Mirrors ``User.detect_threshold_drift``. The values pushed (HRVT2 HR,
    pace at HRVT2, pow at HRVT2) align with Intervals.icu's `lthr` /
    `threshold_pace` / `ftp` fields, which conceptually correspond to the
    anaerobic threshold.
    """
    sport = activity.type or "?"
    lines: list[str] = [f"⚡ {_('Ramp Test')} ({sport}) — {_('результат')}"]
    show_button = False
    auto_update_fired = False

    if hrv.hrvt1_hr is not None:
        hrvt1 = f"HRVT1: {hrv.hrvt1_hr:.0f} bpm"
        if hrv.hrvt1_power:
            hrvt1 += f" / {hrv.hrvt1_power:.0f}W"
        if hrv.hrvt1_pace:
            hrvt1 += f" / {hrv.hrvt1_pace}"
        lines.append(hrvt1)
        if hrv.hrvt2_hr:
            hrvt2 = f"HRVT2: {hrv.hrvt2_hr:.0f} bpm"
            if hrv.hrvt2_power:
                hrvt2 += f" / {hrv.hrvt2_power:.0f}W"
            if hrv.hrvt2_pace:
                hrvt2 += f" / {hrv.hrvt2_pace}"
            lines.append(hrvt2)

        meta_bits = []
        if hrv.threshold_r_squared is not None:
            meta_bits.append(f"R²={hrv.threshold_r_squared:.2f}")
        if hrv.threshold_confidence:
            meta_bits.append(hrv.threshold_confidence)
        if meta_bits:
            lines.append(f"({', '.join(meta_bits)})")

        r2 = hrv.threshold_r_squared
        soft_hints: list[str] = []  # «низкое R²» — collected if no drift fires

        if config_lthr and hrv.hrvt2_hr is not None:
            lthr_delta = round(hrv.hrvt2_hr) - config_lthr
            lines.append(f"{_('текущий LTHR')}: {config_lthr} bpm (Δ {lthr_delta:+d} bpm)")
            visible, hint, tier = _drift_button_status("LTHR", hrv.hrvt2_hr, config_lthr, r2)
            if tier == "high":
                auto_update_fired = True
                lines.append(f"✅ {hint}")
            elif visible:
                show_button = True
                lines.append(f"💡 {hint}")
            elif hint:
                soft_hints.append(hint)

        if config_threshold_pace and hrvt2_pace_sec:
            cfg_pace = int(round(config_threshold_pace))
            pace_delta = hrvt2_pace_sec - cfg_pace
            cfg_pace_fmt = format_pace(cfg_pace) or f"{cfg_pace} s/km"
            lines.append(f"{_('текущий threshold pace')}: {cfg_pace_fmt} (Δ {pace_delta:+d} s/km)")
            visible, hint, tier = _drift_button_status("PACE", hrvt2_pace_sec, cfg_pace, r2)
            if tier == "high":
                auto_update_fired = True
                # Announce only once even if multiple metrics are high — the
                # LTHR branch already added the headline auto-update line, so
                # subsequent high tiers note the metric inline.
                lines.append(f"✅ {hint} ({_('threshold pace')})")
            elif visible and not show_button:
                show_button = True
                lines.append(f"💡 {hint}")
            elif visible:
                lines.append(f"💡 {hint} ({_('threshold pace')})")
            elif hint:
                soft_hints.append(hint)

        if config_ftp and hrv.hrvt2_power is not None:
            ftp_delta = round(hrv.hrvt2_power) - config_ftp
            lines.append(f"{_('текущий FTP')}: {config_ftp} W (Δ {ftp_delta:+d} W)")
            visible, hint, tier = _drift_button_status("FTP", hrv.hrvt2_power, config_ftp, r2)
            if tier == "high":
                auto_update_fired = True
                lines.append(f"✅ {hint} ({_('FTP')})")
            elif visible and not show_button:
                show_button = True
                lines.append(f"💡 {hint}")
            elif visible:
                lines.append(f"💡 {hint} ({_('FTP')})")
            elif hint:
                soft_hints.append(hint)

        # Show one soft hint only when no drift fired anywhere — avoids
        # «recommend update» + «low R²» showing together.
        if not show_button and not auto_update_fired and soft_hints:
            lines.append(f"ℹ️ {soft_hints[0]}")
    else:
        lines.append(f"⚠️ {_('детекция HRVT не удалась')}")
        if failure_reason:
            lines.append(f"{_('причина')}: {_ramp_failure_text(failure_reason)}")
            advice = _ramp_failure_advice(failure_reason)
            if advice:
                lines.append(f"💡 {advice}")

    return "\n".join(lines), show_button, auto_update_fired


# ---------------------------------------------------------------------------
# RPE inline keyboard (Borg CR-10, see docs/RPE_SPEC.md)
# ---------------------------------------------------------------------------

# Anchor labels: emoji only on 1, 3, 5, 7, 10 — keeps mobile rows readable.
_RPE_BUTTON_LABELS: dict[int, str] = {
    1: "1 😴",
    2: "2",
    3: "3 😌",
    4: "4",
    5: "5 💪",
    6: "6",
    7: "7 🔥",
    8: "8",
    9: "9",
    10: "10 🤯",
}

# Public mapping for places that render the value back to the user (e.g. the
# "RPE: 7 🔥" suffix appended to the message after a successful tap).
RPE_EMOJI_BY_VALUE: dict[int, str] = {
    1: "😴",
    2: "",
    3: "😌",
    4: "",
    5: "💪",
    6: "",
    7: "🔥",
    8: "",
    9: "",
    10: "🤯",
}


def rpe_label_with_emoji(value: int) -> str:
    """Format ``"7 🔥"`` for in-message rendering. Bare number when no anchor emoji."""
    emoji = RPE_EMOJI_BY_VALUE.get(value, "")
    return f"{value} {emoji}".strip()


def build_rpe_keyboard(activity_id: str) -> dict:
    """Two-row inline keyboard for Borg CR-10 RPE rating, raw Telegram Bot API format.

    Returns a dict matching the ``inline_keyboard`` markup schema so it can
    be passed directly to :meth:`tasks.tools.TelegramTool.send_message`.

    Callback data: ``rpe:{activity_id}:{value}`` — handled by
    :func:`bot.main.handle_rpe_callback`. Single-shot semantics: handler
    edits the message to remove this keyboard after the first successful tap.
    """

    def _btn(value: int) -> dict:
        return {"text": _RPE_BUTTON_LABELS[value], "callback_data": f"rpe:{activity_id}:{value}"}

    return {
        "inline_keyboard": [
            [_btn(v) for v in (1, 2, 3, 4, 5)],
            [_btn(v) for v in (6, 7, 8, 9, 10)],
        ]
    }


def build_activity_link_button(activity_id: str) -> dict:
    """Single inline-keyboard button opening the activity detail page in the Mini App.

    Raw Telegram Bot API format — append the returned dict as a keyboard row.
    """
    url = f"{settings.API_BASE_URL.rstrip('/')}/activity/{activity_id}"
    return {"text": _("📊 Открыть тренировку"), "web_app": {"url": url}}


def _build_post_race_message(activity: Activity, race: Race) -> str:
    """Race finish notification with distance, time, pace, fitness context."""
    sport = sport_emoji(activity.type)
    name = race.name or (activity.type or _("Гонка"))
    header = f"🏁 {sport} {_('Гонка завершена')}: {name}"
    if race.race_type:
        header += f" ({race.race_type})"

    lines: list[str] = [header]

    finish = _format_hms(race.finish_time_sec) or _format_hms(activity.moving_time)
    goal = _format_hms(race.goal_time_sec)
    dist_km = round(race.distance_m / 1000, 2) if race.distance_m else None

    time_parts: list[str] = []
    if finish:
        time_parts.append(f"⏱ {finish}" + (f" ({_('цель')}: {goal})" if goal else ""))
    if dist_km is not None:
        time_parts.append(f"📏 {dist_km} km")
    pace = format_pace(race.avg_pace_sec_km)
    if pace:
        time_parts.append(f"⚡ {pace}")
    if time_parts:
        lines.append(" | ".join(time_parts))

    hr_parts: list[str] = []
    if activity.average_hr:
        hr_parts.append(f"💓 avg {activity.average_hr:.0f}")
    if activity.icu_training_load:
        hr_parts.append(f"TSS {activity.icu_training_load:.0f}")
    if hr_parts:
        lines.append(" | ".join(hr_parts))

    ctx_parts: list[str] = []
    if race.race_day_ctl is not None:
        ctx_parts.append(f"CTL {race.race_day_ctl:.0f}")
    if race.race_day_tsb is not None:
        ctx_parts.append(f"TSB {race.race_day_tsb:+.0f}")
    if race.race_day_recovery_score is not None:
        ctx_parts.append(f"Recovery {race.race_day_recovery_score:.0f}")
    if ctx_parts:
        lines.append("📊 " + " | ".join(ctx_parts))

    if race.placement:
        place = f"{race.placement}"
        if race.placement_total:
            place += f"/{race.placement_total}"
        lines.append(f"🏆 {_('Место')}: {place}")

    lines.append("")
    lines.append(_("Заполни детали (RPE, погода, заметки) — запомню для анализа."))

    return "\n".join(lines)


def _format_workout_short(w) -> str:
    """Format a ScheduledWorkout as short string."""
    sport_names = {
        "Swim": _("Плавание"),
        "Ride": _("Вело"),
        "Run": _("Бег"),
        "Other": _("Другое"),
    }
    sport = sport_names.get(w.type or "", w.type or _("Тренировка"))
    name_part = ""
    if w.name:
        parts = w.name.split(":", 1)
        name_part = parts[1].strip() if len(parts) > 1 else parts[0].strip()
    dur = format_duration(w.moving_time)
    return f"{sport} {name_part} {dur}" if name_part else f"{sport} {dur}"


def build_evening_message(
    row: Wellness | None,
    activities: list[Activity],
    hrv_analyses: list[ActivityHrv],
    tomorrow_workouts: list | None = None,
) -> str:
    """Build evening report message."""
    today = local_today()
    date_str = f"{today.day} {_get_months().get(today.month, '')}"

    lines: list[str] = [f"📊 {_('Итог дня')} — {date_str}", ""]

    if activities:
        total_tss = sum(a.icu_training_load or 0 for a in activities)
        lines.append(f"{_('Тренировки')}: {len(activities)} | TSS: {total_tss:.0f}")
        for a in activities:
            emoji = sport_emoji(a.type)
            dur = format_duration(a.moving_time)
            tss = f" (TSS {a.icu_training_load:.0f})" if a.icu_training_load else ""
            lines.append(f"  {emoji} {a.type or '?'} {dur}{tss}")
    else:
        lines.append(f"🏋️ {_('День отдыха')}")

    lines.append("")

    if row:
        if row.recovery_score is not None:
            emoji, title = _category_display().get(row.recovery_category or "", ("⚪", "—"))
            lines.append(f"Recovery: {row.recovery_score:.0f}/100 ({title.lower()})")

        ess_banister_parts = []
        if row.ess_today is not None:
            ess_banister_parts.append(f"ESS: {row.ess_today:.1f}")
        if row.banister_recovery is not None:
            ess_banister_parts.append(f"Banister: {row.banister_recovery:.0f}%")
        if ess_banister_parts:
            lines.append(" | ".join(ess_banister_parts))

        if row.hrv is not None:
            hrv_emoji = STATUS_EMOJI.get(row.readiness_level or "", "⚪")
            lines.append(f"HRV: {hrv_emoji} {row.hrv:.1f} {_('мс')}")

        if row.resting_hr is not None:
            lines.append(f"RHR: {row.resting_hr} {_('уд/мин')}")

    processed = [h for h in hrv_analyses if h.processing_status == "processed" and h.ra_pct is not None]
    if processed:
        ra_parts = []
        for h in processed:
            sport = h.activity_type.lower() if h.activity_type else "?"
            ra_parts.append(f"Ra {h.ra_pct:+.1f}% ({sport})")
        lines.append(f"DFA: {' | '.join(ra_parts)}")

    if tomorrow_workouts:
        workout_strs = [_format_workout_short(w) for w in tomorrow_workouts if w.category == "WORKOUT"]
        if workout_strs:
            lines.append("")
            lines.append(f"📋 {_('Завтра')}: {', '.join(workout_strs)}")
    elif tomorrow_workouts is not None:
        lines.append("")
        lines.append(f"📋 {_('Завтра')}: {_('отдых')}")

    return "\n".join(lines)


def build_morning_message(row: Wellness) -> str:
    """Build compact morning Telegram message.

    Drift-alert rendering lives in ``tasks.actors.reports`` (the morning-report
    actor), not here — that path consumes the live ``ThresholdDriftDTO`` object
    directly with proper formatting. The dead ``threshold_drift=`` parameter
    that used to hang off this function was never wired up in production.
    """
    lines = []

    score = row.recovery_score or 0
    cat = row.recovery_category or "moderate"
    cat_display = _category_display().get(cat, ("", cat))[1]
    hrv_emoji = STATUS_EMOJI.get(row.readiness_level or "", "⚪")
    lines.append(f"Recovery {score:.0f} ({cat_display}), HRV {hrv_emoji}")

    # 5-band model (see `data/utils.py:tsb_zone`): only the `risk` zone
    # surfaces in the morning message. `optimal` / `gray` / `fresh` /
    # `transition` are informational on the frontend and don't warrant a
    # Telegram line.
    tsb = (row.ctl - row.atl) if row.ctl and row.atl else None
    if tsb is not None and tsb < -30:
        lines.append(f"TSB: {tsb:+.0f} 🔴 (high risk)")

    return "\n".join(lines)


def build_onboarding_hey_message() -> str:
    """Post-onboarding nudge for athletes who finished bootstrap but haven't
    sent a single chat message in 24-48h (issue #258). i18n via the active
    contextvar — caller must ``set_language(user.language)`` first.

    The body deliberately spells out the chat mental model (stateless per
    message + Reply continues + long-term facts) since it's non-obvious and
    affects how the athlete writes their first message.
    """
    return _(
        "👋 Привет! Готов помочь с тренировками и восстановлением.\n\n"
        "⚙️ Как со мной работать:\n"
        "• каждое сообщение — отдельный диалог (контекст не тянется)\n"
        "• Reply на моё сообщение — продолжает разговор\n"
        "• важные факты (травмы, график) я запоминаю\n\n"
        "Пробуй!"
    )
