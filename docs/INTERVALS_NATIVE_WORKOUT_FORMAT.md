# Intervals.icu Native Workout Format — Research

> Цель: задокументировать грамматику Intervals.icu native description-формата
> и поведение их парсера, чтобы корректно генерировать `event.description`
> при пуше тренировок через `POST /events`.

**Status:** ✅ Грамматика установлена и проверена на живом API (2026-05-12, user 1, Swim probes).
**Trigger:** Пользователь сообщил, что AI-pushed тренировки в Intervals.icu UI выглядят как «голое имя+длительность, без шагов» для всех спортов и всегда. Часы (Garmin) видят структуру корректно через FIT.
**Источник грамматики:** [Workout Builder Syntax Quick Guide](https://forum.intervals.icu/t/workout-builder-syntax-quick-guide/123701) (Intervals.icu forum).
**Probe-скрипт:** `scripts/probe_intervals_swim_regression.py`.

---

## Два независимых канала отображения

`POST /athlete/{id}/events` принимает структуру тренировки **двумя способами одновременно**, и они обслуживают разные нужды:

| Канал | Поле | Кто читает | Что происходит при отсутствии |
|---|---|---|---|
| Структурированный JSON | `workout_doc.steps` | FIT-экспорт → Garmin/Wahoo, наш `ScheduledWorkout` сидинг | Часы не получают структурированную тренировку |
| Native-format текст | top-level `description` | Intervals.icu **web/mobile UI** (рендерит шаги + чарт + цели) | UI показывает только имя и длительность — шагов не видно |

**Наш прод-баг (до 2026-05-12):** `PlannedWorkoutDTO.to_intervals_event()` намеренно не ставит top-level `description` (из-за «Swim-регрессии» — см. ниже). Поэтому шагов в UI **никогда не было видно** ни для одного спорта.

**Что писать в `workout_doc.description`:** это поле рендерится в **Garmin Connect** (приложение на телефоне) как workout note — атлет видит его при просмотре тренировки. У нас туда уходит AI-rationale (`PlannedWorkoutDTO.rationale`), значит атлет читает обоснование AI прямо в Garmin Connect без захода в Intervals. **Подтверждено на user 1, Run-probe `109774400`, 2026-05-12.** Не путать с top-level `description` — это разные слоты.

---

## Грамматика native-формата

### Дистанция и время

| Что | Юнит | Пример | Что НЕ работает |
|---|---|---|---|
| Метры | `mtr` | `200mtr` | `200m` (= 200 минут!), `200 metres`, `200 meters` |
| Километры | `km` | `1.2km` | — |
| Мили | `mi` | `1mi` | — |
| Минуты | `m` | `5m`, `1h30m` | — |
| Секунды | `s` | `30s` | `30 secs`, `30sec` |
| Комбо | — | `5m30s`, `1h2m30s` | — |

**Ключевое:** буква `m` после числа означает **минуты**. Для метров — только `mtr`. Это первая ловушка, на которой мы сожгли первую пробу (`200m 50% Pace` → парсер решил «200 минут» → workout 12 км / 8h20m).

### Базовая строка шага

```
- [Label text] <duration|distance> <target> [cadence]
```

- `-` (тире + пробел) — bullet-маркер
- `[Label text]` — необязательный cue text. Парсер считает label'ом всё **до первого числового токена**. Поэтому label НЕ ДОЛЖЕН содержать цифр.
- `<duration|distance>` — обязательный numeric token: `5m` / `200mtr` / `1km`
- `<target>` — обязательный target: `75%`, `90-95% LTHR`, `70-80% Pace`, `200-240w`
- `[cadence]` — необязательный: `90rpm`

### Target-keywords (контекстно-зависимы)

| Keyword | Семантика | Применимость |
|---|---|---|
| `XX%` (без слова) | %FTP (power) | Ride — корректный таргет, в Run/Swim резолвится как 0-0w (см. ловушка 5) |
| `XX% LTHR` | %LTHR (HR) | Run, Ride, Swim, любой со сконфигурированным LTHR |
| `XX% Pace` | %threshold pace (velocity ratio) | Run, Swim |
| `XX-YYw` | абсолютные ватты | Ride |
| `Z1`–`Z5` (без квалификатора) | **резолвится в power zones** | **Только Ride** — для Run/Swim лейбл превратится в 0-0w таргет |
| `mm:ss/100m Pace` | абсолютный pace per 100m | Swim |
| `mm:ss/km Pace` | абсолютный pace per km | Run |

**Range:** `90-95% LTHR`, `200-240w`, `2:00/100m-2:20/100m Pace` — через дефис.

### Repeat-блоки

```
4x
- 100mtr 80-90% Pace
- 15s 40-50% Pace
```

**Правила** (нарушение → парсер срывается, описание трактуется как plain text):
1. Строка `Nx` отдельно (без bullet)
2. Саб-шаги — **flush-left bullets**, без вложенной индентации
3. **Пустая строка до и после** repeat-блока обязательна

---

## Парсерные ловушки

Список того, на чём мы сгорали в живых пробах. Все собраны на user 1, Swim, 2026-05-12.

### 1. `200m` parsed as 200 minutes
**Probe 1** (109768016, deleted). Native: `200m 50% Pace`. Результат: workout 12 км / 8h20m, pace 4:42/100m (это `% Pace` от user's CSS, развёрнутый на 200 мин). Парсер сработал — но не туда. Фикс: `200mtr`.

### 2. `200 metres` / `30 secs` — не распознаются
**Probe 2** (109769013, deleted). Native: `200 metres 50% Pace`, `30 secs 50% Pace`. Парсер: ❌ не распарсил, `workout_doc.steps` ушли в `[]` (полностью стёрты на серверной стороне), enrichment-поля (`normalized_power`, `polarization_index`, `zoneTimes`) тоже отсутствуют — диагностический признак «парсер не справился». Фикс: `mtr` / `s`.

### 3. «Свим-регрессия 2026-04-30» — на самом деле parse-failure
В прод-коде есть длинный комментарий о том, что при наличии top-level `description` Intervals «молча дропает workout_doc.steps для Swim events». Probes 1 (parse OK) и 2 (parse fail) на той же среде показали: **дропа `workout_doc.steps` не происходит, если парсер успешно распознал description**. Drop случается только когда парсер не справился — Intervals очищает оба представления, чтобы не было рассинхрона. Под Swim это просто чаще ловилось из-за специфики distance-based syntax.

### 4. Цифры внутри label
**Probe 3** (109769910, deleted после 109770902). В завтрашней реальной AI-Swim текст шагов был `"Drill: 50 fingertip drag + 50 free"`. Если положить такой label в native description, парсер схватит первое `50` как duration. Sanitization: убрать или ужать цифры — `"Drill fingertip drag and free"`.

### 5. `Z1`/`Z2` в Swim → power zones
**Probe v3** (109771372, deleted). Native: `100mtr Z2` для Swim. Парсер сматчил `Z2` как power zone (без квалификатора `Z*` дефолтит в power). UI отрисовал шкалу 0-400w, все targets `(0-0w)`. Фикс: для Swim/Run использовать `% Pace` или `% LTHR` явно. `Z*` оставить только для Ride.

### 6. `Z2` substring в label
В probe v3 label `"Z2 freestyle DPS focus"` тоже сматчился парсером как target — UI показал две target-аннотации на одной строке (`Z2 (0-0w) freestyle DPS focus 150mtr Z2 (0-0w)`). Sanitization: вырезать `Z\d+` из label перед рендером.

### 7. Заголовки разделов («Warmup», «Main», «Cooldown»)
В probe v1 у нас были такие заголовки между bullet-блоками. Без двойных переносов они приклеивались к соседним bullet'ам в UI (`200m 50% Pace ... Main` одной строкой). С двойными переносами могут работать как Markdown-заголовки (парсер игнорирует, UI рендерит). Безопаснее: не использовать секционные заголовки, label-cue на самом шаге достаточен.

---

## Sport-specific рецепты (проверены на user 1, 2026-05-12)

Минимальный набор форматов, в которых description гарантированно парсится и UI рендерит структуру корректно. Прод-рендерер должен следовать этим паттернам.

### Swim — distance + `% Pace`

```
- Warm-up easy mix 300mtr 65-78% Pace

4x
- Drill fingertip drag and free 100mtr 80-90% Pace
- Rest 15s 40-50% Pace

- Cool-down easy 100mtr 60-75% Pace
```

- Distance в `mtr`, rest-шаги по времени в `s`.
- `% Pace` резолвится через athlete's CSS из Swim sport_settings (`threshold_pace`).
- Enrichment-обогащение (`zoneTimes`, `normalized_power`, `polarization_index`) приходит в ответе — Intervals пересчитывает дистанции rep-групп с учётом pace.
- Watch-side: pace target экспортируется как velocity (m/s) → часы отображают в default speed unit (обычно km/h). UI рендерит /100m корректно.

### Run — duration + `% LTHR` (HR-driven)

```
- Warm-up easy 10m 75-82% LTHR

- Main aerobic 25m 85-89% LTHR

- Upper progression 10m 89-94% LTHR

- Cool-down 5m 70-80% LTHR
```

- Duration в `m`/`s`. Distance работает аналогично Swim (`mtr`/`km`) если нужны distance-based шаги.
- `% LTHR` резолвится через athlete's LTHR.
- Workout_doc echo без enrichment-полей — это норма для HR-only workout, нечего пересчитывать.
- Pace-driven Run: использовать `% Pace` (резолвится через Run threshold_pace) или абсолютный `mm:ss/km Pace`.

### Ride — duration + `% FTP` (power-driven)

```
- Warmup 8m 62-72%

4x
- On cadence build 4m 72-84%
- Off 1m 44-55%

- Transition 5m 72-77%

2x
- SS On 6m 88-94%
- SS Off 3m 50-60%

- Cooldown 9m 48-62%
```

- Bare `XX%` без квалификатора → %FTP (для Ride это валидный default).
- `Z\d+` тоже работает для Ride (резолвится в power zones).
- Workout_doc echo без enrichment-полей — same как Run.
- Repeat-блоки с rest-шагом внутри: rest по времени, work по времени или distance.

### Общее правило label sanitization

Перед рендером лейбла из `WorkoutStepDTO.text` — вырезать:
1. Leading digits (`"50 fingertip drag"` → `"fingertip drag"`)
2. `Z\d+` substrings для **Run и Swim** (для Ride оставить, валидный target)
3. Если после очистки label пустой — опустить, шаг рендерится без cue text

---

## Диагностические индикаторы

Когда `description` есть, по `workout_doc` в ответе `GET /events` можно понять, что увидел сервер:

| Признак | Что значит |
|---|---|
| `workout_doc.steps` есть, длина матчит | Парсер OK, шаги отрисуются в UI |
| `workout_doc.steps == []` | Парсер не распознал description — UI покажет plain text |
| `workout_doc` содержит `normalized_power` / `polarization_index` / `zoneTimes` | Парсер распознал и посчитал метрики |
| `workout_doc` БЕЗ этих обогащений | Парсер не справился (см. predikate `steps == []`) |
| Число top-level шагов в ответе ≠ числу в payload | Парсер реструктурировал — обычно из-за заголовков/синтакс-ошибок (см. ловушку 1, 7) |

---

## Watch-side ограничения (вне нашего кода)

Установлены опытным путём на пользовательском Garmin (user 1, 2026-05-12):

- **Swim pace target → отображается как km/h, а не /100m.** Это настройка часов: Activity > Swim > Data Pages > Pace. FIT-target кодируется как velocity (m/s), Garmin рендерит в дефолтном speed-юните часов. С нашей стороны Intervals UI показывает pace в /100m корректно (user'ы `pace_units: "SECS_100M"` в Swim sport_settings) — это watch-side.
- **Cue text для коротких rest-шагов не показывается.** Часы вместо «Rest» (label из description) отрисовывают generic-индикатор сегмента (например, оранжевый бордер экрана и название спорта). Workaround на нашей стороне нет — FIT-формат не гарантирует отображение cue text для каждого шага.

Оба пункта не блокируют ship — структура и target'ы доходят, только cosmetic-юниты watch-side.

---

## Что менять в проде

`data/intervals/dto.py:PlannedWorkoutDTO.to_intervals_event()` сейчас намеренно держит `description=None` для всех событий. После починки парсер-ловушек (label sanitization, target-keyword по sport) безопасно класть туда native-format рендер `self.steps`.

**Реализация:**
1. `_render_native_steps(steps: list[WorkoutStepDTO], sport: str) -> str` — рекурсивный рендер, обрабатывает repeat-группы с пустыми строками вокруг.
2. `_sanitize_label(text: str) -> str` — strip leading digits, strip `Z\d+` substrings, ensure label не начинается с цифры.
3. Target-keyword by sport:
   - Run → `% LTHR` (HR), `% Pace` (Pace)
   - Ride → `%` (FTP implied) или `XX-YYw`
   - Swim → `% Pace`
4. Distance vs duration: `step.distance` → `Nmtr`, иначе `step.duration` → `Ns`/`Nm`.
5. Top-level `event.description` = результат рендера. Top-level `event.target` уже устанавливается корректно (`PACE` для Swim/Run с pace-шагами).

**Тесты в `tests/db/test_ai_workouts.py`:**
- Грамматика для каждого спорта (Run HR, Run Pace, Ride power, Ride watts, Swim pace).
- Sanitization: leading-digit label, `Z\d+` substring, label с цифрой в середине.
- Repeat-блоки: blank-line invariant, nested target rendering.
- Round-trip: native description, отправленный пробным юзером, парсится Intervals'ом без потерь (golden file).

**Probe-скрипт после ship:**
`scripts/probe_intervals_swim_regression.py` — оставить как историческую справку и инструмент для ad-hoc верификации API-изменений Intervals'а. Безопасный idempotent с `--cleanup`.

---

## Probe-история (для воспроизводимости)

User 1 (athlete `i317960`), 2026-05-12:

| ID | Время | Что тестировали | Verdict |
|---|---|---|---|
| 109768016 | 17:xx | `200m` + headers `Warmup`/`Main`/`Cooldown` | ❌ minutes-as-distance, 12 км/8h20m |
| 109769013 | 17:xx | `200 metres` + `30 secs`, no headers | ❌ парсер не распознал → `steps == []` |
| 109769910 | 18:xx | `200mtr` + `30s`, no labels, `% Pace` | ✅ грамматика принята; rests есть, но без cue text |
| 109770902 | 18:22 | Зеркало завтрашней Swim 1700m, no labels | ✅ структура корректная |
| 109771372 | 18:27 | + labels на шагах, target `Z1/Z2` | ⚠️ `Z1/Z2` → power zones (0-0w UI) |
| 109771472 | 18:28 | + labels (без цифр и `Z*`), target `% Pace` | ✅ финальная рабочая конфигурация |
| 109774400 | 18:45 | Run 50' mirror, HR-driven (`% LTHR`), labels без `Z\d+` | ✅ UI/часы OK; Garmin Connect показал `workout_doc.description` как note |
| 109775099 | 18:51 | Ride 60' mirror (2×6 Sweet Spot + 4× cadence), `% FTP` | ✅ UI/часы OK; structure 1:1, две repeat-группы с разными reps в одном workout |

**Итог по трём спортам:** native-формат принят парсером, structure preserved 1:1 от наших `workout_doc.steps`, UI и часы рендерят корректно. Swim даёт enrichment-обогащение в ответе (Intervals пересчитывает distance для pace-rep-групп), Run и Ride возвращают минимальный echo — норма, не баг. Прод-рендерер можно делать единым (один code path для всех спортов) с per-sport target-keyword (см. §«Sport-specific рецепты»).

---

## Workout cards / зарядки (sport=Other)

Отдельный кейс — `compose_workout` (`mcp_server/tools/workout_cards.py`) пушит силовые/mobility тренировки с `type=Other`. У этих шагов нет естественного intensity-таргета (yoga/strength), и `_NO_TARGET_SPORTS` в `PlannedWorkoutDTO` пропускает их через validator.

**Native-format для Other не подходит.** Грамматика требует target после duration; без target парсер либо игнорирует строку, либо роняет всю структуру. Probe не проводился — путь нерентабельный по сравнению с альтернативой ниже.

**Текущее решение (`compose_workout` в `workout_cards.py`, корректно):**
- `workout_doc.steps` — structured 1-step-per-exercise repeat-группы (work + Rest). Garmin/Wahoo получают через FIT, на часах работает.
- `workout_doc.description` — `Exercises: N, ~M min\n<URL>`. Garmin Connect показывает как workout note + кликабельный URL на HTML-карточки с фото.
- **Top-level `event.description` — оставляется `None`** (для `Other` `to_intervals_event` не рендерит native-format). Раньше сюда мирорился тот же `Exercises: N, ~M min\n<URL>` через `_TOP_LEVEL_DESC_SPORTS = {Run, Ride, Other}` — константа **удалена в `28ce47ab` (14 мая)**. Причина: строка не нативная, и парсер Intervals **иногда** цепляет число из `N` / `~M min`, падает и стирает ОБА представления (`workout_doc.steps → []` И сам `description → None`, см. §3). Поведение data-dependent и молчаливое: на одних строках проходит чисто (probe 109776724/109777679, 2026-05-12; live-проба `Other` с description, 2026-06-10 — шаги целы), на других рушит (датапоинты ниже). Поэтому `None` — единственный strip-immune вариант. Цена: URL не виден в web-UI Intervals, но остаётся в Garmin-note (`workout_doc.description`) и в ответе тула.

**Датапоинты strip** (битая зарядка наблюдается как `description=None` + `steps=[]` одновременно — следствие того, что parse-failure стирает оба поля):
- `108298132` (2026-05-11) — `description=None`, steps целы. Это **норма** для текущего кода (ранняя версия дока трактовала `None` здесь как deploy-window-аномалию — неверно: `None` и есть правильное strip-immune состояние).
- `115111453` (2026-06-10) — `description=None`, `steps=[]`. Создана **не** финальным путём `compose_workout` (имя с префиксом `AI:`, которого тул не ставит — он его срезает) → ушла в Intervals с не-нативным top-level description, парсер упал и обнулил оба поля. Фикс: перепушнута чистым `compose_workout` → `115289823`, `steps=7`, стабильно (проверено +50с). Триггер именно битого create не зафиксирован (live-strip не воспроизвёлся текущим кодом).

**Для прод-рендера native description в `to_intervals_event()`:** для `sport in _NO_TARGET_SPORTS` (т.е. Other) **не рендерить native-format**, оставить текущую логику workout_cards.py. Только Swim/Run/Ride идут через native-format renderer.
