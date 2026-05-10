"""Unit tests for ``extract_weekly_preview`` — the headline-extraction helper.

Powers the ``/api/weekly-reports`` list cards (PR2) and, eventually, the
Sunday actor's chat preview (parked until PR3 webapp route exists). It must:
- target the «📊 Итог недели» paragraph when present at line-start
- ignore inline ``📊`` mentions mid-paragraph (false-anchor guard)
- fall back to the first non-heading paragraph when the anchor is absent
- strip bold/italic markdown so previews read cleanly in any context
- truncate at a word boundary with an ellipsis so we never overflow the cap
"""

from data.weekly_preview import extract_weekly_preview


class TestAnchorPath:
    """Happy path: the «📊» heading is followed by a normal paragraph."""

    def test_pulls_first_paragraph_after_anchor(self):
        md = (
            "Все данные собраны.\n\n"
            "---\n\n"
            "📊 **Итог недели (4–10 мая)**\n\n"
            "Выполнено **12 из 20** тренировок, compliance **55%**.\n\n"
            "💚 **Восстановление**\n\n"
            "HRV стабилен."
        )
        preview = extract_weekly_preview(md)
        # Bold markers stripped; only the targeted paragraph included.
        assert preview == "Выполнено 12 из 20 тренировок, compliance 55%."

    def test_strips_italic_markdown(self):
        md = "📊 **Итог**\n\nКомпланс *низкий* на этой неделе."
        assert extract_weekly_preview(md) == "Компланс низкий на этой неделе."


class TestAnchorMustBeAtLineStart:
    """``📊`` only counts as the section anchor when it leads a line. An
    inline mention in body text (e.g. an athlete's note Claude echoed) must
    not hijack the preview — review L1 / H2."""

    def test_inline_emoji_does_not_hijack_anchor(self):
        md = "Введение со встроенным символом 📊 посреди фразы.\n\n" "Второй абзац здесь."
        # No line-start 📊 — fallback path picks the head paragraph,
        # NOT a slice starting at the inline emoji.
        assert extract_weekly_preview(md).startswith("Введение со встроенным")

    def test_bold_marker_before_emoji_still_anchors(self):
        """``**📊 Итог**`` — Claude sometimes emits the entire heading bolded.
        The character class in the anchor regex permits leading ``*`` so the
        match should hit; this test pins that intent so a future regex
        tightening doesn't silently drop the case."""
        md = "**📊 Итог недели**\n\nВыполнено 12 из 20 тренировок."
        assert extract_weekly_preview(md) == "Выполнено 12 из 20 тренировок."

    def test_md_heading_with_emoji_anchors(self):
        """``## 📊 Итог`` — markdown ``#`` heading prefix should also count
        as a leader (the anchor regex permits ``#`` in its char class)."""
        md = "## 📊 Итог\n\nКомпланс 55%."
        assert extract_weekly_preview(md) == "Компланс 55%."


class TestFallbackPath:
    """Anchor absent → first non-heading paragraph. Skips leading ``#``
    headings, ``---``/``===`` dividers, blank lines (review H2 / L4)."""

    def test_no_anchor_uses_head(self):
        md = "Краткий отчёт без секций.\n\nВторой абзац."
        assert extract_weekly_preview(md) == "Краткий отчёт без секций."

    def test_skips_leading_heading(self):
        """Without skip-headings, preview rendered as «# Weekly summary» —
        useless to the athlete (review H2)."""
        md = "# Weekly summary\n\nSessions: 12 of 20."
        assert extract_weekly_preview(md) == "Sessions: 12 of 20."

    def test_skips_horizontal_rule_and_blank_lines(self):
        md = "\n\n---\n\nВыполнено 12 из 20."
        assert extract_weekly_preview(md) == "Выполнено 12 из 20."

    def test_anchor_with_heading_only_returns_placeholder(self):
        """Anchor matched but the document has nothing after — return a
        dash placeholder rather than an empty preview that would render as
        a blank notification (review L4)."""
        md = "📊 **Итог**"
        assert extract_weekly_preview(md) == "—"


class TestTruncation:
    def test_truncates_at_word_boundary_with_ellipsis(self):
        long_word_stream = "📊 **Итог**\n\n" + " ".join(["слово"] * 200)
        preview = extract_weekly_preview(long_word_stream, max_chars=50)
        assert len(preview) <= 51  # +1 for ellipsis char
        assert preview.endswith("…")
        # No partial-word breaks before the ellipsis (last token must be «слово», not a fragment)
        body = preview[:-1].rstrip()
        assert body.split(" ")[-1] == "слово"

    def test_short_input_returned_as_is(self):
        md = "📊 **Итог**\n\nКоротко."
        assert extract_weekly_preview(md, max_chars=200) == "Коротко."
        # No trailing ellipsis when under the cap.
        assert not extract_weekly_preview(md, max_chars=200).endswith("…")
