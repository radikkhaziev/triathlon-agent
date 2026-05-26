# Post-Workout AI Report Spec

> AI-комментарий к завершённой тренировке: сравнение с похожими сессиями, ключевые маркеры, прогресс к гонке. Генерируется Claude через MCP сразу после завершения FIT-pipeline и отправляется отдельным TG-сообщением (или редактирует существующее уведомление). Персистится в `activities.ai_recommendation`.

**Related:**

| Issue / Spec | Связь |
|---|---|
| `tasks/actors/activities.py:_actor_send_activity_notification` | Текущее детерминированное уведомление |
| `tasks/actors/activities.py:actor_update_activity_details` | Активити-pipeline (FIT → DFA → notification) |
| `tasks/actors/reports.py:actor_compose_user_morning_report` | Прецедент: sentinel + Claude+MCP + persist |
| `tasks/tools.py:MCPTool.generate_morning_report_via_mcp` | Прецедент: sync Claude API + tool loop |
| `mcp_server/tools/compliance.py:get_workout_compliance` | Существующий MCP tool, переиспользуется |
| `mcp_server/tools/progression.py:get_efficiency_trend` | Существующий MCP tool, переиспользуется |
| `bot/prompts.py:get_static_system_prompt` + `render_athlete_block` | Промпт-кэш паттерн (две `cache_control` секции) |
| `docs/USER_CONTEXT_SPEC.md` | Long-term facts в системном промпте |
| `docs/RACE_PLAN_SPEC.md` | Прецедент: structured AI-генерация с persist в БД |
| `docs/MULTI_TENANT_SECURITY_SPEC.md` T1 | `user_id`-scoped reads — все tools берут user_id из contextvars |

---

## 1. Мотивация

Сейчас post-workout уведомление в Telegram (`build_post_activity_message`) — детерминированный шаблон: эмодзи спорта + длительность + TSS, плюс блоки DFA a1 / Ra / HRVT1 / Da когда они есть. Это полезный «дашборд», но он не отвечает на главные вопросы атлета сразу после тренировки:

- **«Как это было относительно прошлых таких же сессий?»** Видишь decoupling 3.2% — это много или мало? Без сравнения с историей цифра мёртвая.
- **«Виден ли прогресс к гонке?»** На горизонте 70.3 хочется видеть «pace при том же HR падает на N sec/km за месяц», а не разовые числа.
- **«Что это значит для плана?»** Стоит ли пересчитать зоны? Стоит ли скорректировать следующую сессию?

Утренний отчёт (`actor_compose_user_morning_report`) этих вопросов не закрывает — он отвечает на «как день начинается», а не «как тренировка прошла». Окно «сразу после тренировки» уникально: ощущения свежие, RPE логируется в тех же кнопках, конкретная сессия имеет очерченные границы, и можно сразу предложить action (update zones, отметить PR, обсудить выпавший compliance).

У нас уже есть все вводные:
- `activities` + `activity_details` + `activity_hrv` — текущая сессия со всеми маркерами
- `get_efficiency_trend` — последние 5 похожих, медиана decoupling
- `get_workout_compliance` — план vs факт
- `get_thresholds_history` — drift зон
- `get_race_projection` — прогноз времени на целевую дистанцию (ML)
- `get_training_load` + `predict_ctl` — где это на decay-кривой к гонке
- `get_personal_patterns` — индивидуальные ритмы восстановления
- `athlete_goals` — целевая race + целевое время

Остаётся вызвать Claude с правильным набором tools и сохранить итог.

---

## 2. Scope

### Phase 1 (MVP) — backend + webapp + push с callback

**Backend:**
- Колонки `activities.ai_recommendation` (Text, nullable) + `activities.ai_recommendation_generated_at` (TIMESTAMPTZ, nullable) + миграция.
- Новый Dramatiq actor `actor_compose_post_workout_report(user, activity_id)`.
- Sentinel-pattern (как в morning report): `__generating__:<unix_ts>`, 10-min stale-retry.
- **Два детерминированных MCP tools:** `find_similar_activities(activity_id, n=5)` и `get_workout_progress_summary(activity_id, weeks_back=8)` — Claude не должен сам склеивать sparkline'ы из 3 round-trip'ов и делать LLM math, это даёт ошибки.
- Отдельный prompt builder `bot/prompts.py:get_static_system_prompt_post_workout` + динамический user-prompt с явно перечисленными «уже показанными в дашборде» цифрами + tool-список `POST_WORKOUT_TOOLS` в `tasks/tools.py`.
- Метод `MCPTool.generate_post_workout_report_via_mcp(activity_id)`.
- Триггер: после успешного завершения `_actor_update_analityc_tables` в pipeline `actor_update_activity_details`.
- **Двухуровневая фильтрация** (см. §4): cost-gate (always-persist) vs signal-gate (push-to-TG-only). Persist всегда, если activity свежий и тип Run/Ride. TG-сообщение только если есть «interesting signal».
- Defensive validator с forbidden-phrases (молодец/отлично/well done/keep going) — фейл блокирует push, не блокирует persist.
- Force-regenerate endpoint `POST /api/jobs/post-workout-report/{activity_id}?force=true` (owner-only) — для отладки промпта.
- Фича-флаг `POST_WORKOUT_AI_ENABLED` (env, default `false`) + allowlist `user_id=1` для первого выкатывания.

**Webapp (primary surface):**
- Блок «AI-разбор» на странице `/activity/:id` с рендером `activity.ai_recommendation` + meta (timestamp, model_version если будет).
- Inline-таблица «Похожие сессии» из `find_similar_activities` — это и есть «другие данные» в callback'е.
- Sparkline-блок из `get_workout_progress_summary` (decoupling history, EF history, race projection delta).
- Sentinel-aware UI: `__generating__` → spinner с текстом «Готовим разбор», NULL → «Разбор для этой сессии не сгенерирован» (с owner-кнопкой «Перегенерировать»).
- Кнопка «🔁 Перегенерировать» — owner only в Phase 1, rate-limit 1/час/activity.

**Telegram (push, не primary):**
- TG-сообщение **с inline-кнопкой** `📊 Открыть разбор` → WebApp deep-link `/activity/:id`. Это и есть основная UX-связка: push атлета на webapp, не пытаться уместить разбор в TG.
- Текст в TG короткий — заголовок-«крючок» (1-2 строки с самым интересным сигналом) + кнопка. Полный разбор живёт в webapp.
- Существующий детерминированный дашборд (`build_post_activity_message`) остаётся как есть, отдельным сообщением — мгновенно после загрузки активности.

### Phase 2 — UX-polish & sport coverage

- Sport-specific промпт-секции: long Z2 → акцент на decoupling/EF, tempo/threshold → акцент на pace-at-HR, intervals → акцент на VI/repeatability.
- Swim coverage: лёгкий вариант разбора без DFA (есть только pace + HR + compliance).
- Inline-кнопка `🔄 Обновить зоны` в TG если signal-gate сработал на drift detected — переиспользовать существующий `update_zones` callback.
- Auto-rename signal-gate triggers («interesting» → разные тексты-заголовки в TG: `📈 Decoupling упал на N%`, `⚡ Drift detected`, `🏆 PR на отрезке`).

### Phase 3 — feedback loop

- Колонка `activity.user_feedback` (👍/👎 + freetext) + кнопки в TG → собираются в датасет для оценки качества разборов.
- A/B промптов через `model_version` колонку (как у `race_plans.model_version`) — позволяет таргетно регенерировать stale rows.
- Аналитика: «AI-разбор за 30d покрыл X% активностей, Y% получили 👍, средняя длина = Z токенов» как админ-метрика.
- Вписать сигналы из разборов в weekly report («за неделю decoupling упал с 4.5% до 3.2% — durability растёт»).

### Вне scope

- Реал-тайм комментарий **во время** тренировки.
- Подмена `build_post_activity_message` — детерминированный «дашборд» остаётся, AI-разбор его дополняет, не заменяет.
- AI-комментарий для активностей не-Run/Ride (Swim, WeightTraining, Walk) в Phase 1 — нет данных DFA/decoupling, ROI низкий. Phase 2 добавит лёгкий вариант для Swim (compliance + pace trend).
- Обучение собственной модели поверх корпуса разборов — слишком рано.

---

## 3. Data model

### Колонки `activities.ai_recommendation` + `ai_recommendation_generated_at` + `ai_signal_reason`

```python
# data/db/activity.py:Activity
ai_recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
ai_recommendation_generated_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True), nullable=True
)
ai_signal_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
```

**Миграция** (`migrations/versions/xxxx_add_activities_ai_recommendation.py`):

```python
def upgrade() -> None:
    op.add_column("activities", sa.Column("ai_recommendation", sa.Text(), nullable=True))
    op.add_column(
        "activities",
        sa.Column("ai_recommendation_generated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("activities", sa.Column("ai_signal_reason", sa.String(40), nullable=True))
    op.create_index(
        "ix_activities_user_signal_reason_date",
        "activities",
        ["user_id", "ai_signal_reason", "start_date_local"],
        postgresql_where=sa.text("ai_signal_reason IS NOT NULL"),
    )

def downgrade() -> None:
    op.drop_index("ix_activities_user_signal_reason_date", table_name="activities")
    op.drop_column("activities", "ai_signal_reason")
    op.drop_column("activities", "ai_recommendation_generated_at")
    op.drop_column("activities", "ai_recommendation")
```

**Решения по схеме:**

- Хранить готовый текст (Text, не JSONB). Унификация с `wellness.ai_recommendation`. Если Phase 2 потребует структурированного payload (для webapp-карточки), мигрируем к JSONB и прокинем `model_version`.
- `ai_recommendation_generated_at` добавляем **сейчас**, не в Phase 3 — поле бесплатное в миграции, но позволяет webapp'у показывать «разбор сгенерирован 2 мин назад / 3 дня назад», а Phase 3 (force-regen, A/B промптов) обойдётся без миграции.
- `ai_signal_reason` (VARCHAR 40) хранит триггер signal-gate'а: `drift_detected` / `pr_detected` / `rpe_mismatch` / `decoupling_outlier` / `compliance_miss` / `race_imminent`. NULL = signal-gate не сработал (silent сессия) или legacy row. Нужен для duplicate-reason suppression (§4) — без structured column нельзя написать запрос «был ли тот же reason за последние 7 дней». 40 chars хватает с запасом для всех текущих и предвидимых reason-имён. Partial index на `(user_id, ai_signal_reason, start_date_local) WHERE ai_signal_reason IS NOT NULL` — cooldown query летает на ~10-20 rows per user, full-table scan не нужен.
- Sentinel `"__generating__:<unix_ts>"` — тот же паттерн, что и в morning report (`tasks/actors/reports.py:244`). 10-мин stale-retry.
- TTL не нужен — отчёт привязан к конкретной сессии и остаётся релевантным навсегда.
- Отдельная таблица не нужна (1:1 с activity, нет истории версий в Phase 1). В Phase 3 при добавлении `model_version` + `user_feedback` можно расширить ту же строку или вынести в `activity_ai_reports`.

### Новый MCP tool `find_similar_activities`

`mcp_server/tools/activities.py:find_similar_activities`:

```python
@mcp.tool()
async def find_similar_activities(
    activity_id: str,
    n: int = 5,
    duration_tolerance_pct: float = 20.0,
    tsb_bucket: bool = True,
) -> dict:
    """Find historical activities similar to the given one for trend comparison.

    Match criteria (all required unless noted):
    - Same sport (`activity.type`)
    - Duration within ±duration_tolerance_pct (default 20%)
    - Same intensity_factor bucket (recovery <0.65 / endurance 0.65-0.75 /
      tempo 0.75-0.88 / threshold 0.88-1.00 / vo2 >1.00) — primary anchor:
      Z2 long and Z2 recovery share dominant_zone but live in different IF buckets.
    - Same dominant HR zone, where dominant requires ``share ≥ 50%`` of
      `hr_zone_times`. If no zone reaches 50% the reference is tagged
      ``dominant_zone="MIXED"`` and zone-matching is **disabled** for the
      query (IF-bucket alone is the anchor); response surfaces the MIXED
      flag so callers can mark the report baseline-only.
    - When `tsb_bucket=True`: same TSB bucket on workout day
      (low <-15 / mid -15..+5 / high >+5)
    - Excludes the activity itself
    - Window: last 90 days, sorted by date descending, capped at `n`

    Returns:
        {
          "reference": { ... },
          "dominant_zone": "Z2" | "MIXED",
          "dominant_zone_share": 0.62,
          "if_bucket": "endurance",
          "matches": [ ... up to n ... ],   # side-by-side: avg HR, avg power/pace,
                                            # decoupling, EF, VI, DFA a1
        }
    """
```

Логика реализуется детерминированно (не Claude'у решать «что считать похожим») и отдаёт уже готовую таблицу для сравнения. Это:

1. Уменьшает галлюцинации (Claude не придумывает «похожих» из общих фраз).
2. Делает поведение тестируемым (deterministic input → deterministic output).
3. Переиспользуется в weekly report и race-prep.

**Алгоритм match'а** в порядке убывания строгости:

```
sport = activity.type
duration = activity.moving_time
duration_min = duration * (1 - tolerance)
duration_max = duration * (1 + tolerance)

# Dominant zone with share threshold — иначе 35/30/25 распределение
# вернёт Z2 на tempo-сессии и сравнения становятся мусором.
zone_times = activity_details.hr_zone_times  # {"Z1": 120, "Z2": 1800, ...}
total = sum(zone_times.values())
top_zone, top_secs = max(zone_times.items(), key=lambda kv: kv[1])
dominant_zone_share = top_secs / total
if dominant_zone_share >= 0.50:
    dominant_zone = top_zone
else:
    dominant_zone = "MIXED"   # сравнение по zone'е отключается, остаётся IF-bucket

# Intensity factor bucket — Z2 long (IF 0.65) и Z2 recovery (IF 0.55)
# попадут в один dominant_zone, но это разные тренировки. Bucket'ы:
#   recovery (<0.65) / endurance (0.65-0.75) / tempo (0.75-0.88) /
#   threshold (0.88-1.00) / vo2 (>1.00)
#
# Boundary tolerance: если IF в пределах ±0.03 от границы бакета, кандидаты
# из соседнего бакета тоже принимаются. Boundary-cliff matters: Z2-план,
# выполненный на IF 0.62 («чуть-чуть не дотянул до endurance»), не должен
# терять все матчи в endurance-бакете на ровном месте. Bucket'ы — coarse
# anchors, не законы. Empirically выявлено в demo 2026-05-14 (i144736371 —
# Endurance Z2 ×120 мин при IF 0.62 → recovery bucket → 0 матчей за 90 дней,
# хотя ближайший 90-мин ride 25.04 с IF 0.62 и EF 0.98 — очевидный кандидат).
if_bucket = bucket(activity.intensity_factor)
near_boundary_buckets = adjacent_buckets_within(activity.intensity_factor, tol=0.03)

candidates = Activity
  .where(user_id, type==sport, is_race==False)
  .where(moving_time BETWEEN duration_min AND duration_max)
  .where(start_date_local < activity.start_date_local)
  .where(start_date_local >= activity.start_date_local - 90 days)

# Filter post-fetch (zone/IF/TSB requires details + wellness join):
filtered = [
    c for c in candidates
    if bucket(c.intensity_factor) in ({if_bucket} | near_boundary_buckets)
]

# Bursty intensity sessions (tempo+ buckets) — strength / threshold / VO2 —
# by their nature накапливают много Z1 в recovery между сетами, что искажает
# dominant_zone и часто загоняет history-кандидатов в MIXED. Для этих бакетов
# IF-bucket остаётся единственным якорем match'а; zone-фильтр отключаем.
# Empirically выявлено в demo 2026-05-14 (i143572460 — strength 45 мин,
# HR 74% в Z1 из-за recovery между Z5-спайками; оба кандидата из tempo-IF
# попали в MIXED по distribution 28/40/14/18 и были вырезаны фильтром).
ZONE_MATCH_DISABLED_BUCKETS = {"tempo", "threshold", "vo2"}
if dominant_zone != "MIXED" and if_bucket not in ZONE_MATCH_DISABLED_BUCKETS:
    filtered = [
        c for c in filtered
        if compute_dominant(c)[0] == dominant_zone
        and compute_dominant(c)[1] >= 0.50
    ]
if tsb_bucket:
    today_tsb_bucket = bucket_tsb(wellness_on(activity.date).tsb)
    filtered = [c for c in filtered if bucket_tsb(wellness_on(c.date).tsb) == today_tsb_bucket]

return {
    "reference": ...,
    "dominant_zone": dominant_zone,         # may be "MIXED"
    "dominant_zone_share": round(dominant_zone_share, 2),
    "if_bucket": if_bucket,
    "matches": filtered[:n],
}
```

`bucket_tsb(tsb)` — `low` (<-15) / `mid` (-15..+5) / `high` (>+5).
`if_bucket` указанный выше.

**Если `dominant_zone == "MIXED"`** — возвращаем результат с пометкой, Claude в промпте инструктирован пометить разбор как «неклассифицированная сессия, baseline-only» и не делать сравнений по зонам. IF-bucket остаётся надёжным якорем match'а.

### Новый MCP tool `get_workout_progress_summary` (Phase 1)

Свёртка истории в один полезный объект для промпта:

```python
@mcp.tool()
async def get_workout_progress_summary(
    activity_id: str,
    weeks_back: int = 8,
) -> dict:
    """Summarize key trends for activities of the same sport+IF-bucket as the reference one.

    Returns deterministic sparkline-style series (no LLM math required):
    - decoupling_history: list of (week_start_date, median_pct) for last N weeks
    - efficiency_factor_history: same shape
    - hr_at_pace_history: HR at the reference activity's avg pace, week-by-week
      (skipped for Ride — uses power_at_hr instead)
    - threshold_drift: { current_lthr, measured_lthr_p7d, current_ftp, measured_ftp_p7d }
    - race_projection_delta: { sport, distance, prediction_now, prediction_4w_ago, delta_sec }
      from get_race_projection (ML), only when active RACE_A goal exists
    - sample_size: count of source activities in the window
    """
```

**Почему в Phase 1, а не отрезать через комбинацию tools:** если Claude собирает sparkline руками из `get_efficiency_trend` + `get_thresholds_history` + `get_race_projection`, он добавит ошибки на склейке (разные окна, разные единицы, путает медиану и среднее). Один детерминированный tool с готовым свёрнутым объектом — дешевле (1 round-trip вместо 3) и надёжнее (нет LLM-математики). Тесты покрывают именно склейку, а не текст.

---

## 4. Pipeline integration

Текущий pipeline в `actor_update_activity_details`:

```python
pipeline([
    _actor_download_fit_file,
    _actor_process_fit_file,
    _actor_post_process_fit_file,
    _actor_update_analityc_tables,
    _actor_send_activity_notification,   # ← deterministic dashboard, остаётся
]).run()
actor_after_activity_update.send(...)
```

После Phase 1 становится:

```python
pipeline([
    _actor_download_fit_file,
    _actor_process_fit_file,
    _actor_post_process_fit_file,
    _actor_update_analityc_tables,
    _actor_send_activity_notification,
]).run()
actor_after_activity_update.send(...)
actor_compose_post_workout_report.send(user=user, activity_id=activity_id)  # ← новое
```

`actor_compose_post_workout_report` запускается **параллельно** с `actor_after_activity_update`, не блокирует pipeline. Это важно: генерация может занять 30-120с, а детерминированный «дашборд» уже улетел в TG моментально.

### Двухуровневая фильтрация: cost-gate vs signal-gate

**Cost-gate** (всегда применяется, до Claude-вызова) — отсекает то, что не имеет смысла даже хранить:

1. `POST_WORKOUT_AI_ENABLED` env-флаг (default false).
2. `user.id in POST_WORKOUT_AI_ALLOWLIST` (Phase 1).
3. **Backfill detection:** `(now() - activity.last_synced_at) > BACKFILL_AGE_THRESHOLD` (default 24h) → skip. **Не** `activity.start_date_local == local_today()` — это ломается на late-evening session, чей FIT доходит после полуночи (pipeline видит вчерашнюю дату и отбрасывает свежий разбор). `last_synced_at` ставится в `Activity.save_bulk` (`data/db/activity.py:96`), `now() - last_synced_at < 24h` — single source of truth для «свежей» активности.
4. `activity.icu_training_load is not None and >= MIN_TSS` (default 15) — не разбираем разминки и 5-минутные walks.
5. `activity.type in {"Run", "Ride"}` (Phase 1; Swim добавим в Phase 2).
6. `activity.is_race=False` — race-effort идёт через отдельный пост-race flow (Phase 3, отдельная спека).
7. `_is_ramp_test_activity(...)` — ramp-tests уже имеют свой rich-notification (`build_ramp_test_message`), не генерим параллельно.
8. Sentinel claim: `activity.ai_recommendation IS NULL OR ai_recommendation NOT LIKE '__generating__:%' OR (sentinel_age > 600s)`.

Если cost-gate проходит → переходим к **signal-gate (deterministic, ДО Claude-вызова)**. Без явного сигнала разбор **не генерируется**: storage + API tokens + alert-fatigue не стоят filler-комментариев типа «Z2 60 мин, ничего особенного». Webapp при `ai_recommendation IS NULL` рендерит плейсхолдер «без AI-комментария» (см. §8). По coverage-валидации 2026-05-25 (см. §13) ~60-70% сессий идут без AI-блока — это **by design**, а не баг.

> **Decision 2026-05-14 (demo-driven):** прежняя схема «persist-always, push-if-signal» давала water — Claude каждый раз заполнял 5 строк template'ом (verdict / observations / race-line / action item), потому что обязан был. Перенос signal-gate ДО генерации убирает обязательность вывода и превращает разбор в реакцию на конкретный триггер, а не пересказ сессии.

**Signal-gate** (deterministic, до Claude-вызова) — определяет (a) ЕСТЬ ли что комментировать и (b) КАКОЙ сигнал служит anchor'ом для промпта. Возвращает `SignalResult(passed, reason, evidence)`:

```python
@dataclass
class SignalResult:
    """Output of signal-gate. `reason` names the anchor; `evidence` is JSON-
    serializable and gets embedded into the user prompt verbatim — the more
    specific the evidence, the better the anchored generation."""
    passed: bool
    reason: str | None  # "drift_detected" / "pr_detected" / "rpe_mismatch" /
                        # "decoupling_outlier" / "compliance_miss" /
                        # "race_imminent" / None
    evidence: dict      # {"today": ..., "history_median": ..., "delta": ...}


def evaluate_signal_gate(activity_id: str, user: UserDTO) -> SignalResult:
    """Decide if this activity warrants an AI report and what to anchor on.
    Deterministic — no Claude call. Returns the FIRST matching trigger;
    precedence is intentional (drift > pr > rpe > decoupling > compliance > race) —
    higher signals override lower ones as the prompt anchor."""
    activity = Activity.get(activity_id)
    detail = ActivityDetail.get(activity_id)
    similar = find_similar_activities(activity_id, n=5)
    compliance = get_workout_compliance(activity_id)
    drift = get_threshold_drift(user.id)

    # 1. Threshold drift — strongest signal (alerts are vetted upstream)
    if drift and drift.alerts:
        return SignalResult(True, "drift_detected", {"alerts": drift.alerts})

    # 2. PR — has ACTIVITY_ACHIEVEMENTS row for this activity
    if achievements := activity_achievements_for(activity.id):
        return SignalResult(True, "pr_detected", {"achievements": achievements})

    # 3. RPE drift vs last-5 same-bucket median (Δ ≥ 3)
    # Δ-based catches «subjective effort несвойственно низкий/высокий» — empirically
    # выявлено в demo 2026-05-14 (i148059070 — RPE 2 при IF 0.77 на threshold session;
    # старое правило «RPE ≤ 3 при IF > 0.85» этого не ловило, хотя сигнал реальный).
    if activity.rpe is not None:
        history_rpe = [m.rpe for m in similar.matches if m.rpe is not None]
        if len(history_rpe) >= 3:
            median_rpe = statistics.median(history_rpe)
            if abs(activity.rpe - median_rpe) >= 3:
                return SignalResult(
                    True, "rpe_mismatch",
                    {"today": activity.rpe, "history_median": median_rpe,
                     "delta": activity.rpe - median_rpe},
                )
        elif activity.intensity_factor is not None:
            # Cold-start fallback when history too thin for Δ-based test
            if (activity.rpe >= 8 and activity.intensity_factor < 0.75) or \
               (activity.rpe <= 3 and activity.intensity_factor > 0.85):
                return SignalResult(
                    True, "rpe_mismatch",
                    {"today": activity.rpe, "if": activity.intensity_factor,
                     "delta": None},  # absolute fallback, no median yet
                )

    # 4. Decoupling outlier vs last-5 median — validity-gated on BOTH sides
    # AND out-of-green-zone (see below).
    #
    # Validity (see §3 + data/metrics.py:is_valid_for_decoupling: VI ≤ 1.10,
    # ≥70% Z1+Z2, bike ≥ 60min, run ≥ 45min, swim excluded). На коротком
    # интервальном ride decoupling 15% — артефакт HR-задержки на rest/work
    # циклах, не сигнал. Тот же фильтр на history-кандидатов: невалидных
    # исключаем из медианы, иначе она сама смещается шумом коротких ride'ов.
    # Empirically выявлено в demo 2026-05-14 (i148059070 — 36-мин threshold
    # intervals: decoupling 15.3% vs медиана 3.2% → false-positive push
    # без фильтра).
    #
    # Out-of-green-zone (`max(|today|, |median|) ≥ 5.0`): абсолютный Δ-порог
    # 1.5pp ловит как настоящие сигналы (Z2 ride 11% при усталости vs медиана
    # 2%), так и движение внутри «зелёной» зоны durability (recovery ride
    # 0.2% vs медиана 3.3% — оба значения нормальные, разница — шум VI/EF/
    # температуры). Без этого фильтра ~50% decoupling-сигналов на текущих
    # данных user_id=1 (за 30d, май 2026) приходят на сессии, где и reference,
    # и median лежат в green zone <5%. Спека держит «silence beats filler»
    # (Decision 2026-05-14) — green-zone-фильтр это контракт по decoupling-
    # каналу. Empirically выявлено coverage-валидацией 2026-05-25
    # (i147431143 — Ride 60 мин recovery, dec 0.2% vs medianа 3.3%, Δ=−3.1pp:
    # формально trigger, содержательно — chill Tuesday spin).
    if (
        similar.matches
        and detail.decoupling_pct is not None
        and is_valid_for_decoupling(activity, detail)
    ):
        valid_history = [
            m for m in similar.matches
            if m.decoupling_pct is not None
            and is_valid_for_decoupling(m.activity, m.detail)
        ]
        if valid_history:
            median = statistics.median(m.decoupling_pct for m in valid_history)
            if (
                abs(detail.decoupling_pct - median) >= 1.5
                and max(abs(detail.decoupling_pct), abs(median)) >= 5.0
            ):
                return SignalResult(
                    True, "decoupling_outlier",
                    {"today_pct": detail.decoupling_pct,
                     "history_median_pct": median,
                     "history_dates": [m.activity.start_date_local
                                       for m in valid_history[:3]],
                     "delta_pct": detail.decoupling_pct - median},
                )

    # 5. Compliance miss (duration or intensity)
    if compliance:
        if abs(compliance.duration_pct - 100) > 10:
            return SignalResult(
                True, "compliance_miss",
                {"kind": "duration", "duration_pct": compliance.duration_pct,
                 "plan_minutes": compliance.plan_duration_min},
            )
        if compliance.intensity_drift_z_count >= 1:
            return SignalResult(
                True, "compliance_miss",
                {"kind": "intensity",
                 "drift_z_count": compliance.intensity_drift_z_count,
                 "target_zone": compliance.target_zone,
                 "actual_zone": compliance.actual_zone},
            )

    # 6. Race imminent — generic context anchor, lowest priority. Outside the
    # 12-week window race-line is filler (CTL gap is months-stable, not news),
    # внутри — даже рутинная сессия читается в race-терминах.
    if (race := active_race_within_days(user.id, days=84)) is not None:
        return SignalResult(
            True, "race_imminent",
            {"race_name": race.event_name, "days_to_race": race.days_to_race,
             "ctl_gap": race.ctl_target - current_ctl(user.id)},
        )

    return SignalResult(False, None, {})
```

### Duplicate-reason suppression (cooldown wrapper)

`evaluate_signal_gate` отвечает только на вопрос «есть ли anchor signal?». Решение «стоит ли surface'ить его атлету сейчас» отделено в `_apply_cooldown_filter` — это сознательное разделение concerns: signal-detection остаётся pure, фильтрация анти-спама живёт рядом и легко тюнится.

```python
COOLDOWN_DAYS_BY_REASON = {
    "drift_detected":     14,   # threshold change happens slowly, weekly cadence noisy
    "pr_detected":         0,   # every PR is independently newsworthy — no cooldown
    "rpe_mismatch":        7,
    "decoupling_outlier":  7,
    "compliance_miss":     7,
    "race_imminent":       7,
}

def _apply_cooldown_filter(signal: SignalResult, user_id: int) -> SignalResult:
    """Suppress repeat reasons over a per-reason cooldown window.

    Empirically выявлено на user_id=62 (coverage validation 2026-05-25):
    5 rpe_mismatch cold-start триггеров за 30 дней — все об одном факте
    (атлет систематически ставит RPE=1 на high-IF run'ах). Без cooldown
    бот шлёт 5 идентичных разборов «почему RPE 1 при IF 1.2», атлет
    свайпает и перестаёт открывать. То же возможно для compliance_miss
    (стабильно перерабатывает план) и decoupling_outlier (durability
    деградировала и держится).

    `pr_detected` обходит cooldown: каждый PR — отдельная новость,
    подавлять было бы странно. `drift_detected` — 14 дней (threshold
    change — медленный процесс, push 2x за неделю = шум).
    """
    if not signal.passed:
        return signal
    cooldown_days = COOLDOWN_DAYS_BY_REASON.get(signal.reason, 7)
    if cooldown_days == 0:
        return signal
    cutoff = local_today() - timedelta(days=cooldown_days)
    recent = Activity.exists_with_signal_reason(
        user_id=user_id,
        reason=signal.reason,
        since=cutoff,
    )
    if recent:
        # Suppressed — actor проставит ai_signal_reason=NULL и закроет sentinel.
        # Возвращаем passed=False с диагностикой в evidence для метрик.
        return SignalResult(
            False, None,
            {"suppressed_reason": signal.reason,
             "cooldown_days": cooldown_days},
        )
    return signal
```

`Activity.exists_with_signal_reason` — тривиальный `@dual` хелпер на ORM (`SELECT 1 FROM activities WHERE user_id=? AND ai_signal_reason=? AND start_date_local >= ? LIMIT 1`). Partial index `ix_activities_user_signal_reason_date` (см. §3) делает запрос O(log n) на ~10-20 rows.

**Если signal.passed == False** → actor завершает БЕЗ Claude-вызова. `ai_recommendation` и `ai_signal_reason` остаются `NULL`, webapp на `/activity/:id` рендерит «без AI-комментария» (см. §8). Никакого filler-разбора «Z2 60 мин, ничего особенного» в БД не лежит — пустота лучше воды. Suppressed-by-cooldown сессии видимы в логах через `evidence.suppressed_reason`, но в БД от них следов нет — это намеренно (idempotent retry той же activity не должен внезапно проснуться через 8 дней).

**Если passed == True** → `signal.reason` + `signal.evidence` пробрасываются в user prompt как **anchor** (см. §5). Claude обязан остаться на этом сигнале и не растекаться по сессии. Push в TG attempted после persist'а и валидатора. `ai_signal_reason` персистится вместе с текстом — это и есть «отметка» для следующего cooldown-check'а.

**Метрики для тюнинга порогов** (Sentry breadcrumb или actor-метрика):
- `post_workout.gate.signal_passed_total{reason}` — какие триггеры срабатывают
- `post_workout.gate.signal_suppressed_total{reason}` — какие триггеры гасит cooldown (важно отличать от «не было сигнала»)
- `post_workout.gate.signal_skipped_total` — сколько сессий ушло в пустоту без триггера
- `post_workout.report.length_lines_histogram` — фактическое распределение длины (цель: median 2-3, max 5)

Через 2-3 недели после выкатки можно подтянуть пороги (decoupling Δ, RPE Δ, race window, cooldown days) на реальных данных. Cooldown days — главный кандидат на тюнинг: суппресс отдельной метрикой даст ответ, **достаточно ли** 7 дней или паттерны (как user_id=62 с RPE) требуют 14.

### Структура actor'а

```python
@dramatiq.actor(queue_name="default", time_limit=180_000)  # 3 min
@validate_call
def actor_compose_post_workout_report(user: UserDTO, activity_id: str):
    # ── Step 1: cost-gate + sentinel claim (short lock)
    with get_sync_session() as s:
        a = s.execute(
            select(Activity).where(Activity.id == activity_id).with_for_update()
        ).scalar_one_or_none()
        if not a or not _passes_cost_gate(a, user):
            return
        if a.ai_recommendation:
            if a.ai_recommendation.startswith("__generating__"):
                ts = float(a.ai_recommendation.split(":", 1)[1])
                if time.time() - ts < 600:
                    return  # someone else is generating
            else:
                return  # already evaluated, idempotent (text OR NULL stays as-is)
        a.ai_recommendation = f"__generating__:{time.time():.0f}"
        s.commit()

    # ── Step 2: signal-gate (deterministic, NO Claude call)
    # Loads similar/compliance/drift/achievements and decides if there's an anchor.
    # If no signal — clear sentinel back to NULL and return. Webapp will render
    # «без AI-комментария» placeholder. This is the path for ~60-70% of sessions
    # on real data (coverage validation 2026-05-25 — see §13).
    try:
        signal = evaluate_signal_gate(activity_id, user)
        signal = _apply_cooldown_filter(signal, user.id)  # duplicate-reason suppression
    except Exception as e:
        sentry_sdk.capture_exception(e)
        _clear_post_workout_sentinel(activity_id)
        return

    logger.info(
        "post_workout signal-gate user_id=%d activity_id=%s passed=%s reason=%s suppressed=%s",
        user.id, activity_id, signal.passed, signal.reason,
        signal.evidence.get("suppressed_reason") if not signal.passed else None,
    )

    if not signal.passed:
        _clear_post_workout_sentinel(activity_id)  # NULL → webapp shows placeholder
        return

    # ── Step 3: generate via Claude+MCP, ANCHORED on signal — 30-120s
    with get_sync_session() as s:
        u_orm = s.get(User, user.id)
    if u_orm is None:
        _clear_post_workout_sentinel(activity_id)
        return

    try:
        mcp = MCPTool(token=u_orm.mcp_token, user_id=user.id, language=user.language)
        text = mcp.generate_post_workout_report_via_mcp(activity_id, signal=signal)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        _clear_post_workout_sentinel(activity_id)
        return

    if not text:
        _clear_post_workout_sentinel(activity_id)
        return

    # ── Step 4: defensive validator — fail blocks push, NOT persist
    validation = _validate_post_workout_text(text, user_id=user.id)
    if not validation.is_clean:
        sentry_sdk.capture_message(
            "post_workout report failed validator",
            level="warning",
            extras={"activity_id": activity_id, "issues": validation.issues,
                    "text": text, "signal_reason": signal.reason},
        )
        # Persist anyway — text useful for debugging via webapp; push blocked.

    # ── Step 5: persist (text + signal.reason — last is the cooldown anchor for next sessions)
    with get_sync_session() as s:
        a = s.get(Activity, activity_id)
        if not a:
            return
        a.ai_recommendation = text
        a.ai_recommendation_generated_at = datetime.now(timezone.utc)
        a.ai_signal_reason = signal.reason
        s.commit()

    if not validation.is_clean or user.is_silent:
        return  # no push; persisted text remains visible on webapp

    # ── Step 6: send compact push with deep-link to webapp
    headline = _extract_headline(text, signal)  # 1-2 lines, signal.reason-aware
    keyboard = {
        "inline_keyboard": [[{
            "text": _("📊 Открыть разбор"),
            "web_app": {"url": f"{settings.API_BASE_URL}/activity/{activity_id}"},
        }]],
    }
    TelegramTool(user=user).send_message(
        text=headline,
        reply_markup=keyboard,
        markdown=True,
    )
```

---

## 5. Prompt design

### System prompt (статическая часть, кэшируется)

`bot/prompts.py:get_static_system_prompt_post_workout()`:

```
You are an endurance triathlon coach. The athlete just finished a workout.

The user prompt names ONE anchor signal — the specific reason this session
warrants commentary (decoupling_outlier / drift_detected / pr_detected /
rpe_mismatch / compliance_miss / race_imminent) and gives you the numbers
behind it (today's value, history median, delta).

Your job: stay on that anchor. Prove it with tool data. Surface its
co-factor if one explains it. NOT a session summary.

Hard rules:
- 1-3 lines preferred. 5 is the absolute ceiling. Each line must earn its place.
  If at line 2 you've said the signal and its explanation — STOP.
- Anchor on the named signal. Don't drift to other topics, even if you find
  another interesting cifra during tool calls.
- Numbers must come from tools, not from memory.
- Don't restate the dashboard. Dashboard shows: sport, duration, TSS, DFA a1,
  HRVT1, Ra, Da — these are above your message, the athlete sees them.
- Don't recommend the next workout — that's the morning report's job.
- Race-context (race name, days to event, CTL gap) ONLY when it directly
  explains the anchor signal (e.g. signal == race_imminent, or compliance
  miss is explained by race-week taper). NEVER as a standalone closing line.
- Skip filler: "хорошая работа", "молодец", "well done", "продолжай в том же
  духе", "продолжай прогрессировать". Silence beats filler — if while
  loading data the signal turns out weaker than expected (history changed
  the median, validity invalidated the metric), write ONE line and stop.

Output shape (no headers, no bullets, plain prose lines separated by \n):
- Line 1: THE signal, named with the comparison. "Decoupling −11% on Z2
  ride 106 мин — last-5 median +2%."
- Line 2 (optional): interpretation / co-factor / "what this might mean",
  evidence-backed (other numbers as supporting clues).
- Line 3 (optional, rare): explaining context — only if it changes how
  Line 1 reads. E.g. TSB −26 explains why the workout was cut short.

NEVER write a generic "до 70.3 N дней, CTL X vs Y" closer. The morning
report shows CTL daily; reposting it here is filler.
```

Затем `render_athlete_block(user_id)` (как в утреннем отчёте) — атлет, цели, зоны, активные facts. **Две** `cache_control: ephemeral` секции, как в текущем коде (`docs/USER_CONTEXT_SPEC.md` §6).

### User prompt — динамический, с явным контекстом дашборда

«Don't repeat the dashboard» в system prompt — hope-based: Claude всё равно вызовет `get_activity_details` + `get_activity_hrv` и пересчитает уже показанные числа в текст. Решение, которое работает на практике — **передавать конкретные показанные числа в user prompt**:

```python
def build_post_workout_user_prompt(activity_id: str, signal: SignalResult) -> str:
    a = Activity.get(activity_id)
    hrv = ActivityHrv.get(activity_id)

    shown_lines = []
    if hrv and hrv.dfa_a1_warmup is not None:
        shown_lines.append(f"DFA a1 warmup: {hrv.dfa_a1_warmup:.2f}")
    if hrv and hrv.dfa_a1_mean is not None:
        shown_lines.append(f"DFA a1 avg: {hrv.dfa_a1_mean:.2f}")
    if hrv and hrv.ra_pct is not None:
        shown_lines.append(f"Ra: {hrv.ra_pct:+.1f}%")
    if hrv and hrv.hrvt1_hr is not None:
        shown_lines.append(f"HRVT1: {hrv.hrvt1_hr:.0f} bpm")
    # ... TSS, Da, sport icon, duration ...

    shown = "\n".join(shown_lines) if shown_lines else "—"
    evidence = "\n".join(f"  {k}: {v}" for k, v in signal.evidence.items())

    return (
        f"Activity {activity_id} ({a.start_date_local}, type={a.type}).\n\n"
        f"### Anchor signal\n"
        f"reason: {signal.reason}\n"
        f"evidence:\n{evidence}\n\n"
        f"### Dashboard already shown (DON'T repeat verbatim)\n{shown}\n\n"
        f"### Task\n"
        f"Write 1-3 lines anchored on the signal above. Use tools to validate "
        f"and dig for the co-factor that EXPLAINS the signal — not for other "
        f"random observations. If at tool-load the signal turns out weaker "
        f"than evidence suggests (history median shifted, validity invalidated "
        f"the metric), write ONE line saying so and stop. Don't invent a "
        f"different angle to fill space."
    )
```

`signal` пробрасывается как `kwargs={"signal": signal}` через `MCPTool.generate_post_workout_report_via_mcp(activity_id, signal)`. Evidence-dict сериализуется в строки `k: v` дословно — тестовый гейт `test_build_user_prompt_includes_signal` сверяет, что `signal.reason` и каждый ключ `evidence` присутствуют в финальной строке.

Это режет 80% случаев пересказа дашборда (через `shown_lines`) и 100% случаев растекания темы (через явный anchor). Оставшиеся 20% пересказа ловит post-validator (см. §9).

**Prompt-cache инвариант** (важно для Phase 3): динамический user prompt со `shown_lines` уникален на activity и **остаётся вне `cache_control`**. Кэшируются только две `cache_control: ephemeral` секции в **system prompt** — `get_static_system_prompt_post_workout()` (статика) и `render_athlete_block(user_id)` (атлет/цели/зоны/facts), как в утреннем отчёте (`docs/USER_CONTEXT_SPEC.md` §6). Любой новый код, добавляющий per-activity данные в системный промпт, ломает cache hit rate — этого делать не надо. `model_version` (Phase 3) тоже должен быть в user prompt, не в системном.

### Tool whitelist `POST_WORKOUT_TOOLS`

```python
POST_WORKOUT_TOOLS = [
    # текущая сессия
    "get_activity_details",            # zones, intervals, EF, decoupling
    "get_workout_compliance",          # plan vs actual
    # сравнение и тренды (НОВЫЕ детерминированные tools, см. §3)
    "find_similar_activities",         # ← Phase 1
    "get_workout_progress_summary",    # ← Phase 1, sparkline-ready
    # фоновые тренды (для перекрёстной проверки и редких запросов)
    "get_efficiency_trend",
    "get_progression_analysis",
    # пороги и drift
    "get_thresholds_history",
    "get_zones",
    # цель и projection
    "get_goal_progress",
    "get_race_projection",
    # контекст нагрузки
    "get_training_load",
    "predict_ctl",
    # личные паттерны
    "get_personal_patterns",
    # на крайний случай
    "get_activities",                  # fallback для произвольных запросов
    "get_wellness",                    # TSB на день тренировки
]
```

**Не входят:** `get_activity_hrv`, `get_recovery`, `get_hrv_analysis`, `get_rhr_analysis` — числа из них уже в дашборде, в user prompt'е перечислены как «не повторяй». Если Claude всё равно их вызовет — это не катастрофа (validator поймает дубликат), но в whitelist'е им делать нечего.

Тест-гейт `tests/tasks/test_post_workout_tool_names.py` — повторяет паттерн `test_morning_tool_names.py`: Claude не должен вызывать tool, который не в whitelist.

### Целевой формат вывода (примеры)

**Anchor: `decoupling_outlier`** (из demo 2026-05-14, i146893675 — Ride 106 мин Z2, TSB −26):

```
🚴 Decoupling −11.4% на Z2 ride 106 мин — last-5 long Z2 median +2% (25.04: +1.6, 02.05: +3.3).
HR падал относительно мощности во второй половине: либо хорошее pacing, либо слишком осторожный старт (VI 1.02 и IF 0.62 ближе ко второму).
План 140 мин закрыт на 76% — ожидаемо при TSB −26.
```

3 строки. Anchor — decoupling Δ −13.4% от медианы. Line 2 — интерпретация с уликами (VI и IF подкрепляют «осторожный старт»). Line 3 — co-factor (TSB) объясняет, почему сессия укорочена.

**Anchor: `rpe_mismatch`** (гипотетический — i148059070, RPE 2 при IF 0.77 на threshold session):

```
🚴 RPE 2 при IF 0.77 — на 4 пункта ниже tempo-median ~6 за 90 дней.
Power Z4 ~11 мин выполнено по плану, subjective effort несвойственно низкий.
```

2 строки. Anchor — RPE Δ −4. Line 2 — улика, что объективная нагрузка по плану, контраст усиливает сигнал.

**Anchor weakened mid-generation** (signal оказался шумом после load — пример того, как Claude должен сократиться):

```
🏃 Decoupling 9.2% на tempo 35 мин — медиана за 90 дней 11%, не выбивается. Метрика в пределах нормы.
```

1 строка. Изначально triggered как outlier, но при load истории median оказалась рядом — Claude обязан написать честно и остановиться, а не «дотягивать» до 3 строк.

**Чего никогда НЕ должно быть в output:**

- ❌ «🏃 Z2 60 мин · TSS 65 (план 70 ✓)» — дашборд, не комментарий
- ❌ «К 70.3 (5 нед): прогноз 1:39…» в каждой сессии — race-line как дежурный закрывающий
- ❌ «По плану следующая Z2 long через 3 дня» — рекомендация next workout (это morning report)
- ❌ «История похожих сессий пуста — baseline» — Claude вообще не позвали бы при отсутствии сигнала
- ❌ «Молодец», «keep going», «продолжай в том же духе» — filler

---

## 6. Idempotency, retries, edge cases

### Идемпотентность

`activity.ai_recommendation` — natural lock. Тот же activity_id → один отчёт, повторный вызов actor'а возвращает раньше после проверки sentinel'а.

Webhook'и Intervals.icu могут прилететь дважды (наблюдалось в `INTERVALS_WEBHOOKS_RESEARCH.md`) — sentinel + UPDATE-проверка покрывают.

### Force re-generation

Для отладки/Phase 3 фидбэка — endpoint `POST /api/jobs/post-workout-report/{activity_id}?force=true` (gate: owner only). Сбрасывает `ai_recommendation`, `ai_recommendation_generated_at` **и `ai_signal_reason`** в NULL и dispatch'ит actor. Без NULL'инга reason'а cooldown-фильтр (§4) увидит саму reference-activity как «триггерилась внутри окна» и подавит свежий запуск — activity «гасит сама себя».

### Retry policy

- Anthropic API errors (5xx, rate limit) → Dramatiq автоматически ретраит (default 3 попытки), каждый раз sentinel update'ится свежим timestamp'ом.
- MCP tool errors → ловим в `_call_mcp`, пробрасываем как tool_result error → Claude видит и продолжает или сдаётся.
- Pure failure → sentinel очищается, отчёт остаётся NULL. Не ретраим автоматически (в отличие от утреннего отчёта, где cron ретраит каждые 30 мин). Здесь триггер один — pipeline-end.

### Stale sentinel

10-минутный TTL (как в morning report). Worker crashed mid-generation → следующая попытка (force / next webhook) увидит sentinel старше 10 мин и заберёт задачу.

### Late activity (бэкфилл)

См. §4 gate 3 — `now() - activity.last_synced_at < 24h`. Покрывает:
- бэкфилл при первом OAuth-подключении (см. `OAUTH_BOOTSTRAP_SYNC_SPEC.md`) — сразу N сотен активностей за всю историю
- ручной пересинк через `cli sync-activities`
- ручной импорт Garmin GDPR-архива (`cli import-garmin`)
- late-evening session, чей FIT доходит после полуночи (старый гейт `start_date_local == today` ломался — `last_synced_at` нет)

Иначе при включении фичи на старого юзера прилетит сотня уведомлений.

### Silent users

`user.is_silent=True` → отчёт **генерится и сохраняется** в БД (для webapp-surface), но в TG не отправляется. Это согласуется с поведением `actor_compose_weekly_report:308`.

---

## 7. Triggers

В Phase 1 — один триггер: добавление dispatch'а в `actor_update_activity_details` после `pipeline.run()`. Срабатывает на каждый новый/обновлённый activity, прошедший FIT-pipeline.

Источники активностей сейчас:
- `actor_fetch_user_activities` cron (каждые 10 мин в 4-23h)
- `ACTIVITY_UPLOADED` webhook (`api/routers/intervals/webhook.py`) с 5-мин задержкой
- `ACTIVITY_UPDATED` webhook
- `cli sync-activities` (ручной)

Все они в итоге доходят до `actor_update_activity_details`, поэтому одной точки внедрения достаточно.

---

## 8. UX: webapp = primary, Telegram = push с callback

**Базовая модель:** полный AI-разбор + таблица похожих + sparkline'и живут на странице тренировки в webapp. Telegram — это **push-нотификация** с короткой завлекалкой и callback-кнопкой, открывающей эту страницу. Текст в TG целиком разбор не дублирует.

Причина — несколько факторов сходятся:

1. TG-сообщение приходит через 30-120с после загрузки активности, атлет уже мог уйти из чата. Единственная надёжная точка возврата — страница тренировки.
2. На webapp-странице есть место для таблицы «похожие сессии» и sparkline'ов decoupling/EF. В TG это всё развалится по разметке.
3. Push-сообщение должно быть коротким и signal-aware (см. signal-gate §4) — заголовок сообщает «что интересного», webapp показывает разбор.

### Phase 1 — поток сообщений

```
[deterministic dashboard, ~instant after upload — оставляем как есть]
🏃 Run 60m | TSS 65
DFA a1: 1.05 (warmup) → 0.78 (avg)
Ra: -2.1% ✅
HRVT1: 152 bpm / 5:08
[кнопки: RPE 1..10]

[AI push, +30-90s later, ТОЛЬКО если signal-gate прошёл]
📈 Decoupling упал на 1.5% относительно last-5 медианы. К 70.3 (5 нед) прогноз сдвинулся на -3 мин.
[кнопка: 📊 Открыть разбор → /activity/:id]
```

Заголовок (1-2 строки) формируется из триггера signal-gate'а: drift detected → `⚡ Drift detected: LTHR ...`, PR → `🏆 PR: 5min power ...`, decoupling outlier → `📈 Decoupling упал на N%...`. Если signal-gate прошёл по generic причине (race within 12 weeks) — берём первую строку из самого разбора (Claude знает писать её плотной).

Если signal-gate **не** прошёл — TG молчит. Разбор лежит в БД и показывается на webapp при следующем визите атлета на `/activity/:id`.

### Webapp `/activity/:id` (Phase 1, primary surface)

Структура страницы:

```
┌─────────────────────────────────────────────┐
│ [activity header — sport, date, duration]   │
├─────────────────────────────────────────────┤
│ ⚡ AI-разбор                                │
│ [text from activity.ai_recommendation]      │
│ Сгенерировано 5 мин назад                   │
│ [🔁 Перегенерировать]  ← owner only Phase 1 │
├─────────────────────────────────────────────┤
│ 📊 Похожие сессии                           │
│ [table from find_similar_activities]        │
│ Date  | Dur | TSS | HR  | Decoupling | EF   │
│ 03-15 | 62m | 67  | 145 | 4.5%       | 1.78 │
│ 03-08 | 58m | 64  | 144 | 4.1%       | 1.82 │
│ ...                                         │
├─────────────────────────────────────────────┤
│ 📈 Тренды (8 нед)                           │
│ [sparkline'и из get_workout_progress_summary]│
│ Decoupling: ▁▂▃▂▁ ↘                         │
│ EF:         ▁▂▃▄▅ ↗                         │
│ HR @ pace:  ▅▄▃▂▁ ↘                         │
├─────────────────────────────────────────────┤
│ [existing dashboard widgets — DFA, zones, …]│
└─────────────────────────────────────────────┘
```

Sentinel-aware рендер:
- `ai_recommendation IS NULL` → «Разбор для этой сессии не сгенерирован» + owner-кнопка «Сгенерировать».
- `ai_recommendation LIKE '__generating__:%'` → spinner «Готовим разбор…», auto-poll `/api/activity/:id` каждые 5с пока sentinel живой.
- normal text → рендер markdown'а + timestamp из `ai_recommendation_generated_at`.

Endpoint: `GET /api/activity/{activity_id}` уже отдаёт детали — расширяем response объектом `ai_report: { text, generated_at, similar: [...], progress: {...} }`. `similar` и `progress` подгружаем из тех же MCP tools, но через **API endpoint** (не через MCP — webapp должен работать без mcp_token UI flow).

### Phase 2 inline-кнопки в TG (опционально)

- `🔄 Обновить зоны` — появляется только когда signal-gate сработал на drift. Переиспользует существующий `update_zones` callback.
- `🔁 Перегенерировать` — owner-only через bot command `/regen <activity_id>` в Phase 1; в Phase 2 — кнопка в webapp для всех allowlist-юзеров.

---

## 9. Нормализация и валидация

### Что обязательно нормализовать

- **TSB-бакет** — `find_similar_activities` matched на похожем TSB-окне (low/mid/high). Без этого «улучшение HR» может быть просто свежим состоянием.
- **Сезон/блок** — Phase 2: тренды считать в окне ±6 нед, не за весь год (база vs билд vs пик дают разные «нормали»).

### Что нормализовать желательно (Phase 2)

- **Температура** — Garmin даёт `min/max temp` через FIT, мы их пока не сохраняем в `activities`. Колонка `activities.avg_temp_c` + парсинг в `_actor_post_process_fit_file`. HR при 28°C на 5-8 bpm выше при той же мощности.
- **Elevation** — для бега использовать gradient-adjusted pace; для вело Normalized Power уже компенсирует.
- **Time-of-day / fasted-state** — не приоритет в Phase 1.

### Defensive validator (на стороне actor'а)

После генерации, **до push'а в TG**, но **до persist'а в БД** запускаем `_validate_post_workout_text(text, user_id)`. Validator возвращает `(is_clean, issues[])`. Persist всегда (даже грязный текст полезен для отладки промпта на webapp); push только если `is_clean`.

Проверки:

1. **Длина** 200-1500 символов (короче — пустое; длиннее — Claude не уложился в 4-7 строк, push блокируем).
2. **Содержит хотя бы одну цифру** — не «всё хорошо, продолжай в том же духе» без данных.
3. **Forbidden phrases** (regex, case-insensitive):
   ```python
   FORBIDDEN_PHRASES = [
       r"\bмолодец\b", r"\bотлично\b", r"\bпродолжай в том же\b",
       r"\bхорошая работа\b", r"\bуспех(а|ов)?\b",
       r"\bwell done\b", r"\bgreat job\b", r"\bkeep (it )?up\b",
       r"\bkeep going\b", r"\bnice work\b",
   ]
   ```
   Filler-фразы — главная причина почему атлет начинает свайпать AI-сообщения. Lock'аем сразу, не ждём до AI-eval.
4. **No dashboard duplicates** — для каждой цифры из `shown_lines` (см. §5 user prompt) проверяем, что она не появляется в тексте 1-в-1. `f"{hrv.dfa_a1_mean:.2f}"` (`"0.78"`) не должно совпадать со substring'ом разбора. Если совпало — Claude пересказал дашборд → push блокируется (persist остаётся для разбора в проде).
5. **No alien activity_id** — берём все `i\d{6,12}` из текста, проверяем `Activity.user_id == user_id`. Поймает hallucination на чужие сессии.
6. **No markdown headers** (`#`, `##`) — Telegram их не рендерит, выглядит как мусор.

При фейле любой проверки → `sentry_sdk.capture_message(level="warning", extras={...})` с raw text + список нарушений. Это даёт прямую обратную связь по промпту (видно, какие правила Claude обходит чаще всего, → подкручиваем system prompt).

---

## 10. Тестирование

### Unit-тесты

- `tests/tasks/test_post_workout_actor.py` — cost-gate / sentinel / lock / retry pattern (зеркало `test_activity_actors.py::TestActorComposeMorningReport`). Включая late-FIT case: activity со `start_date_local=yesterday` но `last_synced_at=now()` должна **проходить** cost-gate.
- `tests/tasks/test_post_workout_signal_gate.py` — отдельный тест на каждый из 6 reason'ов (`compliance_miss` / `decoupling_outlier` / `drift_detected` / `rpe_mismatch` / `pr_detected` / `race_imminent`). Фикстуры на «всё в норме → passed=False» и «один из триггеров → passed=True, reason=...». Без него подбирать пороги (decoupling: Δ ≥ 1.5pp **И** `max(|today|,|median|) ≥ 5pp`, RPE 8/3 границы и т.п.) без регрессий невозможно. Для decoupling-outlier обязательны два golden-fail кейса: green-zone-движение (today 0.2 / median 3.3 — `max < 5` → no trigger) и invalid-VI short interval (today 15 / VI 1.18 — validity-fail → no trigger).
- `tests/tasks/test_post_workout_cooldown.py` — `_apply_cooldown_filter`. Кейсы: (1) `pr_detected` всегда проходит (cooldown=0); (2) `rpe_mismatch` в первый раз → passed=True, второй раз через 3 дня → passed=False с `evidence.suppressed_reason='rpe_mismatch'`; (3) `rpe_mismatch` через 8 дней (вне окна) → passed=True; (4) `drift_detected` через 10 дней (внутри 14d окна) → suppressed; (5) `passed=False` на входе → passed=False на выходе без обращения к БД. Партиал-индекс не тестируем (это инфраструктура Alembic), но покрываем `Activity.exists_with_signal_reason` отдельным ORM-тестом на (user-scope, reason-filter, since-cutoff).
- `tests/tasks/test_post_workout_validator.py` — все 6 проверок из §9 (длина / цифры / forbidden phrases / no-dashboard-duplicate / alien activity_id / no-markdown-headers). По каждой — golden-pass + golden-fail + edge cases (пустой текст, текст из одних чисел, текст с DFA cifрой 0.78 округлённой до 0.8 — должно проходить).
- `tests/tasks/test_post_workout_user_prompt.py` — `build_post_workout_user_prompt` корректно собирает `shown_lines`. Кейсы: full ActivityHrv (DFA + Ra + HRVT1 + Da), partial (только DFA, остальное NULL), пустой ActivityHrv (Swim/non-HRV), отсутствие `ActivityDetail`. shown_lines должен быть стабильно отсортирован — иначе prompt-cache ломается на ровном месте.
- `tests/tasks/test_post_workout_tool_names.py` — drift между `POST_WORKOUT_TOOLS` и упоминаниями tool'ов в промпте.
- `tests/mcp_server/test_find_similar_activities.py` — детерминистика match'а: sport / duration window / `dominant_zone_share ≥ 0.5` (включая MIXED-fallback при 35/30/25 распределении) / IF-bucket / TSB-bucket / 90-дневное окно.
- `tests/mcp_server/test_get_workout_progress_summary.py` — свёртка sparkline-данных. Stable shape, корректная weekly-медиана, NULL-handling для отсутствующих race goal / vo2max trend.
- `tests/tasks/test_tools.py::test_generate_post_workout_report_via_mcp` — Claude+MCP loop, `tools=POST_WORKOUT_TOOLS`, корректная обработка stop_reason.

### Integration

- `tests/integration/test_post_workout_pipeline.py` — pipeline `actor_update_activity_details → actor_compose_post_workout_report` end-to-end на тестовой БД.

### AI-eval

- `tests/ai/test_post_workout_quality.py` — golden-set из 10-15 реальных activities с разными типами (Z2 long, tempo, intervals, ramp-test) и разной историей (новая → 0 similar, старая → 5 similar). Проверки: формат (4-7 строк), наличие цифр, отсутствие forbidden phrases (молодец/отлично/well done).

### Drift-тест промпта

`tests/ai/test_post_workout_prompt_drift.py` — токены статической части промпта не растут больше +10% от baseline (важно для prompt-cache hit rate, см. `docs/USER_CONTEXT_SPEC.md` §6).

---

## 11. Открытые вопросы и решения

### Q1: AI-разбор как **дополнение** или как **замена** детерминированного дашборда?

**Решение:** дополнение в Phase 1. Дашборд — машинно-читаемая правда (DFA / Ra / HRVT1 / Da), AI-разбор — интерпретация. Атлет может прочитать только дашборд если торопится. В Phase 2 пересмотрим, если фидбэк скажет «два сообщения — много шума».

### Q2: Один промпт для Run/Ride или отдельные?

**Решение:** один в Phase 1. В Phase 2 — sport-specific секции в System prompt, которые активируются по `activity.type`. Главное — не два разных файла промптов, чтобы избежать drift'а.

### Q3: Force-regenerate — endpoint в Phase 1 или нет?

**Решение:** да, в Phase 1, **owner-only** (через `require_owner` в `api/deps.py`).

Endpoint: `POST /api/jobs/post-workout-report/{activity_id}?force=true`. Логика:
1. Проверяет `Activity.user_id == current_user.id OR current_user.role == "owner"`.
2. UPDATE `activities SET ai_recommendation = NULL, ai_recommendation_generated_at = NULL, ai_signal_reason = NULL`. Третья колонка обязательна — иначе cooldown-фильтр (§4) увидит саму reference-activity как «триггер внутри окна» и подавит regen.
3. `actor_compose_post_workout_report.send(user, activity_id)`.
4. Возвращает 202 + `{ "status": "dispatched" }`.

Без этого endpoint'а отлаживать промпт на проде надо через `psql` UPDATE — медленно и опасно. С ним — кнопка «🔁 Перегенерировать» в webapp (для owner) и `/regen <activity_id>` bot command.

Rate-limit: 1/час/(activity_id, user_id) — Redis INCR+EXPIRE.

**Важно:** «грязный» persist (validator failed → текст сохранён, но push заблокирован) **не самовосстанавливается** при следующем webhook'е. Step 1 actor'а смотрит на `ai_recommendation IS NOT NULL` и считает разбор готовым. Чтобы перегенерировать после fix'а промпта — только через force (NULL'ит все три колонки: `ai_recommendation`, `ai_recommendation_generated_at`, `ai_signal_reason` — перед dispatch'ем). Это сознательно: иначе каждый webhook ретраит фейлящий промпт и жжёт API-кредиты на одном и том же activity.

### Q4: Кэшировать `find_similar_activities` для одного activity_id?

Не в Phase 1 — детерминистика дешёвая, и инвалидировать кэш при бэкфилле сложно. Если на проде увидим >50ms latency — Redis с TTL=24h, ключ = `(activity_id,)`.

### Q5: А если activity uploaded, но FIT pipeline упал (poor RR quality)?

Сейчас `_actor_send_activity_notification` всё равно отправляет дашборд (с пустыми DFA-блоками). Логично, чтобы AI-разбор тоже отрабатывал — у нас есть compliance, efficiency_trend, training_load даже без FIT. Гейт по `processing_status` **не ставим**, разбор будет полезный без DFA.

### Q6: Ramp-test обходит этот flow — корректно?

Да. `_is_ramp_test_activity` вернёт True → actor early-return на гейте 7 (§4). Ramp-test'ы имеют свой rich-notification с zone-update триггером — параллельный AI-разбор будет шумом.

### Q7: `model_version` в Phase 1?

Не добавляем колонку в Phase 1, добавляем при первом изменении промпта в проде (тогда же мигрируем в `activity_ai_reports` если хотим хранить историю версий). `ai_recommendation_generated_at` уже добавлен — этого достаточно для базовой провенансы в Phase 1.

### Q8: Стоимость API-вызовов?

Оценка для Run/Ride с 10-15 tool calls на сессию: ~3-5k input + ~500 output → ~$0.02-0.04 на разбор. Один атлет с 8 тренировками в неделю = ~$1.5/мес. Приемлемо. При масштабировании на N юзеров можно агрессивнее tool-фильтровать.

---

## 12. Phasing & GitHub issues

### Phase 1 — backend + webapp + push

- [ ] **PW-1 — Колонки + миграция.** `activities.ai_recommendation` Text + `ai_recommendation_generated_at` TIMESTAMPTZ + `ai_signal_reason` VARCHAR(40) + partial index `ix_activities_user_signal_reason_date` + Alembic. Все три колонки в одной миграции — `ai_signal_reason` нужен для cooldown с первого дня (см. §4 + §13).
- [ ] **PW-2 — `find_similar_activities` MCP tool.** В `mcp_server/tools/activities.py`. Unit-тесты на match-логику: `dominant_zone_share ≥ 0.5`, MIXED-fallback, IF-bucket, TSB-bucket, **IF-boundary tolerance ±0.03** (см. §3 — кандидат с IF 0.66 при reference 0.62 должен проходить), **zone-match disabled для buckets `tempo`/`threshold`/`vo2`** (strength-сессия в tempo bucket принимает MIXED-кандидатов из того же IF-bucket'а).
- [ ] **PW-3 — `get_workout_progress_summary` MCP tool.** Sparkline-ready объект (decoupling/EF history, threshold drift, race projection delta). Unit-тесты на свёртку, не на текст.
- [ ] **PW-4 — `get_static_system_prompt_post_workout` + `build_post_workout_user_prompt` + `POST_WORKOUT_TOOLS`.** В `bot/prompts.py` и `tasks/tools.py`. Drift-тест против упоминаний tool'ов в промпте.
- [ ] **PW-5 — `MCPTool.generate_post_workout_report_via_mcp(activity_id)`.** Зеркало `generate_morning_report_via_mcp`. Unit-тесты на retry/timeout.
- [ ] **PW-6 — `actor_compose_post_workout_report` + cost-gate + dispatch в pipeline.** Sentinel-pattern, env-флаг + allowlist. Late-FIT bug fix: backfill detection через `now() - last_synced_at < 24h`, **не** `start_date_local == today`.
- [ ] **PW-7 — Signal-gate `evaluate_signal_gate`.** Compliance/decoupling-outlier/drift/RPE-mismatch/PR/race-window. **Decoupling-outlier триггер обязан (a) фильтровать через `is_valid_for_decoupling()` (`data/metrics.py`) и сегодняшнюю activity, и каждого history-кандидата** — иначе короткие интервалы дают false-positive push (см. §4 + demo 2026-05-14); **(b) применять out-of-green-zone-фильтр `max(|today|,|median|) ≥ 5pp`** поверх Δ≥1.5pp — без него ~50% decoupling-сигналов на текущих данных приходят на чистый шум recovery-ride'ов (coverage-валидация 2026-05-25, см. §4 + §13). Метрики `post_workout.gate.signal_passed_total{reason}` для тюнинга. **Persist всегда, push только при passed=true.**
- [ ] **PW-7.5 — Cooldown wrapper `_apply_cooldown_filter`.** Per-reason cooldown 7d (drift=14d, pr=0d) поверх signal-gate. ORM helper `Activity.exists_with_signal_reason(user_id, reason, since)`. Гасит «5 одинаковых rpe_mismatch за месяц на одной систематической ошибке» (user_id=62, см. §13). Метрика `post_workout.gate.signal_suppressed_total{reason}` для тюнинга cooldown_days после 2-3 недель проды. Тест: signal passes на пустой истории, второй проход в окне cooldown → suppressed.
- [ ] **PW-8 — Defensive validator + forbidden phrases.** Длина / цифры / forbidden / no-dashboard-duplicate / activity_id sanity / no-markdown-headers. Persist при fail, push блокируется.
- [ ] **PW-9 — Webapp surface на `/activity/:id`.** Блок AI-разбор + таблица «Похожие сессии» + sparkline'ы трендов. Sentinel-aware UI (spinner / NULL-state / normal). Endpoint `GET /api/activity/{id}` расширяется `ai_report` объектом.
- [ ] **PW-10 — Telegram push с deep-link callback.** Короткое signal-aware сообщение + `📊 Открыть разбор` → WebApp `/activity/:id`. `_extract_headline` на основе triggered signal-reason.
- [ ] **PW-11 — Force-regenerate endpoint.** `POST /api/jobs/post-workout-report/{activity_id}?force=true` (owner-only, rate-limit 1/час/activity). NULL'ит **три** колонки: `ai_recommendation`, `ai_recommendation_generated_at`, `ai_signal_reason` — последняя обязательна, иначе cooldown-фильтр (PW-7.5) увидит саму reference-activity как «триггер в окне» и подавит regen. Bot command `/regen <activity_id>`.

### Phase 2 — UX-polish & sport coverage

- [ ] **PW-12 — Sport-specific промпт-секции.** Long Z2 / tempo / intervals — разные акценты. По-прежнему один файл промптов с conditional блоками.
- [ ] **PW-13 — Swim coverage.** Лёгкий разбор без DFA: pace + HR + compliance.
- [ ] **PW-14 — `🔄 Обновить зоны` inline-кнопка в TG.** Только при signal=drift. Reuse существующего callback'а.
- [ ] **PW-15 — Webapp `🔁 Перегенерировать`** — для всех allowlist-юзеров (не только owner).

### Phase 3 — feedback loop & race path

- [ ] **PW-16 — Race-day report path** (отдельная спека `RACE_REPORT_SPEC.md`). `is_race=True` идёт через свой flow.
- [ ] **PW-17 — User feedback loop.** 👍/👎 кнопки в webapp + колонка `activity.user_feedback`. Метрика «AI-разбор за 30d покрыл X% активностей, Y% получили 👍».
- [ ] **PW-18 — `model_version` колонка + A/B промптов.** Только когда меняем промпт второй раз — раньше преждевременная оптимизация.
- [ ] **PW-19 — Weekly report integration.** Сигналы из разборов вклеиваются в weekly summary («за неделю decoupling упал с 4.5% до 3.2%»).

Owner: Radik.
ETA Phase 1: 1 спринт после merge'а спеки.
Allowlist: `user_id=1` для первой недели, расширяем по результатам signal-gate метрик.

### Критический путь Phase 1

```
PW-1 (миграция: ai_recommendation_generated_at + ai_signal_reason + partial index)
  ↓
PW-2, PW-3, PW-4 (tools + prompt) ← параллельно
  ↓
PW-5 (MCPTool method)
  ↓
PW-6 (actor skeleton + sentinel + cost-gate)
  ↓
PW-7 (signal-gate evaluate_signal_gate)
  ↓
PW-7.5 (_apply_cooldown_filter — нужны и PW-1 partial index, и PW-7 SignalResult shape)
  ↓
PW-8 (validator)
  ↓
PW-9 (webapp) || PW-10 (TG push) || PW-11 (force-regen — NULL'ит ai_signal_reason тоже) ← параллельно
```

PW-9 (webapp) можно начинать параллельно с PW-6/7/8 — нужны только колонки из PW-1.

---

## 13. Coverage validation 2026-05-25 — observations & decisions

Перед стартом Phase 1 прогнали signal-gate (полу-ручно через SQL) против последних 30 дней реальных активностей user_id=1 (owner, 27 Run/Ride) и user_id=62 (athlete, 32 Run/Ride). Цель — убедиться, что (a) сигналы ловят то, что должны, (b) coverage попадает в прогноз спеки «60-70% silent», (c) пороги не дают filler-разборов на нормальных сессиях.

### 13.1. Сводная таблица coverage

| | user_id=1 (owner) | user_id=62 (athlete) |
|---|---|---|
| Активностей в окне | 27 | 32 |
| С RPE | 27 (100%) | 15 (47%) |
| С paired plan | 18 | **0** |
| С `ACTIVITY_ACHIEVEMENTS` | 4 | **0** |
| `pr_detected` | 4 | 0 |
| `decoupling_outlier` (после green-zone) | 4 | 4 |
| `rpe_mismatch` (cold-start fallback) | 2 | 5 |
| `compliance_miss` | 4 | 0 |
| **Coverage** | **41%** | **28%** |
| **Silent** | 59% | **72%** |

Совокупно: 20 уникальных сессий с сигналом из 59 = **34% coverage**, попадает в нижнюю границу прогноза спеки.

### 13.2. Ключевые находки

**A. Decoupling Δ≥1.5pp без green-zone-фильтра — наполовину шум.**
8 trigger'ов у user_id=1, из них 4 — движение внутри «зелёной» durability-зоны (today/median оба <5%, разница — естественный VI/EF/температурный шум recovery-ride'ов). Главный кейс: i147431143 (Ride 60 мин Z2, dec 0.2% vs median 3.3%, Δ=−3.1pp, RPE=1) — формально trigger, содержательно chill Tuesday spin. Фикс — `max(|today|, |median|) ≥ 5.0` дополнительно к Δ-порогу (см. §4). После фикса: user_id=1 = 4 honest dec_out, user_id=62 = 4 dec_out — все либо явно red-zone (>10pp), либо переход через 5% порог.

**B. RPE cold-start порог `IF > 0.85` завышен — пропускает demo-кейс i148059070.**
Спека сама строится на этом примере (Ride 36 мин, IF 0.77, RPE 2 → должен trigger), но cold-start fallback `(rpe ≤ 3 AND IF > 0.85)` его пропускает (0.77 < 0.85). Median-test тоже недоступен — у user_id=1 в tempo-bucket за 90 дней только 1 кандидат с RPE. **Не фиксили в спеке.** Понижение до 0.75 ловит i148059070, но без больше данных не понять, не наплодит ли false-positives на других сессиях. Откладываем до 50+ RPE-наблюдений в tempo-bucket — тогда median-test перекроет cold-start и порог не понадобится.

**C. Половина сигналов — не универсальная, зависит от user behavior.**
- `compliance_miss` требует `paired_event_id` (Intervals.icu calendar). У user_id=62 — 0 paired plans за месяц → сигнал мёртв на этом юзере.
- `pr_detected` требует `ACTIVITY_ACHIEVEMENTS` webhook данных. У user_id=62 — 0 achievements в окне (либо webhook не настроен, либо fitness стабилен — без отдельной диагностики не понять).
- Для каждого нового юзера какой-то из 6 сигналов окажется dead code. **Coverage в проде будет ниже, чем теоретический.**

**D. `race_imminent` мёртв 100% времени, когда нет race в 84 днях.**
У user_id=1 за месяц — ни одной trigger. Спека позиционирует его как «generic context anchor — низший приоритет», но эта роль не оправдана: silent коэффициент с/без `race_imminent` идентичен. Не убираем (когда race появится — он включится автоматически), но не считаем opportunity-генератором.

**E. Systematic-pattern сигналы спамят без cooldown.**
user_id=62: 13 потенциальных rpe-mismatch cold-start trigger'ов (Run, IF>1.0, RPE=1). 8 гасит median-test (история такая же — атлет систематически ставит RPE=1 на high-IF run'ах, median ≈ 1, Δ=0). Остаются 5 на «тонкой истории» (не хватает 3 матчей). **Все 5 — про одно и то же** (либо underreporting, либо неоткалиброванный threshold pace). Без cooldown атлет получит 5 идентичных разборов «почему RPE 1 при IF 1.2», и свайпнет их. Фикс — duplicate-reason suppression (см. §4 «Duplicate-reason suppression», PW-7.5). Cooldown 7d (drift=14d, pr=0d).

**F. Decoupling — единственный «всегда-работающий» сигнал на обоих юзерах (4 trigger'а каждому).**
Не зависит от plans / achievements / RPE-привычек. Если делать stripped-down MVP — это первый кандидат как единственный сигнал. Остальные — «marketing breadth», по факту срабатывают эпизодически и зависят от индивидуального профиля.

### 13.3. Решения, внесённые в спеку

| # | Решение | Где |
|---|---|---|
| 1 | Decoupling-outlier: green-zone-фильтр `max(\|today\|, \|median\|) ≥ 5pp` поверх Δ≥1.5pp | §4 + §10 тесты + PW-7 (b) |
| 2 | Колонка `activities.ai_signal_reason VARCHAR(40)` + partial index | §3 + PW-1 |
| 3 | `_apply_cooldown_filter` поверх `evaluate_signal_gate`, per-reason cooldown (7d default, drift=14d, pr=0d) | §4 «Duplicate-reason suppression» + actor Step 2 + PW-7.5 |
| 4 | Метрика `signal_suppressed_total{reason}` — отличать suppress'ы от пустых сигналов | §4 «Метрики» |
| 5 | Тесты на cooldown wrapper + golden-fail кейсы для green-zone | §10 |
| 6 | Cooldown гейтит по `start_date_local` (а не по `ai_recommendation_generated_at`) | §4 + ниже |

**Решение 6 — обоснование.** `Activity.exists_with_signal_reason(user_id, reason, since)` фильтрует по `start_date_local >= cutoff`. Альтернатива — гейтить по `ai_recommendation_generated_at` (момент анализа), но это ломается на late-arriving FIT: бэкфилл-загрузка тренировки 6-дневной давности с `rpe_mismatch` посчитается «вне 7d-окна» по дате тренировки и пройдёт фильтр, хотя это первое появление сессии в pipeline. Решение — гейтим по `start_date_local`:
- **Cooldown — про защиту атлета от спама про один и тот же паттерн.** Если две физически-разные сессии триггерят одинаковый reason — gate отрабатывает по их датам, не по моменту, когда мы их разобрали.
- **Late-FIT не ломает гейт** благодаря backfill cost-gate `now() - last_synced_at < 24h` (см. §4 gate 3) — он отсекает late-arriving активности **до** signal-gate. То есть в cooldown-чек reference-activity со старой `start_date_local` попадает только если она пришла свежей (live webhook), а старые бэкфилл-rows не дойдут до фильтра вообще.

### 13.4. Решения **не** внесённые в спеку (отложено)

| Что | Почему |
|---|---|
| Понизить cold-start RPE порог IF<0.75/IF>0.75 | Нужно ≥50 наблюдений per IF-bucket — на user_id=1 (27 сессий/мес) это 2 месяца сбора. Текущий порог консервативен (false-negative лучше false-positive). |
| Убрать `race_imminent` | Дешевле оставить — код есть, потребит 0 ресурсов когда race вне окна, включится сам когда появится. |
| Расширить выборку валидации на >2 юзеров | Multi-tenant Phase 2 deferred. Без неё «увидеть третий паттерн» нельзя — copy SQL и прогнать вручную дешевле, но мало кто кроме owner'а будет в bot allowlist первые месяцы. |
| Per-bucket decoupling thresholds (recovery=5pp / endurance=2pp / tempo=1.5pp) | Зеленая зона теперь обрезает recovery-шум без bucket-specific логики. Если на проде «зелёная зона + Δ=1.5» окажется недостаточным — добавим. |

### 13.5. Что мы НЕ знаем после валидации

- **Качество текста Claude.** Главная неопределённость спеки — не «срабатывает ли signal-gate», а «полезен ли текст разбора атлету». Это проверяется только в проде с реальными атлетами на 1-2 неделях dry-run.
- **Влияние cooldown на UX.** 7 дней — догадка из 1 кейса (5 rpe_mismatch у user_id=62). Может оказаться, что 7d слишком жёстко (атлет ожидает разбор после каждого тренинга и недоумевает silence'у) или слишком мягко (всё ещё спам). Метрика `signal_suppressed_total{reason}` даст ответ через 2-3 недели.
- **Coverage на новом юзере.** user_id=1 и user_id=62 — оба активные с ≥3 месяцами истории. Cold-start новый юзер за первые 90 дней получит почти 0 разборов (нет history для match'а). Это OK для MVP — onboarding fixes другая спека (`OAUTH_BOOTSTRAP_SYNC_SPEC.md`), пост-workout AI на пустую историю смысла не имеет.

---
