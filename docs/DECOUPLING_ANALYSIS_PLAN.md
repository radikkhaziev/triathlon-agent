# Интеграция анализа сердечного дрейфа (Decoupling) в Triathlon AI Agent

Данный документ описывает техническую спецификацию для внедрения метрики сердечного дрейфа (Aerobic Decoupling) в платформу Triathlon AI Agent.

---

## Часть 1. Теория и методология

Сердечный дрейф — это расщепление между интенсивностью (темп/мощность) и пульсом во время длительной тренировки. Ключевая формула: `Decoupling = ((EF1 - EF2) / EF1) * 100`, где EF — Efficiency Factor (нормализованная мощность / средний ЧСС) для каждой половины тренировки. Intervals.icu вычисляет Pa:Hr decoupling по аналогичной модели — нам нужно реализовать правильную фильтрацию, а не пересчитывать математику.

> Full theory: [docs/knowledge/decoupling.md](knowledge/decoupling.md)

---

## Часть 2. Архитектурный анализ

### Текущее состояние

В нашей БД таблица `activity_details` уже хранит поля `efficiency_factor` и `decoupling`. Эти данные мы получаем через API из Intervals.icu. Платформа Intervals.icu вычисляет Pa:Hr decoupling по аналогичной математической модели (деление активности пополам после разминки).

Кроме того, `activity_details` уже хранит `variability_index` (`database.py:820`) и `hr_zone_times` (`database.py:828`) — оба поля доступны и заполняются при синке. Это означает, что фильтрация может опираться на объективные данные из БД, а не на теги.

В MCP-инструменте `get_efficiency_trend` (`mcp_server/tools/progress.py`) уже считается `decoupling_mean` по неделям (строки 211–213) и возвращается decoupling по каждой активности. Однако этот инструмент фильтрует по Z2 avg HR и минимальной длительности (30 мин bike / 20 мин run), что недостаточно строго для анализа дрейфа.

_Вывод:_ Нам не нужно писать математику с нуля, мы можем доверять данным `decoupling` из API Intervals.icu. Нам нужно реализовать **правильную фильтрацию** для decoupling-анализа и **расширить существующий инструмент**, а не создавать дублирующий.

---

## Часть 3. Предложения по улучшениям архитектуры (Propose enhancements)

Основываясь на архитектуре платформы (Python, FastAPI, MCP, Claude API), предлагаются следующие доработки.

### 1. Фильтр `is_valid_for_decoupling()` в `data/metrics.py`

Функция принимает activity + activity_details и возвращает `True`, если тренировка пригодна для decoupling-анализа:

| Критерий | Bike | Run | Swim |
|---|---|---|---|
| Минимальная длительность (`moving_time`) | >= 60 мин | >= 45 мин | Исключён |
| Variability Index (`activity_details.variability_index`) | <= 1.10 | <= 1.10 | — |
| Зонная принадлежность (`activity_details.hr_zone_times`) | >70% в Z1+Z2 | >70% в Z1+Z2 | — |
| Decoupling не null | Да | Да | — |

**Почему VI <= 1.10 (а не 1.05):** валидировано на реальных данных. VI=1.05 слишком строг — steady-state outdoor ride с холмами или светофорами даст VI 1.05–1.10. Интервальные тренировки имеют VI >= 1.24 и чётко отсеиваются. VI <= 1.10 — оптимальный баланс: пропускает ровные тренировки, отсекает интервалки.

**Почему не 90 минут:** при текущем CTL ~30–40 за 6 месяцев только 4 bike-тренировки прошли бы фильтр (VI <= 1.10 + 60 мин). При пороге 90 мин — только 3. Порог 60/45 мин даёт на 33% больше данных, оставаясь физиологически валидным (Intervals.icu корректно считает decoupling для таких длительностей).

**Почему не теги ("Intervals", "Fartlek"):** теги ненадёжны — атлет не всегда их проставляет. `variability_index` + `hr_zone_times` объективно определяют стабильность темпа и зонную принадлежность из данных самой тренировки.

### 2. Расширение `get_efficiency_trend` MCP-инструмента

Вместо создания отдельного `get_decoupling_trend` — расширить существующий `get_efficiency_trend` в `mcp_server/tools/progress.py`:

- Добавить параметр `strict_filter: bool = False`. Если `True`, применяется строгая фильтрация через `is_valid_for_decoupling()` (VI, hr_zone_times, min duration) и в ответ включается `decoupling_trend`.
- В `_group_weekly()` при `strict_filter` добавить `decoupling_median` вместо `decoupling_mean` — медиана устойчивее к выбросам от разовых плохих дней.
- **Trend window:** rolling last-5 (последние 5 подходящих тренировок, независимо от даты). При текущем объёме (~4 точки за 6 месяцев) фиксированное 4-week окно будет постоянно давать gaps. Last-N адаптивен к частоте тренировок.
- Добавить в ответ `decoupling_status` (traffic light) для последнего окна.

### 3. Грейдинг — Traffic Light System

| Зона | Decoupling | Значение | Действие |
|---|---|---|---|
| **green** | abs(dec) < 5% | Аэробная база в норме | Зелёный свет для интенсивных блоков (Z4/Z5) |
| **yellow** | 5% — 10% | Требуется корректировка | Снизить темп длительных, мониторить гидратацию/сон |
| **red** | > 10% | Аэробный дефицит или перетрен | Base Building Protocol (при устойчивом паттерне) |

**Отрицательный decoupling** (пульс падает при неизменном усилии): бывает при хорошем прогреве, overhydration, или когда Intervals.icu считает разминку. Грейдинг: `abs(value) < 5%` = green. В утреннем отчёте не акцентируется — это нормальный вариант.

### 4. Контекст для утреннего анализа (Claude Morning Analysis)

- **Входные данные:** в промпт агента (`ai/prompts.py`) передавать decoupling + traffic light с последней длительной тренировки, прошедшей фильтр `is_valid_for_decoupling()`.
- **Формат в промпте:** `"decoupling": {"value": 12.3, "status": "red", "activity_date": "2026-03-28", "days_since": 2, "sport": "run"}`.
- **`days_since`** — критически важно. При текущем объёме тренировок последняя подходящая тренировка может быть 2+ недели назад. Если `days_since > 7`, Claude не должен акцентировать decoupling в отчёте — данные устарели. Если `days_since > 14`, decoupling не включается в промпт вовсе.
- **Логика Claude:** утренний отчёт — one-shot report, не conversational UI. Claude формулирует **наблюдение**, а не вопрос: _"Дрейф 12% на вчерашней длительной — проверь гидратацию и углеводы"_. Вопросы уместны только в свободном чате (Phase 3).

### 5. Интеграция с Adaptive Training Plan (ATP)

- **Recovery Score (0-100):** Decoupling НЕ включается в формулу Recovery Score (она базируется на утренних показателях покоя: HRV, RHR, Sleep).
- **ATP-триггер с критерием устойчивости:** один плохой день (жара, обезвоживание) не должен ломать тренировочный план. Триггер для Base Building Protocol: **2 из 3 последних** тренировок, прошедших фильтр `is_valid_for_decoupling()`, показали дрейф > 10%. При единичном красном дрейфе — только предупреждение в утреннем отчёте, без модификации плана.
- **Base Building Protocol:** запланированные Z4/Z5 работы понижаются до Z2 на 1-2 недели. Возвращение к интенсивным работам — после того как decoupling вернётся в жёлтую/зелёную зону.

### 6. Визуализация в Dashboard (Webapp)

- **График: Темп/Мощность vs Decoupling.** Scatter plot на странице `/dashboard` (или расширение Progress). Ось X — дата, двойная ось Y — EF (или pace) и decoupling%. Атлет видит: год назад 6:00/км с дрейфом 5%, сейчас 5:15/км с тем же дрейфом — это и есть прогресс.
- Данные берутся из расширенного `get_efficiency_trend(strict_filter=True)`.

---

## Часть 4. Фазирование реализации

### Фаза 1 — Core (Issue #10)

Минимально ценная реализация. Всё, что нужно для того, чтобы Claude начал учитывать decoupling.

| Задача | Файл | Описание |
|---|---|---|
| `is_valid_for_decoupling()` | `data/metrics.py` | Фильтр: moving_time, VI <= 1.10, hr_zone_times > 70% Z1+Z2. Bike >= 60 мин, Run >= 45 мин, Swim исключён |
| Расширить `get_efficiency_trend` | `mcp_server/tools/progress.py` | Параметр `strict_filter`, last-5 trend median, `decoupling_status` |
| Decoupling в утренний промпт | `ai/prompts.py`, `ai/tool_definitions.py` | Передать decoupling + traffic light + `days_since`. Suppress if > 14 дней |
| Traffic light grading | `data/metrics.py` | `decoupling_status(value) -> green/yellow/red`. `abs(value)` для negative drift |
| Тесты | `tests/` | Фильтрация, грейдинг, edge cases (null, negative, short, swim, VI > 1.10) |

### Фаза 2 — Trend & Visualization

Графики и тренд-анализ.

| Задача | Файл | Описание |
|---|---|---|
| Dashboard chart | `webapp/src/pages/Dashboard.tsx` | Scatter: EF + Decoupling% vs Date, двойная ось Y |
| API endpoint | `api/routes.py` | GET `/api/decoupling-trend` (прокси к MCP tool) |
| Rolling trend | `mcp_server/tools/progress.py` | Last-5 median (адаптивный к частоте тренировок) |

### Фаза 3 — Advanced (отдельные issues)

Зависит от готовности Gemini Weekly Analyst и накопления данных.

| Задача | Зависимости | Описание |
|---|---|---|
| ATP-интеграция | Фаза 1 + 30+ записей в training_log | Base Building Protocol при устойчивом дрейфе >10% (2 из 3) |
| Heat Adaptation logging | Новая таблица `heat_exposures` | `/sauna` команда + MCP tool. Корреляция с дрейфом |
| Gemini-корреляция | #21 Gemini Weekly Analyst | Автоматический еженедельный анализ паттернов дрейфа |
| Z2 кросс-валидация | Фаза 1 + DFA a1 данные | Сопоставление decoupling <5% с пульсовыми зонами для уточнения границ |

---

## Часть 5. Ключевые архитектурные решения

| Решение | Обоснование |
|---|---|
| Не создавать отдельный MCP tool `get_decoupling_trend` | `get_efficiency_trend` уже возвращает decoupling, нет смысла дублировать. Расширяем параметром `strict_filter` |
| Не включать decoupling в Recovery Score | Recovery Score — утренние показатели покоя (HRV, RHR, Sleep). Decoupling — тренировочный показатель, влияет на ATP, а не на recovery |
| Ступенчатые пороги длительности (60/45 мин) вместо единых 90 мин | При CTL ~30–40 за 6 месяцев 4 точки при 60 мин vs 3 при 90 мин. Каждая точка на вес золота |
| VI <= 1.10 (а не 1.05) | Валидировано: steady-state VI = 1.00–1.01, интервалки = 1.24–1.32. Порог 1.10 оптимален. 1.05 отсёк бы outdoor rides с холмами |
| VI + hr_zone_times вместо тегов | Теги ненадёжны. `variability_index` и `hr_zone_times` заполняются автоматически из Intervals.icu API |
| `strict_filter` (а не `decoupling_only`) | Семантически точнее: это режим строгой фильтрации для decoupling-анализа, а не "только decoupling" |
| Last-5 median (а не 4-week rolling) | При ~4 точках за 6 месяцев фиксированное 4-week окно постоянно даёт gaps. Last-N адаптивен к частоте тренировок |
| `days_since` в утреннем промпте | Последняя подходящая тренировка может быть 2+ недели назад. Suppress decoupling в промпте при days_since > 14 |
| `abs(value)` для грейдинга | Отрицательный drift (-4.7% = пульс падает) — нормальный вариант, green. Без abs() попал бы в green по < 5%, но логику лучше сделать явной |
| Критерий устойчивости (2 из 3) для ATP-триггера | Один плохой день (жара, недосып) не должен блокировать интервалы на неделю |
| Swim исключён из decoupling-анализа | Интервальная природа бассейна (стенки, развороты) делает Pa:Hr decoupling нерелевантным |
