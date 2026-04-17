"""Intervals.icu integration — OAuth flow and webhook receiver."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/intervals", tags=["intervals"])

# Import sub-modules to register their routes on the shared router.
from . import oauth, webhook  # noqa: E402, F401
