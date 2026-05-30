# Training Intensity Distribution (Polarization) — Theory & Methodology

> Сколько времени проводить в каждой интенсивности. База для таргета polarization-фичи (`docs/INTENSITY_DISTRIBUTION_SPEC.md`). Реализация: `data/metrics.py` (`polarization_index`, `target_distribution`, `delta_vs_target`, `compute_polarization`).

---

## Трёхзонная модель

| Зона | Граница | %HRmax | Лактат | Borg | Что это |
|---|---|---|---|---|---|
| **Z1** (easy/low) | < VT1/LT1 | 50–72% | <2 mM | <13 | Аэробная база, «легко» |
| **Z2** (moderate/threshold) | VT1→VT2 (LT1→LT2) | 72–85% | 2–4 mM | — | «Серая зона» — слишком тяжело для базы, слишком легко для стимула |
| **Z3** (hard/severe) | > VT2/LT2 | 92–100% | >4 mM | ≥race-pace | Интенсивная работа |

Маппинг на наши `hr_zone_times` (5–7 зон Intervals.icu): **Low = Z1+Z2** движка, **Mid = Z3**, **High = Z4+**. Эту агрегацию сохраняем — не вводим вторую зонную систему (`data/metrics.py:compute_polarization`).

---

## Целевое распределение

| Источник | Сплит Z1/Z2/Z3 | Контекст |
|---|---|---|
| Esteve-Lanao 2007 (RCT, n=12) | **80/12/8** | Экспериментальный оптимум: +30% к приросту 10к при той же нагрузке |
| Sperlich 2023 (437 элит) | **85/7/6** (медиана) | Кросс-спорт; 91% TID держат >60% в Z1 |
| Stöggl & Sperlich 2015 | ~80 / ~20 (split к Z3) | Элит-консенсус; поляр > threshold ≈ HVLIT |
| Galbraith 2014 (1 год) | ~86 / ~14 (>LT) | Что трен. бегуны держат сами весь год |

**Дефолтный таргет:** ~**80% Z1**. **Потолки:** `Z3 ≤ ~10%` (выше 15% → перетрен за 2–3 нед, Esteve-Lanao pilot), `Z2 ≤ ~20%` (выше — серая зона вредит well-trained, ES падает).

---

## Polarization Index (PI)

Формула Treff et al. (2019), классификатор «насколько распределение полярное»:

```
PI = log10( (Z1 / Z2) × Z3 )     # Z1,Z2,Z3 — проценты времени (low/mid/high)
PI > 2.0  → polarized
```

> ⚠️ Эквивалентная каноническая запись `log10((Z1÷Z2)×Z3×100)` использует **доли** (0–1) для Z3; с процентами множитель `×100` уходит. У нас на вход проценты → формула без `×100` (`data/metrics.py:polarization_index`).

**Важный нюанс «polarized» vs PI:** классический ярлык **80/12/8** (Seiler/Esteve-Lanao, по 3-зонной VT-модели) имеет **Z2 > Z3** и даёт **PI ≈ 1.73** — то есть по строгому индексу это **пирамидальное**, не полярное. Истинно полярное требует **Z3 > Z2** (напр. 80/6/14 → PI 2.27). Оба валидны; путаница снимается фазой (ниже). Sperlich 2023: 51% TID пирамидальные, 37% полярные.

**Degenerate:** при Z2≈0 или Z3≈0 индекс не определён (деление / log от 0) → возвращаем `None`, откатываемся на %-классификатор паттерна.

---

## Фазозависимость

Распределение не статично — меняется по макроциклу (Stöggl & Sperlich 2015):

| Фаза | Модель | Профиль | Логика |
|---|---|---|---|
| **base / build** | **пирамидальное** | Z1 высокий (84–94%), Z2 умеренный, Z3 < Z2 | Набор аэробной базы; объём важнее |
| **peak / race / taper** | **полярное** | Z3 > Z2, PI > 2.0 | Ближе к гонке добавляем интенсивность — **за счёт Z3, не Z2** |

Ключевое правило перехода: к гонке поднимают **Z3**, а не Z2. Добавлять нагрузку в серую зону — ошибка на любой фазе.

---

## Калибровка по спорту

Зоны держатся по-разному (Sperlich 2023):

- **Run / Swim:** ~80–85% Z1, Z2 < 12% — строгий пирамид/поляр.
- **Bike:** велосипед естественно несёт **больше Z2** (велосипедисты <72% Z1, >16% Z2; на практике 30–35% Z2 норма). Единый порог дал бы ложный флаг «threshold» на байке.

Поэтому таргет-band в коде разный для `ride` vs `run/swim` (`data/metrics.py:_TID_BANDS`): у байка ниже Z1-таргет и выше Z2-потолок.

---

## Применение в проекте

| Что | Где | Статус |
|---|---|---|
| Агрегат Low/Mid/High + паттерн | `data/metrics.py:compute_polarization` | ✅ было |
| PI-число | `data/metrics.py:polarization_index` | ✅ Phase 1 |
| Целевой band (спорт×фаза) | `data/metrics.py:target_distribution` | ✅ Phase 1 |
| Отклонение от таргета | `data/metrics.py:delta_vs_target` | ✅ Phase 1 |
| Trend-сигналы (дрейф/тейпер/деролд) | `data/metrics.py:compute_polarization_trends` | ✅ было |
| target/delta внутри окон | `mcp_server/tools/polarization.py:get_polarization_multi_window` | ✅ Phase 1 |
| Target-линия в UI + проактивный prompt | webapp / `bot/prompts.py` | ⏳ Phase 2 |
| Авто-деривация фазы из календаря гонок | — | ⏳ Phase 3 |

**Вне scope:** power/pace-распределение (остаёмся на HR-зонах), генерация корректирующих сессий (это `suggest_workout`), свим (зонное распределение шумное у любителя).

---

## Источники

| Работа | Вклад |
|---|---|
| Esteve-Lanao, Foster, Seiler & Lucia 2007 — *Impact of Training Intensity Distribution on Performance*, J Strength Cond Res 21(3):943–949 | RCT: 80/12/8 оптимум, +30% к приросту при той же нагрузке; Z3>15% → overreach |
| Stöggl & Sperlich 2015 — *The training intensity distribution among well-trained and elite endurance athletes*, Front Physiol 6:295 | Элит-консенсус ~80/20; фазы пирамида (база) → поляр (гонка) |
| Sperlich, Matzka & Holmberg 2023 — *The proportional distribution of training at different intensities*, Front Sports Act Living 5:1258585 | Медиана 85/7/6 (437 атлетов); PI-формула + порог 2.0; спорт-калибровка (байк больше Z2) |
| Galbraith et al. 2014 — *A 1-Year Study of Endurance Runners*, IJSPP 9:1019–1025 | ~86/14 что трен. бегуны держат год; объём + доля >LT драйвят CS/VO₂max |
| Treff et al. 2019 — Polarization Index | Каноническая формула `PI = log10((Z1/Z2)×Z3×100)` |
