import argparse
import asyncio
import logging
from datetime import date, timedelta

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

    sub.add_parser("backfill", help="Run daily_metrics_job for the last 2 months")
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
        asyncio.run(_backfill())


async def _backfill() -> None:
    from bot.scheduler import daily_metrics_job
    from data.garmin_client import GarminClient

    GarminClient(settings.GARMIN_EMAIL, settings.GARMIN_PASSWORD.get_secret_value())

    today = date.today()
    start = today - timedelta(days=60)
    dt = start

    while dt <= today:
        print(f"Processing {dt} ...")
        try:
            await daily_metrics_job(target_date=dt, notify=False)
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
    import garth
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
