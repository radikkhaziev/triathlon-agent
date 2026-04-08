import hashlib
import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.dashboard_routes import router as dashboard_router
from api.routes import router
from api.telegram_webhook import router as telegram_webhook_router
from bot.main import build_application
from config import settings
from data.db import User
from data.redis_client import close_redis, init_redis
from mcp_server.context import set_current_user_id
from sentry_config import init_sentry

init_sentry()

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


class MCPAuthMiddleware:
    """Pure ASGI middleware for Bearer token auth on /mcp endpoints.

    Resolves the user by mcp_token from the DB and sets the user_id
    in contextvars for downstream MCP tools.

    Uses raw ASGI instead of BaseHTTPMiddleware to support streaming.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and scope["path"].startswith("/mcp"):
            headers = dict(scope.get("headers", []))
            auth = headers.get(b"authorization", b"").decode()
            if not auth.startswith("Bearer "):
                await self._reject(scope, receive, send, 401, b'{"detail":"Missing Bearer token"}')
                return

            token = auth[7:]  # strip "Bearer "
            user = await User.get_by_mcp_token(token)
            if not user:
                await self._reject(scope, receive, send, 401, b'{"detail":"Invalid MCP token"}')
                return

            set_current_user_id(user.id, athlete_id=user.athlete_id)

        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(scope, receive, send, status: int, body: bytes):
        if scope["type"] == "websocket":
            await receive()  # wait for websocket.connect
            await send({"type": "websocket.close", "code": 4001})
        else:
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body})


# Mount MCP server on /mcp (Streamable HTTP transport — stateless, no session expiry issues)
from mcp_server.server import mcp as mcp_server  # noqa: E402

_mcp_app = mcp_server.streamable_http_app()


@asynccontextmanager
async def lifespan(app):
    """Manage MCP sub-app and Telegram webhook lifecycles."""
    async with _mcp_app.router.lifespan_context(_mcp_app):
        # Init Redis (idempotent — safe if bot's _post_init already called it)
        await init_redis()

        # Start Telegram bot in webhook mode if configured
        if settings.TELEGRAM_WEBHOOK_URL:
            tg_app = build_application()
            await tg_app.initialize()
            await tg_app.post_init(tg_app)
            await tg_app.start()

            webhook_url = f"{settings.TELEGRAM_WEBHOOK_URL}/telegram/webhook"
            await tg_app.bot.set_webhook(
                url=webhook_url,
                secret_token=hashlib.sha256(settings.TELEGRAM_BOT_TOKEN.get_secret_value().encode()).hexdigest()[:32],
            )
            app.state.tg_app = tg_app
            logger.info("Telegram webhook set: %s", webhook_url)

        try:
            yield
        finally:
            tg_app = getattr(app.state, "tg_app", None)
            if tg_app is not None:
                await tg_app.bot.delete_webhook()
                await tg_app.stop()
                await tg_app.shutdown()
                await tg_app.post_shutdown(tg_app)
                logger.info("Telegram webhook removed")
            await close_redis()


app = FastAPI(title="Triathlon Agent API", version="0.1.0", lifespan=lifespan)

_allowed_origins = [settings.WEBAPP_URL] if settings.WEBAPP_URL else []
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

app.add_middleware(MCPAuthMiddleware)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Let FastAPI handle HTTP exceptions normally (4xx, etc.)
    if isinstance(exc, StarletteHTTPException):
        raise exc
    # Sentry FastApiIntegration captures automatically — no manual capture needed
    logger.exception("Unhandled exception", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(router)
app.include_router(dashboard_router)
app.include_router(telegram_webhook_router)


app.mount("/mcp", _mcp_app)

# Serve workout card static HTML files
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_static_dir = os.path.join(_project_root, "static")
os.makedirs(_static_dir, exist_ok=True)
os.makedirs(os.path.join(_static_dir, "uploads"), exist_ok=True)
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

# Serve React SPA — check for dist/ (production build), fallback to webapp/ root
_webapp_dist = os.path.join(_project_root, "webapp", "dist")
_webapp_root = os.path.join(_project_root, "webapp")
_spa_dir = _webapp_dist if os.path.isdir(_webapp_dist) else _webapp_root


class SPAStaticFiles(StaticFiles):
    """Serves static files with SPA fallback to index.html for client-side routing."""

    async def get_response(self, path: str, scope) -> Response:
        try:
            resp = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                resp = await super().get_response("index.html", scope)
            else:
                raise

        # Hashed assets (/assets/index-abc123.js) — cache aggressively
        # index.html and SPA fallback routes — never cache (so deploys take effect immediately)
        if "/assets/" in path:
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

        return resp


if os.path.isdir(_spa_dir):
    app.mount("/", SPAStaticFiles(directory=_spa_dir, html=True), name="webapp")


if __name__ == "__main__":
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)
