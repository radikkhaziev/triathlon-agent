"""Tests for `PUT /api/auth/sports` and the SportsUpdateRequest DTO.

Contract:
- min_length=1 / max_length=3 enforced by Pydantic
- only `swim` / `ride` / `run` accepted (Literal)
- duplicates collapsed silently, output canonicalised (alphabetical sort)
- 401 unauthenticated, 403 demo, 200 owner/athlete
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from api.dto import SportsUpdateRequest
from api.routers.auth import set_sports


def _user(*, user_id: int = 1, role: str = "athlete") -> SimpleNamespace:
    return SimpleNamespace(id=user_id, role=role)


class TestSportsUpdateRequest:
    """DTO-level validation runs at the Pydantic boundary, before the handler.
    Bad input never reaches the DB writer."""

    def test_accepts_single_sport(self):
        body = SportsUpdateRequest(sports=["run"])
        assert body.canonical() == ["run"]

    def test_accepts_all_three(self):
        body = SportsUpdateRequest(sports=["swim", "ride", "run"])
        # Canonical = alphabetical
        assert body.canonical() == ["ride", "run", "swim"]

    def test_canonical_sorts(self):
        body = SportsUpdateRequest(sports=["run", "ride"])
        assert body.canonical() == ["ride", "run"]

    def test_canonical_dedupes(self):
        body = SportsUpdateRequest(sports=["run", "run", "ride"])
        assert body.canonical() == ["ride", "run"]

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            SportsUpdateRequest(sports=[])

    def test_rejects_unknown_sport(self):
        with pytest.raises(ValidationError):
            SportsUpdateRequest(sports=["fitness"])

    def test_rejects_more_than_three(self):
        # The four-element case can only happen with duplicates (since enum has
        # only 3 values), but the max_length check fires before Literal does.
        with pytest.raises(ValidationError):
            SportsUpdateRequest(sports=["run", "ride", "swim", "run"])


class TestSetSportsEndpoint:
    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self):
        body = SportsUpdateRequest(sports=["run"])
        with pytest.raises(HTTPException) as exc:
            await set_sports(body=body, user=None)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_demo_role_returns_403(self):
        body = SportsUpdateRequest(sports=["run"])
        with pytest.raises(HTTPException) as exc:
            await set_sports(body=body, user=_user(role="demo"))
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_athlete_persists_canonical(self):
        body = SportsUpdateRequest(sports=["run", "ride"])
        with patch("api.routers.auth.User.update_sports", new=AsyncMock()) as mock_update:
            result = await set_sports(body=body, user=_user(role="athlete"))

        # Canonical = sorted, dedup'd
        mock_update.assert_awaited_once_with(1, ["ride", "run"])
        assert result == {"sports": ["ride", "run"]}

    @pytest.mark.asyncio
    async def test_owner_can_update(self):
        body = SportsUpdateRequest(sports=["swim"])
        with patch("api.routers.auth.User.update_sports", new=AsyncMock()) as mock_update:
            result = await set_sports(body=body, user=_user(role="owner"))

        mock_update.assert_awaited_once_with(1, ["swim"])
        assert result == {"sports": ["swim"]}

    @pytest.mark.asyncio
    async def test_duplicates_dedup_through_canonical(self):
        body = SportsUpdateRequest(sports=["ride", "ride"])
        with patch("api.routers.auth.User.update_sports", new=AsyncMock()) as mock_update:
            result = await set_sports(body=body, user=_user(role="athlete"))

        mock_update.assert_awaited_once_with(1, ["ride"])
        assert result == {"sports": ["ride"]}
