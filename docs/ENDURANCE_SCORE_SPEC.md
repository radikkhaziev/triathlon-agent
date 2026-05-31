# Endurance Score — composite endurance state across all sports

> Status: 🟢 **Phase 1 + 2 shipped** (2026-05-25). Drift vs Garmin anchor −2% on real data. 156 tests green. Phase 3 deferred — no triggering pain, see §9.
>
> Дизайн: `design-package/endurai/direction-b-halo.jsx:3396-3622` (карточка `EnduranceScoreCard` + детальный экран `BEnduranceScoreDetail`).

---

## 1. Problem

Garmin Endurance Score — единственный композитный «headline»-индикатор тренировочного состояния, который **смешивает все три спорта** в одну цифру (1–8600+) и кладёт её в одну из шести зон (Detrained → Overreaching). Это то, что атлет смотрит первым, чтобы понять «где я сейчас в цикле».

В нашем стеке такого числа нет: CTL/ATL/TSB дают только усталость, Marathon Shape — только бег, Bike Readiness — только вело. Атлету нужен **один** ответ на «как я в целом по выносливости», который читается до того, как он провалится в per-sport drill'ы.

Дизайн Halo уже разместил карточку Endurance Score **сверху** Load-таба, чтобы все trend'ы ниже читались относительно неё. Бэкенда под неё нет — карточка статична («coming soon»).

### Declarative stance

**Garmin Endurance Score формула закрыта** — Firstbeat Analytics не публикует. Из официальных Garmin/Firstbeat источников + реверс-инжиниринга восстанавливается **структура**, но не точные веса. Это значит:

- мы **не клонируем Garmin 1-в-1** (как Marathon Shape клонирует Runalyze) — клонировать нечего;
- мы **строим свою формулу той же структуры**, с фиксированными по литературе весами и одной точкой калибровки;
- метрика декларируется как **эмпирическая** в UI (аналогично Marathon Shape disclaimer): «оценка состояния выносливости», без претензии на научную точность.

**Decision rule для будущих изменений:** если Firstbeat когда-то опубликует формулу — приводим в её сторону. До тех пор — оставляем нашу с фиксированной структурой и калибруем только overall scale, когда появляется 3+ новых якорных точек.

---

## 2. Solution overview

Карточка `EnduranceScoreCard` в Dashboard Load-табе (заменяет текущий статический placeholder в `webapp/src/pages/DashboardLoadTab.tsx:84-99`). Tap → full-screen `BEnduranceScoreDetail` с 12-недельным трендом, current zone и легендой зон.

Бэкенд:
1. **Pure module** `data/endurance_score.py` — функция `compute_endurance_score(user_id, ref_date)` возвращает `EnduranceScoreResult` (overall + per-sport sub-scores + components breakdown).
2. **Storage** — таблица `endurance_scores(user_id, snapshot_date, score, components_json)` с daily-grain, UNIQUE на `(user_id, snapshot_date)` — идемпотентный upsert.
3. **Triggers** (см. §7.0) — Level 1: после wellness/activities sync; Level 2: daily safety-net cron 18:30 Belgrade; Level 3 (read-only): morning report читает latest.
4. **API** — `GET /api/endurance-score?period=3m` → current score + components + period-filtered time-series (1m/3m/6m/1y).
5. **Backfill CLI** — `python -m cli backfill-endurance-scores` (default: all active × 365 days) пройдёт по wellness/activities за прошлый год и заполнит таблицу.

Никаких изменений в существующих pipeline'ах: модуль читает уже-имеющиеся данные (`wellness`, `activities`, `activity_details`, `athlete_settings`).

---

## 3. Формула

Аддитивная (НЕ мультипликативная — см. §11 Considered alternatives):

```
ES = 100 · VO2max_composite
   + LongTermBonus       (capped 0..1000)
   + RecentBonus         (capped 0..200)
   + DurationBonus       (capped 0..400)
   + ConsistencyBonus    (capped 0..200)
   + RecoveryBonus       (capped 0..200)
```

VO₂max-композит задаёт «потолок-якорь» (≈3500–5500 на типичный AG-диапазон 35–55 ml/kg/min), бонусы (макс ~2000 суммарно) показывают, насколько атлет в этот потолок упёрся реальной работой.

### 3.1 VO₂max composite

Считается per-sport, затем смешивается по sport-CTL share.

**Bike (Storer formula)** — учитывает FTP, вес, возраст:

```python
vo2max_bike = (10.51 * ftp_w + 6.35 * weight_kg - 10.49 * age + 519.3) / weight_kg
```

Источник: `athlete_settings.power_zones_bike.ftp`, `users.weight_kg`, `users.age`.

**Run (Daniels VDOT)** — от threshold pace:

```python
vlt_kmh = 3600 / threshold_pace_sec_per_km   # км/ч на пороге
vvo2max_kmh = vlt_kmh / 0.86                  # threshold ≈ 86% vVO2max (Daniels)
vo2max_run = 0.2 * vvo2max_kmh * 1000 / 60 + 3.5   # ml/kg/min (ACSM running equation)
```

Источник: `athlete_settings.pace_zones_run.threshold_pace`.

**Swim** — proxy: `vo2max_swim = vo2max_run`.
Обоснование: общепринятой VO₂max-формулы из swim pace нет (в отличие от bike/run), плавание — аэробика с близким cardiac demand'ом. Использовать run-VO₂max как proxy лучше, чем выкидывать swim или придумывать свою формулу. **Если у атлета нет run-данных** — fallback на `vo2max_bike`.

**Композит** — взвешенно по sport-CTL share:

```python
total_ctl = sum(sport_ctl.values())
share = {sport: ctl / total_ctl for sport, ctl in sport_ctl.items()}
vo2max_composite = (
    share.get("ride", 0) * vo2max_bike +
    share.get("run", 0)  * vo2max_run +
    share.get("swim", 0) * vo2max_swim
)
```

Источник sport-CTL: `extract_sport_ctl(wellness_row.sport_info)` (см. `data/utils.py:81`).

**Fallback при отсутствии исходных данных:**
- Нет `ftp` → `vo2max_bike = vo2max_run` (если run есть), иначе `vo2max_bike = 40` (default для AG-male 40–44).
- Нет `threshold_pace` → симметрично.
- Нет ни одного — `score = None` для этой недели (карточка показывает «недостаточно данных»).

### 3.2 LongTermBonus — тренировочная база (8 недель)

```python
ctl_avg_8w = mean(wellness.ctl for last 56 days where ctl is not null)
long_term = min(ctl_avg_8w / 80.0, 1.0) * 1000
```

Линейно от 0 до 1000 на диапазоне CTL 0–80. Cap 1000 — выше CTL=80 не даём бонус (это уже территория elite-IM, не наша audience).

### 3.3 RecentBonus — ramp rate (14 дней)

**Замена для предложенного `ATL` — ATL это усталость, не выносливость.**

```python
ramp = current_ramp_rate   # из wellness.ramp_rate (TSS/week)
recent = clamp(ramp / 8.0, 0.0, 1.0) * 200
```

Cap при ramp = +8 TSS/нед: выше — это уже overreaching, бонус не растёт. Отрицательный ramp (untraining) → 0, не штраф (штраф уже неявно в LongTerm через падающий CTL).

### 3.4 DurationBonus — длинные качественные сессии (28 дней)

```python
LONG_THRESHOLDS = {  # минимум для long session
    "Run":   timedelta(minutes=90),
    "Ride":  timedelta(minutes=120),
    "Swim":  timedelta(minutes=60),
}

long_session_tss = sum(
    a.icu_training_load
    for a in activities_last_28d
    if a.moving_time >= LONG_THRESHOLDS[a.type]
    and a.z2plus_time_pct >= 0.70   # ≥70% времени в Z2+ — фильтр trash-rides
)
total_tss_28d = sum(a.icu_training_load for a in activities_last_28d)

share_long = long_session_tss / max(total_tss_28d, 1)
duration_bonus = clamp(share_long, 0, 0.5) * 800
```

Cap при share=0.5 (50% TSS от длинных сессий) — выше уже несбалансированный тренировочный план.

`z2plus_time_pct` берётся из `activity_details.zone_time_in_zone` (массив с временем по зонам), сумма зон 2+ / total time.

### 3.5 ConsistencyBonus — стабильность объёма (8 недель)

```python
weekly_tss = [sum_tss(week) for week in last_8_weeks]
weekly_tss = [w for w in weekly_tss if w > 0]   # пустые недели не считаем
if len(weekly_tss) < 4:
    consistency_bonus = 0
else:
    cv = pstdev(weekly_tss) / mean(weekly_tss)
    consistency_bonus = clamp(1 - cv, 0, 1) * 200
```

CV (coefficient of variation) ниже 0.3 = «стабильно», выше 0.5 = «дёрганый план». Cap 200, никогда не отрицательный.

**Population stdev (`pstdev`), не sample (`stdev`)** — недельные TSS это **наблюдаемые** значения за известное окно, не выборка из бесконечной популяции. Sample-stdev делит на `n-1` (Bessel correction) для несмещённой оценки population variance, что здесь не нужно: мы не оцениваем что-то большее, а описываем именно эти 4-8 недель. На N=8 разница ~7% — материально для калибровки.

**Почему 8 недель, а не 12** (изменено 2026-05-25 по результатам валидации на реальных данных Радика): окно 12 недель **накладывается на смены фаз** — например, пик-неделя ноября 2025 + детрейн февраля 2026 в одном окне дали бы CV ≈ 0.8, что превратило бы метрику из «consistency» в «была ли у меня большая пауза». 8 недель + skip-empty + минимум 4 непустых лучше отражает текущий тренировочный ритм, не штрафуя за давние фазы. Реальные CV-замеры на 4-недельных окнах: pre-injury build 0.14–0.43, текущий build 0.19 — диапазон даёт bonus 115–172, что в pre-injury peak (Nov 2025) на 8-недельном окне останется ≈170, на 12-недельном с базой Aug–Oct упадёт до ~120 без причины.

### 3.6 RecoveryBonus — DFA-α1 aerobic stability (28 дней)

**Primary signal — DFA-α1** (изменено 2026-05-25 по результатам валидации):

```python
valid_sessions = [
    a for a in activities_last_28d
    if a.dfa_a1_mean is not None
    and ((a.type == "Ride" and a.moving_time >= 3600) or   # ≥60min
         (a.type == "Run"  and a.moving_time >= 2700))     # ≥45min
]
green_count = sum(1 for a in valid_sessions if a.dfa_a1_mean >= 0.75)

if len(valid_sessions) < 3:
    recovery_bonus = 0
else:
    share_green = green_count / len(valid_sessions)
    recovery_bonus = share_green * 200
```

**Почему DFA-α1, а не decoupling** (изменено 2026-05-25):
- **Доступно out-of-the-box** в `activities.dfa_a1_mean` (см. `data/hrv_activity.py`) — не требует JOIN на `activity_details` и фильтра `is_valid_for_decoupling()` (VI ≤1.10, >70% Z1+Z2 и т.д.).
- **Семантически измеряет то же самое**: DFA-α1 ≥0.75 = aerobic-state, atril fatigue низкая → recovery character'ный «зелёный»; DFA-α1 <0.5 = high-intensity / fatigued. Это **прямой autonomic-state signal**, тогда как Pa:Hr decoupling — derived metric с теми же предпосылками.
- **Валидация на реальных данных Радика** (см. §8): для Nov 2025 peak 6/6 sessions green (cap 200); для текущего build 11/12 green (183); для детрейн-периодов 0 valid → 0 bonus. Поведение идентично тому, что давал бы decoupling, без compute-overhead.

**Пороги:** Ride ≥60min, Run ≥45min — соответствуют тренировочной длительности, на которой DFA-α1 mean стабилизируется (короче 30–40 минут signal слишком шумный).

**Caveat — покрытие.** DFA-α1 пишется только когда атлет вёл HRV-strap (`activity_details.hrv_pct >= 90%`). У Радика — большинство outdoor-рейдов в Nov 2025 без strap, поэтому в peak-окне валидных только 6 (все runs). Это **не** приводит к недооценке (formula использует *доли* зелёных, не абсолютные count'ы), но при <3 валидных = bonus 0 (insufficient data clamp).

**Phase 2 refinement** — добавить decoupling как fallback когда DFA-α1 unavailable: если у активности есть decoupling, но нет DFA-α1 — считать её валидной, classify по decoupling threshold (<5% = green). Это расширит coverage для outdoor-рейдов без strap. Не делать в Phase 1: усложняет логику без явной выгоды на текущих данных.

### 3.7 Sport decomposition (доли + sub-scores)

**Что показывает карточка** (`direction-b-halo.jsx:3416-3420`, обновлённый дизайн 2026-05-25): per-sport breakdown — это **доли total CTL** (bike 38.2% / run 34.4% / swim 22.9% / other 4.4%), не отдельные ES-числа. Это decomposition «откуда пришла нагрузка», совпадает с тем, что Garmin рендерит на своей карточке.

```python
per_sport_share = {sport: ctl / total_ctl * 100 for sport, ctl in sport_ctl.items()}
per_sport_share["Other"] = max(0, 100 - sum(per_sport_share.values()))   # gap = strength/walk/etc
```

**Bonus — per-sport sub-scores.** Та же формула применяется к одному спорту (фильтр activities/CTL по `type`, VO₂max берётся per-sport вместо composite, остальные components считаются на sport-filtered subset). Карточка sub-scores не рендерит, но они доступны в API response (`per_sport[].sub_score`) для будущих drill-down экранов и debug-инспекции:

```python
sub_score_bike = (
    100 * vo2max_bike +
    long_term_bonus(sport_ctl["ride"], target=50)  # bike-specific scaling
    + ...
)
```

Sub-scores **не агрегируются обратно в composite** — composite считается напрямую через `vo2max_composite` (§3.1). Sub-scores — это explanatory artifact, не intermediate variable.

### 3.8 Zones (categorization)

**5 зон вместо 6** (изменено 2026-05-25 по результатам валидации) — пороги откалиброваны под реальный диапазон AG-триатлета (4000–7000) и проверены на 5 фазах Радика (все различимы). Дизайн `direction-b-halo.jsx:3403-3411` подлежит обновлению при имплементации Phase 1 frontend'а.

```python
ENDURANCE_ZONES = [
    ("detrained",    0,    "Растренирован",    "#ef4444"),   # red
    ("recovering",   3000, "Восстанавливаюсь", "#f97316"),   # orange
    ("maintaining",  4500, "Поддерживаю",      "#eab308"),   # yellow
    ("productive",   5500, "Развиваюсь",       "#22c55e"),   # green
    ("peaking",      6500, "На пике",          "#3b82f6"),   # blue
]
ENDURANCE_MAX = 8000
```

Категория = последняя зона, у которой `score >= min`. **Framework — «training state»**, не «cohort/tier» (см. §11.I). **Не age/sex-нормирована** в Phase 1 — пороги фиксированы. Phase 3 опционально добавит age/sex shift таблицы.

**Семантика зон:**

| Зона | Что значит | Что делать |
|---|---|---|
| **Растренирован** (<3000) | база утрачена или никогда не строилась — новичок или возврат из длинного break'а (3+ месяца) | начни с любых easy-Z1/Z2 сессий, 3–4 раза в неделю, без структуры |
| **Восстанавливаюсь** (3000–4499) | возврат после паузы/травмы, объёмы низкие, нет long-сессий | постепенный ramp ≤+3 TSS/нед, фокус на постоянстве (5–6 sessions/нед), без интенсивности |
| **Поддерживаю** (4500–5499) | базовая форма, объёмы стабильные, но без роста — typical для «между сезонами» или active maintenance | если цель есть — добавляй 1 long ride/run/нед; если нет — нормально оставаться тут |
| **Развиваюсь** (5500–6499) | активный build, объёмы растут, long-сессии стабильно | поддерживай ramp +5–7 TSS/нед, не больше |
| **На пике** (6500+) | гоночная форма, можешь стартовать на A-race | начни taper за 2–3 нед до старта, не пытайся ещё нарастить |

**Calibration на реальных данных Радика:**

| Дата | Score | Зона | Реальное состояние | ✓ |
|---|---|---|---|---|
| 2025-06-01 | 4561 | Поддерживаю | base build без long-сессий | ✓ |
| 2025-11-15 | 6917 | **На пике** | peak fitness pre-injury | ✓ |
| 2026-02-01 | 4445 | Восстанавливаюсь | полный детрейн (реактивка) | ✓ |
| 2026-04-01 | 4161 | Восстанавливаюсь | дно возврата | ✓ |
| 2026-05-25 | 5422 | Поддерживаю | active build (близко к 5500 границы Развиваюсь) | ✓ |

Все 5 фаз попадают в разумные зоны, состояния различимы. Garmin-якорь 5773 → Поддерживаю / Развиваюсь (5500 граница).

**Почему `Overreaching` убран** (был в дизайне как 6-я зона >8000): физически противоречит — high ES = high endurance, не overreaching по определению. Overreaching — про ATL/TSB acute load (`tsb_zone == "risk"` в `data/utils.py:tsb_zone`), это другая ось, не fitness level. Был leak'нувшийся label из Garmin Training Status, который к Endurance Score не относится. См. §11.J.

---

### 3.9 Gamification — milestone badges

Карточка показывает **одну** компактную плашку под gauge'м для усиления motivational-feel. Плашка вычисляется по rule-engine на каждом снапшоте, не хранится в БД отдельно (derived из `endurance_scores` history).

**Phase 1 — 4 правила** (priority order — первое сработавшее побеждает):

| # | Правило (rule) | Плашка | Когда срабатывает |
|---|---|---|---|
| 1 | `today_zone > yesterday_zone` (перешёл вверх) | ✨ **Новая зона: {label}** | переход Recovering → Maintaining, Maintaining → Productive, Productive → Peaking |
| 2 | `today_score >= max(score for last 90 days)` | 🏆 **Лучший за 3 месяца** | текущий score = максимум за 90д |
| 3 | `today_score in top_10_percentile(scores_last_365d)` | 🔥 **Топ 10% твоих недель** | текущий ≥ 90-го перцентиля своей годовой истории |
| 4 | `все snapshots последних 84 дней в зонах productive/peaking` | 💪 **3 месяца в форме** | стабильное держание формы |

**Anti-spam:**
- Cooldown 7 дней на повтор того же бейджа (если #2 сработал сегодня — не показывать #2 ещё неделю, даже если score продолжает быть max).
- Исключение для #1 (zone breakthrough) — cooldown 1 день (зона меняется редко, момент эмоционально важный).
- Нужна history ≥30 дней в `endurance_scores` для #2, ≥365 дней для #3, ≥84 дня для #4. Иначе правило skip'ается (нет данных).

**Если ни одно не сработало** — плашка не рендерится, просто «Прогресс +222 за неделю» как сейчас в дизайне.

**API:** `current.badge` в response — nullable объект:
```json
"badge": {
  "id": "new_zone",
  "label": "Новая зона: Развиваюсь",
  "icon": "✨"
}
```

**Phase 2 backlog — Cooper VO₂max percentile** (Badge B):
- Источник: Cooper Institute VO₂max-нормативы (age × sex × percentile, public domain).
- Размещение: detail-экран, отдельная секция «Сравнение», под 12-week trend.
- Текст: «VO₂max 47 — выше 85% мужчин 40-44» + тултип «по нормативам Cooper Institute (общая популяция, не triathlon-only)».
- НЕ делаем «top N% триатлетов» — нет triathlon-population data, любая цифра была бы выдуманной.

## 4. Data model

### 4.1 Новая таблица

```sql
CREATE TABLE endurance_scores (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    snapshot_date DATE NOT NULL,        -- daily snapshot
    score INTEGER NOT NULL,
    vo2max_composite NUMERIC(5,2),
    components JSONB NOT NULL,          -- {long_term, recent, duration, consistency, recovery, per_sport: [...]}
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_endurance_scores_user_date UNIQUE (user_id, snapshot_date)
);

CREATE INDEX ix_endurance_scores_user_date ON endurance_scores (user_id, snapshot_date DESC);
```

**Зачем daily, а не weekly** (изменено 2026-05-25): дизайн поддерживает фильтры `1M / 3M / 6M / 1Y` на trend-chart'е. Weekly granularity даёт всего 4 точки на 1M — chart выглядит как палки. Daily: 30 точек на 1M, плавная линия. Cost storage'а тривиален (365 строк/юзер/год = ~30 KB JSONB). Weekly view из старого дизайна (12 точек) derive'ится downsample'ом — берём Sunday-snapshots.

**Зачем JSONB components:** для drill-down («почему мой score упал — стал меньше тренироваться или decoupling вырос?»), для post-hoc анализа без перерасчёта, и для хранения per-sport sub-scores без отдельной таблицы.

### 4.2 Почему отдельная таблица, а не `wellness.sport_info`

Рассматривался вариант extend'нуть JSONB `wellness.sport_info` дополнительным `endurance_score`-объектом. Отвергнут:

| Критерий | `endurance_scores` (отдельная) | `wellness.sport_info` extend |
|---|---|---|
| Источник истины | наш compute | Intervals.icu (sync via cron/webhook) |
| Write cadence | daily evening (наш cron) | daily Intervals + webhook → write-skew |
| Time-series queries | прямой `(user_id, date DESC)` index | unnest JSONB в каждом запросе |
| Backfill | independent, не трогает wellness | rewrite wellness рисково — рядом другие fields |
| Изменение формулы | regenerate одной таблицы | rewrite wellness rows |
| Coupling | низкое | размывает границу «то что прислал Intervals» vs «то что мы посчитали» |

Главный аргумент — **separation of sources**. `wellness` = Intervals.icu, `endurance_scores` = наш расчёт. Если завтра меняем формулу — перегенерация одной таблицы; если декомпозируем ES (отвалится компонент) — не теряем `sport_info`.

### 4.3 Источники данных

| Поле | Таблица | Колонка | Notes |
|---|---|---|---|
| FTP | `athlete_settings` | `power_zones_bike` (JSONB) | `.ftp` |
| Threshold pace | `athlete_settings` | `pace_zones_run` (JSONB) | `.threshold_pace` (sec/km) |
| Weight | `users` | `weight_kg` | latest |
| Age | `users` | `age` | latest |
| Sport CTL | `wellness` | `sport_info` (JSONB) | `extract_sport_ctl()` |
| Ramp rate | `wellness` | `ramp_rate` | weekly TSS rate |
| Activity TSS | `activities` | `icu_training_load` | per-session |
| Activity duration | `activities` | `moving_time` | seconds (no `elapsed_time` column on this table — `moving_time` is canonical) |
| Z2+ time | `activity_details` | `zone_time_in_zone` (JSONB) | array per zone |
| DFA-α1 mean | `activities` | `dfa_a1_mean` | per-session, null если не вёл HRV-strap |
| Decoupling | `activity_details` | `decoupling` | float %, reserved для Phase 2 refinement |

**Никаких миграций в существующих таблицах.** Только новая `endurance_scores`.

---

## 5. API endpoint

```python
# api/routers/endurance_score.py (новый файл)

Period = Literal["1m", "3m", "6m", "1y"]
PERIOD_DAYS = {"1m": 30, "3m": 90, "6m": 180, "1y": 365}

@router.get("/api/endurance-score")
async def endurance_score(
    period: Period = Query(default="3m"),
    user: User = Depends(require_viewer),
) -> EnduranceScoreResponse:
    """Endurance Score + historical trend for Dashboard Load tab.

    Reads daily snapshots from `endurance_scores` table. Returns `current`
    (today's row) + `trend` (daily series for the requested period).

    Falls back to on-the-fly computation if today's row is missing
    (e.g., cron hasn't fired yet, or backfill incomplete).
    """
```

**Response shape:**

```json
{
  "current": {
    "score": 5422,
    "zone": "productive",
    "vo2max_composite": 42.0,
    "components": {
      "base": 4200,
      "long_term": 520,
      "recent": 155,
      "duration": 202,
      "consistency": 162,
      "recovery": 183
    },
    "per_sport": [
      {"name": "Bike",  "pct": 42.0, "sub_score": 5100},
      {"name": "Run",   "pct": 43.0, "sub_score": 5600},
      {"name": "Swim",  "pct": 14.0, "sub_score": 4900},
      {"name": "Other", "pct":  1.0, "sub_score": null}
    ],
    "delta_vs_week_ago": 222,
    "badge": {
      "id": "new_zone",
      "label": "Новая зона: Развиваюсь",
      "icon": "✨"
    },
    "computed_at": "2026-05-25T18:00:00Z"
  },
  "trend": [
    {"date": "2026-02-23", "score": 4160},
    {"date": "2026-02-24", "score": 4180},
    ...
    {"date": "2026-05-25", "score": 5422}
  ],
  "period": "3m"
}
```

**Period filter semantics:**
- `1m` → 30 dailу-точек (хорошо смотрится как connected line)
- `3m` → 90 точек (default — covers training-cycle scope)
- `6m` → 180 (показывает фазу + восстановление)
- `1y` → 365 (year-over-year context)

Frontend сам решает downsampling если нужно (на 1Y возможно агрегировать до weekly для visual clarity — TBD при имплементации).

Auth: `require_viewer` (demo-friendly — нет компромата в score'е, в отличие от Recap-таба).

---

## 6. Frontend integration

Карточка живёт на **Wellness home** (`/wellness`), между Recovery hero и Training load. Детальный экран — отдельный route `/wellness/endurance` (по паттерну `/wellness/recovery`, `/wellness/sleep`). Реализация: `webapp/src/components/halo/EnduranceScore.tsx` (карточка), `webapp/src/pages/EnduranceDetail.tsx` (детальный экран). Источник дизайна — `direction-b-halo.jsx:666-677` (карточка), `direction-b-halo.jsx:3641-3845` (детальный экран).

**Карточка (`/wellness` → top of column 2 на desktop, между Recovery и Load на mobile):**
- Eyebrow «Endurance Score» + `i`-info-tooltip: «Композитный балл по всем видам спорта. Зоны: Растренирован → Восстанавливаюсь → Поддерживаю → Развиваюсь → На пике. «Развиваюсь» и «На пике» — целевые состояния для гоночной формы.»
- `EnduranceGauge` — SVG 5-segment arc, marker на текущем score (`ENDURANCE_MAX = 8000`).
- Milestone-badge plate под gauge'м (см. §3.9) — рендерится только если правило сработало; иначе строка «Прогресс +Δ vs last week».
- Per-sport breakdown — 2-column grid с цветными точками + %.
- «tap for trend ›» → `navigate('/wellness/endurance')`.

Карточка владеет собственным fetch'ем (`/api/endurance-score?period=3m`); period для карточки не важен, она читает только `current` блок.

**Детальный экран (`EnduranceDetail`):**
- Restate gauge (260px) + badge plate + «Δ vs last week».
- Period-filter `PeriodFilter` (1M / 3M / 6M / 1Y) — driven by local `range` state, на каждый switch fetch'ит `/api/endurance-score?period=<range>` (backend трактует period как window в днях, см. §5).
- Trend chart (line, hand-rolled SVG) с тремя визуальными слоями:
  - **Zone bands** — горизонтальные stripes для каждой зоны, current zone opacity 0.12, остальные 0.06.
  - **Y-axis snap к границам зон** — y-tick'и стоят на 3000 / 4500 / 5500 / 6500 / 8000 (тех, что попадают в видимый диапазон data). Labels в k-формат: «3.0k / 4.5k / 5.5k / 6.5k».
  - **Zone-coloured line runs** — линия перекрашивается на пересечении границ зон (`buildRuns` паттерн из TSB-графика в `LoadDetail.tsx`); dots colored by zone — рендерятся только при `N ≤ 40` (1M view), на 3M/6M/1Y маркируется только последняя точка.
- Zone legend — **5 строк** с цветом, лейблом, диапазоном; current zone подсвечена.

i18n: keys остались в namespace `load.endurance.*` (исторически, перенос не требуется — namespace это label, не location).

---

## 7. Storage & history

### 7.0 Triggers — что вызывает пересчёт

ES чувствителен к **двум типам write-event'ов**: wellness sync (CTL/ramp_rate) и activity sync (Duration/Consistency/Recovery). Соответственно — три уровня триггеров:

**Level 1 — real-time во время дня:**

```python
# tasks/actors/wellness.py — после wellness write
def actor_user_wellness(...):
    Wellness.upsert(...)
    actor_compose_user_morning_report.send_with_options(args=[user_dto], delay=...)
    actor_snapshot_endurance_scores.send(user_id=user.id)   # NEW

# tasks/actors/activities.py — после activities write
def actor_fetch_user_activities(...):
    Activity.save_bulk(...)
    actor_snapshot_endurance_scores.send(user_id=user.id)   # NEW
```

Оба actor'а уже вызываются из cron + соответствующих webhook'ов (WELLNESS_UPDATED, ACTIVITY_UPLOADED). ES автоматически наследует ту же частоту обновления.

**Level 2 — daily safety-net cron** (см. §7.1) — фиксирует:
- естественный decay (день +29 после long-ride: сессия выпадает из 28d окна, Duration падает — без нового write'а Level-1 не сработает);
- EOD-snapshot для юзеров, у которых wellness/activities sync упал в этот день (Intervals down, user offline).

**Level 3 — morning report (READ-only, НЕ триггер):**

```python
# tasks/actors/reports.py
def actor_compose_user_morning_report(user_dto):
    es = EnduranceScore.get_latest(user_id=user_dto.id)   # просто читаем
    # ...
```

Morning report — **downstream от** `actor_user_wellness` (см. CLAUDE.md «Multi-Tenant Data Flow»). К моменту его запуска wellness-actor уже отстрелил `actor_snapshot_endurance_scores` параллельно — свежий snapshot уже в таблице. Самостоятельный recompute из report'а создал бы race с wellness-trigger'ом и double-compute.

**Coalescing** — не требуется в Phase 1. Wellness ~8 sync/день + activities ~10 sync/день + cron = ~20 recompute'ов/юзер/день. Compute <100ms, upsert идемпотентный, side-effect'ов нет. Если в будущем добавится heavy compute (ML-component) — можно ввести Redis-marker debouncing «skip if last_recompute < 5 min ago», сейчас premature.

### 7.1 Daily cron

```python
# tasks/scheduler.py
scheduler.add_job(
    actor_snapshot_endurance_scores_all_users.send,
    CronTrigger(hour=18, minute=30, timezone=settings.TIMEZONE),
    id="endurance_scores_daily",
    misfire_grace_time=3600,
    coalesce=True,
)
```

Запускается **каждый день в 18:30 Belgrade** — после того как Intervals.icu закончил sync wellness/activities (последние syncs в 17:00–18:00 по нашему расписанию). Если cron не сработал (restart, deploy) — `misfire_grace_time=3600` покрывает час, `coalesce=True` сжимает накопившиеся firings в один.

В воскресенье score получается за час до weekly report (Sunday 19:00) — можно потом включить ES-дельту в weekly summary (Phase 3 опция).

**Идемпотентность:** upsert на `(user_id, snapshot_date)`. Повторный запуск того же дня перезаписывает запись текущего дня с актуальными данными (например, после поздней синхронизации активностей).

### 7.2 Actors

Два actor'а — единичный (per-user, на Level 1 триггеры) и all-users (для cron'а):

```python
# tasks/actors/endurance.py (новый файл)

@dramatiq.actor(queue_name="default", max_retries=2)
def actor_snapshot_endurance_scores(user_id: int):
    """Compute + upsert today's endurance score for one user.

    Fired from actor_user_wellness, actor_fetch_user_activities (Level 1),
    and as a sub-job from actor_snapshot_endurance_scores_all_users (Level 2).
    Idempotent — safe to fire multiple times per day.
    """
    today = local_today()
    result = compute_endurance_score(user_id, ref_date=today)
    if result.score is not None:
        EnduranceScore.upsert(
            user_id=user_id,
            snapshot_date=today,
            score=result.score,
            vo2max_composite=result.vo2max_composite,
            components=result.components,
        )


@dramatiq.actor(queue_name="default", max_retries=1)
def actor_snapshot_endurance_scores_all_users():
    """Safety-net daily cron — fires per-user actor for every active user.

    Catches users where Level-1 triggers didn't fire today (e.g., Intervals
    sync was down) + captures natural decay (sessions rolling out of 28d window).
    """
    for user in User.list_active():
        actor_snapshot_endurance_scores.send(user_id=user.id)
```

### 7.3 Backfill CLI

```bash
# All active athletes, last 365 days (default — для production-rollout)
python -m cli backfill-endurance-scores --user-id=1 --days=180

# Один user
python -m cli backfill-endurance-scores --user-id=123

# Custom window
python -m cli backfill-endurance-scores --days=180

# Dry-run — посмотреть scope без записи
python -m cli backfill-endurance-scores --dry-run

# Принудительно перезаписать существующие rows (default — пропускает уже посчитанные)
python -m cli backfill-endurance-scores --force
```

**Сигнатура** (отличается от существующих `backfill-races` / `bootstrap-sync` — там user_id positional required; у нас по умолчанию **все активные**):

```python
# cli.py — registration
p_be = sub.add_parser(
    "backfill-endurance-scores",
    help="Compute + upsert daily Endurance Score snapshots. Default: all active "
         "athletes × last 365 days. Idempotent (skip existing rows unless --force).",
)
p_be.add_argument("--user-id", type=int, default=None,
                  help="Limit to one user (default: all active)")
p_be.add_argument("--days", type=int, default=365,
                  help="History window in days (default: 365)")
p_be.add_argument("--force", action="store_true",
                  help="Re-compute and overwrite even if row already exists")
p_be.add_argument("--dry-run", action="store_true",
                  help="Print scope (users × days) without writing")
```

**Поведение:**

1. Резолвит список user'ов: `[User.get(user_id)]` если `--user-id`, иначе `User.list_active()`.
2. Для каждого user'а итерирует `today - days + 1 → today` по дням.
3. Per day: если `--force` или row не существует → `compute_endurance_score(user_id, ref_date=day)` → upsert.
4. Идемпотентность через `--force=False` дефолт: повторный запуск той же команды no-op для уже-посчитанных дат, только заполняет пропуски.
5. `--dry-run` печатает `users=N, days=N, rows_to_compute=N` и выходит без write'а.

**Performance** — pure-module `compute_endurance_score` без IO в hot-loop'е делает один день за <100ms; для 365 × 1 user — ~30 сек; для 365 × 5 active users — ~2.5 мин. Можно ускорить распараллеливанием по user'ам (отправка в Dramatiq actor, см. §7.2 `actor_snapshot_endurance_scores_all_users`-pattern), но для one-shot backfill'а sequential pure-Python приемлем — Phase 2 не делать parallel premature.

**Progress logging** — стандартный `print()` per-user с прогрессом и summary в конце (см. формат `_backfill_races` в `cli.py:522`):

```
backfill-endurance-scores: 5 active users, window 2025-05-25 → 2026-05-25 (365 days)
  user 17 (Radik): 365 days, 365 computed, 0 skipped, 0 errors
  user 23 (Test):   365 days, 360 computed, 5 skipped (insufficient_data)
  ...
Total: 1825 days across 5 users, 1820 written, 0 errors, runtime 2m 34s
```

**Error handling** — per-user try/except, ошибка одного юзера не валит всю команду; ошибки логируются в Sentry с `user_id`/`ref_date` tag'ами и в stdout. Возврат exit-code = `1` если ≥1 error, иначе `0`.

---

## 8. Calibration

**Якорная точка (Garmin-измерена):** Радик, 2026-05-25, ES ≈ 5773 при CTL=41.6, sport-CTL bike 17.6 / run 17.7 / swim 5.7, sport-share 42/43/14%.

**Sanity check на реальных данных** (28-day window 2026-04-27 → 2026-05-25, цифры verified в `tests/data/test_endurance_score.py::TestVO2maxRunDaniels` и `TestComputeEnduranceScoreRadik::test_today_2026_05_25`):

```
VO2max_composite ≈ 44.0    (Storer bike 35.0 · 0.42 + Daniels run 52.1 · 0.57)
Base              = 4400
LongTerm (CTL=41.6)        = 520
Recent (ramp +6.2)         = 155
Duration  (share_long 0.252, 4 long sessions)   = 202
Consistency (8w CV 0.188)                       = 162
Recovery (DFA-α1: 11/12 sessions ≥ 0.75)        = 183
─────────────────────────────────────────────────
ES_predicted ≈ 5660
```

Garmin = 5773, наша ≈ 5660. **Drift −2%** — отлично, внутри Phase 1 envelope ±15%. Daniels VDOT для threshold 4:47/km даёт ~52 ml/kg/min, что совпадает с recorded Garmin VO₂max=53 из ноябрьской wellness-row (см. §3.1).

**Историческая валидация** (тот же runner на 4 разных фазах):

| Date | Phase | ES | Зона | Что показывает |
|---|---|---|---|---|
| 2025-06-01 | active build, no long-sessions | ~4800 | Поддерживаю | формула штрафует за отсутствие long-rides |
| 2025-11-15 | true endurance peak (Garmin VO₂max=53) | ~6900 | На пике | 7 long sessions, CV 0.14, recovery 200/200 |
| 2026-02-01 | 2 недели в детрейн (реактивный артрит) | ~4685 | Поддерживаю | формула честно говорит что VO₂max ещё не упал за 2 недели; bonuses=0 |
| 2026-04-01 | 2 месяца в детрейне, дно | ~4400 | Восстанавливаюсь | теперь Base просел вместе с (отсутствующей) тренировкой |
| 2026-05-25 | current build | ~5660 | Развиваюсь | drift −2% от Garmin (5773 vs 5660) |

**Замечание про задержку детрейна**: Feb 2026 формула классифицирует как Поддерживаю, а не Восстанавливаюсь — потому что Base (VO₂max от FTP/threshold_pace из `athlete_settings`) сохраняется неделями после прекращения тренировок. Это **физиологически корректно**: VO₂max действительно не падает за 2 недели. Только когда атлет либо проходит ramp-test и thresholds обновляются вниз, либо проходит 4–6 недель и Intervals.icu eFTP-decay подгоняет цифры — Base падает и зона переходит в Восстанавливаюсь (как в Apr 2026 = ~4400 = recovering). См. open question Q5.

**Структура верна** — формула:
1. различает «active без long-sessions» (Jun 2025=4561) и «true endurance peak» (Nov 2025=6917) — разница 2356 пунктов при близком CTL (62 vs 74);
2. честно обнуляет training-components при детрейне (Feb/Apr 2026: Duration=0, Consistency=0, Recovery=0);
3. floor через VO₂max-Base сохраняется во всех фазах (детрейн не уносит в минус).

**Что НЕ делать:** не подгонять веса под одну точку (4 свободные переменные × 1 наблюдение = бесконечно много решений; смысла нет).

**Что делать:**
1. **Phase 1** — запускаемся с фиксированными литературными весами, accepting ±10% drift от Garmin как «эмпирическая модель».
2. **Phase 2** — собираем ещё 3–4 якорных точки (например, ES Радика на CTL=30, CTL=50, CTL=68 — последняя на пике перед IM-стартом). Когда есть 4+ точек, фитим **только overall scale** (одну свободную переменную) через least-squares: `score_predicted * k ≈ score_garmin`.
3. **Phase 3** — если drift систематический (наш всегда -10%, например), смотрим на residuals и подтягиваем веса. Не раньше.

**Калибровочный лог** живёт в `endurance_scores.components.calibration_anchor` (nullable JSONB-поле для записи «эта неделя сравнивалась с Garmin ES=X»).

---

## 9. Phases

### Phase 1 — Core (one-shot, no history) ✅ shipped 2026-05-25

- [x] Pure-module `data/endurance_score.py` с тестами на формулу (51 unit-тестов: base / каждый компонент / fallback / zones / badges + cooldown).
- [x] API endpoint `GET /api/endurance-score` — Phase 1 считал on-the-fly; Phase 2 переписал на storage.
- [x] Замена static-карточки в `DashboardLoadTab.tsx` на fetch'ащую.
- [x] Детальный экран `EnduranceScoreDetail` (port `BEnduranceScoreDetail`).
- [x] Badge rule-engine (§3.9) — 4 правила + priority + cooldown (7d / 1d для new_zone).

**Acceptance:** ✅ drift от Garmin −2% (envelope ±15%), карточка fetched + детальный экран + badge plate работают.

### Phase 2 — History (storage + cron + backfill) ✅ shipped 2026-05-25

- [x] Миграция `endurance_scores` таблицы с daily-grain (alembic `f6e360a8f4fa`, JSONB components, Numeric(5,1) vo2max, partial UNIQUE).
- [x] Per-user actor `actor_snapshot_endurance_scores(user_id)` + all-users wrapper (`max_retries=0`, фильтр `athlete_id.isnot(None)`).
- [x] **Level 1 hooks:** `actor_user_wellness` + `actor_fetch_user_activities` fire per-user actor после write'а (top-level import, no cycle).
- [x] **Level 2 cron:** `daily 18:30 Belgrade` → `_all_users` wrapper (`misfire_grace_time=3600`, `coalesce=True`).
- [ ] **Level 3 (read-only):** `actor_compose_user_morning_report` читает `EnduranceScore.get_latest()`. — **Deferred к Phase 3** (см. §9 Phase 3: «Включение ES в weekly summary и morning report»); triggering pain отсутствует.
- [x] CLI `backfill-endurance-scores` — default all active × 365 days, `--user-id` / `--days` / `--force` / `--dry-run`, shared session.
- [x] Endpoint читает из таблицы; fallback на on-the-fly для today + synthesized today-point в trend.
- [x] Frontend period-фильтры `1M / 3M / 6M / 1Y` (4 chip'а), default `3M`.

**Acceptance:** ✅ 6 integration tests (multi-tenant, period filter, fallback, JSONB serialize, invalid period 422). Roundtrip migration verified.

### Phase 3 — Optional / deferred (no triggering pain — see §12)

Все пункты ниже — nice-to-have без блокера. Открываются по конкретному триггеру:

- [ ] **Badge B — Cooper VO₂max percentile** (§3.9 deferred) → open when 2-й/3-й атлет присоединится или когда pop-percentile ценнее «лучший за 90д»
- [ ] **ES в morning report / weekly summary** (Level 3 в §7.0) → open когда захочется habit-форма «ES сегодня +15 vs неделя» в утреннем отчёте
- [ ] **Calibration anchor logging** → open когда наберётся 3+ Garmin-снимков ES на разных CTL для scale-fit
- [ ] **Age/sex-нормирование zone-порогов** → open при росте user-base за пределы single-male-40-44
- [ ] **Q5 refinement — Base decay через свежесть thresholds** → open если delayed detrain detection начнёт смущать в реальных кейсах
- [ ] **Q6 refinement — historical thresholds для trend backfill** → open если honest peak Nov 2025 в trend будет занижен и это явная проблема

---

## 10. Multi-tenant correctness

- Pure module принимает `user_id` явно, читает только user-scoped данные (`wellness`, `activities`, `activity_details`, `athlete_settings`, `users` — все уже отфильтрованы по `user_id`).
- Actor итерирует `User.list_active()`, для каждого вызывает `compute_endurance_score(user.id, ...)`. Cross-user leak невозможен.
- Endpoint защищён `require_viewer` (`api/deps.py`), читает `user.id` из dependency.
- Demo-доступ: разрешён (read-only), цифры не sensitive (нет упоминаний здоровья, контекста травм и т.д. — в отличие от weekly reports).

---

## 11. Considered alternatives

### A. Multiplicative `ES = VO2max × (1 + Training_Adjustment)`

Отвергнуто (см. §1 critique). Сидячий с высоким VO₂max получит высокий score без тренировок — противоречит Garmin'овскому требованию 3–4 недели данных. Аддитив + cap'ы решают это структурно.

### B. ATL как ShortTerm component

Отвергнуто. ATL = усталость, не выносливость. Перетренированный получил бы бонус, отсвежевший — штраф. Заменено на `ramp_rate` с cap'ом (positive contribution only).

### C. DurationBonus без фильтра по интенсивности

Отвергнуто. 4ч Z1 trash-ride эквивалентно 4ч Z2 quality-ride'у по этой логике, что неправильно. Добавлен фильтр `z2plus_time_pct >= 0.70`.

### D. Calibration через `scipy.optimize` на одной точке

Отвергнуто. 4 свободных веса × 1 якорь = переопределено. Фиксируем веса по литературе, калибруем только overall scale когда наберём 4+ точек.

### E. Историю считать on-the-fly без таблицы

Отвергнуто на Phase 2. 365 дней × (28d activities + 8w wellness + aggregations) = ~10с на запрос для 1Y фильтра, неприемлемо для дашборда. Phase 1 терпит это как trade-off (нет миграции, **weekly granularity, фиксированно 12 weeks ≈ 12 точек × <100ms = ~1с**), Phase 2 переходит на daily-snapshot table — любой period-filter O(N rows).

### G. Weekly snapshot вместо daily

Отвергнуто 2026-05-25 при добавлении period-фильтра `1M / 3M / 6M / 1Y`. Weekly granularity даёт 4 точки на 1M — chart выглядит как палки, не линия. Daily-snapshots: 30 точек на 1M, 365 на 1Y, любой view выглядит плавно. Storage cost тривиален (~30 KB JSONB на юзера в год). Если в Phase 3 окажется, что 1Y view нужен в weekly-aggregate для visual clarity — frontend сам делает downsample (берём каждый 7-й day), data в БД остаётся daily.

### H. Хранить ES в `wellness.sport_info` JSONB вместо отдельной таблицы

Отвергнуто. Аргументы в §4.2 — главное: разные источники истины (wellness = Intervals.icu, ES = наш compute), разный write-cadence, time-series queries проще на индексной колонке чем на JSONB-unnest'е, изменение формулы регенерирует одну таблицу без touch'а wellness.

### F. Age/sex-нормированные zones

Отвергнуто на Phase 1 как premature. Используем **фиксированные** пороги (0/3000/4500/5500/6500). Garmin таблицы по age/sex применяются только в UI-labelling, не в score'е. Опционально на Phase 3.

### I. Cohort/tier-зоны (Garmin Endurance Score стиль)

Отвергнуто 2026-05-25. Рассматривался framework как у Garmin: лейблы `Любитель → Натренированный → Эксперт → Элита` отражают где атлет стоит относительно популяции (cohort percentiles), а не его тренировочное состояние.

Проблемы:
- Требует age/sex normalization (иначе нечестно сравнивать 25-летнего с 50-летним) — лишняя сложность.
- Lost actionability: «Эксперт» не говорит что делать, в отличие от «Развиваюсь / Поддерживаю».
- Single-user app для одного атлета (Радик) — comparison с популяцией не value-add.

Выбран framework **«training state»** (Detrained → Peaking) — coaching-app tone, каждая зона = guidance что делать. Mirror Garmin Training Status, не Endurance Score.

### J. 6-я зона «Overreaching»

Отвергнуто 2026-05-25. В дизайне `direction-b-halo.jsx:3409` была 6-я зона `Overreaching` при score ≥8000. Убрана:

- **Семантически противоречит** — высокий ES = высокая выносливость, а overreaching это острая acute fatigue, не fitness level. Это разные оси.
- **Overreaching = `tsb_zone == "risk"`** в `data/utils.py:tsb_zone` (TSB < −30) — уже метрика, не нужна дублирующая в ES.
- **Реально недостижимо** для AG-атлета — для score >8000 нужен VO₂max ≥60 + max all bonuses; Радик в peak Nov 2025 = 6917, классически peaking, не overreaching.
- Был leak'нувшийся label из Garmin Training Status, не Endurance Score.

### K. Recalibrated thresholds (3000/4500/5500/6500 вместо 1500/3000/4500/6500/8000)

Изменено 2026-05-25 по результатам валидации на реальных данных. Старые пороги (0/1500/3000/4500/6500) проектировались под полную шкалу 0–9500, но реальные AG-атлеты живут в 4000–7000. Из 6 зон работали только 3 (maintaining/productive/peaking) — нижние две (detrained <1500, recovering <3000) физически недостижимы при VO₂max ≥35 (Base ≥3500).

Новые пороги откалиброваны под реальный AG-range:
- Detrained <3000 — только новичок или 3+ месяца break'а
- Recovering 3000–4499 — пост-травма / post-break (твои Feb/Apr 2026 = 4445/4161)
- Maintaining 4500–5499 — базовая форма (твой Jun 2025 = 4561)
- Productive 5500–6499 — active build (твой May 2026 = 5422 — у границы)
- Peaking 6500+ — гоночная форма (твой Nov 2025 = 6917)

`ENDURANCE_MAX = 8000` (был 9500) — даёт headroom для гипотетического элитного атлета +1500 над текущим Peaking-минимумом, но не растягивает gauge впустую под цифры которые никто не достигнет.

---

## 12. Open questions

- **Q1: `users.age` / `users.weight_kg` сейчас не везде заполнены.** Что делать с fallback'ом? Сейчас в спеке — default'ы (`age=40`, `weight_kg=75`), но это снижает точность для атлетов без профиля. Альтернатива: жёсткий `score=None` пока пользователь не заполнит профиль. **→ Решить при имплементации Phase 1.**
- **Q2: Swim VO₂max proxy = run.** Если у атлета нет run-данных (только swim+bike) — fallback на bike. Корректно ли это? Нужно валидировать на одном-двух non-running swimmer'ах. **→ Отложено, edge case.**
- **Q3: «Other»-bucket в per-sport breakdown.** Дизайн (`direction-b-halo.jsx:3419`) показывает Other=4.41% — что туда попадает? Strength training, walks, hiking? Сейчас в спеке — gap до 100% от total CTL. **→ Уточнить при имплементации Phase 1.**
- **Q4: ~~Zones-секция на detail-экране~~** — **РЕШЕНО 2026-05-25**: keep, но в новой 5-зонной конфигурации (см. §3.8 + §11.J/K). Дизайн `direction-b-halo.jsx:3596-3617` рендерит 6-строчную легенду — при имплементации Phase 1 frontend'а схлопнуть до 5 строк, изменить лейблы на русские (Растренирован/Восстанавливаюсь/Поддерживаю/Развиваюсь/На пике), обновить пороги (0/3000/4500/5500/6500). Цвета остаются.
- **Q6: Historical trend использует текущие thresholds для всех точек.** Backfill (CLI или fallback-compute) фетчит `athlete_settings.ftp` / `pace_zones_run.threshold_pace` на момент **запроса**, а не на момент `ref_date`. Атлет, прошедший ramp-test 2 недели назад, увидит post-test пороги в Base для всех точек тренда — старые недели выглядят стабильно, хотя в реальности тогда были другие пороги. Bonuses (LongTerm/Recent/Duration/Consistency/Recovery) считаются правильно (используют дату-специфичные wellness/activities), но Base-shift невозможен без historical athlete_settings. Согласовано с Q5 (delayed detrain detection) — это связанная задача. **→ Phase 3:** добавить `athlete_settings_history` таблицу либо снапшотить thresholds в `endurance_scores.components` при write'е (доступ к историческим thresholds через прошлые ES-rows).
- **Q5: Задержка детектирования детрейна (Base не падает быстро).** Когда атлет уходит в перерыв 2+ недели, `athlete_settings.power_zones_bike.ftp` и `pace_zones_run.threshold_pace` остаются на старых значениях до следующего ramp-теста или Intervals.icu eFTP-decay (~4–6 недель). За это время VO₂max-Base не отражает реальное падение формы (тесты `test_detrain_2026_02_01`). Apr 2026 (2 месяца) уже корректно классифицируется как Восстанавливаюсь, но Feb 2026 (2 недели) — Поддерживаю. **Это физиологически корректно** (VO₂max действительно не падает за 2 недели), но визуально выглядит так, будто карточка не реагирует. Возможный refinement в Phase 3: коррекция Base'а через свежесть thresholds (`athlete_settings.updated_at` или eFTP-trend в `wellness.sport_info`). **→ Отложено, edge case.**

---

## 12.1 Decisions log (после ship'а Phase 1+2)

| Дата | Решение | Обоснование |
|---|---|---|
| 2026-05-25 | Карточка перенесена с Dashboard → Trends → Load tab на Wellness home (между Recovery hero и Training load) | Per `direction-b-halo.jsx:666-677`: «primary endurance read-out lives on the daily home screen» — это первая цифра, которую атлет должен видеть, а не нырять в Trends. Сидит над Training load, чтобы composite zone (где я в цикле) и CTL/ATL/TSB (что я сделал) читались парой. |
| 2026-05-25 | Детальный экран — отдельный route `/wellness/endurance`, не in-place swap | Паттерн уже принят для `/wellness/recovery`, `/wellness/sleep`, `/wellness/body`, `/wellness/load` — последовательно с остальной Wellness-навигацией. In-place swap из Halo-прототипа был одиночным исключением. |
| 2026-05-25 | Trend chart переделан: zone bands + zone-coloured line runs + y-axis snap к границам зон | Старый чарт (одна цветная линия, y-ticks step 250) не передавал «где я по ladder зон». Новый чарт читается как «вот так я двигался по зонам», current zone выделен сильнее (opacity 0.12 vs 0.06). `buildRuns` паттерн уже работает в TSB-чарте `LoadDetail.tsx`. |
| 2026-05-25 | Карточка владеет собственным fetch'ем; детальный экран — своим (с period state) | Раньше LoadTab держал shared `enduranceData` для card+detail с общим `endurancePeriod`. После split на два роута это duplicate fetch (card на `/wellness`, detail на `/wellness/endurance`) — приемлемая цена за независимость компонентов. |
| 2026-05-31 | **Реверс 2026-05-25**: карточка возвращена с Wellness home обратно в Trends → Load tab, над sport-свитчером (`DashboardLoadTab.tsx`). Детальный экран переехал `/wellness/endurance` → `/trends/endurance` (старый путь оставлен редиректом для закладок; back-ссылка ведёт в Trends, нижнее меню подсвечивает Trends). | Endurance Score — медленно-меняющийся composite (base = 100·VO₂max стоит на статичных порогах, шевелится только после ramp-теста / eFTP-decay). Для понимания «как я сегодня» он не важен и занимал hero-слот на daily home. Место над sport-свитчером логично: показатель межспортивный, читается до per-sport drill'ов. Освободившийся col-2 слот на `/wellness` занял новый Training Strain (отзывчивая метрика — ей там и место). |

---

## 13. References

- Garmin Endurance Score user-facing docs: `https://support.garmin.com/` (поиск «Endurance Score»).
- Firstbeat Analytics white papers: `https://www.firstbeat.com/en/science-and-physiology/` (общая структура VO₂max-based scoring).
- Storer bike VO₂max formula: Storer TW, Davis JA, Caiozzo VJ (1990) «Accurate prediction of VO2max in cycle ergometry», Med Sci Sports Exerc 22:704-12.
- Daniels VDOT: Daniels J. «Daniels' Running Formula», 3rd ed., chapter 2.
- ACSM running metabolic equation: ACSM's Guidelines for Exercise Testing, 10th ed., Table D-1.
- Internal: [`MARATHON_SHAPE_SPEC.md`](MARATHON_SHAPE_SPEC.md), [`BIKE_READINESS_SPEC.md`](BIKE_READINESS_SPEC.md), [`WEBAPP_HALO_REDESIGN_SPEC.md`](WEBAPP_HALO_REDESIGN_SPEC.md) §AD.
