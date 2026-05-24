# Halo primitives

Building blocks for the Halo redesign. Tracked solely via
`docs/WEBAPP_HALO_REDESIGN_SPEC.md` (no GitHub issues for this workstream).

## Phase-0 contract

- **Additive / opt-in.** Nothing here is mounted yet. `Layout`, the legacy
  `components/BottomTabs.tsx`, and every live screen are untouched — Phase 0
  ships **zero user-visible change** (token strategy A1).
- **Namespaced tokens.** Style only with the `halo-*` Tailwind keys
  (`bg-halo-surface`, `text-halo-ink`, `rounded-card`, `shadow-card`, …),
  backed by `--color-*` vars in `src/styles/index.css`. Never reuse the
  legacy `--bg/--accent/--text` palette here.
- **`--color-border` is alpha-on-ink (`rgb(10 13 24 / 0.08)`),
  `border-color` only.** Don't use `bg-halo-border` as a fill — it renders
  near-invisible. Tracks/empty bars use `bg-halo-surface-2`.

## Primitives

| Component | Notes |
|---|---|
| `Card` | `surface` \| `hero` (brand fill) — README §4 |
| `TopBar` | logo + title / micro-right — README §4 |
| `MicroLabel` | section eyebrow — README §4 |
| `StatusChip` | tones good/warn/bad/neutral — README §7 |
| `HaloBottomTabs` | visual shell, route-agnostic — README §4 |

## Deferred (do NOT add here yet)

- **TSB-zone map** — resolved 2026-05-23 to the 5-band PMC scheme.
  Source of truth lives in `webapp/src/pages/LoadDetail.tsx::TSB_ZONES`
  (mirrored on the backend via `data/utils.py:tsb_zone`); don't fork into
  a halo primitive — second source-of-truth anti-pattern.
- **Final tab IA** (Today/Plan/History/Trends/Profile) → spec **F1/F16**,
  Phase 7. `HaloBottomTabs` takes `items` as a prop until then.

## Cleanup (end of migration)

Once every screen is ported: delete the legacy palette, drop the `halo`
key in `tailwind.config.js`, rename `--color-*` → canonical, remove this
namespace prefix.
