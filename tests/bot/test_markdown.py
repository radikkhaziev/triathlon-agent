"""Unit tests for `bot.markdown.md_to_html`."""

from __future__ import annotations

import pytest

from bot.markdown import md_to_html


class TestBold:
    def test_double_star(self) -> None:
        assert md_to_html("**bold**") == "<b>bold</b>"

    def test_double_underscore(self) -> None:
        assert md_to_html("__bold__") == "<b>bold</b>"

    def test_bold_in_sentence(self) -> None:
        assert md_to_html("это **важно** сейчас") == "это <b>важно</b> сейчас"

    def test_multiple_bold_in_line(self) -> None:
        assert md_to_html("**a** and **b**") == "<b>a</b> and <b>b</b>"

    def test_bold_with_punctuation(self) -> None:
        assert md_to_html("**Серьёзно:**") == "<b>Серьёзно:</b>"


class TestItalic:
    def test_single_star(self) -> None:
        assert md_to_html("*italic*") == "<i>italic</i>"

    def test_single_underscore(self) -> None:
        assert md_to_html("_italic_") == "<i>italic</i>"

    def test_italic_does_not_match_inside_word(self) -> None:
        assert md_to_html("snake_case_word") == "snake_case_word"

    def test_bold_takes_precedence_over_italic(self) -> None:
        assert md_to_html("**bold**") == "<b>bold</b>"

    def test_lone_asterisk_passes_through(self) -> None:
        assert md_to_html("a * b") == "a * b"


class TestStrike:
    def test_basic(self) -> None:
        assert md_to_html("~~gone~~") == "<s>gone</s>"


class TestCode:
    def test_inline_code(self) -> None:
        assert md_to_html("use `foo()` here") == "use <code>foo()</code> here"

    def test_inline_code_escapes_html(self) -> None:
        assert md_to_html("`<b>raw</b>`") == "<code>&lt;b&gt;raw&lt;/b&gt;</code>"

    def test_inline_code_protects_markdown_inside(self) -> None:
        assert md_to_html("`**not bold**`") == "<code>**not bold**</code>"

    def test_fenced_code_no_lang(self) -> None:
        assert md_to_html("```\nhello\n```") == "<pre>hello\n</pre>"

    def test_fenced_code_with_lang(self) -> None:
        result = md_to_html("```python\nprint(1)\n```")
        assert result == '<pre><code class="language-python">print(1)\n</code></pre>'

    def test_fenced_code_escapes_html(self) -> None:
        assert md_to_html("```\n<tag>\n```") == "<pre>&lt;tag&gt;\n</pre>"


class TestLinks:
    def test_basic(self) -> None:
        assert md_to_html("[click](http://x.com)") == '<a href="http://x.com">click</a>'

    def test_url_with_query(self) -> None:
        result = md_to_html("[a](http://x.com?a=1&b=2)")
        assert result == '<a href="http://x.com?a=1&amp;b=2">a</a>'

    def test_https_scheme_allowed(self) -> None:
        assert md_to_html("[x](https://x.com)") == '<a href="https://x.com">x</a>'

    def test_mailto_allowed(self) -> None:
        assert md_to_html("[mail](mailto:a@b.com)") == '<a href="mailto:a@b.com">mail</a>'

    def test_tg_scheme_allowed(self) -> None:
        assert md_to_html("[u](tg://user?id=1)") == '<a href="tg://user?id=1">u</a>'

    def test_javascript_scheme_rejected(self) -> None:
        out = md_to_html("[click](javascript:alert(1))")
        assert "<a" not in out
        assert "javascript:alert(1)" in out
        assert out == "[click](javascript:alert(1))"

    def test_data_scheme_rejected(self) -> None:
        out = md_to_html("[x](data:text/html,abc)")
        assert "<a" not in out

    def test_relative_path_rejected(self) -> None:
        out = md_to_html("[x](/admin)")
        assert "<a" not in out

    def test_url_with_balanced_parens(self) -> None:
        out = md_to_html("[wiki](https://en.wikipedia.org/wiki/Foo_(bar))")
        assert out == '<a href="https://en.wikipedia.org/wiki/Foo_(bar)">wiki</a>'


class TestHeaders:
    def test_h1(self) -> None:
        assert md_to_html("# Title") == "<b>Title</b>"

    def test_h3(self) -> None:
        assert md_to_html("### Section") == "<b>Section</b>"

    def test_header_in_multiline(self) -> None:
        assert md_to_html("intro\n## H\nbody") == "intro\n<b>H</b>\nbody"

    def test_header_strips_inner_bold_markers(self) -> None:
        # Inner ** would otherwise produce nested <b><b>...</b></b>; we strip
        # bold markers since the whole header is already bold.
        assert md_to_html("# Title with **bold**") == "<b>Title with bold</b>"

    def test_header_strips_inner_underscore_bold(self) -> None:
        assert md_to_html("## H with __x__") == "<b>H with x</b>"


class TestBullets:
    def test_dash(self) -> None:
        assert md_to_html("- one\n- two") == "• one\n• two"

    def test_star_bullet(self) -> None:
        assert md_to_html("* one\n* two") == "• one\n• two"

    def test_indented_bullet(self) -> None:
        assert md_to_html("  - nested") == "  • nested"


class TestEscaping:
    def test_lt_gt_amp_escaped(self) -> None:
        assert md_to_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"

    def test_html_tags_in_input_neutralized(self) -> None:
        assert md_to_html("<script>x</script>") == "&lt;script&gt;x&lt;/script&gt;"


class TestSecurity:
    def test_null_byte_in_input_stripped(self) -> None:
        # Crafted input must not collide with internal placeholder sentinels.
        crafted = "before \x00MD0\x00 middle `real` after"
        out = md_to_html(crafted)
        assert "\x00" not in out
        assert "<code>real</code>" in out
        # The literal "MD0" stays as text since the NUL bytes were stripped.
        assert "MD0" in out

    def test_null_byte_alone(self) -> None:
        assert md_to_html("hi\x00there") == "hithere"


class TestEdgeCases:
    def test_empty(self) -> None:
        assert md_to_html("") == ""

    def test_plain_text(self) -> None:
        assert md_to_html("just some text") == "just some text"

    def test_issue_330_repro(self) -> None:
        src = (
            "😄 Классика жанра!\n\n"
            "**Серьёзно:**\n"
            "- **«Без сил в выходные»** — частая жалоба\n"
            "- Восстановление важнее"
        )
        out = md_to_html(src)
        assert "<b>Серьёзно:</b>" in out
        assert "<b>«Без сил в выходные»</b>" in out
        assert "• " in out
        assert "**" not in out

    def test_mixed_bold_italic_code(self) -> None:
        out = md_to_html("**a** _b_ `c`")
        assert out == "<b>a</b> <i>b</i> <code>c</code>"

    def test_code_block_preserves_md_inside(self) -> None:
        src = "before\n```\n**not bold**\n```\nafter"
        out = md_to_html(src)
        assert "**not bold**" in out
        assert "<pre>" in out

    def test_triple_asterisk_renders_as_bold_italic(self) -> None:
        out = md_to_html("***wow***")
        # `**` consumed first → `<b>*wow*</b>`; remaining `*..*` is italic.
        # Order of nesting may vary but both tags must be present.
        assert "<b>" in out
        assert "<i>" in out
        assert "wow" in out
        assert "*" not in out


@pytest.mark.parametrize(
    "src,expected",
    [
        ("hi", "hi"),
        ("**x**", "<b>x</b>"),
        ("_x_", "<i>x</i>"),
        ("`x`", "<code>x</code>"),
        ("# x", "<b>x</b>"),
        ("- x", "• x"),
    ],
)
def test_smoke(src: str, expected: str) -> None:
    assert md_to_html(src) == expected
