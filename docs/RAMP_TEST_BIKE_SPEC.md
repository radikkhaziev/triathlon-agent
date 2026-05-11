# Ramp Test Protocol — DFA a1 (Run + Bike)

**Status:** ✅ shipped 2026-05-08. Issue #313 closed in same window. Drift-pipeline integration done.

**Methodology** (физиология протокола, threshold tables, calibration trap, athlete-facing descriptions, decisions log) перенесена в `docs/knowledge/ramp-test-protocols.md`. Этот файл оставлен как точка входа для архитектуры/имплементации.

---

## What shipped

| Layer | Artifact | Location |
|---|---|---|
| Builders | `build_ramp_steps_run` / `build_ramp_steps_ride` | `data/ramp_tests.py` |
| Builder fallbacks | Run 295 s/km, Bike 200W when sport-settings missing | same |
| MCP integration | `create_ramp_test_tool(sport=...)` consumes builders | `mcp_server/tools/` |
| Phase-aware cadence | `RampTrainingSuggestion` (peak/taper/base/build/no-goal, multi-goal aware) | `tasks/utils.py` |
| Drift detection | Absolute gates (3 bpm / 5 s/km / 5 W) + R² 3-tier (high → auto, medium → button, low → hint) | `data/metrics.py` + actor |
| Zone push | `actor_update_zones` writes HRVT2 → Intervals `lthr` / `threshold_pace` / `ftp` (Ride only for FTP) | `tasks/actors/workout.py` |
| Confidence | `hrvt1_confidence` / `hrvt2_confidence` columns (`n_local` ±0.15 around α1 crossing × global R²) | `activity_hrv` table, migration `x4e5f6a7b8c9` |
| DFA sanity | Slope-sign sanity check, power-bound WARN logging | `data/hrv_activity.py` |

CLAUDE.md «Implementation Status» headline summarises the rebuild — single source of truth для что-когда-вышло.

---

## Pending / deferred

- **DFA regression rewrite** (H1 sigmoid fit, H2a per-step steady-state averaging) — отдельная спека `docs/DFA_REGRESSION_METHODOLOGY_SPEC.md`. Текущий chord-based α1 detector — best-effort, замены на sigmoid пока не блокируют пользу.
- **Swim ramp-test** — `docs/RAMP_TEST_SWIM_SPEC.md` Phase 1-4 (manual CSS workflow → automated → templates → continuous monitoring). Не начато.

---

## Open dependencies — all closed

- ~~Issue #313~~ ✅ closed 2026-05-08 — `get_zones` reshape (sport-tagged keys, dual-unit zone objects) + HRVT2→Intervals fix.
- ~~`update_zones` write-path audit~~ ✅ verified end-to-end on real Intervals.icu API.
- ~~`create_ramp_test_tool` consumes new builders~~ ✅.

---

## References

Theory + protocol details: `docs/knowledge/ramp-test-protocols.md`. DFA theory: `docs/knowledge/dfa-alpha1.md`. Drift detection methodology (deferred sigmoid): `docs/DFA_REGRESSION_METHODOLOGY_SPEC.md`.
