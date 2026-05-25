import argparse
import code
import re
import time
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone

import httpx
import sentry_sdk
from sqlalchemy import select

from config import settings
from data.db import Activity, ActivityDetail, AthleteSettings, User, Wellness, get_session, get_sync_session
from data.db.dto import UserDTO
from data.endurance_score_service import recompute_and_upsert as _recompute_endurance_score
from data.garmin import importer as garmin_importer
from data.garmin.parser import GarminExportParser
from data.ml.noise_classifier import classify_activity_row
from tasks.actors.activities import actor_fetch_user_activities
from tasks.actors.wellness import actor_user_wellness
from tasks.dto import local_today
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

    sub.add_parser(
        "create-weekly-report",
        help="Generate this week's weekly report for ALL active athletes and save to DB "
        "without sending to Telegram. Mirrors actor_compose_weekly_report's pipeline up to "
        "(but excluding) the chat push — use for backfilling a Sunday cron firing that didn't "
        "deliver, or for seeding the webapp history view (PR2/PR3) end-to-end. "
        "Idempotent per user: re-running overwrites each existing row for the current "
        "Mon-Sun window. Sequential — ~30-40s/user, ~$0.04/user.",
    )

    p_pc = sub.add_parser(
        "publish-changelog",
        help="Manually trigger the weekly changelog publisher (debug). "
        "Runs the same code path as the Sun 15:00 cron — fetches PRs, calls Claude, "
        "creates a Discussion. Idempotent by week: a no-op if a Discussion already "
        "exists within this week's window (now - 6d); a prior week's Discussion does "
        "NOT block a new one. See docs/WEEKLY_CHANGELOG_SPEC.md §16.",
    )
    p_pc.add_argument(
        "--force",
        action="store_true",
        help="Override weekly idempotency — publish a second Discussion this week.",
    )

    p_trm = sub.add_parser(
        "train-race-models",
        help="Train race-projection models (Run/Ride/Swim) for one user. "
        "Saves joblib bundles to static/models/race_{user_id}_{discipline}.joblib. "
        "Logs MAE/R² per discipline; disciplines with <30 examples are skipped. "
        "See docs/ML_RACE_PROJECTION_SPEC.md §12.",
    )
    p_trm.add_argument("user_id", type=int)

    p_rsl = sub.add_parser(
        "recalc-sport-load",
        help="Backfill per-sport CTL+ATL across all active athletes for the last N days. "
        "Dispatches actor_user_wellness(force=True) per (user, day), which re-pulls wellness "
        "from Intervals (creates row if missing for inactive users) and re-runs the pipeline — "
        "including _actor_enrich_wellness_sport_info that recomputes sport_info[].ctl/atl. "
        "See docs/PER_SPORT_LOAD_SPEC.md §Step 1.5.",
    )
    p_rsl.add_argument("--user-id", type=int, default=None, help="Specific user; default: all active athletes")
    p_rsl.add_argument("--days", type=int, default=200, help="Window in days (default: 200, matches CTL EMA 5τ)")
    p_rsl.add_argument("--dry-run", action="store_true", help="List users + dates without dispatching")

    p_bes = sub.add_parser(
        "backfill-endurance-scores",
        help="Compute + upsert daily Endurance Score snapshots into the "
        "endurance_scores table. Default: all active athletes × last 365 days. "
        "Idempotent — skips existing rows unless --force. See "
        "docs/ENDURANCE_SCORE_SPEC.md §7.3.",
    )
    p_bes.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="Limit to one user (default: all active athletes)",
    )
    p_bes.add_argument(
        "--days",
        type=int,
        default=365,
        help="History window in days from today (default: 365)",
    )
    p_bes.add_argument(
        "--force",
        action="store_true",
        help="Re-compute and overwrite existing rows (default: skip)",
    )
    p_bes.add_argument(
        "--dry-run",
        action="store_true",
        help="Print scope (users × days) without writing",
    )

    p_cn = sub.add_parser(
        "classify-noise",
        help="Backfill activities.noise_reason for Run activities — Phase 1.6 "
        "(see docs/ML_RACE_PROJECTION_SPEC.md §6.4). Idempotent: re-running "
        "updates noise_scored_at + reason; rows untouched if classifier output "
        "matches existing reason. Without --user-id: iterates active athletes.",
    )
    p_cn.add_argument("--user-id", type=int, default=None, help="Specific user; default: all active athletes")
    p_cn.add_argument(
        "--since-days",
        type=int,
        default=365,
        help="Window in days (default: 365, matches RACE_FEATURE_WINDOW_DAYS)",
    )
    p_cn.add_argument("--dry-run", action="store_true", help="Count without writing")

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
    elif args.command == "create-weekly-report":
        _create_weekly_report_all()
    elif args.command == "publish-changelog":
        _publish_changelog(force=args.force)
    elif args.command == "train-race-models":
        _train_race_models(args.user_id)
    elif args.command == "backfill-endurance-scores":
        _backfill_endurance_scores(user_id=args.user_id, days=args.days, force=args.force, dry_run=args.dry_run)
    elif args.command == "classify-noise":
        _classify_noise(user_id=args.user_id, since_days=args.since_days, dry_run=args.dry_run)
    elif args.command == "recalc-sport-load":
        _recalc_sport_load(user_id=args.user_id, days=args.days, dry_run=args.dry_run)


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


def _create_weekly_report_all() -> None:
    """Generate this week's report for every active athlete; no Telegram sends.

    Same code path as the Sunday cron actor minus the chat push — useful for
    filling in a missed Sunday delivery (the original bug PR1 addresses is
    Telegram silently dropping long messages despite ``ok=true``) or for
    seeding the webapp history view in dev. Idempotent per user: re-running
    overwrites each existing row for the current Mon-Sun window.

    Errors per user (Intervals 5xx, expired OAuth, transient Anthropic) are
    caught and logged so one bad athlete doesn't abort the whole sweep —
    mirrors the actor's fail-soft contract (``generate_and_save_weekly_report``
    returns ``None``; here we surface that as a per-user line).
    """
    from tasks.actors.reports import generate_and_save_weekly_report

    athletes = User.get_active_athletes()
    if not athletes:
        print("No active athletes — nothing to do.")
        return

    print(f"Generating weekly reports for {len(athletes)} active athletes…")
    saved = 0
    skipped = 0
    failed = 0
    for u in athletes:
        user_dto = UserDTO.model_validate(u)
        try:
            result = generate_and_save_weekly_report(user_dto)
        except Exception as e:
            # Surface to Sentry: silently swallowed per-user failures would
            # turn this recovery tool into an observability blackhole — by
            # design we keep iterating, but each break-the-build error
            # should still raise a flag in the dashboard.
            sentry_sdk.capture_exception(e)
            print(f"  user_id={u.id} chat_id={u.chat_id} FAILED: {type(e).__name__}: {e}")
            failed += 1
            continue
        if result is None:
            print(f"  user_id={u.id} chat_id={u.chat_id} skipped (empty text or stale user)")
            skipped += 1
            continue
        text, week_start = result
        print(f"  user_id={u.id} chat_id={u.chat_id} saved week_start={week_start} len={len(text)}")
        saved += 1

    base = settings.API_BASE_URL.rstrip("/")
    print(f"\nDone: saved={saved} skipped={skipped} failed={failed}")
    print(f"View any user's report at {base}/weekly/<YYYY-MM-DD> (Monday of target week).")


def _publish_changelog(*, force: bool = False) -> None:
    """Manual trigger for the weekly changelog publisher.

    Runs the same path as the Sun 15:00 cron, prints the result dict.
    Idempotent by default — a Wed run blocks the Sun cron from publishing a
    duplicate. Pass ``--force`` to override.
    """
    from tasks.actors.changelog import publish_weekly_changelog

    result = publish_weekly_changelog(force=force)
    print(f"publish_weekly_changelog(force={force}) → {result}")


def _train_race_models(user_id: int) -> None:
    """Train race-projection models for one user — three disciplines sequentially."""
    from data.ml.race_features import InsufficientDataError
    from data.ml.race_train import train_user_model

    for discipline in ("run", "ride", "swim"):
        try:
            result = train_user_model(user_id, discipline)
            print(
                f"  {discipline:5s}: n={result['n_examples']:4d}  "
                f"MAE={result['mae']:.3f}  R²={result['r2']:+.3f}  → {result['model_path']}"
            )
        except InsufficientDataError as e:
            print(f"  {discipline:5s}: skip — {e}")
        except Exception as e:
            print(f"  {discipline:5s}: FAILED — {type(e).__name__}: {e}")


def _recalc_sport_load(*, user_id: int | None, days: int, dry_run: bool) -> None:
    """Backfill per-sport CTL+ATL for the last `days` days across active athletes.

    For each (user, day) → `actor_user_wellness(force=True)`. The actor re-pulls
    wellness from Intervals (creates row if missing), then dispatches
    `actor_after_activity_update` which re-runs `_actor_enrich_wellness_sport_info`
    on the 200-day window — recomputing per-sport CTL/ATL with the new algorithm.

    Sequential per user: user `i` starts at `i * days * 20s`. The 20s/day pacing
    mirrors `_sync_wellness` and stays under Intervals.icu's rate limit.
    """
    today = date.today()
    start = today - timedelta(days=days - 1)

    with get_sync_session() as session:
        if user_id is not None:
            user = session.get(User, user_id)
            if not user or not user.is_active or not user.athlete_id:
                raise SystemExit(f"User {user_id} not active or has no athlete_id")
            users = [user]
        else:
            users = list(
                session.execute(
                    select(User).where(User.is_active.is_(True), User.athlete_id.isnot(None)).order_by(User.id)
                )
                .scalars()
                .all()
            )

    if not users:
        print("No active athletes found")
        return

    # 60s/day vs sync-wellness's 20s: actor_user_wellness fans out HRV/RHR/Banister/
    # recovery analyses that walk rolling 7/60d baselines. 20s can be tight under
    # API retries and risks the cross-day race OAUTH_BOOTSTRAP_SYNC_SPEC §17 warns
    # about. 60s is a 3× margin — one-shot backfill, wall-time cost is acceptable.
    delay_per_day_ms = 60_000
    days_per_user = days
    per_user_window_ms = days_per_user * delay_per_day_ms

    total_msgs = len(users) * days_per_user
    wall_minutes = (len(users) * per_user_window_ms) / 60_000
    print(
        f"recalc-sport-load: {len(users)} user(s), {days_per_user} day(s) each "
        f"({start} → {today}) → {total_msgs} dispatches, "
        f"~{wall_minutes:.0f} min wall time"
    )

    if dry_run:
        for u in users:
            print(f"  user_id={u.id} chat_id={u.chat_id} @{u.username or '-'}")
        print("Dry run — no messages queued.")
        return

    for i, u in enumerate(users):
        user_dto = UserDTO.model_validate(u)
        user_offset_ms = i * per_user_window_ms
        for j in range(days_per_user):
            day = (start + timedelta(days=j)).isoformat()
            actor_user_wellness.send_with_options(
                kwargs={"user": user_dto, "dt": day, "force": True},
                delay=user_offset_ms + j * delay_per_day_ms,
            )
        print(f"  user_id={u.id} queued {days_per_user} days, " f"starts at +{user_offset_ms // 60_000} min")


def _backfill_endurance_scores(*, user_id: int | None, days: int, force: bool, dry_run: bool) -> None:
    """Backfill daily Endurance Score snapshots — Phase 2 of ENDURANCE_SCORE_SPEC §7.3.

    Default: all active athletes × last 365 days. With ``--user-id`` restricts to
    one user. Idempotent — pre-existing rows are skipped unless ``--force`` is
    set (overwrite). Per-user errors → sentry + continue, never fail the batch.
    Performance: pure-module compute is ~50ms/day; 365 × 5 users ≈ 2.5 min
    sequential. Acceptable for one-shot backfill (parallelism premature here).
    """
    today = local_today()
    window_start = today - timedelta(days=days - 1)

    with get_sync_session() as session:
        if user_id is not None:
            # Validate: must exist + be active athlete. Match actor + cron
            # filter (`is_active AND athlete_id IS NOT NULL`) to avoid writing
            # `insufficient_data` rows for demo/viewer/non-onboarded users.
            row = session.execute(select(User.id, User.is_active, User.athlete_id).where(User.id == user_id)).first()
            if row is None:
                print(f"user {user_id}: not found")
                return
            uid, active, ath = row
            if not active or ath is None:
                print(
                    f"user {user_id}: skipped (is_active={active}, athlete_id={ath}) — "
                    "endurance score requires an active athlete with Intervals.icu connected"
                )
                return
            user_ids = [uid]
        else:
            # Same filter as `actor_snapshot_endurance_scores_all_users`.
            user_ids = [
                row[0]
                for row in session.execute(
                    select(User.id).where(User.is_active.is_(True), User.athlete_id.isnot(None)).order_by(User.id)
                )
            ]

    if not user_ids:
        print("No active athletes found")
        return

    suffix = " (dry-run, no writes)" if dry_run else ""
    print(
        f"backfill-endurance-scores: {len(user_ids)} user(s), window "
        f"{window_start.isoformat()} → {today.isoformat()} ({days} days){suffix}"
    )

    if dry_run:
        print(f"would compute {len(user_ids) * days} (user, day) rows")
        return

    start_time = time.monotonic()
    grand = {"computed": 0, "skipped": 0, "errors": 0}

    # Single sync session reused across all (user, day) compute calls —
    # opening/closing per call adds ~5-10 min wall-time on a 5-user × 365-day
    # backfill via the connection-pool checkout/checkin overhead. The service
    # supports `session=` reuse explicitly for this case.
    with get_sync_session() as session:
        for uid in user_ids:
            per_user = {"computed": 0, "skipped": 0, "errors": 0}
            for offset in range(days):
                ref_date = window_start + timedelta(days=offset)
                try:
                    outcome = _recompute_endurance_score(uid, ref_date, force=force, session=session)
                    if outcome.written:
                        per_user["computed"] += 1
                    else:
                        per_user["skipped"] += 1
                except Exception as e:
                    per_user["errors"] += 1
                    sentry_sdk.capture_exception(e)
                    print(f"    {ref_date} user={uid}: {type(e).__name__}: {e}")
                    # SQLAlchemy auto-rollbacks the failed statement, but the
                    # session is poisoned for further work in the same TX —
                    # explicit rollback to keep iterating cleanly.
                    session.rollback()

            print(
                f"  user {uid:>4}: {per_user['computed']:>3} computed, "
                f"{per_user['skipped']:>3} skipped, {per_user['errors']:>2} errors"
            )
            for key in grand:
                grand[key] += per_user[key]

    elapsed = time.monotonic() - start_time
    mm, ss = divmod(int(elapsed), 60)
    print(
        f"\ntotal: {grand['computed']} written, {grand['skipped']} skipped, "
        f"{grand['errors']} errors, runtime {mm}m {ss:02d}s"
    )


def _classify_noise(*, user_id: int | None, since_days: int, dry_run: bool) -> None:
    """Backfill `activities.noise_reason` over a window — Phase 1.6 (§6.4).

    Idempotent: re-running over the same window with the same rules is a no-op
    on rows whose classification didn't change. Per-user errors → sentry +
    continue (don't fail the batch on one bad user).
    """
    cutoff_iso = (date.today() - timedelta(days=since_days)).isoformat()

    with get_sync_session() as session:
        if user_id is not None:
            user_ids = [user_id]
        else:
            user_ids = [
                row[0] for row in session.execute(select(User.id).where(User.is_active.is_(True)).order_by(User.id))
            ]

    if not user_ids:
        print("No active athletes found")
        return

    grand_total = {"changed": 0, "unchanged": 0, "walk": 0, "jog": 0, "clean": 0}

    for uid in user_ids:
        try:
            stats = _classify_noise_for_user(uid, cutoff_iso=cutoff_iso, dry_run=dry_run)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            print(f"user {uid:>4}: FAILED — {type(e).__name__}: {e}")
            continue

        print(
            f"user {uid:>4}: scanned={stats['scanned']:>4}  "
            f"changed={stats['changed']:>3}  unchanged={stats['unchanged']:>3}  "
            f"walk={stats['walk']:>3}  jog={stats['jog']:>3}  clean={stats['clean']:>4}"
        )
        for key in grand_total:
            grand_total[key] += stats.get(key, 0)

    if len(user_ids) > 1:
        suffix = " (dry-run, no writes)" if dry_run else ""
        print(
            f"\ntotal: changed={grand_total['changed']}  unchanged={grand_total['unchanged']}  "
            f"walk={grand_total['walk']}  jog={grand_total['jog']}  clean={grand_total['clean']}{suffix}"
        )


def _classify_noise_for_user(user_id: int, *, cutoff_iso: str, dry_run: bool) -> dict[str, int]:
    """Walk an athlete's Run activities ≥ cutoff_iso, write noise_reason changes."""
    stats = {"scanned": 0, "changed": 0, "unchanged": 0, "walk": 0, "jog": 0, "clean": 0}

    with get_sync_session() as session:
        thresholds = AthleteSettings.get_thresholds(user_id, session=session)
        # Phase 1.6 scope: Run only. Other types pass-through but we don't
        # bother loading them — minimizes I/O on Ride-heavy datasets.
        rows = session.execute(
            select(Activity, ActivityDetail)
            .join(ActivityDetail, ActivityDetail.activity_id == Activity.id, isouter=True)
            .where(
                Activity.user_id == user_id,
                Activity.type == "Run",
                Activity.start_date_local >= cutoff_iso,
            )
            .order_by(Activity.start_date_local)
        ).all()

        now = datetime.now(timezone.utc)
        for activity, detail in rows:
            stats["scanned"] += 1
            if detail is None:
                # No activity_details synced — can't classify (zones/distance missing).
                continue
            reason = classify_activity_row(activity, detail, thresholds)
            if reason == "run_walk":
                stats["walk"] += 1
            elif reason == "run_recovery_jog":
                stats["jog"] += 1
            else:
                stats["clean"] += 1

            if reason == activity.noise_reason and activity.noise_scored_at is not None:
                stats["unchanged"] += 1
                continue
            stats["changed"] += 1
            if not dry_run:
                Activity.set_noise_classification(user_id, activity.id, reason=reason, scored_at=now, session=session)

        # Single commit per user — set_noise_classification doesn't commit
        # internally (Copilot #360 #3 fix), so on a 500-activity user this is
        # 1 round-trip vs 500. dry_run skips the writes entirely so commit is
        # a no-op there.
        if not dry_run:
            session.commit()

    return stats


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
