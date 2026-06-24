"""DB-backed tests for `data/endurance_score_service.py`.

Pure formulas live in `test_endurance_score.py`; this file covers the bits
that touch Postgres — currently the detrain-decay CTL-peak fetch (spec §13.1),
whose tenant-scoping and 182d window boundary are security/correctness
sensitive. Uses the patched test DB (no `real_db` marker), so each test runs
against a freshly-truncated schema with the conftest-seeded user 1.
"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from data.db import User, Wellness, get_sync_session
from data.endurance_score import EnduranceComponents, EnduranceScoreResult, PerSport
from data.endurance_score_service import _fetch_ctl_peak_26w, _result_from_row, _serialize_components

_REF = date(2026, 2, 1)


def _add_wellness(session, user_id: int, dt: date, ctl: float) -> None:
    session.add(
        Wellness(
            user_id=user_id,
            date=dt.isoformat(),
            ctl=ctl,
            updated=datetime.now(timezone.utc),
        )
    )


class TestFetchCtlPeak26w:
    def test_returns_max_in_window(self):
        with get_sync_session() as s:
            _add_wellness(s, 1, _REF, 19.6)
            _add_wellness(s, 1, _REF - timedelta(days=90), 60.0)  # in-window peak
            _add_wellness(s, 1, _REF - timedelta(days=30), 45.0)
            s.commit()
            assert _fetch_ctl_peak_26w(1, _REF, s) == 60.0

    def test_excludes_rows_before_182d_window(self):
        with get_sync_session() as s:
            _add_wellness(s, 1, _REF, 20.0)
            _add_wellness(s, 1, _REF - timedelta(days=183), 80.0)  # just outside → excluded
            s.commit()
            assert _fetch_ctl_peak_26w(1, _REF, s) == 20.0

    def test_includes_exact_182d_boundary(self):
        with get_sync_session() as s:
            _add_wellness(s, 1, _REF, 20.0)
            _add_wellness(s, 1, _REF - timedelta(days=182), 70.0)  # boundary is inclusive
            s.commit()
            assert _fetch_ctl_peak_26w(1, _REF, s) == 70.0

    def test_excludes_future_rows(self):
        with get_sync_session() as s:
            _add_wellness(s, 1, _REF, 25.0)
            _add_wellness(s, 1, _REF + timedelta(days=10), 90.0)  # after ref_date → excluded
            s.commit()
            assert _fetch_ctl_peak_26w(1, _REF, s) == 25.0

    def test_tenant_scoped(self):
        with get_sync_session() as s:
            if s.get(User, 2) is None:
                s.add(User(id=2, chat_id="tenant2", role="athlete"))
            _add_wellness(s, 1, _REF, 30.0)
            _add_wellness(s, 2, _REF - timedelta(days=10), 99.0)  # other tenant's higher peak must not leak
            s.commit()
            assert _fetch_ctl_peak_26w(1, _REF, s) == 30.0

    def test_none_when_no_history(self):
        with get_sync_session() as s:
            assert _fetch_ctl_peak_26w(1, _REF, s) is None


class TestComponentsDetrainRoundTrip:
    """Detrain fields (spec §13.1) survive the components-JSONB round-trip, and
    pre-Phase-3 rows without the keys deserialize to safe defaults. Pure — no
    DB — so plain `def` (no session, no autouse fixture needed)."""

    def _result(self, *, detrain_factor: float, ctl_peak_26w: float | None) -> EnduranceScoreResult:
        return EnduranceScoreResult(
            score=3700,
            zone_id="recovering",
            vo2max_composite=43.1,
            components=EnduranceComponents(base=3700, long_term=200, recent=0, duration=0, consistency=0, recovery=0),
            per_sport=[PerSport(name="Bike", pct=52.0, sub_score=None)],
            badge=None,
            detrain_factor=detrain_factor,
            ctl_peak_26w=ctl_peak_26w,
        )

    def test_serialize_writes_detrain_fields(self):
        c = _serialize_components(self._result(detrain_factor=0.852, ctl_peak_26w=60.0))
        assert c["detrain_factor"] == 0.852
        assert c["ctl_peak_26w"] == 60.0

    def test_round_trip_preserves_detrain_fields(self):
        c = _serialize_components(self._result(detrain_factor=0.852, ctl_peak_26w=60.0))
        row = SimpleNamespace(score=3700, vo2max_composite=43.1, components=c)
        restored = _result_from_row(row)
        assert restored.detrain_factor == 0.852
        assert restored.ctl_peak_26w == 60.0

    def test_old_row_without_keys_defaults_no_decay(self):
        # Pre-Phase-3 JSONB row — detrain keys absent → 1.0 / None (no decay).
        row = SimpleNamespace(score=5247, vo2max_composite=44.0, components={"base": 5000})
        restored = _result_from_row(row)
        assert restored.detrain_factor == 1.0
        assert restored.ctl_peak_26w is None
