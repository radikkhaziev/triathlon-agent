import argparse
import code
import re
import time
from calendar import monthrange
from datetime import date, timedelta

import httpx
from sqlalchemy import select

from config import settings
from data.db import User, Wellness, get_session, get_sync_session
from data.db.dto import UserDTO
from data.garmin import importer as garmin_importer
from data.garmin.parser import GarminExportParser
from tasks.actors.activities import actor_fetch_user_activities
from tasks.actors.wellness import actor_user_wellness
from tasks.tools import TelegramTool


def main() -> None:
    parser = argparse.ArgumentParser(prog="triathlon-agent", description="Triathlon AI Agent CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("shell", help="Open interactive Python shell with app context")

    p_sync = sub.add_parser("sync-settings", help="Sync athlete settings & goals from Intervals.icu")
    p_sync.add_argument("user_id", type=int)

    p_sw = sub.add_parser("sync-wellness", help="Force re-sync wellness + HRV/RHR pipelines day by day")
    p_sw.add_argument("user_id", type=int)
    p_sw.add_argument(
        "period",
        nargs="?",
        default=None,
        help="2025Q4 | 2025-11 | 2025-01-01:2025-03-31 (default: 180d)",
    )

    p_sa = sub.add_parser("sync-activities", help="Force re-sync activities day by day")
    p_sa.add_argument("user_id", type=int)
    p_sa.add_argument(
        "period",
        nargs="?",
        default=None,
        help="2025Q4 | 2025-11 | 2025-01-01:2025-03-31 (default: 180d)",
    )
    p_sa.add_argument("--force", action="store_true", help="Force re-process even if data unchanged")

    p_tl = sub.add_parser("sync-training-log", help="Recalculate training log from existing activities")
    p_tl.add_argument("user_id", type=int)
    p_tl.add_argument(
        "period",
        nargs="?",
        default=None,
        help="2025Q4 | 2025-11 | 2025-01-01:2025-03-31 (default: 180d)",
    )

    p_gi = sub.add_parser("import-garmin", help="Import Garmin GDPR export")
    p_gi.add_argument("user_id", type=int)
    p_gi.add_argument("source", help="Path to dir/ZIP or HTTPS URL (wrap URLs in quotes!)")
    p_gi.add_argument("--types", default="all", help="Comma-separated: sleep,daily,readiness,health (default: all)")
    p_gi.add_argument("--period", default=None, help="Date filter: 2025Q1, 2025-11, 2025-01-01:2025-06-30")
    p_gi.add_argument("--force", action="store_true", help="Overwrite existing records")
    p_gi.add_argument("--dry-run", action="store_true", help="Parse and validate only, don't write to DB")

    p_br = sub.add_parser("backfill-races", help="Create Race records for historical race activities")
    p_br.add_argument("user_id", type=int)
    p_br.add_argument(
        "period", nargs="?", default=None, help="2025Q4 | 2025-11 | 2025-01-01:2025-03-31 (default: 180d)"
    )

    p_bm = sub.add_parser(
        "broadcast-migration",
        help="Notify active athletes about bot migration to @endurai_bot. Uses the bot token from .env "
        "(should be the OLD bot token when run locally before migration). See docs/BOT_MIGRATION_SPEC.md §2.1.",
    )
    p_bm.add_argument("--dry-run", action="store_true", help="List recipients without sending")

    p_bs = sub.add_parser(
        "bootstrap-sync",
        help="Trigger OAuth bootstrap backfill manually. With --force, resets any existing state "
        "(cursor back to oldest) so re-run overwrites a completed/failed row. "
        "See docs/OAUTH_BOOTSTRAP_SYNC_SPEC.md §10.4.",
    )
    p_bs.add_argument("user_id", type=int)
    p_bs.add_argument("--period", type=int, default=365, help="How many days of history to load (default: 365)")
    p_bs.add_argument("--force", action="store_true", help="Overwrite existing state even if recently completed")

    p_rrt = sub.add_parser(
        "reprocess-ramp-test",
        help="Re-run HRV threshold detector on a single activity to back-fill `hrvt2_pace`. "
        "Used after the v2c3d4e5f6a7 migration so existing ramp tests get pace at HRVT2 populated, "
        "letting `actor_update_zones` push the correct threshold_pace to Intervals.icu.",
    )
    p_rrt.add_argument("user_id", type=int)
    p_rrt.add_argument("activity_id", type=str, help="Intervals.icu activity id, e.g. i146377549")
    p_rrt.add_argument(
        "--push",
        action="store_true",
        help="After patching hrvt2_pace, dispatch actor_update_zones to push HRVT2 + pace to Intervals.icu",
    )

    args = parser.parse_args()

    if args.command == "shell":
        _shell()
    elif args.command == "sync-settings":
        _sync_settings(args.user_id)
    elif args.command == "sync-wellness":
        _sync_wellness(args.user_id, args.period)
    elif args.command == "sync-activities":
        _sync_activities(args.user_id, args.period, force=args.force)
    elif args.command == "sync-training-log":
        _sync_training_log(args.user_id, args.period)
    elif args.command == "import-garmin":
        _import_garmin(args.user_id, args.source, args.types, args.period, args.force, args.dry_run)
    elif args.command == "backfill-races":
        _backfill_races(args.user_id, args.period)
    elif args.command == "broadcast-migration":
        _broadcast_migration(dry_run=args.dry_run)
    elif args.command == "bootstrap-sync":
        _bootstrap_sync(args.user_id, period_days=args.period, force=args.force)
    elif args.command == "reprocess-ramp-test":
        _reprocess_ramp_test(args.user_id, args.activity_id, push=args.push)


def _resolve_user(user_id: int) -> UserDTO:
    user = User.get_by_id(user_id)
    if not user or not user.athlete_id:
        raise SystemExit(f"User {user_id} not found or has no athlete_id")
    return UserDTO.model_validate(user)


def _parse_period(period: str | None) -> tuple[date, date]:
    """Parse period string into (start, end) dates.

    Formats: 2025Q4, 2025-11, 2025-01-01:2025-03-31, None (180 days).
    """
    if not period:
        end = date.today()
        return end - timedelta(days=180), end

    # Quarter: 2025Q4
    m = re.match(r"^(\d{4})Q([1-4])$", period, re.IGNORECASE)
    if m:
        year, q = int(m.group(1)), int(m.group(2))
        start_month = (q - 1) * 3 + 1
        end_month = start_month + 2
        return date(year, start_month, 1), date(year, end_month, monthrange(year, end_month)[1])

    # Month: 2025-11
    m = re.match(r"^(\d{4})-(\d{2})$", period)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        return date(year, month, 1), date(year, month, monthrange(year, month)[1])

    # Range: 2025-01-01:2025-03-31
    m = re.match(r"^(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2})$", period)
    if m:
        return date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))

    raise SystemExit(f"Unknown period format: {period}. Use 2025Q4, 2025-11, or 2025-01-01:2025-03-31")


def _sync_settings(user_id: int) -> None:
    from tasks.actors.athlets import actor_sync_athlete_goals, actor_sync_athlete_settings

    user = _resolve_user(user_id)
    actor_sync_athlete_settings.send(user=user)
    actor_sync_athlete_goals.send(user=user)
    print(f"Queued sync-settings + sync-goals for user {user_id}")


def _sync_wellness(user_id: int, period: str | None) -> None:
    """Force re-sync wellness with HRV/RHR/recovery pipelines, day by day sequentially."""
    user = _resolve_user(user_id)
    start, end = _parse_period(period)

    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)

    print(f"sync-wellness user {user_id}: {start} → {end} ({len(days)} days, force=True)")

    delay_per_day_ms = 20_000
    for i, day in enumerate(days):
        actor_user_wellness.send_with_options(
            kwargs={"user": user, "dt": day.isoformat(), "force": True},
            delay=i * delay_per_day_ms,
        )

    print(f"Queued: {len(days)} days (wellness+HRV+RHR+recovery, {delay_per_day_ms // 1000}s apart)")


def _sync_activities(user_id: int, period: str | None, force: bool = False) -> None:
    """Force re-sync activities day by day sequentially."""
    user = _resolve_user(user_id)
    start, end = _parse_period(period)

    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)

    mode = " (FORCE)" if force else ""
    print(f"sync-activities user {user_id}: {start} → {end} ({len(days)} days{mode})")

    delay_per_day_ms = 20_000
    for i, day in enumerate(days):
        actor_fetch_user_activities.send_with_options(
            kwargs={"user": user, "oldest": day.isoformat(), "newest": day.isoformat(), "force": force},
            delay=i * delay_per_day_ms,
        )

    print(f"Queued: {len(days)} days (activities, {delay_per_day_ms // 1000}s apart)")


def _sync_training_log(user_id: int, period: str | None) -> None:
    """Recalculate training log (PRE+ACTUAL+POST) from existing activities."""
    from sqlalchemy import distinct, select, update

    from data.db import Activity, TrainingLog, get_sync_session
    from tasks.actors.training_log import actor_fill_training_log, actor_fill_training_log_post

    user = _resolve_user(user_id)
    start, end = _parse_period(period)

    with get_sync_session() as s:
        # Reset POST data so it gets recalculated with correct dt+1
        reset = s.execute(
            update(TrainingLog)
            .where(
                TrainingLog.user_id == user_id,
                TrainingLog.date >= str(start),
                TrainingLog.date <= str(end),
                TrainingLog.post_recovery_score.isnot(None),
            )
            .values(
                post_recovery_score=None,
                post_hrv_delta_pct=None,
                post_rhr_today=None,
                post_sleep_score=None,
                post_ra_pct=None,
                recovery_delta=None,
            )
        )
        s.commit()
        if reset.rowcount:
            print(f"Reset POST data for {reset.rowcount} entries")

        dates = (
            s.execute(
                select(distinct(Activity.start_date_local))
                .where(
                    Activity.user_id == user_id,
                    Activity.start_date_local >= str(start),
                    Activity.start_date_local <= str(end),
                )
                .order_by(Activity.start_date_local)
            )
            .scalars()
            .all()
        )

    if not dates:
        print(f"sync-training-log user {user_id}: no activities found in {start} → {end}")
        return

    print(f"sync-training-log user {user_id}: {start} → {end} ({len(dates)} activity dates)")

    delay_ms = 5_000
    for i, dt in enumerate(dates):
        next_day = (date.fromisoformat(dt) + timedelta(days=1)).isoformat()

        actor_fill_training_log.send_with_options(
            kwargs={"user": user, "dt": dt},
            delay=i * delay_ms,
        )
        actor_fill_training_log_post.send_with_options(
            kwargs={"user": user, "dt": next_day},
            delay=i * delay_ms + 2_000,
        )

    print(f"Queued: {len(dates)} dates (training log PRE+ACTUAL+POST, {delay_ms // 1000}s apart)")


def _resolve_garmin_source(source: str) -> str:
    """Resolve source: URL → download + extract, ZIP → extract, DIR → as-is. Returns directory path."""
    import tempfile
    import zipfile
    from pathlib import Path
    from urllib.error import HTTPError
    from urllib.request import urlopen

    path = Path(source)

    # URL → download (HTTPS only, 5 min timeout)
    if source.startswith("https://"):
        tmp_dir = Path(tempfile.mkdtemp(prefix="garmin-import-"))
        zip_path = tmp_dir / "export.zip"
        print(f"Downloading {source[:80]}...")
        try:
            with urlopen(source, timeout=300) as resp, open(zip_path, "wb") as out:
                while chunk := resp.read(1024 * 1024):
                    out.write(chunk)
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]
            raise SystemExit(
                f"Download failed: HTTP {e.code} {e.reason}\n"
                f"Response: {body}\n"
                "If the link is valid, try downloading manually and pass the local path:\n"
                "  import-garmin <user_id> /path/to/export.zip"
            ) from None
        print(f"Downloaded: {zip_path.stat().st_size / 1024 / 1024:.1f} MB")
        path = zip_path

    # ZIP → extract
    if path.is_file() and path.suffix == ".zip":
        extract_dir = path.parent / path.stem
        print(f"Extracting to {extract_dir}...")
        with zipfile.ZipFile(path) as zf:
            zf.extractall(extract_dir)
        return str(extract_dir)

    if path.is_dir():
        return str(path)

    raise SystemExit(f"Source not found or unsupported: {source}")


def _import_garmin(user_id: int, source: str, types: str, period: str | None, force: bool, dry_run: bool) -> None:
    """Import Garmin GDPR export data."""
    _resolve_user(user_id)  # validate user exists

    export_dir = _resolve_garmin_source(source)
    period_range = _parse_period(period) if period else None
    all_types = {"sleep", "daily", "readiness", "health", "load", "fitness", "race", "bio", "abnormal_hr"}
    enabled = set(types.split(",")) if types != "all" else all_types

    parser = GarminExportParser(export_dir)

    parsed: dict[str, list] = {}
    if "sleep" in enabled:
        parsed["sleep"] = parser.parse_sleep(period_range)
    if "daily" in enabled:
        parsed["daily"] = parser.parse_daily_summary(period_range)
    if "readiness" in enabled:
        parsed["readiness"] = parser.parse_training_readiness(period_range)
    if "health" in enabled:
        parsed["health"] = parser.parse_health_status(period_range)
    if "load" in enabled:
        parsed["load"] = parser.parse_training_load(period_range)
    if "fitness" in enabled:
        parsed["fitness"] = parser.parse_fitness_metrics(period_range)
    if "race" in enabled:
        parsed["race"] = parser.parse_race_predictions(period_range)
    if "bio" in enabled:
        parsed["bio"] = parser.parse_bio_metrics(period_range)
    if "abnormal_hr" in enabled:
        parsed["abnormal_hr"] = parser.parse_abnormal_hr_events(period_range)

    print(f"Parsed: {', '.join(f'{k}: {len(v)}' for k, v in parsed.items())}")

    if dry_run:
        print("Dry run — no data written to DB")
        return

    counts = garmin_importer.import_all(
        user_id,
        sleep=parsed.get("sleep"),
        daily=parsed.get("daily"),
        readiness=parsed.get("readiness"),
        health=parsed.get("health"),
        load=parsed.get("load"),
        fitness=parsed.get("fitness"),
        race=parsed.get("race"),
        bio=parsed.get("bio"),
        abnormal_hr=parsed.get("abnormal_hr"),
        force=force,
    )
    print(f"Imported: {', '.join(f'{k}: {v}' for k, v in counts.items())}")


def _backfill_races(user_id: int, period: str | None) -> None:
    """Create Race records for historical activities where is_race=True."""
    from sqlalchemy import select

    from data.db import Activity, ActivityDetail, Race, Wellness, get_sync_session

    _resolve_user(user_id)
    start, end = _parse_period(period)

    with get_sync_session() as s:
        race_activities = (
            s.execute(
                select(Activity)
                .where(
                    Activity.user_id == user_id,
                    Activity.is_race.is_(True),
                    Activity.start_date_local >= str(start),
                    Activity.start_date_local <= str(end),
                )
                .order_by(Activity.start_date_local)
            )
            .scalars()
            .all()
        )

        existing_ids = set(s.execute(select(Race.activity_id).where(Race.user_id == user_id)).scalars().all())

        created = 0
        for a in race_activities:
            if a.id in existing_ids:
                continue

            w = s.execute(
                select(Wellness).where(Wellness.user_id == user_id, Wellness.date == a.start_date_local)
            ).scalar_one_or_none()

            tsb = round(w.ctl - w.atl, 1) if w and w.ctl is not None and w.atl is not None else None
            detail = s.get(ActivityDetail, a.id)
            distance = detail.distance if detail else None
            avg_pace = (
                round(a.moving_time / (distance / 1000), 1) if distance and a.moving_time and distance > 0 else None
            )

            race = Race(
                user_id=user_id,
                activity_id=a.id,
                name=a.type or "Race",
                race_type="C",
                distance_m=distance,
                finish_time_sec=a.moving_time,
                avg_pace_sec_km=avg_pace,
                race_day_ctl=w.ctl if w else None,
                race_day_atl=w.atl if w else None,
                race_day_tsb=tsb,
                race_day_recovery_score=w.recovery_score if w else None,
                race_day_weight=w.weight if w else None,
            )
            s.add(race)
            created += 1

        s.commit()

    print(f"backfill-races user {user_id}: {len(race_activities)} race activities, {created} new Race records")


_MIGRATION_MESSAGE = (
    "🔄 **Бот переезжает!**\n\n"
    "Основной бот теперь @endurai_bot — перейди и нажми /start. "
    "Все твои данные (настройки, история, тренировки) сохранятся автоматически.\n\n"
    "Этот бот скоро будет отключён."
)


def _broadcast_migration(dry_run: bool) -> None:
    """Send a one-time migration notice to every active athlete in the DB.

    Scope: `is_active=True AND role='athlete'` — viewers and blocked users are
    skipped. Athletes are the only group with actual data at stake (wellness,
    training plan, activities) and a reason to care about migration.

    Uses `settings.TELEGRAM_BOT_TOKEN` — when run locally **before** the migration,
    this should point at the OLD bot (@radikrunbot), which is where users currently
    have chats. `TelegramTool` handles 403 (blocked by user) gracefully: marks them
    inactive in DB and returns None. Rate limit: Telegram caps bot messages at
    ~30/sec globally; a small sleep between sends is insurance.
    """
    with get_sync_session() as s:
        users = list(s.execute(select(User).where(User.is_active.is_(True), User.role == "athlete")).scalars().all())

    if not users:
        print("No active athletes found.")
        return

    print(f"Found {len(users)} active athlete(s)")
    for u in users:
        print(f"  id={u.id} chat_id={u.chat_id} @{u.username or '-'}")

    if dry_run:
        print("\nDry run — nothing sent.")
        return

    confirm = input(f"\nSend migration message to {len(users)} user(s)? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    tool = TelegramTool()
    sent = 0
    failed: list[tuple[int, str]] = []

    for u in users:
        try:
            result = tool.send_message(text=_MIGRATION_MESSAGE, chat_id=u.chat_id, markdown=True)
            if result is None:
                # 403 — TelegramTool marked user inactive already
                failed.append((u.id, "blocked (403)"))
                print(f"  ✗ id={u.id} blocked, marked inactive")
            else:
                sent += 1
                print(f"  ✓ id={u.id} sent")
        except httpx.HTTPStatusError as e:
            failed.append((u.id, f"HTTP {e.response.status_code}"))
            print(f"  ✗ id={u.id} HTTP {e.response.status_code}")
        except Exception as e:
            failed.append((u.id, type(e).__name__))
            print(f"  ✗ id={u.id} {type(e).__name__}: {e}")

        time.sleep(0.1)  # 10 req/sec — well under Telegram's 30/sec bot limit

    print(f"\nSummary: sent={sent}, failed={len(failed)}")
    if failed:
        for uid, reason in failed:
            print(f"  - user {uid}: {reason}")


def _bootstrap_sync(user_id: int, period_days: int, force: bool) -> None:
    """Kick off the chunk-recursive bootstrap for an existing user.

    Idempotency lives in the actor (§7 in spec). With ``--force`` we reset the
    state row here so the actor's "status != 'running' → skip" guard fires on
    a fresh ``running`` row, matching the OAuth-callback call shape.
    """
    from datetime import datetime, timezone

    from data.db import UserBackfillState
    from tasks.actors.bootstrap import actor_bootstrap_step

    user = _resolve_user(user_id)
    today = date.today()
    oldest = today - timedelta(days=period_days)
    newest = today - timedelta(days=1)

    if force:
        with get_sync_session() as session:
            UserBackfillState.start(
                user_id=user_id,
                period_days=period_days,
                oldest_dt=oldest,
                newest_dt=newest,
                session=session,
            )
        print(f"bootstrap-sync: reset state for user {user_id} (period={period_days}d, {oldest} → {newest})")

    actor_bootstrap_step.send(
        user=user,
        cursor_dt=oldest.isoformat(),
        period_days=period_days,
    )
    now_iso = datetime.now(timezone.utc).isoformat()
    print(f"bootstrap-sync: queued at {now_iso} (user={user_id}, period={period_days}d)")


def _reprocess_ramp_test(user_id: int, activity_id: str, *, push: bool) -> None:
    """Re-run HRV threshold detection on one activity, patch the HRVT2-derived
    threshold fields (``hrvt2_pace`` for Run, ``hrvt2_power`` for Ride).

    Migrations ``v2c3d4e5f6a7`` (hrvt2_pace) and ``w3d4e5f6a7b8`` (hrvt2_power)
    add columns that older ramp tests don't have populated. The drift detector
    pushes pace-at-HRVT2 to Intervals' ``threshold_pace`` and pow-at-HRVT2 to
    ``ftp``, so we back-fill these before ``actor_update_zones`` runs.

    Only the HRVT2-derived fields are patched — other thresholds (HRVT1/2 HR,
    R², confidence) stay as the original processing wrote them, since
    re-running the detector can produce slightly different rounding and we
    don't want to perturb fields that are already consistent with the user's
    current zones.
    """
    import asyncio

    from sqlalchemy import select

    from data.db import Activity, ActivityDetail, ActivityHrv
    from data.hrv_activity import detect_hrv_thresholds

    user = _resolve_user(user_id)

    async def _run() -> tuple[dict, dict, str, bool]:
        """Load → detect → patch in a single event loop (asyncpg binds
        connections to one loop).

        Returns ``(new, old, sport, is_latest)`` where ``new``/``old`` carry
        the HRVT2-derived fields actually patched (one or both of
        ``hrvt2_pace`` / ``hrvt2_power``). ``is_latest`` flags whether this
        activity is the newest valid ramp for its sport — drift detector
        reads ``LIMIT 1 ORDER BY date DESC``, so patching anything else has
        no effect on the next ``actor_update_zones`` dispatch.
        """
        async with get_session() as session:
            activity = await session.get(Activity, activity_id)
            hrv = await session.get(ActivityHrv, activity_id)
            detail = await session.get(ActivityDetail, activity_id)

            if not activity:
                raise SystemExit(f"Activity {activity_id} not found")
            if activity.user_id != user_id:
                raise SystemExit(f"Activity {activity_id} belongs to user {activity.user_id}, not {user_id}")
            if not hrv or not hrv.dfa_timeseries:
                raise SystemExit(f"No HRV timeseries on {activity_id} (run activity processing first)")

            icu_intervals = ((detail.intervals or {}).get("icu_intervals") or []) if detail else []
            work_segs = [
                (int(iv["start_time"]), int(iv["end_time"]))
                for iv in icu_intervals
                if iv.get("type") == "WORK" and iv.get("start_time") is not None and iv.get("end_time") is not None
            ]

            print(
                f"Re-running detector on {activity_id} ({activity.type}, "
                f"{len(hrv.dfa_timeseries)} pts, {len(work_segs)} WORK segs)"
            )
            result = detect_hrv_thresholds(hrv.dfa_timeseries, activity.type or "", work_segments=work_segs)
            if not result:
                raise SystemExit("Detection returned None — see data/hrv_activity.py for rejection criteria")

            new_pace = result.get("hrvt2_pace")
            new_power = result.get("hrvt2_power")
            print(
                f"Detector → hrvt2_hr={result.get('hrvt2_hr')}, "
                f"hrvt2_pace={new_pace}, hrvt2_power={new_power}, "
                f"R²={result.get('r_squared')}"
            )
            if not new_pace and not new_power:
                raise SystemExit(
                    "Detector did not produce hrvt2_pace or hrvt2_power " "(no speed/power data, or HRVT2 out of range)"
                )

            old = {"hrvt2_pace": hrv.hrvt2_pace, "hrvt2_power": hrv.hrvt2_power}
            new = {"hrvt2_pace": new_pace, "hrvt2_power": new_power}
            if new_pace:
                hrv.hrvt2_pace = new_pace
            if new_power:
                hrv.hrvt2_power = new_power
            await session.commit()

            latest_id = (
                await session.execute(
                    select(Activity.id)
                    .join(ActivityHrv, ActivityHrv.activity_id == Activity.id)
                    .where(
                        Activity.user_id == user_id,
                        Activity.type == activity.type,
                        ActivityHrv.processing_status == "processed",
                        ActivityHrv.hrvt2_hr.isnot(None),
                        ActivityHrv.hrv_quality.in_(["good", "moderate"]),
                    )
                    .order_by(Activity.start_date_local.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            return new, old, activity.type, latest_id == activity_id

    new, old, sport, is_latest = asyncio.run(_run())
    for field in ("hrvt2_pace", "hrvt2_power"):
        if new[field] is not None:
            print(f"✓ Patched activity_hrv.{field} for {activity_id}: {old[field]!r} → {new[field]!r}")

    if push:
        if not is_latest:
            raise SystemExit(
                f"Refusing --push: {activity_id} is not the latest valid {sport} ramp test. "
                "Drift detector reads only the newest row, so this push would act on a different "
                "activity. Re-run reprocess-ramp-test against the latest one, or drop --push."
            )
        from tasks.actors.athlets import actor_update_zones

        actor_update_zones.send(user=user)
        print(
            f"✓ Queued actor_update_zones for user {user_id} — "
            "drift detector will see the new HRVT2 fields and push to Intervals.icu"
        )
    else:
        print("(dry-run — pass --push to dispatch actor_update_zones)")


def _shell() -> None:
    banner = (
        "Triathlon Agent Shell\n"
        "Available variables:\n"
        "  settings     - app settings\n"
        "  get_session  - async session context manager\n"
        "  Wellness  - wellness model (use Wellness.get(date))\n"
    )
    import asyncio

    ctx = {
        "settings": settings,
        "get_session": get_session,
        "Wellness": Wellness,
        "asyncio": asyncio,
        "date": date,
        "timedelta": timedelta,
    }
    code.interact(banner=banner, local=ctx)


if __name__ == "__main__":
    main()
