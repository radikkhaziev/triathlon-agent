# Durability Classifier Spec

> Классифицировать тип усталости в длинных тренировках (≥75-90 мин) по тому, какие физиологические системы «поплыли» к концу: **CV-drift only / metabolic erosion / neuromuscular / mixed / inconclusive**. Без новых датчиков — на тех данных, что уже прилетают через Intervals.icu.
>
> Базируется на **Tymewear three-system fatigue decomposition** (см. `docs/TRAINING_METHODOLOGY_RESEARCH.md` entry #2). У них V̇E + BR с chest strap; у нас вместо V̇E/BR — power/cadence/RPE как прокси.

**Version:** 0.2
**Status:** Draft — все §10 open questions resolved (2026-05-09); applied review v0.1 → v0.2 (см. §12 changelog). Готов к Phase 0.
**Owner:** radik
**Project:** triathlon-agent

**Related:**

| Spec / code | Связь |
|---|---|
| `docs/TRAINING_METHODOLOGY_RESEARCH.md` entry #2 | Источник идеи + цитаты C2-C7 |
| `docs/knowledge/decoupling.md` | Pa:Hr — основа CV-drift детектора, переиспользуем |
| `data/metrics.py:is_valid_for_decoupling` | Существующий фильтр steady-state, переиспользуем |
| `data/db/activity.py:ActivityDetail` | Источник `intervals[]` (per-interval HR/power/cadence/pace) |
| `data/db/activity.py:Activity` | `rpe`, `moving_time`, `is_race` |
| `data/db/activity.py:ActivityHrv` | DFA α1 при наличии — bonus signal для metabolic |

---

## 1. Цель

Для каждой подходящей activity (long, steady-state) выдать одну категорию + per-signal deltas. Использовать это:

1. В morning report / weekly report: «3 из 5 длинных за месяц — metabolic erosion → проверить fueling».
2. В AI чат как фактический контекст (через MCP tool).
3. В webapp на странице activity: бейдж + объяснение, что именно деградировало.
4. (Long-term) feed в race-projection model — durability score как фича.

## 2. Tymewear → наш стек: маппинг сигналов

Tymewear читают V̇E и BR с chest strap. У нас этих сигналов нет, но фреймворк («три независимых системы») переносится на наши данные:

| Tymewear signal | Что отражает | Наш прокси | Источник |
|---|---|---|---|
| HR drift | плазма / терморегуляция (CV-drift) | HR drift early→late | `intervals[].average_heartrate` |
| Minute ventilation V̇E | реальный метаболический спрос | NP / pace drop early→late + decoupling | `intervals[].weighted_average_watts`, `decoupling` |
| Breathing rate BR | central command / нейромышечная усталость | cadence drop + RPE-vs-power divergence | `intervals[].average_cadence`, `activities.rpe` |

**Bonus:** `dfa_a1_mean` из `activity_hrv` (когда есть) — прямой proxy на autonomic balance, может усилить metabolic-erosion детектор.

## 3. Eligibility filter

Activity допускается до классификации если:

1. Sport ∈ {Ride, Run}. Swim excluded (Tymewear durability literature → endurance cycling/running only).
2. `moving_time ≥ MIN_DURATION_S`: **Run 75 min** (4500 s), **Bike 90 min** (5400 s). См. §10 Q1.
3. Steady-state: переиспользуем `is_valid_for_decoupling()` — VI ≤ 1.10, ≥70% времени в Z1+Z2, decoupling computed.
4. `intervals[]` не пуст и содержит ≥4 элемента (нужно early/late сравнение).
5. **Races excluded** (`is_race=True`) — race effort ≠ durability test, deltas будут confounded.

Failed eligibility → `classification = 'ineligible'`, причина в `ineligible_reason`. Не блокируем — записываем «не подходит», чтобы UI мог показать «недостаточно длинная» вместо тишины.

## 4. Сигналы и deltas

### 4.1 Early/late windows

`intervals[]` уже разрезан Intervals.icu на однородные блоки. Окна сравниваются на уже-прогретом atlete'е, чтобы не путать warm-up с «свежим состоянием»:

- `WARMUP_OFFSET_S = 600` — пропускаем первые 10 минут (оценка длительности обычного warm-up).
- `early = [WARMUP_OFFSET_S, WARMUP_OFFSET_S + 0.25 * (moving_time - WARMUP_OFFSET_S)]`
- `late = last 25%` от total moving_time

Минимум 1 interval в каждом окне; если сплит даёт пустой window или `late_window_sec < 600` (после короткого CD) — `inconclusive`.

**Phase 0 acceptance check (см. §9 Phase 0):** прогнать корреляцию `hr_drift_pct` ↔ длина warm-up на исторических activities. Если |r| > 0.3 → `WARMUP_OFFSET_S` нужно повышать или брать median(early-25-50%) вместо first 25%.

**Однородность intensity домена:** даже после warm-up offset, early и late могут попадать в разные intensity blocks (e.g. tempo block в early, recovery в late после большого интервала). Дополнительный sanity-check на уровне взвешенной мощности: если `|np_late / np_early - 1| > 0.20` (вне ±20%) → activity не steady-state по нашему стандарту → `ineligible(reason="non_homogeneous_intensity")`. Точное значение порога — Phase 0 калибровка.

### 4.2 Per-signal deltas

**Convention:** все `*_pct` метрики нормализованы так, чтобы **положительное значение = усталость**. Это убирает мысленную gimnastics при чтении decision tree (§5).

| Signal | Sport | Формула | Знак при усталости |
|---|---|---|---|
| `hr_drift_pct` | both | `(hr_late_mean / hr_early_mean - 1) * 100` | **+** (HR растёт) |
| `power_drop_pct` | Ride | `(1 - np_late / np_early) * 100` | **+** (watts падают, дробь → 0+, drop_pct → положительное) |
| `pace_drop_pct` | Run | `(pace_late_sec_per_km / pace_early_sec_per_km - 1) * 100` | **+** (sec/km растёт = темп замедляется) |
| `cadence_drop_pct` | both | `(1 - cad_late / cad_early) * 100` | **+** (rpm/spm падает) |
| `decoupling_pct` | both | `activity_detail.decoupling` (whole-activity Pa:Hr) | **+** (HR обгоняет power) |
| `rpe_excess` | both | `actual_rpe - expected_rpe(intensity_factor)` | **+** (тяжелее, чем должно быть на этом IF) |

**Window mismatch (A2 — осознанное допущение):** `decoupling_pct` тянется как whole-activity (50%/50% Pa:Hr split, считается Intervals.icu), все остальные — late-window (25/25 c warm-up offset). Эти метрики отвечают на разные вопросы: `decoupling` — «расходились ли HR и power на всей дистанции», `power_drop_pct` — «упала ли мощность к концу относительно прогретого начала». Их комбинация в decision tree §5 осознанна, не баг. Считать свой Pa:Hr на 25/25 окнах не делаем — слишком дорого, эффект на классификацию малый.

**`rpe_excess` — overall, не late-window сигнал (A3 — осознанное ограничение):** RPE приходит post-activity целиком за тренировку. То есть `rpe_excess` отражает суммарную субъективную сложность, а не RPE-в-последней-четверти. Поэтому в §5 decision tree `rpe_excess` понижен до **confirmer**, не primary trigger neuromuscular ветки: cadence drop остаётся жёстким сигналом, RPE только подтверждает. Phase 0 проверяет корреляцию `rpe_excess` ↔ `cadence_drop_pct` — если она слабая (|r| < 0.3), сигнал убирается из дерева вовсе.

`expected_rpe(IF)` — fixed linear map (Phase 1, §10 Q3): IF 0.65 → RPE 4, IF 0.75 → RPE 5, IF 0.85 → RPE 6, IF 0.95 → RPE 7. Per-user calibration отложена до Phase 2+ и условна на наличие systematic bias (§10 Q3).

### 4.3 Bonus: DFA α1

Если `activity_hrv.dfa_a1_mean` есть и `< 0.5` → усиливает metabolic-erosion гипотезу. Не обязательно — много activities без DFA processed.

## 5. Классификатор

Decision tree (порядок проверок важен):

```
# Guard 0: всё разъезжается одновременно — отдельная категория, не «metabolic_erosion».
# Без этого ветка metabolic_erosion поглощает neuromuscular co-морбидность.
if count_red_signals(power_drop_pct, hr_drift_pct, cadence_drop_pct, decoupling_pct) ≥ 3:
    → severe                     # три-плюс системы в red одновременно

elif power_drop_pct ≥ POWER_DROP_RED AND decoupling_pct ≥ DECOUPLING_HIGH:
    → metabolic_erosion          # пропавшая нога + Pa:Hr расходится

elif hr_drift_pct ≥ HR_DRIFT_HIGH AND power_drop_pct < POWER_DROP_YELLOW AND cadence_drop_pct < CADENCE_DROP_YELLOW:
    → cv_drift_only              # пульс растёт сам по себе, силы те же

elif cadence_drop_pct ≥ CADENCE_DROP_RED:
    → neuromuscular              # каденс провалился (жёсткий сигнал)
elif cadence_drop_pct ≥ CADENCE_DROP_YELLOW AND rpe_excess ≥ RPE_EXCESS_HIGH:
    → neuromuscular              # каденс желтый + RPE подтверждает (см. A3 в §4.2)

elif (хотя бы 2 сигнала в yellow):
    → mixed                      # многофакторно, одну причину не выделить

else:
    → none                       # ничего критичного, durability OK
```

**`count_red_signals(...)`:** считает сколько из переданных аргументов перешло в red-зону по своему порогу. `None`-аргументы считаются 0 (не red). Универсально работает для bike (где `pace_drop_pct=None`) и run (где `power_drop_pct=None`).

**None-safety (B2):** все сравнения в дереве должны защищаться от `None`. Конвенция: `None < threshold` → `False`, `None ≥ threshold` → `False` (т.е. отсутствующий сигнал не триггерит и не блокирует ветку). На практике это означает helper:
```python
def _ge(value: float | None, threshold: float) -> bool:
    return value is not None and value >= threshold
```

`rpe_excess` понижен до confirmer'а в neuromuscular ветке (A3): primary trigger — только cadence; RPE добавлен в защитную ветку «cadence yellow + RPE high» чтобы не пропускать early neuromuscular fatigue, но и не fire'ить ложно при overall тяжёлой тренировке.

Пороги (стартовые, требуют валидации Phase 0 на нашей истории):

| Threshold | Bike | Run | Источник |
|---|---|---|---|
| `HR_DRIFT_HIGH` | 5% | 5% | Rothschild 2025 (C4): VT1 HR 142→151 = +6.3% за 2.5ч |
| `POWER_DROP_YELLOW` | 3% | — | Rothschild 2025: ~10% mean threshold drop, individual 1-45W |
| `POWER_DROP_RED` | 7% | — | конец того же диапазона |
| `PACE_DROP_YELLOW` | — | 4% | proxy от bike, scaled (run pace меняется быстрее под утомлением) |
| `PACE_DROP_RED` | — | 8% | — |
| `CADENCE_DROP_YELLOW` | 2 rpm (~2.5%) | 2 spm (~1.2%) | empirical, требует валидации |
| `CADENCE_DROP_RED` | 5 rpm | 5 spm | — |
| `DECOUPLING_HIGH` | 10% | 10% | существующий traffic-light red threshold |
| `RPE_EXCESS_HIGH` | 1.5 | 1.5 | empirical (RPE — Borg CR-10) |

## 6. Storage

Новая таблица `activity_durability`:

```python
class ActivityDurability(Base):
    __tablename__ = "activity_durability"

    id: int  # PK
    user_id: int  # FK -> users.id, индекс
    activity_id: str  # FK -> activities.id, UNIQUE с user_id
    classification: str  # 'severe' / 'metabolic_erosion' / 'cv_drift_only' / 'neuromuscular' / 'mixed' / 'none' / 'ineligible' / 'inconclusive'
    ineligible_reason: str | None  # 'too_short' / 'high_vi' / 'race' / 'no_intervals' / 'non_homogeneous_intensity' / 'late_window_too_short' / 'not_yet_supported'

    hr_drift_pct: float | None
    power_drop_pct: float | None  # Ride
    pace_drop_pct: float | None   # Run
    cadence_drop_pct: float | None
    decoupling_pct: float | None  # копируем из ActivityDetail для self-contained query
    rpe_excess: float | None
    dfa_a1_late_mean: float | None  # bonus, может быть NULL

    early_window_sec: int | None
    late_window_sec: int | None
    computed_at: datetime
```

`UNIQUE(user_id, activity_id)` — одна запись на activity. Recompute on activity update / RPE backfill / DFA reprocess.

## 7. Pipeline integration

1. **Initial compute trigger:** в конце `actor_compute_activity_details` (после `decoupling`) — отправить `actor_classify_durability.send(user_id, activity_id)`. Тонкий separate actor чтобы failure классификатора не валил decoupling-pipeline.
2. **Backfill:** новый CLI command `python -m cli backfill-durability --user <id> --since <date>`, идёт по `activities` без записи в `activity_durability`. Owner-only в Phase 1; multi-tenant rollout в конце Phase 3 (см. §10 Q5).
3. **Recompute triggers** — explicit list, ничего «при activity update» расплывчатого:
   - **RPE update** (Telegram inline button → `bot/main.py` handler пишет `activities.rpe`): handler вызывает `actor_classify_durability.send(user_id, activity_id)` в самом конце, после commit. Owns: Phase 2.
   - **DFA reprocess** (`actor_process_activity_hrv` обновил `activity_hrv.dfa_a1_mean`): добавить `actor_classify_durability.send(...)` в конец actor'а. Owns: Phase 2.
   - **`ACTIVITY_UPDATED` webhook** — recompute только если изменилось одно из: `intervals` (JSON), `weighted_average_watts`, `moving_time`, `decoupling`, `variability_index`. Renaming/notes/lap-name edits НЕ триггерят recompute. Фильтрация в webhook dispatcher до `.send()`. Owns: Phase 2.
   - **Activity backfill / re-import** (rare ops command): покрывается CLI `backfill-durability` явно, не через triggers.

## 8. MCP tool surface

```python
@mcp.tool()
def get_durability_trend(
    period: str = "30d",   # 7d / 30d / 90d / "2026Q1"
    sport: str | None = None,  # "Ride" / "Run" / None=both
) -> DurabilityTrendDTO:
    """Recent long-session durability classifications + roll-up."""
```

Returns:

```python
class DurabilityTrendDTO:
    period: str
    eligible_count: int
    classifications: dict[str, int]  # {'metabolic_erosion': 3, 'cv_drift_only': 2, ...}
    median_hr_drift_pct: float | None
    median_power_drop_pct: float | None  # bike-only
    activities: list[DurabilityActivityDTO]  # last N=10
    interpretation: str  # human-readable summary, AI-friendly
```

`interpretation` — короткая выжимка типа `"3 of 5 long bike sessions in last 30d show metabolic erosion (median power drop 6%); cv_drift_only stable; consider fueling review."`. AI чат потребляет это как контекст, не как final answer.

## 9. Phases

> **Стратегия:** bike-first. Phase 0-3 покрывают только Ride на чистых power-данных. После валидации в production на bike — переносим framework на Run (Phase 4). Это снижает risk дебажить два пороговых пакета одновременно.

### Phase 0 — Validate thresholds (no code changes, **bike-only**)

1. Прочитать papers C2-C7 (см. `TRAINING_METHODOLOGY_RESEARCH.md` Citations harvested), занести выжимку в новый `docs/knowledge/durability.md`.
2. Скрипт-однодневка: пройти по owner's `activity_detail.intervals[]` за последние 12 мес для **Ride only**, для каждой long-ride (≥90 min) посчитать:
   - все 6 deltas из §4.2 (`hr_drift_pct`, `power_drop_pct`, `cadence_drop_pct`, `decoupling_pct`, `rpe_excess`)
   - длину warm-up (время до первого interval с power ≥ 0.55*FTP) — для C1 проверки
   - однородность intensity (`np_late / np_early`) — для C2 фильтра
3. **Acceptance checks (sanity до утверждения порогов):**
   - **C1 — warm-up confound:** корреляция `hr_drift_pct` ↔ длина warm-up. Если `|r| > 0.3` — поднять `WARMUP_OFFSET_S` или перейти на early `[25%, 50%]` median window. Зафиксировать решение.
   - **C2 — homogeneity threshold:** распределение `|np_late / np_early - 1|`. На каком percentile стоит провести черту между «steady-state» и «interval workout»? Зафиксировать `HOMOGENEITY_BAND` точно.
   - **A3 — rpe_excess validity:** корреляция `rpe_excess` ↔ `cadence_drop_pct`. Если `|r| < 0.3` → выкинуть `rpe_excess` из decision tree §5 целиком (ветка neuromuscular остаётся только на cadence). Если `≥ 0.3` → оставить как confirmer как сейчас.
4. **Output:** проверить, что bike-пороги (§5 Bike столбец) ловят сигнал на истории. Если 95% activities в `none` — пороги жёсткие. Если 80% в `mixed` или `severe` — мягкие. Подкорректировать.

**Gate:** показать таблицу распределений + результаты trёх acceptance checks user'у, получить OK на bike-пороги + final версии формул (с учётом C1/C2/A3 решений) перед Phase 1.

### Phase 1 — Schema + classifier (read-only, owner-only, **bike-only**)

1. ORM `ActivityDurability` + миграция (sport-agnostic schema, `pace_drop_pct` оставляем nullable — заполнится в Phase 4 для Run).
2. `data/durability.py:classify_activity(activity_id) -> ActivityDurability` — pure function. **В Phase 1 только бранч `if sport == "Ride"`**, остальные → `ineligible(reason="not_yet_supported")`.
3. CLI `python -m cli backfill-durability` — bulk на owner'е, Ride only.
4. Юнит-тесты на classifier с фикстурами по каждой ветке decision tree для bike (cv_only, metabolic, neuromuscular, mixed, none, 4-5 ineligible reasons).

### Phase 2 — Auto-compute + MCP (**bike-only**)

1. Hook в `actor_compute_activity_details` (или после — тонкий bg actor `actor_classify_durability`).
2. Recompute trigger на RPE update / DFA reprocess.
3. MCP tool `get_durability_trend` + `DurabilityTrendDTO` (sport фильтр уже в API; в Phase 2 на практике возвращает только Ride).
4. AI системный prompt — короткая ремарка, что инструмент существует и когда им пользоваться.

### Phase 3 — Surfaces (**bike-only**)

Из ответа на Q4 (§10): Weekly + MCP + Webapp must-have, Morning nudge сдвигаем в Phase 4+.

1. **AI чат через MCP** — автоматически работает с Phase 2; здесь только убедиться что Claude его дёргает в правильных контекстах (через системный prompt).
2. **Weekly report section** (Sunday 19:00):
   - Two-horizon call: `get_durability_trend(period="7d")` + `get_durability_trend(period="30d")`.
   - Suppression: рендерится только если `eligible_count_7d ≥ 1`.
   - Plan-side coupling: при `(metabolic_erosion + severe) ≥ 2 in 30d` AI инструктируется не добавлять длинную выше текущего IF в плане следующей недели.
   - **Plan-side enforcement (D — обязательно, не только prompt):** инструкция в системном промте — soft signal, Claude может её пропустить. Поэтому в `suggest_workout` MCP tool добавляем детерминированный post-validator: если у user `(metabolic_erosion + severe) ≥ 2 in 30d` AND предложенная длинная (>= MIN_DURATION_S по sport'у) имеет `IF > min(prev_long_IF, 0.75)` → инструмент возвращает плейлоад с `validation_warning` и режет IF до `min(prev_long_IF, 0.75)`. AI получает обратно скорректированный план, не падает. Это safety guard, не визуально-tone replacement промта.
3. **Webapp badge** на `/activity/:id` — бейдж классификации + 3 delta-bars (HR drift / power drop / cadence drop).
4. **Multi-tenant:** разкатать на двух остальных активных пользователей после in-production валидации на owner'е.

### Phase 4 — Run port + Morning nudge

**Phase 4 entry gate:** bike-классификатор должен отработать в production достаточно, чтобы уверенно говорить о его поведении. Конкретные критерии (**OR** — любой из):

- `n ≥ 10` bike long-rides классифицированы у owner'а (4 для full-distribution sanity check мало; 10 даёт минимально читаемые percentiles по 6+ категориям) **AND** доля не-`none` категорий ≥ 30% (значит классификатор реально что-то ловит, а не возвращает default'ы).
- ИЛИ `≥ 4 weeks` production usage без жалоб user'а на ложно-позитивные срабатывания.

1. **Phase 0-style validation для Run** — те же скрипты на `Run` activities ≥75 мин, откорректировать Run-пороги в §5.
2. Снять «not_yet_supported» branch в `classify_activity` для Run.
3. Backfill durability для всей run-истории.
4. Surfaces автоматически подхватывают Run (sport фильтр уже параметризован).
5. **Morning report nudge** при `metabolic_erosion ≥ 3 in 30d` (ежедневный канал требует higher signal-to-noise — добавляем после того как у нас уже два спорта в Phase 3).

### Non-goals

- Real-time (in-workout) classification. Только post-activity.
- Replace decoupling. Decoupling остаётся самостоятельной метрикой; durability — над ней.
- Cross-sport durability score (один общий ranking). Пороги per-sport.
- Substrate-flexibility marker (Spragg C3) — мы не меряем RER. Оставлено как theory note в `docs/knowledge/durability.md`.

## 10. Open questions (нужен OK перед Phase 1)

1. **Eligibility duration:** ✅ **Resolved 2026-05-09 — Run 75 min, Bike 90 min.** (Совпадает с `is_valid_for_decoupling` +30 мин запас под durability; больше data volume на старте.)
2. **Sport coverage:** ✅ **Resolved 2026-05-09 (revised) — bike-first, потом run после валидации.** Phase 0-3 покрывают только Ride. После того как bike-классификатор работает в production и его пороги валидированы на реальных activities — переносим framework на Run (§9 Phase 4): re-run threshold validation для run, минимальные правки в classifier, те же surfaces. Run-пороги отдельные (§5 уже разделены).
3. **`expected_rpe(IF)` table:** ✅ **Resolved 2026-05-09 — fixed map в Phase 1.** Per-user calibration отложена до Phase 2+ и условна: только если Phase 0 валидация покажет systematic bias у user'а (среднее `rpe_excess` ≠ 0 на >50% activities).
4. **Surfaces priority Phase 3:** ✅ **Resolved 2026-05-09 — must-have в Phase 3: AI MCP + Weekly report + Webapp badge.** Morning report nudge сдвинут в Phase 4 (после Run порта; ежедневный канал требует higher signal-to-noise после того как два спорта валидированы).
5. **Multi-tenant rollout:** ✅ **Resolved 2026-05-09 — автоматически в конце Phase 3.** Как только bike-classifications выглядят разумно в production на owner'е → backfill истории остальным двоим + включение surfaces в одной транзакции.
6. **Threshold validation gate (Phase 0):** ✅ **Resolved 2026-05-09 — оставляем.** Скрипт-однодневка по 12 мес bike истории → таблица распределений HR drift / power drop / cadence drop / decoupling → user корректирует пороги в §5 → потом Phase 1.

## 11. References

- C2 Maunder et al. 2021 — durability concept introduction
- C3 Spragg et al. 2023 — pro cyclist durability + substrate flexibility
- C4 Rothschild 2025 — n=51, empirical thresholds (used for §5 baselines)
- C5 Stevenson 2024 — neuromuscular fatigue signature without metabolic change
- C6 Nicolò et al. 2018 — respiratory control mechanisms
- C7 Meyer et al. 1999 — HR zone reliability critique

(см. `docs/TRAINING_METHODOLOGY_RESEARCH.md` Citations harvested.)

---

## 12. Changelog

### v0.2 — 2026-05-09

Применены замечания review v0.1 → v0.2.

**Блокеры закрыты:**
- **A1** (sign of `pace_drop_pct`) — все `*_drop_pct` метрики унифицированы: «положительное = усталость». Run pace формула изменена на `(pace_late/pace_early - 1)*100`; bike power на `(1 - np_late/np_early)*100`. См. §4.2.
- **B1** (≥3 red → отдельная категория) — введена новая категория `severe` как guard в самом верху decision tree §5. Storage enum в §6 расширен.
- **C1** (warm-up confound) — early window сдвинут на `WARMUP_OFFSET_S = 600s`; Phase 0 acceptance check 1 проверяет корреляцию `hr_drift_pct` ↔ длина warm-up.

**Доработки:**
- **A2** (window mismatch decoupling vs deltas) — выбран вариант 2: явно зафиксировано в §4.2 что `decoupling_pct` whole-activity, остальные late-window, и комбинация осознанна.
- **A3** (rpe_excess overall vs late) — `rpe_excess` понижен до confirmer'а в neuromuscular ветке: primary trigger только cadence; Phase 0 acceptance check 3 проверяет корреляцию `rpe_excess` ↔ `cadence_drop_pct` и удаляет сигнал если |r|<0.3.
- **B2** (None-safety) — explicit helper `_ge` и конвенция «None → не triggers и не блокирует» зафиксированы в §5.
- **C2** (homogeneity filter) — добавлен фильтр eligibility `non_homogeneous_intensity` (band ±20% pre Phase 0, точная калибровка в Phase 0 acceptance check 2).
- **D** (plan-side coupling determinism) — в §9 Phase 3.2 добавлен deterministic post-validator в `suggest_workout` MCP tool, не только prompt instruction.
- **E** (recompute triggers) — §7 переписан с explicit list: RPE button / DFA reprocess / `ACTIVITY_UPDATED` с filter on `intervals|weighted_average_watts|moving_time|decoupling|variability_index`. Owns each — Phase 2.
- **F** (Phase 4 gate) — цифра 4 заменена на конкретный criterion: `n≥10` long-rides AND ≥30% non-`none`, ИЛИ `≥4 weeks` production без false-positive жалоб.

**Опечатки (G):** «матерь» → «важен» (§5), «ноога» → «нога» (§5), «Citations harvested table» → «Citations harvested» (§11).

### v0.1 — 2026-05-09 (initial draft)

Initial spec from Tymewear three-system fatigue decomposition. Q1-Q6 в §10 resolved сразу при создании.
