"""Guard against drift between SYSTEM_PROMPT_V2 and MORNING_TOOL_NAMES.

If the morning prompt instructs Claude to call a tool, that tool must be in
the whitelist or it will not be sent to the API in `tools=[...]` and the
call becomes physically impossible. This test extracts every `get_*` /
`predict_*` reference from the prompt and asserts each one is whitelisted.
Mirrors the weekly drift test (see test_weekly_tool_names.py).
"""

import re

from bot.prompts import SYSTEM_PROMPT_V2
from tasks.tools import MORNING_TOOL_NAMES

# Match function-call shape only: `name(`. Avoids false positives on prose
# like "you can get_started quickly" — only call sites count as a contract
# the tool actually has to be invokable.
_TOOL_CALL_RE = re.compile(r"\b(?:get|predict)_[a-z][a-z0-9_]*(?=\s*\()")


def test_morning_prompt_tools_are_whitelisted():
    mentioned = set(_TOOL_CALL_RE.findall(SYSTEM_PROMPT_V2))
    missing = mentioned - MORNING_TOOL_NAMES
    assert not missing, (
        f"SYSTEM_PROMPT_V2 mentions tools not in MORNING_TOOL_NAMES: "
        f"{sorted(missing)}. Add them to MORNING_TOOLS or remove from the prompt."
    )
