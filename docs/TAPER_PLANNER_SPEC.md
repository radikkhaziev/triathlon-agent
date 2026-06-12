# Taper Planner Spec

> Детерминированный калькулятор подводки: по дате гонки + текущим CTL/ATL + типу события строит посуточный график снижения нагрузки (TSS-таргеты) и правила («режь длительность, держи частоту и интенсивность»). Без ML — чистая арифметика на impulse-response модели. Методбаза: `docs/knowledge/taper.md`.

**Status:** 📝 SPEC ONLY — код не написан. Ждёт подтверждения scope перед Phase 1.

**Related:**

| Issue / Spec | Связь |
|---|---|
| `docs/knowledge/taper.md` | Методология, дефолтные параметры, источники |
| `docs/knowledge/fitness-fatigue-model.md` | `p(t) = CTL − 2·ATL`, EWMA-рекурсия, TSB-зоны |
| `data/metrics.py` | Целевой модуль для детерминированного ядра (Phase 1) |
| `mcp_server/tools/` | `get_taper_plan` MCP tool (Phase 2) |
| `data/db/fitness_projection.py` | Источник прогнозного CTL/ATL к дате гонки |
| `data/race_plan_service.py:build_race_plan` | Точка интеграции taper-секции (Phase 3, опц.) |
| `tasks/utils.py` (`PEAK_TAPER_DAYS=14`) | Существующая taper-aware логика (suppress ramp-test) |
| `docs/BUSINESS_RULES.md` | Race-categories A/B/C → тип/длина тейпера |

---

## 1. Мотивация

Любительские A-гонки часто проигрываются на подводке: либо «доколачивают» объём в последнюю неделю (усталость не уходит), либо уходят в полный отдых (детренировка), либо срезают интенсивность вместе с объёмом (теряют фитнес). У нас уже есть все вводные — `CTL/ATL/TSB` из Intervals.icu, прогноз `fitness_projection`, дата и тип гонки из `athlete_goals`, per-sport зоны. Не хватает одного: превратить методологию (`docs/knowledge/taper.md`) в конкретный посуточный план.

Фича закрывает пробел между «система знает, что идёт тейпер» (блокирует ramp-test) и «система говорит, что именно делать каждый день».

---

## 2. Scope (фазы)

### Phase 1 — детерминированное ядро + тесты ✅ (2026-06-12)

- [x] Чистая функция `build_taper_plan(...)` в `data/metrics.py` (секция «Taper Planner» в конце файла; без I/O, без БД — только числа на вход/выход).
- [x] EWMA forward-simulation CTL/ATL под экспоненциально спадающей нагрузкой (§4) — переиспользует `project_sport_load_forward` / `_project_loads_one_day`, **exp-форма** `e^(−1/τ)`, не линеаризованная `1/τ` (см. Deviations ниже).
- [x] Grid-search длины × `τ_taper` (3–5 д), максимизация `p = CTL − 2·ATL` с приземлением TSB в `fresh`/`transition` (§4). Сетка длины — пер-классовый коридор §5, не глобальная 7–21.
- [x] Event-specific дефолты по типу гонки (§5) — `_TAPER_CLASS_PARAMS`.
- [x] Возврат: посуточный график TSS (`target_tss` + `pct_of_peak`), `taper_start_date`, прогноз race-day CTL/ATL/TSB/p + `tsb_zone`, текстовые правила, `confidence`, `warnings[]`.
- [x] Детерминированные unit-тесты — `tests/metrics/test_taper.py` (19): монотонность спада, EWMA-эквивалентность с `project_sport_load_forward` (вкл. независимую переderивацию на границе dtr=2 / taper_start=today), коридорный порядок длины long > short, инвариантность выбора `(L, τ)` к CTL/ATL, int=hold в rules, edge-cases (низкий CTL → warning + shallow clamp, гонка через 1/2/3 дня, early-режим — сокрытие таргетов/проекции/sim-warning'ов, невалидные входы, детерминизм).

**Deviations от текста спеки (реализация — источник истины):**
- EWMA — exp-форма `e^(−1/τ)` (как весь кодбейс и Intervals.icu), не `1/τ`-рекурсия из §4.1.
- Декей стартует с `i+1`: день 0 тейпера уже срезан (~78% peak при τ=4), а не 100% peak — полный peak-день с нотой «режь длительность» противоречил бы сам себе. Согласуется с примером §3.
- `volume_reduction_pct` считается по **тренировочным дням** (race-day 0 исключён) — литературные коридоры §5 описывают срез тренировочного объёма; включение нуля гонки завышало бы срез на ~4–5 пп.
- `taper_days` — включительно с race day (как в примере §3: 06-15..06-28 = 14).

**Вне Phase 1:** никакого surface — ни MCP, ни REST, ни UI. Только тестируемая функция.

### Phase 2 — surface (read-only MCP) ✅ (2026-06-12)

- [x] MCP tool `get_taper_plan(goal_id?, race_date?, race_distance_class?)` — `mcp_server/tools/taper.py`, тонкий wrapper → `build_taper_plan`, `get_current_user_id()`, без `user_id` в параметрах. Регистрация: `server.py` + группа `analysis` и keywords «тейпер»/«taper»/«подводк» в `bot/tool_filter.py`.
- [x] Резолв вводных: CTL/ATL — **de-planned** через `recompute_today_loads` (Intervals подмешивает плановые тренировки в утренний CTL), fallback последняя wellness-строка; peak daily load — `max(ctl_now, медиана дневного TSS лучшего rolling-7d окна за 42 дня)` (кандидат §7 принят); гонка — `goal_id` → primary goal (`get_goal_dto`, RACE_A first) → явный `race_date`; класс дистанции — параметр или эвристика по `event_name` (70.3/ironman/marathon→long, sprint/5k/parkrun→short).
- [x] Refusal gates (§6): `no_future_race` / `goal_not_found` / `invalid_race_date` / `race_date_in_past` / `invalid_distance_class` / `no_wellness_data`; история < 14 дней → fallback `peak = ctl_now` + warning `peak_load_fallback_ctl`; early/late/degenerate/low_ctl отдаёт ядро.
- [x] Чтение — ничего не мутирует. Тесты: `tests/mcp/test_taper_tool.py` (14, mock-based).

**Deviations Phase 2:**
- Параметр `race_type` (A/B/C) из сигнатуры убран — ядро коридоры ключует distance-class'ом, не приоритетом гонки; вместо него `race_distance_class`.
- `fitness_projection` как источник прогнозного CTL для early-режима **не** подключён — заблокировано issue #349 (decay-curve скрывает план, прогноз даёт мусор); early-оценка остаётся flat-CTL симуляцией ядра. Вернуться после фикса #349.

### Phase 3 — интеграция в race-plan (deferred, опц.)

- [ ] Добавить `taper` блок в `race_plans` JSONB через `build_race_plan` (посуточный объём на последние 1–3 недели).
- [ ] Решить: инжектить `docs/knowledge/taper.md` в race-plan system prompt или передавать готовый расчёт из `build_taper_plan` как факты (предпочтительно — детерминированный расчёт, не доверять числам LLM).

### Phase 4 — webapp surface ✅ (2026-06-12)

Экран: **LoadDetail** (`webapp/src/pages/LoadDetail.tsx`) — единственный экран, где будущее уже
рендерится (CTL/ATL/TSB dashed-forecast, planned TSS-бары как opacity 0.55 + diagonal hatch).
Хедер-коммент «no forecast» в файле устарел — сверяться с кодом `SportTssChart`, не с ним.

Визуальный контракт (решено 2026-06-12, см. Decisions log):

- [x] **TSS-бар чарт:** plan vs taper разнесены по **геометрии**. Плановые тренировки —
  hatched-бары; тейпер-таргеты — **ступенчатая линия** поверх будущих баров (`--color-taper`
  #8b5cf6, отдельный токен вне спортивной палитры). Бар выше линии = день с перебором vs
  бюджета. Scrub-callout показывает «Taper: N» на днях окна; легенда — чип «Taper budget».
- [x] **Единый гейт видимости** (фикс H1 ревью v2, 2026-06-12): все taper-поверхности (линия,
  тинт, RACE-флаг, чип легенды, TSB-точка) гейтятся одним условием `taperOnChart =
  targets && dates.length <= TAPER_AXIS_MAX_DAYS (70)` в родителе — порознь они рассинхронились
  (легенда по `pastDays`, чарт по `N` с прогнозным расширением: на 1m N≈58 оверлей пропадал, чип
  оставался). При видимом тейпере `SportTssChart` принудительно остаётся в **daily-режиме**
  (weekly-агрегация усредняет бюджет в шум); на 3m+ (118+ слотов, ~2px бары) оверлей скрыт
  целиком и консистентно. Контракт-связка: `_FORECAST_FALLBACK_DAYS (28) >=
  _TAPER_EARLY_HORIZON_DAYS (21)` — race day всегда на оси при видимом оверлее
  (закомментировано с обеих сторон).
- [x] Фоновый тинт окна тейпера + вертикальный RACE-флаг на дне гонки.
- [x] **TSB-чарт:** одна прогнозная точка race-day TSB (`RACE +N` лейбл, цвет — зона
  приземления; клампится в фиксированный y-домен, но лейбл показывает реальное значение).
  Второй dashed-кривой нет — два пунктира в одних осях нечитаемы.
- [x] API: **новый `GET /api/taper-plan`** (`require_viewer` + `get_data_user_id`) — не поле в
  `/api/training-load` (тот тяжёлый, тейпер релевантен только при гонке в горизонте).
  Резолв-логика вынесена в **`data/taper_service.py`** (паттерн `race_plan_service.py`) — MCP-тул
  и API стали тонкими обёртками над `get_taper_plan_for_user`, чат и webapp не могут разойтись.
  Тесты переехали: `tests/test_taper_service.py` (gates + резолв), `tests/mcp/test_taper_tool.py`
  и `tests/api/test_taper_plan_endpoint.py` — делегация + wiring через dep chain.
- Грациозная деградация: `available: false` / early-режим (пустые `daily_targets`) / 500 от
  API → оверлей просто не рендерится, чарт не зависит от этого fetch'а. Off-axis таргеты
  (гонка за forecast-горизонтом) молча отбрасываются — линия обрывается на краю оси.

### Phase 5 — morning report line (deferred, опц.)

- [ ] В окне тейпера детерминированно подмешивать строку в контекст
  `actor_compose_user_morning_report`: «день N/L тейпера, таргет сегодня ~X TSS, режь
  длительность — не интенсивность». Без дополнительных вызовов Claude — готовая строка из
  `build_taper_plan`, модель её только вплетает в текст.

### Вне scope (всего)

- Генерация конкретных тренировок тейпера (это шаг `suggest_workout` / `training-architect`, не этой фичи — фича отдаёт TSS-бюджет и правила, не структуру сессий).
- Локальный пересчёт CTL/ATL (берём из Intervals.icu).
- Замена 5 TSB-зон или k₁=k₂=1 модели в TSB — `CTL − 2·ATL` используется **только** как целевая функция оптимизации тейпера, не как новый показатель формы во фронте.
- Авто-генерация по cron — только по явному запросу (как race-plan).

---

## 3. Входы и выходы

### Inputs (Phase 1 функция — pure)

| Поле | Тип | Источник в Phase 2 |
|---|---|---|
| `race_date` | date | `athlete_goals.event_date` |
| `days_to_race` | int | derived |
| `ctl_now`, `atl_now` | float | `wellness` (последний день) |
| `peak_daily_load` | float | медиана дневного TSS пиковой недели за 4–6 нед (`activities`) |
| `race_type` | enum A/B/C | `athlete_goals` / `docs/BUSINESS_RULES.md` |
| `race_distance_class` | enum | для event-specific длины (§5) |

### Output (предлагаемая форма)

```jsonc
{
  "taper_start_date": "2026-06-15",
  "taper_days": 14,                  // выбрано grid-search
  "tau_taper": 4,                    // выбрано grid-search
  "volume_reduction_pct": 52,        // итоговый срез vs peak
  "daily_targets": [                 // от taper_start до race_date
    {"date": "2026-06-15", "target_tss": 78, "note": "режь длительность, не интенсивность"},
    {"date": "2026-06-16", "target_tss": 71},
    // ...
    {"date": "2026-06-28", "target_tss": 0, "note": "race day"}
  ],
  "projected_race_day": {"ctl": 71.2, "atl": 18.4, "tsb": 52.8, "p_banister": 34.4, "tsb_zone": "transition"},
  "rules": [
    "Держи интенсивность: race-pace/качественные сессии оставь, режь объём через длительность.",
    "Держи частоту сессий — не выкидывай тренировочные дни.",
    "Опционально: +20–30% load за 3 дня до гонки (two-phase), затем съезд."
  ],
  "confidence": "late"               // зависит от days_to_race + полноты данных
}
```

---

## 4. Алгоритм (детерминированный)

1. **Forward-sim.** Для кандидата `(taper_days L, τ_taper)`: дневная нагрузка `w(i) = peak_daily_load · e^(−i/τ)` для `i = 0..L−1`. Прогон EWMA от `ctl_now/atl_now`:
   `CTL_t = CTL_{t−1} + (w_t − CTL_{t−1})/42`, `ATL_t = ATL_{t−1} + (w_t − ATL_{t−1})/7`.
2. **Целевая функция.** `p(race_day) = CTL_race − 2·ATL_race` (Banister k₁=1, k₂=2; см. `fitness-fatigue-model.md`).
3. **Grid-search.** `L ∈ [7..21]` (с event-specific clamp, §5), `τ ∈ [3..5]`. Tier-отбор: (а) constraint — `TSB_race` в `fresh` (+5..+25) / `transition` (≥+25), иначе все кандидаты + warning; (б) коридор объёмного среза §5 (ближайшие кандидаты, см. п.5); (в) max `p(race_day)`. **На практике выбор решает коридор, не p:** reduction — почти инъективная функция `(L, τ)` без участия CTL/ATL, фильтр коридора обычно схлопывает пул до одного кандидата, и p остаётся tie-break'ом. Выбранная пара `(L, τ)` — свойство класса гонки, а не атлета; CTL/ATL влияют на проекцию, не на выбор (закреплено тестом `test_choice_is_class_property_not_athlete_property`).
4. **Условие достаточности.** Если `ctl_now` низкий (напр. < race-type порога) — тейпер мельче (меньше глубина среза), потому что нечего «раскрывать». Грубый guard, не оптимизация overload-блока (это вне scope).
5. **Итоговый срез** `volume_reduction_pct` = `1 − mean(daily_targets)/peak_daily_load`. Зажать в коридор §5; если grid-выбор вышел за коридор — клампить длину/τ, не объём.

> Вся математика — арифметика на CTL/ATL, которые **уже есть**. Никаких новых τ не вводим (наследуем 42/7 от Intervals.icu).

---

## 5. Дефолты по типу события

Из `docs/knowledge/taper.md` (Bosquet / Le Meur / Smyth / Fortes / Divsalar):

| `race_distance_class` | `taper_days` коридор | `volume_reduction_pct` коридор | Примечание |
|---|---|---|---|
| Long-course (IM / 70.3 / марафон) | 14–21 | 50–65% | strict монотонный спад; ≥4 нед не уходить |
| Standard endurance (дефолт) | 10–14 | 41–60% | общий оптимум |
| Short / анаэробное (спринт, ≤5к, 200м swim) | 7–14 | 50–70% | крутой ранний спад; 4 нед вредит |

Глобальные правила (одинаковы для всех): **интенсивность — hold/чуть↑**, **частота — hold**, **форма — прогрессивный спад**. Эти три не параметризуются типом гонки — они константны (§ методология).

---

## 6. Refusal gates (Phase 2)

- Нет активной будущей гонки (`athlete_goals` пуст / event_date в прошлом) → отказ с подсказкой создать goal.
- `days_to_race > 21` → **не отказ** (решено 2026-06-12): вернуть оценку `taper_start_date`
  (grid-search от прогнозного CTL/ATL из `fitness_projection` на момент старта тейпера) с
  `confidence: "early"` и пометкой «ориентировочно, точный посуточный план — ближе к дате».
  Посуточные `daily_targets` в early-режиме не возвращать (пустой список), и
  `projected_race_day` тоже `None` — обе вещи на таком горизонте симулируются от сегодняшних
  CTL/ATL, удержанных плоско на 20+ дней, т.е. создают ровно ту ложную точность, ради
  подавления которой gate и существует. Phase 2 caller пересчитывает ближе к дате. Вопрос
  «когда начинать тейпер?» должен работать в чате в любой момент.
- `days_to_race < 2` → вернуть остаток графика (degenerate, 1–2 дня), без grid-search.
- Недостаточно данных для `peak_daily_load` (< 2–3 недель activities) → отказ или fallback на CTL·коэффициент с warning.
- Низкий `ctl_now` → не отказ, но warning «фитнес не набран, тейпер даст мало».

---

## 7. Открытые вопросы (решить перед Phase 1)

- **`peak_daily_load` — как считать?** Медиана дневного TSS лучшей недели за 4–6 нед vs `CTL_now` напрямую (CTL ≈ среднесуточная нагрузка по построению). Кандидат: `max(CTL_now, медиана пиковой недели)`.
- **Per-sport или global?** Тейпер можно считать на глобальном CTL (проще) или per-sport (точнее для мультиспорта — `calculate_sport_ctl` уже есть). Phase 1 — global, per-sport как расширение.
- **Two-phase bump** (+20–30% за 3 дня) — включать в дефолт или как опцию? Доказательная база слабее остального консенсуса (Thomas). Предложение: вернуть как `rules` подсказку, не вшивать в `daily_targets`.
- **Sprint priming** — добавлять в вывод подсказку про еженедельный микро-спринт (3–6×10–30с, см. `taper.md` «Спринты в тейпере»)? Доказательства из off-season, не pre-race — отдавать как опциональное `rules`-правило с оговоркой, не как обязательную сессию.
- **Выход — TSS или %peak?** TSS конкретнее (ложится на Intervals load), но требует надёжного `peak_daily_load`. Альтернатива — отдавать оба: абсолютный TSS + `pct_of_peak`.

---

## 8. Decisions log

- **2026-05-30** — Спека создана по итогам разбора Academia-бандла (Banister 1999 + 9 современных работ). Решено: **A (knowledge-doc) сделать сразу, код `taper_planner` не писать — зафиксировать спекой.** Параметры тейпера обновлены vs Banister 1999: держать частоту (не дни отдыха), срез 41–60% (не 50–65%), event-specific длина, fast-vs-slow τ не разрешён. Целевая функция оптимизации — `CTL − 2·ATL`, без замены TSB-показателя во фронте.
- **2026-06-12 (ревью v2)** — Закреплено: (1) выбор `(L, τ)` де-факто определяется коридором среза, p — tie-break (§4.3 переформулирован, свойство закреплено тестом инвариантности к CTL/ATL); (2) в early-режиме подавляется и `tsb_lands_outside_target` — это вердикт той же скрытой симуляции; `low_ctl` остаётся (считается от реального сегодняшнего CTL).
- **2026-06-12** — Зафиксированы surface-решения (обсуждение порядка внедрения). (1) Порядок: чат через MCP (Phase 2) — основная поверхность, webapp — Phase 4, morning-report строка — Phase 5; отдельной страницы `/taper` не будет — нет своей сущности. (2) Webapp-экран — **LoadDetail** (будущее там уже рендерится; planned-бары = hatch + opacity 0.55). (3) Plan vs taper различаем **геометрией**: плановые тренировки — бары, тейпер-бюджет — ступенчатая линия в своём цвете; конфликт «бар выше линии» — фича, а не шум. (4) На TSB-чарте — только точка race-day приземления, без второй dashed-кривой. (5) Gate `days_to_race > 21` смягчён с отказа до early-оценки `taper_start_date` от прогнозного CTL (`fitness_projection`), без посуточных таргетов — «когда начинать тейпер?» отвечается в любой момент.
