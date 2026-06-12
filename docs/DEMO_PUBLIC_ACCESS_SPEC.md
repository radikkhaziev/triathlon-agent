# Public Demo Access — Spec

> Open the demo to everyone, on **live** owner data, with sensitive data scrubbed.
> AI free-text is **not served** to demo — the frontend shows a curated English
> sample instead. The owner keeps the same data in Russian for personal use.

Status: ✅ Phase 1-3 implemented 2026-06-12 (is_demo helper + AI-text stub + frontend samples + passwordless mint with 24h TTL).

---

## 1. Goal

Make the demo a public, always-open, read-only window into the owner's **live** training
data:

- **Live** — "today shows today". No snapshot/clone; demo reads the owner's real row, same
  as it does now.
- **Sanitized** — no PII / sensitive fields leak (name, chat_id, Telegram handle, tokens),
  and no AI free-text (the PII-bearing surface) reaches a demo session at all.
- **Sample, not translation** — wherever the real app shows AI-generated text (coach
  recommendation, race plan), demo renders a hand-written English **sample** with a
  "Sample" badge, so the product's form is visible without any live content.
- **Open to all** — reachable without a private password (decision in Phase 3).

The owner continues to consume the *same* data in Russian (personal `language='ru'`).

---

## 2. Decisions log

- **2026-06-12 — dual-write EN storage REJECTED, placeholder + frontend sample chosen.**
  The earlier draft proposed writing every AI free-text field twice (ru + en translate
  pass) and serving `*_en` to demo. Killed because:
  1. **PII lives inside the AI text itself.** Morning report / race plan are generated
     *from* mood check-ins, IQOS counts and `user_facts` — the Russian text routinely
     interpolates intimate content ("вчера 12 стиков, тревожность 4/5, учитывая травму…").
     A faithful translation pass carries the leak into English verbatim. Scrubbing
     structured fields does nothing about it. Not serving the text closes the hole
     **by construction**.
  2. Kills the whole pipeline cost: migration, second model pass per generation, backfill
     CLI, null-fallback policy, and the standing "every new AI-text field must dual-write"
     obligation.
  3. `weekly_reports` keeps `require_athlete` — no auth downgrade needed (its router
     docstring already warns the markdown contains athlete-private context).
- **2026-06-12 — Russian workout names/descriptions stay as-is in demo.** Scheduled-workout
  `name` / `description` / `rationale` are AI-generated Russian text on `/plan` and
  `/workout/:id`. No real PII (zone/interval structure), and stubbing them would leave the
  plan page empty. Mixed-language demo accepted as the smallest problem.
- **Snapshot-clone tenant rejected** — any clone lags, violating the "live" requirement.
  The demo stays read-only over the owner's live row.
- **2026-06-12 — owner sign-off on the remaining open questions:**
  1. **Inventory (§5): everything shows.** Race notes / weather / placement — show.
     Weight / age — show. Workout rationale — show as-is (ru); if a `user_facts` leak is
     spotted in practice, it joins the Phase 2 stub list as a one-liner.
  2. **Public access = Option A** — passwordless "Try the demo" button; `POST
     /api/auth/demo` issues a demo token to any visitor, IP rate-limit stays, env flag is
     the off switch.
  3. **Sample texts** — written by hand at implementation time, one static variant per
     surface (coach + race plan), styled after real bot reports, reviewed in the PR like
     normal code.
  4. **Demo token TTL = 24h** in public mode — a visitor browses all day without
     re-login (re-login is one click anyway with Option A), and flipping the flag closes
     the door within a day.

---

## 3. Current-state audit (facts — verified against code 2026-06-12)

**Auth & role plumbing:**

- `api/routers/auth.py` — `POST /api/auth/demo`: passwordless mint → JWT `purpose="demo"`
  with **owner's** `chat_id`, 24h TTL. IP rate-limited (5 / 5 min, lazy-pruned;
  real client IPs via uvicorn `--proxy-headers` with `forwarded-allow-ips` PINNED to the compose-network gateway — `*` would trust attacker-prepended XFF entries (Caddy appends, doesn't overwrite)). `DEMO_ENABLED=false` → endpoint 404s
  **and** existing demo tokens are rejected at verification (instant kill switch).
- `api/deps.py:79-94` — `is_demo` → virtual `role="demo"`; `last_action_at` **not** bumped
  (so demo traffic doesn't keep the owner "alive").
- `api/deps.py:156` — `require_athlete` / `require_owner` 403 on `role=="demo"` → demo is
  read-only.
- **All demo-reachable write surfaces are guarded** (each checks `role == "demo"` → 403):
  `PUT /api/auth/language`, `PUT /api/auth/sports`, `POST /api/auth/retry-backfill`,
  `POST /api/intervals/auth/init`, `POST /api/intervals/auth/disconnect`.
  `GET /api/auth/mcp-config` is role-gated (`_MCP_ALLOWED_ROLES = {"athlete", "owner"}`)
  — demo **cannot** obtain the owner's `mcp_token` and cannot reach MCP at all.
  These checks are ad-hoc per endpoint — Phase 1 centralizes them.
- `api/routers/auth.py:310-343` — `/api/auth/me` already demo-scrubs: `language="en"`,
  `intervals.athlete_id="demo"`, `display_name=None`, `username=None`, `avatar_url=None`,
  `bot_chat_initialized=True`, `sports` pinned. `GET /api/auth/avatar` 404s for demo.
  **This is the redaction pattern to extend.**

**Data exposure:**

- `chat_id` is never serialized by any router — verified.
- No GPS / polyline / coordinates in any API response — verified.
- `mood_checkins`, `iqos_daily`, `user_facts` have **no API endpoints** (MCP-only).
  Their only path to a demo session was *through the AI free-text* — closed by Phase 2.
- `/api/weekly-reports*` — both endpoints `require_athlete`; demo **cannot** reach them
  today and this stays (see Decisions log).

**AI free-text surfaces a demo session CAN reach today:**

| Surface | Endpoint | Demo treatment (Phase 2) |
| --- | --- | --- |
| `wellness.ai_recommendation` | `GET /api/wellness-day` (`api/routers/wellness.py:384`), rendered by `Coach.tsx` (full) + `Wellness.tsx` (teaser) | stub → frontend sample |
| Race plan payload (JSONB, ru free-text) | `GET /api/race-plan` (`require_viewer`) , rendered by `RacePlanPanel.tsx` | stub → frontend sample |
| Scheduled workout `name` / `description` / `rationale` | `GET /api/scheduled-workouts`, `GET /api/scheduled-workout/{id}` (`api/routers/workouts.py:154-163`), plus `paired.name` in `GET /api/activity/{id}/details` (`api/routers/activities.py:114`) | served as-is (Russian, accepted) — but see §5 rationale row |

---

## 4. Phases

### Phase 1 — Sensitive-data redaction (safety gate)

Make the demo safe to expose **before** anything else.

- Define the sensitive-field inventory (§5) — **needs owner sign-off**.
- Centralize the demo check (helper on `User` or a `deps` predicate, e.g. `is_demo(user)`)
  instead of ad-hoc `user.role == "demo"` scattered across ~7 endpoints.
- Inventory every endpoint a demo session can reach (`require_viewer` +
  `get_current_user`-direct) and scrub PII at serialization when demo. Storage untouched.
- Tests: for each demo-reachable endpoint, assert a demo token never returns a sensitive
  field.

Shippable on its own — demo becomes safe even while still password-gated.

### Phase 2 — AI-text stub + frontend sample

- **Backend:** when demo, AI free-text fields are not serialized — replaced by a stub
  marker the frontend can branch on:
  - `GET /api/wellness-day` → `ai_recommendation: null` + `ai_recommendation_demo_stub: true`
    (or equivalent single convention — pick one shape and reuse it).
  - `GET /api/race-plan` → same stub convention instead of the payload's free-text.
- **Frontend:** `Coach.tsx` / `Wellness.tsx` teaser / `RacePlanPanel.tsx` render a
  hardcoded English sample when the stub flag is set, visually marked, e.g.:
  > *Sample — in the real app this is generated daily from your HRV, sleep and training
  > load.* "Your HRV is back within baseline and sleep was solid (7h40). Green light for
  > today's 4×8' Z4 bike intervals…"
  Sample texts live as constants in the webapp (i18n en.json) — no backend round-trip,
  no storage, zero leak risk.
- Tests: demo token → stub flag + no AI text in response body; owner token → real text
  unchanged.

### Phase 3 — Public (passwordless) access

**Decided: Option A** (sign-off 2026-06-12) — `POST /api/auth/demo` auto-issues a demo
token without a password; the login page gets a public "Try the demo" button.

- **Kill switch:** `DEMO_ENABLED` (replaced `DEMO_PASSWORD`) — when off, the mint endpoint
  404s **and** `get_current_user` rejects existing `purpose="demo"` tokens at
  verification, so the door closes instantly (security review 2026-06-12 upgraded the
  original "within a day" design). TTL = 24h stays as the hard ceiling. Session auth
  also pins the purpose allowlist to `(None, "demo")` — an OAuth-state JWT can never
  be replayed as a login.
- **Abuse surface:** demo is read-only, triggers no Telegram I/O, no owner
  `last_action_at` bump, and cannot reach MCP — it only hits the DB through the API.
  Keep the per-IP rate limit on the mint endpoint.
- **Accepted exposure (signed off):** live + public means anyone can observe in
  near-real-time when the owner trains (activity timestamps), plus race
  name/date/location from goals. Inherent to "live", consciously accepted.

---

## 5. Sensitive-field inventory (SIGNED OFF 2026-06-12)

Final keep/scrub list:

| Field | Source | Status / proposed |
| --- | --- | --- |
| First/last name, @username, avatar | `/api/auth/me`, `/api/auth/avatar` | scrubbed (done) |
| `chat_id` | users | never serialized — verified ✅ |
| Intervals `athlete_id`, OAuth scope, tokens | users | scrubbed (`"demo"`/None, done) |
| `mcp_token` | `/api/auth/mcp-config` | role-gated, demo 403 — verified ✅ |
| `mood_checkins`, `iqos_daily`, `user_facts` | MCP-only, no API endpoints | not exposed ✅; AI-text vector closed by Phase 2 |
| AI free-text (coach, race plan) | wellness / race-plan endpoints | stubbed in Phase 2 ✅ |
| Workout `rationale` | `/api/scheduled-workout/{id}` | **show (ru) — signed off**; it's AI text and *may* reference `user_facts` (injury etc.). If a leak is spotted in practice, add it to the Phase 2 stub list as a one-liner. |
| Race notes / weather / placement | races (via goals/race-plan) | **show — signed off** |
| Weight / age | profile (`/api/auth/me`, `/api/wellness-day` body block) | **show — signed off** |
| Activity timestamps (live schedule) | activities endpoints | show — accepted exposure (Phase 3) |

---

## 6. Open questions

None — all four resolved 2026-06-12, see Decisions log (§2): inventory sign-off,
Option A public access, hand-written sample texts at implementation, 24h demo TTL.

---

## 7. Non-goals

- No snapshot/clone tenant (kills "live").
- No dual-write EN storage / translation pipeline (rejected 2026-06-12, see Decisions log).
- No on-the-fly translation at render time.
- Owner does **not** lose Russian.
- No write access for demo (already enforced; verified across all reachable endpoints).
- Weekly reports stay athlete-only (`require_athlete` unchanged).
