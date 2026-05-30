# Intensity Distribution (Polarization) — Target & Refinement Spec

> Расширение существующей polarization-фичи: от «описываем распределение» к «задаём целевой таргет + фазовую/спорт-калибровку». База: peer-reviewed TID-числа (Esteve-Lanao 2007, Stöggl & Sperlich 2015, Sperlich 2023, Galbraith 2014). Методбаза: `docs/knowledge/intensity-distribution.md` (создаётся в Phase 0).

**Status:** ✅ COMPLETE — Phase 0 + 1 + 2 shipped (33 tests, flake8 clean, webapp build green, code-reviewed ×2). Phase 3 closed at the ±14d auto-phase gate; full periodization deliberately **not** built (base=build=pyramidal → no-op; see §2 + Decisions log).

**Related:**

| Issue / Spec | Связь |
|---|---|
| `mcp_server/tools/polarization.py` | `get_polarization_index` — 4 окна (7/14/28/56д) + сигналы |
| `data/metrics.py` | `compute_polarization`, `_classify_polarization`, `compute_polarization_trends` (~670–750) |
| `api/routers/activities.py` | `GET /api/polarization?sport=&days=` |
| `webapp/src/pages/DashboardLoadTab.tsx` | `ZoneDistributionCard` (stacked bar + window picker + pattern pill + signals) |
| `bot/prompts.py` | Morning-report: эмитит coaching-сигналы при непустых signals |
| `data/db/activity.py` | `hr_zone_times` (массив сек по зонам) + Intervals' `polarization_index` per-activity |
| `docs/TRAINING_PROGRESSION_SPEC.md` | polarization как фича EF-модели (не таргет) |
| `docs/knowledge/intensity-distribution.md` | Методология + дефолтные таргеты + источники (Phase 0) |

---

## 1. Мотивация

Фича уже считает распределение по 4 окнам, классифицирует паттерн (polarized/pyramidal/threshold/too_easy/too_hard) и ловит дрейфы. Но три пробела:

1. **Нет целевого таргета.** Система говорит «у тебя 72/20/8», но не «нужно ~80/12/8». Коучинг реактивный (сигналы при аномалии), без проактивной цели.
2. **Phase-blind.** Одни и те же пороги в базе и перед гонкой. Литература: база → пирамидальное (84–94% Z1), ближе к гонке → полярное за счёт Z3 (не Z2).
3. **Sport-agnostic пороги.** Велосипед естественно держит больше Z2 (~30–35%), наши единые пороги дают ложные «threshold»-флаги на байке.

Самодельные пороги (`polarized: low≥75/mid≤15/high≥5`) заменяемы на peer-reviewed: целевой сплит **80/12/8** и индекс **PI = log10(Z1 ÷ Z2 × Z3 × 100) > 2.0** (Sperlich 2023).

---

## 2. Scope (фазы)

### Phase 0 — knowledge-doc ✅

- [x] `docs/knowledge/intensity-distribution.md` — 3-зонная модель (анкеры VT1/VT2, %HRmax, лактат), целевой сплит 80/12/8, медиана 85/7/6, PI-формула + порог 2.0, правила-ограничители (Z3≤10%, Z2>20% = ловушка), фазозависимость (пирамида→поляр), спорт-калибровка, источники.

### Phase 1 — метрики + таргет (pure logic + тесты) ✅

- [x] **PI-формула** `polarization_index()` = `log10((Z1/Z2)×Z3)` (проценты; `data/metrics.py`). Guard на low/mid/high ≤0 → `None`. **Дополняет** %-классификатор, не заменяет (решение §6). NB: формула без `×100` (вход — проценты, не доли — см. knowledge-doc).
- [x] `target_distribution(sport, phase=None)` → band Z1/Z2/Z3 + потолки; `phase=None` → dual band (base+race).
- [x] **Спорт-калибровка:** `_TID_BANDS` — run/swim Z1≥84% (base) / Z2≤14; bike Z1 72/66% / Z2≤32. PI-gate только run/swim (`_PI_TARGET_SPORTS`).
- [x] **Phase-калибровка:** `_PHASE_MODEL` base/build→пирамида, peak/race/taper→поляр. Источник фазы — §7 (deferred Phase 3, пока explicit-параметр).
- [x] `compute_polarization` отдаёт `polarization_index`; `delta_vs_target()` считает gaps+verdict; `get_polarization_multi_window` кладёт `target`+`delta` в каждое окно.
- [x] Детерминированные тесты (33): PI на 80/6/14→2.27 / 80/12/8→1.73, degenerate (low/mid/high=0), спорт-различие вердикта (65/30/5 run vs ride), фаза меняет таргет, ride без PI-gate, insufficient_data.

**Code review:** 1 critical (low_pct=0 → log10(0) краш) + ride-polarized PI-несовместимость — исправлены. Деталь: `target`/`delta` теперь сериализуются в live `/api/polarization` + MCP response (по punch-list item 4), но UI/prompt их пока не рендерят — это Phase 2.

### Phase 2 — surface ✅

- [x] `/api/polarization` + MCP-тул отдают `target`/`delta`/`polarization_index` (через `_attach_targets` внутри окон — surface-код не менялся, данные текут as-is).
- [x] `ZoneDistributionCard`: target-маркер на stacked-bar (≈low_pct_target), verdict-чип, target-строка «🎯 Target (phase): X% easy · Z2 ≤ Y», PI в totals.
- [x] `bot/prompts.py`: проактивная подсказка при `delta.verdict != on_target` (gated `signals пуст и verdict=on_target → молчать`); docstring тула описывает target/delta/PI.
- [x] `_resolve_training_phase(user_id)` — авто-фаза из ближайшей гонки (≤14d→peak/поляр, иначе base/пирамида), mirror `tasks/utils.py`.

### Phase 3 — phase-awareness ✅ (closed at the ±14d gate — won't expand)

- [x] Фаза из календаря гонок (`athlete_goals` + `_PEAK_TAPER_DAYS=14`) → авто-выбор пирамида/поляр (Phase 2).
- [~] ~~Полная периодизация (base/build/peak)~~ — **не делаем** (решение 2026-05-30). `base` и `build` обе → pyramidal, поэтому различение фаз без нового band'а = no-op. Осмысленный вариант (transitional band для плавного рампа) — третий набор чисел со слабее доказательной базой, чем 80/20-ядро; бинарный gate literature-clean и достаточен. Если когда-нибудь понадобится плавный рамп — см. «3-тир + transitional» в Decisions log ниже.

### Вне scope

- Локальное переопределение зон (берём `hr_zone_times` из Intervals.icu webhook).
- Генерация конкретных сессий для коррекции распределения (это `suggest_workout` / `training-architect`).
- Power/pace-based распределение — остаёмся на HR-зонах (как сейчас). `power_zone_times` есть в БД, но агрегация — отдельная задача.

---

## 3. Целевые числа (из knowledge-doc)

| Источник | Сплит Z1/Z2/Z3 | Контекст |
|---|---|---|
| Esteve-Lanao 2007 (RCT, n=12) | **80/12/8** | Экспериментальный оптимум: +30% к приросту 10к при той же нагрузке |
| Sperlich 2023 (437 элит) | **85/7/6** (медиана) | Кросс-спорт; PI>2.0 = polarized |
| Stöggl & Sperlich 2015 | ~80 / ~20 (split к Z3) | Элит-консенсус; поляр > threshold ≈ HVLIT |
| Galbraith 2014 (1 год) | ~86 / ~14 (>LT) | Что трен. бегуны держат сами весь год |

**Дефолтный таргет-band:** `80/15/5 → 80/10/10`. **Потолки:** `Z3 ≤ ~10%` (выше 15% → перетрен за 2–3 нед), `Z2 ≤ ~20%` (выше — «серая зона» вредит). **Bike:** Z1 58–65% / Z2 30–35% — норма, не флаг.

---

## 4. Зонная модель (3-зонная, анкеры)

| Зона | Граница | %HRmax | Лактат | Borg |
|---|---|---|---|---|
| Z1 (easy/low) | < VT1/LT1 | 50–72% | <2 mM | <13 |
| Z2 (moderate/threshold) | VT1→VT2 | 72–85% | 2–4 mM | — |
| Z3 (hard/severe) | > VT2/LT2 | 92–100% | >4 mM | ≥race-pace |

Маппинг на наши `hr_zone_times`: Low = Z1+Z2 движка Intervals (≤VT1), Mid = Z3, High = Z4+. **NB:** наши текущие Low/Mid/High уже агрегируют 5–7 зон Intervals в 3 — сохранить это соответствие, не вводить вторую зонную систему.

---

## 5. Классификатор PI

```
PI = log10( (Z1 / Z2) × Z3 × 100 )     # Z1,Z2,Z3 — проценты времени
PI > 2.0  → polarized
```

- **Guard:** Z2→0 (деление) или Z3→0 (log от 0) — clamp на малый ε или вернуть degenerate-вердикт «too_easy/too_hard» через старый %-классификатор. Решить в Phase 1.
- **Решение оставить открытым:** PI заменяет 5-паттерновый классификатор или дополняет его. Предложение — **дополняет** (PI как число + наш паттерн как ярлык), т.к. паттерны too_easy/too_hard несут смысл, которого PI не даёт.

---

## 6. Open questions (решить перед Phase 1)

- **Источник «фазы» атлета.** Деривить из `days_to_race` (`athlete_goals` + `PEAK_TAPER_DAYS`)? Или явное поле периодизации? Без надёжной фазы — Phase 1 отдаёт оба таргета (пирамида + поляр) и помечает «зависит от фазы».
- **PI vs %-классификатор** — заменить или дополнить (§5).
- **Спорт-пороги** — захардкодить (run/swim vs bike) или вынести в `athlete_settings`? Дефолт — хардкод констант с комментарием-обоснованием.
- **Свим** — `pace_zone_times` есть, но в polarization сейчас не агрегируется. Включать свим в таргет или оставить run/ride? (Свим у любителя часто интервальный — распределение шумное.)

---

## 7. Decisions log

- **2026-05-30** — Спека создана по итогам разбора Academia-бандла (TID-кластер: Esteve-Lanao, Stöggl & Sperlich, Sperlich 2023, Galbraith). Решено зафиксировать спекой, код не писать. Ключевое: фича переходит от описания к **таргету 80/12/8 + PI>2.0**, добавляет **фазовую** (пирамида база → поляр гонка) и **спорт** (bike больше Z2) калибровку, которых сейчас нет. Power/pace-распределение и генерация корректирующих сессий — вне scope.
- **2026-05-30** — Phase 0+1+2 реализованы в одном заходе (см. §2 чекбоксы). Phase 2 surface не потребовал правок API/тула — `_attach_targets` кладёт target/delta внутрь окон, данные текут as-is. Auto-phase (±14d gate) подтянут вперёд из Phase 3 по решению пользователя.
- **2026-05-30** — **Phase 3 закрыт без полной периодизации.** Причина: `base` и `build` обе → pyramidal, поэтому 3-тир без нового band'а = no-op. Рассмотрен «transitional band» (build→промежуточный ~82/11/9 run, 3-тир резолвер ≤14/≤42/else) для плавного рампа base→гонка (Stöggl). Отклонён как анти-оверинжиниринг: третий набор чисел со слабее доказательной базой, чем 80/20-ядро; бинарный gate literature-clean и достаточен. **Если понадобится плавный рамп:** добавить `transitional` в `_TID_BANDS` (3 модели), `_PHASE_MODEL: build→transitional`, `_resolve_training_phase` 3-тир через `BUILD_PHASE_CADENCE_DAYS=42`; surface менять не надо (рендер дженерик). Фича считается завершённой.
