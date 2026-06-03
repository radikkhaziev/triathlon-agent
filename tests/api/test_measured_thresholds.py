"""Endpoint contract test for GET /api/athlete/measured-thresholds.

Complements the ORM-level tests in tests/db/test_measured_thresholds.py: here
we exercise the router wiring the unit tests can't see —

  • ``require_viewer`` gate (the endpoint is the read-only demo/viewer tour
    target; a regression to ``require_athlete`` would silently break it),
  • ``get_data_user_id`` resolution (viewer/demo tokens are minted as the
    owner, so the owner's measured data must come back).
"""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.deps import require_viewer
from api.routers.athlete import router as athlete_router
from data.db import Activity, ActivityHrv, get_session


@pytest.fixture
def client():
    test_app = FastAPI()
    test_app.include_router(athlete_router)
    # Viewer role resolves to the owner (id=1, seeded by conftest) — mirrors how
    # demo/viewer tokens are minted with the owner's chat_id.
    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.role = "viewer"
    mock_user.is_active = True
    test_app.dependency_overrides[require_viewer] = lambda: mock_user
    return AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")


async def _seed_measured(user_id: int) -> None:
    async with get_session() as session:
        session.add(Activity(id="b1", user_id=user_id, start_date_local="2026-05-12", type="Ride", moving_time=3000))
        session.add(
            ActivityHrv(
                activity_id="b1",
                activity_type="Ride",
                processing_status="processed",
                hrv_quality="good",
                hrvt2_hr=166.0,
                hrvt2_power=240.0,
                hrvt2_confidence="high",
            )
        )
        await session.commit()


class TestMeasuredThresholdsEndpoint:
    async def test_viewer_sees_owner_measured(self, _test_db, client):
        await _seed_measured(1)
        async with client as c:
            r = await c.get("/api/athlete/measured-thresholds")
        assert r.status_code == 200
        body = r.json()
        assert len(body["thresholds"]) == 1
        tile = body["thresholds"][0]
        assert tile["sport"] == "Ride"
        assert tile["hrvt2_power"] == 240.0
        assert tile["hrvt2_confidence"] == "high"

    async def test_empty_when_no_measured(self, _test_db, client):
        async with client as c:
            r = await c.get("/api/athlete/measured-thresholds")
        assert r.status_code == 200
        assert r.json() == {"thresholds": []}
