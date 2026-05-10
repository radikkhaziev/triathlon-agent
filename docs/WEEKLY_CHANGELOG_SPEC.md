# Weekly Changelog Spec

> Еженедельная авто-сводка «что нового» — собирается из merged PR'ов в `main` за неделю, переводится Claude'ом в athlete-friendly текст, публикуется как GitHub Discussion в категории `Announcements`. Webapp показывает ссылку «📝 What's new» в sidebar — атлет открывает когда сам захочет, никаких push-нотификаций. POC сделан 2026-05-09 → Discussion #334.

**Status:** delivered 2026-05-10 (Phase 1: PR1 + PR2 + idempotency). Phase 2 deferred until first EN athlete arrives.

**Related:**

| Ссылка | Связь |
|---|---|
| `tasks/actors/changelog.py` | Dramatiq actor `actor_publish_weekly_changelog` |
| `api/routers/changelog.py` | REST `GET /api/changelog/latest` |
| `bot/scheduler.py:scheduler_publish_weekly_changelog_job` | Sun 15:00 Belgrade cron |
| `webapp/src/components/Sidebar.tsx` | Unread-badge link |
| `mcp_server/tools/github.py:create_github_issue` | Reference — паттерн использования `GITHUB_TOKEN` |

---

## 1. Мотивация

Атлет (и owner) хочет понимать «что в боте/web появилось нового» без постоянного дёрганья разработчика. Альтернативы:

| Подход | Почему отвергли |
|---|---|
| Push в Telegram при каждом релизе | Конкурирует с morning/evening/weekly reports → notification fatigue |
| Push сводки в Telegram раз в неделю | Та же проблема + риск пустых недель = noise |
| Page в webapp с manual changelog | Дисциплина «писать руками каждый раз» — забывается через 2 недели |

**Pull-модель** через GitHub Discussion + sidebar link:
- Атлет сам решает когда смотреть.
- GitHub-native: RSS feed, comments, reactions, search.
- Источник = merged PRs — мы и так пишем PR descriptions для ревью, переиспользуем.
- Owner получает побочный эффект: видит публичный список своих изменений за неделю → дисциплинирует scope creep.

---

## 2. Scope

### Phase 1 (MVP)

- Dramatiq actor собирает merged PR'ы за последние 7 дней.
- Pre-filter: dependabot, conventional-commit prefixes (`chore:`, `docs:`, `refactor:`, `test:`).
- Claude переписывает в 3-7 буллетов на русском, группирует по темам, возвращает `NO_USER_FACING_CHANGES` если всё internal.
- Публикация в GitHub Discussion (`Announcements` category) через GraphQL `createDiscussion`.
- APScheduler cron — Воскресенье 15:00 Belgrade (за 4 часа до `scheduler_weekly_report_job` в 19:00 — даёт буфер на ручную проверку Discussion'а перед тем как weekly report пойдёт атлетам).
- REST endpoint `GET /api/changelog/latest` — 1ч кэш, дёргает GraphQL `discussions(first:1, categoryId)`.
- Sidebar link «📝 What's new» в webapp → открывает Discussion в новой вкладке.

### Phase 2 (если попросят)

- Локализация — `?lang=ru|en` параметр в endpoint, два Discussion'а на разных языках.
- Email digest для opt-in athletes (`User.email_digest_optin`).
- Дёргать changelog по дате релиза (если делать релизы), а не строго по неделе.
- Markdown на русском в UI inline (не open-in-new-tab) — нужен GitHub Markdown→HTML рендерер на фронте.

### Вне scope

- Push в Telegram (явно отвергнуто, см. §1).
- Per-user changelog (что нового **для конкретного атлета**) — другая задача (требует attribution PR'ов к user-stories).
- Auto-detect breaking changes / migration warnings.
- Multi-repo агрегация.

---

## 3. Data flow

Sun 15:00 Belgrade cron → `actor_publish_weekly_changelog.send()` (no-arg fan-out, не per-user) → 8 шагов:

1. **Idempotency lookup** — `fetch_latest_discussion()`. Если последний Discussion ≤ 7d 12h → `skipped_already_published` (см. §13).
2. **Fetch merged PRs** — REST `pulls?state=closed&base=main`, paginated, stop когда `updated_at < since`.
3. **Pre-filter** (cheap, без Claude) — см. §4.
4. **Empty?** → log + return (`skipped_no_prs` / `skipped_all_filtered`).
5. **Build Claude prompt** — title + URL + `body[:1500]` per PR, top-50 cap (см. §5).
6. **Call Claude** (`claude-sonnet-4-6`); если sentinel `NO_USER_FACING_CHANGES` → log + return.
7. **Wrap output** в Discussion body (см. §6) → GraphQL `createDiscussion`.
8. **Log success** с URL + number; `skipped_error` + Sentry на любой ошибке.

Реализация: `tasks/actors/changelog.py:publish_weekly_changelog`. Никогда не raises — fail-soft на каждой ветке.

---

## 4. Pre-filter rules

Запускается ДО Claude — экономит токены на очевидно internal PR'ах.

### Drop by author

```python
SKIP_AUTHORS = {"dependabot[bot]", "github-actions[bot]", "renovate[bot]"}
```

### Drop by conventional-commit prefix

PR title матчится regex:
```python
INTERNAL_TITLE_RE = re.compile(r"^(chore|ci|build|test|docs):", re.IGNORECASE)
```

`perf|style|refactor` намеренно НЕ в hard-drop:
- `perf:` обычно user-facing («дашборд грузится в 3× быстрее»).
- `style:` в нашем repo чаще про UI Tailwind, чем про lint.
- `refactor:` иногда меняет UX (онбординг flow, например).

Пропускаем их в Claude — правило промпта «только то что атлет заметит» (§5) отсеет если internal. +5-7k input tokens worst-case (~$0.02/неделю).

### Drop by label

PR с любым из лейблов `skip-changelog`, `internal`, `dependencies` пропускается.

### Dedup

Ключ dedup: `(title.lower().strip(), sha1(body[:200])[:8])` — оставить newest по `merged_at`.

**Почему не title-only:** stacked PRs могут иметь одинаковый title но разные bodies (фиксы к одной фиче). Title-only dedup потерял бы контекст. Title+body[:200]-hash коллапсит только реальные дубли.

POC observation: PR #318 / #320 были байт-идентичны по title И body (re-merge артефакт после force-push на ветке `versions/multi-tenant`) → ловятся как раньше.

### Branch filter

Только `pr.base.ref == "main"` — игнорируем merge'ы в feature branches (бывает при stacked PRs).

---

## 5. Claude prompt

Source of truth: `tasks/actors/changelog.py:PROMPT_TEMPLATE`. TL;DR rules:

- 3-7 буллетов на русском, активный залог («Теперь можно X»).
- Группировка по темам с emoji-заголовками (🎯 Цели, 🧪 Тесты, 📊 Отчёты, 🔌 Onboarding, 🐛 Багфиксы).
- НЕ упоминать PR-номера, файлы, классы, миграции.
- Если все PR'ы internal → ровно строка `NO_USER_FACING_CHANGES` (sentinel, actor пропускает публикацию).
- Без введения/заключения.

**Параметры:** `claude-sonnet-4-6`, `max_tokens=800`, `temperature=0.3`, system пустой.

**Body truncation rationale (deviation from initial 500-char limit):** обрезаем `body` каждого PR до 1500 chars + суффикс `"... [truncated]"`. Наши PR descriptions в среднем 800-1500 chars («What was done / How to verify»), 500 резали бы именно «How to verify» — ту часть откуда Claude видит user impact. **Top-50** PR'ов по `merged_at desc` (cap в `MAX_PRS_FOR_CLAUDE`) — операционный лог если cap сработал. Worst case с учётом cap: 50×1500 ≈ 18.75k input tokens, ~$0.06/прогон по sonnet rates.

---

## 6. Output format

Discussion title:
```
✨ Что нового — неделя 03–09 мая 2026
```

Format: `неделя DD–DD MMM YYYY` где DD — числа (понедельник недели и воскресенье публикации), MMM — короткое название месяца на русском (`мая`, `июня`).

Discussion body — wrapper вокруг Claude output:

```markdown
> Сводка изменений за неделю. Сгенерирована автоматически из merged PR'ов
> в `main` (пропущены рефакторинги, миграции, обновления зависимостей).

{claude_output}

---

[Полный список merged PR'ов за неделю →](https://github.com/{owner}/{repo}/pulls?q=is%3Apr+merged%3A%3E%3D{since}+base%3Amain)
```

---

## 7. Persistence

**Где:** GitHub Discussion в категории `Announcements`.

**Почему Discussion, не Issue:**
- Семантика: Issue = баг/задача; Discussion = объявление.
- Categories: `Announcements` уже есть в дефолтном наборе при включении Discussions.
- RSS feed (https://github.com/.../discussions.atom) — атлет может подписаться через RSS-reader.
- Comments/reactions — атлет может оставить feedback на конкретный буллет.
- Не засоряет Issue tracker с product backlog'ом.

**Почему не Release:**
- Releases требуют semver tag — overkill для weekly digest.
- Не делаем releases как практику.

**Почему не Markdown файл в repo:**
- Каждая публикация = commit. Шумит git log.
- Нет stable-URL «latest».
- Нет comments/reactions.

### Cached IDs

Repo ID и Category ID константны → хранить в env vars (не дёргать GraphQL `repository.id` каждый раз):

```
CHANGELOG_REPO_ID=R_kgDORnuZCQ
CHANGELOG_DISCUSSION_CATEGORY_ID=DIC_kwDORnuZCc4C8reQ
```

Получаются один раз через:
```bash
gh api graphql -f query='query { repository(owner:"X", name:"Y") { id discussionCategories(first:20) { nodes { id name } } } }'
```

---

## 8. APScheduler job

`bot/scheduler.py:scheduler_publish_weekly_changelog_job` — async, без `@with_athletes` (нет per-user fan-out, просто `actor_publish_weekly_changelog.send()`).

Cron config: `day_of_week="sun", hour=15, minute=0, timezone=settings.TIMEZONE, misfire_grace_time=7200, coalesce=True, max_retries=0` (на actor).

**Почему Sun 15:00, а не 19:30 после weekly:** 4-часовой буфер до weekly report (Sun 19:00) — даёт окно проверить Discussion глазами и поправить вручную если Claude выдал ересь, до того как weekly report уйдёт атлетам.

---

## 9. REST endpoint

`api/routers/changelog.py:get_latest_changelog` — `GET /api/changelog/latest`.

| Параметр | Значение |
|---|---|
| Auth | `require_viewer` (демо тоже читает) |
| Response | `{url, title, published_at}` или 404 если Discussion'ов ещё нет |
| Cache | 1h in-process для **обоих** 200 и 404 (fresh repo не должен дёргать GitHub на каждом page load) |
| Single-flight | `asyncio.Lock` против thundering herd на cache miss — concurrent requests на TTL boundary делят один upstream fetch |
| Failure | 503 с `Retry-After: 300` через `HTTPException(headers=...)` (не через `Response.headers` — FastAPI сбрасывает их при подмене body на error JSON) |

**GraphQL query:** `data/github.py:LATEST_DISCUSSION_QUERY` — shared с actor'ом (idempotency lookup), single source of truth.

**Token leak guard:** `GITHUB_TOKEN` идёт только в outbound `Authorization` header, никогда не появляется в response.

---

## 10. Webapp integration

### Unread-only link, dual viewport placement

Постоянная эмодзи-ссылка для атлета, который changelogs не читает = visual debt. Рендерим **только** если `cl.url !== localStorage["changelog.last_seen_url"]`. Клик → write localStorage + `setUnread(false)` → ссылка исчезает в этой же сессии.

**Где показывается** — в обоих viewport'ах, ровно после строки «📋 План»:

| Где | Файл | Когда виден |
|---|---|---|
| Desktop sidebar (≥768px) | `webapp/src/components/Sidebar.tsx` | После `/plan` в основном nav |
| Mobile More-меню (<768px / Telegram Mini App) | `webapp/src/components/BottomTabs.tsx` | После `/plan` в выпадающем More-меню |
| Mobile More-кнопка (свёрнуто) | `BottomTabs.tsx` | Маленькая `●` точка-индикатор поверх ⚙️ когда unread + меню закрыто |

### Singleton hook

`webapp/src/hooks/useChangelog.ts` — `{changelog, unread, markRead}`. Module-level singleton Promise (`_inFlight`) гарантирует один fetch на сессию: Sidebar и BottomTabs делят одну подписку. Без singleton'а получили бы `2× /api/changelog/latest` на каждом page-mount + рассинхрон localStorage между двумя `useEffect`. Auth-gated через `useAuth().isAuthenticated` — без gate центральный `apiFetch` 401-handler редиректил бы unauthenticated `/login`-страницу обратно на `/login`, ломая login flow.

Failure recovery: `_inFlight = null` сбрасывается при `.catch()`, чтобы один transient 503 не залипал хук до full-page reload (M3 fix).

### Storage key — URL, не timestamp

`localStorage["changelog.last_seen_url"]` хранит URL последнего просмотренного Discussion'а. Преимущества vs timestamp: устойчиво к смене URL Discussion'а; не зависит от часов клиента. Edge case: cleared localStorage → один раз покажется «непрочитан» актуальный changelog → атлет кликнет → запишется. Acceptable.

`setItem` обёрнут в `try/catch` — Safari private mode и переполненный quota бросают `QuotaExceededError`; глотаем (unread-state можно потерять, но page не должна падать из-за storage failure).

### i18n / types

`webapp/src/i18n/{ru,en}.json` — ключи `sidebar.whats_new` («Что нового» / «What's new») и `sidebar.unread` («не прочитано» / «unread») для `aria-label`.

`webapp/src/api/types.ts:ChangelogLatest` — `{url, title, published_at}` (ISO).

---

## 11. Multi-tenant

Single Discussion per week — все атлеты видят одну и ту же ссылку. **НЕ** per-user changelog (другая задача, требует attribution PR'ов).

Auth для endpoint — `require_viewer` (демо тоже видит).

Permissions для actor — использует `GITHUB_TOKEN` от owner'а; PR list (public repo) и Discussion creation выполняются от owner'а независимо от того, кто триггернул запрос. Атлет в endpoint только читает результат.

### Локализация

**Phase 1 (MVP, default):** Single Russian Discussion. Все active athletes на момент написания спеки — русскоязычные. EN demo / future EN athletes увидят русский текст; webapp метка `What's new` остаётся на их языке через `react-i18next`. Не делаем сложности под пока-несуществующих юзеров.

**Phase 2 (когда придёт первый EN athlete):** Single bilingual Discussion. Один Claude call с двойной инструкцией:

```
... (existing rules) ...
- Produce TWO versions in the same response: Russian first, then English.
- Separate them by the literal marker on its own line:
  <!--LANG-SEPARATOR-->
- English version follows the same rules (3-7 bullets, theme grouping, etc.).
- If both versions would be NO_USER_FACING_CHANGES, return NO_USER_FACING_CHANGES once.
```

Body wrapper становится:
```markdown
> Сводка ... | Weekly summary ...

## 🇷🇺 Русский
{claude_ru_section}

<!--LANG-SEPARATOR-->

## 🇬🇧 English
{claude_en_section}

---

[Полный список / Full list →](https://...)
```

Endpoint остаётся один (`GET /api/changelog/latest` → один URL). Webapp при `User.language == "en"` добавляет якорь:
```typescript
const target = user.language === "en" ? `${changelog.url}#english` : changelog.url
```

GitHub auto-генерит anchor `#english` из `## 🇬🇧 English` (slug = lowercase + alphanumeric). Атлет landing-ит сразу на свою секцию; может скроллить вверх к чужой если хочет.

**Почему НЕ two Discussions per week (отвергнуто):** ×2 Claude call cost, ×2 cache keys, нужно различать ru/en Discussion в GraphQL query (метки или title-suffix), endpoint dispatch по `?lang=`. Не оправдано экономией скролла.

---

## 12. Cost

| Item | Estimate |
|---|---|
| Anthropic Claude (sonnet-4-6) | до ~18.75k in + ~600 out tokens × 1 call/week ≈ $0.06/неделю (worst case top-50 × 1500 chars). Реалистично 30-40 PR/неделю → ~$0.04/неделю |
| GitHub API calls | 1 REST + 1 GraphQL/неделю — free tier (5000/час для authenticated) |
| Storage | 0 (Discussions хранятся у GitHub) |
| Total | < $6/год worst case, ~$2/год realistic |

Включить в `ApiUsageDaily.increment` через sentinel user_id (или просто owner — POC показал что это owner-driven feature).

---

## 13. Edge cases

| Случай | Поведение |
|---|---|
| 0 merged PRs за неделю | Skip publish, log info |
| Все PR'ы отсеяны pre-filter'ом | Skip publish, log info |
| Claude вернул `NO_USER_FACING_CHANGES` | Skip publish, log info |
| Claude API down | Try-except, retry on next week's run, no fallback |
| GitHub API down при fetch PRs | Try-except, retry next week, no Discussion published |
| GitHub API down при createDiscussion | Try-except, log error to Sentry, retry next week |
| Дубликаты PR title (same week) | Dedup, оставить newest |
| PR с пустым body | Use title only — Claude разберётся |
| >100 PRs за неделю | Top-50 по `merged_at desc` |
| Webapp: cache miss + GitHub down | 503, sidebar link скрыт |
| Cron misfired | `misfire_grace_time=7200` (2h grace), `coalesce=True` (no double-publish) |
| Manual `publish-changelog` Wed + Sun cron | Idempotency-by-week: actor перед публикацией дёргает `fetch_latest_discussion`; если последний Discussion ≤ **7d 12h** назад → `skipped_already_published`. Padding 12h в past — против late-jitter cron'а: фир Sun 15:00 + N секунд иначе позволил бы Discussion'у возрастом ровно 7d выпасть из окна и продублироваться. CLI `--force` обходит для редких «второй digest за неделю». Cron всегда без force |
| `fetch_latest_discussion` упал (transient GraphQL 5xx) | Best-effort guard — логируем warning, продолжаем публикацию. Худший случай: дубль за неделю (поправляется руками через `gh`) |
| Discussion создан с дубликатом title | GitHub разрешает дубли — не блокируем; следующая неделя перезапишет в кэше |

---

## 14. Tests

### `tests/tasks/test_weekly_changelog.py`

- `test_skips_when_no_merged_prs` — патчит GitHub fetch → empty list → no Discussion call
- `test_skips_when_all_prs_filtered_out_by_prefilter` — список из 3 dependabot PR'ов
- `test_skips_when_claude_returns_no_user_facing_changes` — Claude mock возвращает sentinel
- `test_publishes_discussion_with_correct_shape` — happy path, проверяет title format + body wrapper
- `test_dedup_same_title_keeps_newest` — два PR одного title, один остаётся
- `test_drops_pr_titles_with_conventional_commit_prefix` — `chore:` / `docs:` пропускаются
- `test_drops_dependabot_authors` — pr.user.login `dependabot[bot]` пропускается
- `test_handles_github_api_failure_gracefully` — httpx raises → log, no exception propagates

### `tests/api/test_changelog_routes.py`

- `test_returns_latest_discussion_shape` — patch GraphQL → `{url, title, published_at}`
- `test_returns_404_when_no_discussions_yet` — empty `nodes[]` → 404
- `test_caches_response_for_one_hour` — second call hits cache, no second GraphQL
- `test_returns_503_when_github_unreachable` — GraphQL raises → 503 + Retry-After
- `test_demo_viewer_can_read` — auth via require_viewer → 200

---

## 15. Open issues

### 15.2 Per-PR feedback loop

GitHub Discussion имеет comments. Если атлет напишет «эта фича сломала X» — никто не получит уведомление (subscribers не настроены). Можно настроить email-notification для owner'а в repo settings — vanilla GitHub feature, не требует кода.

### 15.3 Опциональные группировки

Сейчас prompt просит группировать по темам — но темы выбирает Claude свободно. Возможно стоит дать список разрешённых emoji-секций для consistency:
```
🎯 Цели и план | 🧪 Тесты и зоны | 📊 Отчёты | 💪 Тренировки |
🔌 Onboarding | 🐛 Багфиксы | 🌐 Webapp | 🤖 Чат с ботом
```

Решить после 3-4 реальных публикаций — пусть Claude покажет какие темы естественно возникают.

### 15.4 PR description quality

Spec предполагает что PR descriptions осмысленные. Если PR merged с empty body или `"misc fixes"` — Claude получит мусор. Можно (а) обязать в `CONTRIBUTING.md` или PR template писать body, (б) принять качественную деградацию.

### 15.5 Dedup across weeks

Если PR смержен в субботу одной недели и в воскресенье попадает в обе weekly раундirovки (`merged:>=last_sunday` overlap) — может задвоиться. Использовать exclusive bound `merged:>last_sunday AND <=this_sunday`.

---

## 16. Migration path (rollout)

1. **PR1 — backend actor + GitHub integration** ✅ commit 2026-05-10
   - `tasks/actors/changelog.py` actor (с weekly idempotency через `fetch_latest_discussion`)
   - `bot/scheduler.py` cron job (Sun 15:00 Belgrade)
   - Env vars: `CHANGELOG_REPO_ID`, `CHANGELOG_DISCUSSION_CATEGORY_ID` (opt-in: empty defaults; prod values copied into `.env` from `.env.example`)
   - Tests
   - Manual trigger: `python -m cli publish-changelog` (idempotent), `--force` для override

2. **PR2 — REST endpoint + webapp link** ✅ commit 2026-05-10
   - `api/routers/changelog.py` — `GET /api/changelog/latest`, 1h in-process cache (404 кэшируется тоже), 503 + `Retry-After: 300` на upstream-фейлах
   - `webapp/src/components/Sidebar.tsx` — unread-badge через `localStorage["changelog.last_seen_url"]` (§10 deviation)
   - i18n: `sidebar.whats_new` ru/en
   - Tests: 8 кейсов

3. **Run for 4 weeks**, observe quality of Claude output. Tune prompt if needed.

4. **Phase 2 если попросят** — per-language, email digest, etc.

---

## 17. Decisions log

| Date | § | Decision | Why |
|---|---|---|---|
| 2026-05-09 | §1 | Pull-модель (Discussion + sidebar), не push в Telegram | Notification fatigue, морning/evening/weekly уже есть |
| 2026-05-09 | §3 | Источник = merged PRs, не git log | PR titles+bodies написаны для людей |
| 2026-05-09 | §7 | GitHub Discussion (Announcements), не Issue/Release/Markdown | Семантически правильное место + RSS + comments |
| 2026-05-09 | §5 | sonnet-4-6 (не Opus) для prompt | Задача простая (формат + фильтр), sonnet справится дешевле |
| 2026-05-09 | §11 | Single shared Discussion per week, не per-user | Per-user requires PR↔user-story attribution — отдельная задача |
| 2026-05-09 | §10 | Sidebar link, не inline-render Discussion | Inline требует GitHub Markdown→HTML на фронте — overkill |
| 2026-05-09 | §8 | Cron Sun 15:00 (не 19:30 после weekly) | Buffer 4h до weekly report — успеть глазами проверить Discussion / поправить вручную если что |
| 2026-05-09 | §11 | Phase 1 = Russian-only, Phase 2 = bilingual single Discussion (не two-Discussions) | Two-Discussions добавляет ×2 cost + dispatch в endpoint ради экономии скролла; bilingual single — простая эволюция, активируется когда EN athlete действительно появится |
| 2026-05-09 | POC | Discussion #334 создан вручную через `gh api graphql` | Проверили flow end-to-end до написания spec'и |
| 2026-05-10 | §4 | Hard-drop regex: только `chore\|ci\|build\|test\|docs`, без `perf\|style\|refactor` | `perf:` («дашборд в 3× быстрее») — user-facing; `style:` чаще про UI; `refactor:` иногда меняет UX. Trust Claude'у отсеять (+~$0.02/неделю) |
| 2026-05-10 | §4 | Dedup ключ: `(title, sha1(body[:200])[:8])` вместо title-only | Stacked PRs могут иметь одинаковый title но разные bodies. POC observation: #318/#320 идентичны и по title, и по body (re-merge артефакт) — оба варианта dedup'а ловят их |
| 2026-05-10 | §5 | Body truncation: 500 → 1500 chars + `"... [truncated]"` суффикс | Наши PR bodies 800-1500 chars; 500 резали именно «How to verify» — ту часть откуда Claude видит user impact |
| 2026-05-10 | §10 | Sidebar link — unread-only через localStorage, не permanent | Постоянная эмодзи-ссылка для not-readers = visual debt; ~10 строк добавляет unread-state |
| 2026-05-10 | §13 | Weekly idempotency: actor перед публикацией смотрит latest Discussion; ≤ 7 дней → skip | Manual `publish-changelog` Wed не должен ломать Sun cron. CLI `--force` для override; cron всегда без force |
| 2026-05-10 | §9 | Endpoint кэширует и 200, и 404 на 1h | Fresh repo с нулём Discussion'ов иначе бил бы GitHub на каждом page load до первой публикации |
| 2026-05-10 | §9 | `Retry-After: 300` отдаётся через `HTTPException(headers=...)`, не через `Response` | FastAPI заменяет body на error JSON и сбрасывает `response.headers`; правильный путь — на самом исключении |
| 2026-05-10 | §16 | Env defaults opt-in (пустые в `config.py`, prod values в `.env.example`) | Fork с `GITHUB_TOKEN` иначе случайно бы лез в upstream Announcements; explicit copy-on-deploy безопаснее (Copilot review #335) |
| 2026-05-10 | §9 | `asyncio.Lock` single-flight вокруг GraphQL refresh | Под нагрузкой N parallel page-load'ов били GitHub N раз вместо 1; lock коллапсирует thundering herd на cache miss (Copilot review #335) |
| 2026-05-10 | §9 | `LATEST_DISCUSSION_QUERY` вынесен в `data/github.py` | Actor (idempotency check) и REST endpoint используют тот же query — single source of truth, не дрейфит. Плюс API-процесс не тянет `dramatiq`/`anthropic` ради импорта строки (Copilot review #335) |
| 2026-05-10 | §10 | Dual placement: Sidebar + BottomTabs More-menu, обе после `/plan` | Mobile-first атлеты живут в bottom-tabs; sidebar-only был desktop-bias и невидим в Telegram Mini App. Indicator dot на More-button — unread сигнал когда меню закрыто |
| 2026-05-10 | §10 | `useChangelog` singleton hook | Sidebar и BottomTabs обa читают changelog → без хука = двойной fetch + рассинхрон localStorage между mount'ами. Module-level `_inFlight` Promise + reset на `.catch()` (M3) для retry после transient 503 |

---

## 18. POC artifacts (2026-05-09)

Manual POC produced 5 emoji-sections + 8 bullets from 23 merged PRs (~10 post-filter); ~1500 char output. POC Discussion #334 deleted 2026-05-10 as part of deploy prep so the first cron run wouldn't be blocked by the idempotency check. Repo ID + Category ID resolved during the POC live in `.env.example` (copied into prod `.env`); `config.py` defaults are empty (opt-in, see §16).
