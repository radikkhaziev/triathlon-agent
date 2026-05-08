# Zones MCP Tool Fix — Research & Implementation Spec

> Issue #313: `get_zones` returns sport-ambiguous `power_zones` with mis-labeled
> units (zone boundaries stored as %FTP but emitted as `min_w`/`max_w`).
> Bike FTP invisible. Same shape problem affects `pace_zones`. Plus: FTP
> drift detection / push currently has no automated path (the LTHR/pace flow
> we built 2026-05-08 has no analogue for cycling FTP). This spec drives the
> research-first cycle: audit current state, lock decisions, then implement
> all of it in one PR.

**Related:**

| Issue / Spec | Связь |
|---|---|
| GitHub #313 | Reporter context, expected behavior, screenshots |
| `CLAUDE.md` (zones contract) | `data/db/athlete.py:33` — `hr_zones` bpm, `power_zones` %FTP, `pace_zones` %threshold |
| `mcp_server/tools/zones.py` | `get_zones` — primary fix target |
| `bot/prompts.py:get_system_prompt_chat` | Second consumer of zones, parallel rendering path |
| `data/ramp_tests.py` | Bike ramp = `RAMP_STEPS_RIDE`, fixed %FTP (no scaling), but `create_ramp_test_tool` consumes zones data indirectly |
| `tasks/actors/athlets.py:actor_update_zones` | LTHR/THRESHOLD_PACE push paths; FTP push status TBD (research) |

---

## 1. Problem statement

`get_zones` MCP tool has three coupled defects:

1. **Sport ambiguity.** The tool writes to a single `result["power_zones"]` key in a per-sport loop — last sport wins. Athletes with both running power (Stryd / Garmin RP) and cycling power lose one side. Issue reporter has Run FTP=366W and Bike FTP=208W in Intervals; only one survives in the response.
2. **Unit mis-labeling.** `AthleteSettings.power_zones` stores **%FTP** boundaries (per CLAUDE.md zone contract) but `_zones_from_boundaries(...,"w")` emits them with `min_w`/`max_w` keys as if they were absolute watts. Result: response self-inconsistent — `ftp: 366` together with `Z2 Endurance min_w=56 max_w=75` (Coggan percentages, not 205-275W).
3. **`pace_zones` has the same shape bug.** `pace_zones` stores **%threshold** (where 100.0 = threshold pace). `_zones_from_boundaries(..., "pace")` emits `slower_than`/`faster_than` as if boundaries were absolute s/km. Plus the sign of the comparison is inverted (our values are ascending %=ascending speed, code treats as descending pace).

Issue #313 explicitly scopes to power. Pace shares the root cause; the fix should bundle both unless that complicates rollout disproportionately.

### Why it matters

- **Bike ramp test mis-targeted.** Any logic that scales `%FTP × power_zones.ftp` to absolute watts uses the wrong FTP (Run's 366 → ~76% overshoot of every bike target).
- **Drift detection blind to FTP.** Zones tool is read-only; for FTP drift (analogous to LTHR / THRESHOLD_PACE) we'd need a clean data path. Today `actor_update_zones` doesn't touch FTP at all (research §4 below).
- **LLM downstream confusion.** Claude reads `get_zones` output verbatim. Inconsistent ftp/zone_boundary pairs mean unreliable workout generation and sport-specific reasoning.

### What "fixed" looks like

Per issue #313 expected behavior:

```json
{
  "power_zones_bike": {
    "ftp": 208,
    "source": "intervals.icu",
    "zones": [
      {"zone": 2, "name": "Endurance",
       "min_pct": 56, "max_pct": 75,
       "min_w": 117,  "max_w": 156},
      ...
    ]
  },
  "power_zones_run": { "ftp": 366, "source": "...", "zones": [...] }
}
```

Each zone object carries **both** `min_pct/max_pct` (raw % from DB) and `min_w/max_w` (computed `ftp × pct / 100`). Sport-tagged keys mirror the existing `hr_zones_bike` / `hr_zones_run` / `hr_zones_swim` pattern. Same shape for `pace_zones_run` / `pace_zones_swim` if pace is in scope.

---

## 2. Research findings (audit complete — 2026-05-08)

Subagent pass over Q1-Q7. Citations preserved; full transcript archived in
session log.

### Q1. Other code paths rendering zones — `bot/prompts.py:_zones_block`

**Independent renderer, no bug.** `_zones_block` (`bot/prompts.py:343-415`,
called from `render_athlete_block` at `:595`) rebuilds zones from
`AthleteSettings` directly, not via `get_zones`. Crucially it **already
treats `s.power_zones` as %FTP** (`bot/prompts.py:376-377` →
`_pct_ranges(list(ride.power_zones))`) and emits with explicit
`"units": "%ftp"` label (`:382`, `:384`). No watts/percentage confusion.

Fallback tables (`_FALLBACK_RUN_HR_PCT`, `_FALLBACK_BIKE_HR_PCT`,
`_FALLBACK_RIDE_POWER_PCT` at `bot/prompts.py:293-295`) duplicate
`mcp_server/tools/zones.py:_FALLBACK_HR_RUN/_FALLBACK_HR_BIKE/_FALLBACK_POWER`
(`zones.py:48-72`) — same numeric values, different format (int vs float
tuples). Extracting a shared helper would be DRY but is not a Phase 1
blocker; the prompts path is correct as-is.

### Q2. `power_zones` = %FTP in DB

Confirmed via test fixtures + schema contract:
- `data/db/athlete.py:56` schema comment: «%FTP, ascending. Example:
  `[55, 75, 90, 105, 120, 150, 999]`».
- `tests/bot/test_prompts_zones.py:88` owner Ride fixture matches that
  example exactly with `ftp=250`.
- `tests/api/test_webhook_dispatch.py:69` synthetic user uses
  `[115, 156, 189, 220, 252]` — max ~252 still fits %FTP scale (Z6/Z7 of
  Coggan model in %); not absolute watts.

No row anywhere stores absolute watts. Sync path (Intervals →
`AthleteSettings`) is honored.

### Q3. `pace_zones` = %threshold (100.0 anchor)

Schema contract `data/db/athlete.py:57`: «%threshold where 100.0 = threshold,
ascending. Example: `[77.5, 87.7, 94.3, 100.0, 103.4]`». Owner Swim row
matches. No fixture deviates.

### Q4. FTP push — exists via MCP tool, untested

**Found:** `mcp_server/tools/update_zones.py:9-48`. Signature
`update_zones(sport: str, lthr: int | None = None, ftp: int | None = None)`.
Lines 36-38: if `ftp is not None`, payload includes `"ftp": ftp`, pushed via
`client.update_sport_settings(sport, payload)` at `:40`.

**Not tested.** `grep update_sport_settings.*ftp tests/` returns nothing.

**Actor flow does NOT handle FTP.** `tasks/actors/athlets.py:184-196` elif
chain covers `LTHR` and `THRESHOLD_PACE` only. The 2026-04-07 push from
issue #313 was the manual MCP-tool path.

Implication for this spec: Phase 2 (FTP drift detection) needs new metric
plumbing in the drift detector + actor; the *push* primitive already exists.

### Q5. Untagged `power_zones` consumers — none in-repo

Only writer: `mcp_server/tools/zones.py:113`. No Python file reads
`result["power_zones"]` from the untagged key. `bot/prompts.py:376` reads
`ride.power_zones` directly off `AthleteSettings`, not through MCP.
`bot/tool_filter.py:60` lists `get_zones` as filterable but doesn't
introspect the response.

**Drop is safe.** No alias / deprecation needed (D2 = A confirmed).

### Q6. Bike LTHR 163↔165 staleness

Normal cached-vs-Intervals lag pattern (already documented in CLAUDE.md
implementation notes). Not a sync bug. Out of scope.

### Q7. Tests landscape

- `tests/bot/test_prompts_zones.py` — 11 tests, all on `_zones_block` with
  `AthleteSettings` directly. **Untouched** by Phase 1.
- `tests/api/test_activity_details_serialization.py` — activity-level zone
  times, unrelated. Untouched.
- `tests/api/test_webhook_dispatch.py` — sync-actor coverage, fixtures use
  %FTP. Untouched.
- **Zero tests on `get_zones` output shape.** Phase 1 must add
  `tests/mcp/test_zones.py` — coverage gap regardless of the fix.

---

## 3. Decisions (locked 2026-05-08)

### D1. Pace zones in scope? — **B (power + pace bundled)**

The bug shape is identical (boundaries stored as %, emitted as if absolute units), splitting into two PRs duplicates review effort. If §2 audit reveals pace consumers that complicate rollout, escalate before implementation; otherwise keep bundled.

### D2. Backwards compat for `power_zones` (untagged) key? — **A (drop cleanly)**

Per CLAUDE.md «Always delete dead code, no wrappers/deprecation shims». MCP consumers are Claude + project Python code; no external API surface. The `power_zones` (untagged) key disappears entirely; new code reads `power_zones_bike` / `power_zones_run`. Audit §2 Q5 must confirm the in-repo consumer list before merge so each call site is migrated in the same PR.

### D3. Pace boundary semantics — **A (drop `slower_than`/`faster_than`)**

Each pace zone object outputs `min_pct` / `max_pct` (raw % from DB) **and** `min_sec_per_km` / `max_sec_per_km` (or `_per_100m` for Swim, computed via `threshold_pace_sec × 100 / pct` — note inverted formula vs power because pace is reciprocal of speed). The legacy `slower_than` / `faster_than` keys are removed; the inverted-sign bug goes with them. Same in-repo migration discipline as D2.

### D4. Phase 2 (FTP drift) — **A (keep in this spec, defer implementation)**

§2 Q4 audits the existing FTP push paths. Findings become §6 «Phase 2 — FTP drift detection» appendix in this spec, but actual implementation lands in a separate PR after Phase 1 ships. This keeps research context co-located.

---

## 4. Audit checklist (closed, kept for traceability)

Original investigator format. Concrete findings now live in §2.

The investigator (subagent or human) produces a markdown report answering Q1-Q7 above. Constraints:

- **Read-only** — no edits during research phase.
- **Cite files + line numbers** for every claim. No "probably X" — either find it or note "not found".
- **Sample real data** — run the listed SQL/grep commands, paste actual output (truncated if huge).
- **Time-box**: ~30 min focused. If a question stays unanswered, escalate as a sub-question.

Output format suggestion:

```markdown
## Q1. _zones_block in prompts.py
- File: bot/prompts.py:NNN
- Renders zones from: AthleteSettings (direct), not get_zones
- Has %FTP confusion: yes/no — evidence: ...
- Fallback tables: _FALLBACK_RUN_HR_PCT, _FALLBACK_BIKE_HR_PCT, _FALLBACK_RIDE_POWER_PCT
  - These are %, not absolute — match get_zones fallback semantics
- Recommendation: extract shared helper / fix both / doesn't intersect

## Q2. DB sample for power_zones
- Owner row: ...
- Other users (if any): ...
- All values look like %FTP: yes/no
```

---

## 5. Implementation plan

> Audit complete (§2), decisions locked (§3). **Phases 1, 1.5, 2, 3 — done
> (2026-05-08).** Issue #313 ready to close after merge.

### Phase 1 — `get_zones` rewrite (single PR)

**Scope (per D1=B, both power and pace bundled):**

1. `mcp_server/tools/zones.py` — main rewrite:
   - `_build_sport_zones`: replace single `result["power_zones"]` write with
     sport-tagged `result[f"power_zones_{prefix}"]` (mirrors HR pattern).
     Same for the `pace_zones` blocks (already partially sport-tagged but
     shape changes — see below).
   - New helper `_dual_unit_power_zones(zones_pct, ftp, names)` — emits zone
     objects with all four keys: `min_pct`, `max_pct`, `min_w`, `max_w`.
     Replaces `_zones_from_boundaries(..., "w")` for the power path.
   - New helper `_dual_unit_pace_zones(zones_pct, threshold_pace_sec, names, sport)` —
     emits zone objects with `min_pct`, `max_pct`, `min_sec_per_km` /
     `min_sec_per_100m` (sport-dependent), and corresponding max. Drops
     `slower_than`/`faster_than` (D3).
   - `_fallback_power_zones(ftp)` updated to also emit `min_pct/max_pct`
     alongside `min_w/max_w`.
   - Fallback for HR zones unchanged (already absolute bpm — correct).

2. **Pace formula details** (D3 confirmed):
   - For Run pace stored as %threshold where 100.0 = threshold pace:
     `sec_per_km(pct) = threshold_pace_sec × 100 / pct` (inverse because
     pace is reciprocal of speed).
   - For Swim: identical, output `min_sec_per_100m` / `max_sec_per_100m`.
   - Edge: `pct == 0` (Z1 lower bound) → no `max_sec` (zone has no slower
     limit). Similarly `pct >= 999` (sentinel) → no `min_sec` (zone has no
     faster limit).

3. Drop `result["power_zones"]` (untagged) — D2 confirmed safe per §2 Q5.

4. **`bot/prompts.py:_zones_block` not touched** — already correct
   (§2 Q1). Shared-helper extraction deferred (out of scope for this PR).

### Phase 1.5 — Tests

- New file `tests/mcp/test_zones.py`:
  - Owner-shape: Bike has `power_zones` synced + `ftp`, Run has only `ftp`
    (fallback path), Swim has `pace_zones` + `threshold_pace`.
  - Assert each zone carries all four units (pct + absolute).
  - Assert sport-tagged keys exist (`power_zones_bike`, `power_zones_run`,
    `pace_zones_run`, `pace_zones_swim`).
  - Assert untagged `power_zones` / `pace_zones` keys are absent.
  - Edge: athlete with `ftp=None` → no `power_zones_*` block at all.
  - Edge: `pct=0` and `pct=999` sentinel handling.

- Existing `tests/bot/test_prompts_zones.py` — confirm green, no changes.

### Phase 2 — FTP drift detection (Ride)

Same PR. Mirrors the HRVT2 mapping fix landed for LTHR/threshold_pace
(2026-05-08): the value pushed to Intervals' `ftp` field is pow at HRVT2
(anaerobic threshold ≈ LT2), not pow at HRVT1.

**Why HRVT2, not HRVT1.** Coggan's FTP definition ≈ pow at LT2 ≈ pow at
HRVT2 (DFA α1 = 0.50). Pow at HRVT1 (α1 = 0.75) is the aerobic threshold —
significantly lower (~70% of FTP for trained cyclists). Pushing
`hrvt1_power` to `AthleteSettings.ftp` / Intervals' `ftp` field would
under-shift cycling zones the same ~13% way HRVT1→`lthr` did. Avoid by
construction.

**Schema:**

1. New migration `wXXX_add_activity_hrv_hrvt2_power.py`:
   - `ALTER TABLE activity_hrv ADD COLUMN hrvt2_power FLOAT NULL;`
   - `down_revision = "v2c3d4e5f6a7"` (chain after the hrvt2_pace migration).

2. `data/db/activity.py:ActivityHrv` — add
   `hrvt2_power: Mapped[float | None]` next to `hrvt1_power`.

3. `tasks/dto.py:ThresholdsDTO` — add `hrvt2_power: int | None = None`.

**Detector** (`data/hrv_activity.py:detect_hrv_thresholds`):

Existing block at `:417-429` interpolates `hrvt1_power` via
`p_coeffs = np.polyfit(p_hr, p_power, 1)` then `np.polyval(p_coeffs,
hrvt1_hr)`. Extend with the same `(key, hr_target)` loop pattern used for
hrvt1_pace/hrvt2_pace (already merged), gated on `hrvt2_hr_safe`:

```python
for key, hr_target in (("hrvt1_power", hrvt1_hr), ("hrvt2_power", hrvt2_hr_safe)):
    if hr_target is None:
        continue
    pow_at = np.polyval(p_coeffs, hr_target)
    if 50 < pow_at < 800:  # generous bound for hrvt2 vs hrvt1's 50-500
        result[key] = round(pow_at)
```

Upper bound widened (800W) because pow at HRVT2 can legitimately exceed pow
at HRVT1's 500W ceiling for very strong cyclists. Lower bound stays 50W.

**Drift detector** (`data/db/user.py`):

1. Extend `detect_threshold_drift` — loop already iterates Ride+Run; for
   Ride only, after the LTHR alert, run an FTP alert path:
   ```python
   if sport_label == "Ride" and settings_row.ftp:
       alert = _drift_alert_ftp(sport_label, hrvt2_power, r_squared, settings_row.ftp)
       if alert:
           alerts.append(alert)
   ```
2. New helper `_drift_alert_ftp(sport, hrvt2_power, r_squared, config_ftp)`:
   - Identical shape to `_drift_alert_lthr` (gates `|drift|>5%` ∧ `R²≥0.7`).
   - Returns `DriftAlertDTO(metric="FTP", measured=round(hrvt2_power), config_value=config_ftp, ...)`.
3. Update the SQL in `detect_threshold_drift` to also SELECT
   `ActivityHrv.hrvt2_power` (single query already pulls `hrvt2_hr` and
   `hrvt2_pace` — add the third column).

**Actor** (`tasks/actors/athlets.py:actor_update_zones`):

Add elif branch:
```python
elif alert.metric == "FTP":
    client.update_sport_settings(sport, {"ftp": new_value})
    AthleteSettings.upsert(user_id=user.id, sport=sport, ftp=new_value)
    updated.append(f"FTP {sport}: {old_value} → {new_value} W")
    logger.info("Updated FTP %s for user %d: %d → %d", sport, user.id, old_value, new_value)
```

**Formatter** (`tasks/formatter.py:build_ramp_test_message`):

Currently shows HRVT1 line with optional `hrvt1_power`. Add HRVT2 power to
the HRVT2 line (already shows pace) and add an FTP drift comparison line
parallel to «текущий LTHR» / «текущий threshold pace»:

```python
if config_ftp and hrv.hrvt2_power is not None:
    ftp_pct = (hrv.hrvt2_power - config_ftp) / config_ftp * 100
    lines.append(f"{_('текущий FTP')}: {config_ftp} W ({ftp_pct:+.1f}%)")
    visible, hint = _drift_button_status(hrv.hrvt2_power, config_ftp, r2)
    # ... mirror LTHR/pace logic
```

Pass `config_ftp = settings.ftp` from `tasks/actors/activities.py` (where
the message is built post-activity).

**MCP tool exposure** (`mcp_server/tools/activity_hrv.py`):

Add `hrvt2_power` to the threshold dict alongside `hrvt2_pace` (analogous
to the M5 fix already landed):
```python
result["thresholds"] = {
    ...,
    "hrvt2_pace": row.hrvt2_pace,
    "hrvt2_power": row.hrvt2_power,
    ...
}
```

**CLI** (`cli.py:_reprocess_ramp_test`):

Currently patches `hrvt2_pace` only. Extend to also patch `hrvt2_power`
when present in detector output. Same idempotent semantics, same `--push`
flag triggers `actor_update_zones` which now also pushes FTP if drift
fires. Help text updated to mention FTP.

**Tests:**

1. `tests/db/test_threshold_drift.py` — extend `TestDriftAlertHelpers`
   with `_drift_alert_ftp` cases (None hrvt2_power, low R², below 5%, fires).
2. Integration tests in same file — add FTP-drift cases (Ride row with
   `hrvt2_power` mismatching `ftp`).
3. `tests/tasks/test_athlete_actors.py` — `actor_update_zones` test for
   `metric="FTP"`, asserts `update_sport_settings(sport, {"ftp": …})`
   called and notification renders `FTP Ride: X → Y W`.
4. `tests/api/test_notifications.py` — `build_ramp_test_message` ramp test
   for Ride showing FTP drift line.
5. **`mcp_server/tools/update_zones.py` test coverage** (closes Q4 gap):
   `tests/mcp/test_update_zones.py` — assert `update_zones(sport="Ride",
   ftp=210)` calls `update_sport_settings("Ride", {"ftp": 210})` +
   `AthleteSettings.upsert(ftp=210)`.

### Phase 3 — Documentation

- Update CLAUDE.md zones-contract paragraph: cite new sport-tagged shape.
  Note FTP drift via HRVT2 power, parallel to LTHR/threshold_pace pattern.
- `docs/IMPLEMENTATION_STATUS.md` — entry under «Zones tool fix +
  FTP drift (2026-05-XX)».
- `docs/ADAPTIVE_TRAINING_PLAN.md` — add FTP to the threshold drift
  section's metric table; mention `hrvt2_power` in the detector flow.
- Close issue #313 with «What was done / How to verify» comment.

---

## 6. Acceptance criteria

**Phase 1 (`get_zones` reshape):**
- `get_zones` response for the owner contains both `power_zones_bike`
  (ftp=208) and `power_zones_run` (ftp=366), each with zones carrying
  `min_pct` / `max_pct` **and** `min_w` / `max_w`.
- `pace_zones_run` and `pace_zones_swim` carry `min_pct` / `max_pct` and
  `min_sec_per_km` / `min_sec_per_100m` (sport-dependent). No
  `slower_than` / `faster_than` keys remain.
- Untagged `power_zones` and `pace_zones` keys absent from response.
- No regression in `hr_zones_bike` / `hr_zones_run` / `hr_zones_swim`
  (already correct).

**Phase 2 (FTP drift):**
- `actor_update_zones` pushes FTP to Intervals when latest valid Ride
  ramp test produces `hrvt2_power` differing from
  `AthleteSettings.ftp` by `>5%` with `R²≥0.7`.
- `build_ramp_test_message` for Ride ramp tests displays FTP drift line
  alongside LTHR.
- `mcp_server/tools/update_zones.py` covered by test, including FTP path.
- CLI `reprocess-ramp-test` back-fills `hrvt2_power` for old Ride ramp
  tests; `--push` flow propagates FTP drift end-to-end.

**Cross-cutting:**
- Lint clean, full test suite green, focused suite extended.
- Migration `wXXX_add_activity_hrv_hrvt2_power` chains correctly off
  `v2c3d4e5f6a7`.
- Existing 11 tests in `tests/bot/test_prompts_zones.py` remain green
  (no changes to `_zones_block`).

---

## 7. Notes / open

- **Owner-only audit risk.** Research §2 leans heavily on the owner's row. If our fixtures/test users have synthetic data that doesn't match the %FTP convention, fix may need to handle both unit-shapes defensively. Should be flagged in Q2 audit.
- **Webhook-driven sync.** `SPORT_SETTINGS_UPDATED` webhook (`actor_sync_athlete_settings`) is the upstream of `power_zones`. If a future user reports zones=watts not %, look there first.
- **Why the bug stuck this long.** Owner has Bike FTP set in Intervals long before Run power was added; for a long time only Bike had `power_zones` populated, and the consumer was Claude (LLM-tolerant of bad data). The bug surfaces only when (a) athlete adds run power *and* (b) someone audits the tool output.
