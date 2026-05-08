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
- `get_garmin_race_predictions` — прогноз времени на 5km/10km/HM
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

### Колонки `activities.ai_recommendation` + `ai_recommendation_generated_at`

```python
# data/db/activity.py:Activity
ai_recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
ai_recommendation_generated_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True), nullable=True
)
```

**Миграция** (`migrations/versions/xxxx_add_activities_ai_recommendation.py`):

```python
def upgrade() -> None:
    op.add_column("activities", sa.Column("ai_recommendation", sa.Text(), nullable=True))
    op.add_column(
        "activities",
        sa.Column("ai_recommendation_generated_at", sa.DateTime(timezone=True), nullable=True),
    )

def downgrade() -> None:
    op.drop_column("activities", "ai_recommendation_generated_at")
    op.drop_column("activities", "ai_recommendation")
```

**Решения по схеме:**

- Хранить готовый текст (Text, не JSONB). Унификация с `wellness.ai_recommendation`. Если Phase 2 потребует структурированного payload (для webapp-карточки), мигрируем к JSONB и прокинем `model_version`.
- `ai_recommendation_generated_at` добавляем **сейчас**, не в Phase 3 — поле бесплатное в миграции, но позволяет webapp'у показывать «разбор сгенерирован 2 мин назад / 3 дня назад», а Phase 3 (force-regen, A/B промптов) обойдётся без миграции.
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
if_bucket = bucket(activity.intensity_factor)

candidates = Activity
  .where(user_id, type==sport, is_race==False)
  .where(moving_time BETWEEN duration_min AND duration_max)
  .where(start_date_local < activity.start_date_local)
  .where(start_date_local >= activity.start_date_local - 90 days)

# Filter post-fetch (zone/IF/TSB requires details + wellness join):
filtered = [c for c in candidates if bucket(c.intensity_factor) == if_bucket]
if dominant_zone != "MIXED":
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
      from get_garmin_race_predictions, only when active RACE_A goal exists
    - sample_size: count of source activities in the window
    """
```

**Почему в Phase 1, а не отрезать через комбинацию tools:** если Claude собирает sparkline руками из `get_efficiency_trend` + `get_thresholds_history` + `get_garmin_race_predictions`, он добавит ошибки на склейке (разные окна, разные единицы, путает медиану и среднее). Один детерминированный tool с готовым свёрнутым объектом — дешевле (1 round-trip вместо 3) и надёжнее (нет LLM-математики). Тесты покрывают именно склейку, а не текст.

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

Если cost-gate проходит → разбор **генерится и persist'ится в БД** (для webapp-surface, всегда). Это важно: webapp = primary surface, и атлет, открывший `/activity/:id` через 3 дня, должен увидеть разбор, даже если тогда ничего «interesting» не было.

**Signal-gate** (применяется к готовому разбору, до push-в-TG) — отсекает скучные разборы от рассылки в Telegram:

```python
def has_interesting_signal(
    activity: Activity,
    detail: ActivityDetail,
    progress: WorkoutProgressSummary,
    similar: SimilarActivitiesResult,
    compliance: WorkoutCompliance | None,
    drift: ThresholdDriftDTO | None,
    user: UserDTO,
) -> bool:
    # 1. Compliance miss
    if compliance and abs(compliance.duration_pct - 100) > 10:
        return True
    if compliance and compliance.intensity_drift_z_count >= 1:
        return True

    # 2. Decoupling outlier vs last-5 median
    if similar.matches and detail.decoupling_pct is not None:
        median = statistics.median(m.decoupling_pct for m in similar.matches if m.decoupling_pct is not None)
        if median is not None and abs(detail.decoupling_pct - median) >= 1.5:
            return True

    # 3. Threshold drift
    if drift and drift.alerts:
        return True

    # 4. RPE-vs-HR mismatch — RPE 8+ при IF<0.75, или RPE 3- при IF>0.85
    if activity.rpe is not None and activity.intensity_factor is not None:
        if (activity.rpe >= 8 and activity.intensity_factor < 0.75) or \
           (activity.rpe <= 3 and activity.intensity_factor > 0.85):
            return True

    # 5. PR — есть ACTIVITY_ACHIEVEMENTS row для этой activity
    if activity_achievements_exists(activity.id):
        return True

    # 6. Race within 12 weeks — даже скучный разбор полезен (race-relevant projection)
    if active_race_within_days(user.id, days=84):
        return True

    return False
```

Если signal-gate **не** прошёл → разбор остаётся в БД, в Telegram **не уходит** (push-в-TG молчит). Когда атлет сам откроет `/activity/:id` — увидит. Это режет шум и стоимость alert fatigue без потери данных.

**Метрики для тюнинга порогов** (Sentry breadcrumb или свой actor-метрика):
- `post_workout.gate.signal_passed_total{reason}` — какие триггеры срабатывают
- `post_workout.gate.signal_skipped_total` — сколько разборов осели только в БД

Через 2-3 недели после выкатки можно подтянуть пороги (decoupling delta 1.5%, RPE 8/3, etc.) на реальных данных.

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
                return  # already generated, idempotent
        a.ai_recommendation = f"__generating__:{time.time():.0f}"
        s.commit()

    # ── Step 2: generate via Claude+MCP (no lock) — 30-120s
    with get_sync_session() as s:
        u_orm = s.get(User, user.id)
    if u_orm is None:
        _clear_post_workout_sentinel(activity_id)
        return

    try:
        mcp = MCPTool(token=u_orm.mcp_token, user_id=user.id, language=user.language)
        text = mcp.generate_post_workout_report_via_mcp(activity_id)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        _clear_post_workout_sentinel(activity_id)
        return

    if not text:
        _clear_post_workout_sentinel(activity_id)
        return

    # ── Step 3: defensive validator — fail blocks push, NOT persist
    validation = _validate_post_workout_text(text, user_id=user.id)
    if not validation.is_clean:
        sentry_sdk.capture_message(
            "post_workout report failed validator",
            level="warning",
            extras={"activity_id": activity_id, "issues": validation.issues, "text": text},
        )
        # Persist anyway — for debugging via webapp; just don't push to TG.

    # ── Step 4: persist (always, if cost-gate passed)
    with get_sync_session() as s:
        a = s.get(Activity, activity_id)
        if not a:
            return
        a.ai_recommendation = text
        a.ai_recommendation_generated_at = datetime.now(timezone.utc)
        s.commit()

    if not validation.is_clean:
        return  # validator failed → no push

    # ── Step 5: signal-gate — push to TG only if "interesting"
    signal = _has_interesting_signal(activity_id, user)
    logger.info(
        "post_workout signal-gate user_id=%d activity_id=%s passed=%s reason=%s",
        user.id, activity_id, signal.passed, signal.reason,
    )
    if not signal.passed:
        return  # silent persist; webapp shows it when athlete opens /activity/:id

    if user.is_silent:
        return

    # ── Step 6: send compact push with deep-link to webapp
    headline = _extract_headline(text, signal)  # 1-2 lines, signal-aware
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
You are an endurance triathlon coach. The athlete just finished a workout
and you are writing a SHORT (4-7 lines) Russian/English message about it.
Tone: data-driven, concise, no emojis except sport icon, no fluff.

Your job is to surface what's INTERESTING about this session:
- comparison with similar sessions (use find_similar_activities)
- progress trends (use get_efficiency_trend, get_thresholds_history)
- race-relevant projection when an active goal exists (get_goal_progress,
  get_garmin_race_predictions)
- one actionable insight if warranted (drift, RPE-mismatch, PR, low compliance)

Hard rules:
- 4-7 lines maximum. If you need more, you're padding.
- Numbers must come from tools, not from memory.
- Don't repeat the deterministic dashboard (DFA, HRVT1, Ra are already
  shown above your message — assume the athlete sees them).
- Don't recommend the next workout — that's the morning report's job.
- If history is too thin (<3 similar activities), say so honestly and
  give baseline-only commentary.
- Skip "хорошая работа" / "молодец" — silence beats filler.

Output structure (no headers, just lines):
1. One-line verdict: compliance + main marker
2. 1-2 observations vs history (sparkline-style preferred when fitting)
3. Race-relevant context (if RACE_A active and within 12 weeks)
4. Action item OR "следующая такая через X дней по плану"
```

Затем `render_athlete_block(user_id)` (как в утреннем отчёте) — атлет, цели, зоны, активные facts. **Две** `cache_control: ephemeral` секции, как в текущем коде (`docs/USER_CONTEXT_SPEC.md` §6).

### User prompt — динамический, с явным контекстом дашборда

«Don't repeat the dashboard» в system prompt — hope-based: Claude всё равно вызовет `get_activity_details` + `get_activity_hrv` и пересчитает уже показанные числа в текст. Решение, которое работает на практике — **передавать конкретные показанные числа в user prompt**:

```python
def build_post_workout_user_prompt(activity_id: str) -> str:
    a = Activity.get(activity_id)
    hrv = ActivityHrv.get(activity_id)
    detail = ActivityDetail.get(activity_id)

    shown_lines = []
    if hrv and hrv.dfa_a1_warmup is not None:
        shown_lines.append(f"DFA a1 warmup: {hrv.dfa_a1_warmup:.2f}")
    if hrv and hrv.dfa_a1_mean is not None:
        shown_lines.append(f"DFA a1 avg: {hrv.dfa_a1_mean:.2f}")
    if hrv and hrv.ra_pct is not None:
        shown_lines.append(f"Ra: {hrv.ra_pct:+.1f}%")
    if hrv and hrv.hrvt1_hr is not None:
        shown_lines.append(f"HRVT1: {hrv.hrvt1_hr:.0f} bpm")
    # ... TSS, Da, etc.

    shown = "\n".join(shown_lines) if shown_lines else "—"
    return (
        f"Сгенерируй разбор тренировки {activity_id} (дата {a.start_date_local}).\n\n"
        f"Дашборд УЖЕ показал атлету эти числа. НЕ повторяй их в тексте, "
        f"иначе разбор станет дубликатом дашборда:\n{shown}\n\n"
        f"Твоя задача — то, чего в дашборде нет: сравнения с похожими сессиями, "
        f"тренды, race-relevant projection, action items. Используй tools."
    )
```

Это режет 80% случаев пересказа дашборда. Оставшиеся 20% ловит post-validator (см. §9 — содержит ли текст любую цифру из `shown_lines` без переформулировки).

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
    "get_garmin_race_predictions",
    "get_garmin_vo2max_trend",
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

### Целевой формат вывода (пример)

```
🏃 Z2 60 мин · TSS 65 (план 70 ✓)
Decoupling: 5.2 → 4.8 → 3.9 → 4.1 → 3.5 → **3.2%** ↘
HR при pace 5:30/km: 145 → 142 (-3 bpm за 4 нед, при сопоставимом TSB)
К 70.3 (5 нед): Garmin прогноз 1:39 vs 1:42 месяц назад — в коридоре цели 1:40.
По плану следующая Z2 long через 3 дня.
```

vs первая такая сессия:

```
🏃 Tempo 40 мин · TSS 55 (план 60 ⚠️ -8%)
Pace 4:32/km при HR 165 — выше плана Z3, но без drift'а (decoupling 2.1%, EF 1.92).
История похожих сессий пока пуста — это baseline. Запиши ощущение через RPE.
```

---

## 6. Idempotency, retries, edge cases

### Идемпотентность

`activity.ai_recommendation` — natural lock. Тот же activity_id → один отчёт, повторный вызов actor'а возвращает раньше после проверки sentinel'а.

Webhook'и Intervals.icu могут прилететь дважды (наблюдалось в `INTERVALS_WEBHOOKS_RESEARCH.md`) — sentinel + UPDATE-проверка покрывают.

### Force re-generation

Для отладки/Phase 3 фидбэка — endpoint `POST /api/jobs/post-workout-report/{activity_id}?force=true` (gate: owner only). Сбрасывает `ai_recommendation` в NULL и dispatch'ит actor.

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
[кнопки: RPE 1..10, 📸 Card]

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
- `tests/tasks/test_post_workout_signal_gate.py` — отдельный тест на каждый из 6 reason'ов (`compliance_miss` / `decoupling_outlier` / `drift_detected` / `rpe_mismatch` / `pr_detected` / `race_within_12w`). Фикстуры на «всё в норме → passed=False» и «один из триггеров → passed=True, reason=...». Без него подбирать пороги (1.5% decoupling delta, RPE 8/3 границы и т.п.) без регрессий невозможно.
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
2. UPDATE `activities SET ai_recommendation = NULL, ai_recommendation_generated_at = NULL`.
3. `actor_compose_post_workout_report.send(user, activity_id)`.
4. Возвращает 202 + `{ "status": "dispatched" }`.

Без этого endpoint'а отлаживать промпт на проде надо через `psql` UPDATE — медленно и опасно. С ним — кнопка «🔁 Перегенерировать» в webapp (для owner) и `/regen <activity_id>` bot command.

Rate-limit: 1/час/(activity_id, user_id) — Redis INCR+EXPIRE.

**Важно:** «грязный» persist (validator failed → текст сохранён, но push заблокирован) **не самовосстанавливается** при следующем webhook'е. Step 1 actor'а смотрит на `ai_recommendation IS NOT NULL` и считает разбор готовым. Чтобы перегенерировать после fix'а промпта — только через force (NULL'ит обе колонки перед dispatch'ем). Это сознательно: иначе каждый webhook ретраит фейлящий промпт и жжёт API-кредиты на одном и том же activity.

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

- [ ] **PW-1 — Колонки + миграция.** `activities.ai_recommendation` Text + `ai_recommendation_generated_at` TIMESTAMPTZ + Alembic.
- [ ] **PW-2 — `find_similar_activities` MCP tool.** В `mcp_server/tools/activities.py`. Unit-тесты на match-логику: `dominant_zone_share ≥ 0.5`, MIXED-fallback, IF-bucket, TSB-bucket.
- [ ] **PW-3 — `get_workout_progress_summary` MCP tool.** Sparkline-ready объект (decoupling/EF history, threshold drift, race projection delta). Unit-тесты на свёртку, не на текст.
- [ ] **PW-4 — `get_static_system_prompt_post_workout` + `build_post_workout_user_prompt` + `POST_WORKOUT_TOOLS`.** В `bot/prompts.py` и `tasks/tools.py`. Drift-тест против упоминаний tool'ов в промпте.
- [ ] **PW-5 — `MCPTool.generate_post_workout_report_via_mcp(activity_id)`.** Зеркало `generate_morning_report_via_mcp`. Unit-тесты на retry/timeout.
- [ ] **PW-6 — `actor_compose_post_workout_report` + cost-gate + dispatch в pipeline.** Sentinel-pattern, env-флаг + allowlist. Late-FIT bug fix: backfill detection через `now() - last_synced_at < 24h`, **не** `start_date_local == today`.
- [ ] **PW-7 — Signal-gate `_has_interesting_signal`.** Compliance/decoupling-outlier/drift/RPE-mismatch/PR/race-window. Метрики `post_workout.gate.signal_passed_total{reason}` для тюнинга. **Persist всегда, push только при passed=true.**
- [ ] **PW-8 — Defensive validator + forbidden phrases.** Длина / цифры / forbidden / no-dashboard-duplicate / activity_id sanity / no-markdown-headers. Persist при fail, push блокируется.
- [ ] **PW-9 — Webapp surface на `/activity/:id`.** Блок AI-разбор + таблица «Похожие сессии» + sparkline'ы трендов. Sentinel-aware UI (spinner / NULL-state / normal). Endpoint `GET /api/activity/{id}` расширяется `ai_report` объектом.
- [ ] **PW-10 — Telegram push с deep-link callback.** Короткое signal-aware сообщение + `📊 Открыть разбор` → WebApp `/activity/:id`. `_extract_headline` на основе triggered signal-reason.
- [ ] **PW-11 — Force-regenerate endpoint.** `POST /api/jobs/post-workout-report/{activity_id}?force=true` (owner-only, rate-limit 1/час/activity). Bot command `/regen <activity_id>`.

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
PW-1 (миграция)
  ↓
PW-2, PW-3, PW-4 (tools + prompt) ← параллельно
  ↓
PW-5 (MCPTool method)
  ↓
PW-6, PW-7, PW-8 (actor + signal-gate + validator) ← последовательно
  ↓
PW-9 (webapp) || PW-10 (TG push) || PW-11 (force-regen) ← параллельно, независимы
```

PW-9 (webapp) можно начинать параллельно с PW-6/7/8 — нужны только колонки из PW-1.
