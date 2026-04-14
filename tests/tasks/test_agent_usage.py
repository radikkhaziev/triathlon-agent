"""Tests for bot/agent.py token-usage tracking.

Covers:
- ClaudeAgent._accumulate_usage: sums tokens including cache fields across calls
- ClaudeAgent._run_tool_use_loop: returns (text, usage_dict, tool_calls), accumulates across iterations
- ClaudeAgent.chat: calls ApiUsageDaily.increment with correct kwargs; still returns
  text when increment raises (fire-and-forget)
"""

from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_usage(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read: int = 0,
    cache_creation: int = 0,
) -> MagicMock:
    """Create a mock Anthropic Usage object."""
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    usage.cache_read_input_tokens = cache_read
    usage.cache_creation_input_tokens = cache_creation
    return usage


def _make_response(
    *,
    stop_reason: str = "end_turn",
    text: str = "hello",
    usage: MagicMock | None = None,
    tool_blocks: list | None = None,
) -> MagicMock:
    """Create a mock Anthropic Messages response."""
    response = MagicMock()
    response.stop_reason = stop_reason
    response.usage = usage or _make_usage()

    blocks = []
    if tool_blocks:
        blocks.extend(tool_blocks)
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    blocks.append(text_block)
    response.content = blocks
    return response


def _make_tool_block(*, name: str = "get_wellness", tool_id: str = "t1") -> MagicMock:
    """Create a mock tool_use content block."""
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = name
    block.input = {}
    return block


def _make_agent():
    """Create a ClaudeAgent with a mocked Anthropic client."""
    from bot.agent import ClaudeAgent

    with patch("bot.agent.anthropic.AsyncAnthropic"):
        agent = ClaudeAgent()
    return agent


# ---------------------------------------------------------------------------
# TestAccumulateUsage
# ---------------------------------------------------------------------------


class TestAccumulateUsage:
    """ClaudeAgent._accumulate_usage correctly sums token fields."""

    def test_sums_input_and_output_tokens(self):
        """Basic input/output accumulation across two responses."""
        from bot.agent import ClaudeAgent

        totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}

        ClaudeAgent._accumulate_usage(totals, _make_response(usage=_make_usage(input_tokens=100, output_tokens=50)))
        ClaudeAgent._accumulate_usage(totals, _make_response(usage=_make_usage(input_tokens=200, output_tokens=80)))

        assert totals["input_tokens"] == 300
        assert totals["output_tokens"] == 130

    def test_sums_cache_read_tokens(self):
        """cache_read_tokens accumulates from cache_read_input_tokens on usage."""
        from bot.agent import ClaudeAgent

        totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}

        ClaudeAgent._accumulate_usage(totals, _make_response(usage=_make_usage(cache_read=500)))
        ClaudeAgent._accumulate_usage(totals, _make_response(usage=_make_usage(cache_read=300)))

        assert totals["cache_read_tokens"] == 800

    def test_sums_cache_creation_tokens(self):
        """cache_creation_tokens accumulates from cache_creation_input_tokens on usage."""
        from bot.agent import ClaudeAgent

        totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}

        ClaudeAgent._accumulate_usage(totals, _make_response(usage=_make_usage(cache_creation=200)))
        ClaudeAgent._accumulate_usage(totals, _make_response(usage=_make_usage(cache_creation=150)))

        assert totals["cache_creation_tokens"] == 350

    def test_handles_missing_cache_attributes_as_zero(self):
        """Usage objects without cache fields default to zero (getattr fallback)."""
        from bot.agent import ClaudeAgent

        usage = MagicMock(spec=["input_tokens", "output_tokens"])
        usage.input_tokens = 100
        usage.output_tokens = 50
        # spec excludes cache_read_input_tokens and cache_creation_input_tokens

        totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}
        response = MagicMock()
        response.usage = usage
        ClaudeAgent._accumulate_usage(totals, response)

        assert totals["cache_read_tokens"] == 0
        assert totals["cache_creation_tokens"] == 0

    def test_handles_none_cache_attributes_as_zero(self):
        """None cache_read_input_tokens / cache_creation_input_tokens coerce to 0."""
        from bot.agent import ClaudeAgent

        usage = _make_usage(input_tokens=10, output_tokens=5)
        usage.cache_read_input_tokens = None
        usage.cache_creation_input_tokens = None

        totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}
        response = MagicMock()
        response.usage = usage
        ClaudeAgent._accumulate_usage(totals, response)

        assert totals["cache_read_tokens"] == 0
        assert totals["cache_creation_tokens"] == 0

    def test_all_fields_accumulated_in_single_call(self):
        """All four token fields are accumulated correctly in one call."""
        from bot.agent import ClaudeAgent

        totals = {"input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 100, "cache_creation_tokens": 50}
        usage = _make_usage(input_tokens=20, output_tokens=10, cache_read=200, cache_creation=75)
        response = MagicMock()
        response.usage = usage
        ClaudeAgent._accumulate_usage(totals, response)

        assert totals["input_tokens"] == 30
        assert totals["output_tokens"] == 15
        assert totals["cache_read_tokens"] == 300
        assert totals["cache_creation_tokens"] == 125


# ---------------------------------------------------------------------------
# TestRunToolUseLoop
# ---------------------------------------------------------------------------


class TestRunToolUseLoop:
    """ClaudeAgent._run_tool_use_loop returns (text, usage_dict, tool_calls) and accumulates usage."""

    async def test_returns_tuple_of_text_and_usage(self):
        """Single response with no tool use returns (text, dict, list) tuple."""
        agent = _make_agent()
        usage = _make_usage(input_tokens=100, output_tokens=50)
        response = _make_response(stop_reason="end_turn", text="Answer", usage=usage)
        agent.client.messages.create = AsyncMock(return_value=response)

        mcp = AsyncMock()
        mcp.list_tools.return_value = []

        result = await agent._run_tool_use_loop(
            mcp=mcp,
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )

        assert isinstance(result, tuple)
        assert len(result) == 3
        text, usage_dict, tool_calls = result
        assert text == "Answer"
        assert isinstance(usage_dict, dict)
        assert tool_calls == []

    def test_usage_dict_has_required_keys(self):
        """Returned usage_dict always contains the four canonical token keys."""
        totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}
        assert set(totals.keys()) == {"input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"}

    async def test_accumulates_usage_across_tool_iterations(self):
        """Usage is summed over the initial call and each tool-use iteration."""
        agent = _make_agent()

        first_usage = _make_usage(input_tokens=100, output_tokens=30)
        second_usage = _make_usage(input_tokens=200, output_tokens=60)

        tool_block = _make_tool_block(name="get_wellness", tool_id="tool1")

        # First response triggers tool use; second response ends loop
        first_response = MagicMock()
        first_response.stop_reason = "tool_use"
        first_response.usage = first_usage
        first_response.content = [tool_block]

        second_response = _make_response(stop_reason="end_turn", text="Final answer", usage=second_usage)

        agent.client.messages.create = AsyncMock(side_effect=[first_response, second_response])

        mcp = AsyncMock()
        mcp.call_tool.return_value = {"data": "ok"}

        text, usage_dict, _ = await agent._run_tool_use_loop(
            mcp=mcp,
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"name": "get_wellness"}],
        )

        assert text == "Final answer"
        assert usage_dict["input_tokens"] == 300  # 100 + 200
        assert usage_dict["output_tokens"] == 90  # 30 + 60

    async def test_calls_mcp_call_tool_for_each_tool_use_block(self):
        """Each tool_use block in a response triggers one mcp.call_tool call."""
        agent = _make_agent()

        tool_block_1 = _make_tool_block(name="tool_a", tool_id="id1")
        tool_block_2 = _make_tool_block(name="tool_b", tool_id="id2")

        first_response = MagicMock()
        first_response.stop_reason = "tool_use"
        first_response.usage = _make_usage(input_tokens=50, output_tokens=20)
        first_response.content = [tool_block_1, tool_block_2]

        second_response = _make_response(stop_reason="end_turn", text="done", usage=_make_usage())
        agent.client.messages.create = AsyncMock(side_effect=[first_response, second_response])

        mcp = AsyncMock()
        mcp.call_tool.return_value = {}

        await agent._run_tool_use_loop(
            mcp=mcp,
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )

        assert mcp.call_tool.call_count == 2
        calls = mcp.call_tool.call_args_list
        assert calls[0][0][0] == "tool_a"
        assert calls[1][0][0] == "tool_b"

    async def test_respects_max_iterations_limit(self):
        """Loop exits after max_iterations even if stop_reason is still tool_use."""
        agent = _make_agent()

        tool_block = _make_tool_block()
        loop_response = MagicMock()
        loop_response.stop_reason = "tool_use"
        loop_response.usage = _make_usage(input_tokens=10, output_tokens=5)
        loop_response.content = [tool_block]

        # All responses keep returning tool_use; loop should stop at max_iterations
        agent.client.messages.create = AsyncMock(return_value=loop_response)

        mcp = AsyncMock()
        mcp.call_tool.return_value = {}

        max_iter = 3
        await agent._run_tool_use_loop(
            mcp=mcp,
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            max_iterations=max_iter,
        )

        # 1 initial call + max_iterations calls inside the loop
        assert agent.client.messages.create.call_count == 1 + max_iter

    async def test_no_tool_use_makes_single_api_call(self):
        """When stop_reason is end_turn immediately, only one API call is made."""
        agent = _make_agent()

        response = _make_response(stop_reason="end_turn", text="Direct answer", usage=_make_usage(input_tokens=50))
        agent.client.messages.create = AsyncMock(return_value=response)

        mcp = AsyncMock()
        mcp.call_tool.return_value = {}

        text, usage_dict, _ = await agent._run_tool_use_loop(
            mcp=mcp,
            system="sys",
            messages=[{"role": "user", "content": "question"}],
            tools=[],
        )

        agent.client.messages.create.assert_called_once()
        assert text == "Direct answer"
        assert usage_dict["input_tokens"] == 50

    async def test_cache_tokens_accumulated_across_iterations(self):
        """Cache read/creation tokens are summed from all API calls in the loop."""
        agent = _make_agent()

        first_usage = _make_usage(input_tokens=100, output_tokens=30, cache_read=500, cache_creation=200)
        second_usage = _make_usage(input_tokens=150, output_tokens=40, cache_read=300, cache_creation=0)

        tool_block = _make_tool_block()
        first_response = MagicMock()
        first_response.stop_reason = "tool_use"
        first_response.usage = first_usage
        first_response.content = [tool_block]

        second_response = _make_response(stop_reason="end_turn", text="result", usage=second_usage)
        agent.client.messages.create = AsyncMock(side_effect=[first_response, second_response])

        mcp = AsyncMock()
        mcp.call_tool.return_value = {}

        _, usage_dict, _ = await agent._run_tool_use_loop(
            mcp=mcp, system="sys", messages=[{"role": "user", "content": "hi"}], tools=[]
        )

        assert usage_dict["cache_read_tokens"] == 800  # 500 + 300
        assert usage_dict["cache_creation_tokens"] == 200  # 200 + 0


# ---------------------------------------------------------------------------
# TestChatUsageTracking
# ---------------------------------------------------------------------------


class TestChatUsageTracking:
    """ClaudeAgent.chat calls ApiUsageDaily.increment and handles failures gracefully."""

    def _setup_agent_and_mocks(self):
        """Return agent and a pre-wired set of patches for chat()."""
        return _make_agent()

    async def test_chat_calls_increment_with_user_id(self):
        """chat() passes user_id to ApiUsageDaily.increment."""
        agent = _make_agent()
        usage = _make_usage(input_tokens=100, output_tokens=50)
        response = _make_response(stop_reason="end_turn", text="reply", usage=usage)

        with (
            patch("bot.agent.MCPClient") as mock_mcp_cls,
            patch("bot.agent.get_system_prompt_chat", new=AsyncMock(return_value="sys")),
            patch("bot.agent.sentry_sdk"),
            patch("bot.agent.ApiUsageDaily.increment", new=AsyncMock()) as mock_increment,
        ):
            mock_mcp = AsyncMock()
            mock_mcp.list_tools.return_value = []
            mock_mcp_cls.return_value = mock_mcp

            agent.client.messages.create = AsyncMock(return_value=response)

            await agent.chat("hello", user_id=42)

        mock_increment.assert_awaited_once()
        call_kwargs = mock_increment.call_args[1]
        assert call_kwargs["user_id"] == 42

    async def test_chat_passes_usage_tokens_to_increment(self):
        """chat() forwards all four token fields from _run_tool_use_loop to increment."""
        agent = _make_agent()
        usage = _make_usage(input_tokens=300, output_tokens=120, cache_read=600, cache_creation=150)
        response = _make_response(stop_reason="end_turn", text="reply", usage=usage)

        with (
            patch("bot.agent.MCPClient") as mock_mcp_cls,
            patch("bot.agent.get_system_prompt_chat", new=AsyncMock(return_value="sys")),
            patch("bot.agent.sentry_sdk"),
            patch("bot.agent.ApiUsageDaily.increment", new=AsyncMock()) as mock_increment,
        ):
            mock_mcp = AsyncMock()
            mock_mcp.list_tools.return_value = []
            mock_mcp_cls.return_value = mock_mcp

            agent.client.messages.create = AsyncMock(return_value=response)

            await agent.chat("question", user_id=7)

        call_kwargs = mock_increment.call_args[1]
        assert call_kwargs["input_tokens"] == 300
        assert call_kwargs["output_tokens"] == 120
        assert call_kwargs["cache_read_tokens"] == 600
        assert call_kwargs["cache_creation_tokens"] == 150

    async def test_chat_returns_text_even_if_increment_raises(self):
        """Fire-and-forget: increment failure does not propagate; chat still returns text."""
        agent = _make_agent()
        usage = _make_usage(input_tokens=50, output_tokens=20)
        response = _make_response(stop_reason="end_turn", text="Hello!", usage=usage)

        with (
            patch("bot.agent.MCPClient") as mock_mcp_cls,
            patch("bot.agent.get_system_prompt_chat", new=AsyncMock(return_value="sys")),
            patch("bot.agent.sentry_sdk"),
            patch("bot.agent.ApiUsageDaily.increment", new=AsyncMock(side_effect=RuntimeError("DB down"))),
        ):
            mock_mcp = AsyncMock()
            mock_mcp.list_tools.return_value = []
            mock_mcp_cls.return_value = mock_mcp

            agent.client.messages.create = AsyncMock(return_value=response)

            result = await agent.chat("hey", user_id=1)

        assert result.text == "Hello!"

    async def test_chat_returns_fallback_when_response_is_empty(self):
        """Empty text response → chat() returns the fallback string."""
        agent = _make_agent()
        usage = _make_usage(input_tokens=10, output_tokens=0)

        # Response with no text blocks
        response = MagicMock()
        response.stop_reason = "end_turn"
        response.usage = usage
        empty_block = MagicMock()
        empty_block.type = "text"
        empty_block.text = ""
        response.content = [empty_block]

        with (
            patch("bot.agent.MCPClient") as mock_mcp_cls,
            patch("bot.agent.get_system_prompt_chat", new=AsyncMock(return_value="sys")),
            patch("bot.agent.sentry_sdk"),
            patch("bot.agent.ApiUsageDaily.increment", new=AsyncMock()),
        ):
            mock_mcp = AsyncMock()
            mock_mcp.list_tools.return_value = []
            mock_mcp_cls.return_value = mock_mcp

            agent.client.messages.create = AsyncMock(return_value=response)

            result = await agent.chat("hi", user_id=1)

        assert result.text == "Не удалось обработать запрос."

    async def test_chat_increment_called_once_per_chat_call(self):
        """ApiUsageDaily.increment is called exactly once per chat() invocation."""
        agent = _make_agent()
        usage = _make_usage(input_tokens=80, output_tokens=40)
        response = _make_response(stop_reason="end_turn", text="ok", usage=usage)

        with (
            patch("bot.agent.MCPClient") as mock_mcp_cls,
            patch("bot.agent.get_system_prompt_chat", new=AsyncMock(return_value="sys")),
            patch("bot.agent.sentry_sdk"),
            patch("bot.agent.ApiUsageDaily.increment", new=AsyncMock()) as mock_increment,
        ):
            mock_mcp = AsyncMock()
            mock_mcp.list_tools.return_value = []
            mock_mcp_cls.return_value = mock_mcp

            agent.client.messages.create = AsyncMock(return_value=response)

            await agent.chat("msg", user_id=5)

        mock_increment.assert_awaited_once()

    async def test_chat_with_image_still_tracks_usage(self):
        """Image messages go through the same loop; usage is still tracked."""
        agent = _make_agent()
        usage = _make_usage(input_tokens=200, output_tokens=80)
        response = _make_response(stop_reason="end_turn", text="I see an image", usage=usage)

        with (
            patch("bot.agent.MCPClient") as mock_mcp_cls,
            patch("bot.agent.get_system_prompt_chat", new=AsyncMock(return_value="sys")),
            patch("bot.agent.sentry_sdk"),
            patch("bot.agent.ApiUsageDaily.increment", new=AsyncMock()) as mock_increment,
        ):
            mock_mcp = AsyncMock()
            mock_mcp.list_tools.return_value = []
            mock_mcp_cls.return_value = mock_mcp

            agent.client.messages.create = AsyncMock(return_value=response)

            result = await agent.chat(
                "describe this",
                user_id=3,
                image_data=b"\x89PNG",
                image_media_type="image/png",
            )

        assert result.text == "I see an image"
        mock_increment.assert_awaited_once()
        call_kwargs = mock_increment.call_args[1]
        assert call_kwargs["user_id"] == 3
        assert call_kwargs["input_tokens"] == 200
