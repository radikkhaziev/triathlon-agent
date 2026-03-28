# Gemini Role Specification: Pattern Analyst

> Разделение ролей Claude и Gemini — от дублирования к специализации.

---

## Проблема

Сейчас Claude и Gemini получают одинаковые данные, одинаковый промпт (с минимальными различиями в форматировании) и выдают два похожих утренних отчёта. Это дублирование: два мнения на одну тему без существенной разницы. Пользователь видит два таба в webapp и не получает дополнительной ценности.

## Решение: разделение ролей

| Роль | Модель | Частота | Данные | Задача |
|---|---|---|---|---|
| **Оперативный тренер** | Claude (`claude-sonnet-4-6`) | Ежедневно (утро) | Сегодняшние метрики + план | Утренняя рекомендация, адаптация тренировки, генерация workout |
| **Аналитик паттернов** | Gemini (`gemini-2.5-flash`) | Еженедельно (понедельник) | Вся история training_log + wellness | Поиск корреляций, персональные паттерны, тренды, prompt enrichment |

Claude принимает решения в реальном времени. Gemini анализирует историю в фоне.

---

## Текущее состояние (до изменений)

### Claude
- Модель: `claude-sonnet-4-6`, max_tokens 1024
- Промпт: `MORNING_REPORT_PROMPT` (русский, 4 секции, 250 слов)
- Вызов: ежедневно в `daily_metrics_job` при появлении sleep data (или 11:00 deadline)
- Результат: `wellness.ai_recommendation`
- Отображение: Telegram message + webapp tab

### Gemini
- Модель: `gemini-2.5-flash`, thinking 4096, max output 8192
- Промпт: `MORNING_REPORT_PROMPT_GEMINI` (русский, те же 4 секции, строже формат)
- Вызов: параллельно с Claude через `asyncio.gather()`
- Результат: `wellness.ai_recommendation_gemini`
- Отображение: только webapp tab (не в Telegram)
- Гейт: `GOOGLE_AI_API_KEY` не пустой

### Проблема дублирования
- Оба получают **одни и те же данные** через `build_morning_prompt()`
- Оба отвечают на **один и тот же вопрос** — "как тренироваться сегодня?"
- Разница только в стиле: Claude — компактный, Gemini — подробный с Markdown
- Нет разделения ответственности

---

## Новая архитектура

### Claude — Оперативный тренер (без изменений)

Продолжает делать то, что делает сейчас:
- Утренний анализ готовности (ежедневно)
- Генерация тренировки (Фаза 1 Adaptive Training Plan)
- Адаптация тренировки HumanGo (Фаза 2 Adaptive Training Plan)

Единственное дополнение — Claude получает `personal_patterns` в промпте (результат работы Gemini). Это повышает качество решений без изменения архитектуры.

### Gemini — Аналитик паттернов (новая роль)

#### Зачем Gemini?

1. **Контекстное окно.** 1M+ токенов. Когда в `training_log` накопится 60+ записей с полным pre/post контекстом — это десятки тысяч токенов. Claude с 200K может не вместить всю историю + метрики + промпт. Gemini вместит всю историю целиком.

2. **Thinking mode.** `gemini-2.5-flash` с thinking budget 4096 хорош для аналитических задач: поиск корреляций, группировка паттернов, статистические наблюдения.

3. **Стоимость.** Flash-модель дешёвая. Еженедельный вызов с большим контекстом — копейки.

#### Что анализирует

**Входные данные:**
- Все записи `training_log` за последние 60 дней (pre-контекст → нагрузка → post-outcome)
- Все записи `wellness` за 60 дней (CTL/ATL/TSB динамика)
- Текущие пороги HRVT1/HRVT2 и их история
- Mood check-ins за период
- IQOS данные (корреляция с recovery)

**Выходные данные (JSON):**

```json
{
  "analysis_date": "2026-04-06",
  "period_days": 60,
  "records_analyzed": 45,

  "patterns": {
    "recovery_response": {
      "summary": "При recovery 55-65 Z2 steady даёт recovery_delta +5..+8. Z3 интервалы при том же recovery → delta -15..-20.",
      "safe_z3_threshold": 68,
      "optimal_z2_recovery_range": [50, 70],
      "examples": [
        {"date": "2026-03-15", "pre_recovery": 62, "workout": "Z3 intervals 45min", "delta": -18},
        {"date": "2026-03-18", "pre_recovery": 58, "workout": "Z2 steady 50min", "delta": +7}
      ]
    },

    "personal_thresholds": {
      "summary": "Стандартный порог moderate (<70) → max Z2 слишком консервативный. Факт: при recovery 55-65 атлет успешно выполняет Z3 и delta > 0 в 60% случаев.",
      "suggested_moderate_threshold": 50,
      "confidence": "medium",
      "sample_size": 12
    },

    "hrv_sensitivity": {
      "summary": "HRV yellow + recovery > 70 → тренировки проходят нормально (delta > 0 в 75% случаев). HRV red — всегда негативный delta.",
      "hrv_yellow_safe": true,
      "hrv_red_override": true
    },

    "dfa_readiness": {
      "summary": "Ra < -5% три дня подряд предшествует провалу recovery на 4-й день в 80% случаев.",
      "ra_warning_streak": 3,
      "ra_predictive_value": 0.8
    },

    "sleep_impact": {
      "summary": "Sleep score < 60 + Z3 тренировка → recovery_delta всегда < -10. Sleep < 60 + Z2 → delta нейтральный.",
      "sleep_threshold_for_intensity": 60
    },

    "sport_recovery": {
      "summary": "Бег нагружает сильнее велосипеда при одинаковом TSS. Run TSS 50 ≈ Bike TSS 65 по recovery_delta.",
      "run_to_bike_tss_ratio": 0.77
    },

    "weekly_volume": {
      "summary": "Оптимальный недельный объём: 5-6 часов. При >7 часов recovery_delta падает ниже нуля к концу недели.",
      "optimal_weekly_hours": [5, 6],
      "overload_threshold_hours": 7
    },

    "iqos_correlation": {
      "summary": "Дни с >8 стиков: HRV на следующий день ниже на 8-12% vs baseline.",
      "threshold_sticks": 8,
      "hrv_impact_pct": -10
    }
  },

  "recommendations": [
    "Снизить порог moderate с 70 до 55 для адаптации тренировок",
    "При HRV yellow не снижать Z3 автоматически — проверять recovery score",
    "Приоритизировать велосипед над бегом в дни с recovery 55-65",
    "Рассмотреть ramp-тест: последний HRVT1 bike — 25 дней назад"
  ],

  "prompt_snippet": "Персональные паттерны атлета (обновлено 2026-04-06): ..."
}
```

#### Четыре паттерна обучения

##### 1. Recovery Response Model

**Вопрос:** при каком `pre_recovery` + каком типе нагрузки → какой `recovery_delta`?

```
Данные: training_log записи с заполненным post-outcome
Группировка: pre_recovery_score buckets (40-55, 55-70, 70-85, 85+) × workout_type (Z1, Z2, Z3, Z4+, rest)
Метрика: средний recovery_delta по группе

Результат: матрица "recovery × intensity → outcome"
→ "При recovery 55-65 Z2 даёт +5, Z3 даёт -18"
```

##### 2. Personal Adaptation Thresholds

**Вопрос:** при каком `pre_recovery` атлет реально справляется с Z3+ нагрузкой?

```
Данные: записи где actual_max_zone >= Z3
Группировка: по pre_recovery_score
Метрика: % случаев где recovery_delta > 0

Результат: персональный порог для снижения зоны
→ "Стандарт: recovery < 70 → max Z2. Факт: этот атлет справляется с Z3 при recovery > 55"
```

##### 3. HRV Sensitivity

**Вопрос:** насколько HRV yellow/red предсказывает плохой outcome?

```
Данные: записи с HRV yellow/red
Группировка: по hrv_status × фактической интенсивности
Метрика: recovery_delta

Результат: валидация HRV как предиктора
→ "HRV yellow при recovery > 70 — ложная тревога в 75% случаев"
```

##### 4. DFA Readiness (Ra) Predictor

**Вопрос:** предсказывает ли серия отрицательных Ra провал?

```
Данные: записи с Ra данными
Паттерн: N дней подряд Ra < -5%
Метрика: recovery_delta на N+1 день

Результат: предиктивная ценность Ra streak
→ "Ra < -5% три дня → 80% шанс провала на 4-й день"
```

---

## Интеграция

### Scheduler

Новый cron job: `weekly_patterns_job`

```python
# bot/scheduler.py

# Еженедельный анализ паттернов — понедельник 03:00
scheduler.add_job(
    weekly_patterns_job,
    CronTrigger(day_of_week="mon", hour=3, minute=0),
    id="weekly_patterns",
)
```

**Время:** 03:00 понедельника — до утреннего отчёта, чтобы свежие паттерны были доступны Claude.

### Поток данных

```
Понедельник 03:00  weekly_patterns_job
  → Gemini получает: training_log (60 дней) + wellness (60 дней) + thresholds + mood + iqos
  → Gemini анализирует: Recovery Response + Personal Thresholds + HRV Sensitivity + DFA Readiness
  → Результат: JSON с паттернами + prompt_snippet
  → Сохраняется: personal_patterns (таблица или JSON файл)

Каждое утро 06:00  daily_metrics_job
  → Claude получает: сегодняшние метрики + planned workouts + personal_patterns.prompt_snippet
  → Claude решает: адаптировать / генерировать / не трогать тренировку
  → Решения основаны на персональных порогах, а не стандартных
```

### Prompt enrichment

`build_morning_prompt()` дополняется секцией:

```
ПЕРСОНАЛЬНЫЕ ПАТТЕРНЫ (обновлено {patterns_date}):
{prompt_snippet}

Используй эти паттерны для корректировки стандартных правил.
Если паттерн противоречит стандартному правилу — приоритет у паттерна
(при confidence >= medium и sample_size >= 10).
```

### Хранение результатов

**Вариант A: таблица `personal_patterns`**

```sql
CREATE TABLE personal_patterns (
    id              SERIAL PRIMARY KEY,
    analysis_date   VARCHAR(10) NOT NULL,
    period_days     INTEGER NOT NULL DEFAULT 60,
    records_analyzed INTEGER NOT NULL,
    patterns_json   JSONB NOT NULL,          -- полный JSON результат
    prompt_snippet  TEXT NOT NULL,            -- сжатый текст для Claude промпта
    model           VARCHAR(50) DEFAULT 'gemini-2.5-flash',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_personal_patterns_date ON personal_patterns(analysis_date DESC);
```

**Вариант B: колонка в wellness** — менее чисто, patterns не привязаны к конкретному дню.

Рекомендация: **Вариант A** — отдельная таблица. Паттерны обновляются раз в неделю, а не ежедневно.

### Утренний Gemini отчёт

Ежедневный Gemini отчёт (текущий `ai_recommendation_gemini`) **убирается**. Вместо него — еженедельный анализ паттернов. Это устраняет дублирование и даёт Gemini осмысленную роль.

**Миграция:**
1. `get_morning_recommendation()` в `gemini_agent.py` → deprecated
2. Новый `analyze_patterns()` в `gemini_agent.py`
3. `wellness.ai_recommendation_gemini` остаётся для обратной совместимости (старые записи)
4. Webapp: вместо таба "Gemini" → "Паттерны" (показывает последний анализ)

---

## Webapp изменения

### Tab "Паттерны" (вместо "Gemini")

Показывает последний еженедельный анализ:
- Дата анализа, количество проанализированных записей
- Recovery Response: матрица recovery × intensity → outcome
- Personal Thresholds: текущие vs стандартные
- HRV Sensitivity: статистика yellow/red
- Рекомендации Gemini

### Report page

- Tab 1: Claude (утренняя рекомендация) — без изменений
- Tab 2: Паттерны (последний Gemini анализ) — вместо дублирующего отчёта

---

## Промпт для Gemini

```python
WEEKLY_PATTERNS_PROMPT = """
Ты — спортивный аналитик. Твоя задача — найти персональные паттерны
восстановления и адаптации атлета на основе исторических данных.

### ВХОДНЫЕ ДАННЫЕ

TRAINING LOG (последние {period_days} дней, {records_count} записей):
{training_log_data}

WELLNESS TREND:
{wellness_trend_data}

ПОРОГИ (HRVT1/HRVT2):
{thresholds_data}

MOOD CHECK-INS:
{mood_data}

IQOS:
{iqos_data}

### ЗАДАЧА

Проанализируй данные и найди 4 типа паттернов:

1. **Recovery Response:** При каком pre_recovery + какой нагрузке → какой recovery_delta?
   Группируй по: recovery buckets (40-55, 55-70, 70-85, 85+) × intensity (Z1, Z2, Z3, Z4+, rest).

2. **Personal Thresholds:** При каком recovery атлет реально справляется с Z3+?
   Сравни стандартный порог (70) с фактическим.

3. **HRV Sensitivity:** Насколько HRV yellow/red предсказывает плохой outcome?
   Отдельно: HRV yellow + recovery > 70 — ложная тревога?

4. **DFA Readiness:** Предсказывает ли серия Ra < -5% провал?

Также проверь:
- Влияние сна (sleep_score) на outcome
- Разницу между спортами (run vs bike) при одинаковом TSS
- Оптимальный недельный объём
- Корреляцию IQOS с HRV/recovery

### ФОРМАТ ОТВЕТА — строго JSON

{json_schema}

Поле "prompt_snippet" — сжатый текст (до 300 слов, русский) для включения
в ежедневный промпт другой AI-модели. Должен содержать конкретные цифры
и пороги, не общие фразы.

Если данных недостаточно для уверенного вывода (< 10 записей в группе),
укажи confidence: "low" и не включай в prompt_snippet.
"""
```

---

## Зависимости

| Зависимость | Статус | Описание |
|---|---|---|
| `training_log` таблица | Фаза 3 ATP | Основной источник данных для Gemini |
| `personal_patterns` таблица | Эта спека | Хранение результатов анализа |
| `build_morning_prompt()` | Существует | Дополнить секцией персональных паттернов |
| `gemini_agent.py` | Существует | Добавить `analyze_patterns()`, deprecated `get_morning_recommendation()` |

**Важно:** полноценный анализ паттернов возможен только после 30+ записей в `training_log`. До этого — Gemini работает в текущем режиме (утренний отчёт) или не вызывается.

---

## Порядок реализации

| # | Задача | Зависит от | Файлы |
|---|---|---|---|
| 1 | Таблица `personal_patterns` + Alembic миграция | — | `data/database.py`, миграция |
| 2 | `analyze_patterns()` в `gemini_agent.py` | training_log (Фаза 3 ATP) | `ai/gemini_agent.py` |
| 3 | `WEEKLY_PATTERNS_PROMPT` | — | `ai/prompts.py` |
| 4 | `weekly_patterns_job` в scheduler | #2 | `bot/scheduler.py` |
| 5 | Prompt enrichment в `build_morning_prompt()` | #1 | `ai/claude_agent.py` |
| 6 | Webapp: tab "Паттерны" | #1 | `webapp/src/pages/Report.tsx` |
| 7 | Убрать ежедневный Gemini отчёт | #2 | `ai/gemini_agent.py`, `data/database.py` |

**Критический путь:** Фаза 3 ATP (`training_log`) → эта спека (#2, #4) → prompt enrichment (#5).

---

## Критерии готовности

- [ ] `weekly_patterns_job` запускается в понедельник 03:00
- [ ] Gemini получает полный training_log + wellness за 60 дней
- [ ] Результат сохраняется в `personal_patterns` как JSON
- [ ] `prompt_snippet` включается в утренний промпт Claude
- [ ] Webapp показывает tab "Паттерны" с последним анализом
- [ ] Ежедневный Gemini отчёт убран (legacy данные доступны)
- [ ] При < 30 записей в training_log — Gemini не вызывается (недостаточно данных)
