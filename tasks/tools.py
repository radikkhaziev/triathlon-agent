from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from data.db import UserDTO

import anthropic
import httpx

from bot.prompts import get_system_prompt_v2
from config import settings
from data.db import User
from tasks.dto import DateDTO

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool definitions (Anthropic tool-use format)
# ---------------------------------------------------------------------------

MORNING_TOOLS = [
    # --- Core tools (recommended sequence) ---
    {
        "name": "get_recovery",
        "description": (
            "Get composite recovery score and training recommendation for a date. "
            "Recovery score (0-100) combines: RMSSD 35%, Banister 25%, RHR 20%, Sleep 20%. "
            "Categories: excellent >85, good 70-85, moderate 40-70, low <40."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
            },
            "required": ["date"],
        },
    },
    {
        "name": "get_hrv_analysis",
        "description": (
            "Get HRV analysis with dual-algorithm baselines. "
            "Returns status (green/yellow/red), 7d/60d means, bounds, CV, SWC, trend. "
            "Algorithm: 'flatt_esco' or 'ai_endurance'. Empty = both."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                "algorithm": {
                    "type": "string",
                    "description": "Algorithm: 'flatt_esco', 'ai_endurance', or empty for both",
                },
            },
            "required": ["date"],
        },
    },
    {
        "name": "get_rhr_analysis",
        "description": (
            "Get resting heart rate analysis with baselines. "
            "Inverted vs HRV: elevated RHR = red. "
            "Returns status (green/yellow/red), today vs 7d/30d/60d means, bounds, trend."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
            },
            "required": ["date"],
        },
    },
    {
        "name": "get_training_load",
        "description": (
            "Get CTL/ATL/TSB and per-sport CTL for a given date. "
            "All values from Intervals.icu (tau_CTL=42d, tau_ATL=7d). "
            "TSB zones: >+10 under-training, -10..+10 optimal, -10..-25 productive overreach, <-25 overtraining risk."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
            },
            "required": ["date"],
        },
    },
    {
        "name": "get_scheduled_workouts",
        "description": (
            "Get planned workouts from Intervals.icu calendar for a date. "
            "Returns workout name, sport type, duration, and description with interval structure."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                "days_ahead": {
                    "type": "integer",
                    "description": "Days ahead to include (0 = single day). Default: 0",
                },
            },
            "required": ["date"],
        },
    },
    {
        "name": "get_goal_progress",
        "description": (
            "Get race goal progress — overall and per-sport CTL vs targets. "
            "Shows event name, date, weeks remaining, and percentage of target CTL achieved."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_activity_hrv",
        "description": (
            "Get DFA alpha 1 analysis for activities on a given date. "
            "Returns Ra (readiness %), Da (durability %), HRVT1/HRVT2 thresholds, quality."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
            },
            "required": ["date"],
        },
    },
    # --- Optional tools (Claude calls when suspicious data) ---
    {
        "name": "get_wellness_range",
        "description": (
            "Get wellness data for a date range. "
            "Useful for trend analysis — returns daily wellness records with recovery, HRV, sleep."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
            "required": ["from_date", "to_date"],
        },
    },
    {
        "name": "get_activities",
        "description": (
            "Get completed activities for a date range. "
            "Returns sport type, training load (TSS), duration, DFA availability."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_date": {"type": "string", "description": "End date YYYY-MM-DD. Default: today"},
                "days_back": {"type": "integer", "description": "Days to look back. Default: 7"},
            },
        },
    },
    {
        "name": "get_training_log",
        "description": (
            "Get training log with pre-workout context, actual data, and post-outcome. "
            "Shows compliance, adaptation, and recovery response."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days_back": {"type": "integer", "description": "Days to look back. Default: 14"},
            },
        },
    },
    {
        "name": "get_threshold_freshness",
        "description": (
            "Check how fresh HRVT1/HRVT2 thresholds are. "
            "Thresholds older than 21 days are stale. Returns last test date and drift alerts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sport": {"type": "string", "description": "Filter: 'Ride' or 'Run'. Empty = all"},
            },
        },
    },
    {
        "name": "get_readiness_history",
        "description": (
            "Get Readiness (Ra) trend over recent activities. "
            "Ra > +5%: excellent, -5..+5%: normal, < -5%: under-recovered."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sport": {"type": "string", "description": "Filter: 'bike' or 'run'. Empty = all"},
                "days_back": {"type": "integer", "description": "Days to look back. Default: 30"},
            },
        },
    },
    {
        "name": "get_mood_checkins",
        "description": (
            "Get mood check-ins for a date range. " "Returns energy, mood, anxiety, social ratings (1-5) and notes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date_str": {"type": "string", "description": "Reference date YYYY-MM-DD. Default: today"},
                "days_back": {"type": "integer", "description": "Days to look back. Default: 7"},
            },
        },
    },
    {
        "name": "get_iqos_sticks",
        "description": (
            "Get IQOS stick count for a day or range. "
            "Use days_back=0 for single day, >0 for range with totals and average."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_date": {"type": "string", "description": "Date YYYY-MM-DD. Default: today"},
                "days_back": {"type": "integer", "description": "0 = single day, 7 = week. Default: 0"},
            },
        },
    },
    {
        "name": "get_efficiency_trend",
        "description": (
            "Get aerobic efficiency and cardiac drift (decoupling) trend. "
            "Use strict_filter=true for decoupling analysis: applies stricter filtering "
            "(VI <= 1.10, >70% Z1+Z2, bike >= 60min / run >= 45min, swim excluded). "
            "Returns decoupling_trend with last-5 median and traffic light status (green/yellow/red). "
            "If days_since > 14, data is stale — don't emphasize in report."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sport": {"type": "string", "description": "bike, run, or swim. Empty = all."},
                "days_back": {"type": "integer", "description": "Lookback days. Default: 90"},
                "strict_filter": {
                    "type": "boolean",
                    "description": "Apply strict decoupling filter (VI, zones, duration). Default: false",
                },
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Chat-only tool definitions
# ---------------------------------------------------------------------------

SAVE_MOOD_CHECKIN_TOOL = {
    "name": "save_mood_checkin",
    "description": (
        "Record a mood check-in. At least one field required. "
        "Scales 1-5: energy, mood, anxiety (1=calm, 5=very anxious), social. "
        "Call autonomously when athlete's message contains emotional signals."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "energy": {"type": "integer", "description": "Energy level 1-5"},
            "mood": {"type": "integer", "description": "Mood 1-5"},
            "anxiety": {"type": "integer", "description": "Anxiety 1-5 (1=calm, 5=very anxious)"},
            "social": {"type": "integer", "description": "Social desire 1-5"},
            "note": {"type": "string", "description": "Optional text note"},
        },
    },
}

GET_GITHUB_ISSUES_TOOL = {
    "name": "get_github_issues",
    "description": (
        "List GitHub issues from the triathlon-agent repository. "
        "Use to check existing issues before creating new ones (avoid duplicates), "
        "review open tasks, or reference issue numbers."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "state": {
                "type": "string",
                "enum": ["open", "closed", "all"],
                "description": "Filter by state (default: open)",
            },
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Filter by labels (e.g. ['bug'])",
            },
            "limit": {
                "type": "integer",
                "description": "Max issues to return (default: 10, max: 100)",
            },
        },
    },
}

CREATE_GITHUB_ISSUE_TOOL = {
    "name": "create_github_issue",
    "description": (
        "Create a GitHub issue in the triathlon-agent repository. "
        "Use for tracking bugs, feature requests, and tasks discovered during conversation. "
        "Title: English, imperative mood. Body: Markdown with Context, What needs to happen, Acceptance criteria."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Issue title in English, imperative mood ('Add X', 'Fix Y')"},
            "body": {"type": "string", "description": "Markdown body with structured sections"},
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Labels to apply (e.g. ['bug'], ['enhancement', 'needs-implementation'])",
            },
            "images": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Public URLs of uploaded screenshots to embed in the issue. Always pass image URLs from the conversation.",  # noqa
            },
        },
        "required": ["title", "body"],
    },
}

CHAT_TOOLS = [*MORNING_TOOLS, SAVE_MOOD_CHECKIN_TOOL, GET_GITHUB_ISSUES_TOOL, CREATE_GITHUB_ISSUE_TOOL]


_BOLD_RE = re.compile(r"\*\*([^\n*][^\n]*?)\*\*")
_BOLD_UNDERSCORE_RE = re.compile(r"__([^\n_][^\n]*?)__")
_CODE_RE = re.compile(r"`([^`\n]+)`")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.*?)\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_LIST_BULLET_RE = re.compile(r"^(\s*)[-*]\s+")


def markdown_to_telegram_html(text: str) -> str:
    """Convert Claude-style markdown to Telegram-compatible HTML.

    Handles the pieces Telegram cannot render natively:
    - ``### heading`` → ``<b>heading</b>``
    - pipe tables → plain " — "-separated rows (separator rows dropped)
    - ``**bold**`` / ``__bold__`` → ``<b>...</b>``
    - ``` `code` ``` → ``<code>...</code>``
    - ``- item`` list bullets → ``• item``
    """
    out_lines: list[str] = []
    for raw in text.splitlines():
        line = raw
        heading = _HEADING_RE.match(line)
        if heading:
            inner = heading.group(1).replace("**", "").replace("__", "")
            out_lines.append(f"**{inner}**")
            continue
        if _TABLE_SEP_RE.match(line) and "|" in line and "-" in line:
            continue
        table_row = _TABLE_ROW_RE.match(line)
        if table_row:
            cells = [c.strip() for c in table_row.group(1).split("|")]
            line = " — ".join(c for c in cells if c)
        bullet = _LIST_BULLET_RE.match(line)
        if bullet:
            line = f"{bullet.group(1)}• {line[bullet.end():]}"
        out_lines.append(line)

    text = "\n".join(out_lines)

    # Protect already-formed bold/code tokens before escaping.
    text = _BOLD_RE.sub(lambda m: f"\x00B\x00{m.group(1)}\x00/B\x00", text)
    text = _BOLD_UNDERSCORE_RE.sub(lambda m: f"\x00B\x00{m.group(1)}\x00/B\x00", text)
    text = _CODE_RE.sub(lambda m: f"\x00C\x00{m.group(1)}\x00/C\x00", text)

    text = html.escape(text)

    text = text.replace("\x00B\x00", "<b>").replace("\x00/B\x00", "</b>")
    text = text.replace("\x00C\x00", "<code>").replace("\x00/C\x00", "</code>")
    # Strip any stray NUL sentinels — Telegram rejects raw \x00 in messages.
    return text.replace("\x00", "")


@dataclass
class TelegramTool:
    """Sync Telegram Bot API client via HTTP."""

    user: UserDTO | None = None  # if provided, uses chat_id and checks is_silent
    bot_token: str = field(default_factory=lambda: settings.TELEGRAM_BOT_TOKEN.get_secret_value())
    base_url: str = field(init=False)

    def __post_init__(self):
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    # Telegram 400 ``description`` substrings that mean "this chat is
    # permanently unreachable from this bot" — sending again will deterministically
    # fail. Drop ``bot_chat_initialized``, swallow the error, return None.
    # The frontend banner re-arms automatically. Any other 400 (parse_mode
    # error, empty text, etc.) keeps raising — that's our bug to fix.
    #
    # NOTE: ``"bot was blocked by the user"`` is intentionally NOT in this
    # list — Telegram returns it with HTTP 403, not 400, and the 403 branch
    # below handles that path with different semantics (``is_active=False``
    # vs ``bot_chat_initialized=False``).
    #
    # ``ClassVar`` is mandatory inside an ``@dataclass`` — without it the
    # tuple becomes a per-instance field (added to ``__init__``, dumped in
    # ``repr``, compared in ``__eq__``) and external callers can override
    # the allowlist via constructor kwargs.
    _TG_400_PERMANENT_SUBSTRINGS: ClassVar[tuple[str, ...]] = (
        "chat not found",
        "user is deactivated",
        "peer_id_invalid",
    )

    def _post_with_retries(
        self,
        endpoint: str,
        chat_id: str,
        *,
        timeout: float,
        retries: int = 3,
        **httpx_kwargs,
    ) -> dict | None:
        """POST to Telegram endpoint with retry on transient errors.

        403 marks the user inactive and returns None.
        400 with a permanent ``description`` (chat not found, user is
        deactivated, etc.) clears ``bot_chat_initialized`` and returns None.
        Passes through ``data=`` / ``files=`` / ``json=`` kwargs to ``httpx.post``.

        Retry policy: ONLY ``httpx.TimeoutException`` and ``httpx.ConnectError``
        retry. 4xx responses don't — both terminal-handler branches above
        return ``None`` inside the try-block, and any unhandled non-2xx hits
        ``raise_for_status`` whose ``HTTPStatusError`` is NOT caught by the
        ``except (TimeoutException, ConnectError)``. If you ever broaden the
        ``except`` to include ``HTTPError``, the 400 self-healing branch
        will fire N times per failure and issue N redundant DB UPDATEs.
        """
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                resp = httpx.post(
                    f"{self.base_url}/{endpoint}",
                    timeout=timeout,
                    **httpx_kwargs,
                )
                if resp.status_code == 403:
                    # User blocked the bot (or deactivated). `my_chat_member` is
                    # the primary signal, but scheduled actors may fire between
                    # the block and the webhook — flip the flag here too so the
                    # next scheduled run skips this user.
                    logger.info("Telegram 403 for chat_id=%s — marking inactive", chat_id)
                    User.set_active_by_chat_id(chat_id, False)
                    return None
                if resp.status_code == 400 and self._is_permanent_400(resp):
                    # Self-healing: chat is gone (deleted) or never existed
                    # (widget signup that bypassed /start by some race).
                    # Clear bot_chat_initialized so future actors are skipped
                    # by ``_suppress`` and the webapp banner re-appears,
                    # prompting the user to /start again. Issue #266 bleed-stop.
                    #
                    # Guard: only flip the flag when the failing chat_id
                    # belongs to the user this tool was instantiated for.
                    # Broadcast paths (``TelegramTool()`` without ``user``,
                    # owner-broadcast with explicit ``chat_id`` override)
                    # could otherwise update the wrong row (a typo'd chat_id
                    # could match a real victim user). Swallow the error
                    # either way, but only mutate state for the bound user.
                    if self.user is not None and str(self.user.chat_id) == chat_id:
                        logger.info(
                            "Telegram 400 permanent for chat_id=%s — clearing bot_chat_initialized",
                            chat_id,
                        )
                        User.set_bot_chat_initialized(chat_id, False)
                    else:
                        logger.warning(
                            "Telegram 400 permanent for chat_id=%s (broadcast/no-user) — "
                            "swallowing without state mutation",
                            chat_id,
                        )
                    return None
                resp.raise_for_status()
                return resp.json()
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_exc = e
                logger.warning("Telegram %s attempt %d/%d failed: %s", endpoint, attempt + 1, retries, e)
        assert last_exc is not None  # retries >= 1
        raise last_exc

    @classmethod
    def _is_permanent_400(cls, resp: httpx.Response) -> bool:
        """Match Telegram's 400 ``description`` against the permanent-failure
        allowlist. Any parse error or unknown shape returns False so the
        caller falls through to ``raise_for_status`` and Sentry sees it."""
        try:
            description = (resp.json().get("description") or "").lower()
        except (ValueError, AttributeError):
            return False
        return any(marker in description for marker in cls._TG_400_PERMANENT_SUBSTRINGS)

    def _suppress(self) -> bool:
        """Skip-send predicate shared by every Telegram outbound.

        ``is_silent`` is the user's quiet-hours opt-out.
        ``bot_chat_initialized=False`` means the bot chat does not exist on
        Telegram's side (Login Widget signup that never typed /start) — see
        issue #266. Sending would deterministically 400 with
        ``chat not found`` and create a Sentry storm; suppress instead.
        """
        if not self.user:
            return False
        return self.user.is_silent or not self.user.bot_chat_initialized

    def send_message(
        self,
        text: str,
        reply_markup: dict | None = None,
        chat_id: int | str | None = None,
        markdown: bool = False,
    ) -> dict | None:
        """Send a message via Telegram Bot API. Skips if user is_silent.

        If ``markdown`` is True, ``text`` is converted from common Claude-style
        markdown (``**bold**``, ``### headings``, pipe tables) to Telegram HTML
        and sent with ``parse_mode=HTML``.
        """
        if self._suppress():
            return None

        _chat_id = str(chat_id or (self.user.chat_id if self.user else ""))
        if not _chat_id:
            raise ValueError("chat_id required: pass it or provide user to TelegramTool")

        payload: dict = {
            "chat_id": _chat_id,
            "text": markdown_to_telegram_html(text) if markdown else text,
        }
        if markdown:
            payload["parse_mode"] = "HTML"
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)

        return self._post_with_retries("sendMessage", _chat_id, timeout=15.0, json=payload)

    def send_photo(
        self,
        photo: bytes,
        caption: str = "",
        reply_markup: dict | None = None,
        chat_id: int | str | None = None,
    ) -> dict | None:
        """Send a photo via Telegram Bot API. Skips if user is_silent."""
        if self._suppress():
            return None

        _chat_id = str(chat_id or (self.user.chat_id if self.user else ""))
        if not _chat_id:
            raise ValueError("chat_id required: pass it or provide user to TelegramTool")

        data: dict = {"chat_id": _chat_id}
        if caption:
            data["caption"] = caption
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)

        files = {"photo": ("card.png", photo, "image/png")}
        return self._post_with_retries("sendPhoto", _chat_id, timeout=30.0, data=data, files=files)

    def send_document(
        self,
        document: bytes,
        filename: str,
        mime_type: str = "application/octet-stream",
        caption: str = "",
        reply_markup: dict | None = None,
        chat_id: int | str | None = None,
    ) -> dict | None:
        """Send a document via Telegram Bot API. Preserves PNG transparency. Skips if user is_silent."""
        if self._suppress():
            return None

        _chat_id = str(chat_id or (self.user.chat_id if self.user else ""))
        if not _chat_id:
            raise ValueError("chat_id required: pass it or provide user to TelegramTool")

        data: dict = {"chat_id": _chat_id}
        if caption:
            data["caption"] = caption
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)

        files = {"document": (filename, document, mime_type)}
        return self._post_with_retries("sendDocument", _chat_id, timeout=30.0, data=data, files=files)


@dataclass
class MCPTool:
    mcp_url: str = field(default_factory=lambda: f"{(settings.MCP_BASE_URL or settings.API_BASE_URL).rstrip('/')}/mcp/")
    token: str = field(default_factory=lambda: settings.MCP_AUTH_TOKEN.get_secret_value())
    user_id: int = 1
    language: str = "ru"
    headers: dict = field(init=False)
    _session_id: str | None = field(init=False, default=None)
    _request_id: int = field(init=False, default=0)

    def __post_init__(self):
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _parse_response(self, resp: httpx.Response) -> dict:
        """Parse JSON-RPC response — plain JSON or SSE."""
        text = resp.text
        for line in text.split("\n"):
            if line.startswith("data: "):
                return json.loads(line[6:])
        return resp.json()

    def _ensure_session(self) -> None:
        """Initialize MCP session if not established."""
        if self._session_id is not None:
            return

        init_payload = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "triathlon-worker", "version": "1.0"},
            },
            "id": self._next_id(),
        }

        resp = httpx.post(self.mcp_url, json=init_payload, headers=self.headers, timeout=15.0)
        resp.raise_for_status()
        self._session_id = resp.headers.get("mcp-session-id")

        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        headers = {**self.headers}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        httpx.post(self.mcp_url, json=notif, headers=headers, timeout=15.0)

        logger.info("MCPTool session initialized: %s", self._session_id)

    def _invalidate_session(self) -> None:
        """Reset session so next call re-initializes."""
        self._session_id = None

    def _call_mcp(self, name: str, arguments: dict) -> dict:
        """Call an MCP tool via HTTP POST to /mcp (JSON-RPC).

        On 401/408/404 — invalidates session and retries once (server restart / stale session).
        """
        return self._call_mcp_inner(name, arguments, retry=True)

    def _call_mcp_inner(self, name: str, arguments: dict, *, retry: bool) -> dict:
        self._ensure_session()

        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments,
            },
            "id": self._next_id(),
        }

        headers = {**self.headers}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        resp = httpx.post(
            self.mcp_url,
            json=payload,
            headers=headers,
            timeout=30.0,
        )

        if resp.status_code in (401, 404, 408, 409) and retry:
            logger.warning("MCP session stale (%d), re-initializing", resp.status_code)
            self._invalidate_session()
            return self._call_mcp_inner(name, arguments, retry=False)

        resp.raise_for_status()
        data = self._parse_response(resp)

        if "error" in data:
            logger.warning("MCP tool %s error: %s", name, data["error"])
            return {"error": str(data["error"])}

        # MCP tools/call result: {"result": {"content": [{"type": "text", "text": "..."}]}}
        result = data.get("result", {})
        content = result.get("content", [])
        for block in content:
            if block.get("type") != "text":
                continue
            try:
                return json.loads(block["text"])
            except (ValueError, KeyError):
                return {"text": block["text"]}
        return {}

    def generate_morning_report_via_mcp(self, dt: date | DateDTO | str) -> str | None:
        """Generate morning report using sync Claude API + MCP tool calls."""
        _dt = dt if isinstance(dt, str) else dt.isoformat()

        try:
            client = anthropic.Anthropic(
                api_key=settings.ANTHROPIC_API_KEY.get_secret_value(),
                max_retries=5,
            )

            system = get_system_prompt_v2(user_id=self.user_id, language=self.language)
            prompt = f"Сгенерируй утренний отчёт за {_dt}"

            messages: list[dict] = [{"role": "user", "content": prompt}]

            max_iterations = 10
            for _ in range(max_iterations):
                response = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=4096,
                    system=system,
                    messages=messages,
                    tools=MORNING_TOOLS,
                )

                if response.stop_reason != "tool_use":
                    break

                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = self._call_mcp(block.name, block.input)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(result, default=str),
                            }
                        )

                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})

            text_blocks = [b.text for b in response.content if b.type == "text"]
            text = "\n".join(text_blocks)
            return text or None
        except Exception:
            logger.exception("Morning report generation failed for %s", _dt)
            return None

    # Tools allowed in weekly report (no Garmin, no workout creation, no admin)
    WEEKLY_TOOL_NAMES = {
        "get_weekly_summary",
        "get_personal_patterns",
        "get_training_load",
        "get_efficiency_trend",
        "get_goal_progress",
        "get_scheduled_workouts",
        "get_mood_checkins_tool",
        "get_iqos_sticks",
        "get_wellness_range",
        "get_hrv_analysis",
        "get_rhr_analysis",
        "get_recovery",
    }

    def _list_mcp_tools(self, *, _retry: bool = True) -> list[dict]:
        """Fetch tool definitions from MCP server via tools/list."""
        self._ensure_session()
        payload = {"jsonrpc": "2.0", "method": "tools/list", "id": self._next_id()}
        headers = {**self.headers}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        resp = httpx.post(self.mcp_url, json=payload, headers=headers, timeout=30.0)

        if resp.status_code in (401, 404, 408, 409) and _retry:
            self._invalidate_session()
            return self._list_mcp_tools(_retry=False)

        resp.raise_for_status()
        data = self._parse_response(resp)
        tools = data.get("result", {}).get("tools", [])
        # Convert MCP format to Anthropic format
        return [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("inputSchema", {"type": "object", "properties": {}}),
            }
            for t in tools
        ]

    def generate_weekly_report_via_mcp(self) -> str | None:
        """Generate weekly report using sync Claude API + MCP tool calls."""
        from bot.prompts import get_system_prompt_weekly

        try:
            client = anthropic.Anthropic(
                api_key=settings.ANTHROPIC_API_KEY.get_secret_value(),
                max_retries=5,
            )

            system = get_system_prompt_weekly(user_id=self.user_id, language=self.language)

            all_tools = self._list_mcp_tools()
            tools = [t for t in all_tools if t["name"] in self.WEEKLY_TOOL_NAMES]

            messages: list[dict] = [{"role": "user", "content": "Сгенерируй недельный отчёт"}]

            max_iterations = 12
            for _ in range(max_iterations):
                response = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=4096,
                    system=system,
                    messages=messages,
                    tools=tools,
                )

                if response.stop_reason != "tool_use":
                    break

                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = self._call_mcp(block.name, block.input)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(result, default=str),
                            }
                        )

                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})

            text_blocks = [b.text for b in response.content if b.type == "text"]
            text = "\n".join(text_blocks)
            return text or None
        except Exception:
            logger.exception("Weekly report generation failed for user %d", self.user_id)
            return None
