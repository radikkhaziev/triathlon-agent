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

## 3. Implementation map (code is source of truth)

Where each behaviour lives — see the files, not this doc, for exact shapes.

**Auth & role:**

- `api/routers/auth.py` — `POST /api/auth/demo` passwordless mint → JWT `purpose="demo"`
  with **owner's** `chat_id`, 24h TTL, per-IP rate limit (5/5min). `/api/auth/me` is the
  redaction pattern (scrubs name/username/avatar, `intervals.athlete_id="demo"`,
  `language="en"`, pins `sports`); `GET /api/auth/avatar` 404s for demo.
- `api/deps.py` — `is_demo(user)` predicate; demo resolves to virtual `role="demo"` in
  `get_current_user` and does **not** bump owner `last_action_at` (so demo traffic doesn't
  keep the owner "alive"). `require_athlete` / `require_owner` 403 on demo → read-only.
  Session-auth purpose allowlist pinned to `(None, "demo")` so an OAuth-state JWT can never
  be replayed as a login.

**Gotcha — XFF trust:** real client IPs come via uvicorn `--proxy-headers` with
`forwarded-allow-ips` PINNED to the compose-network gateway. `*` would trust
attacker-prepended XFF entries (Caddy appends, doesn't overwrite), defeating the rate limit.

**Verified-safe data surfaces:** `chat_id` never serialized; no GPS/polyline in any
response; `mood_checkins` / `iqos_daily` / `user_facts` have no API endpoints (MCP-only) —
their only demo path was *through AI free-text*, closed by the stub. `mcp-config` is
role-gated (`{"athlete","owner"}`) so demo can't get `mcp_token` / reach MCP. All
demo-reachable write surfaces (language/sports/retry-backfill/intervals init+disconnect)
403 on demo. `/api/weekly-reports*` stay `require_athlete`.

**AI free-text stub (`demo_stub` convention):** `GET /api/wellness-day`
(`api/routers/wellness.py`) and `GET /api/race-plan` (`api/routers/race_plan.py`) drop the
AI text and set `demo_stub: true` for demo sessions. Frontend (`Coach.tsx`,
`RacePlanPanel.tsx`, `DemoSampleBadge.tsx`) renders a hand-written English sample with a
"Sample" badge off that flag — samples are frontend constants, no backend round-trip, zero
leak risk. Scheduled-workout `name`/`description`/`rationale` are served **as-is** (Russian,
accepted — see §5).

---

## 4. Phases (all shipped 2026-06-12)

**Phase 1 — sensitive-data redaction (safety gate):** centralized `is_demo` predicate
replacing ad-hoc `role == "demo"` checks; PII scrubbed at serialization for every
demo-reachable endpoint; storage untouched.

**Phase 2 — AI-text stub + frontend sample:** AI free-text not serialized to demo
(`demo_stub` flag); frontend renders hand-written EN sample with badge. Why a stub and not
a translation: the PII lives inside the AI text — not serving it closes the hole **by
construction** (see §2).

**Phase 3 — public (passwordless) access:** Option A — `POST /api/auth/demo` mints without
a password; login page has a public "Try the demo" button.

- **Kill switch:** `DEMO_ENABLED` (replaced `DEMO_PASSWORD`) — when off, mint 404s **and**
  `get_current_user` rejects existing `purpose="demo"` tokens at verification, so the door
  closes **instantly** (security review 2026-06-12 upgraded the original "within a day"
  design). 24h TTL is a hygiene ceiling, not the revocation path.
- **Abuse surface:** demo is read-only, triggers no Telegram I/O, no owner `last_action_at`
  bump, cannot reach MCP — only hits the DB through the API. Per-IP rate limit on mint stays.
- **Accepted exposure (signed off):** live + public means anyone can observe near-real-time
  when the owner trains (activity timestamps) plus race name/date/location from goals.
  Inherent to "live", consciously accepted.

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
