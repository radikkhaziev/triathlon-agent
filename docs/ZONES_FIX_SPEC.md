# Zones MCP Tool Fix — Issue #313 (Resolved 2026-05-08)

**Status:** ✅ Closed. Bundled fix shipped 2026-05-08 covering Phase 1 (reshape) + 1.5 (tests) + 2 (FTP drift) + 3 (docs).

`get_zones` had three coupled defects: sport-ambiguous `power_zones` key (last sport wins), %FTP values emitted with `min_w/max_w` labels as if absolute watts, identical bug in `pace_zones` plus inverted-sign comparison. FTP drift detection had no automated path — only LTHR + threshold-pace were pushed to Intervals.

---

## Where the code lives

| Concern | File |
|---|---|
| Reshape (main rewrite) | `mcp_server/tools/zones.py` |
| Parallel renderer (no bug, untouched) | `bot/prompts.py:_zones_block` |
| FTP drift detector | `data/hrv_activity.py:detect_hrv_thresholds` |
| Drift gate + alert helper | `data/db/user.py:detect_threshold_drift`, `_drift_alert_ftp` |
| FTP push actor | `tasks/actors/athlets.py:actor_update_zones` (metric == "FTP" branch) |
| FTP push primitive | `mcp_server/tools/update_zones.py` |
| Schema | migration `w3d4e5f6a7b8` (`activity_hrv.hrvt2_power` FLOAT NULL) |
| Back-fill | `cli.py:reprocess-ramp-test` |

---

## Decisions (locked 2026-05-08)

1. **Bundle power + pace fix.** Bug shape identical (boundaries stored as %, emitted as absolute units). Splitting в две PR'ы — двойной review effort.
2. **Drop untagged `power_zones` cleanly, no deprecation shim.** Per CLAUDE.md «Always delete dead code». Callers — Claude + project Python; нет внешнего API. Audit confirmed no in-repo readers; new shape: `power_zones_bike` / `power_zones_run` (mirrors HR pattern). Same для `pace_zones_*`.
3. **Pace boundary semantics — drop `slower_than`/`faster_than`.** Каждая pace zone теперь `min_pct/max_pct` (raw %) + `min_sec_per_km/_per_100m` (absolute, computed via inverted formula `threshold_pace_sec × 100 / pct` — pace = reciprocal of speed). Inverted-sign bug уходит вместе с легаси ключами.
4. **FTP push uses HRVT2, не HRVT1.** Coggan FTP ≈ pow at LT2 ≈ pow at HRVT2 (DFA α1 = 0.50). Pow at HRVT1 (α1 = 0.75) — аэробный порог (~70% FTP). Push HRVT1→FTP под-смещал бы Ride зоны на те же ~13% что HRVT1→`lthr` bug делал до фикса. **Cross-spec invariant**: тот же принцип закреплён в `docs/knowledge/ramp-test-protocols.md` §6 (HRVT2 — anaerobic threshold → Intervals' anaerobic anchor).

---

## Post-shipped shape (acceptance bar)

- Owner response carries `power_zones_bike` (FTP=208) **и** `power_zones_run` (FTP=366), каждая zone имеет `min_pct/max_pct` + `min_w/max_w`.
- `pace_zones_run` / `pace_zones_swim` — `min_pct/max_pct` + `min_sec_per_km/_per_100m`.
- Sentinel handling: `pct == 0` → нет `max_*`; `pct >= 999` → нет `min_*` (zone unbounded upward).
- `actor_update_zones` пушит FTP в Intervals когда `|drift|>5%` ∧ `R²≥0.7` для Ride.

---

## Postmortem

- **Почему bug стоял долго.** Owner имел Bike FTP в Intervals задолго до Run power; только Bike had `power_zones` populated, consumer был Claude (LLM-tolerant). Surfaced только когда (a) Run power добавили *и* (b) кто-то аудитнул tool output.
- **Owner-only audit risk.** Если будущие fixtures введут synthetic data не по %FTP convention — fix может потребовать defensive handling. Flag для будущих audit'ов.
- **Upstream of zones.** `SPORT_SETTINGS_UPDATED` webhook (`actor_sync_athlete_settings`) — источник `power_zones`. Если в будущем user сообщит «zones в watts не в %» — смотреть туда.
