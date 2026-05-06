"""Guard against drift between SYSTEM_PROMPT_WEEKLY and MCPTool.WEEKLY_TOOL_NAMES.

If the prompt instructs Claude to call a tool, that tool must be in the
whitelist or it will not be sent to the API in `tools=[...]` and the call
becomes physically impossible. This test extracts every `get_*` reference
from the prompt and asserts each one is whitelisted. See END-95.
"""

import re

from bot.prompts import SYSTEM_PROMPT_WEEKLY
from tasks.tools import MCPTool

# Match function-call shape only: `get_xxx(`. Avoids false positives on prose
# like "you can get_started quickly" — only call sites count as a contract
# the tool actually has to be invokable.
_TOOL_CALL_RE = re.compile(r"\bget_[a-z][a-z0-9_]*(?=\s*\()")


def test_weekly_prompt_tools_are_whitelisted():
    mentioned = set(_TOOL_CALL_RE.findall(SYSTEM_PROMPT_WEEKLY))
    missing = mentioned - MCPTool.WEEKLY_TOOL_NAMES
    assert not missing, (
        f"SYSTEM_PROMPT_WEEKLY mentions tools not in MCPTool.WEEKLY_TOOL_NAMES: "
        f"{sorted(missing)}. Add them to the whitelist or remove from the prompt."
    )
