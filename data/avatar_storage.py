"""Shared file-system constants for cached Telegram avatars.

Single source of truth so the writer (`tasks/actors/avatars.py`) and the
reader (`api/routers/auth.py`) agree on where the file lives. Without this
the path used to be hard-coded in two modules — easy to drift.

The directory is under `static/` so it shares the Docker volume mount with
the API and worker containers (`static_data:/app/static`), but the
`/static/avatar/*` URL prefix is explicitly blocked in `api/server.py`
to prevent unauthenticated access — the file bytes are served only via
the authenticated `GET /api/auth/avatar` endpoint.
"""

import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AVATAR_DIR = os.path.join(_PROJECT_ROOT, "static", "avatar")


def avatar_path(chat_id: str) -> str:
    """Absolute on-disk path to a user's cached avatar PNG."""
    return os.path.join(AVATAR_DIR, f"{chat_id}.png")
