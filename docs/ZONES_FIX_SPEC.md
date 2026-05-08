# Zones MCP Tool Fix — Issue #313 (Resolved 2026-05-08)

> Issue #313: `get_zones` returned sport-ambiguous `power_zones` with %FTP boundaries emitted as `min_w/max_w`. Same shape bug in `pace_zones`. FTP drift detection had no automated path. Bundled fix shipped 2026-05-08 covering Phase 1 (reshape) + 1.5 (tests) + 2 (FTP drift) + 3 (docs).

**Status:** ✅ Done. Issue #313 closed.

**Code anchors (post-fix truth):**

| Concern | File |
|---|---|
| Reshape — main rewrite | `mcp_server/tools/zones.py` |
| Parallel renderer (no bug, untouched) | `bot/prompts.py:_zones_block` |
| FTP drift detector | `data/hrv_activity.py:detect_hrv_thresholds` |
| Drift gate + alert helper | `data/db/user.py:detect_threshold_drift`, `_drift_alert_ftp` |
| FTP push actor | `tasks/actors/athlets.py:actor_update_zones` |
| Existing FTP push primitive | `mcp_server/tools/update_zones.py` |
| Schema | migration `w3d4e5f6a7b8` (`activity_hrv.hrvt2_power`) |
| Back-fill | `cli.py:reprocess-ramp-test` |

---

## 1. Problem statement (postmortem)

`get_zones` had three coupled defects:

1. **Sport ambiguity** — single `result["power_zones"]` key in a per-sport loop; last sport wins. Athletes with both running power and cycling power lost one side.
2. **Unit mis-labeling** — `AthleteSettings.power_zones` stores **%FTP** but the tool emitted them with `min_w`/`max_w` keys as if absolute watts. Result: `ftp: 366` together with `Z2 min_w=56 max_w=75` (Coggan percentages, not watts).
3. **`pace_zones` had identical shape bug** plus inverted comparison sign (ascending %=ascending speed, code treated as descending pace).

---

## 2. Research findings (audit summary, 2026-05-08)

| Q | Answer | Cite |
|---|---|---|
| Q1. Other zones renderers? | `bot/prompts.py:_zones_block` independent — treats `power_zones` as %FTP correctly with explicit `"units": "%ftp"` label | `bot/prompts.py:343-415, 376-377, 382` |
| Q2. `power_zones` = %FTP in DB? | Confirmed schema + test fixtures. No row stores absolute watts. | `data/db/athlete.py:56`, `tests/bot/test_prompts_zones.py:88`, `tests/api/test_webhook_dispatch.py:69` |
| Q3. `pace_zones` = %threshold (100.0 anchor)? | Confirmed. Owner Swim row matches. | `data/db/athlete.py:57` |
| Q4. FTP push exists? | Yes — `update_zones` MCP tool accepts `ftp` param. **Untested at audit time.** Actor branch covered LTHR + THRESHOLD_PACE only. | `mcp_server/tools/update_zones.py:9-48`, `tasks/actors/athlets.py:184-196` |
| Q5. Untagged `power_zones` consumers in-repo? | None. Only writer is `mcp_server/tools/zones.py:113`. `bot/prompts.py:376` reads off `AthleteSettings` directly. | — |
| Q6. Bike LTHR 163↔165 staleness? | Normal cached-vs-Intervals lag. Out of scope. | — |
| Q7. Tests landscape? | 11 tests in `tests/bot/test_prompts_zones.py` on `_zones_block`. **Zero on `get_zones` output shape.** Phase 1.5 closed this gap. | — |

---

## 3. Decisions (locked 2026-05-08)

### D1. Pace zones in scope? — **B (power + pace bundled)**

Bug shape identical (boundaries stored as %, emitted as if absolute units). Splitting into two PRs duplicates review effort.

### D2. Backwards compat for `power_zones` (untagged) key? — **A (drop cleanly)**

Per CLAUDE.md «Always delete dead code, no wrappers/deprecation shims». MCP consumers are Claude + project Python code; no external API surface. Audit Q5 confirmed no in-repo readers; `power_zones` (untagged) key disappears entirely, callers migrate to `power_zones_bike` / `power_zones_run`.

### D3. Pace boundary semantics — **A (drop `slower_than`/`faster_than`)**

Each pace zone outputs `min_pct` / `max_pct` (raw % from DB) **and** `min_sec_per_km` / `max_sec_per_km` (or `_per_100m` for Swim, computed via `threshold_pace_sec × 100 / pct` — inverted formula vs power because pace is reciprocal of speed). The legacy `slower_than` / `faster_than` keys are removed; the inverted-sign bug goes with them.

### D4. Phase 2 (FTP drift) — **A (keep in this spec, ship in same PR)**

Researched alongside Phase 1, implemented in same PR. **Why HRVT2, not HRVT1:** Coggan FTP ≈ pow at LT2 ≈ pow at HRVT2 (DFA α1 = 0.50). Pow at HRVT1 (α1 = 0.75) is the aerobic threshold (~70% of FTP). Pushing `hrvt1_power` to `ftp` would under-shift Ride zones the same ~13% way HRVT1→`lthr` did before the 2026-05-08 fix.

---

## 4. What shipped (Phase 1 + 1.5 + 2 + 3)

**Phase 1 — `get_zones` reshape** (`mcp_server/tools/zones.py`):
- Sport-tagged keys: `power_zones_bike` / `_run`, `pace_zones_run` / `_swim` (mirrors HR pattern).
- Dual-unit zone objects: `min_pct/max_pct` (raw %) **and** `min_w/max_w` or `min_sec_per_*/max_sec_per_*` (absolute, computed `ftp × pct / 100` for power, `threshold_pace_sec × 100 / pct` for pace).
- Untagged `power_zones` / `pace_zones` keys removed entirely.
- Sentinel handling: `pct == 0` → no `max_sec`/`max_w`; `pct >= 999` → no `min_sec`/`min_w` (zone unbounded upward).
- `bot/prompts.py:_zones_block` untouched (already correct, see Q1).

**Phase 1.5 — Tests:**
- New `tests/mcp/test_zones.py` covers owner shape, dual-unit invariants, sport-tagged presence, untagged absence, `ftp=None` edge, sentinel handling.
- New `tests/mcp/test_update_zones.py` closes the Q4 coverage gap on the FTP push primitive.

**Phase 2 — FTP drift detection (Ride only):**
- Migration `w3d4e5f6a7b8` adds `activity_hrv.hrvt2_power FLOAT NULL`.
- `detect_hrv_thresholds` extends to interpolate `hrvt2_power` (bounds widened to 50-800W vs HRVT1's 50-500W).
- `detect_threshold_drift` adds Ride-only FTP alert path via `_drift_alert_ftp` (gate `|drift|>5%` ∧ `R²≥0.7`).
- `actor_update_zones` adds `metric == "FTP"` branch → `update_sport_settings(sport, {"ftp": new_value})` + `AthleteSettings.upsert(ftp=new_value)`.
- Formatter `build_ramp_test_message` shows FTP drift line for Ride ramp tests alongside LTHR / threshold pace.
- CLI `reprocess-ramp-test` extended to back-fill `hrvt2_power` for old Ride ramp tests.

**Phase 3 — Documentation:**
- CLAUDE.md zones-contract paragraph reshaped (sport-tagged shape + dual-unit objects).
- `IMPLEMENTATION_STATUS.md` entry under «Zones tool fix + FTP drift (2026-05-08)».
- `ADAPTIVE_TRAINING_PLAN_SPEC.md` threshold-drift table extended with FTP.
- Issue #313 closed with «What was done / How to verify» comment.

---

## 5. Acceptance criteria (met)

**Phase 1 (`get_zones` reshape):**
- ✅ Owner response carries `power_zones_bike` (ftp=208) **and** `power_zones_run` (ftp=366), each with `min_pct/max_pct` + `min_w/max_w`.
- ✅ `pace_zones_run` / `pace_zones_swim` carry `min_pct/max_pct` + `min_sec_per_km/_per_100m`. No `slower_than/faster_than` keys remain.
- ✅ Untagged `power_zones` / `pace_zones` absent from response.
- ✅ No regression in `hr_zones_*` (already correct pre-fix).

**Phase 2 (FTP drift):**
- ✅ `actor_update_zones` pushes FTP to Intervals when latest valid Ride ramp test produces `hrvt2_power` differing from `AthleteSettings.ftp` by `>5%` with `R²≥0.7`.
- ✅ `build_ramp_test_message` for Ride displays FTP drift line.
- ✅ `update_zones` MCP tool covered by test, including FTP path.
- ✅ CLI `reprocess-ramp-test` back-fills `hrvt2_power`; `--push` flow propagates FTP drift end-to-end.

---

## 6. Notes / postmortem

- **Why the bug stuck this long.** Owner had Bike FTP set in Intervals long before Run power was added; only Bike had `power_zones` populated, and the consumer was Claude (LLM-tolerant of bad data). Surfaced only when (a) Run power got added *and* (b) someone audited the tool output.
- **Owner-only audit risk.** Research §2 leans heavily on the owner's row. If future fixtures introduce synthetic data not matching the %FTP convention, fix may need defensive handling — flag in any future Q2-style audit.
- **Webhook-driven sync.** `SPORT_SETTINGS_UPDATED` (`actor_sync_athlete_settings`) is upstream of `power_zones`. If a future user reports zones=watts not %, look there first.
