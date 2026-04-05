# Level 2: DFA Alpha 1 — План реализации

> HRV-анализ во время тренировки. Post-activity pipeline: FIT → RR → DFA a1 → thresholds → Ra/Da.

DFA alpha1 (α1) — это краткосрочный показатель фрактальной корреляции RR-интервалов. При нагрузке α1 монотонно снижается: α1 ≈ 0.75 соответствует аэробному порогу (HRVT1), α1 ≈ 0.50 — анаэробному (HRVT2). Ra (Readiness) сравнивает текущую мощность/темп на разминке с 14-дневным baseline; Da (Durability) — drift внутри длинной тренировки.

> Full theory: [docs/knowledge/dfa-alpha1.md](knowledge/dfa-alpha1.md)

---

## Архитектура

Отдельная cron job, работает поверх уже загруженных activities в БД:

```
sync_activities_job (hourly :30) → activities table (id, type, moving_time, avg_hr)
                                          ↓
process_fit_job (hourly :45)     → для каждой необработанной bike/run активности:
                                   1. Скачать оригинальный FIT (Intervals.icu API)
                                   2. Извлечь RR-интервалы (fitparse)
                                   3. Artifact correction
                                   4. DFA a1 timeseries (скользящее окно 2 мин)
                                   5. Threshold detection (HRVT1/HRVT2)
                                   6. Ra (Readiness) — сравнение warmup Pa с baseline
                                   7. Da (Durability) — первая vs вторая половина
                                   8. Сохранить → activity_hrv table
```

### Что НЕ обрабатывается

- Плавание — нет RR в воде
- WeightTraining, Walk — нет смысла для DFA
- Активности < 15 мин — недостаточно данных
- Активности без HRM-Dual (запястье) — плохое качество RR

---

## Зависимости

### Новые пакеты (pyproject.toml)

```toml
fitparse = ">=0.0.7"    # FIT file parsing (RR intervals)
scipy = ">=1.10"         # DFA alpha 1 calculation
```

### API endpoint (Intervals.icu)

```
GET /api/v1/activity/{id}/file
→ Returns: original FIT file (binary)
→ Content-Type: application/octet-stream
```

---

## Шаги реализации

### Шаг 1. Таблица `activity_hrv` + миграция

```python
class ActivityHrvRow(Base):
    __tablename__ = "activity_hrv"

    activity_id: Mapped[str] = mapped_column(String, ForeignKey("activities.id"), primary_key=True)
    date: Mapped[str] = mapped_column(String)              # "YYYY-MM-DD"
    activity_type: Mapped[str] = mapped_column(String)     # "Ride" | "Run"

    # Quality
    hrv_quality: Mapped[str | None] = mapped_column(String, nullable=True)   # good | moderate | poor
    artifact_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    rr_count: Mapped[int | None] = mapped_column(Integer, nullable=True)     # total RR intervals

    # DFA alpha 1 summary
    dfa_a1_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    dfa_a1_warmup: Mapped[float | None] = mapped_column(Float, nullable=True)  # first 15 min

    # Thresholds (if detected)
    hrvt1_hr: Mapped[float | None] = mapped_column(Float, nullable=True)     # HR at a1=0.75
    hrvt1_power: Mapped[float | None] = mapped_column(Float, nullable=True)  # Power at a1=0.75 (bike)
    hrvt1_pace: Mapped[str | None] = mapped_column(String, nullable=True)    # Pace at a1=0.75 (run)
    hrvt2_hr: Mapped[float | None] = mapped_column(Float, nullable=True)     # HR at a1=0.50
    threshold_r_squared: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold_confidence: Mapped[str | None] = mapped_column(String, nullable=True)  # high | moderate | low

    # Readiness (Ra)
    ra_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    pa_today: Mapped[float | None] = mapped_column(Float, nullable=True)     # power/pace at fixed a1

    # Durability (Da)
    da_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Status: processed | no_rr_data | low_quality | too_short | error
    processing_status: Mapped[str] = mapped_column(String, default="processed")

    # Raw timeseries (JSON) — для графиков в webapp
    dfa_timeseries: Mapped[dict | None] = mapped_column(JSON, nullable=True)
```

**Ключевое поле: `processing_status`** — позволяет отличить "ещё не обработан" (нет записи) от "обработан, но нет RR" (`no_rr_data`). Job не будет повторно скачивать FIT для уже обработанных.

### Шаг 2. Таблица `pa_baseline`

```python
class PaBaselineRow(Base):
    __tablename__ = "pa_baseline"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    activity_type: Mapped[str] = mapped_column(String)     # "Ride" | "Run"
    date: Mapped[str] = mapped_column(String)              # "YYYY-MM-DD"
    pa_value: Mapped[float] = mapped_column(Float)         # Power/pace at fixed a1 (warmup)
    dfa_a1_ref: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality: Mapped[str | None] = mapped_column(String, nullable=True)
```

Используется для расчёта Ra — среднее Pa за 2 недели = baseline.

### Шаг 3. Скачивание FIT — `intervals_client.py`

```python
async def download_fit(self, activity_id: str) -> bytes | None:
    """Download original FIT file for an activity.

    Returns raw bytes or None if not available.
    """
    resp = await self._request(
        "GET",
        f"/activity/{activity_id}/file",
    )
    if resp.status_code == 404:
        return None
    return resp.content
```

### Шаг 4. Модуль `data/hrv_activity.py` — ядро Level 2

Новый модуль с 6 основными функциями:

#### 4.1 extract_rr_intervals(fit_bytes) → list[float]

```python
def extract_rr_intervals(fit_bytes: bytes) -> list[float]:
    """Extract RR intervals (ms) from FIT file HRV messages.

    FIT HRV messages contain arrays of RR intervals (seconds, UINT16/1000).
    Values 0xFFFF (65.535s) are invalid markers — filter them out.

    Returns: list of RR intervals in milliseconds.
    """
```

#### 4.2 correct_rr_artifacts(rr_ms) → dict

```python
def correct_rr_artifacts(
    rr_ms: list[float],
    threshold_pct: float = 0.10,  # 10% — recommended for HRM-Dual
) -> dict:
    """Artifact correction (Lipponen & Tarvainen 2019).

    Returns:
        {
            "rr_corrected": [...],
            "artifact_count": 12,
            "artifact_pct": 1.8,
            "quality": "good"    # good (<5%), moderate (5-10%), poor (>10%)
        }
    """
```

#### 4.3 calculate_dfa_alpha1(rr_ms) → float

```python
def calculate_dfa_alpha1(
    rr_ms: np.ndarray,
    window_beats: tuple[int, int] = (4, 16),
) -> float:
    """Detrended Fluctuation Analysis — short-term scaling exponent.

    Algorithm:
    1. Integrate: y[i] = cumsum(RR - mean(RR))
    2. Split y into windows of size n (from 4 to 16 beats)
    3. Detrend each window (linear fit), compute residuals
    4. F(n) = sqrt(mean(residuals²))
    5. alpha1 = slope(log(n), log(F(n)))

    Interpretation:
    - a1 > 1.0:  low intensity (rest)
    - a1 ≈ 0.75: aerobic threshold (HRVT1)
    - a1 ≈ 0.50: anaerobic threshold (HRVT2)
    - a1 < 0.50: max effort
    """
```

#### 4.4 calculate_dfa_timeseries(rr_ms) → list[dict]

```python
def calculate_dfa_timeseries(
    rr_ms: list[float],
    window_sec: int = 120,  # 2 min standard
    step_sec: int = 5,
) -> list[dict]:
    """Sliding-window DFA alpha 1 across activity.

    For each window:
    1. Take RR intervals spanning last window_sec seconds
    2. Check artifact_pct < 10%
    3. Calculate DFA alpha 1
    4. Pair with HR from the same window

    Returns: [{"time_sec": 120, "dfa_a1": 1.05, "hr_avg": 118, "artifact_pct": 1.2}, ...]
    """
```

#### 4.5 detect_hrv_thresholds(timeseries) → dict | None

```python
def detect_hrv_thresholds(
    dfa_timeseries: list[dict],
) -> dict | None:
    """Detect HRVT1 (a1=0.75) and HRVT2 (a1=0.50) from DFA timeseries.

    Strategy:
    1. Find ramp segment (monotonic HR increase, ≥10 min)
    2. Linear regression: DFA_a1 = f(HR)
    3. Interpolate HR where a1 = 0.75 (HRVT1) and a1 = 0.50 (HRVT2)
    4. Validate: R² > 0.7, sufficient range (a1 from >1.0 to <0.75)

    Returns None if no valid ramp detected or insufficient quality.
    """
```

#### 4.6 calculate_ra() и calculate_da()

```python
def calculate_readiness_ra(
    dfa_timeseries: list[dict],
    baseline_pa: float,
    warmup_minutes: int = 15,
) -> dict | None:
    """Ra = (Pa_today - Pa_baseline) / Pa_baseline * 100.

    Pa = power/pace at stable DFA a1 during warmup.
    Ra > +5%: excellent, -5..+5%: normal, <-5%: under-recovered.
    """

def calculate_durability_da(
    dfa_timeseries: list[dict],
    min_duration_min: int = 40,
) -> dict | None:
    """Da = (Pa_second_half - Pa_first_half) / Pa_first_half * 100.

    Requires ≥40 min activity, steady-state (not intervals).
    Da > 0: excellent endurance, <-5%: fatigue, <-15%: overreached.
    """
```

### Шаг 5. Pipeline function — `process_activity_hrv()`

```python
async def process_activity_hrv(activity_id: str) -> str:
    """Full post-activity HRV pipeline.

    Returns processing_status: processed | no_rr_data | low_quality | too_short | error
    """
    # 1. Download FIT
    fit_bytes = await intervals.download_fit(activity_id)
    if not fit_bytes:
        return "no_rr_data"

    # 2. Extract RR
    rr_ms = extract_rr_intervals(fit_bytes)
    if len(rr_ms) < 300:  # < 5 min of data
        return "too_short"

    # 3. Artifact correction
    corrected = correct_rr_artifacts(rr_ms)
    if corrected["quality"] == "poor":  # >10% artifacts
        return "low_quality"

    # 4. DFA timeseries
    timeseries = calculate_dfa_timeseries(corrected["rr_corrected"])

    # 5. Thresholds (optional — needs ramp)
    thresholds = detect_hrv_thresholds(timeseries)

    # 6. Ra (needs pa_baseline from last 14 days)
    # 7. Da (needs ≥40 min)

    # 8. Save to activity_hrv
    return "processed"
```

### Шаг 6. Cron job — `process_fit_job()`

```python
async def process_fit_job(batch_size: int = 5) -> int:
    """Process FIT files for unanalyzed bike/run activities.

    Runs hourly at :45. Processes up to batch_size activities per run
    to avoid overloading Intervals.icu API.

    Logic:
    1. SELECT from activities WHERE type IN ('Ride','VirtualRide','Run','VirtualRun','TrailRun',...)
       AND id NOT IN (SELECT activity_id FROM activity_hrv)
       AND moving_time >= 900  (≥15 min)
       ORDER BY start_date_local DESC
       LIMIT batch_size
    2. For each: process_activity_hrv() → save result
    """
```

**batch_size=5** — не больше 5 FIT-файлов за раз. Intervals.icu API имеет rate limits. Одна активность = один запрос. При первом запуске (backfill) может потребоваться несколько часов для обработки всей истории.

### Шаг 7. Scheduler registration

```python
scheduler.add_job(
    process_fit_job,
    trigger="cron",
    hour="4-23",
    minute="*/5",
    id="process_fit",
)
```

> Примечание: изначально планировалось :45, в продакшене изменено на */5 для более быстрой обработки новых активностей.

### Шаг 8. Streams для HR/power привязки

DFA a1 сам по себе — число. Для threshold detection и Ra/Da нужно привязать к HR и power/pace в тот же момент. Два варианта:

**Вариант A: из FIT file** — Record messages содержат timestamp + HR + power + speed. Синхронизируем с RR по времени. Всё в одном файле.

**Вариант B: из Intervals.icu streams API** — `GET /activity/{id}/streams.csv` — HR, power, pace, time columns. Проще парсить, не нужен fitparse для этой части.

**Рекомендация: Вариант A** — один FIT содержит и RR, и Record messages. Один API call вместо двух.

### Шаг 9. MCP tools

Новый файл `mcp_server/tools/activity_hrv.py` — 3 tools:

```python
@mcp.tool()
async def get_activity_hrv(activity_id: str) -> dict:
    """Get DFA alpha 1 analysis for a specific activity.

    Returns DFA a1 summary (mean, warmup), quality metrics (artifact_pct, rr_count),
    detected thresholds (HRVT1/HRVT2 with HR/power/pace), Readiness (Ra %),
    Durability (Da %), and processing status.

    Only available for bike/run activities processed with chest strap HRM (BLE).
    Swim activities have no RR data.

    Args:
        activity_id: Intervals.icu activity ID (e.g. "i12345")
    """
    async with get_session() as session:
        row = await session.get(ActivityHrvRow, activity_id)

    if not row:
        return {"error": f"No HRV analysis for activity {activity_id}. Either not processed yet or not a bike/run activity."}

    return {
        "activity_id": activity_id,
        "date": row.date,
        "activity_type": row.activity_type,
        "processing_status": row.processing_status,
        "quality": {
            "hrv_quality": row.hrv_quality,
            "artifact_pct": row.artifact_pct,
            "rr_count": row.rr_count,
        },
        "dfa_a1": {
            "mean": row.dfa_a1_mean,
            "warmup": row.dfa_a1_warmup,
        },
        "thresholds": {
            "hrvt1_hr": row.hrvt1_hr,
            "hrvt1_power": row.hrvt1_power,
            "hrvt1_pace": row.hrvt1_pace,
            "hrvt2_hr": row.hrvt2_hr,
            "r_squared": row.threshold_r_squared,
            "confidence": row.threshold_confidence,
        } if row.hrvt1_hr else None,
        "readiness_ra": row.ra_pct,
        "durability_da": row.da_pct,
    }


@mcp.tool()
async def get_thresholds_history(sport: str = "", days_back: int = 90) -> dict:
    """Get HRVT1/HRVT2 threshold trend over recent activities.

    Tracks how aerobic (HRVT1, DFA a1=0.75) and anaerobic (HRVT2, DFA a1=0.50)
    thresholds change over time. Useful for monitoring fitness progression.

    Args:
        sport: Filter by sport: "bike" or "run". Empty = all.
        days_back: How many days to look back (default 90).
    """
    # Query activity_hrv WHERE hrvt1_hr IS NOT NULL, ordered by date
    # Return list of {date, activity_type, hrvt1_hr, hrvt1_power, hrvt2_hr, confidence}


@mcp.tool()
async def get_readiness_history(sport: str = "", days_back: int = 30) -> dict:
    """Get Readiness (Ra) trend over recent activities.

    Ra compares warmup power/pace at a fixed DFA a1 level against 14-day baseline.
    Ra > +5%: excellent readiness, -5..+5%: normal, < -5%: under-recovered.

    Args:
        sport: Filter by sport: "bike" or "run". Empty = all.
        days_back: How many days to look back (default 30).
    """
    # Query activity_hrv WHERE ra_pct IS NOT NULL, ordered by date
    # Return list of {date, activity_type, ra_pct, pa_today, status}
```

Регистрация в `mcp_server/server.py`:

```python
import mcp_server.tools.activity_hrv  # noqa: F401
```

Расширить `mcp_server/tools/activities.py` — `get_activities()`:
- Добавить LEFT JOIN на `activity_hrv` для каждой активности
- В ответ включить `has_hrv_analysis: bool` и `dfa_a1_mean: float | None`
- Claude видит, какие тренировки проанализированы

### Шаг 10. Post-activity Telegram уведомление

Сразу после успешной обработки FIT в `process_fit_job` — отправлять краткую сводку в Telegram.

**`bot/formatter.py` — `build_post_activity_message(activity_row, hrv_row)`:**

```
🚴 Ride 1h20m | TSS 85
DFA a1: 0.92 (warmup) → 0.68 (avg)
Ra: +3.2% ✅ нормальная готовность
HRVT1: 142 bpm / 180W
```

Логика:
- `process_fit_job` после `process_activity_hrv()` → `status == "processed"` → отправить
- Не отправлять для `no_rr_data`, `low_quality`, `too_short`
- Bot instance передаётся через `create_scheduler(bot=bot)` (уже есть паттерн)

### Шаг 11. Вечерний отчёт (Evening Report)

Cron job `evening_report_job` (21:00) — итог дня с учётом Level 1 + Level 2 данных.

**`bot/scheduler.py`:**

```python
scheduler.add_job(
    evening_report_job,
    trigger="cron",
    hour=settings.EVENING_REPORT_HOUR,  # default: 21
    minute=0,
    id="evening_report",
    kwargs={"bot": bot},
)
```

**Данные для вечернего отчёта:**
- Все тренировки за день (activities) + суммарный TSS
- ESS и Banister R(t) после дня
- DFA данные (Ra, Da) из activity_hrv — если были обработаны
- Прогноз на завтра: ожидаемый recovery score

**`bot/formatter.py` — `build_evening_message(wellness_row, activities, hrv_analyses)`:**

```
📊 Итог дня — 24 марта

Тренировки: 2 | TSS: 120
🚴 Ride 1h20m (TSS 85) — DFA Ra: +3.2%
🏃 Run 40m (TSS 35) — DFA Ra: -2.1%

Стресс: ESS 95.3 | Banister: 68%
Прогноз: recovery ~65%, умеренная нагрузка завтра
```

**Вариант с AI (опционально):**
- Аналогично утреннему — `EVENING_REPORT_PROMPT` в `ai/prompts.py`
- Claude получает дневные данные + DFA результаты → короткий анализ
- Одна дополнительная API call в день

**`config.py` — новые настройки:**

```python
EVENING_REPORT_HOUR: int = 21
EVENING_REPORT_MINUTE: int = 0
```

### Шаг 12. Обновление утреннего AI промпта

Добавить в `ai/prompts.py` → `MORNING_REPORT_PROMPT` блок «вчерашняя тренировка»:

```
Вчерашние тренировки:
{yesterday_activities}
DFA анализ:
- Ra: {ra_pct}% (готовность на разминке)
- Da: {da_pct}% (устойчивость)
- HRVT1: {hrvt1_hr} bpm / {hrvt1_power}W
```

Это даёт Claude контекст для утренней рекомендации: если вчера Da=-15%, утром стоит предложить лёгкую тренировку.

---

## Порядок реализации

```
Шаг 1-2 (таблицы + миграция)               ✅
    ↓
Шаг 3 (download_fit в API client)          ✅
    ↓
Шаг 4 (hrv_activity.py — ядро)             ✅
    ↓
Шаг 5 (pipeline function)                  ✅
    ↓
Шаг 6-7 (cron job + scheduler)             ✅
    ↓
Шаг 8 (HR/power привязка из FIT Records)   ✅
    ↓
Шаг 9 (MCP tools)                          ✅
    ↓
Шаг 10 (post-activity Telegram)            ✅
    ↓
Шаг 11 (evening report)                    ✅
    ↓
Шаг 12 (утренний промпт + DFA данные)      ✅
```

**Все 12 шагов реализованы.** Pipeline полностью интегрирован в Telegram и AI промпты.

---

## Edge cases

1. **Нет RR в FIT** — запястный датчик, забыл надеть пояс → `no_rr_data`
2. **artifact_pct > 10%** — плохой контакт, потоотделение → `low_quality`, не использовать
3. **Активность < 15 мин** — разминка, короткий заплыв → `too_short`, пропустить
4. **Нет ramp segment** — ровная Z2 тренировка → thresholds = None (нормально, Ra/Da всё равно считаются)
5. **Indoor vs outdoor** — indoor (trainer) стабильнее для thresholds (нет ветра/рельефа)
6. **Первые 2 недели** — нет Pa baseline → Ra = None, только DFA summary
7. **Swim activities** — тип Swim/OpenWaterSwim → skip (нет RR в воде)
8. **FIT 404** — Strava-imported activity без оригинала → `no_rr_data`

---

## Тесты

### Unit tests (test_hrv_activity.py)

- `test_extract_rr_valid_fit` — парсинг реального FIT с RR
- `test_extract_rr_no_hrv_messages` — FIT без HRV → пустой list
- `test_artifact_correction_clean` — чистый сигнал → artifact_pct ≈ 0
- `test_artifact_correction_noisy` — 15% артефактов → quality = "poor"
- `test_dfa_alpha1_resting` — синтетические RR (resting) → a1 > 1.0
- `test_dfa_alpha1_exercise` — синтетические RR (high HR) → a1 < 0.75
- `test_threshold_detection_ramp` — monotonic HR increase → HRVT1 detected
- `test_threshold_detection_steady` — flat HR → None

### Integration test

- Скачать реальный FIT через API → полный pipeline → verify activity_hrv row

---

## Зависимости от существующего кода

| Зависимость | Файл | Статус |
|---|---|---|
| ActivityRow (id, type, moving_time) | data/database.py | ✅ |
| sync_activities_job (activities в БД) | bot/scheduler.py | ✅ |
| IntervalsClient (API access) | data/intervals_client.py | ✅ |
| SPORT_MAP (type → canonical) | data/utils.py | ✅ |
| settings (ATHLETE_*) | config.py | ✅ |
| Alembic migrations | migrations/ | ✅ |

---

## Ссылки и теория

Полная теоретическая база (DFA алгоритм, физиологическое обоснование, Ra/Da формулы, литература) — в [docs/knowledge/dfa-alpha1.md](knowledge/dfa-alpha1.md).

Intervals.icu API: `GET /activity/{id}/file`
