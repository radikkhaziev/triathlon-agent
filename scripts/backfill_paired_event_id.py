"""Backfill `activities.paired_event_id` for one user via single-activity fetches.

Intervals.icu's list endpoint (`GET /athlete/{id}/activities`) does NOT include
`paired_event_id` — only the single-activity endpoint (`GET /activity/{id}`)
and the `ACTIVITY_UPLOADED` webhook do. So new activities arriving via webhook
pick up the field automatically, but historical activities synced through the
polling path stayed `paired_event_id=NULL`.

Usage (in docker):

    docker compose run --rm api python scripts/backfill_paired_event_id.py \\
        --user-id 1 --from 2026-05-01 --to 2026-05-13

Defaults to today-only when --from / --to are omitted. Rate-limits at ~5 req/s
to be polite to Intervals.icu's API.
"""

import argparse
import asyncio
from datetime import date

from sqlalchemy import select, update

from data.db import Activity, get_session
from data.intervals.client import IntervalsAccessError, IntervalsAsyncClient


async def backfill(user_id: int, date_from: date, date_to: date) -> None:
    date_from_s = date_from.isoformat()
    date_to_s = date_to.isoformat()
    async with get_session() as session:
        result = await session.execute(
            select(Activity.id)
            .where(
                Activity.user_id == user_id,
                Activity.paired_event_id.is_(None),
                Activity.start_date_local >= date_from_s,
                Activity.start_date_local <= date_to_s,
            )
            .order_by(Activity.start_date_local.desc())
        )
        ids: list[str] = [r[0] for r in result]

    if not ids:
        print(f"No NULL-paired activities for user_id={user_id} in [{date_from_s}..{date_to_s}]")
        return

    print(f"Backfilling {len(ids)} activities for user_id={user_id} in [{date_from_s}..{date_to_s}]")
    updated = 0
    async with IntervalsAsyncClient.for_user(user_id) as client:
        for i, aid in enumerate(ids, 1):
            # Rate-limit at the TOP of the loop so it applies regardless of
            # which branch we take (success / no-pairing / fetch-error).
            # First iteration sleeps too — single tick is negligible.
            await asyncio.sleep(0.2)
            try:
                data = await client.get_activity_detail(aid)
            except IntervalsAccessError as e:
                # 401 / 403 / 5xx after retries — log and skip the row.
                # Continuing is safe; idempotent on next run.
                print(f"  [{i}/{len(ids)}] {aid}: api access error: {e}")
                continue
            if data is None:
                print(f"  [{i}/{len(ids)}] {aid}: 404 on Intervals")
                continue
            paired = data.get("paired_event_id")
            if not paired:
                print(f"  [{i}/{len(ids)}] {aid}: no pairing on Intervals side")
                continue
            async with get_session() as session:
                await session.execute(
                    update(Activity)
                    .where(Activity.user_id == user_id, Activity.id == aid)
                    .values(paired_event_id=paired)
                )
                await session.commit()
            updated += 1
            print(f"  [{i}/{len(ids)}] {aid}: paired_event_id={paired}")

    print(f"Done. Updated {updated} / {len(ids)} rows.")


def main() -> None:
    today = date.today().isoformat()
    parser = argparse.ArgumentParser(description="Backfill activities.paired_event_id from Intervals.icu")
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--from", dest="date_from", default=today, help="YYYY-MM-DD (default: today)")
    parser.add_argument("--to", dest="date_to", default=today, help="YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    # Reject bad inputs early — `Activity.start_date_local` is a TEXT column,
    # so lexicographic compare against a malformed `--from 2026-5-1` would
    # silently produce the wrong window.
    try:
        date_from = date.fromisoformat(args.date_from)
        date_to = date.fromisoformat(args.date_to)
    except ValueError as e:
        parser.error(f"invalid date: {e}")
    if date_from > date_to:
        parser.error(f"--from ({date_from}) must be <= --to ({date_to})")

    asyncio.run(backfill(args.user_id, date_from, date_to))


if __name__ == "__main__":
    main()
