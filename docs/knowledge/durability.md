# Durability — Theory & Methodology

> Спутник `docs/DURABILITY_CLASSIFIER_SPEC.md`. Теоретическая база, без implementation details.

---

## Что такое durability

**Durability** = способность сохранять физиологические параметры (силу, темп, экономичность) к концу длинной тренировки или гонки. Концепт формально введён Maunder et al. (2021, **C2**) для отделения «свежей» производительности (single-effort thresholds) от «состаренной»: 30-минутный FTP в свежем состоянии не предсказывает мощность на 3-м часу.

**Зачем мерять отдельно от обычных threshold-тестов:**
- В коротких тестах (Ramp, FTP-20) durability не виден — все «крепкие» одинаковы.
- На длинных дистанциях (Half/Full Ironman, гран-фондо) durability определяет место в финишном протоколе сильнее, чем ramp-test FTP.
- Spragg et al. (2023, **C3**) показали: про-велогонщики с лучшей **substrate flexibility** (выше fat oxidation на subthreshold) теряют меньше CP после 2.5ч предварительной нагрузки. То есть durability — это про метаболику, а не про мускулы.

---

## Tymewear three-system framework

Tymewear (entry #2 в `TRAINING_METHODOLOGY_RESEARCH.md`) разложили fatigue по трём независимым системам, каждая со своим сигналом:

| Система | Что отражает | Их сигнал | Наш прокси |
|---|---|---|---|
| **CV / термо** | плазма, дегидратация, теплоотвод | HR drift early→late | `intervals[].average_heartrate` |
| **Метаболическая** | реальный VO₂ запрос, glycogen depletion | V̇E (минутная вентиляция) | NP / pace падение + decoupling |
| **Нейромышечная / central** | усталость ЦНС, техническая деградация | BR (breathing rate) | cadence drop + RPE-vs-power divergence |

**Ключевая идея:** комбинация трёх независимых сигналов даёт **диагноз причины** усталости, а не просто факт «спортсмен поплыл к концу». Разные диагнозы → разные действия:
- CV-drift only → жара / гидратация / акклиматизация
- Metabolic erosion → fueling (calories/hour, тип углеводов)
- Neuromuscular → durability runs / силовые / техника
- Severe (всё одновременно) → перебрал, нужен recovery

V̇E + BR с chest strap у нас нет — заменяем power/cadence/RPE как прокси. Конструкция фреймворка переносится; конкретные пороги — наши, валидируются Phase 0.

---

## Empirical baselines

### Rothschild 2025 (C4) — n=51 cyclists, 85 measurements

Самая близкая к нашей задаче работа. После 2.5 часов работы при 90% VT1:

- **VT1 HR drift:** 142 → 151 bpm = **+6.3%**
- **Threshold power decline:** mean ~10% (range 1-45W)
- **Power loss range:** очень wide individual variation — 1-45W. Группа неоднородна, для нашего малого датасета это значит «жди broad distribution».

**Что мы из этого взяли:**
- `HR_DRIFT_HIGH = 5%` — ниже их 6.3%, ловим раньше начала проблем. Phase 0 валидирует.
- `POWER_DROP_YELLOW = 3%` / `POWER_DROP_RED = 7%` — около медианы их распределения и «начинает быть тревожно».

### Stevenson 2024 (C5) — neuromuscular signature

После 2 часов умеренной работы:
- V̇E «remarkably stable» — метаболика не сдвинулась
- BR +16% — нейромышечная подпись без метаболического сдвига

**Что это значит для нас:** усталость может быть **только** нейромышечной (cadence drop / RPE excess) при сохранённой метаболике (NP не упал). Поэтому важен `count_red_signals` guard — три сигнала одновременно → `severe`, не «metabolic erosion с co-морбидностью».

### Nicolò et al. 2018 (C6) — respiratory control theory

Объясняет, **почему BR — независимый сигнал**: дыхание контролируется не только метаболическим запросом, но и центральной командой. То есть BR может расти когда V̇E стабильна — это сигнал «central fatigue». В нашем стеке BR-эквивалент — это RPE-vs-IF divergence.

### Meyer et al. 1999 (C7) — критика HR zones

Классический paper: HR zones, основанные на %HRmax / %HRR, имеют большую individual variability и плохо коррелируют с метаболическими порогами. Используется в spec как обоснование почему мы используем **HRVT-based zones** (через DFA α1) вместо %HRmax.

---

## Substrate flexibility — теория, которую мы НЕ реализуем

Spragg et al. 2023 (C3) показали: про-велогонщики с лучшим **fat oxidation на subthreshold** теряют меньше CP после 2.5ч. Логика: если ты сжигаешь больше жира на Z2, гликоген сохраняется на финиш.

**Почему мы это не считаем:** для substrate flexibility нужен **RER (respiratory exchange ratio)** — отношение CO₂/O₂, мерится газоанализатором. У нас этого нет. Можно проксировать через `decoupling × time-in-zone`, но это слабый прокси, шум перекрывает сигнал.

**Косвенно** substrate flexibility отражается в `metabolic_erosion` категории (power падение + decoupling растёт): атлет с плохой substrate flexibility будет чаще попадать туда. То есть лечение — то же самое (fueling review, длинные Z2), просто без отдельного маркера.

---

## Связь с существующими модулями

| Модуль | Связь | Где |
|---|---|---|
| **Decoupling** | Whole-activity Pa:Hr — переиспользуем как один из 6 deltas (`decoupling_pct`) | `data/db/activity.py:ActivityDetail.decoupling`, `docs/knowledge/decoupling.md` |
| **`is_valid_for_decoupling`** | Eligibility filter — переиспользуем напрямую | `data/metrics.py:594` |
| **DFA α1** | Bonus signal: `dfa_a1_late_mean < 0.5` усиливает metabolic-erosion гипотезу | `activity_hrv.dfa_a1_mean`, `docs/knowledge/dfa-alpha1.md` |
| **RPE** | `rpe_excess` против `expected_rpe(IF)` — confirmer в neuromuscular ветке (post-activity, не late-window — см. spec §4.2 A3) | `activities.rpe` |
| **Aerobic Efficiency** | EF тренд — long-term durability proxy, но per-activity не используется | `docs/knowledge/aerobic-efficiency.md` |

---

## Открытые вопросы (не закрыты литературой)

1. **Window mismatch** между whole-activity decoupling (50/50 split) и late-window deltas (last 25%) — осознанное допущение в spec §4.2 A2. Корректность валидируется только тем что классификатор работает в проде.
2. **Run pace drop thresholds** — proxy от bike, scaled. Литературы напрямую по run durability мало; Phase 0 валидирует.
3. **Cadence drop как neuromuscular маркер** — пограничный сигнал. Зависит от типа местности (груня vs ровно), `cadence_drop_pct` может быть compound с power_drop. Phase 0 acceptance check A3 проверяет корреляцию с `rpe_excess`.

---

## References (в порядке цитирования)

- **C2** Maunder E. et al. (2021). *Durability: a contemporary issue in endurance physiology.* Sports Medicine 51(6): 1093-1105.
- **C3** Spragg J. et al. (2023). *Substrate metabolism and durability in elite cyclists.* Med Sci Sports Exerc 55(3).
- **C4** Rothschild J.A. (2025). *Empirical thresholds of cycling durability: n=51 cohort study.* (Citation harvested from Tymewear.)
- **C5** Stevenson J. (2024). *Neuromuscular fatigue signatures without metabolic shift.* (Citation harvested from Tymewear.)
- **C6** Nicolò A. et al. (2018). *Respiratory frequency: central neural control mechanisms.* Front Physiol 9:222.
- **C7** Meyer T. et al. (1999). *HR-based vs metabolic-threshold zone prescription: reliability critique.* Int J Sports Med 20(7).

Полный контекст — `docs/TRAINING_METHODOLOGY_RESEARCH.md` Citations harvested.
