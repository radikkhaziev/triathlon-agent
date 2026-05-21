"""Unit tests for the weekly-report markdown extractors.

``extract_weekly_preview`` — the prose-preview helper. Powers the
``/api/weekly-reports`` list cards. It must:
- target the «📊 Итог недели» paragraph when present at line-start
- ignore inline ``📊`` mentions mid-paragraph (false-anchor guard)
- fall back to the first non-heading paragraph when the anchor is absent
- strip bold/italic markdown so previews read cleanly in any context
- truncate at a word boundary with an ellipsis so we never overflow the cap

``extract_weekly_headline`` — the short-title helper. Pulls the leading ``# ``
H1 the weekly prompt now emits; ``None`` for legacy reports without one.
"""

from data.weekly_preview import extract_weekly_headline, extract_weekly_preview


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


class TestHeadline:
    """``extract_weekly_headline`` — only a *leading* ``# `` H1 counts; legacy
    reports written before the prompt change return ``None`` so the API can
    fall back to ``extract_weekly_preview``."""

    def test_extracts_leading_h1(self):
        md = "# Брик-блок, подводка к гонке\n\n📊 **Итог недели**\n\nВыполнено 5 из 5."
        assert extract_weekly_headline(md) == "Брик-блок, подводка к гонке"

    def test_tolerates_leading_blank_lines(self):
        md = "\n\n# Recovery week\n\nbody"
        assert extract_weekly_headline(md) == "Recovery week"

    def test_strips_bold_inside_headline(self):
        md = "# **Threshold** focus\n\nbody"
        assert extract_weekly_headline(md) == "Threshold focus"

    def test_strips_atx_closing_hashes(self):
        """``# Title #`` — ATX-closed form; the trailing run is dropped."""
        md = "# Big bike block #\n\nbody"
        assert extract_weekly_headline(md) == "Big bike block"

    def test_strips_markdown_link(self):
        """A link would otherwise render its raw ``[...](...)`` syntax in the
        plain-text card title — backstop for an unlikely model output."""
        md = "# [Brick block](http://x) tune-up\n\nbody"
        assert extract_weekly_headline(md) == "Brick block tune-up"

    def test_legacy_report_without_h1_returns_none(self):
        # Pre-prompt-change report — starts straight with the 📊 section.
        md = "📊 **Итог недели**\n\nВыполнено 5 из 5."
        assert extract_weekly_headline(md) is None

    def test_hash_without_space_is_not_a_headline(self):
        # ``#tag`` — no space after ``#``, not an ATX H1.
        assert extract_weekly_headline("#notaheading\n\nbody") is None

    def test_mid_body_heading_is_not_picked_up(self):
        # Only a *leading* H1 counts — a ``# `` deeper in the doc is ignored.
        md = "📊 Итог\n\nтекст\n\n# Late heading\n\nещё"
        assert extract_weekly_headline(md) is None

    def test_empty_h1_returns_none(self):
        assert extract_weekly_headline("# \n\nbody") is None
