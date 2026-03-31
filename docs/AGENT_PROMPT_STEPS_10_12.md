# Agent Prompt — Steps 10-12: Telegram Notifications + DFA in Reports

> Copy this entire prompt and send to the agent.

---

## Task

Implement 3 features for the triathlon agent (DFA_ALPHA1_PLAN.md steps 10-12):

1. **Post-activity Telegram notification** — short DFA summary after FIT processing
2. **Evening report** (21:00) — day summary sent to Telegram
3. **Morning prompt + DFA context** — add yesterday's DFA data to the existing morning AI prompt

Read `CLAUDE.md` fully before starting. It contains the complete project spec, database schema, and architecture.

---

## 1. Post-activity Telegram notification

**What:** After `process_fit_job` processes a FIT file with `status == "processed"`, send a short Telegram message.

**Where to change:**

### `bot/scheduler.py` — modify `process_fit_job` wrapper

Current code (lines 16-30) wraps `data.hrv_activity.process_fit_job`. The wrapper needs to:

1. Accept `bot: Bot | None = None` parameter
2. After processing, query newly created `activity_hrv` rows with `processing_status == "processed"`
3. For each, send a notification via `bot.send_message`
4. The cron job registration already passes no kwargs — add `kwargs={"bot": bot}` (same pattern as `sync_wellness_job`)

**Important:** The inner `data.hrv_activity.process_fit_job` returns a count but doesn't return which activities were processed. You need to either:

- Track which activities were processed (query `activity_hrv` before and after)
- Or modify the inner function to return a list of `(activity_id, status)` tuples instead of just a count

Recommended approach: modify `data.hrv_activity.process_fit_job` to return `list[tuple[str, str]]` — list of `(activity_id, processing_status)`. Then update the wrapper in `bot/scheduler.py`.

### `bot/formatter.py` — add `build_post_activity_message`

```python
def build_post_activity_message(activity: ActivityRow, hrv: ActivityHrvRow) -> str:
```

Short message, 3-4 lines max. Template:

```
🚴 Ride 1h20m | TSS 85
DFA a1: 0.92 (warmup) → 0.68 (avg)
Ra: +3.2% ✅ нормальная готовность
HRVT1: 142 bpm / 180W
```

Rules:

- Sport emoji: 🚴 for bike types, 🏃 for run types
- Duration from `activity.moving_time` (format as Xh Ym or Ym)
- TSS from `activity.icu_training_load`
- DFA line: `dfa_a1_warmup` → `dfa_a1_mean`
- Ra line: only if `ra_pct` is not None. Emoji: ✅ if > -5%, ⚠️ if < -5%
- HRVT1 line: only if `hrvt1_hr` is not None. Add power (bike) or pace (run)
- Da line: only if `da_pct` is not None and activity ≥ 40 min
- Skip lines where data is None — message may be just 2 lines for minimal data
- **Do NOT send anything** for `no_rr_data`, `low_quality`, `too_short`, `error`

### `bot/scheduler.py` — update cron registration

```python
scheduler.add_job(
    process_fit_job,
    trigger="cron",
    hour="4-23",
    minute="*/5",
    id="process_fit",
    kwargs={"bot": bot},  # ADD THIS
)
```

---

## 2. Evening report (21:00)

**What:** Daily summary at 21:00. No AI — just formatted data. Sent to the same Telegram chat.

### `bot/scheduler.py` — add `evening_report_job`

```python
async def evening_report_job(bot: Bot | None = None) -> None:
```

Logic:

1. Get today's date (in `settings.TIMEZONE`)
2. Get `WellnessRow` for today
3. Get activities for today from `activities` table
4. Get `ActivityHrvRow` for today's activities (if any have `processing_status == "processed"`)
5. Build message via `formatter.build_evening_message`
6. Send to Telegram
7. If no activities and no wellness data — skip (don't send "no data")

Register in scheduler:

```python
scheduler.add_job(
    evening_report_job,
    trigger="cron",
    hour=21,
    minute=0,
    id="evening_report",
    kwargs={"bot": bot},
)
```

### `bot/formatter.py` — add `build_evening_message`

```python
def build_evening_message(
    row: WellnessRow | None,
    activities: list[ActivityRow],
    hrv_analyses: list[ActivityHrvRow],
) -> str:
```

Template:

```
📊 Итог дня — 24 марта

Тренировки: 2 | TSS: 120
🚴 Ride 1h20m (TSS 85)
🏃 Run 40m (TSS 35)

Recovery: 72/100 (хорошее)
ESS: 95.3 | Banister: 68%
HRV: 🟢 45.2 мс (δ +3.2%)
RHR: 🟢 42 уд/мин
```

If DFA data available for any activity, add:

```
DFA: Ra +3.2% (ride) | Ra -1.5% (run)
```

Rules:

- Date in Russian format: "24 марта"
- If no activities: "День отдыха" instead of activity lines
- Recovery emoji and title from `CATEGORY_DISPLAY` (already exists in formatter.py)
- HRV status emoji from `STATUS_EMOJI` (already exists)
- TSS sum across all activities
- DFA Ra line only if at least one activity has `ra_pct`
- ESS/Banister from WellnessRow — skip line if None

### Database queries needed

Add to `data/database.py`:

```python
async def get_activities_for_date(dt: date) -> list[ActivityRow]:
    """Get all activities for a specific date."""

async def get_activity_hrv_for_date(dt: date) -> list[ActivityHrvRow]:
    """Get all activity_hrv rows for activities on a specific date."""
```

---

## 3. Morning prompt + DFA context

**What:** Add yesterday's DFA data to `MORNING_REPORT_PROMPT` in `ai/prompts.py`. The morning AI recommendation already works — just expand the data it receives.

### `ai/prompts.py` — extend MORNING_REPORT_PROMPT

Add a new section after "ЗАПЛАНИРОВАННЫЕ ТРЕНИРОВКИ НА СЕГОДНЯ":

```
ВЧЕРАШНИЕ ТРЕНИРОВКИ (DFA):
{yesterday_dfa_summary}
```

Where `yesterday_dfa_summary` is either:

- Formatted DFA data per activity (Ra, Da, HRVT1, quality)
- Or "Нет данных DFA за вчера" if no processed activities

### `ai/claude_agent.py` — pass DFA data to prompt

In `get_morning_recommendation()` (or wherever the prompt is assembled):

1. Query yesterday's activities from `activities` table
2. Query their `activity_hrv` rows
3. Format into text block
4. Pass as `yesterday_dfa_summary` to `MORNING_REPORT_PROMPT.format(...)`

Format per activity:

```
- 🚴 Ride 1h20m: Ra +3.2%, Da -2.1%, HRVT1 142bpm/180W, quality: good
- 🏃 Run 40m: Ra -1.5%, HRVT1 155bpm, quality: good
```

If `processing_status` is not `processed`, show:

```
- 🚴 VirtualRide 30m: нет RR данных (Rouvy)
```

### `data/database.py` — ensure queries exist

The same `get_activities_for_date` and `get_activity_hrv_for_date` from step 2 will be reused here for yesterday's date.

---

## Implementation order

1. First: `data/database.py` — add `get_activities_for_date` and `get_activity_hrv_for_date`
2. Then: `bot/formatter.py` — add `build_post_activity_message` and `build_evening_message`
3. Then: `data/hrv_activity.py` — modify `process_fit_job` return type
4. Then: `bot/scheduler.py` — modify `process_fit_job` wrapper + add `evening_report_job` + update cron registrations
5. Then: `ai/prompts.py` + `ai/claude_agent.py` — extend morning prompt with DFA
6. Last: update `CLAUDE.md` — mark steps 10-12 as done, update scheduler description

---

## Key patterns to follow

- **Telegram send:** use `bot.send_message(chat_id=settings.TELEGRAM_CHAT_ID, text=msg)` — same as `_send_morning_report`
- **Error handling:** wrap sends in try/except, log warnings, never crash the job
- **Date formatting:** use `datetime.now(zoneinfo.ZoneInfo(settings.TIMEZONE)).date()` for today
- **Type imports:** `from __future__ import annotations` + `TYPE_CHECKING` pattern (see formatter.py)
- **Duration formatting:** convert `moving_time` (seconds) to "Xh Ym" or "Ym"

---

## What NOT to do

- Do NOT add `EVENING_REPORT_HOUR` / `EVENING_REPORT_MINUTE` to config.py — use hardcoded `hour=21, minute=0`
- Do NOT use AI for evening report — just formatted data
- Do NOT create new Telegram commands — these are automated messages
- Do NOT modify the existing morning report flow — only extend the prompt data
- Do NOT change `mcp_server/` — MCP tools already work
