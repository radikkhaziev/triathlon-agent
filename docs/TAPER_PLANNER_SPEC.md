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

### Phase 1 — детерминированное ядро + тесты

- [ ] Чистая функция `build_taper_plan(...)` в `data/metrics.py` (без I/O, без БД — только числа на вход/выход).
- [ ] EWMA forward-simulation CTL/ATL под экспоненциально спадающей нагрузкой (§4).
- [ ] Grid-search длины (7–21 д) × `τ_taper` (3–5 д), максимизация `p = CTL − 2·ATL` с приземлением TSB в `fresh`/`transition` (§4).
- [ ] Event-specific дефолты по типу гонки (§5).
- [ ] Возврат: посуточный график TSS, `taper_start_date`, прогноз race-day CTL/ATL/TSB/p, текстовые правила.
- [ ] Детерминированные unit-тесты (паттерн проекта «deterministic tests for metric calculations»): монотонность спада, корректность EWMA, что более длинная гонка → более длинный тейпер, что int=hold (не режется), edge-cases (низкий CTL, гонка через 3 дня).

**Вне Phase 1:** никакого surface — ни MCP, ни REST, ни UI. Только тестируемая функция.

### Phase 2 — surface (read-only MCP)

- [ ] MCP tool `get_taper_plan(goal_id?, race_date?, race_type?)` (тонкий wrapper → `build_taper_plan`), `get_current_user_id()`, без `user_id` в параметрах.
- [ ] Резолв вводных: текущие CTL/ATL из `wellness`, прогноз из `fitness_projection`, peak daily load из последних 4–6 недель activities.
- [ ] Refusal gates (§6): нет будущей гонки / гонка слишком далеко / недостаточно данных.
- [ ] Чтение — фича ничего не мутирует (не создаёт workouts). Атлет/Claude видят план, дальше — отдельный шаг генерации сессий.

### Phase 3 — интеграция в race-plan (deferred, опц.)

- [ ] Добавить `taper` блок в `race_plans` JSONB через `build_race_plan` (посуточный объём на последние 1–3 недели).
- [ ] Решить: инжектить `docs/knowledge/taper.md` в race-plan system prompt или передавать готовый расчёт из `build_taper_plan` как факты (предпочтительно — детерминированный расчёт, не доверять числам LLM).

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
3. **Grid-search.** `L ∈ [7..21]` (с event-specific clamp, §5), `τ ∈ [3..5]`. Выбрать пару, максимизирующую `p(race_day)`, **при условии** что `TSB_race` попадает в `fresh` (+5..+25) или `transition` (≥+25). Если ни один кандидат не приземляет TSB в зону — вернуть лучший по `p` с warning.
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
- `days_to_race > 21` → «ещё рано, тейпер начинается за ≤3 недели; вернись ближе к дате».
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
