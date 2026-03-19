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

    args = parser.parse_args()

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
            await daily_metrics_job(target_date=dt)
        except Exception as exc:
            print(f"  Error: {exc}")
        dt += timedelta(days=1)

    print("Backfill completed.")


if __name__ == "__main__":
    main()
