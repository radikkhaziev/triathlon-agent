"""Tests for mcp_server/tools/exercise_guidelines.py."""


class TestGetAnimationGuidelines:
    """get_animation_guidelines returns structured guidelines text."""

    def test_returns_non_empty_string(self):
        # The underlying GUIDELINES constant (sync access)
        from mcp_server.tools.exercise_guidelines import GUIDELINES

        assert isinstance(GUIDELINES, str)
        assert len(GUIDELINES) > 100

    def test_contains_color_palette_section(self):
        from mcp_server.tools.exercise_guidelines import GUIDELINES

        assert "Color Palette" in GUIDELINES

    def test_contains_stick_figure_anatomy_section(self):
        from mcp_server.tools.exercise_guidelines import GUIDELINES

        assert "Stick Figure Anatomy" in GUIDELINES

    def test_contains_animation_rules_section(self):
        from mcp_server.tools.exercise_guidelines import GUIDELINES

        assert "Animation Rules" in GUIDELINES

    def test_contains_key_colors(self):
        from mcp_server.tools.exercise_guidelines import GUIDELINES

        assert "#60a5fa" in GUIDELINES  # body blue
        assert "#34d399" in GUIDELINES  # active green

    def test_contains_viewbox_spec(self):
        from mcp_server.tools.exercise_guidelines import GUIDELINES

        assert "200 300" in GUIDELINES
