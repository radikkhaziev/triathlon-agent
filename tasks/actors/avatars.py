"""Dramatiq actor — sync a user's Telegram profile photo to static/avatar/.

Source of truth is file existence at ``static/avatar/{chat_id}.png``:
- File present → user has an avatar → API returns the URL → UI renders <img>.
- File absent → no avatar → UI falls back to initials.

The actor's job is to keep that file in sync with what Telegram exposes for
the user. Three outcomes:
- **Photo available** → download + atomically replace the file.
- **No photo / access denied** (privacy setting) → delete the local file so
  we don't keep showing a stale avatar after the user revoked access.
- **Transient error** (network/Telegram 5xx) → leave the file untouched and
  retry once. Permanent errors don't retry — old file stays as best-effort.
"""

import io
import logging
import os

import dramatiq
import httpx
import sentry_sdk
from PIL import Image, UnidentifiedImageError
from pydantic import validate_call

from config import settings
from data import avatar_storage
from data.avatar_storage import avatar_path
from data.db import UserDTO

logger = logging.getLogger(__name__)

# Telegram occasionally hiccups on getUserProfilePhotos / getFile; one retry
# absorbs that without storming on permanent failures (user blocked the bot,
# image corrupted, privacy setting). Each attempt = ~3 HTTP calls.
_MAX_RETRIES = 1
_HTTP_TIMEOUT = 15.0
# Telegram avatars are <1 MB in practice; cap at 5 MB to harden against a
# spoofed response (MITM on dev, mock server, future contract change) without
# rejecting any realistic photo.
_MAX_IMAGE_BYTES = 5 * 1024 * 1024
# Decompression-bomb guard: Pillow's allocation = width × height × bytes-per-
# pixel, so a 32k×32k PNG would request ~4 GB. Telegram caps profile photos at
# 640×640 (~0.4 MPx); 4 MPx leaves headroom for future bumps without exposing
# the worker to a bomb. Module-level set so the limit applies on first import.
Image.MAX_IMAGE_PIXELS = 4_000_000


def _delete_local(chat_id: str) -> None:
    """Remove the cached avatar so callers fall back to initials. Broad OSError
    catch (not just FileNotFoundError) — permission glitches or
    IsADirectoryError shouldn't trigger a dramatiq retry that would just hit
    the same condition next attempt."""
    target = avatar_path(chat_id)
    try:
        os.remove(target)
        logger.info("Removed avatar for chat_id=%s (no longer available)", chat_id)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Failed to remove avatar for chat_id=%s: %s", chat_id, exc)


@dramatiq.actor(queue_name="default", max_retries=_MAX_RETRIES)
@validate_call
def actor_download_user_avatar(user: UserDTO) -> None:
    """Sync ``static/avatar/{chat_id}.png`` with the user's Telegram profile photo.

    Side-effects only — the in-flight ``UserDTO`` is not mutated; callers read
    the result through the file system (or the ``avatar_url`` exposed by
    ``/api/auth/me``).
    """
    bot_token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    base_url = f"https://api.telegram.org/bot{bot_token}"
    chat_id = user.chat_id

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            photos_resp = client.get(
                f"{base_url}/getUserProfilePhotos",
                params={"user_id": chat_id, "limit": 1},
            )
            photos_resp.raise_for_status()
            photos = photos_resp.json()

            # `ok=false` usually means user blocked the bot or hid photos —
            # `error_code=400 Bad Request: user not found` is the typical
            # shape. Treat as "no avatar available": clear local file.
            if not photos.get("ok"):
                logger.info(
                    "getUserProfilePhotos not ok for chat_id=%s: %s",
                    chat_id,
                    photos.get("description"),
                )
                _delete_local(chat_id)
                return

            if photos["result"]["total_count"] == 0:
                # User has zero profile photos — either never set one, or
                # removed the last one, or privacy setting hides them all.
                logger.info("No profile photo visible for chat_id=%s", chat_id)
                _delete_local(chat_id)
                return

            # Largest size is last in the PhotoSize array.
            file_id = photos["result"]["photos"][0][-1]["file_id"]

            meta_resp = client.get(f"{base_url}/getFile", params={"file_id": file_id})
            meta_resp.raise_for_status()
            meta = meta_resp.json()
            if not meta.get("ok"):
                logger.warning(
                    "getFile failed for chat_id=%s: %s",
                    chat_id,
                    meta.get("description"),
                )
                # Photo metadata vanished mid-flight — treat as unavailable.
                _delete_local(chat_id)
                return

            file_path = meta["result"]["file_path"]
            # Streamed read with a hard byte cap — see _MAX_IMAGE_BYTES comment.
            with client.stream(
                "GET",
                f"https://api.telegram.org/file/bot{bot_token}/{file_path}",
            ) as image_resp:
                image_resp.raise_for_status()
                buf = bytearray()
                for chunk in image_resp.iter_bytes(chunk_size=64 * 1024):
                    buf.extend(chunk)
                    if len(buf) > _MAX_IMAGE_BYTES:
                        logger.warning(
                            "Avatar download too large for chat_id=%s: >%d bytes",
                            chat_id,
                            _MAX_IMAGE_BYTES,
                        )
                        return
                image_bytes = bytes(buf)
    except httpx.HTTPError as exc:
        # Network or 5xx — keep the existing avatar; dramatiq retries once.
        logger.warning("Avatar download HTTP error for chat_id=%s: %s", chat_id, exc)
        raise

    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        # Corrupted bytes / oversized pixel dimensions. Don't retry — same
        # bytes will fail the same way. `DecompressionBombError` fires when
        # the image exceeds `Image.MAX_IMAGE_PIXELS`.
        logger.warning("Avatar decode failed for chat_id=%s: %s", chat_id, exc)
        sentry_sdk.capture_exception(exc)
        return

    # Access via module attribute (not `from … import AVATAR_DIR`) so test
    # monkeypatch on `data.avatar_storage.AVATAR_DIR` reaches this lookup —
    # a value-import would freeze a copy at module load time.
    os.makedirs(avatar_storage.AVATAR_DIR, exist_ok=True)
    target = avatar_path(chat_id)
    # Write to a tmp path then rename — readers see either old or new file,
    # never a half-written PNG (avatars are served by the API process).
    # PID suffix avoids tmp-file collision if two workers race on the same
    # chat_id (manual replay, fan-out duplicate, etc).
    tmp = f"{target}.{os.getpid()}.tmp"
    try:
        image.save(tmp, format="PNG")
        os.replace(tmp, target)
    except OSError as exc:
        logger.warning("Avatar write failed for chat_id=%s: %s", chat_id, exc)
        sentry_sdk.capture_exception(exc)
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass
        return

    logger.info("Saved avatar for chat_id=%s → %s", chat_id, target)
