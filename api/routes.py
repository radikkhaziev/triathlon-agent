from fastapi import APIRouter

from api.routers.activities import router as activities_router
from api.routers.auth import router as auth_router
from api.routers.intervals import router as intervals_router
from api.routers.jobs import router as jobs_router
from api.routers.system import router as system_router
from api.routers.wellness import router as wellness_router
from api.routers.workouts import router as workouts_router

router = APIRouter()

router.include_router(system_router)
router.include_router(auth_router)
router.include_router(wellness_router)
router.include_router(workouts_router)
router.include_router(activities_router)
router.include_router(jobs_router)
router.include_router(intervals_router)
