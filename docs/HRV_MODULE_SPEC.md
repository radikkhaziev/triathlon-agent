# HRV Analysis Module — Архитектурная спецификация

> Модуль для Garmin Recovery Agent (триатлон-бот).
> Двухуровневый HRV-анализ: восстановление в покое + тренировочная готовность через DFA alpha 1.

---

## Обзор архитектуры

```
┌──────────────────────────────────────────────────────────┐
│               Telegram Bot / Mini App                     │
│          (графики Chart.js, алерты, рекомендации)         │
├──────────────────────────────────────────────────────────┤
│                     FastAPI Gateway                       │
├───────────────┬───────────────┬──────────────────────────┤
│  HRV Rest     │  HRV Activity │  Recovery Model          │
│  Analyzer     │  Analyzer     │  (Banister + RMSSD)      │
│  (Level 1)    │  (Level 2)    │                          │
├───────────────┴───────────────┴──────────────────────────┤
│                   Data Layer (SQLite)                      │
├───────────────┬──────────────────────────────────────────┤
│  Garmin API   │  FIT File Parser                          │
│ (garminconnect)│  (fitparse)                              │
└───────────────┴──────────────────────────────────────────┘
```

### Зависимости

```
garminconnect>=0.2.38    # Garmin Connect API
fitparse>=0.0.7          # FIT file parsing (RR-интервалы)
numpy>=1.24              # DFA alpha 1, статистика
scipy>=1.10              # Detrended Fluctuation Analysis
pandas>=2.0              # Временные ряды, скользящие окна
```

---

## Level 1: HRV в покое (RMSSD-based Recovery)

### 1.1 Источники данных

```python
# garminconnect API — ежедневный сбор
client.get_hrv_data(cdate)                    # Ночной RMSSD, HRV status
client.get_heart_rates(cdate)                 # Resting HR, min HR за ночь
client.get_training_readiness(cdate)          # Garmin Training Readiness (0-100)
client.get_morning_training_readiness(cdate)  # Утренняя готовность
client.get_sleep_data(cdate)                  # Sleep score, время засыпания, фазы
client.get_stress_data(cdate)                 # Stress level timeline
client.get_body_battery(cdate)                # Body Battery при пробуждении
```

### 1.2 Схема данных (SQLite)

```sql
CREATE TABLE daily_hrv (
    date              TEXT PRIMARY KEY,  -- 'YYYY-MM-DD' ++
    rmssd_night       REAL,             -- Ночной RMSSD (ms), основная метрика
    rmssd_morning     REAL,             -- Утренний RMSSD если доступен
    resting_hr        REAL,             -- Пульс покоя (bpm)
    min_hr            REAL,             -- Минимальный ЧСС за ночь
    sleep_score       INTEGER,          -- Garmin sleep score (0-100) ++
    sleep_start       TEXT,             -- Время засыпания ISO 8601 ++
    body_battery_am   INTEGER,          -- Body Battery при пробуждении
    stress_avg        REAL,             -- Средний stress level за день
    garmin_readiness  INTEGER,          -- Garmin Training Readiness (0-100)
    created_at        TEXT DEFAULT (datetime('now'))
);

CREATE TABLE training_load (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    date              TEXT NOT NULL,
    activity_id       TEXT UNIQUE,       -- Garmin activity ID
    activity_type     TEXT NOT NULL,     -- 'swim' | 'bike' | 'run' | 'walk'
    duration_min      REAL,
    avg_hr            REAL,
    max_hr            REAL,
    ess               REAL,             -- External Stress Score (см. 1.4)
    trimp             REAL,             -- TRaining IMPulse (альтернатива)
    has_fit_hrv       BOOLEAN DEFAULT 0, -- Есть ли RR-данные в FIT
    created_at        TEXT DEFAULT (datetime('now'))
);
```

### 1.3 RMSSD Baseline Analysis

Подход AIEndurance: сравнение краткосрочного базлайна с долгосрочной нормой.

```python
def calculate_rmssd_status(daily_hrv: list[dict]) -> dict:
    """
    Возвращает статус восстановления на основе RMSSD.

    Логика:
    - normal_60d: среднее ± 0.5*SD за последние 60 дней (диапазон нормы)
    - baseline_7d: среднее за последние 7 дней
    - Если baseline_7d < lower_bound → recovery = 'low'
    - Если baseline_7d > upper_bound → recovery = 'elevated' (возможен парасимпатический rebound)
    - Иначе → recovery = 'normal'
    """
    rmssd_values = [d['rmssd_night'] for d in daily_hrv if d['rmssd_night']]

    if len(rmssd_values) < 14:
        return {'status': 'insufficient_data', 'days_needed': 14 - len(rmssd_values)}

    recent_60 = rmssd_values[-60:]  # или сколько есть, минимум 14
    recent_7 = rmssd_values[-7:]

    mean_60 = np.mean(recent_60)
    sd_60 = np.std(recent_60)
    mean_7 = np.mean(recent_7)

    # Коэффициент вариации (CV) для оценки стабильности
    cv_7 = np.std(recent_7) / mean_7 * 100 if mean_7 > 0 else 0

    lower_bound = mean_60 - 0.5 * sd_60
    upper_bound = mean_60 + 0.5 * sd_60

    # SWC (Smallest Worthwhile Change) — минимально значимое изменение
    swc = 0.5 * sd_60

    return {
        'status': _classify_recovery(mean_7, lower_bound, upper_bound),
        'rmssd_7d': round(mean_7, 1),
        'rmssd_60d': round(mean_60, 1),
        'rmssd_sd_60d': round(sd_60, 1),
        'lower_bound': round(lower_bound, 1),
        'upper_bound': round(upper_bound, 1),
        'cv_7d': round(cv_7, 1),      # CV < 10% = стабильно
        'swc': round(swc, 1),
        'trend': _calculate_trend(rmssd_values[-14:])  # 14-дневный тренд
    }
```

### 1.4 Resting HR Analysis

```python
def calculate_rhr_status(daily_hrv: list[dict]) -> dict:
    """
    Resting HR — обратная метрика к RMSSD.
    Повышенный RHR → недовосстановление.

    Логика (AIEndurance):
    - normal_30d: среднее ± 0.5*SD за 30 дней
    - today: сегодняшнее значение
    - Если today > upper_bound → recovery = 'low'
    - Если today < lower_bound → recovery = 'good' (сильное восстановление)
    """
    # Аналогично RMSSD, но инвертированная интерпретация
    # 30-дневное окно для RHR (быстрее реагирует чем RMSSD)
```

### 1.5 External Stress Score (ESS)

```python
def calculate_ess(duration_min: float, avg_hr: float, max_hr: float,
                  hr_rest: float, hr_max: float) -> float:
    """
    ESS — нормализованная тренировочная нагрузка.
    Условие: 1 час на пороге ≈ ESS 100.

    Используем модифицированный TRIMP (Lucia):
    Zone 1 (< VT1):  duration_in_zone * 1
    Zone 2 (VT1-VT2): duration_in_zone * 2
    Zone 3 (> VT2):   duration_in_zone * 3

    Упрощённый вариант без зон (Banister TRIMP):
    TRIMP = duration_min * (avg_hr - hr_rest) / (hr_max - hr_rest) * 0.64 * exp(1.92 * hr_ratio)
    где hr_ratio = (avg_hr - hr_rest) / (hr_max - hr_rest)

    Нормализация: ESS = TRIMP / TRIMP_threshold_1h * 100
    """
```

### 1.6 Recovery Model (Banister)

```python
def calculate_recovery(training_log: list[dict], k: float, tau: float,
                       current_recovery: float = 100.0) -> list[dict]:
    """
    Рекурсивная модель восстановления Banister:

    R(t+1) = R(t) * exp(-1/τ) + k * ESS(t)

    Параметры:
    - k:   чувствительность к нагрузке (больше k → больше "урон" от тренировки)
    - τ:   скорость восстановления в днях (больше τ → медленнее восстановление)
    - R:   процент восстановления (100% = полностью восстановлен)

    Начальные значения (калибруются по данным):
    - k = 0.1 (консервативно для фазы возврата)
    - τ = 2.0 (быстрое восстановление для лёгких нагрузок)

    Калибровка:
    Минимизируем расхождение между модельным R(t) и реальным RMSSD-статусом.
    Используем scipy.optimize.minimize с ограничениями:
    - 0.01 ≤ k ≤ 1.0
    - 0.5 ≤ τ ≤ 7.0
    """
```

### 1.7 Комбинированный Recovery Score

```python
def combined_recovery_score(rmssd_status: dict, rhr_status: dict,
                            banister_recovery: float,
                            sleep_score: int, body_battery: int) -> dict:
    """
    Интегральная оценка готовности (0-100%).

    Весовая модель:
    - RMSSD baseline status:  35%  (основной сигнал)
    - Banister model R(t):    25%  (учёт кумулятивной нагрузки)
    - Resting HR status:      15%  (быстрый индикатор)
    - Sleep score:            15%  (качество восстановления)
    - Body Battery:           10%  (Garmin's composite)

    Модификаторы:
    - Время засыпания > 23:00 → penalty -10%
    - CV RMSSD 7d > 15% → penalty -5% (нестабильный паттерн)
    - RMSSD тренд отрицательный > 3 дня → warning flag

    Выход:
    {
        'score': 72,
        'category': 'moderate',  # low(<40) | moderate(40-70) | good(70-85) | excellent(>85)
        'recommendation': 'zone1_short',  # skip | zone1_short | zone1_long | zone2_ok
        'flags': ['late_sleep', 'rmssd_declining'],
        'components': { ... }  # разбивка по компонентам
    }
    """
```

---

## Level 2: HRV во время тренировки (DFA alpha 1)

### 2.1 Источники данных

```python
# FIT файл — скачивание через garminconnect
fit_data = client.download_activity(activity_id, dl_fmt='FIT')

# Парсинг RR-интервалов из FIT
from fitparse import FitFile

def extract_rr_intervals(fit_path: str) -> list[float]:
    """
    Извлекает RR-интервалы из HRV-записей FIT файла.

    FIT HRV messages содержат массивы RR-интервалов (в секундах, UINT16 / 1000).
    Значения 0xFFFF (65535/1000 = 65.535) — невалидные, фильтруем.
    HRV messages не имеют timestamp — синхронизируются по Record messages.

    Возвращает: список RR-интервалов в миллисекундах.
    """
    fitfile = FitFile(fit_path)
    rr_intervals = []

    for record in fitfile.get_messages('hrv'):
        for field in record.fields:
            if field.name == 'time' and field.value is not None:
                for rr in field.value:
                    if rr is not None and rr < 2.0:  # < 2 секунды, отсечь невалидные
                        rr_intervals.append(rr * 1000)  # → миллисекунды

    return rr_intervals
```

### 2.2 Ограничения по датчику

```
┌─────────────────────┬──────────────┬───────────────────────────────┐
│ Датчик              │ RR качество  │ Пригодность для DFA a1        │
├─────────────────────┼──────────────┼───────────────────────────────┤
│ Polar H10 (BLE)     │ Отличное     │ Золотой стандарт              │
│ Garmin HRM-Dual     │ Хорошее      │ Пригоден (нагрудный strap)    │
│  └─ BLE connection  │ Лучше        │ Предпочтительно               │
│  └─ ANT+ connection │ Хуже >120bpm │ Garmin ограничивает 2 RR/сек  │
│ Запястье (Garmin)   │ Плохое       │ НЕ пригоден для DFA a1        │
│ Плавание (любой)    │ Нет данных   │ Нет RR в воде                 │
└─────────────────────┴──────────────┴───────────────────────────────┘

ВАЖНО для HRM-Dual:
- Подключать к часам по BLE (не ANT+) для лучшего качества RR при ЧСС > 120
- Включить HRV logging: Settings → System → Data Recording → Log HRV
- Плавание: HRM-Dual не передаёт RR в воде → Level 2 только для вело и бега
```

### 2.3 Artifact Correction

```python
def correct_rr_artifacts(rr_ms: list[float], threshold_pct: float = 0.10) -> dict:
    """
    Коррекция артефактов в RR-ряде.
    Критически важно — без коррекции DFA a1 бессмысленен.

    Алгоритм (по Lipponen & Tarvainen 2019):
    1. Вычислить dRR[i] = RR[i] - RR[i-1]
    2. Вычислить медиану и MAD (median absolute deviation) для dRR
    3. Пометить как артефакт если |dRR[i]| > threshold_pct * median(RR)
    4. Типы артефактов:
       - Пропущенный удар (long RR ≈ 2x median)
       - Лишний удар (short RR ≈ 0.5x median)
       - Эктопический (резкий скачок + возврат)

    Коррекция:
    - Интерполяция кубическим сплайном для единичных артефактов
    - Удаление сегмента при кластере артефактов (>3 подряд)

    Параметры:
    - threshold_pct: 0.05 (строгий) — 0.20 (мягкий)
    - Для HRM-Dual рекомендуется 0.10 (средний)

    Возвращает:
    {
        'rr_corrected': [...],
        'artifact_count': 12,
        'artifact_pct': 1.8,  # Процент артефактов
        'quality': 'good'     # good (<5%), moderate (5-10%), poor (>10%)
    }

    ВАЖНО: Если artifact_pct > 10%, результаты DFA a1 ненадёжны.
    В этом случае помечаем активность как 'low_hrv_quality' и не используем
    для расчёта порогов и readiness.
    """
```

### 2.4 DFA Alpha 1 Calculation

```python
def calculate_dfa_alpha1(rr_ms: np.ndarray, window_beats: tuple = (4, 16)) -> float:
    """
    Detrended Fluctuation Analysis — short-term scaling exponent.

    Алгоритм:
    1. Интегрировать: y[i] = sum(RR[0:i] - mean(RR))
    2. Разбить y на окна размером n (от window_beats[0] до window_beats[1])
    3. В каждом окне: линейный fit (детрендинг), вычислить residual
    4. F(n) = sqrt(mean(residuals^2)) для каждого n
    5. alpha1 = slope(log(n), log(F(n))) — линейная регрессия в log-log

    Параметры:
    - rr_ms: массив RR-интервалов в мс (после artifact correction)
    - window_beats: (4, 16) — стандарт для short-term alpha1

    Возвращает: float, значение DFA alpha 1

    Интерпретация:
    - a1 > 1.0  → сильно коррелированный (низкая нагрузка, покой)
    - a1 ≈ 0.75 → аэробный порог (HRVT1)
    - a1 ≈ 0.50 → анаэробный порог (HRVT2) / высокая нагрузка
    - a1 < 0.50 → антикоррелированный (максимальная нагрузка)

    Реализация: scipy или nolds, либо кастомная на numpy.
    Вычислительная сложность: O(N * M * log(M)) где N = кол-во окон,
    M = размер окна. Для 2-мин сегмента (~200 RR) — мгновенно.
    """
```

### 2.5 Time-Varying DFA a1

```python
def calculate_dfa_timeseries(rr_ms: list[float],
                              window_sec: int = 120,
                              step_sec: int = 5) -> list[dict]:
    """
    Скользящее окно DFA alpha 1 по ходу тренировки.

    Параметры:
    - window_sec: 120 (2 минуты — стандарт в литературе)
    - step_sec: 5 (пересчёт каждые 5 секунд)

    Для каждого окна:
    1. Взять RR-интервалы за последние window_sec секунд
    2. Проверить artifact_pct < 10%
    3. Рассчитать DFA alpha 1
    4. Сопоставить с HR (средний за окно) и power/pace из Record messages

    Возвращает список:
    [
        {
            'time_sec': 120,         # Секунда активности
            'dfa_a1': 1.05,          # DFA alpha 1
            'hr_avg': 118,           # Средний HR за окно
            'power_avg': 150,        # Средняя мощность (вело) или None
            'pace_avg': '5:45',      # Средний темп (бег) или None
            'artifact_pct': 1.2,     # % артефактов в окне
            'quality': 'good'
        },
        ...
    ]
    """
```

### 2.6 Threshold Detection (HRVT1 / HRVT2)

```python
def detect_hrv_thresholds(dfa_timeseries: list[dict]) -> dict:
    """
    Автоматическое определение аэробного и анаэробного порогов
    из DFA alpha 1 time series.

    Стратегия (по AIEndurance):

    1. Ramp Detection:
       - Ищем участок с монотонным ростом intensity (HR или power)
       - Минимум 10 минут плавного нарастания
       - Должен происходить в первые 30 минут активности (до усталости)

    2. Interpolation:
       - Строим линейную регрессию: DFA_a1 = f(HR) или DFA_a1 = f(power)
       - HRVT1 (аэробный порог): HR/power где DFA a1 = 0.75
       - HRVT2 (анаэробный порог): HR/power где DFA a1 = 0.50

    3. Validation:
       - R² регрессии > 0.7 (хорошее качество)
       - Достаточный диапазон: DFA a1 должен пройти от >1.0 до <0.75
       - Artifact pct < 5% в зоне перехода

    Возвращает:
    {
        'hrvt1_hr': 142,           # HR на аэробном пороге
        'hrvt1_power': 180,        # Power на аэробном пороге (вело)
        'hrvt1_pace': '5:15',      # Pace на аэробном пороге (бег)
        'hrvt2_hr': 168,           # HR на анаэробном пороге
        'hrvt2_power': 240,        # Power на анаэробном пороге (вело)
        'r_squared': 0.89,
        'confidence': 'high',      # high (R²>0.8) | moderate (0.6-0.8) | low (<0.6)
        'method': 'ramp'           # 'ramp' | 'steady_state'
    }

    ВАЖНО: Для текущей фазы (лёгкие тренировки zone 1) порог может
    НЕ определиться, т.к. DFA a1 не опустится до 0.75.
    Это нормально — вместо этого используем readiness (2.7).
    """
```

### 2.7 Readiness to Train (Ra)

```python
def calculate_readiness_ra(dfa_timeseries: list[dict],
                            baseline_sessions: list[dict],
                            warmup_minutes: int = 15) -> dict:
    """
    Readiness to train (Ra) — ключевая метрика из AIEndurance.
    Оценивает готовность ПРЯМО ВО ВРЕМЯ разминки.

    Концепция:
    Pa = power/pace необходимый для поддержания фиксированного DFA a1.
    Если сегодня Pa ниже базлайна → не восстановлен.
    Если Pa выше → хороший день.

    Алгоритм:
    1. Из warmup (первые warmup_minutes минут):
       - Взять сегменты где DFA a1 стабильный (± 0.1)
       - Вычислить средний power/pace для этих сегментов → Pa_today

    2. Из baseline_sessions (последние 2 недели):
       - Аналогично вычислить Pa для каждой сессии
       - Pa_baseline = среднее

    3. Ra = (Pa_today - Pa_baseline) / Pa_baseline * 100

    Интерпретация:
    - Ra > +5%:  отличная готовность
    - Ra -5..+5%: нормальная готовность
    - Ra < -5%:  недовосстановление
    - Ra < -15%: сильное недовосстановление, рассмотреть отмену тренировки

    Типичный диапазон: -20% .. +20% (данные AIEndurance)

    ПРИМЕНИМОСТЬ:
    - Велосипед: power-based → наиболее точный
    - Бег: pace-based → зависит от рельефа, ветра (менее точный)
    - Плавание: НЕ применимо (нет RR-данных в воде)

    Возвращает:
    {
        'ra_pct': -8.5,
        'pa_today': 145,          # Вт или мин/км
        'pa_baseline': 158,
        'status': 'below_normal',
        'recommendation': 'reduce_intensity',
        'confidence': 'moderate',  # зависит от количества baseline сессий
        'warmup_quality': 'good'   # enough stable segments found
    }
    """
```

### 2.8 Durability (Da)

```python
def calculate_durability_da(dfa_timeseries: list[dict],
                             min_duration_min: int = 40) -> dict:
    """
    Durability (Da) — оценка устойчивости к усталости.

    Концепция:
    Сравнивает Pa (power при фиксированном a1) между первой
    и второй половиной тренировки.
    Если Pa падает → организм устаёт, теряет эффективность.

    Алгоритм:
    1. Разделить активность на 2 половины по времени
    2. Pa_first = средний power при стабильном a1 (первая половина)
    3. Pa_second = средний power при стабильном a1 (вторая половина)
    4. Da = (Pa_second - Pa_first) / Pa_first * 100

    Интерпретация:
    - Da > 0:   отличная выносливость (нет дрифта)
    - Da -5..0: нормальная выносливость
    - Da < -5:  заметная усталость
    - Da < -15: сильный дрифт, тренировка была слишком тяжёлой

    Требования:
    - Минимум 40 минут тренировки (иначе недостаточно данных)
    - Применимо только для устойчивых тренировок (не интервалы)

    Возвращает:
    {
        'da_pct': -3.2,
        'pa_first_half': 155,
        'pa_second_half': 150,
        'status': 'normal',
        'applicable': True  # False если < 40 мин или интервалы
    }
    """
```

---

## Level 2 — Схема данных

```sql
CREATE TABLE activity_hrv (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id     TEXT NOT NULL REFERENCES training_load(activity_id),
    date            TEXT NOT NULL,
    activity_type   TEXT NOT NULL,       -- 'bike' | 'run'  (не 'swim')
    hrv_quality     TEXT,                -- 'good' | 'moderate' | 'poor'
    artifact_pct    REAL,
    -- DFA alpha 1 summary
    dfa_a1_mean     REAL,               -- Среднее a1 за тренировку
    dfa_a1_warmup   REAL,               -- Среднее a1 за разминку (15 мин)
    -- Thresholds (если определены)
    hrvt1_hr        REAL,
    hrvt1_power     REAL,
    hrvt1_pace      TEXT,
    hrvt2_hr        REAL,
    r_squared       REAL,
    threshold_confidence TEXT,           -- 'high' | 'moderate' | 'low' | null
    -- Readiness
    ra_pct          REAL,               -- Readiness %
    pa_today        REAL,
    -- Durability
    da_pct          REAL,               -- Durability %
    -- Raw timeseries (JSON)
    dfa_timeseries  TEXT,               -- JSON array of {time_sec, dfa_a1, hr, power, ...}
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Baseline Pa для Readiness calculation
CREATE TABLE pa_baseline (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_type   TEXT NOT NULL,       -- 'bike' | 'run'
    date            TEXT NOT NULL,
    pa_value        REAL NOT NULL,       -- Power/pace at fixed a1 (warmup)
    dfa_a1_ref      REAL,               -- Reference a1 level used
    quality         TEXT,                -- 'good' | 'moderate'
    created_at      TEXT DEFAULT (datetime('now'))
);
```

---

## Интеграция уровней: Decision Engine

```python
def daily_training_decision(date: str) -> dict:
    """
    Ежедневная рекомендация на основе всех доступных данных.

    Приоритет сигналов:
    1. Level 1 (утро): Combined Recovery Score → базовое решение
    2. Level 2 (разминка): Ra → корректировка перед тренировкой

    Decision matrix:
    ┌──────────────────┬────────────────┬──────────────────────────────┐
    │ Recovery Score   │ Ra (если есть) │ Рекомендация                 │
    ├──────────────────┼────────────────┼──────────────────────────────┤
    │ < 30%            │ любой          │ REST: пропустить тренировку  │
    │ 30-50%           │ < -10%         │ REST: подтвердить отдых      │
    │ 30-50%           │ > -10%         │ EASY: zone 1, < 30 мин      │
    │ 50-70%           │ < -10%         │ EASY: zone 1, < 45 мин      │
    │ 50-70%           │ -10..+5%       │ MODERATE: zone 1, до 60 мин │
    │ 50-70%           │ > +5%          │ MODERATE: zone 1-2, до 60мин│
    │ 70-85%           │ < -5%          │ MODERATE: zone 1, до 60 мин │
    │ 70-85%           │ > -5%          │ GOOD: обычная тренировка     │
    │ > 85%            │ > 0%           │ EXCELLENT: можно качественную│
    └──────────────────┴────────────────┴──────────────────────────────┘

    Модификаторы для фазы возврата после болезни:
    - Если дней с тренировками < 14: максимум MODERATE (zone 1)
    - Если RMSSD < 40 ms (текущая ситуация): максимум EASY
    - Если RMSSD тренд растущий > 7 дней: +1 уровень
    - Велодром / высокая интенсивность запрещена до Recovery > 70%

    Возвращает:
    {
        'decision': 'EASY',
        'max_zone': 1,
        'max_duration_min': 30,
        'suggested_activity': 'swim',  # ротация с учётом предыдущих дней
        'hr_cap': 125,                 # Максимальный HR
        'reasoning': [
            'RMSSD 7d baseline (33ms) ниже 60d нормы',
            'Recovery score 45%: умеренное восстановление',
            'Фаза возврата: ограничение zone 1'
        ],
        'level1_score': 45,
        'level2_ra': None,  # Будет заполнено после разминки
        'flags': ['return_phase', 'rmssd_below_target']
    }
    """
```

---

## Pipeline обработки

### Ежедневный cron (утро, после sync)

```
1. Sync Garmin data → daily_hrv, training_load
2. Calculate RMSSD status (1.3)
3. Calculate RHR status (1.4)
4. Update Banister recovery model (1.6)
5. Generate Combined Recovery Score (1.7)
6. Run Decision Engine → утренняя рекомендация
7. Push Telegram notification
```

### Post-activity pipeline

```
1. Detect new activity via Garmin API polling
2. Download FIT file
3. Check: has HRV data? (activity_type in ['bike', 'run'] + HRM-Dual connected)
4. If yes:
   a. Extract RR intervals (2.1)
   b. Artifact correction (2.3)
   c. Quality check: artifact_pct < 10%?
   d. Calculate DFA a1 timeseries (2.5)
   e. Attempt threshold detection (2.6)
   f. Calculate Ra (2.7) → сравнить с утренним прогнозом
   g. Calculate Da (2.8) если duration > 40 мин
   h. Store in activity_hrv, update pa_baseline
5. If no (плавание или без датчика):
   a. Store basic metrics only (HR, duration, ESS)
6. Generate post-activity report → Telegram
```

---

## Спорт-специфичные нюансы

### Плавание
- **Level 1**: полностью применим (RMSSD покоя, ESS по HR)
- **Level 2**: НЕ применим (HRM-Dual не передаёт RR в воде)
- **ESS**: рассчитывать по HR если данные есть, иначе по duration + RPE
- **Пульс**: Garmin сохраняет HR пост-фактум из памяти пояса (средний HR бывает доступен)
- **Альтернатива**: субъективный RPE через Telegram check-in после тренировки

### Велосипед / велостанок
- **Level 1 + 2**: полностью применим
- **Power**: основная метрика для Pa, Ra, Da, порогов
- **Indoor (trainer)**: стабильнее для threshold detection (нет ветра, рельефа)
- **Cadence**: доступна, полезна для нормализации

### Бег
- **Level 1 + 2**: применим, но pace менее стабилен чем power
- **Pace/GAP**: использовать GAP (Grade Adjusted Pace) при рельефе
- **Running power** (если есть Stryd/Garmin): предпочтительнее pace для Ra/Da
- **Cardiac drift**: на длинных пробежках HR растёт при том же темпе — учитывать в Da

---

## Telegram Bot — команды и уведомления

```
/status          — текущий Recovery Score + рекомендация
/hrv             — RMSSD тренд (7d/60d), график
/readiness       — Level 2 Ra из последней тренировки
/thresholds      — текущие HRVT1/HRVT2 (если определены)
/week            — недельный обзор: нагрузка, восстановление, тренд
/activity [id]   — детальный разбор активности (DFA a1 график)

Автоматические уведомления:
- 07:00  Утренний Recovery Score + план дня
- Post-activity: Ra результат, сравнение с прогнозом
- Alert: Recovery < 30% два дня подряд → "рекомендуется день отдыха"
- Alert: Threshold shift > 5% → "аэробный порог изменился"
```

### Mini App (Chart.js) — графики

```
1. RMSSD Trend: 60-дневный график с 7d скользящим, зоной нормы, SWC
2. Recovery Timeline: Combined Score за 30 дней + маркеры тренировок
3. DFA a1 Activity View: time-varying a1 vs HR/power для конкретной активности
4. Threshold History: HRVT1/HRVT2 тренд по неделям
5. Readiness (Ra) History: тренд Ra по тренировкам
6. Banister Model: fitness/fatigue/form (CTL/ATL/TSB аналог)
```

---

## Фазовая реализация

### Phase 1 (сейчас — фаза возврата)
- [x] Daily sync: RMSSD, RHR, sleep, body battery
- [ ] RMSSD baseline analysis (7d vs 60d)
- [ ] RHR analysis (30d baseline)
- [ ] Simple ESS calculation
- [ ] Combined Recovery Score
- [ ] Telegram: /status, утренний алерт
- [ ] Chart: RMSSD trend

### Phase 2 (когда RMSSD стабилизируется > 40ms)
- [ ] FIT file download + RR extraction
- [ ] Artifact correction
- [ ] DFA a1 timeseries
- [ ] Ra (Readiness) calculation
- [ ] Telegram: post-activity report
- [ ] Chart: DFA a1 activity view

### Phase 3 (регулярные тренировки zone 1-2)
- [ ] Banister model calibration
- [ ] Threshold detection (HRVT1/HRVT2)
- [ ] Da (Durability) calculation
- [ ] Pa baseline tracking
- [ ] Decision matrix: Level 1 + Level 2 combined
- [ ] Charts: thresholds trend, Ra history

### Phase 4 (полноценные тренировки)
- [ ] Zone distribution analysis (time in zones via DFA a1)
- [ ] Fitness/fatigue/form model (CTL/ATL/TSB)
- [ ] Race readiness prediction
- [ ] Training plan adaptation based on recovery

---

## Ссылки

- Banister et al. — оригинальная модель fitness-fatigue
- Gronwald et al. 2020 — DFA a1 как биомаркер интенсивности
- Rogers et al. 2021 — DFA a1 для определения аэробного порога
- Lipponen & Tarvainen 2019 — artifact correction для RR-интервалов
- AIEndurance blog — recovery model, Ra, Da определения
- python-garminconnect — 105+ API endpoints, включая HRV, readiness
- fitparse — парсинг Garmin FIT файлов
