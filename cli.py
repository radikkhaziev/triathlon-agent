import argparse
import code
import re
from calendar import monthrange
from datetime import date, timedelta

from dramatiq import group

from config import settings
from data.db import User, Wellness, get_session
from data.db.dto import UserDTO
from tasks.actors.activities import actor_fetch_user_activities
from tasks.actors.wellness import actor_user_wellness


def main() -> None:
    parser = argparse.ArgumentParser(prog="triathlon-agent", description="Triathlon AI Agent CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("shell", help="Open interactive Python shell with app context")

    p_sync = sub.add_parser("sync-settings", help="Sync athlete settings & goals from Intervals.icu")
    p_sync.add_argument("user_id", type=int)

    p_back = sub.add_parser("backfill", help="Backfill wellness + activities day by day")
    p_back.add_argument("user_id", type=int)
    p_back.add_argument(
        "period",
        nargs="?",
        default=None,
        help="2025Q4 | 2025-11 | 2025-01-01:2025-03-31 (default: 180d)",
    )

    args = parser.parse_args()

    if args.command == "shell":
        _shell()
    elif args.command == "sync-settings":
        _sync_settings(args.user_id)
    elif args.command == "backfill":
        _backfill(args.user_id, args.period)


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


def _backfill(user_id: int, period: str | None) -> None:
    user = _resolve_user(user_id)
    start, end = _parse_period(period)

    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)

    print(f"Backfill user {user_id}: {start} → {end} ({len(days)} days)")

    # Each day: wellness + activities as a group, delayed by day index (60s apart)
    delay_per_day_ms = 60_000  # 1 min between days
    for i, day in enumerate(days):
        dt = day.isoformat()
        delay = i * delay_per_day_ms
        group(
            [
                actor_user_wellness.message_with_options(kwargs={"user": user, "dt": dt}, delay=delay),
                actor_fetch_user_activities.message_with_options(
                    kwargs={"user": user, "oldest": dt, "newest": dt},
                    delay=delay,
                ),
            ]
        ).run()

    print(f"Queued: {len(days)} days (wellness + activities per day, 60s apart)")


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
