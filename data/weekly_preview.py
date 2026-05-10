"""Pure-function helper for headline extraction from weekly-report markdown.

Lives at the leaf-module layer because both the API router
(``api/routers/weekly_reports.py``) and the chat actor
(``tasks/actors/reports.py``) consume it. Without this split the router
would have to import from ``tasks.actors.reports``, which transitively
pulls dramatiq, sentry_sdk, MCPTool, and a dozen ORM models — all unused
by the formatter and a measurable cold-start cost on the API container.
"""

from __future__ import annotations

import re

_DEFAULT_MAX_CHARS = 220
# Anchor matches the «📊 Итог недели» heading. The prompt enforces this
# emoji even when ``response_language=English``, so it stays language-stable.
# Character class permits only whitespace + markdown leaders (``#``/``*``/``_``/
# ``>``/``-``) before 📊 — that's enough for ``📊 **Итог**``, ``## 📊 …``,
# and ``**📊 …``, but rejects inline mentions like ``Note: 📊 trend``.
# Without the leader-only constraint the regex would happily match any line
# containing 📊 anywhere, hijacking the preview to body text.
_ANCHOR_RE = re.compile(r"^[\s#*_>\-]*📊", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
# Lines we skip in the no-anchor fallback when scanning for the first body
# paragraph: markdown headings (``#``), horizontal rules (``---``/``===``),
# and blank lines. Without this the fallback would render the heading itself
# as the preview, leaving the athlete with «# Weekly summary» instead of
# anything informative.
_SKIPPABLE_RE = re.compile(r"^(\s*$|#+\s|-{3,}|={3,})")


def extract_weekly_preview(content_md: str, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    """Pull a short headline from the weekly markdown for previews.

    Targets the first paragraph after the «📊 Итог недели» section header —
    that's where Claude is told to put compliance %, total TSS, and the
    sessions completed/planned tally, which is exactly what an athlete would
    want to glance at before deciding to open the full report.

    Falls back to the first non-heading paragraph if the anchor isn't
    present (prompt drift, model formatting refusal). Strips bold/italic
    markdown so previews read cleanly in any HTML/plain context.
    """
    anchor = _ANCHOR_RE.search(content_md)
    if anchor is not None:
        # The anchor line is the heading; we want the *next* paragraph.
        # If there's no ``\n\n`` after the heading, the document IS the
        # heading line — return the placeholder rather than echoing
        # ``Итог`` as the preview.
        rest = content_md[anchor.end() :]
        para_break = rest.find("\n\n")
        if para_break == -1:
            return "—"
        body = rest[para_break + 2 :]
    else:
        # Skip leading headings / dividers / blank lines until we reach
        # actual prose, then take that paragraph.
        lines = content_md.splitlines()
        body_start = 0
        for i, line in enumerate(lines):
            if not _SKIPPABLE_RE.match(line):
                body_start = i
                break
        body = "\n".join(lines[body_start:])

    next_break = body.find("\n\n")
    if next_break != -1:
        body = body[:next_break]

    body = _BOLD_RE.sub(r"\1", body)
    body = _ITALIC_RE.sub(r"\1", body)
    body = body.strip()

    if not body:
        # Anchor matched but the rest of the document is empty/heading-only.
        # Better to surface a placeholder than an empty notification.
        return "—"

    if len(body) <= max_chars:
        return body

    truncated = body[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > max_chars * 0.7:
        truncated = truncated[:last_space]
    return truncated.rstrip(" .,;:…—") + "…"
