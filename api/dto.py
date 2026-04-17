"""Pydantic models used by FastAPI routers.

Grouping request/response models in one module keeps router files focused
on HTTP wiring and makes the API surface easier to audit — run `grep class`
here to see every shape the server accepts or emits.

Conventions:
- `*Request` — body model for POST / PUT endpoints
- `*Response` — return type for endpoints that don't use `dict`
- Webhook payloads from 3rd-party providers use `extra='allow'` for
  forward-compat so new upstream fields never break parsing.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Auth (api/routers/auth.py)
# ---------------------------------------------------------------------------


class DemoAuthRequest(BaseModel):
    """Body for `POST /api/auth/demo` — shared password for read-only demo."""

    password: str = Field(..., min_length=1)


class VerifyCodeRequest(BaseModel):
    """Body for `POST /api/auth/verify-code` — one-time code from `/web` bot
    command, exchanged for a session JWT.
    """

    code: str = Field(..., min_length=1)


class TelegramWidgetAuthRequest(BaseModel):
    """Telegram Login Widget callback payload for `POST /api/auth/telegram-widget`.

    Extra fields are allowed for forward-compat — they're included in the
    HMAC-SHA256 data-check-string per Telegram's spec, so the verifier must
    see all of them even if we don't read them here.
    """

    model_config = ConfigDict(extra="allow")

    id: int
    auth_date: int
    hash: str
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None


class SetLanguageRequest(BaseModel):
    """Body for `PUT /api/auth/language` — flip the user's preferred UI
    language (also used by the server for `_()` calls on scheduled reports).
    """

    language: Literal["ru", "en"]


# ---------------------------------------------------------------------------
# Intervals.icu (api/routers/intervals/)
# ---------------------------------------------------------------------------


class IntervalsAuthInitResponse(BaseModel):
    """Response of `POST /api/intervals/auth/init` — the signed Intervals.icu
    authorize URL that the frontend navigates to via `window.location.assign`.
    """

    authorize_url: str


class IntervalsWebhookEvent(BaseModel):
    """Single event inside an Intervals.icu webhook payload. Forward-compat:
    `extra='allow'` so new fields added by Intervals.icu don't break parsing.
    """

    model_config = ConfigDict(extra="allow")

    athlete_id: str
    type: str
    timestamp: str | None = None
    records: list[dict[str, Any]] = Field(default_factory=list)


class IntervalsWebhookPayload(BaseModel):
    """Top-level shape of `POST /api/intervals/webhook` body. Includes a
    shared `secret` that Intervals.icu copies from its Webhook Secret setting
    into every request — we verify it against `INTERVALS_WEBHOOK_SECRET`.
    """

    model_config = ConfigDict(extra="allow")

    secret: str | None = None
    events: list[IntervalsWebhookEvent] = Field(default_factory=list)
