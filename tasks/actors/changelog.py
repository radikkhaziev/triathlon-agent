"""Weekly changelog publisher — see docs/WEEKLY_CHANGELOG_SPEC.md.

Sunday 15:00 Belgrade: fetch merged PRs from the last 7 days, pre-filter the
clearly-internal ones, ask Claude to translate the rest into 3-7 athlete-facing
bullets, publish as a GitHub Discussion in the Announcements category.

The actor is intentionally fail-soft: any GitHub/Claude/network error is logged
(and reported to Sentry) but never re-raised — a missed weekly digest is far
less harmful than a Dramatiq retry storm hammering GitHub.
"""

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import anthropic
import dramatiq
import httpx
import sentry_sdk

from config import settings
from data.github import LATEST_DISCUSSION_QUERY

logger = logging.getLogger(__name__)

GITHUB_REST_BASE = "https://api.github.com"
GITHUB_GRAPHQL = "https://api.github.com/graphql"

CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_MAX_TOKENS = 800
CLAUDE_TEMPERATURE = 0.3

# Sentinel returned by Claude when every PR in the input is internal.
NO_USER_FACING_CHANGES = "NO_USER_FACING_CHANGES"

# perf|style|refactor are deliberately NOT in the hard-drop list — see spec §4.
# Claude's "only what the athlete notices" rule (§5) filters them at the second
# stage; missing a perf-improvement bullet is a worse error than ~$0.02/week
# of extra Claude tokens on the rare borderline PR.
INTERNAL_TITLE_RE = re.compile(r"^(chore|ci|build|test|docs):", re.IGNORECASE)

SKIP_AUTHORS = frozenset({"dependabot[bot]", "github-actions[bot]", "renovate[bot]"})
SKIP_LABELS = frozenset({"skip-changelog", "internal", "dependencies"})

# Spec §5 — bumped from 500 to 1500 chars. Our PR bodies follow a "What was
# done / How to verify" template; 500 chars cut the second block, which is
# exactly where Claude reads user-facing impact from.
PR_BODY_MAX_CHARS = 1500
PR_BODY_TRUNC_SUFFIX = "... [truncated]"

# >100 PRs/week → top-50 by merged_at desc. Caps worst-case Claude input at
# 50 × 1500 ≈ 19k chars and protects us from a hypothetical merge spree.
MAX_PRS_FOR_CLAUDE = 50

PROMPT_TEMPLATE = """Ты пишешь краткую сводку обновлений для триатлета (не разработчика).
Прочитай список merged PR'ов за неделю и выдай 3-7 буллетов на русском.

Правила:
- Только то что атлет ЗАМЕТИТ в боте или web (новые фичи, изменённый UX,
  исправленные баги).
- Пропусти рефакторинг, миграции, тех-долг, обновления зависимостей,
  внутренние улучшения промптов.
- Сгруппируй по темам — заголовок секции + 1-3 буллета под ним.
  Используй emoji в заголовках (🎯 Цели, 🧪 Тесты, 📊 Отчёты, 🔌
  Onboarding, 🐛 Багфиксы — если подходит). Группа должна быть
  непустой — не пиши заголовок без буллетов.
- 1 буллет = 1 предложение, активный залог («Теперь можно X»,
  «Исправили Y»).
- НЕ упоминай PR-номера, имена файлов, классы, функции, миграции.
- Если все PR'ы — внутренние улучшения, верни РОВНО строку:
  NO_USER_FACING_CHANGES
- Не добавляй введение, заключение, технические подробности.

PR'ы за неделю (отсортированы newest-first):

{pr_block}
"""


@dataclass(frozen=True)
class MergedPR:
    """Slim PR projection — only the fields we feed into pre-filter and Claude."""

    number: int
    title: str
    body: str
    url: str
    author: str
    author_type: str  # "User" | "Bot" | "Organization" — REST `user.type`
    labels: tuple[str, ...]
    merged_at: datetime  # tz-aware UTC
    base_ref: str


# --------------------------------------------------------------------------- #
# 1. Fetch
# --------------------------------------------------------------------------- #


def fetch_merged_prs(repo: str, since: datetime, *, token: str) -> list[MergedPR]:
    """REST `pulls?state=closed&base=main&sort=updated&direction=desc&per_page=100`.

    GitHub doesn't expose a "merged after X" filter — we paginate `state=closed`
    sorted by `updated_at desc` and stop as soon as we hit a PR updated before
    `since` (anything older was last touched outside the window).
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"{GITHUB_REST_BASE}/repos/{repo}/pulls"
    params = {
        "state": "closed",
        "base": "main",
        "sort": "updated",
        "direction": "desc",
        "per_page": 100,
    }
    out: list[MergedPR] = []
    with httpx.Client(timeout=30) as client:
        page = 1
        while True:
            resp = client.get(url, params={**params, "page": page}, headers=headers)
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break

            stop = False
            for pr in batch:
                updated = _parse_iso(pr["updated_at"])
                if updated < since:
                    # Sorted by updated_at desc → everything past this is older
                    stop = True
                    break
                merged_at_raw = pr.get("merged_at")
                if not merged_at_raw:
                    continue  # closed but never merged
                merged_at = _parse_iso(merged_at_raw)
                if merged_at < since:
                    continue  # merged before window (rare — touched but not merged inside it)
                user = pr.get("user") or {}
                out.append(
                    MergedPR(
                        number=pr["number"],
                        title=pr.get("title") or "",
                        body=pr.get("body") or "",
                        url=pr["html_url"],
                        author=user.get("login") or "",
                        author_type=user.get("type") or "User",
                        labels=tuple(lbl["name"] for lbl in pr.get("labels") or []),
                        merged_at=merged_at,
                        base_ref=(pr.get("base") or {}).get("ref") or "",
                    )
                )
            if stop or len(batch) < 100:
                break
            page += 1
    return out


def _parse_iso(s: str) -> datetime:
    # GitHub returns ISO with trailing Z; fromisoformat handles "+00:00" only.
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _is_within_window(iso_ts: str, since: datetime) -> bool:
    """True if ``iso_ts`` (GitHub UTC ISO) is at or after ``since``."""
    return _parse_iso(iso_ts) >= since


# --------------------------------------------------------------------------- #
# 2. Pre-filter
# --------------------------------------------------------------------------- #


def prefilter_prs(prs: list[MergedPR]) -> list[MergedPR]:
    """Drop authors/title-prefixes/labels/non-main; dedup by (title, body[:200]).

    Spec §4. The dedup key combines normalized title with a hash of body[:200]
    so stacked PRs (same title, different bodies) survive while accidental
    re-merges (POC: #318/#320 — byte-identical title and body) collapse.
    """
    kept: dict[tuple[str, str], MergedPR] = {}
    for pr in prs:
        if pr.base_ref != "main":
            continue
        # Belt + suspenders bot filter (spec §3 line 80) — explicit allowlist
        # catches the well-known names; ``author_type == "Bot"`` catches new
        # bots (mergify, imgbot, pre-commit-ci, …) without per-bot churn.
        if pr.author_type == "Bot" or pr.author in SKIP_AUTHORS:
            continue
        if INTERNAL_TITLE_RE.match(pr.title):
            continue
        if any(lbl in SKIP_LABELS for lbl in pr.labels):
            continue
        key = _dedup_key(pr)
        existing = kept.get(key)
        if existing is None or pr.merged_at > existing.merged_at:
            kept[key] = pr
    # Newest-first for the prompt block.
    return sorted(kept.values(), key=lambda p: p.merged_at, reverse=True)


def _dedup_key(pr: MergedPR) -> tuple[str, str]:
    title_norm = pr.title.lower().strip()
    body_hash = hashlib.sha1(pr.body[:200].encode("utf-8", errors="replace")).hexdigest()[:8]
    return (title_norm, body_hash)


# --------------------------------------------------------------------------- #
# 3. Build Claude prompt
# --------------------------------------------------------------------------- #


def build_prompt(prs: list[MergedPR]) -> str:
    """Assemble the user message — title/url/body[:1500] per PR, top-50 cap."""
    if len(prs) > MAX_PRS_FOR_CLAUDE:
        # Operator-visible signal — a merge spree this large is rare and worth
        # noticing in case the digest mysteriously misses recent PRs.
        logger.warning(
            "Weekly changelog: capping %d PRs to top %d by merged_at desc",
            len(prs),
            MAX_PRS_FOR_CLAUDE,
        )
    capped = prs[:MAX_PRS_FOR_CLAUDE]
    blocks = []
    for idx, pr in enumerate(capped, start=1):
        body = pr.body or ""
        if len(body) > PR_BODY_MAX_CHARS:
            body = body[:PR_BODY_MAX_CHARS] + PR_BODY_TRUNC_SUFFIX
        blocks.append(f'[{idx}] title: "{pr.title}"\n    url: {pr.url}\n    body: """\n{body}\n"""')
    return PROMPT_TEMPLATE.format(pr_block="\n\n".join(blocks))


# --------------------------------------------------------------------------- #
# 4. Claude call
# --------------------------------------------------------------------------- #


def call_claude(prompt: str) -> str:
    """Returns Claude's text or the NO_USER_FACING_CHANGES sentinel.

    Caller treats any exception as «skip publish» — we don't retry next-day
    or fall back to a hand-crafted message; missing one weekly digest is fine.
    """
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY.get_secret_value())
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=CLAUDE_MAX_TOKENS,
        temperature=CLAUDE_TEMPERATURE,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


# --------------------------------------------------------------------------- #
# 5. Discussion publish
# --------------------------------------------------------------------------- #

_RU_MONTHS_GENITIVE = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}


def build_discussion_title(week_start: datetime, week_end: datetime) -> str:
    """`✨ Что нового — неделя 03–09 мая 2026` per spec §6."""
    same_month = week_start.month == week_end.month
    month_name = _RU_MONTHS_GENITIVE[week_end.month]
    if same_month:
        return f"✨ Что нового — неделя {week_start.day:02d}–{week_end.day:02d} {month_name} {week_end.year}"
    start_month = _RU_MONTHS_GENITIVE[week_start.month]
    return (
        f"✨ Что нового — неделя {week_start.day:02d} {start_month} – "
        f"{week_end.day:02d} {month_name} {week_end.year}"
    )


def build_discussion_body(claude_output: str, repo: str, since: datetime) -> str:
    """Wrap Claude output with the standard header/footer (§6)."""
    since_iso = since.date().isoformat()
    pulls_url = f"https://github.com/{repo}/pulls" f"?q=is%3Apr+merged%3A%3E%3D{since_iso}+base%3Amain"
    return (
        "> Сводка изменений за неделю. Сгенерирована автоматически из merged PR'ов\n"
        "> в `main` (пропущены рефакторинги, миграции, обновления зависимостей).\n\n"
        f"{claude_output}\n\n"
        "---\n\n"
        f"[Полный список merged PR'ов за неделю →]({pulls_url})\n"
    )


def fetch_latest_discussion(*, repo: str, category_id: str, token: str) -> dict | None:
    """Sync wrapper around the same GraphQL the FastAPI endpoint uses.

    Returns ``{"url", "title", "created_at"}`` (UTC ISO from GitHub) or
    ``None`` if no Discussion exists yet. Raises on transport failure — the
    caller decides whether to skip or fall through.
    """
    owner, _, name = repo.partition("/")
    payload = {
        "query": LATEST_DISCUSSION_QUERY,
        "variables": {"categoryId": category_id, "owner": owner, "name": name},
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(timeout=15) as client:
        resp = client.post(GITHUB_GRAPHQL, json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    nodes = data["data"]["repository"]["discussions"]["nodes"]
    if not nodes:
        return None
    n = nodes[0]
    return {"url": n["url"], "title": n["title"], "created_at": n["createdAt"]}


def create_discussion(*, repo_id: str, category_id: str, title: str, body: str, token: str) -> dict:
    """GraphQL `createDiscussion` mutation. Returns `{number, url, title}` on success."""
    mutation = """
    mutation($repoId: ID!, $categoryId: ID!, $title: String!, $body: String!) {
      createDiscussion(input: {
        repositoryId: $repoId,
        categoryId: $categoryId,
        title: $title,
        body: $body
      }) {
        discussion { number url title }
      }
    }
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "query": mutation,
        "variables": {
            "repoId": repo_id,
            "categoryId": category_id,
            "title": title,
            "body": body,
        },
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post(GITHUB_GRAPHQL, json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]["createDiscussion"]["discussion"]


# --------------------------------------------------------------------------- #
# Entry point — usable both from the actor and from CLI debug command.
# --------------------------------------------------------------------------- #


def publish_weekly_changelog(*, force: bool = False) -> dict:
    """Run the full pipeline once. Returns `{status, ...}` dict — never raises.

    Idempotent by week: if a Discussion was created within the last 8 days,
    we skip (and the Sun 15:00 cron sees a Wed manual ``publish-changelog``
    and gracefully steps aside). Pass ``force=True`` to override — used by
    the CLI when the owner really wants a second digest in the same week.

    Status values:
        - "skipped_disabled"          — env vars not configured.
        - "skipped_already_published" — fresh Discussion exists; payload has `existing`.
        - "skipped_no_prs"            — 0 merged PRs in the window.
        - "skipped_all_filtered"      — pre-filter dropped everything.
        - "skipped_internal"          — Claude returned NO_USER_FACING_CHANGES.
        - "skipped_error"             — GitHub/Claude failure (logged + Sentry).
        - "published"                 — Discussion created; payload includes `discussion`.
    """
    if not settings.CHANGELOG_REPO_ID or not settings.CHANGELOG_DISCUSSION_CATEGORY_ID:
        logger.info("Weekly changelog disabled: CHANGELOG_REPO_ID or CHANGELOG_DISCUSSION_CATEGORY_ID empty")
        return {"status": "skipped_disabled"}

    token = settings.GITHUB_TOKEN.get_secret_value()
    if not token:
        logger.info("Weekly changelog disabled: GITHUB_TOKEN empty")
        return {"status": "skipped_disabled"}

    # Guard against burning a GitHub fetch when Claude key is missing — failure
    # would surface only after pre-filter runs.
    if not settings.ANTHROPIC_API_KEY.get_secret_value():
        logger.info("Weekly changelog disabled: ANTHROPIC_API_KEY empty")
        return {"status": "skipped_disabled"}

    now = datetime.now(timezone.utc)
    # 8d (not 7d) is intentional. The cron fires Sun 15:00; the owner sometimes
    # merges PRs Sun afternoon AFTER the cron has already run. With a strict 7d
    # window those PRs would only be picked up by NEXT Sunday's digest — by
    # which point the «what shipped this week» framing has decayed. A +1d
    # buffer pulls the previous Saturday afternoon onward into scope so the
    # owner can keep merging on Sunday without pushing to Monday.
    # Cost: a 24h overlap with last week's window — a PR merged Sat 16:00 can
    # appear in BOTH last week's and this week's digest. Acceptable: Claude
    # de-duplicates on its own, and the owner can always patch the Discussion
    # by hand within the 4h buffer to the 19:00 weekly report.
    since = now - timedelta(days=8)
    # Title shows the 7-day Mon-Sun window (publish day = Sunday, week_start =
    # 6 days back). ``since`` widens to -8d so Sun-afternoon merges from the
    # previous week (after the previous cron) still surface in this digest.
    week_end = now
    week_start = now - timedelta(days=6)

    # Weekly idempotency: a manual `publish-changelog` mid-week creates a
    # Discussion; the Sun cron then finds it and skips. ``force=True`` overrides
    # for the rare case the owner wants a second digest in the same week.
    #
    # The window MUST be strictly shorter than the cron period (7d). It used to
    # be ``now - 7d 12h`` — WIDER than the gap between two consecutive Sunday
    # runs, so every Sunday caught the *previous* Sunday's Discussion (~7d old)
    # as "already published" and skipped. The digest silently degraded to
    # biweekly. Real incident: #338 created Sun 07:06Z made the next Sunday's
    # 13:00Z run see it 7d6h old (< 7d12h) → suppressed. Anchoring to
    # ``week_start`` (now - 6d, the digest's own Mon-Sun window) keeps a full
    # ~1-day margin over cron jitter while still catching a same-week manual
    # run. Trade-off: a manual run >6d before the Sunday cron (rare — would
    # need a Mon-or-earlier manual publish) could double-publish; acceptable
    # and recoverable by hand within the 4h buffer to the 19:00 weekly report.
    idempotency_since = week_start
    if not force:
        try:
            latest = fetch_latest_discussion(
                repo=settings.GITHUB_REPO,
                category_id=settings.CHANGELOG_DISCUSSION_CATEGORY_ID,
                token=token,
            )
        except Exception as exc:
            # Don't crash the actor if the lookup fails — fall through to
            # publish. Worst case: a duplicate Discussion that week, which
            # is recoverable (delete via gh).
            logger.warning("Weekly changelog: idempotency lookup failed (%s) — proceeding", exc)
            latest = None
        if latest and _is_within_window(latest["created_at"], idempotency_since):
            logger.info("Weekly changelog: skipped — fresh Discussion already exists at %s", latest["url"])
            return {"status": "skipped_already_published", "existing": latest}

    try:
        prs = fetch_merged_prs(settings.GITHUB_REPO, since, token=token)
    except Exception as exc:
        logger.error("Weekly changelog: GitHub fetch failed: %s", exc)
        sentry_sdk.capture_exception(exc)
        return {"status": "skipped_error", "stage": "fetch", "error": str(exc)}

    if not prs:
        logger.info("Weekly changelog: no merged PRs in last 7 days")
        return {"status": "skipped_no_prs"}

    filtered = prefilter_prs(prs)
    if not filtered:
        logger.info("Weekly changelog: all %d PRs filtered out by pre-filter", len(prs))
        return {"status": "skipped_all_filtered", "fetched": len(prs)}

    prompt = build_prompt(filtered)

    try:
        claude_text = call_claude(prompt)
    except Exception as exc:
        logger.error("Weekly changelog: Claude call failed: %s", exc)
        sentry_sdk.capture_exception(exc)
        return {"status": "skipped_error", "stage": "claude", "error": str(exc)}

    if claude_text.strip() == NO_USER_FACING_CHANGES:
        logger.info("Weekly changelog: Claude says no user-facing changes (kept %d PRs after filter)", len(filtered))
        return {"status": "skipped_internal", "kept": len(filtered)}

    title = build_discussion_title(week_start, week_end)
    body = build_discussion_body(claude_text, settings.GITHUB_REPO, since)

    try:
        discussion = create_discussion(
            repo_id=settings.CHANGELOG_REPO_ID,
            category_id=settings.CHANGELOG_DISCUSSION_CATEGORY_ID,
            title=title,
            body=body,
            token=token,
        )
    except Exception as exc:
        logger.error("Weekly changelog: Discussion creation failed: %s", exc)
        sentry_sdk.capture_exception(exc)
        return {"status": "skipped_error", "stage": "publish", "error": str(exc)}

    logger.info("Weekly changelog: published Discussion #%d %s", discussion["number"], discussion["url"])
    return {"status": "published", "discussion": discussion, "kept": len(filtered)}


# --------------------------------------------------------------------------- #
# Dramatiq actor — thin wrapper around publish_weekly_changelog().
# --------------------------------------------------------------------------- #


@dramatiq.actor(queue_name="default", max_retries=0)
def actor_publish_weekly_changelog() -> None:
    """Sunday 15:00 cron entry point. ``max_retries=0`` — see spec §13:
    a missed digest is acceptable; we'd rather skip a week than retry-storm GitHub.

    No ``force`` arg — the cron always respects weekly idempotency. Manual
    re-publish goes through ``python -m cli publish-changelog --force``.
    """
    publish_weekly_changelog()
