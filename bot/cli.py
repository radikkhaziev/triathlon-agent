import argparse
import asyncio
import logging
import re
from datetime import date, timedelta

from config import settings


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

    args = parser.parse_args()

    if args.command == "shell":
        _shell()
    elif args.command == "backfill":
        asyncio.run(_backfill(args.period))
    elif args.command == "sync-workouts":
        asyncio.run(_sync_workouts(args.days))


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
    from bot.scheduler import daily_metrics_job

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


async def _sync_workouts(days: int = 14) -> None:
    from data.database import save_scheduled_workouts
    from data.intervals_client import IntervalsClient

    today = date.today()
    newest = today + timedelta(days=days)
    print(f"Syncing workouts: {today} → {newest} ({days} days)")

    client = IntervalsClient()
    workouts = await client.get_events(oldest=today, newest=newest)
    count = await save_scheduled_workouts(workouts)
    print(f"Synced {count} workouts.")


def _shell() -> None:
    import code

    from data.database import get_session, get_wellness

    banner = (
        "Triathlon Agent Shell\n"
        "Available variables:\n"
        "  settings     - app settings\n"
        "  get_session  - async session context manager\n"
        "  get_wellness - fetch wellness row by date\n"
        "  asyncio.run  - run async functions\n"
    )
    ctx = {
        "settings": settings,
        "get_session": get_session,
        "get_wellness": get_wellness,
        "asyncio": asyncio,
        "date": date,
        "timedelta": timedelta,
    }
    code.interact(banner=banner, local=ctx)


if __name__ == "__main__":
    main()
