"""Telegram webhook router."""

from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import APIRouter, Request, Response
from telegram import Update

from config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> Response:
    """Receive Telegram updates via webhook."""
    tg_app = getattr(request.app.state, "tg_app", None)
    if tg_app is None:
        return Response(status_code=503, content="Bot not configured for webhook mode")

    # Verify secret token set during set_webhook.
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    expected = hashlib.sha256(settings.TELEGRAM_BOT_TOKEN.get_secret_value().encode()).hexdigest()[:32]
    if not hmac.compare_digest(secret, expected):
        return Response(status_code=403, content="Forbidden")

    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    try:
        await tg_app.process_update(update)
    except Exception:
        logger.exception("Error processing Telegram update")
    return Response(status_code=200)
