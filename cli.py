import argparse
import code
import re
from calendar import monthrange
from datetime import date, timedelta

from config import settings
from data.db import User, Wellness, get_session
from data.db.dto import UserDTO
from data.garmin import importer as garmin_importer
from data.garmin.parser import GarminExportParser
from tasks.actors.activities import actor_fetch_user_activities
from tasks.actors.wellness import actor_user_wellness


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
    p_gi.add_argument("source", help="Path to extracted export directory or ZIP file")
    p_gi.add_argument("--types", default="all", help="Comma-separated: sleep,daily,readiness,health (default: all)")
    p_gi.add_argument("--period", default=None, help="Date filter: 2025Q1, 2025-11, 2025-01-01:2025-06-30")
    p_gi.add_argument("--force", action="store_true", help="Overwrite existing records")
    p_gi.add_argument("--dry-run", action="store_true", help="Parse and validate only, don't write to DB")

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
    from urllib.request import urlopen

    path = Path(source)

    # URL → download (HTTPS only, 5 min timeout)
    if source.startswith("https://"):
        tmp_dir = Path(tempfile.mkdtemp(prefix="garmin-import-"))
        zip_path = tmp_dir / "export.zip"
        print(f"Downloading {source[:80]}...")
        with urlopen(source, timeout=300) as resp, open(zip_path, "wb") as out:
            while chunk := resp.read(1024 * 1024):
                out.write(chunk)
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
    enabled = set(types.split(",")) if types != "all" else {"sleep", "daily", "readiness", "health"}

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
        force=force,
    )
    print(f"Imported: {', '.join(f'{k}: {v}' for k, v in counts.items())}")


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
