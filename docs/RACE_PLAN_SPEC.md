# Race Execution Plan Spec

> Структурированный план исполнения A-гонки (warmup, pacing corridor по этапам, fueling, contingencies), который атлет читает накануне или утром в день гонки. Генерируется по запросу из данных Intervals.icu (6 недель тренировок, зоны, race-day fitness projection) через Claude с forced JSON schema; персистится в `race_plans` JSONB.

**Related:**

| Issue / Spec | Связь |
|---|---|
| END-62 (parent), END-63 (backend), END-100 (this PR) | Тикеты |
| `data/db/race_plan.py` | ORM + CRUD |
| `mcp_server/tools/races.py:generate_race_plan` | MCP tool |
| `migrations/versions/v2c3d4e5f6a7_add_race_plans.py` | Schema |
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

### Phase 1 (MVP, текущий PR) — backend foundation

- Таблица `race_plans` + ORM + миграция.
- MCP tool `generate_race_plan(goal_id?, dry_run=False)`.
- Forced JSON schema через Anthropic tool_use (`submit_race_plan`).
- Refusal gates: <6 активностей за 6 недель / >120 дней до гонки.
- Defensive validator (corridor monotonicity, HR ceiling vs `max_hr+5`).
- Idempotency: 1 plan на (goal_id, UTC-день).
- **Фича-флаг или allowlist `user_id=1`** (см. §11) — coaching surfaces без врачебной валидации не выкатываем на всех.

### Phase 2 — surface

- Telegram command `/raceplan` — рендер последнего plan'а в человекочитаемом виде, кнопка «🔄 Перегенерировать» (с ratelimit).
- Webapp page `/race-plan` или секция на `/dashboard` (Goal tab, если активная RACE_A) — карточки по этапам, fueling cadence, contingencies.
- Push в Telegram **за 24h до гонки** (cron, проверяет `goal.event_date - today == 1`).

### Phase 3 — feedback loop

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
- `model_version` — провенанс сгенерированного payload'а (`"v0-2026-04-30"`). Бамп при изменении промпта или JSON schema. Позволяет таргетно регенерировать stale rows когда выкатывается новая версия.
- `ondelete='SET NULL'` на goal_id — **обсуждаемо**. Plan без goal — мусор без `event_date`/`distance`. Альтернатива: `ondelete='CASCADE'`, или snapshot полей goal'а внутрь `payload.goal_snapshot` (см. §11.4).

### Payload shape (JSON schema)

Полная схема в `mcp_server/tools/races.py:_RACE_PLAN_SCHEMA`. Ключевые поля:

```jsonc
{
  "headline": "One-sentence race-day mantra",
  "warmup": "Pre-race warmup, 2-4 sentences",
  "legs": [
    {
      "leg": "swim | T1 | bike | T2 | run | segment-1",
      "distance": "1.5 km",
      "pacing": {
        "low":    "2:00/100m",   // easier bound
        "target": "1:55/100m",
        "cap":    "1:50/100m"    // do-not-exceed
      },
      "hr_ceiling_bpm": 165,    // optional, omit for transitions
      "notes": "1-2 sentence cue tied to athlete data"
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
  "contingencies": [           // exactly 3: heat, cramp, off-pace
    {"scenario": "heat", "plan": "..."},
    {"scenario": "cramp", "plan": "..."},
    {"scenario": "off-pace", "plan": "..."}
  ],
  "preliminary": false         // true когда days_to_race > 14
}
```

---

## 4. Flow: `generate_race_plan`

```
MCP call: generate_race_plan(goal_id?, dry_run=False)
  ↓
1. Resolve goal:
   - goal_id given → AthleteGoal.get(goal_id), check user_id ownership
   - goal_id None  → AthleteGoal.get_by_category(user_id, "RACE_A")
   - not found → return {error: ...}
  ↓
2. Idempotency pre-check (skip if dry_run):
   existing = RacePlan.get_today_for_goal(goal.id, user_id=user_id)
   if existing → return existing payload, NO Claude call
  ↓
3. Refusal gates:
   days_to_race = goal.event_date - today
   if days_to_race > 120 → return {error, days_to_race}
   activities = Activity.get_range(user_id, today-6w, today)
   if len(activities) < 6 → return {error, activity_count}
  ↓
4. Build context:
   - _summarize_activities(activities)  — per-sport aggregates + 8 long efforts
   - _summarize_zones(AthleteSettings.get_all)
   - race_day_projection from FitnessProjection.get_projection
   - latest Wellness as today-anchor
  ↓
5. Claude call (sonnet-4-6, forced tool_use=submit_race_plan):
   - system: _RACE_PLAN_SYSTEM_PROMPT
   - input_schema: _RACE_PLAN_SCHEMA
   - max_tokens: 2000
  ↓
6. Validate:
   _validate_race_plan(plan, athlete_max_hr=...)
   if errors → return generic error, log details, NO persist
  ↓
7. Tag preliminary:
   plan["preliminary"] = days_to_race > 14
  ↓
8. Persist (skip if dry_run):
   RacePlan.save(user_id, goal_id, model_version, payload)
   IntegrityError → another tab won the race, fall back to get_today_for_goal
  ↓
9. Return:
   {id, dry_run, preliminary, model_version, payload}
```

---

## 5. Refusal gates

| Условие | Решение | Обоснование |
|---|---|---|
| Race >120 дней до старта | Refuse | `fitness_projection` decay-кривая ненадёжна за 4 месяца — pacing corridor галлюцинируется. |
| <6 активностей за 6 недель | Refuse | Нет evidence для калибровки коридора. Возвращаем `activity_count`. |
| 14 ≤ days_to_race ≤ 120 | Generate, tag `preliminary=True` | План полезен (структура + checklist), но коридоры будут уточнены ближе к гонке. |
| <14 days_to_race | Generate, `preliminary=False` | Финальный план. |
| Goal не найден / not RACE_A/B/C | Refuse | Просим сначала создать goal через `/race`. |
| `goal_id` принадлежит другому юзеру | Refuse (`Goal {id} not found`) | Multi-tenant: leakage prevention. |

---

## 6. Validator (`_validate_race_plan`)

JSON schema ловит структуру и типы. Validator ловит то, что схема не может:

1. **Pace/power corridor monotonicity.** `low < target < cap` в effort-space:
   - Pace: парсим `MM:SS/km|100m|mi` → секунды → негируем (быстрее = больше effort).
   - Power: парсим `\d+(\.\d+)? *w` → ватты as-is.
   - Mixed units (pace + power в одном корридоре) → reject.
   - Unparseable значения (`"easy"`) → **skip** (false-reject ломает иначе валидный plan).
2. **HR ceiling vs athlete `max_hr`.** Если в zones есть `max_hr`, ceiling > `max_hr + 5` → reject.

При наличии errors — generic error → user, детали → логи. **Не персистим.**

### Что validator пока **не ловит** (см. §11.5)

- Fueling carbs vs race duration consistency (30 g/hr × 8h IM = голод).
- Sum of leg distances ≈ race total distance.
- Distance plausibility (swim leg «21 km»).
- Transitions присутствуют для tri / отсутствуют для single-sport.

---

## 7. Idempotency & invalidation

**Текущее (Phase 1):** при наличии `RacePlan` для `(goal_id, current_UTC_day)` — возвращаем существующий, Claude не вызываем. Race condition между pre-check и INSERT — `IntegrityError` ловится, fallback на `get_today_for_goal`.

**Известные проблемы:**

1. **UTC-день, не локальный.** Атлет в UTC+12 генерирует в 23:00 локально, через 5 часов — в 04:00 локально. Это разные UTC-дни → дубль проскочит. Решения:
   - (a) Принять как trade-off, документировать.
   - (b) Парт-индекс по `(generated_at AT TIME ZONE user.tz)::date` — нужно тянуть `users.timezone` в condition (PG не любит non-IMMUTABLE expressions в индексах).
   - (c) Хранить `generated_local_date DATE` колонку, заполнять триггером / в коде.
2. **Не инвалидируется при обновлении данных.** За день атлет добавит активность / поменяет zones / придёт новая `fitness_projection` — `get_today_for_goal` вернёт устаревший plan. Опции:
   - (a) Хеш `(goal.event_date, max(activities.updated_at), max(athlete_settings.updated_at), fitness_projection.updated_at)` сохранять в `payload.context_hash`. При запросе сравнивать → mismatch ⇒ regen.
   - (b) Кнопка «🔄 Force regenerate» в UI с rate-limit.
   - Идём с (b) для Phase 2 surface; (a) если будет жалоба.

---

## 8. Cost & API tracking

`generate_race_plan` делает 1 Claude call (sonnet-4-6, ~2000 input + ~1000 output токенов на typical контекст). Pre-check + idempotency спасает от повторных вызовов в один день.

**TODO:** инкрементить `api_usage_daily` (как `compose_workout` делает) — на воркер фан-аут это дорого, без трекинга утечка незаметна. См. `mcp_server/tools/workouts.py:compose_workout` для образца.

---

## 9. Multi-tenant isolation

- `user_id` берётся из contextvars (`get_current_user_id()`), не из параметра тула.
- `RacePlan.get_today_for_goal` / `get_latest_for_race` принимают `user_id` обязательным kwarg'ом и фильтруют по нему **поверх** `goal_id` (defence-in-depth: leaked goal_id не должен ломать tenant boundary).
- `goal.user_id != user_id` → refuse «Goal not found» (не «forbidden» — не подсвечиваем существование чужих goals).

---

## 10. Tests

### `tests/db/test_race_plan.py` (11 тестов)

- `save` round-trip.
- `get_today_for_goal` — попадание / промах по дню.
- `get_latest_for_race` — order by `generated_at DESC`.
- `user_id` scope — SQL inspection: `WHERE user_id = ?` присутствует.
- `get_for_user_recent` — `limit` пробрасывается.

### `tests/mcp/test_races.py` (11 тестов на `generate_race_plan`)

- Refuse — нет RACE_A goal.
- Refuse — race >120d out (assert no Anthropic call made).
- Refuse — <6 активностей.
- Dry-run happy path — `anthropic.AsyncAnthropic` patched, assert `RacePlan.save` НЕ вызван.
- No-tool_use-block fallback — Claude вернул prose без tool_use.
- Validator unit-tests:
  - Inverted pace corridor.
  - Inverted power corridor.
  - HR ceiling > `max_hr + 5`.
  - Unparseable corridor strings — false-reject guard.

### Чего нет (gaps)

- E2e тест с моком реального Anthropic-ответа полной формы (всё, что есть — это unit-тест валидатора, не контракт с Claude).
- Тест preliminary-tag для `14 < days_to_race <= 120`.
- Тест fallback на `IntegrityError` (другая вкладка победила race condition).

---

## 11. Open issues / production readiness

### 11.1 🚨 Coaching surfaces require validation

Pacing corridor / HR ceiling / fueling — это **medical-adjacent advice**. Ошибка может:
- Сорвать гонку (мягкий случай).
- Вызвать heat-illness / cardiac event (тяжёлый случай).

PR-описание сам коммит END-63 говорит: «Coaching-advice surfaces still need CTO sign-off». До этого — env-var allowlist (паттерн уже использован для Strava signature в `config.py:STRAVA_SIGNATURE_USER_IDS` + `_dispatch_activity_uploaded`):

```python
# config.py
RACE_PLAN_USER_IDS: set[int] = {1}

@field_validator("RACE_PLAN_USER_IDS", mode="before")
@classmethod
def _parse_user_id_set(cls, v):
    if isinstance(v, str):
        return {int(x) for x in v.split(",") if x.strip()}
    return v
```

Гейт **внутри тула** (а не в webhook'е, как для Strava signature), потому что `generate_race_plan` вызывается явно из Claude — точка решения там же:

```python
# mcp_server/tools/races.py:generate_race_plan
if user_id not in settings.RACE_PLAN_USER_IDS:
    return {"error": "Race plan is in private beta."}
```

Это даёт Радику обкатать на 2 ближайших гонках, потом снимаем гейт + добавляем surface.

### 11.2 Idempotency gracile gaps

См. §7. Решение: для Phase 1 — оставить UTC-день как есть, документировать; добавить «🔄 Force regenerate» button в Phase 2 surface, со sliding rate-limit (например, 3/час).

### 11.3 `ondelete='SET NULL'` — спорный выбор

Plan без goal — мусор без `event_date`/`distance`. Варианты:
- `CASCADE` — удаление goal'а удаляет историю planов.
- Snapshot `event_date`/`distance_m`/`goal_time_sec` в `payload.goal_snapshot` при `save` — план остаётся читаемым даже после удаления goal'а.

Рекомендация: **snapshot в payload** (lowest churn, plan становится self-contained документом).

### 11.4 Validator расширение

См. §6. Добавить:
- Sum of `legs[].distance` ≈ race total (parsed → meters).
- Fueling × duration sanity: `carbs_g_per_hour * race_duration_h` → check 100-1500g range.
- Transitions present iff sport ∈ {triathlon, duathlon, aquathlon}.
- Each leg.notes references at least one zone/threshold name (groundedness signal).

### 11.5 API cost tracking

См. §8. Инкремент `api_usage_daily` на каждый успешный Claude call.

### 11.6 No fallback on Claude failure

`suggest_workout` имеет template fallback при ошибке Claude. Здесь — только generic error → юзер. Опции:
- Skeleton fallback с базовыми коридорами на основе zones (без AI-prose).
- Хардовое «Try again in 5 minutes» — приемлемо для V0.

### 11.7 Бандлинг с END-95/END-98

В одном merge commit (1856fa9) — END-63 (700 строк), END-95 (5 строк whitelist), END-98 (3 строки cron). Нельзя откатить независимо. **На будущее:** один коммит = один PR.

---

## 12. Migration path (rollout plan)

1. **Phase 1 merge** — backend + private beta gate (user_id=1).
2. **Радик генерит plan на свои 2 ближайшие гонки**, проходит ими, отдаёт feedback по структуре / точности коридоров / fueling.
3. **CTO/coach sign-off** на промпт + JSON schema.
4. **Phase 2 surface** — `/raceplan` + webapp page.
5. **24h pre-race push** — cron, идемпотентность по `goal_id`.
6. **Снятие user_id gate** — feature flag → on.
7. **Phase 3 feedback loop** — post-race compliance metrics.

---

## 13. Schema versioning policy

Бамп `RACE_PLAN_MODEL_VERSION` (`mcp_server/tools/races.py`) при:
- Изменении JSON schema (`_RACE_PLAN_SCHEMA`).
- Изменении system prompt (`_RACE_PLAN_SYSTEM_PROMPT`).
- Смене Claude модели (`claude-sonnet-4-6` → next).

Старые rows остаются read-only с прежним `model_version`. При изменении breaking — миграция: nightly actor regenerates plans where `model_version != latest AND goal.event_date >= today` (TODO Phase 2).
