# Race Execution Plan Spec

> Структурированный план исполнения A-гонки (warmup, pacing corridor по этапам, fueling, contingencies), который атлет читает накануне или утром в день гонки. Генерируется по запросу из данных Intervals.icu (6 недель тренировок, зоны, race-day fitness projection) через Claude с forced JSON schema; персистится в `race_plans` JSONB.

**Status:** PR1 + PR2.1-PR2.6 + PR3 ✅ shipped (commit `1d68ca6`). 149 backend tests + TS clean. Runtime feedback (§12 step 5) pending; PR4/Phase 3/multi-tenant rollout deferred.

**Related:**

| Issue / Spec | Связь |
|---|---|
| END-62 (parent), END-63 (Phase 1 backend), END-100+ (Phase 2 surface) | Тикеты |
| Issue #331 | Geo source upgrade (location/weather) — see §11.11 |
| `data/race_plan_service.py:build_race_plan` | Главная entry-point (single source of truth для MCP + REST) |
| `mcp_server/tools/races.py:generate_race_plan` | MCP wrapper (~30 строк) → `build_race_plan` |
| `api/routers/race_plan.py` | REST `GET/POST /api/race-plan` + `GET /api/race-plan/inheritable-conditions` |
| `data/db/race_plan.py` | `RacePlan` ORM (+ `mark_pushed_for_race_date`) |
| `data/db/race_plan_compliance.py` + `data/race_plan_compliance_service.py:compute_compliance` | Phase 3 metrics writer-stub (§14) |
| `tasks/actors/race_plan.py` + `bot/race_plan_telegram.py` + `bot/scheduler.py:scheduler_pre_race_plan_push_job` | 24h pre-race push pipeline |
| `webapp/src/components/RacePlanPanel.tsx` + `RaceConditionsForm.tsx` | Goal-tab UI |
| `migrations/versions/c3c3d4e5f6a7_add_race_plans.py` | `race_plans` schema |
| `migrations/versions/aa7b8c9d0e1f_add_race_plan_compliance.py` | `race_plan_compliance` + `Race.carbs_consumed_g` |
| `docs/MULTI_TENANT_SECURITY_SPEC.md` | T1 — `user_id`-scoped reads |
| `docs/ML_RACE_PROJECTION_SPEC.md` | `fitness_projection` — race-day CTL/ATL/TSB |
| `docs/RACE_TAGGING.md` | `athlete_goals` (RACE_A/B/C) — источник цели |

---

## 1. Мотивация

Любительские A-гонки чаще всего теряются на **execution failure** — стартанул в первой трети дистанции на 10 уд/мин выше threshold, выпил один гель за весь IM-марафон, на жаре не сменил cap-стратегию. Тренер пишет ученику накануне развёрнутый план: warmup, коридор по сегментам (low / target / cap), HR-потолок, fueling cadence, что делать при судороге / перегреве / отставании от графика. Это конкретная и воспроизводимая работа.

У нас есть все вводные:
- `athlete_goals` — RACE_A/B/C с `event_date`, `distance`, `goal_time`.
- `activities` — последние 6 недель (TSS, avg HR, длинные эффорты).
- `athlete_settings` — per-sport `lthr` / `max_hr` / `ftp` / зоны (HR / power / pace).
- `fitness_projection` — CTL/ATL/TSB на день гонки (decay-кривая из `FITNESS_UPDATED` webhook'а).
- `wellness` — текущий anchor (CTL today, sleep quality перед стартом).

Остаётся попросить Claude собрать всё в structured plan и сохранить.

---

## 2. Scope

### Phase 1 (MVP, ✅ done) — backend foundation

- ✅ Таблица `race_plans` + ORM + миграция.
- ✅ MCP tool `generate_race_plan(goal_id?, dry_run=False)`.
- ✅ Forced JSON schema через Anthropic tool_use (`submit_race_plan`).
- ✅ Refusal gates: <6 активностей за 6 недель / **>200 дней** до гонки (расширено с 120д — см. §5).
- ✅ Defensive validator (corridor monotonicity + mixed-units/numeric-prose reject + HR ceiling vs `max_hr+5` + transitions iff is_tri — см. §6).
- ✅ Idempotency: 1 plan на (goal_id, UTC-день).
- ✅ Cost tracking: `ApiUsageDaily.increment` после каждого Claude call (см. §8).
- ❌ ~~Allowlist gate~~ — отброшен (см. §11.1). Защиту даёт валидатор + JSON schema, owner == единственный реальный consumer на момент Phase 1.

### ✅ Phase 2 — surface (done, commit `1d68ca6`)

**Web (Dashboard/Goal tab):**
- ✅ REST: `GET/POST /api/race-plan` + `GET /api/race-plan/inheritable-conditions` — `api/routers/race_plan.py:43,137,183`.
- ✅ Service-extraction: business logic в `data/race_plan_service.py:build_race_plan`; MCP-тул теперь thin wrapper в `mcp_server/tools/races.py:624` (~30 строк).
- ✅ UI: `webapp/src/components/RacePlanPanel.tsx` (200/404 branches, structured render, regenerate button) + `RaceConditionsForm.tsx` (collapsible elevation/temp inputs + inherit-from-past-race dropdown).
- ✅ Regenerate: in-place UPDATE (preserve id), 1/day rate limit, HTTP 429 + Retry-After.
- ✅ dry_run rate limit (5/day per user via Redis, secH1 fix) — cost guard against `{dry_run: true}` looping.

**Telegram:**
- ✅ 24h pre-race push: `bot/scheduler.py:scheduler_pre_race_plan_push_job` → `tasks/actors/race_plan.py` → `bot/race_plan_telegram.py`. Cron 08:00 Belgrade local. Idempotency via `payload.pushed_for_race_date`.
- ❌ ~~`/raceplan` slash command~~ — dropped from scope (Decisions log §2). Recall lives in webapp Goal tab + chat AI Q&A.

### Phase 3 — feedback loop (deferred)

- После race-day сохранять `Race` row (это уже есть) + сравнивать факт vs plan-corridor (% времени в коридоре, перерасход HR, фактический g/hr → estimated by activity time + body mass).
- Передавать compliance в `model_version`-следующего поколения промпта.

### Вне scope

- Shared race plans между атлетами.
- Plan generation **во время** гонки (real-time pacing) — другая задача.
- Замена существующего `suggest_race` (тот про создание goal'а, этот про execution upcoming goal'а).
- Авто-генерация race plan по cron'у — генерация только по явному запросу или 24h-pre-race trigger.

---

## 3. Data model

### Таблица `race_plans`

```sql
CREATE TABLE race_plans (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    goal_id         INTEGER REFERENCES athlete_goals(id) ON DELETE SET NULL,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    model_version   VARCHAR(64) NOT NULL,
    payload         JSONB NOT NULL
);

CREATE INDEX ix_race_plans_user_id ON race_plans(user_id);
CREATE INDEX ix_race_plans_goal_id ON race_plans(goal_id);
CREATE INDEX ix_race_plans_user_generated ON race_plans(user_id, generated_at DESC);

-- Idempotency: at most one plan per (goal_id, UTC day)
CREATE UNIQUE INDEX uq_race_plans_goal_day
    ON race_plans(goal_id, ((generated_at AT TIME ZONE 'UTC')::date))
    WHERE goal_id IS NOT NULL;
```

**Решения по схеме:**

- `goal_id` ссылается на `athlete_goals.id` (pre-race target), **не** на `races.id` (post-race log с `activity_id`). Issue spec говорил «race_id», но `races` — это таблица **завершённых** гонок. Naming следует прецеденту `races.goal_id`.
- `goal_id` nullable — для ad-hoc / experimental плана без сохранённого goal'а. Partial unique index не дедуплицирует NULL.
- `payload` JSONB (а не отдельные колонки) — schema-flexible, можно индексировать любые поля позже без миграции (например, `payload->'fueling'->>'carbs_g_per_hour'` для аналитики). Cost: schema enforcement только в коде (validator + JSON schema).
- `model_version` — провенанс сгенерированного payload'а. Phase 1 значение: `"v1-2026-05-09"`. Бамп при изменении промпта, JSON schema, или Claude модели. Позволяет таргетно регенерировать stale rows.
- `ondelete='SET NULL'` на goal_id + **inline race-block внутри `payload.race`** (см. §11.3) — plan остаётся читаемым после удаления goal'а: имя/дата/distance/ctl_target уже снэпшотнуты. Без отдельного поля `goal_snapshot` — экономия одного уровня вложенности.

### Payload shape (JSON schema)

Полная схема в `data/race_plan_service.py:_RACE_PLAN_SCHEMA` (~line 79). Ключевые поля:

```jsonc
{
  "headline": "One-sentence race-day mantra",
  "warmup": "Pre-race warmup, 2-4 sentences",
  "legs": [
    {
      "leg": "swim | T1 | bike | T2 | run | segment-1",
      "distance": "1.5 km",     // REQUIRED — атлет видит дистанцию рядом с pacing
      "pacing": {
        "low":    "2:00/100m",   // easier bound
        "target": "1:55/100m",
        "cap":    "1:50/100m"    // do-not-exceed
      },
      "hr_ceiling_bpm": 165,    // optional, omit for transitions
      "notes": "1-2 sentence cue, ≤200 chars (~25 слов) — hard cap в schema"
    }
  ],
  "fueling": {
    "carbs_g_per_hour": 75,    // 30-120 hard bounds, 60-90 default band
    "fluid_ml_per_hour": 600,
    "sodium_mg_per_hour": 500,
    "notes": "Cadence: gel every 25 min, sip every 10 min"
  },
  "transitions": [             // tri-only, omit for single-sport
    {"name": "T1", "checklist": ["..."], "target_time_sec": 75}
  ],
  "contingencies": [           // 3-5, см. confidence_tier+conditions gating
    {"scenario": "heat", "plan": "..."},
    {"scenario": "cramp", "plan": "..."},
    {"scenario": "off-pace", "plan": "..."}
  ],
  "confidence_tier": "mid"     // "final" <7d / "late" 7-14d / "mid" 14-60d / "early" 60-200d
}
```

### Schema rules — текущие (PR1)

- **`legs[].notes` maxLength=200** (~25 слов hard cap). UX: Telegram/web рендер на T2 должен быть scannable, prose-блок 200 слов не читается на race-day. Жёстче в schema надёжнее, чем «попроси Claude быть лаконичным».
- **`confidence_tier`** enum (`"final" / "late" / "mid" / "early"`) — заменяет binary `preliminary`. UI рендерит 4 разных warning. Cutoffs: <7d / 7-14d / 14-60d / 60-200d. Tier выставляется на сервере по `days_to_race`, Claude его не выбирает.
- **`contingencies` minItems=3, maxItems=5** — 3 «обязательных»-по-сути слота (heat / cramp / off-pace) + до 2 опциональных под discipline/conditions (GI, swim panic, late-race bonking, mech failure). Selection — **через system prompt** (`"pick 3-5 contingencies most relevant to this race's distance, discipline, and conditions if provided"`), не через JSON schema.

### Planned schema extensions (Phase 2.5 / Phase 3)

Из методологического review архитектора (2026-05-09):

- **`legs[].target_split_time_sec`** (Phase 2.5) — predicted split per leg. Без него у атлета на гонке нет якоря «успеваю/отстаю» в реальном времени; коридор сам по себе не даёт абсолютную ссылку.
- **`legs[].hr_segments[]`** (Phase 3) — split HR ceiling на early/mid/late (или 30/40/30) вместо одного `hr_ceiling_bpm`. HR drift на 4-часовом bike реален, единый cap либо консервативен с самого старта, либо рискован к концу.
- **Optional input `race_conditions`** в context (Phase 2.5) — `{elevation_gain_m?, expected_temp_c?}`. Pacing для плоского flat 70.3 vs горного differs на 10-15W FTP / 15-20 sec/km. Surface / water_temp / wind — Phase 3 если будет UX-ёмкость для этих полей. **Auto-fill из past `Race` row** (см. §11.10) когда возможно.

---

## 4. Flow: `generate_race_plan`

```
MCP call (or REST POST /api/race-plan/generate):
  generate_race_plan(goal_id?, dry_run=False, force_regen=False)
  ↓
1. Resolve goal (cross-tenant safe — WHERE id=? AND user_id=? in SQL,
   not Python compare after load):
   - goal_id given → SELECT scoped to user_id; not found OR foreign → "not found"
   - goal_id None  → AthleteGoal.get_by_category(user_id, "RACE_A")
  ↓
2. Idempotency pre-check (skip if dry_run OR force_regen):
   existing = RacePlan.get_today_for_goal(goal.id, user_id=user_id)
   if existing AND not force_regen → return existing payload, NO Claude call
   if existing AND force_regen → check rate limit (1/day), then UPDATE in-place
  ↓
3. Refusal gates (see §5):
   days_to_race = goal.event_date - local_today()
   if days_to_race > 200 → return {error, days_to_race}
   activities = Activity.get_range(user_id, today-6w, today)
   if len(activities) < 6 → return {error, activity_count}
  ↓
4. Build context (see §6 of methodology):
   - sport_role = _resolve_coach_role(goal.sport_type)
     ("triathlon and endurance coach" / "running coach" / "cycling coach" /
      "swim coach" / "endurance coach")
   - response_language = user.language (BCP-47, e.g. "ru" / "en")
   - event_name_safe = goal.event_name[:100]   # prompt-injection clamp
   - _summarize_activities(activities)  — per-sport aggregates + 8 long efforts
   - _summarize_zones(AthleteSettings.get_all)
   - race_day_projection from FitnessProjection.get_projection
   - latest Wellness as today-anchor

   **Planned context enrichment (Phase 2.5 — методологические gaps из architect review 2026-05-09):**
   - **Personal race history** — `Race.get_recent_for_user(user_id, sport_type=goal.sport_type, since=today-18m, limit=5)`. Для предыдущей IM 70.3 finish_time/RPE/race-day CTL/HRV — лучший единственный предиктор pacing'а на следующую IM 70.3. Сейчас Claude этого не видит → угадывает то, что уже измерено. **Самый дорогой методологический долг.** **Recency filter `≥ today − 18 months`** + cold-start fallback (если результат пуст → выдать всё что есть с пометкой «historical, may not reflect current fitness»): атлет 2 года назад был на FTP 240W, сейчас 285W — pacing с той гонки активно мисcлидит.
   - **Long-term user facts** — `list_facts(user_id, active_only=True, topics={injury, gi, nutrition, equipment, pacing, heat_response, race_history, recovery_pattern})`. **Whitelist топиков** обязателен — иначе «dog name = Rex» попадёт в системный промпт race plan'а. Whitelist стабильнее против разрастания топиков: Phase 2 экстрактор может производить факты с любыми topic, race plan читает только из своего набора. Память уже работает (USER_CONTEXT_SPEC), не подключить = выбросить персонализацию ради которой строилась.
   - **Wellness 10-14 day trend** — текущий снэпшот заменить на trajectory: HRV trend (растёт/падает), avg sleep 7d, recovery_score sequence. Атлет с CTL=80 + растущий HRV ≠ атлет с CTL=80 + упавший HRV за 3 дня до старта.
   - **Training calibration** — race-rehearsal flag в long_efforts (был ли успешный 70-80% race-pace brick), FTP/threshold-pace trajectory за 8-12 недель (растёт/плато/падает), projected race-day FTP/HRVT2 (decay из taper'а). Без этого «target sits inside last-6-week training band» — это median по разнородным сессиям.
   - **Optional `race_conditions` input** — `{elevation_gain_m, expected_temp_c}` если переданы (Phase 2.5 schema extension, см. §3). Auto-fill из past Race row для повторных гонок (см. §11.10).
  ↓
5. Claude call (claude-opus, forced tool_use=submit_race_plan):
   - system: _RACE_PLAN_SYSTEM_PROMPT.format(sport_role=…, language=…)
   - input_schema: _RACE_PLAN_SCHEMA
   - max_tokens: 2048
   - increment ApiUsageDaily on response (see §8) — even if validator rejects,
     tokens were spent
  ↓
6. Validate (see §6):
   _validate_race_plan(plan, athlete_max_hr=…, is_tri=…)
   if errors → return generic error, log details, NO persist
  ↓
7. Tag preliminary:
   plan["preliminary"] = days_to_race > 14
  ↓
8. Persist (skip if dry_run):
   - INSERT (first-of-day) OR UPDATE in-place (force_regen path).
   - IntegrityError on INSERT → another tab won the race, fall back to
     get_today_for_goal and return that row.
  ↓
9. Return:
   {id, dry_run, preliminary, model_version, payload}
```

---

## 5. Refusal gates

| Условие | Решение | Обоснование |
|---|---|---|
| Race >200 дней до старта | Refuse | `fitness_projection` decay-кривая ненадёжна на горизонте полгода+ — pacing corridor галлюцинируется. Расширено с 120д до 200д (2026-05-09): 120д блокировал реальные A-гонки в плановом окне. |
| <6 активностей за 6 недель | Refuse | Нет evidence для калибровки коридора. Возвращаем `activity_count`. |
| 14 ≤ days_to_race ≤ 200 | Generate, tag `preliminary=True` | План полезен (структура + checklist), но коридоры будут уточнены ближе к гонке. |
| <14 days_to_race | Generate, `preliminary=False` | Финальный план. |
| Goal не найден / not RACE_A/B/C | Refuse | Просим сначала создать goal через `/race`. |
| `goal_id` принадлежит другому юзеру | Refuse (`Goal {id} not found`) | Multi-tenant: leakage prevention. **SQL-scoped** — `WHERE id=? AND user_id=?` в одном SELECT, без Python-compare после load (defensive scoping vs row-level audit logs). |

---

## 6. Validator (`_validate_race_plan`)

JSON schema ловит структуру и типы. Validator ловит то, что схема не может:

1. **Pace/power corridor monotonicity.** `low < target < cap` в effort-space:
   - Pace: парсим `MM:SS/km|100m|mi` → секунды → негируем (быстрее = больше effort).
   - Power: парсим `\d+(\.\d+)? *w` → ватты as-is.
   - All-prose corridor (`low="easy", target="tempo", cap="threshold"`) → **skip** (false-reject ломает иначе валидный plan).
   - **Mixed numeric + prose** в одном корридоре → reject. Это та самая failure mode, ради которой validator существует: `low="5:30/km", target="5:00/km", cap="threshold pace"` структурно правдоподобно, но физиологически нонсенс. Раньше silently passed (см. H2 fix 2026-05-09).
   - **Mixed units** (pace + power) → reject — `{units} != 1` после успешного парсинга.
2. **HR ceiling vs athlete `max_hr`.** Если в zones есть `max_hr`, ceiling > `max_hr + 5` → reject.
3. **Transitions iff is_tri.** `transitions[]` непустой для не-триатлона (sport_type ∉ {triathlon, duathlon, aquathlon}) → reject. Симметрично: транзиций нет в plan'е для триатлона → warning в логах (не reject — допустим minimalist plan).

При наличии errors — generic error → user, детали → логи. **Не персистим.**

### System-prompt rules (PR1 — soft constraints, не validator)

Правила, которые validator не enforce'ит — они в `_RACE_PLAN_SYSTEM_PROMPT`. Принцип: **правила сначала в prompt, validator только если Claude систематически нарушает** (KISS — bias toward observation первый, чтобы validator не превратился в rules-engine с растущим false-reject risk).

PR1 добавляет три правила в system prompt:

1. **🔥 Bike→Run constraint (для триатлона):** `"For triathlon races, bike NP cap MUST be calibrated to the run goal. Strong run goal → bike cap 75-78% FTP. Conservative run goal → bike cap 70-72% FTP. Independent leg corridors that would cumulatively destroy the run are unacceptable."` — кардинальное правило IM 70.3 pacing. В validator пока **не** дублируем (rules-engine drift): сначала наблюдаем, переносим в hard check только если Claude игнорирует.

2. **Negative-split run в триатлоне:** `"For run leg in triathlon, hr_ceiling_bpm in the first 1/3 of the leg MUST NOT exceed Z2-high. Marathon-segment of IM is where most of finish-time is realised — heroic-start plans break the race."` — sport-specific, но методологически устойчивое правило. Аналогично — пока в prompt, не в validator.

3. **Contingencies relevance gating:** `"Pick 3-5 contingencies most relevant to this race's distance, discipline, and conditions. Heat scenario when expected_temp_c < 18 is wasted attention; mech failure for swim-only is nonsense. Default trio (heat / cramp / off-pace) is a starting point, not a quota."`

### Что validator пока **не ловит** (deferred → Phase 2.5)

Hard structural checks, ROI-ranked:

- **Sum of `legs[].distance` ≈ race total** (parsed → meters). 20 строк кода, ловит реальные косяки.
- **Fueling × duration sanity**: `carbs_g_per_hour * race_duration_h` ∈ [100, 1500]. Ловит «30 г/час на 8 часов IM» (голод) и «120 г/час на 1 час спринта» (overload).
- **Per-leg duration plausibility** при наличии goal_time — ловит «swim 21 km».
- **Each leg.notes references at least one zone/threshold name** (groundedness signal — отсекает generic-tips).
- **Bike→run + negative-split — ESCALATE из system prompt в validator** только если из feedback loop (§14) видно что Claude игнорирует правила.

---

## 7. Idempotency & invalidation

**Default flow (без force_regen):** при наличии `RacePlan` для `(goal_id, current_UTC_day)` — возвращаем существующий, Claude не вызываем. Race condition между pre-check и INSERT — `IntegrityError` ловится, fallback на `get_today_for_goal`.

**Regenerate flow (`force_regen=True`, Phase 2 — кнопка в UI):**
- **In-place UPDATE** того же row (preserve `id`, перезапись `payload` + `model_version` + `generated_at`). ID стабилен — внешние ссылки (Telegram-сообщения с deep-link на plan_id) не ломаются.
- **Rate limit: 1 regen в день** на `(user_id, goal_id)`. Совпадает с unique-индексом (UTC-day) — естественный backoff. Реализация: проверять перед Claude call что для существующего row `generated_at < today_utc_start + 24h` AND `user не вызывал force_regen сегодня` (sliding в Redis или счётчик в `payload.regen_count_today`).
- **HTTP 429** на rate-limit hit с `retry_after_sec` — UI показывает «Regenerated already today, next available at HH:MM».
- При rate-limited regen — `payload` НЕ перезаписывается, прежний остаётся.

**Известные ограничения (accepted trade-offs):**

1. **UTC-день, не локальный.** Атлет в UTC+12 генерирует в 23:00 локально, через 5 часов — в 04:00 локально. Это разные UTC-дни → дубль проскочит. Принято как trade-off для Phase 1-2 — alternatives (PG-side `AT TIME ZONE user.tz` в индексе, или `generated_local_date DATE` колонка) усложняют код ради edge-кейса.
2. **Авто-инвалидация по hash контекста — отложено.** За день атлет добавит активность / поменяет zones / придёт новая `fitness_projection` — `get_today_for_goal` вернёт устаревший plan. Кнопка regen решает 80% случаев. Hash в `payload.context_hash` (`(goal.event_date, max(activities.updated_at), max(athlete_settings.updated_at), fitness_projection.updated_at)`) с авто-trigger на mismatch — Phase 3 если поступят жалобы.

---

## 8. Cost & API tracking

`generate_race_plan` делает 1 Claude call (**claude-opus**, ~2000 input + ~1500 output токенов на typical контекст — Opus генерирует чуть длиннее sonnet'а на той же задаче). Pre-check + idempotency спасает от повторных вызовов в один день; force_regen rate-limit (см. §7) — от спама.

✅ **`ApiUsageDaily.increment` после каждого Claude call** (включая случай когда validator потом отрефьюзил — токены уже потрачены). Поля: `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens` из `resp.usage`. Failure при инкременте — warning в логах, не ломает основной flow.

---

## 9. Multi-tenant isolation

- `user_id` берётся из contextvars (`get_current_user_id()`), не из параметра тула.
- `RacePlan.get_today_for_goal` / `get_latest_for_race` принимают `user_id` обязательным kwarg'ом и фильтруют по нему **поверх** `goal_id` (defence-in-depth: leaked goal_id не должен ломать tenant boundary).
- **Goal resolve по `goal_id` — фильтр в SQL** (`SELECT … WHERE id=? AND user_id=?`), не Python-compare после `session.get()`. Иначе row-level audit logs / read-only replicas видят успешный cross-tenant SELECT даже если приложение его потом отбросит. См. C3 fix 2026-05-09.
- `goal.user_id != user_id` (или строка не нашлась) → refuse «Goal not found» (не «forbidden» — не подсвечиваем существование чужих goals).

---

## 10. Tests

### `tests/db/test_race_plan.py` (11 тестов)

- `save` round-trip.
- `get_today_for_goal` — попадание / промах по дню.
- `get_latest_for_race` — order by `generated_at DESC`.
- `user_id` scope — SQL inspection: `WHERE user_id = ?` присутствует.
- `get_for_user_recent` — `limit` пробрасывается.

### `tests/mcp/test_races.py` (12 тестов на `generate_race_plan` после 2026-05-09)

- Refuse — нет RACE_A goal.
- Refuse — race >200d out (assert no Anthropic call made).
- Refuse — <6 активностей.
- **Cross-tenant `goal_id` → "not found" + assert no further DB work + assert `user_id` filter присутствует в SQL** (C3 test).
- Dry-run happy path — `anthropic.AsyncAnthropic` patched, assert `RacePlan.save` НЕ вызван.
- No-tool_use-block fallback — Claude вернул prose без tool_use.
- Validator unit-tests:
  - Inverted pace corridor.
  - Inverted power corridor.
  - HR ceiling > `max_hr + 5`.
  - Unparseable (all-prose) corridor strings — false-reject guard.
  - **Mixed numeric+prose corridor → reject** (H2 test).
  - **Mixed units (pace+power) → reject** (H2 test).

Test fixtures используют `MagicMock(spec=AthleteGoal)` (не plain `SimpleNamespace`) — обращение к dropped-колонкам теперь падает в тестах, а не только в проде (regression-guard от C1).

### Чего нет (gaps — Phase 2/3)

- E2e тест с моком реального Anthropic-ответа полной формы (всё, что есть — это unit-тест валидатора, не контракт с Claude).
- Тест preliminary-tag для `14 < days_to_race <= 200`.
- Тест fallback на `IntegrityError` (другая вкладка победила race condition).
- Тесты regen flow (in-place UPDATE, rate-limit hit → 429).
- Тесты sport-specific coach role + language pass-through.

---

## 11. Open issues / production readiness

### 11.1 ❌ Allowlist gate — отброшен (2026-05-09)

Раньше спека требовала env-var allowlist `RACE_PLAN_USER_IDS` для coaching-adjacent surface'а. **Решение пересмотрено:**
- Owner — единственный реальный consumer на Phase 1-2 (multi-tenant Phase 2 ещё не опубликован).
- Защита от плохих советов уже даёт **валидатор** (corridor monotonicity + mixed-units + HR ceiling vs max_hr+5 + transitions iff is_tri) и **JSON schema** (carbs 30-120 hard bounds).
- Allowlist = config-overhead, который сам же ослабляешь когда добавляешь других тенантов.

Если в Phase 3+ откроем для нескольких тенантов и появится medical-liability surface — вернуться к gate'у, либо заменить на role-based check (`User.role in {"owner", "premium"}`).

### 11.2 Idempotency — regen реализован, авто-инвалидация отложена

См. §7.
- ✅ Force-regenerate через UI кнопку — in-place UPDATE того же row, rate-limit 1/день.
- ❌ Hash-based авто-инвалидация (mismatch в `payload.context_hash` → auto-regen) — Phase 3 если жалобы.
- UTC-день в индексе вместо локального — accepted trade-off.

### 11.3 ✅ goal_snapshot — accepted as inline race-block

Phase 1 пишет `payload.race = {id, name, date, days_to_race, discipline, is_triathlon, ctl_target, preliminary}` — это de facto snapshot. После `goal_id NULL`-ификации (cascade delete от `athlete_goals`) plan остаётся читаемым: имя/дата/distance в `payload.race`. Не выносим в отдельное `payload.goal_snapshot` — экономим уровень вложенности.

### 11.4 Validator расширение (deferred → Phase 2.5)

См. §6 — полный список deferred проверок там. Что в Phase 1 уже добавлено (✅): mixed-numeric+prose, mixed-units, transitions iff is_tri.

**PR1 кладёт rules в system prompt, не в validator** (bike→run constraint, negative-split run, contingencies relevance gating). Escalate в validator только если feedback loop (§14) покажет систематическое нарушение Claude'ом. KISS-подход — каждый rules-check в validator увеличивает false-reject risk, и validator уже становится cross-leg / per-leg-segment лесом.

### 11.5 ✅ API cost tracking — done

См. §8. `ApiUsageDaily.increment` после каждого Claude call (включая validation-fail случай).

### 11.6 No fallback on Claude failure

`suggest_workout` имеет template fallback при ошибке Claude. Здесь — только generic error → юзер. Опции:
- Skeleton fallback с базовыми коридорами на основе zones (без AI-prose).
- Хардовое «Try again in 5 minutes» — приемлемо для V0/V1.

### 11.7 Бандлинг с END-95/END-98 (historical)

В одном merge commit (1856fa9) — END-63 (700 строк), END-95 (5 строк whitelist), END-98 (3 строки cron). Нельзя откатить независимо. **На будущее:** один коммит = один PR.

### 11.8 ✅ Service-extraction (done)

Business logic вынесена из MCP tool в `data/race_plan_service.py:build_race_plan`. MCP wrapper в `mcp_server/tools/races.py:624` теперь ~30 строк. REST endpoint и MCP tool делят один code path.

### 11.9 Race-week Q&A через chat (deferred → PR4)

Plan one-shot. Решение — расширить chat-системник (`bot/prompts.py:get_system_prompt_chat`) инжектом `{active_race_plans_block}` если у юзера есть `RacePlan` для goal'а в окне ±14d от event_date. Атлет цитирует leg → Claude видит план в системнике → объясняет. Context expansion, ~30 строк. Defer до PR4 (после реальных гонок Радика — пока не ясно, нужна ли фича).

### 11.10 Course inheritance UX — partial done

✅ **Inherit-from-past UI selector** — `RaceConditionsForm.tsx` с dropdown'ом последних 5 same-sport `Race`-rows через `GET /api/race-plan/inheritable-conditions`. `parseTempFromWeather` вытаскивает temp из freeform `Race.weather`.
❌ **Auto-fill from past Race row** на сервере (без явного UI-выбора) — deferred → PR4 если будет boilerplate complaints.

### 11.11 Geo source for location/weather (issue #331)

Сейчас `race_conditions` — manual entry. См. [issue #331](https://github.com/radikkhaziev/triathlon-agent/issues/331) — три tier'а апгрейда (Intervals event.location, manual `goal.location` field, full geocode + forecast). Не блокирует ничего; revisit если manual input систематически забывается.

---

## 12. Migration path (rollout plan)

Sequencing согласован с архитектором (2026-05-09):

1. ✅ **Phase 1 merge** — backend (без allowlist gate, см. §11.1).

2. ✅ **PR1 — methodology core** (commit `1d68ca6`): race history + user_facts whitelist + bike→run/negative-split/contingencies-gating prompt rules + validator wins (distance sum, fueling × duration, leg duration plausibility) + schema (`legs[].notes` maxLength, `confidence_tier` enum).

3. ✅ **PR2 — surface + course/conditions** (commit `1d68ca6`): service-extraction + REST endpoints + Goal-tab UI + regenerate (in-place UPDATE, 1/day rate limit) + 24h pre-race push (cron + actor + renderer) + `race_conditions` input + course inheritance UX. ❌ ~~`/raceplan` slash command~~ dropped.

4. ✅ **PR3 — Phase 3 metrics shape define-not-ship** (commit `1d68ca6`): `race_plan_compliance` schema + `Race.carbs_consumed_g` + `compute_compliance` writer-stub (`data/race_plan_compliance_service.py:130`). NO auto-trigger / actor / dashboards.

5. ❌ **Радик прогоняет 2 ближайшие гонки** (Ironman 70.3 / Oceanlava) — runtime feedback. Pending.

6. ❌ **PR4 — Phase 2.5 enrichment** (после feedback от реальных гонок):
   - Wellness 10-14d trend вместо снэпшота.
   - Training calibration (race-rehearsal flag, FTP trajectory).
   - Predicted splits per leg (`target_split_time_sec`).
   - Race-week Q&A через chat (§11.9).
   - Auto-fill `race_conditions` from past Race row (§11.10 server-side).
   - Escalate prompt rules → validator if feedback shows systematic breach.

7. ❌ **Phase 3 feedback loop** — compliance metrics actor (auto on `ACTIVITY_UPLOADED`) + hash-based авто-инвалидация (§11.2) + HR per-segment ceiling.

8. ❌ **Multi-tenant rollout** (если/когда) — re-evaluate allowlist/role gate (§11.1).

---

## 13. Schema versioning policy

Бамп `RACE_PLAN_MODEL_VERSION` (`data/race_plan_service.py:52`) при:
- Изменении JSON schema (`_RACE_PLAN_SCHEMA`).
- Изменении system prompt (`_RACE_PLAN_SYSTEM_PROMPT_TEMPLATE`).
- Смене Claude модели (текущая: **claude-opus**, bump 2026-05-09 с claude-sonnet-4-6).

Старые rows остаются read-only с прежним `model_version`. При изменении breaking — миграция: nightly actor regenerates plans where `model_version != latest AND goal.event_date >= today` (TODO Phase 3).

---

## 14. ✅ Phase 3 compliance metrics — shape defined (PR3, commit `1d68ca6`)

> Schema + writer-stub shipped: `data/race_plan_compliance_service.py:130 compute_compliance`. NO auto-trigger / actor / dashboards (those are Phase 3 proper). Below — durable methodology + persistence contract.

> **Зачем сейчас:** PR2 запускает 24h pre-race push и data collection начинается с реальных гонок. Если шейп compliance metrics определять задним числом (после первой гонки) — переписывать activity-laps парсер, retroactively size'ить колонки, и часть метрик будет пропущена для уже отработанных гонок. **Define-not-ship:** в PR3 фиксируем schema + writer-stub, actor + dashboards уже в Phase 3.

### Три compliance метрики (минимальный набор для ML loop'а)

Все три считаются per leg на post-race данных (activity laps + body mass + plan payload):

#### 14.1 HR-corridor compliance per leg

```
hr_compliance_pct = time_in_seconds(hr ≤ leg.hr_ceiling_bpm) / leg_duration_seconds * 100
```

Нужны **per-second HR samples** из activity (есть в Intervals.icu detail). Per-leg сегментация — по `lap_indexes` или time windows из leg.target_split_time_sec (PR4 schema extension). Для PR3 — fallback на whole-activity HR vs whole-leg ceiling.

#### 14.2 Pace/power band compliance per leg

```
band_compliance_pct = time_in_seconds(low ≤ pace_or_power ≤ cap) / leg_duration_seconds * 100
```

Те же source data (per-second pace/power из activity). Парсим `leg.pacing.{low, cap}` теми же regex что validator (§6 corridor monotonicity).

#### 14.3 Fueling compliance

```
estimated_g_per_hour = (carbs_consumed_g / activity_duration_h)
fueling_compliance_pct = min(estimated, plan.carbs_g_per_hour) / plan.carbs_g_per_hour * 100
```

`carbs_consumed_g` — **manual entry** в `Race` table после гонки (новое поле — добавить колонку `carbs_consumed_g INTEGER` в PR3 миграцией). Без manual entry метрика не считается; auto-detect из swallow events на гарминах ненадёжен.

### Persistence layout

Новая таблица **`race_plan_compliance`** (PR3 миграция):

```sql
CREATE TABLE race_plan_compliance (
    id              SERIAL PRIMARY KEY,
    race_plan_id    INTEGER NOT NULL REFERENCES race_plans(id) ON DELETE CASCADE,
    race_id         INTEGER REFERENCES races(id),  -- post-race log link
    user_id         INTEGER NOT NULL,              -- denormalized for fast user-scoped reads
    leg_name        VARCHAR(32) NOT NULL,          -- "swim" / "bike" / "run" / "segment-1"
    hr_compliance_pct        NUMERIC(5,2),
    band_compliance_pct      NUMERIC(5,2),
    fueling_compliance_pct   NUMERIC(5,2),
    leg_duration_sec         INTEGER,
    notes                    TEXT,                 -- free-form (e.g. "swim leg HR sensor dropouts")
    computed_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_race_plan_compliance_user_id ON race_plan_compliance(user_id);
CREATE INDEX ix_race_plan_compliance_race_plan ON race_plan_compliance(race_plan_id);
```

### Writer-stub в PR3

В `mcp_server/tools/races.py` (или новый `data/race_plan_compliance.py`) — функция `compute_compliance(race_plan_id, race_id)` с тремя метриками. **Не auto-trigger** в PR3 — только manual вызов через CLI или admin для отладки. Auto-actor (на ACTIVITY_UPLOADED webhook когда activity matches race) — Phase 3.

### Что НЕ Phase 3 (deferred дальше)

- Сравнение нескольких planов одного athletа на one-vs-one гонки (regression: «улучшается ли execution от plan to plan?»).
- ML feedback в `_RACE_PLAN_SYSTEM_PROMPT` (compliance < threshold → bias prompt to более консервативным коридорам).
- Cross-athlete compliance benchmarking — multi-tenant требование.

---

## 15. Decisions log

Resolved findings from 6 review rounds (architect ×2 + code-review ×4) on PR1-PR3. Kept as one-liners so future-you understands the trail without diff-archaeology.

| Date | § | Decision | Why |
|---|---|---|---|
| 2026-05-09 | §2 | `/raceplan` slash command dropped from scope | Recall lives in webapp + chat AI; reduces command surface |
| 2026-05-09 | §3 | `confidence_tier` enum replaces binary `preliminary` | 4-tier UX warning vs binary; backend stamps tier from `days_to_race` |
| 2026-05-09 | §3 | `legs[].notes` maxLength=200 | Scannable on race-day phone; schema cap > prompt cajoling |
| 2026-05-09 | §4 | Sport-role + language + event_name clamp shipped | Coach voice tracks discipline; `event_name[:100]` prompt-injection guard |
| 2026-05-09 | §5 | 200d gate (was 120d) | 120d blocked real A-race planning window |
| 2026-05-09 | §6 | bike→run + neg-split + contingency-relevance live in **prompt**, NOT validator | Bias toward observation; escalate only on demonstrated drift |
| 2026-05-09 | §6 | Mixed numeric+prose corridor → reject (H2 fix) | Structurally plausible / physiologically nonsense |
| 2026-05-09 | §7 | Force-regen via in-place UPDATE + 1/day rate-limit | Keep id stable; 1/day enough for typical UX |
| 2026-05-09 | §7 | dry_run rate-limit 5/day per user via Redis (secH1) | Cost guard against `{dry_run: true}` flood; fail-open on Redis down |
| 2026-05-09 | §9 | Goal resolve via SQL `WHERE user_id=?` (C3 fix) | Row-level audit logs vs Python-compare |
| 2026-05-09 | §9 | `Race.get_recent_for_user` JOIN scopes Activity.user_id (secM1) | Defense-in-depth against stale Race FK to foreign Activity |
| 2026-05-09 | §11.1 | Allowlist gate dropped | Owner is sole consumer; validator + JSON schema sufficient |
| 2026-05-09 | §11.8 | Service extracted to `data/race_plan_service.py` | MCP tool now thin wrapper; router shares logic |
| 2026-05-09 | §11.10 | Inherit-from-past UI selector, not name-matching | Explicit user choice; no fragile fuzzy match |
| 2026-05-09 | §13 | Bumped Claude model to opus (was sonnet-4-6) | Quality > tokens for once-per-race call |
| 2026-05-09 | §14 | Define-not-ship in PR3 (schema + stub, no actor/UI) | Prevents retroactive shape rewrites on first real race data |
