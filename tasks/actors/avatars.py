"""Dramatiq actor — download a user's Telegram profile photo to static/avatar/."""

import io
import logging
import os

import dramatiq
import httpx
from PIL import Image
from pydantic import validate_call

from config import settings
from data.db import UserDTO

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_AVATAR_DIR = os.path.join(_PROJECT_ROOT, "static", "avatar")


@dramatiq.actor(queue_name="default")
@validate_call
def actor_download_user_avatar(user: UserDTO) -> dict:
    """Fetch the user's Telegram profile photo and save as static/avatar/{chat_id}.png.

    Returns the input ``UserDTO`` (as a dict) with ``avatar_url`` set to the
    public path on success, or unchanged if the user has no profile photo or
    the download fails.
    """
    bot_token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    base_url = f"https://api.telegram.org/bot{bot_token}"

    with httpx.Client(timeout=15.0) as client:
        photos = client.get(
            f"{base_url}/getUserProfilePhotos",
            params={"user_id": user.chat_id, "limit": 1},
        ).json()
        if not photos.get("ok") or photos["result"]["total_count"] == 0:
            logger.info("No profile photo for chat_id=%s", user.chat_id)
            return user.model_dump()

        # Largest size is last in the PhotoSize array.
        file_id = photos["result"]["photos"][0][-1]["file_id"]
        meta = client.get(f"{base_url}/getFile", params={"file_id": file_id}).json()
        if not meta.get("ok"):
            logger.warning("getFile failed for chat_id=%s: %s", user.chat_id, meta)
            return user.model_dump()

        file_path = meta["result"]["file_path"]
        image_bytes = client.get(f"https://api.telegram.org/file/bot{bot_token}/{file_path}").content

    os.makedirs(_AVATAR_DIR, exist_ok=True)
    target = os.path.join(_AVATAR_DIR, f"{user.chat_id}.png")
    Image.open(io.BytesIO(image_bytes)).convert("RGBA").save(target, format="PNG")

    avatar_url = f"/static/avatar/{user.chat_id}.png"
    logger.info("Saved avatar for chat_id=%s → %s", user.chat_id, avatar_url)
    return user.model_copy(update={"avatar_url": avatar_url}).model_dump()
