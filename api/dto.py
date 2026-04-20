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


class PerSportTargetsPayload(BaseModel):
    """Optional per-sport CTL split inside :class:`AthleteGoalPatchRequest`.

    CTL in single-digit to low-triple-digit range in practice; clamping
    rejects obvious garbage without blocking legit high-volume athletes.
    """

    swim: float | None = Field(default=None, ge=0, le=200)
    ride: float | None = Field(default=None, ge=0, le=200)
    run: float | None = Field(default=None, ge=0, le=200)


class AthleteGoalPatchRequest(BaseModel):
    """Body for `PATCH /api/athlete/goal/{goal_id}` — local-only overlay fields.

    Only ``ctl_target`` and ``per_sport_targets`` are writable from the UI; race
    name/date/category live in Intervals.icu and must go through chat +
    ``suggest_race`` MCP tool (which pushes the edit to Intervals.icu).

    Missing fields are left untouched — the router distinguishes absence from
    explicit ``null`` via ``model_fields_set`` / ``exclude_unset`` so a PATCH
    does not silently clear untouched columns.
    """

    ctl_target: float | None = Field(default=None, ge=0, le=200)
    per_sport_targets: PerSportTargetsPayload | None = None


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
    sport_settings: list[dict[str, Any]] = Field(default_factory=list, alias="sportSettings")
    # ACTIVITY_* events deliver data via `activity` (single object, not array)
    activity: dict[str, Any] | None = None
    # APP_SCOPE_CHANGED delivers scope info as top-level event fields
    scope: str | None = None
    deauthorized: bool | None = None


class IntervalsWebhookPayload(BaseModel):
    """Top-level shape of `POST /api/intervals/webhook` body. Includes a
    shared `secret` that Intervals.icu copies from its Webhook Secret setting
    into every request — we verify it against `INTERVALS_WEBHOOK_SECRET`.
    """

    model_config = ConfigDict(extra="allow")

    secret: str | None = None
    events: list[IntervalsWebhookEvent] = Field(default_factory=list)
