"""Soft donate nudge — appended after `agent.chat()` in free-form chat handlers.

See `docs/DONATE_SPEC.md` §11. Nudge is:
- English-only (intentional — see §11.5)
- Sent as a separate Telegram message to keep parse_mode isolated (§11.6)
- Gated in the handler layer via `should_show_nudge` — `ClaudeAgent` only
  reports the raw boundary signal (§11.4), all policy lives here.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from config import settings
from data.db import User

NUDGE_MESSAGES = [
    "💙 _Happy to help! Support the project:_ /donate",
    "☕ _We've had a good session today. Support the bot:_ /donate",
    "🏊 _Every donation keeps the project swimming:_ /donate",
    "💪 _The bot runs on enthusiasm and Stars:_ /donate",
    "🚴 _If the bot earned its wheels today, consider a tip:_ /donate",
    "🏃 _Fueling the next sprint takes Stars too:_ /donate",
    "⭐ _A few Stars go a long way — support the project:_ /donate",
    "🎯 _Liked today's analysis? Back the bot:_ /donate",
    "🔋 _Help recharge the bot with Stars:_ /donate",
    "📊 _Your support keeps the charts coming:_ /donate",
    "🙌 _Thanks for training with me. If you'd like to chip in:_ /donate",
    "🧪 _Independent indie project — your Stars are the fuel:_ /donate",
    "🏅 _Every session together, every Star appreciated:_ /donate",
    "🌟 _The bot is free, but Stars keep it improving:_ /donate",
    "💬 _If this chat helped, the tip jar is open:_ /donate",
]


def get_nudge_text() -> str:
    return random.choice(NUDGE_MESSAGES)


def should_show_nudge(user: User, nudge_boundary: bool, request_count: int) -> bool:
    """Final gate on whether to append a donate nudge to this response.

    Layers, cheapest first:
    1. `nudge_boundary` — raw signal from agent (request_count % N == 0)
    2. Owner opt-out flag
    3. Daily cap — no more than DONATE_NUDGE_MAX_PER_DAY nudges / day
    4. Recent-donation suppression window
    """
    if not nudge_boundary:
        return False
    if settings.DONATE_NUDGE_SKIP_OWNER and user.role == "owner":
        return False
    # Daily cap: nudges already fired today = request_count // N. Next one would
    # be the (current // N + 1)-th, so cap at MAX.
    if request_count // settings.DONATE_NUDGE_EVERY_N > settings.DONATE_NUDGE_MAX_PER_DAY:
        return False
    if user.last_donation_at is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.DONATE_NUDGE_SUPPRESS_DAYS)
        if user.last_donation_at > cutoff:
            return False
    return True
