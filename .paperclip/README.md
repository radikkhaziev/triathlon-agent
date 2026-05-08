# Triathlon Agent — Paperclip Company

Markdown package describing the AI development team for the [`triathlon-agent`](https://github.com/radikkhaziev/triathlon-agent) project, conformant to the [Agent Companies specification](https://agentcompanies.io/specification) (`agentcompanies/v1`).

This is **not** the triathlon coaching product itself — that's the rest of the repo (`bot/`, `api/`, `data/`, `mcp_server/`, `webapp/`). This package describes the **team that develops it** when running under [Paperclip](https://github.com/paperclipai/paperclip) orchestration.

## Workflow

Pipeline pattern with manual release gate:

```
Radik (board)
  ▼
CEO ── intake, prioritization, human-handoff coordination
  ▼
Tech Lead ── decomposition, worktree, /spec gate, drives review chain
  ▼
Worktree (Claude Code main session) ── actual implementation
  ▼  uses .claude/agents/* and .claude/skills/* directly from repo
Reviewer chain: code-reviewer → optional migration/security-reviewer → Copilot
  ▼
Radik ── final human review, merge feat-PR into dev

Release (manual, no paperclip):
  Radik decides timing → opens dev → main PR himself → merges
  push to main triggers existing .github/workflows/deploy.yml
```

Full architectural rationale and Phase plan: see [`docs/PAPERCLIP_SETUP_SPEC.md`](../docs/PAPERCLIP_SETUP_SPEC.md) at repo root.

## Org chart

| Agent | Title | Reports to | Adapter | Skills | Trigger |
|---|---|---|---|---|---|
| `ceo` | Chief Executive Officer | — (board) | `claude_local` | `triathlon-dev`, `github-workflow` | Heartbeat + board messages |
| `tech-lead` | Engineering Tech Lead | `ceo` | `claude_local` | `triathlon-dev`, `spec`, `pr-review-chain`, `github-workflow` | CEO delegation, worktree events |

No Release Manager — release to `main` is fully manual, owned by the board user. No cron, no routines block in `.paperclip.yaml`.

Read-only reviewer agents (`code-reviewer`, `security-reviewer`, `migration-reviewer`, `spec-curator`, `architecture-advisor`, `unit-test-writer`) are **not** registered as paperclip employees — they live in `.claude/agents/` and are invoked from inside the worktree main session via Claude Code's `Agent` tool. Bringing them up to paperclip company level would duplicate the abstraction.

## Getting Started

This package imports into a running Paperclip instance via the CLI:

```bash
# On the paperclip server (or wherever paperclipai CLI is installed)
npx paperclipai company import \
  https://github.com/radikkhaziev/triathlon-agent/tree/dev/.paperclip \
  --target new \
  --new-company-name "Triathlon Agent" \
  --ref dev \
  --yes
```

Or from a local checkout:

```bash
cd ~/triathlon-agent && git pull origin dev
npx paperclipai company import ./.paperclip \
  --target new \
  --new-company-name "Triathlon Agent" \
  --yes
```

Auth: `paperclipai company *` commands require board access. If the server is headless and `xdg-open` fails, either tunnel `:3100` (`ssh -L 3100:localhost:3100 paperclip@server`) and approve the CLI auth URL in your local browser, or pass `--api-key <board-token>` directly with a token created in the Paperclip UI.

After import:

1. **Set secrets in Paperclip UI** (Agent → Inputs → Env): `GH_TOKEN` for both `ceo` and `tech-lead` (both required — see `Heartbeat cadence` and `Skills` notes below for why).
2. **Set heartbeat cadence** (see next section).
3. **Enable agents.**

## Heartbeat cadence (UI-only, document any changes here)

The Agent Companies spec (`agentcompanies/v1`) doesn't carry a standard per-agent heartbeat field, and `.paperclip.yaml` in this package doesn't pin one either. Cadence lives **in the Paperclip UI** and is **lost on re-import** unless you record it. Current values:

| Agent | Cadence | Why |
|---|---|---|
| `ceo` | Every 30 min | Frequent enough to chase the human-review queue; cheap enough not to burn budget on no-op heartbeats |
| `tech-lead` | Event-driven only (no fixed interval) | Triggered by CEO delegation and worktree status changes, not by clock |

If you change these values in the UI, update this table in the same commit so re-import after a wipe doesn't drift the contract.

## Skills

The agents reference skills by shortname in their `AGENTS.md` frontmatter:

```yaml
skills:
  - triathlon-dev
  - spec
  - pr-review-chain
  - github-workflow
```

Per `agentcompanies/v1` §8, paperclip's importer tries to resolve those names against `skills/<shortname>/SKILL.md` inside the company package — and **this package intentionally vendors nothing**. The actual skill content reaches the agent through a different path: Claude Code (the runtime spawned by the `claude_local` adapter) natively scans `.claude/skills/` in its working directory at startup. Since each session — both the paperclip-orchestration sessions (CEO, Tech Lead) and the worktree sessions — runs inside a checkout of this repo, `.claude/skills/{triathlon-dev,spec,pr-review-chain,github-workflow}` is found and loaded by Claude Code itself.

Net effect: the `skills:` frontmatter field is **documentation of intent**; the actual loading is done by Claude Code's native skill discovery, not by paperclip's resolver. If a skill is missing from `.claude/skills/`, the agent simply won't have it — no error from paperclip's side.

Same logic applies to `.claude/agents/` reviewers (`code-reviewer`, `security-reviewer`, etc.) — those are Claude Code subagents reachable via the `Agent` tool, only resolvable inside a session whose cwd contains `.claude/agents/`. Tech Lead does **not** invoke them directly; the worktree session does, on Tech Lead's instruction.

## Directory layout

```
.paperclip/
├── COMPANY.md                ← entry-point, schema=agentcompanies/v1
├── agents/
│   ├── ceo/AGENTS.md
│   └── tech-lead/AGENTS.md
├── .paperclip.yaml           ← Paperclip vendor extension (adapter + env)
├── README.md                 ← this file
└── LICENSE
```

Skills are referenced by shortname in each `AGENTS.md`; they resolve to `.claude/skills/<shortname>/SKILL.md` in the same repo. We do **not** vendor skill content into this package — Claude Code in the worktree gets them directly.

## References

- [`docs/PAPERCLIP_SETUP_SPEC.md`](../docs/PAPERCLIP_SETUP_SPEC.md) — full architecture, git-flow, review chain, phase plan
- [Agent Companies specification](https://agentcompanies.io/specification) — base format
- [Paperclip](https://github.com/paperclipai/paperclip) — orchestrator runtime
- [`CLAUDE.md`](../CLAUDE.md) — project context for the *product* (not this package)
