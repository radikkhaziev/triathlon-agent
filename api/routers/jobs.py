import logging

from fastapi import APIRouter, BackgroundTasks, Depends

from api.deps import require_owner
from bot.scheduler import daily_metrics_job, scheduled_workouts_job, sync_activities_job

logger = logging.getLogger(__name__)

router = APIRouter()


async def _run_job(coro, job_name: str) -> None:
    try:
        await coro()
    except Exception:
        logger.exception("%s failed", job_name)


@router.post("/api/jobs/sync-workouts", status_code=202)
async def job_sync_workouts(
    background_tasks: BackgroundTasks,
    _: None = Depends(require_owner),
) -> dict:
    background_tasks.add_task(_run_job, scheduled_workouts_job, "sync-workouts job")
    return {"status": "accepted", "job": "sync-workouts"}


@router.post("/api/jobs/sync-activities", status_code=202)
async def job_sync_activities(
    background_tasks: BackgroundTasks,
    _: None = Depends(require_owner),
) -> dict:
    background_tasks.add_task(_run_job, sync_activities_job, "sync-activities job")
    return {"status": "accepted", "job": "sync-activities"}


@router.post("/api/jobs/sync-wellness", status_code=202)
async def job_sync_wellness(
    background_tasks: BackgroundTasks,
    _: None = Depends(require_owner),
) -> dict:
    background_tasks.add_task(_run_job, daily_metrics_job, "sync-wellness job")
    return {"status": "accepted", "job": "sync-wellness"}
