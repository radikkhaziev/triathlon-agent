# Intensity Distribution (Polarization) — Target & Refinement Spec

> Расширение polarization-фичи: от «описываем распределение» к «задаём целевой таргет + фазовую/спорт-калибровку». База: peer-reviewed TID-числа (Esteve-Lanao 2007, Stöggl & Sperlich 2015, Sperlich 2023, Galbraith 2014). Методология + источники: `docs/knowledge/intensity-distribution.md`.

**Status:** ✅ COMPLETE — Phase 0+1+2 shipped (33 tests, flake8 clean, webapp build green, code-reviewed ×2). Phase 3 closed at the ±14d auto-phase gate; full periodization deliberately **not** built (см. §2 + Decisions log).

---

## 1. Что фича делает

Считает HR-зонное распределение по 4 окнам (7/14/28/56д), классифицирует паттерн (polarized/pyramidal/threshold/too_easy/too_hard), ловит дрейфы, и — после этой спеки — **сравнивает с целевым таргетом** с фазовой и спорт-калибровкой:

- **Target-driven.** Не просто «у тебя 72/20/8», а «нужно ~80/12/8» с проактивным коучингом.
- **Phase-aware.** База → пирамидальное (Z1≥84%), ближе к гонке → полярное (за счёт Z3, не Z2). Авто-фаза из ближайшей гонки.
- **Sport-calibrated.** Bike естественно держит больше Z2 (~30–35%) → отдельный band, чтобы не плодить ложные «threshold»-флаги.

---

## 2. Целевые правила (числа — в коде)

- **Целевой сплит:** `80/12/8`. Дефолтный band `80/15/5 → 80/10/10`. Потолки: `Z3 ≤ ~10%`, `Z2 ≤ ~20%` (серая зона).
- **PI-индекс:** `PI = log10((Z1/Z2)×Z3)` на процентах (без `×100` — вход проценты, не доли). `PI > 2.0 → polarized` (Sperlich 2023). PI-gate только для run/swim.
- **Спорт-калибровка:** run/swim Z1≥84% (base) / Z2≤14; bike Z1 72/66% / Z2≤32.
- **Фаза:** base/build → пирамида; peak/race/taper → поляр. Авто-фаза из календаря гонок: `≤14d → peak/поляр`, иначе `base/пирамида`.

Реализация (числа, формула, band-таблица — там, не дублировать здесь): `data/metrics.py` — `polarization_index()` (~920), `_TID_BANDS` / `_PHASE_MODEL` / `_PI_TARGET_SPORTS` (~1051), `target_distribution()` (~1082), `delta_vs_target()` (~1105), `_resolve_training_phase()`. Зонная модель (3-зонная, анкеры VT1/VT2 / %HRmax / лактат) и источники таргет-чисел — `docs/knowledge/intensity-distribution.md`.

**Surface:** `/api/polarization` + `get_polarization_index` MCP-тул отдают `target`/`delta`/`polarization_index` внутри каждого окна; `ZoneDistributionCard` (`webapp/src/pages/DashboardLoadTab.tsx`) рендерит target-маркер + verdict-чип + PI; `bot/prompts.py` эмитит проактивную подсказку при `verdict != on_target`.

### Phase 3 — почему остановились

Авто-фаза (±14d gate, `_PEAK_TAPER_DAYS=14`) реализована. Полная периодизация (base/build/peak) **не делается**: `base` и `build` обе → pyramidal, поэтому различение фаз без нового band'а = **no-op**. Осмысленный вариант (transitional band для плавного рампа base→гонка) — третий набор чисел со слабее доказательной базой, чем 80/20-ядро; бинарный gate literature-clean и достаточен. Если когда-нибудь понадобится плавный рамп — см. рецепт «3-тир + transitional» в Decisions log ниже.

### Вне scope

- Локальное переопределение зон (берём `hr_zone_times` из Intervals.icu webhook).
- Генерация корректирующих сессий (это `suggest_workout` / `training-architect`).
- Power/pace-based распределение — остаёмся на HR-зонах. `power_zone_times` есть в БД, но агрегация — отдельная задача.

---

## 3. Decisions log

- **2026-05-30** — Спека создана по итогам разбора Academia-бандла (TID-кластер: Esteve-Lanao, Stöggl & Sperlich, Sperlich 2023, Galbraith). Ключевое: фича переходит от описания к **таргету 80/12/8 + PI>2.0**, добавляет **фазовую** (пирамида база → поляр гонка) и **спорт** (bike больше Z2) калибровку. Power/pace-распределение и генерация корректирующих сессий — вне scope. PI **дополняет** 5-паттерновый %-классификатор, не заменяет (паттерны too_easy/too_hard несут смысл, которого PI не даёт).
- **2026-05-30** — Phase 0+1+2 реализованы в одном заходе. Phase 2 surface не потребовал правок API/тула — `_attach_targets` кладёт target/delta внутрь окон, данные текут as-is. Code review поймал 1 critical (low_pct=0 → log10(0) краш) + ride-polarized PI-несовместимость — исправлены. Auto-phase (±14d gate) подтянут вперёд из Phase 3 по решению пользователя.
- **2026-05-30** — **Phase 3 закрыт без полной периодизации.** Причина: `base` и `build` обе → pyramidal, поэтому 3-тир без нового band'а = no-op. Рассмотрен «transitional band» (build→промежуточный ~82/11/9 run, 3-тир резолвер ≤14/≤42/else) для плавного рампа base→гонка (Stöggl). Отклонён как анти-оверинжиниринг: третий набор чисел со слабее доказательной базой, чем 80/20-ядро. **Если понадобится плавный рамп:** добавить `transitional` в `_TID_BANDS` (3 модели), `_PHASE_MODEL: build→transitional`, `_resolve_training_phase` 3-тир через `BUILD_PHASE_CADENCE_DAYS=42`; surface менять не надо (рендер дженерик). Фича считается завершённой.
