import argparse
import asyncio
import code
import logging
import re
from datetime import date, timedelta

from bot.scheduler import daily_metrics_job, sync_activities_job
from config import settings
from data.database import ActivityDetailRow, ActivityRow, ScheduledWorkoutRow, WellnessRow, get_session
from data.intervals_client import IntervalsClient


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(prog="triathlon-agent", description="Triathlon AI Agent CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("shell", help="Open interactive Python shell with app context")

    backfill_parser = sub.add_parser(
        "backfill",
        help="Backfill wellness data from Intervals.icu. "
        "Accepts optional period: YYYY-MM-DD (single day), "
        "YYYY-MM-DD:YYYY-MM-DD (range), 2025Q3 (quarter), "
        "2025-03 (month). Default: last 180 days.",
    )
    backfill_parser.add_argument(
        "period",
        nargs="?",
        default=None,
        help="Period to backfill (e.g. 2025-09-01, 2025-01-01:2025-03-31, 2025Q3, 2025-03)",
    )

    sync_parser = sub.add_parser(
        "sync-workouts",
        help="Sync scheduled workouts from Intervals.icu. Default: 14 days ahead.",
    )
    sync_parser.add_argument(
        "days",
        nargs="?",
        type=int,
        default=14,
        help="Number of days ahead to sync (default: 14)",
    )

    activities_parser = sub.add_parser(
        "sync-activities",
        help="Sync completed activities from Intervals.icu. Default: 90 days back.",
    )
    activities_parser.add_argument(
        "days",
        nargs="?",
        type=int,
        default=90,
        help="Number of days back to sync (default: 90)",
    )

    details_parser = sub.add_parser(
        "backfill-details",
        help="Backfill activity details for activities without them. Default: all.",
    )
    details_parser.add_argument(
        "days",
        nargs="?",
        type=int,
        default=0,
        help="Limit to last N days (default: 0 = all)",
    )

    refetch_parser = sub.add_parser(
        "refetch-details",
        help="Re-fetch activity details from Intervals.icu for ALL activities (updates zone_times etc).",
    )
    refetch_parser.add_argument(
        "days",
        nargs="?",
        type=int,
        default=180,
        help="Number of days back to re-fetch (default: 180)",
    )

    sub.add_parser("backfill-max-zone", help="Backfill actual_max_zone_time for training_log entries")

    args = parser.parse_args()

    if args.command == "shell":
        _shell()
    elif args.command == "backfill":
        asyncio.run(_backfill(args.period))
    elif args.command == "sync-workouts":
        asyncio.run(_sync_workouts(args.days))
    elif args.command == "sync-activities":
        asyncio.run(_sync_activities(args.days))
    elif args.command == "backfill-details":
        asyncio.run(_backfill_details(args.days))
    elif args.command == "refetch-details":
        asyncio.run(_refetch_details(args.days))
    elif args.command == "backfill-max-zone":
        asyncio.run(_backfill_max_zone())


def _parse_period(period: str | None) -> tuple[date, date]:
    """Parse a period string into (start, end) dates.

    Supported formats:
        None                      -> last 180 days
        2025-09-01                -> single day
        2025-01-01:2025-03-31     -> explicit range
        2025Q3                    -> quarter (Jul-Sep)
        2025-03                   -> month
    """
    today = date.today()

    if period is None:
        return today - timedelta(days=180), today

    # Quarter: 2025Q3
    m = re.fullmatch(r"(\d{4})Q([1-4])", period)
    if m:
        year, q = int(m.group(1)), int(m.group(2))
        month_start = (q - 1) * 3 + 1
        start = date(year, month_start, 1)
        end_month = month_start + 2
        if end_month == 12:
            end = date(year, 12, 31)
        else:
            end = date(year, end_month + 1, 1) - timedelta(days=1)
        return start, min(end, today)

    # Month: 2025-03
    m = re.fullmatch(r"(\d{4})-(\d{2})", period)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        start = date(year, month, 1)
        if month == 12:
            end = date(year, 12, 31)
        else:
            end = date(year, month + 1, 1) - timedelta(days=1)
        return start, min(end, today)

    # Range: 2025-01-01:2025-03-31
    if ":" in period:
        parts = period.split(":", 1)
        return date.fromisoformat(parts[0]), date.fromisoformat(parts[1])

    # Single day: 2025-09-01
    d = date.fromisoformat(period)
    return d, d


async def _backfill(period: str | None = None) -> None:
    start, end = _parse_period(period)
    total_days = (end - start).days + 1
    print(f"Backfill: {start} -> {end} ({total_days} days)")

    dt = start
    processed = 0
    while dt <= end:
        processed += 1
        print(f"[{processed}/{total_days}] Processing {dt} ...")
        try:
            await daily_metrics_job(target_date=dt)
        except Exception as exc:
            print(f"  Error: {exc}")

        dt += timedelta(days=1)

        if dt <= end:
            await asyncio.sleep(3)

    print("Backfill completed.")


async def _sync_activities(days: int = 90) -> None:
    print(f"Syncing activities: last {days} days")
    count = await sync_activities_job(days=days)
    print(f"Synced {count} activities.")


async def _sync_workouts(days: int = 14) -> None:
    today = date.today()
    newest = today + timedelta(days=days)
    print(f"Syncing workouts: {today} → {newest} ({days} days)")

    client = IntervalsClient()
    workouts = await client.get_events(oldest=today, newest=newest)
    count = await ScheduledWorkoutRow.save_bulk(workouts, oldest=today, newest=newest)
    print(f"Synced {count} workouts.")


async def _backfill_details(days: int = 0) -> None:
    client = IntervalsClient()
    cutoff = str(date.today() - timedelta(days=days)) if days > 0 else None
    activities = await ActivityRow.get_without_details(since_date=cutoff)

    total = len(activities)
    print(f"Backfill details: {total} activities without details")

    for i, act in enumerate(activities, 1):
        print(f"[{i}/{total}] {act.id} ({act.start_date_local}, {act.type}) ...")
        try:
            detail = await client.get_activity_detail(act.id)
            if detail is None:
                print("  Not found (404), skipping")
                continue

            try:
                intervals_data = await client.get_activity_intervals(act.id)
            except Exception:
                print("  Warning: intervals fetch failed, saving detail only")
                intervals_data = None

            await ActivityDetailRow.save(act.id, detail, intervals_data)
        except Exception as exc:
            print(f"  Error: {exc}")

        if i < total:
            await asyncio.sleep(2)

    print("Backfill details completed.")


def _shell() -> None:
    banner = (
        "Triathlon Agent Shell\n"
        "Available variables:\n"
        "  settings     - app settings\n"
        "  get_session  - async session context manager\n"
        "  WellnessRow  - wellness model (use WellnessRow.get(date))\n"
        "  asyncio.run  - run async functions\n"
    )
    ctx = {
        "settings": settings,
        "get_session": get_session,
        "WellnessRow": WellnessRow,
        "asyncio": asyncio,
        "date": date,
        "timedelta": timedelta,
    }
    code.interact(banner=banner, local=ctx)


async def _refetch_details(days: int = 180) -> None:
    """Re-fetch activity details from Intervals.icu for existing activities.

    Unlike backfill-details (which skips existing), this re-fetches ALL activities
    to update columns added after initial fetch (e.g. hr_zone_times, power_zone_times).
    """
    client = IntervalsClient()
    cutoff = str(date.today() - timedelta(days=days))

    async with get_session() as session:
        from sqlalchemy import select as sa_select

        result = await session.execute(
            sa_select(ActivityRow)
            .where(ActivityRow.start_date_local >= cutoff)
            .order_by(ActivityRow.start_date_local.desc())
        )
        activities = list(result.scalars().all())

    total = len(activities)
    updated = 0
    print(f"Re-fetch details: {total} activities (last {days} days)")

    for i, act in enumerate(activities, 1):
        print(f"[{i}/{total}] {act.id} ({act.start_date_local}, {act.type}) ...", end=" ")
        try:
            detail = await client.get_activity_detail(act.id)
            if detail is None:
                print("404, skip")
                continue

            # Check if API returns zone_times
            has_zt = "icu_hr_zone_times" in detail or "icu_zone_times" in detail
            try:
                intervals_data = await client.get_activity_intervals(act.id)
            except Exception:
                intervals_data = None

            await ActivityDetailRow.save(act.id, detail, intervals_data)
            updated += 1
            print(f"OK (zone_times: {has_zt})")
        except Exception as exc:
            print(f"Error: {exc}")

        if i < total:
            await asyncio.sleep(1)

    print(f"\nRe-fetched {updated}/{total} activities.")
    print("Now run: python -m bot.cli backfill-max-zone")


async def _backfill_max_zone() -> None:
    """Backfill actual_max_zone_time for training_log entries with activity but no zone."""
    from bot.utils import compute_max_zone
    from data.database import TrainingLogRow

    rows = await TrainingLogRow.get_range(days_back=365)
    count = 0
    for row in rows:
        if row.actual_activity_id and not row.actual_max_zone_time:
            zone = await compute_max_zone(row.actual_activity_id, sport=row.actual_sport)
            if zone:
                await TrainingLogRow.update(row.id, actual_max_zone_time=zone)
                count += 1
    print(f"Backfilled {count} entries")


if __name__ == "__main__":
    main()
