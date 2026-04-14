"""Intervals.icu webhook receiver — currently a logging-only stub.

Intervals.icu sends POST callbacks to configured webhook URLs when athlete
data changes. Right now we just log the payload to see what actually comes
in, so we can design the real handler once we know the shape. See issue
TBD for the real pipeline (parse event → dispatch dramatiq actor).
"""

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/intervals", tags=["intervals"])


@router.post("/hook/{external_id}")
async def intervals_hook(external_id: str, request: Request) -> dict:
    """Stub receiver — logs method, headers, query params, and JSON body.

    Responds 200 unconditionally so Intervals.icu does not retry while we
    are still figuring out the contract. Once we know the payload shape,
    this will dispatch a dramatiq actor per event type.
    """
    try:
        body = await request.json()
    except Exception:
        body = None
        raw = await request.body()
        logger.info("Intervals hook [%s] non-JSON body: %r", external_id, raw[:2000])

    logger.info(
        "Intervals hook [%s] headers=%s query=%s body=%s",
        external_id,
        dict(request.headers),
        dict(request.query_params),
        body,
    )

    return {"status": "ok", "external_id": external_id}
