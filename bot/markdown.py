"""Markdown → Telegram-safe HTML converter.

Telegram's `parse_mode="Markdown"` (v1) accepts only `*bold*` / `_italic_` and
breaks on the standard `**bold**` Claude emits. `MarkdownV2` requires escaping
every `.`, `-`, `(`, etc. — too fragile. HTML is the most permissive parse mode:
the only special chars are `<`, `>`, `&`, and the supported tag set is small
(`<b>`, `<i>`, `<u>`, `<s>`, `<code>`, `<pre>`, `<a>`, `<blockquote>`,
`<span class="tg-spoiler">`).

This converter handles the subset Claude actually produces in chat replies.
"""

from __future__ import annotations

import re
from html import escape as html_escape

_FENCE_RE = re.compile(r"```([a-zA-Z0-9_+\-.]*)\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_STAR_RE = re.compile(r"\*\*([^*\n]+?)\*\*")
_BOLD_UND_RE = re.compile(r"__([^_\n]+?)__")
_STRIKE_RE = re.compile(r"~~([^~\n]+?)~~")
_ITALIC_STAR_RE = re.compile(r"(?<![*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\w)")
_ITALIC_UND_RE = re.compile(r"(?<![_\w])_(?!\s)([^_\n]+?)(?<!\s)_(?!\w)")
# URL part allows balanced one-level parens: `(foo)` and `(foo(bar))`. Standard
# CommonMark uses recursive balancing; one level covers Wikipedia-style URLs
# (`/wiki/Foo_(bar)`) and is enough for our needs.
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(((?:[^()\s]|\([^()\s]*\))+)\)")
_HEADER_RE = re.compile(r"^[ \t]*#{1,6}[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_BULLET_RE = re.compile(r"^([ \t]*)[-*][ \t]+", re.MULTILINE)

_PLACEHOLDER = "\x00MD{}\x00"
_SAFE_URL_SCHEMES = ("http://", "https://", "tg://", "mailto:", "ftp://", "ftps://")


def _render_link(match: re.Match[str]) -> str:
    label, url = match.group(1), match.group(2)
    if not url.lower().startswith(_SAFE_URL_SCHEMES):
        # Reject `javascript:` / `data:` / unknown schemes — render the raw
        # `[label](url)` as escaped literal text instead of an anchor. The
        # surrounding text was already HTML-escaped, so we only need to
        # rebuild the original syntax with safe punctuation.
        return f"[{label}]({url})"
    safe_url = url.replace('"', "&quot;")
    return f'<a href="{safe_url}">{label}</a>'


def md_to_html(text: str) -> str:
    """Convert Claude-style Markdown to Telegram HTML.

    Handled: ``**bold**`` / ``__bold__``, ``*italic*`` / ``_italic_``,
    ``~~strike~~``, ``` ```fenced``` ```, `` `inline` ``, ``[text](url)``,
    ``# headers`` (rendered bold), bullet lines (``- `` / ``* `` → ``• ``).
    Unknown URL schemes (e.g. ``javascript:``) are rendered as literal text
    rather than anchors. Unrecognized sequences are HTML-escaped and passed
    through literally.
    """
    if not text:
        return ""

    # `_PLACEHOLDER` uses NUL bytes as sentinels — strip them from input so a
    # crafted message can't collide with a stash slot.
    text = text.replace("\x00", "")

    blocks: list[tuple[str, str, str]] = []  # (kind, lang, body)

    def _stash_fence(m: re.Match[str]) -> str:
        idx = len(blocks)
        blocks.append(("fence", m.group(1), m.group(2)))
        return _PLACEHOLDER.format(idx)

    def _stash_inline(m: re.Match[str]) -> str:
        idx = len(blocks)
        blocks.append(("inline", "", m.group(1)))
        return _PLACEHOLDER.format(idx)

    text = _FENCE_RE.sub(_stash_fence, text)
    text = _INLINE_CODE_RE.sub(_stash_inline, text)

    text = html_escape(text, quote=False)

    # Headers run BEFORE bold/italic so we can strip redundant `**`/`__`
    # markers from the header body — the whole line is already wrapped in
    # `<b>`, no need for nested bold tags.
    text = _HEADER_RE.sub(
        lambda m: "<b>" + m.group(1).replace("**", "").replace("__", "") + "</b>",
        text,
    )
    text = _BULLET_RE.sub(r"\1• ", text)

    text = _BOLD_STAR_RE.sub(r"<b>\1</b>", text)
    text = _BOLD_UND_RE.sub(r"<b>\1</b>", text)
    text = _STRIKE_RE.sub(r"<s>\1</s>", text)
    text = _ITALIC_STAR_RE.sub(r"<i>\1</i>", text)
    text = _ITALIC_UND_RE.sub(r"<i>\1</i>", text)
    text = _LINK_RE.sub(_render_link, text)

    for i, (kind, lang, body) in enumerate(blocks):
        ph = _PLACEHOLDER.format(i)
        body_esc = html_escape(body, quote=False)
        if kind == "fence":
            if lang:
                replacement = f'<pre><code class="language-{lang}">{body_esc}</code></pre>'
            else:
                replacement = f"<pre>{body_esc}</pre>"
        else:
            replacement = f"<code>{body_esc}</code>"
        text = text.replace(ph, replacement)

    return text
