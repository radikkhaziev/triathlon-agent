import argparse
import asyncio
import logging
from datetime import date, timedelta

import garth

from config import settings
from data.database import _send_telegram_message


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="triathlon-agent", description="Triathlon AI Agent CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    echo_parser = sub.add_parser("echo", help="Send a message to Telegram chat")
    echo_parser.add_argument("message", help="Message text to send")

    backfill_parser = sub.add_parser(
        "backfill",
        help="Backfill daily metrics. Accepts optional period argument: "
        "YYYY-MM-DD (single day), YYYY-MM-DD:YYYY-MM-DD (range), "
        "2025Q3 (quarter), 2025-03 (month). Default: last 180 days.",
    )
    backfill_parser.add_argument(
        "period",
        nargs="?",
        default=None,
        help="Period to backfill (e.g. 2025-09-01, 2025-01-01:2025-03-31, 2025Q3, 2025-03)",
    )
    sub.add_parser("shell", help="Open interactive Python shell with app context")
    sub.add_parser(
        "garmin-login", help="Login to Garmin with credentials and save tokens"
    )
    sub.add_parser(
        "garmin-refresh", help="Refresh Garmin access token using saved refresh token"
    )

    args = parser.parse_args()

    if args.command == "shell":
        _shell()
        return

    if args.command == "garmin-login":
        _garmin_login()
        return

    if args.command == "garmin-refresh":
        _garmin_refresh()
        return

    if args.command == "echo":
        asyncio.run(_send_telegram_message(args.message))
        print("Message sent.")

    elif args.command == "backfill":
        asyncio.run(_backfill(args.period))


def _parse_period(period: str | None) -> tuple[date, date]:
    """Parse a period string into (start, end) dates.

    Supported formats:
        None              -> last 180 days
        2025-09-01        -> single day
        2025-01-01:2025-03-31 -> explicit range
        2025Q3            -> quarter (Jul-Sep)
        2025-03           -> month
    """
    import re

    today = date.today()

    if period is None:
        return today - timedelta(days=180), today

    # Quarter: 2025Q3
    m = re.fullmatch(r"(\d{4})Q([1-4])", period)
    if m:
        year, q = int(m.group(1)), int(m.group(2))
        month_start = (q - 1) * 3 + 1
        start = date(year, month_start, 1)
        # last day of quarter
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
    from data.garmin_client import GarminClient

    GarminClient(settings.GARMIN_EMAIL, settings.GARMIN_PASSWORD.get_secret_value())

    start, end = _parse_period(period)
    print(f"Backfill: {start} -> {end}")
    dt = start

    while dt <= end:
        print(f"Processing {dt} ...")
        try:
            await daily_metrics_job(target_date=dt)
        except Exception as exc:
            print(f"  Error: {exc}")
        dt += timedelta(days=1)

    print("Backfill completed.")


def _garmin_login() -> None:
    """Full credential login — use when refresh token is expired."""
    from garminconnect import Garmin

    from data.garmin_client import TOKENSTORE

    g = Garmin(settings.GARMIN_EMAIL, settings.GARMIN_PASSWORD.get_secret_value())
    g.login()
    g.garth.dump(TOKENSTORE)
    print(f"Tokens saved to {TOKENSTORE}")


def _garmin_refresh() -> None:
    """Refresh access token using saved refresh token — no credentials needed."""
    from data.garmin_client import TOKENSTORE

    garth.resume(TOKENSTORE)
    garth.client.dump(TOKENSTORE)
    print(f"Access token refreshed, saved to {TOKENSTORE}")


def _shell() -> None:
    import code

    from data.database import SessionLocal
    from data.garmin_client import GarminClient

    garmin = GarminClient(
        settings.GARMIN_EMAIL, settings.GARMIN_PASSWORD.get_secret_value()
    )
    db = SessionLocal()

    banner = (
        "Triathlon Agent Shell\n"
        "Available variables:\n"
        "  settings  - app settings\n"
        "  garmin    - GarminClient instance\n"
        "  db        - SQLAlchemy session\n"
    )
    ctx = {
        "settings": settings,
        "garmin": garmin,
        "db": db,
        "date": date,
        "timedelta": timedelta,
    }
    try:
        code.interact(banner=banner, local=ctx)
    finally:
        db.close()


if __name__ == "__main__":
    main()
