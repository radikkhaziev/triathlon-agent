# Race Execution Plan Spec

> Структурированный план исполнения A-гонки (warmup, pacing corridor по этапам, fueling, contingencies). Атлет читает накануне / утром в день гонки. Генерируется по запросу из данных Intervals.icu (6 недель тренировок, зоны, race-day fitness projection) через Claude с forced JSON schema; персистится в `race_plans` JSONB.

**Status:** PR1 + PR2.1-PR2.6 + PR3 ✅ shipped (commit `1d68ca6`, 2026-05-09). 149 backend tests + TS clean. Runtime feedback (Радик's 2 предстоящих гонки) — pending; PR4 (Phase 2.5 enrichment) / Phase 3 actor / multi-tenant rollout — deferred.

**Related:**

| Issue / Spec | Связь |
|---|---|
| END-62 (parent), END-63 (Phase 1), END-100+ (Phase 2) | Тикеты |
| Issue #331 | Geo source upgrade (location/weather) — §11.11 |
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

---

## 1. Мотивация

Любительские A-гонки чаще теряются на **execution failure** — стартанул на 10 уд/мин выше threshold, выпил один гель за весь IM-марафон, на жаре не сменил cap-стратегию. У нас есть все вводные (`athlete_goals`, последние 6 недель activities, per-sport zones, `fitness_projection` decay-кривая, текущий `wellness`). Остаётся попросить Claude собрать в structured plan и сохранить.

---

## 2. Scope

### ✅ Phase 1 (MVP) — backend foundation

`race_plans` table + ORM + migration, MCP tool `generate_race_plan(goal_id?, dry_run=False)`, forced JSON schema через Anthropic tool_use (`submit_race_plan`), refusal gates (§5), defensive validator (§6), idempotency 1/day, cost tracking via `ApiUsageDaily.increment`. Allowlist gate — отброшен (§11.1).

### ✅ Phase 2 — surface (commit `1d68ca6`)

REST endpoints, service-extraction в `data/race_plan_service.py:build_race_plan`, MCP wrapper остался ~30 строк, Webapp UI (`RacePlanPanel` + `RaceConditionsForm` с inherit-from-past dropdown), regenerate via in-place UPDATE (1/day rate limit, HTTP 429 + Retry-After), dry_run rate limit 5/day per user via Redis (secH1 fix), 24h pre-race push (cron 08:00 Belgrade, idempotency via `payload.pushed_for_race_date`). `/raceplan` slash command — dropped (Decisions log).

### Phase 3 — feedback loop (deferred)

После race-day сохранять `Race` row + сравнивать факт vs plan-corridor (% времени в коридоре, HR overshoot, fueling actual). Передавать compliance в `model_version`-следующего поколения промпта. Schema + writer-stub `compute_compliance` shipped в PR3, actor + dashboards — Phase 3 proper (§14).

### Вне scope

- Shared race plans между атлетами.
- Plan generation **во время** гонки (real-time pacing) — другая задача.
- Замена `suggest_race` (тот про goal creation, этот про execution).
- Авто-генерация race plan по cron'у — только по явному запросу или 24h-pre-race trigger.

---

## 3. Data model

Schema: `migrations/versions/c3c3d4e5f6a7_add_race_plans.py`. Ключевые решения:

- **`goal_id` → `athlete_goals.id`** (pre-race target), не `races.id` (post-race log).
- **`goal_id` nullable**, `ondelete='SET NULL'` + inline race-block в `payload.race` (§11.3) — plan остаётся читаемым после удаления goal'а.
- **Partial unique index** `(goal_id, UTC-day) WHERE goal_id IS NOT NULL` — идемпотентность same-day generation.
- **`payload` JSONB** — schema-flexible, индексируем по конкретным полям позже без миграции; enforcement в коде (validator + JSON schema).
- **`model_version`** провенанс. Bump при изменении промпта / JSON schema / Claude модели (текущая `claude-sonnet-4-6`).

### Payload shape

Полная схема в `data/race_plan_service.py:_RACE_PLAN_SCHEMA` (~line 79). Ключевые поля:

```jsonc
{
  "headline": "One-sentence race-day mantra",
  "warmup": "Pre-race warmup, 2-4 sentences",
  "legs": [
    {
      "leg": "swim | T1 | bike | T2 | run | segment-1",
      "distance": "1.5 km",      // REQUIRED
      "pacing": {"low": "...", "target": "...", "cap": "..."},
      "hr_ceiling_bpm": 165,     // optional, omit for transitions
      "notes": "≤200 chars hard cap"
    }
  ],
  "fueling": {"carbs_g_per_hour": 75, "fluid_ml_per_hour": 600, "sodium_mg_per_hour": 500, "notes": "..."},
  "transitions": [...],           // tri-only, omit for single-sport
  "contingencies": [...],         // 3-5, см. confidence_tier+conditions gating
  "confidence_tier": "mid"        // "final" <7d / "late" 7-14d / "mid" 14-60d / "early" 60-200d
}
```

Hard bounds: `carbs_g_per_hour 30-120`, `legs[].notes ≤200 chars` (~25 слов; scannable on race-day phone), `contingencies` 3-5 items (selection через system prompt по distance/discipline/conditions).

### Planned schema extensions (Phase 2.5 / Phase 3)

Из методологического review (2026-05-09):

- **`legs[].target_split_time_sec`** (Phase 2.5) — predicted split per leg. Без него у атлета на гонке нет якоря «успеваю/отстаю».
- **`legs[].hr_segments[]`** (Phase 3) — split HR ceiling на early/mid/late вместо одного `hr_ceiling_bpm`. HR drift на 4h bike реален.
- **Optional `race_conditions`** в context (Phase 2.5) — `{elevation_gain_m?, expected_temp_c?}`. Pacing на flat 70.3 vs горном differs на 10-15W FTP / 15-20 sec/km. Auto-fill из past Race row (§11.10) когда возможно.

---

## 4. Flow

Подробный псевдокод — `data/race_plan_service.py:build_race_plan`. Конспект:

1. **Resolve goal** — `goal_id` given → SQL `SELECT … WHERE id=? AND user_id=?` (cross-tenant safe, не Python-compare); `goal_id` None → `AthleteGoal.get_by_category(user_id, "RACE_A")`.
2. **Idempotency pre-check** (skip if dry_run / force_regen): `RacePlan.get_today_for_goal(goal.id, user_id)` → return existing payload, NO Claude call.
3. **Refusal gates** (§5).
4. **Build context** — `sport_role` from `_resolve_coach_role(goal.sport_type)`, `response_language` from `user.language`, `event_name[:100]` (prompt-injection clamp), `_summarize_activities` (per-sport aggregates + 8 long efforts), `_summarize_zones`, `race_day_projection` from `FitnessProjection.get_projection`, latest `Wellness` as today-anchor.
   - **PR4 enrichment (planned)**: personal race history (`Race.get_recent_for_user(sport_type=goal.sport_type, since=today-18m, limit=5)`), long-term user facts (`list_facts` whitelist topics: `injury, gi, nutrition, equipment, pacing, heat_response, race_history, recovery_pattern`), wellness 10-14d trend, training calibration (race-rehearsal flag, FTP trajectory).
5. **Claude call** — `claude-sonnet-4-6`, forced `tool_use=submit_race_plan`, `max_tokens=2048`. `ApiUsageDaily.increment` после ответа (включая validator-reject — токены уже потрачены).
6. **Validate** (§6) — errors → generic error → юзер, детали → логи. **Не персистим.**
7. **Tag `preliminary = days_to_race > 14`.**
8. **Persist** (skip if dry_run) — INSERT (first-of-day) OR UPDATE in-place (force_regen). `IntegrityError` на INSERT → fallback на `get_today_for_goal`.

---

## 5. Refusal gates

| Условие | Решение | Обоснование |
|---|---|---|
| Race >200 days out | Refuse | `fitness_projection` decay-кривая ненадёжна на горизонте полгода+. Расширено с 120д до 200д (2026-05-09) — 120 блокировал реальные A-гонки в плановом окне |
| <6 activities за 6 недель | Refuse | Нет evidence для калибровки коридора |
| 14 ≤ days_to_race ≤ 200 | Generate, tag `preliminary=True` | План полезен, коридоры будут уточнены ближе к гонке |
| <14 days_to_race | Generate, `preliminary=False` | Финальный план |
| Goal не найден / not RACE_A/B/C | Refuse | Просим создать goal через `/race` |
| `goal_id` принадлежит другому юзеру | Refuse «Goal not found» | Multi-tenant: SQL `WHERE id=? AND user_id=?` в одном SELECT, без Python-compare после load |

---

## 6. Validator

JSON schema ловит структуру/типы. Validator (`_validate_race_plan`) ловит то, что схема не может:

1. **Pace/power corridor monotonicity** — `low < target < cap` в effort-space (pace: `MM:SS/km|100m|mi` → секунды → negate; power: `\d+w` → watts as-is). All-prose corridor → skip (false-reject ломает иначе валидный plan). **Mixed numeric+prose в одном корридоре → reject** (H2 fix 2026-05-09: structurally plausible / physiologically nonsense). **Mixed units (pace+power) → reject.**
2. **HR ceiling vs `max_hr + 5`** — если в zones есть `max_hr`, ceiling > `max_hr + 5` → reject.
3. **Transitions iff is_tri** — `transitions[]` непустой для не-триатлона → reject. Симметрично: пустые transitions для триатлона → warning в логах (не reject — допустим minimalist plan).

### System-prompt rules (PR1 — soft, не validator)

KISS принцип: правила сначала в prompt, validator только если Claude систематически нарушает (`_RACE_PLAN_SYSTEM_PROMPT`):

1. **🔥 Bike→Run constraint (триатлон):** «Bike NP cap MUST be calibrated to the run goal. Strong run goal → bike cap 75-78% FTP. Conservative → 70-72%. Independent leg corridors that would cumulatively destroy the run are unacceptable.»
2. **Negative-split run в триатлоне:** «Hr_ceiling_bpm in first 1/3 of leg MUST NOT exceed Z2-high. Marathon-segment is where finish-time is realised — heroic-start plans break the race.»
3. **Contingencies relevance gating:** «Pick 3-5 most relevant to distance/discipline/conditions. Heat scenario when expected_temp_c < 18 is wasted attention; mech failure for swim-only is nonsense.»

### Что validator пока не ловит (deferred → Phase 2.5)

ROI-ranked hard structural checks:

- **Sum of `legs[].distance` ≈ race total** (parsed → meters). ~20 строк, ловит реальные косяки.
- **Fueling × duration sanity**: `carbs_g_per_hour × race_h ∈ [100, 1500]`. Ловит «30 г/час на 8h IM» (голод) и «120 г/час на 1h спринт» (overload).
- **Per-leg duration plausibility** при наличии `goal_time` — ловит «swim 21 km».
- **Each leg.notes references at least one zone/threshold name** (groundedness signal).
- **Bike→run + negative-split escalate** из prompt в validator — только если §14 feedback покажет систематическое нарушение.

---

## 7. Idempotency & invalidation

**Default flow (без `force_regen`):** при наличии `RacePlan` для `(goal_id, current_UTC_day)` — return existing, Claude не вызываем. Race condition между pre-check и INSERT — `IntegrityError` → fallback на `get_today_for_goal`.

**Regenerate flow (`force_regen=True`):** in-place UPDATE того же row (preserve `id` — внешние ссылки в TG-сообщениях не ломаются), rate limit **1 regen / день** на `(user_id, goal_id)` (естественный backoff из unique-индекса UTC-day; счётчик в `payload.regen_count_today`). HTTP 429 + `Retry-After` при превышении.

**Accepted trade-offs:**

- **UTC-день, не локальный.** Атлет в UTC+12 генерирует в 23:00 локально, через 5h — в 04:00 локально → дубль проскочит. Принято — alternatives (PG-side `AT TIME ZONE user.tz` или `generated_local_date` колонка) усложняют код ради edge-кейса.
- **Hash-based авто-инвалидация — отложена.** За день атлет добавит активность / поменяет zones — `get_today_for_goal` вернёт устаревший plan. Кнопка regen решает 80%. `payload.context_hash` (`(goal.event_date, max(activities.updated_at), max(athlete_settings.updated_at), fitness_projection.updated_at)`) с авто-trigger — Phase 3 если поступят жалобы.

---

## 8. Multi-tenant isolation

- `user_id` из contextvars (`get_current_user_id()`), не параметр тула.
- `RacePlan.get_today_for_goal` / `get_latest_for_race` — `user_id` обязательный kwarg, фильтр **поверх** `goal_id` (defence-in-depth: leaked goal_id не должен ломать tenant boundary).
- Goal resolve — SQL `WHERE id=? AND user_id=?` в одном SELECT (C3 fix 2026-05-09).
- `Race.get_recent_for_user` JOIN scopes `Activity.user_id` (secM1 fix) — defense-in-depth против stale Race FK на foreign Activity.
- Foreign `goal_id` → «Goal not found» (не «forbidden» — не подсвечиваем существование чужих goals).

---

## 9. Tests

`tests/db/test_race_plan.py` (11) + `tests/mcp/test_races.py` (12 на `generate_race_plan`):

- Round-trip save / get_today_for_goal / get_latest_for_race (order by `generated_at DESC`).
- `user_id` scope — SQL inspection (`WHERE user_id = ?` присутствует).
- Refuse paths: no RACE_A / >200d / <6 активностей.
- **Cross-tenant `goal_id` → not found + assert no further DB + assert `user_id` filter в SQL** (C3 regression test).
- Dry-run happy path — `AsyncAnthropic` patched, assert `save` НЕ вызван.
- No-tool_use-block fallback.
- Validator unit tests: inverted pace/power corridor, HR ceiling > `max_hr + 5`, unparseable all-prose corridor (false-reject guard), mixed numeric+prose (H2), mixed units (H2).
- Test fixtures используют `MagicMock(spec=AthleteGoal)` — обращение к dropped-колонкам падает в тестах, не только в проде (C1 regression-guard).

### Gaps (Phase 2/3)

- E2e тест с моком реального Anthropic response полной формы.
- Тест preliminary-tag для `14 < days_to_race <= 200`.
- Тест fallback на `IntegrityError`.
- Тесты regen flow (in-place UPDATE, rate-limit hit → 429).
- Тесты sport-specific coach role + language pass-through.

---

## 10. Pending / deferred

- **§11.2 — Hash-based авто-инвалидация** (Phase 3 если жалобы).
- **§11.6 — No fallback on Claude failure.** `suggest_workout` имеет skeleton fallback на zones; здесь — generic error → юзер. Опции: skeleton fallback или «Try again in 5 minutes». Приемлемо для V0/V1.
- **§11.9 — Race-week Q&A через chat** (PR4). Inject `{active_race_plans_block}` в `get_system_prompt_chat` если у юзера есть `RacePlan` в окне ±14d от event_date. Атлет цитирует leg → Claude видит план → объясняет. ~30 строк. Defer до feedback от реальных гонок.
- **§11.10 — Auto-fill `race_conditions` server-side** (без UI-выбора) — PR4 если boilerplate complaints. Сейчас inherit-UI selector сделан.
- **§11.11 — Geo source for location/weather** (issue #331). Три tier'а апгрейда (Intervals event.location, manual `goal.location` field, full geocode + forecast). Сейчас `race_conditions` — manual entry.
- **Multi-tenant rollout (§11.1):** re-evaluate allowlist/role gate (`User.role in {"owner", "premium"}` или liability-surface signal) когда откроем для нескольких тенантов.
- **Warmup methodology injection** (PR4, deferred). Сейчас `warmup` — free-text от Claude без методички. Влить в `_RACE_PLAN_SYSTEM_PROMPT_TEMPLATE` как факты (источник: Abade 2017): (1) активная разминка ~10–15 мин + 2–4 strides/accelerations; **избегать эксцентрики** (Nordic-type −5% мощности остро); (2) **каждый 1°C падения t° мышц ≈ −3% мощности** — при вынужденной паузе перед стартом (corral / mass-start wait / между этапами триатлона) не сидеть пассивно; (3) **re-warm-up:** ~2-мин мини-активация (прыжки/strides/COD) за 5–6 мин до старта восстанавливает спринт-мощность. Bump `RACE_PLAN_MODEL_VERSION` при инъекции (§12). Доказательная база — футбол/U-19, принцип t°-мышц переносится, но исходы не endurance-specific — формулировать как guideline, не жёсткое правило.

---

## 11. Migration path / next steps

1. ✅ **PR1-PR3 shipped** (commit `1d68ca6`).
2. ⏳ **Радик прогоняет 2 ближайшие гонки** (Ironman 70.3 / Oceanlava) — runtime feedback.
3. ❌ **PR4 — Phase 2.5 enrichment** (после feedback):
   - Wellness 10-14d trend вместо снэпшота.
   - Training calibration (race-rehearsal flag, FTP trajectory).
   - Predicted splits per leg (`target_split_time_sec`).
   - Race-week Q&A через chat (§10 above).
   - Warmup methodology injection (§10 above — re-warm-up / eccentric-avoid / t°-мышц).
   - Auto-fill `race_conditions` from past Race row.
   - Escalate prompt rules → validator если feedback показывает systematic breach.
4. ❌ **Phase 3 feedback loop** — compliance metrics actor (auto on `ACTIVITY_UPLOADED` matching race) + hash-based авто-инвалидация + HR per-segment ceiling.
5. ❌ **Multi-tenant rollout** — re-evaluate allowlist/role gate.

---

## 12. Schema versioning policy

Bump `RACE_PLAN_MODEL_VERSION` (`data/race_plan_service.py:52`) при:

- Изменении JSON schema (`_RACE_PLAN_SCHEMA`).
- Изменении system prompt (`_RACE_PLAN_SYSTEM_PROMPT_TEMPLATE`).
- Смене Claude модели (текущая `claude-sonnet-4-6`).

Старые rows остаются read-only с прежним `model_version`. При breaking change — миграционный actor `regenerate plans WHERE model_version != latest AND goal.event_date >= today` (Phase 3 TODO).

---

## 13. Phase 3 compliance metrics — shape defined (PR3, commit `1d68ca6`)

> Schema + writer-stub shipped (`data/race_plan_compliance_service.py:130 compute_compliance`). NO auto-trigger / actor / dashboards (Phase 3 proper).

> **Зачем сейчас:** PR2 запускает 24h pre-race push, data collection начинается с реальных гонок. Если шейп определять задним числом — переписывать activity-laps парсер, retroactively size'ить колонки, и часть метрик будет пропущена для уже отработанных гонок. **Define-not-ship.**

### Три compliance метрики (минимальный набор для ML loop'а)

Все три per leg на post-race данных (activity laps + body mass + plan payload):

#### 13.1 HR-corridor compliance per leg

```
hr_compliance_pct = time_in_seconds(hr ≤ leg.hr_ceiling_bpm) / leg_duration_seconds × 100
```

Per-second HR samples из activity (есть в Intervals.icu detail). Per-leg сегментация — по `lap_indexes` или `leg.target_split_time_sec` (PR4). Для PR3 — fallback на whole-activity HR vs whole-leg ceiling.

#### 13.2 Pace/power band compliance per leg

```
band_compliance_pct = time_in_seconds(low ≤ pace_or_power ≤ cap) / leg_duration_seconds × 100
```

Те же source data. Парсим `leg.pacing.{low, cap}` теми же regex что validator (§6).

#### 13.3 Fueling compliance

```
estimated_g_per_hour = carbs_consumed_g / activity_duration_h
fueling_compliance_pct = min(estimated, plan.carbs_g_per_hour) / plan.carbs_g_per_hour × 100
```

`carbs_consumed_g` — manual entry в `Race` table после гонки (новая колонка в PR3 миграции). Без manual entry метрика не считается; auto-detect из swallow events на гарминах ненадёжен.

### Persistence layout

`race_plan_compliance` table (PR3 миграция `aa7b8c9d0e1f`):

- `race_plan_id` FK → `race_plans` (CASCADE), `race_id` FK → `races`, `user_id` (denormalized для fast user-scoped reads), `leg_name`, `hr_compliance_pct` / `band_compliance_pct` / `fueling_compliance_pct` NUMERIC(5,2), `leg_duration_sec`, `notes` (free-form), `computed_at`.

### Writer-stub

`compute_compliance(race_plan_id, race_id)` в `data/race_plan_compliance_service.py:130` — три метрики. **Не auto-trigger** в PR3 — только manual через CLI/admin для отладки. Auto-actor на `ACTIVITY_UPLOADED matching race` — Phase 3.

### Что НЕ Phase 3 (deferred дальше)

- Сравнение нескольких planов одного athlete'а на one-vs-one гонки (regression: «улучшается ли execution от plan to plan?»).
- ML feedback в `_RACE_PLAN_SYSTEM_PROMPT` (compliance < threshold → bias prompt к более консервативным коридорам).
- Cross-athlete compliance benchmarking — multi-tenant требование.

---

## 14. Decisions log

Resolved findings из 6 review rounds (architect ×2 + code-review ×4) on PR1-PR3:

| Date | § | Decision | Why |
|---|---|---|---|
| 2026-05-09 | §2 | `/raceplan` slash command dropped | Recall в webapp + chat AI; reduces command surface |
| 2026-05-09 | §3 | `confidence_tier` enum replaces binary `preliminary` | 4-tier UX warning vs binary; backend stamps tier from `days_to_race` |
| 2026-05-09 | §3 | `legs[].notes` maxLength=200 | Scannable on race-day phone; schema cap > prompt cajoling |
| 2026-05-09 | §4 | Sport-role + language + `event_name[:100]` clamp shipped | Coach voice tracks discipline; prompt-injection guard |
| 2026-05-09 | §5 | 200d gate (was 120d) | 120d блокировал real A-race planning window |
| 2026-05-09 | §6 | bike→run + neg-split + contingency-relevance в **prompt**, NOT validator | Bias toward observation; escalate только on demonstrated drift |
| 2026-05-09 | §6 | Mixed numeric+prose corridor → reject (H2) | Structurally plausible / physiologically nonsense |
| 2026-05-09 | §7 | Force-regen via in-place UPDATE + 1/day rate-limit | Keep id stable; 1/day хватает для типичного UX |
| 2026-05-09 | §7 | dry_run rate-limit 5/day per user via Redis (secH1) | Cost guard против `{dry_run: true}` flood; fail-open on Redis down |
| 2026-05-09 | §8 | Goal resolve via SQL `WHERE user_id=?` (C3) | Row-level audit logs vs Python-compare |
| 2026-05-09 | §8 | `Race.get_recent_for_user` JOIN scopes `Activity.user_id` (secM1) | Defense-in-depth против stale Race FK на foreign Activity |
| 2026-05-09 | §11.1 | Allowlist gate dropped | Owner — sole consumer; validator + JSON schema sufficient |
| 2026-05-09 | — | Service extracted в `data/race_plan_service.py` | MCP wrapper now thin; router shares logic |
| 2026-05-09 | §10 | Inherit-from-past UI selector, not name-matching | Explicit user choice; no fragile fuzzy match |
| 2026-05-09 | §12 | Модель остаётся `claude-sonnet-4-6` | Рассматривали opus (quality > tokens для once-per-race call), но в код не флипнули — sonnet даёт достаточное качество |
