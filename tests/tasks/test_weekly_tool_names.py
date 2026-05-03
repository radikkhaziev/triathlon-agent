"""Guard against drift between SYSTEM_PROMPT_WEEKLY and MCPTool.WEEKLY_TOOL_NAMES.

If the prompt instructs Claude to call a tool, that tool must be in the
whitelist or it will not be sent to the API in `tools=[...]` and the call
becomes physically impossible. This test extracts every `get_*` reference
from the prompt and asserts each one is whitelisted. See END-95.
"""

import re

from bot.prompts import SYSTEM_PROMPT_WEEKLY
from tasks.tools import MCPTool


def test_weekly_prompt_tools_are_whitelisted():
    mentioned = set(re.findall(r"get_[a-z_]+", SYSTEM_PROMPT_WEEKLY))
    missing = mentioned - MCPTool.WEEKLY_TOOL_NAMES
    assert not missing, (
        f"SYSTEM_PROMPT_WEEKLY mentions tools not in MCPTool.WEEKLY_TOOL_NAMES: "
        f"{sorted(missing)}. Add them to the whitelist or remove from the prompt."
    )
