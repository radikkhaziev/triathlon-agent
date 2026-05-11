# Ramp Test Protocols — DFA a1 Method

Унифицированный ramp-test для Run и Bike, дающий HRVT1/HRVT2 пороги через DFA a1 анализ — пригодны для автоматической калибровки зон в Intervals.icu.

Architectural sibling: `docs/RAMP_TEST_BIKE_SPEC.md` (status + ссылки), implementation в `data/ramp_tests.py` + `mcp_server/tools/` + actor `tasks/actors/workout.py`.

---

## 1. Method overview — DFA a1

Detrended Fluctuation Analysis (alpha-1 scaling exponent) RR-интервалов в скользящем 120-beat окне. Ключевые точки:

| DFA a1 | Физиологическая интерпретация |
|---|---|
| ≥ 1.0 | полностью аэробно |
| **0.75** | **HRVT1 = LT1 = аэробный порог** (Z2/Z3 граница) |
| **0.5** | **HRVT2 = LT2 = анаэробный порог** (Z3/Z4 граница) |
| < 0.5 | анаэробно / VO2max |

Выход: пара (HR, power|pace) для HRVT1 и HRVT2 с R² и confidence score → отображается в LTHR + threshold pace/FTP в Intervals.icu.

Более глубокая теория DFA — `docs/knowledge/dfa-alpha1.md`. Регрессионная методология sigmoid/per-step (deferred) — `docs/DFA_REGRESSION_METHODOLOGY_SPEC.md`.

---

## 2. Universal protocol principles

1. **Нагрузка контролируемая, HR наблюдаемый.** Power для bike, pace для run. **Никогда** HR-target на work-шагах — лаг HR 30-60 sec создаёт positive feedback loop с целью.
2. **Якорь — текущий порог.** Все шаги как % `threshold_pace` (run) или FTP (bike). Self-calibrating: после первого теста цифры подтягиваются.
3. **3 мин шаги — для DFA a1 стабилизации.** ≥3 DFA a1 окна на шаг. Стандарт в литературе (Rogers 2020-2023).
4. **5% инкремент — для разрешения.** Даёт 3-5 bpm HR Δ между соседними шагами.
5. **Top должен пробить HRVT2.** Иначе HRVT2 экстраполируется, не измеряется → R² падает.
6. **Покрытие обоих порогов с запасом.** Минимум 2-3 точки ниже HRVT1, в gray zone, выше HRVT2.
7. **Natural failure как stop-сигнал.** Не «дотерпеть до плана», а «дойти до отказа».
8. **Chest HR strap обязателен.** Оптические датчики не дают валидных RR.

---

## 3. Run protocol

### 3.1 Parameters

| Parameter | Value |
|---|---|
| Anchor | `threshold_pace` (sec/km из `pace_zones_run`) |
| Control unit | pace (km/h) |
| Start | 80% threshold |
| Step | 5% threshold (rounded to 0.5 km/h) |
| Top | 115% threshold |
| Work steps | 8 |
| Step duration | 180 sec (3 min) |
| Warm-up | 600 sec, `hr={units:"%lthr", value:70}` |
| Cool-down | 600 sec, `hr={units:"%lthr", value:70}` |
| Total | ~44 min |
| Default fallback | 295 s/km (4:55/km) — average amateur, если `threshold_pace` пустой |

### 3.2 Step ladder (пример: threshold pace 4:47/km)

| Step | %threshold | km/h | min/km | Expected zone |
|---|---|---|---|---|
| WU | by feel | — | — | Z1 |
| 1 | 80% | 10.0 | 6:00 | Z1-Z2 |
| 2 | 85% | 10.5 | 5:43 | Z2 |
| 3 | 90% | 11.5 | 5:13 | Z2-Z3 (~HRVT1) |
| 4 | 95% | 12.0 | 5:00 | Z3 |
| 5 | 100% | 12.5 | 4:48 | Z3-Z4 (threshold) |
| 6 | 105% | 13.0 | 4:37 | Z4 |
| 7 | 110% | 14.0 | 4:17 | Z4-Z5 (~HRVT2) |
| 8 | 115% | 14.5 | 4:08 | Z5+ (failure) |
| CD | by feel | — | — | Z1 |

### 3.3 WU/CD targeting

WU/CD остаются HR-targeted (`%lthr 70`) в DTO — нужно для Intervals.icu TSS calculation. Garmin при этом игнорирует HR-target на work-шагах когда `event.target=PACE` — это desired behavior, атлет бежит WU/CD по ощущениям без подсказок часов.

---

## 4. Bike protocol

### 4.1 Parameters

| Parameter | Value |
|---|---|
| Anchor | bike FTP (watts) |
| Control unit | watts (ERG mode) |
| Start | 60% FTP |
| Step (1-11) | 5% FTP |
| Step 12 (final) | 120% FTP (10% прыжок от шага 11) |
| Work steps | 12 (11 + 1) |
| Step duration (1-11) | 180 sec (3 min) |
| Step 12 duration | 240 sec (4 min, push to failure) |
| Warm-up | 300s @ 50% + 300s @ 60% FTP |
| Cool-down | 600s @ 50% FTP |
| Total | ~57 min |
| Default fallback | 200W — если FTP пустой |

### 4.2 Calibration trap — почему 120% top, а не 110%

Если FTP undercalibrated (типичный случай для атлета, который давно не тестировался), 110% top может не пробить реальный HRVT2 → α1 не пересекает 0.5 → HRVT2 экстраполируется → низкий R².

Пример с реальными цифрами:

- DB FTP = 208W, реальный HRVT2_power ≈ 240W (последний тест R² = 0.62)
- 110% × 208 = 229W → ниже реального HRVT2 → α1 не пересекает 0.5
- 115% × 208 = 239W → на HRVT2 → α1 ≈ 0.5 borderline
- **120% × 208 = 250W → выше HRVT2 → чистое α1 < 0.5** ✓

После первого откалиброванного теста FTP подтягивается, top 120% масштабируется корректно: 120% × 240 = 288W (Z6, достижимо).

### 4.3 Step ladder (пример: FTP = 208W)

| Step | %FTP | Watts | Coggan zone | Duration |
|---|---|---|---|---|
| WU easy | 50% | 104W | Z1 | 5 min |
| WU build | 60% | 125W | Z2 low | 5 min |
| 1 | 60% | 125W | Z2 endurance | 3 min |
| 2 | 65% | 135W | Z2 mid | 3 min |
| 3 | 70% | 146W | Z2 high | 3 min |
| 4 | 75% | 156W | Z3 tempo | 3 min |
| 5 | 80% | 166W | Z3 tempo | 3 min |
| 6 | 85% | 177W | Z3-Z4 (~HRVT1) | 3 min |
| 7 | 90% | 187W | Z4 sub-threshold | 3 min |
| 8 | 95% | 198W | Z4 sub-threshold | 3 min |
| 9 | 100% | 208W | Z4 threshold (FTP) | 3 min |
| 10 | 105% | 218W | Z5 super-threshold | 3 min |
| 11 | 110% | 229W | Z5+ VO2max | 3 min |
| 12 | **120%** | **250W** | Z6 — push to failure | **4 min** |
| CD | 50% | 104W | Z1 recovery | 10 min |

**Total:** 10 + 33 + 4 + 10 = **57 min**.

### 4.4 Cadence & cooling

**Cadence:** 85-90 rpm фиксированный на всех шагах. ERG держит мощность, атлет держит каденс. Drift каденса добавляет шум в DFA a1 кривую.

**Cooling:** мощный вентилятор + холодная вода + вентиляция обязательны. Без cooling cardiac drift доминирует к 30-40-й минуте → ложно-высокий HRVT2.

**ERG lockout:** каденс ниже 70 rpm = signal failure. 60-90 sec на 120% даёт одно валидное DFA a1 окно — этого достаточно.

---

## 5. Athlete-facing workout descriptions

### 5.1 Run

```
RAMP TEST PROTOCOL (DFA a1 method)

EQUIPMENT:
- Chest HR strap MANDATORY (HRM-Dual / Polar H10)
- Treadmill recommended (or flat outdoor)
- RR interval recording enabled

WARM-UP (10 min, by feel):
- Easy jog, build to ~70-75% LTHR
- Watch will not show pace — run by feel

RAMP (8 steps × 3 min):
- Hold each pace step for full 3 minutes
- DO NOT slow down to control HR
- STOP when you cannot hold pace; skip remaining steps

PACING:
- Step 1 should feel almost trivially easy
- Real test starts around Step 5-6
- Final 2-3 steps are where you find your edge

COOL-DOWN (10 min, by feel):
- 1-2 min walk, then easy jog
- HR falls naturally below 70% LTHR
```

### 5.2 Bike

```
RAMP TEST PROTOCOL — BIKE (DFA a1 method)

EQUIPMENT:
- Chest HR strap MANDATORY
- Smart trainer in ERG mode
- Powerful fan + cold water + ventilation
- RR recording enabled

WARM-UP (10 min, ERG):
- 5 min @ 50% FTP, 5 min @ 60% FTP
- Establish cadence 85-90 rpm

RAMP (11 × 3 min + 1 × 4 min):
- Hold cadence 85-90 rpm THROUGHOUT
- ERG holds watts; you maintain cadence
- Drink every 10 min

FINAL STEP (120% FTP, 4 min):
- Push to failure
- Ok to stop at 60-90 sec
- ERG lockout / cadence < 70 rpm = end of test

COOL-DOWN (10 min, ERG):
- 50% FTP easy spin
```

---

## 6. Auto-update zones logic

Drift-detection пайплайн потребляет HRVT2 (анаэробный) и пушит в Intervals.icu `lthr` / `threshold_pace` / `ftp` (Ride only для FTP, issue #313).

| Confidence | R² | Action |
|---|---|---|
| **high** | ≥0.85 | Auto-update LTHR + threshold/FTP |
| medium | 0.70-0.85 | Suggest with confirmation (inline button) |
| low | <0.70 | No update; recommend retest |
| not_detected | n/a | No update; flag protocol issue |

**Drift thresholds** (абсолютные, заменили старые 5% относительные):

- `DRIFT_LTHR_BPM = 3`
- `DRIFT_PACE_SEC_PER_KM = 5`
- `DRIFT_FTP_WATTS = 5`

Прежний баг: HRVT1 (а не HRVT2) пушился в `lthr` → все Intervals-зоны смещались ~13% (закрыто issue #313, 2026-05-08).

---

## 7. Test cadence

Phase-aware (см. `tasks/utils.py:RampTrainingSuggestion`):

| Phase | Cadence |
|---|---|
| Peak/taper (≤14d до ближайшей гонки) | suppress — никаких тестов |
| Base (≤56d) | каждые 8 недель |
| Build (>56d) | каждые 6 недель |
| No goal | default 30 дней |

Multi-goal aware — выигрывает ближайшая race (не обязательно RACE_A first).

После race/illness: retest до возобновления структурированных тренировок.

Run и Bike тесты разделять ≥2-3 дня.

---

## 8. Decisions log

1. **Pace/power control, не HR.** HR-лаг 30-60s создаёт positive feedback loop.
2. **3-минутные шаги.** ≥3 DFA a1 окна, достаточно стабилизации, компактный тотал.
3. **5% инкремент.** Даёт 3-5 bpm HR Δ — хорошая разрешающая способность.
4. **Run start 80%, bike start 60%.** Оба входят в Z1-Z2 без walk/spin balast.
5. **Run top 115%, bike top 120%.** Run threshold стабилен; bike имеет calibration trap → запас нужен.
6. **Bike final step 4 min, regular 3 min.** Буфер для раннего failure — успеть взять валидное окно.
7. **WU/CD by feel для run, ERG-targeted для bike.** Bike на ERG держит низкую мощность без когнитивной нагрузки.
8. **8 run steps, 12 bike steps.** Run impact-fatigue ограничивает; bike даёт длиннее ladder. Оба n ≥ 8.
9. **HRVT2 (не HRVT1) → Intervals.icu LTHR.** HRVT1 → LTHR смещал все зоны ~13% (issue #313 fix, 2026-05-08).
10. **R² 3-tier auto-fire.** ≥0.85 авто; 0.70-0.85 кнопка; <0.70 soft hint. Заменило старое «всегда подтверждение».

---

## References

- Rogers B. et al. (2020). Frontiers in Physiology — DFA a1 aerobic threshold.
- Rogers B. et al. (2021). Eur J Sport Sci — DFA a1 vs blood lactate.
- Gronwald T., Rogers B. et al. (2020). Int J Sports Physiol Perform.
- AI Endurance / FatMaxxer / HRV4Training documentation.
- Friel J. — comparison reference for traditional FTHR methods.
