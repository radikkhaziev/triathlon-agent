# React Migration Plan — Webapp Frontend

> Миграция webapp/ с inline HTML+JS+CSS на React + TypeScript + Vite SPA.

---

## Мотивация

### Текущие проблемы

1. **Дублирование кода** — auth-логика (`getAuthHeader`, 401 redirect), API fetch wrapper, theme переменные копипастятся в каждом из 8 HTML-файлов
2. **Масштабируемость** — wellness.html уже 895 строк inline; dashboard с 4 табами и calendar будут ещё сложнее
3. **Нет переиспользования** — canvas-гейджи, metric-карточки, tab-компоненты написаны заново в каждом файле
4. **Нет типизации** — vanilla JS без проверки типов, ошибки только в рантайме
5. **Dev experience** — нет HMR, нет линтинга, изменения через полный reload

### Что даёт React

- Компонентная архитектура — переиспользование Layout, MetricCard, Gauge, TabSwitcher
- TypeScript — типизация API ответов, пропсов, стейта
- Vite — мгновенный HMR, оптимизированный билд, tree-shaking
- React Router — SPA с client-side навигацией, без перезагрузки страниц
- Centralized auth — единый AuthProvider с useAuth() hook
- Centralized API — один apiClient с interceptors (401 → redirect)

---

## Технологический стек

| Компонент | Выбор | Обоснование |
|---|---|---|
| Framework | React 18 | Стабильный, hooks-based, огромная экосистема |
| Language | TypeScript | Типизация API, пропсов, стейта |
| Build | Vite 6 | Быстрый dev-сервер, оптимизированный билд |
| Routing | React Router v7 | SPA navigation, lazy loading |
| Styling | Tailwind CSS v3 | Уже используем CDN, теперь полноценно с JIT |
| Charts | Chart.js + react-chartjs-2 | Минимум изменений — Chart.js уже используется |
| State | React Context + hooks | Достаточно для текущей сложности, без Redux |
| HTTP | fetch (native) | Минимализм; обёртка в apiClient.ts |
| Linting | ESLint + Prettier | Стандарт для React/TS |

### Отвергнутые альтернативы

- **Next.js** — overkill, SSR не нужен для Mini App; усложняет деплой
- **Zustand/Redux** — избыточно; 8 страниц, нет shared mutable state между ними
- **Axios** — лишняя зависимость; native fetch + обёртка достаточно
- **Radix/shadcn** — стильно, но Telegram Mini App имеет свой дизайн; держим кастомные компоненты

---

## Структура проекта

```
webapp/                          # React SPA (Vite project root)
├── index.html                   # Vite entry point (single HTML)
├── package.json
├── tsconfig.json
├── vite.config.ts
├── tailwind.config.js
├── postcss.config.js
│
├── public/
│   └── favicon.ico
│
└── src/
    ├── main.tsx                 # React entry: createRoot + Router
    ├── App.tsx                  # Routes definition + AuthProvider
    ├── vite-env.d.ts
    │
    ├── api/
    │   ├── client.ts            # apiClient: base fetch wrapper + auth header + 401 handling
    │   └── types.ts             # TypeScript types for all API responses
    │
    ├── auth/
    │   ├── AuthProvider.tsx      # React Context: Telegram initData | JWT | anonymous
    │   ├── useAuth.ts           # hook: { role, isAuthenticated, authHeader, logout }
    │   └── telegram.ts          # Telegram WebApp SDK helpers
    │
    ├── components/
    │   ├── Layout.tsx           # Shared layout: header + back nav + theme
    │   ├── MetricCard.tsx       # Reusable metric display (value, label, delta, status color)
    │   ├── StatusBadge.tsx      # green/yellow/red status indicator
    │   ├── Gauge.tsx            # Canvas-based recovery/readiness gauge (extracted)
    │   ├── TabSwitcher.tsx      # Generic tab component (replaces manual tab logic)
    │   ├── WeekNav.tsx          # Week navigation (prev/next with boundary checks)
    │   ├── DayNav.tsx           # Day navigation (prev/next, no future)
    │   ├── WorkoutCard.tsx      # Collapsible workout card (plan page)
    │   ├── ActivityCard.tsx     # Expandable activity card (activities page)
    │   ├── ZoneChart.tsx        # Chart.js zone distribution (HR/power/pace)
    │   ├── LoadingSpinner.tsx   # Shared loading state
    │   └── ErrorMessage.tsx     # Shared error display
    │
    ├── pages/
    │   ├── Landing.tsx          # index.html → public landing page
    │   ├── Login.tsx            # login.html → one-time code form
    │   ├── Report.tsx           # report.html → morning report
    │   ├── Wellness.tsx         # wellness.html → daily wellness detail
    │   ├── Plan.tsx             # plan.html → scheduled workouts by week
    │   ├── Activities.tsx       # activities.html → completed activities by week
    │   ├── Activity.tsx         # activity.html → single activity detail
    │   └── Dashboard.tsx        # dashboard.html → tabbed dashboard (Today/Load/Goal/Calendar)
    │
    ├── hooks/
    │   ├── useApi.ts            # Generic data fetching hook with loading/error states
    │   ├── useWeekNav.ts        # Week offset + boundary logic (shared by Plan, Activities)
    │   └── useDayNav.ts         # Date navigation logic (shared by Wellness)
    │
    └── styles/
        ├── index.css            # Tailwind directives + CSS custom properties (--tg-theme-*)
        └── gauge.css            # Canvas gauge specific styles (if any)
```

---

## Маршрутизация (React Router)

```tsx
<Routes>
  {/* Public */}
  <Route path="/" element={<Landing />} />
  <Route path="/login" element={<Login />} />

  {/* Authenticated */}
  <Route path="/report" element={<Report />} />
  <Route path="/wellness" element={<Wellness />} />
  <Route path="/plan" element={<Plan />} />
  <Route path="/activities" element={<Activities />} />
  <Route path="/activity/:id" element={<Activity />} />
  <Route path="/dashboard" element={<Dashboard />} />

  {/* Fallback */}
  <Route path="*" element={<Navigate to="/" />} />
</Routes>
```

**Изменение URL:** activity.html?id=xxx → /activity/:id (чистые URL, React Router param).
Остальные пути сохраняются: /report, /wellness, /plan, /activities.
Landing: / (вместо /index.html).

---

## Миграция по страницам

### Порядок миграции (от простого к сложному)

| # | Страница | Строк | Сложность | Зависимости | Приоритет |
|---|---|---|---|---|---|
| 0 | Scaffolding | — | — | Vite + Router + Auth + API client | Первый |
| 1 | Login | 248 | Низкая | AuthProvider | Второй (нужен для тестирования auth) |
| 2 | Landing | 460 | Низкая | Layout, AuthProvider | Третий |
| 3 | Report | 765 | Средняя | Gauge, MetricCard, TabSwitcher | Четвёртый |
| 4 | Plan | 529 | Средняя | WeekNav, WorkoutCard | Пятый |
| 5 | Activities | 546 | Средняя | WeekNav, ActivityCard | Шестой |
| 6 | Activity | 513 | Средняя | ZoneChart (Chart.js) | Седьмой |
| 7 | Wellness | 895 | Высокая | DayNav, TabSwitcher, MetricCard, Gauge | Восьмой |
| 8 | Dashboard | 138+ | Высокая | Все компоненты + новые API endpoints | Последний |

### Детализация шагов

**Шаг 0 — Scaffolding (фундамент)**
- `npm create vite@latest webapp -- --template react-ts`
- Установка: react-router-dom, chart.js, react-chartjs-2, tailwindcss, postcss, autoprefixer
- Настройка: vite.config.ts (proxy /api → localhost:8000), tailwind.config.js
- Создание: AuthProvider, apiClient, Layout, базовые типы
- Telegram SDK интеграция: `telegram.ts` helper
- CSS: перенос --tg-theme-* переменных и dark theme fallbacks

**Шаг 1 — Login**
- Простая форма с 6-digit input
- POST /api/auth/verify-code
- Сохранение JWT в AuthProvider (localStorage)
- Redirect на / после успеха

**Шаг 2 — Landing**
- Статическая страница (hero, features, tech stack)
- Условный рендер кнопок для auth/anonymous пользователей
- Кнопка "Войти" / "Выйти" через useAuth()

**Шаг 3 — Report**
- Извлечение Gauge в отдельный компонент (canvas ref)
- MetricCard для HRV/RHR/Sleep/Load блоков
- TabSwitcher для Claude/Gemini AI рекомендаций
- Fetch /api/report через useApi hook

**Шаг 4-5 — Plan + Activities**
- Общий WeekNav hook (offset, hasPrev, hasNext)
- WorkoutCard / ActivityCard — collapsible компоненты
- Sync button → POST /api/jobs/*

**Шаг 6 — Activity**
- react-chartjs-2 для zone charts
- URL param через useParams() вместо query string
- Intervals table как React компонент

**Шаг 7 — Wellness**
- Самая сложная страница — DayNav + двойные табы (HRV algorithms + AI)
- Переиспользование MetricCard, Gauge, TabSwitcher из Report
- Это главная проверка переиспользования компонентов

**Шаг 8 — Dashboard**
- Табы: Today, Load, Goal, Calendar
- Lazy loading per tab (React.lazy + Suspense)
- Новые API endpoints (из CLAUDE.md секция Web Dashboard)
- Chart.js графики через react-chartjs-2

---

## Telegram Mini App интеграция

### SDK

```tsx
// src/auth/telegram.ts
declare global {
  interface Window {
    Telegram?: { WebApp: TelegramWebApp };
  }
}

export function getTelegramWebApp(): TelegramWebApp | null {
  return window.Telegram?.WebApp ?? null;
}

export function getInitData(): string | null {
  return getTelegramWebApp()?.initData || null;
}
```

SDK загружается через `<script>` в index.html (до React bundle), как сейчас.

### Тема

CSS-переменные `--tg-theme-*` продолжают работать через Tailwind CSS custom properties:

```css
/* src/styles/index.css */
:root {
  --bg: var(--tg-theme-bg-color, #0f172a);
  --text: var(--tg-theme-text-color, #e2e8f0);
  --accent: var(--tg-theme-button-color, #3b82f6);
  /* ... */
}
```

### Lifecycle

```tsx
// App.tsx
useEffect(() => {
  const tg = getTelegramWebApp();
  if (tg) {
    tg.ready();
    tg.expand();
  }
}, []);
```

---

## Backend изменения

### FastAPI — Serve SPA

```python
# api/server.py — изменение
# Было: StaticFiles(directory=webapp_path, html=True)
# Стало: serve built SPA + fallback to index.html

@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """Serve React SPA — any non-API route returns index.html"""
    file_path = webapp_dist / full_path
    if file_path.is_file():
        return FileResponse(file_path)
    return FileResponse(webapp_dist / "index.html")
```

SPA fallback нужен для React Router — все пути (/report, /activity/i123) должны возвращать index.html.

### Docker — Multi-stage build

```dockerfile
# Stage 1: Build React
FROM node:20-alpine AS frontend
WORKDIR /app/webapp
COPY webapp/package*.json ./
RUN npm ci
COPY webapp/ ./
RUN npm run build

# Stage 2: Python app
FROM python:3.12-slim
# ... existing Python setup ...
COPY --from=frontend /app/webapp/dist /app/webapp/dist
```

Финальный образ не содержит Node.js — только собранные статические файлы.

### CORS

Для dev-сервера Vite (localhost:5173) нужно добавить в CORS origins:

```python
allow_origins: [WEBAPP_URL, "http://localhost:5173"]  # dev Vite
```

---

## Dev Workflow

### Локальная разработка

```bash
# Terminal 1: Backend
docker compose up -d db
poetry run uvicorn api.server:app --reload --port 8000

# Terminal 2: Frontend
cd webapp
npm run dev    # Vite dev server on :5173, proxies /api → :8000
```

### Vite proxy config

```ts
// vite.config.ts
export default defineConfig({
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/telegram': 'http://localhost:8000',
      '/mcp': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    }
  }
});
```

### Production build

```bash
cd webapp && npm run build    # → webapp/dist/
# FastAPI serves webapp/dist/ as static files
```

---

## Обратная совместимость

### URL-совместимость

| Старый URL | Новый URL | Изменение |
|---|---|---|
| /index.html | / | React Router (landing) |
| /login.html | /login | React Router |
| /report.html | /report | React Router |
| /wellness.html | /wellness | React Router |
| /plan.html | /plan | React Router |
| /activities.html | /activities | React Router |
| /activity.html?id=xxx | /activity/:id | Параметр в URL path |
| /dashboard.html | /dashboard | React Router |

Telegram Mini App WebAppInfo URL нужно обновить (убрать .html).

### API контракт

Все существующие API endpoints остаются **без изменений**. React фронтенд потребляет те же JSON-ответы.

---

## Оценка трудозатрат

| Шаг | Оценка | Заметки |
|---|---|---|
| Scaffolding + Auth + API | 2-3 часа | Фундамент: Vite, Router, AuthProvider, apiClient, типы |
| Login + Landing | 1-2 часа | Простые страницы |
| Report | 2-3 часа | Gauge компонент, MetricCard, TabSwitcher |
| Plan + Activities | 2-3 часа | WeekNav hook, collapsible cards |
| Activity | 1-2 часа | Chart.js интеграция через react-chartjs-2 |
| Wellness | 2-3 часа | Самая сложная страница, проверка переиспользования |
| Dashboard | 2-3 часа | Зависит от готовности API endpoints |
| Docker + Deploy | 1 час | Multi-stage build, nginx config |
| Тестирование + полировка | 2-3 часа | Telegram Mini App, auth flows, responsive |
| **Итого** | **~15-22 часа** | ~2-3 рабочих дня |

---

## Риски и митигация

| Риск | Вероятность | Митигация |
|---|---|---|
| Telegram SDK не работает в SPA | Низкая | SDK загружается в index.html до React; проверено в Mini App docs |
| Canvas gauge не рендерится в React | Низкая | useRef + useEffect pattern; хорошо документировано |
| Vite proxy не проксирует WebSocket (MCP) | Средняя | Настроить ws: true в proxy config |
| Увеличение bundle size | Низкая | Tree-shaking + lazy loading; React+Router ~45KB gzip |
| Сломанные ссылки из Telegram | Средняя | Nginx redirect .html → чистые URL (переходный период) |

---

## Критерии готовности

- [x] Все 8 страниц работают идентично текущим
- [x] Auth работает: Telegram initData + Desktop JWT + anonymous landing
- [x] Telegram Mini App: theme переменные, expand(), ready()
- [x] Chart.js графики рендерятся корректно
- [x] Canvas gauges работают
- [x] Docker multi-stage build проходит
- [x] Нет регрессий в API (backend не менялся)
- [x] Responsive на мобильных (360px+)
