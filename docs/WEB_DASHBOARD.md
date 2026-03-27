# Web Dashboard (#7)

Полноценный дашборд с управлением, не только просмотр.

## Архитектура

Single-page app: `dashboard.html` + `app.js` + `charts.js` + `style.css`.
Telegram Mini App (через WebAppInfo) или standalone (прямой URL).
Стек: HTML + Chart.js + Tailwind CSS (CDN). Без фреймворков.

## Вкладки

**Today** — утренний отчёт
- Recovery gauge + score
- HRV/RHR/Sleep метрики
- CTL/ATL/TSB
- AI рекомендация
- Источник: `GET /api/report` (уже есть)

**Calendar** — активности и план по дням
- Календарь-сетка с иконками спорта
- Клик по дню → список активностей + запланированные тренировки
- Клик по активности → детальная статистика (HR, power, pace, laps) из `get_activity_details`
- Источник: `GET /api/calendar?from=YYYY-MM-DD&to=YYYY-MM-DD`

**Load** — графики тренировочной нагрузки
- CTL/ATL/TSB line chart (12 недель)
- Daily TSS stacked bar chart по видам спорта
- Ramp rate indicator
- Источник: `GET /api/training-load?days=84`

**Goal** — прогресс к Ironman 70.3
- Countdown (weeks remaining)
- Per-sport CTL progress bars vs targets
- CTL trend chart per sport
- Источник: `GET /api/goal`

## Manual Job Triggers

Кнопки в UI для ручного запуска джобов (без ожидания cron):

| Кнопка | API endpoint | Что делает |
|---|---|---|
| 🔄 Синхронизировать план | `POST /api/jobs/sync-workouts` | `scheduled_workouts_job()` |
| 🔄 Загрузить активности | `POST /api/jobs/sync-activities` | `sync_activities_job()` + `process_fit_job()` |
| 📊 Утренний отчёт | `POST /api/jobs/morning-report` | `daily_metrics_job(run_ai=True)` |
| 🔄 Обновить wellness | `POST /api/jobs/sync-wellness` | `daily_metrics_job()` |

**Безопасность:** Job endpoints защищены Telegram initData (как `/api/report`) — только авторизованный пользователь.

**Ответ:** `202 Accepted` + job запускается async. Опционально: WebSocket/SSE для статуса выполнения (v2).

## API Endpoints (новые)

```
GET  /api/calendar?from=&to=       — активности + planned workouts по дням
GET  /api/training-load?days=84    — CTL/ATL/TSB/TSS timeseries
GET  /api/goal                     — race goal progress
GET  /api/activity/{id}/details    — full activity stats + laps
POST /api/jobs/sync-workouts       — trigger plan sync
POST /api/jobs/sync-activities     — trigger activity sync + DFA
POST /api/jobs/morning-report      — trigger morning report
POST /api/jobs/sync-wellness       — trigger wellness sync
```

## Порядок реализации (вертикальные срезы)

1. **Today tab** — адаптировать `app.js` под `/api/report` (минимум работы)
2. **Job triggers** — POST endpoints + кнопки в UI (максимальная польза сразу)
3. **Load tab** — `/api/training-load` + Chart.js графики
4. **Goal tab** — `/api/goal` + progress bars
5. **Calendar tab** — `/api/calendar` + activity details drill-down (самый объёмный)
