import hashlib
import hmac
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.dashboard_routes import router as dashboard_router
from api.routes import router
from config import settings

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


class MCPAuthMiddleware:
    """Pure ASGI middleware for Bearer token auth on /mcp endpoints.

    Uses raw ASGI instead of BaseHTTPMiddleware to support streaming.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and scope["path"].startswith("/mcp"):
            token = settings.MCP_AUTH_TOKEN.get_secret_value()
            if not token:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 503,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": b'{"detail":"MCP auth not configured"}'})
                return
            headers = dict(scope.get("headers", []))
            auth = headers.get(b"authorization", b"").decode()
            if auth != f"Bearer {token}":
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": b'{"detail":"Invalid MCP token"}'})
                return
        await self.app(scope, receive, send)


# Mount MCP server on /mcp (Streamable HTTP transport — stateless, no session expiry issues)
from mcp_server.server import mcp as mcp_server  # noqa: E402

_mcp_app = mcp_server.streamable_http_app()


@asynccontextmanager
async def lifespan(app):
    """Manage MCP sub-app and Telegram webhook lifecycles."""
    async with _mcp_app.router.lifespan_context(_mcp_app):
        # Start Telegram bot in webhook mode if configured
        if settings.TELEGRAM_WEBHOOK_URL:
            from bot.main import build_application

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


app = FastAPI(title="Triathlon Agent API", version="0.1.0", lifespan=lifespan)

_allowed_origins = [settings.WEBAPP_URL] if settings.WEBAPP_URL else []
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

app.add_middleware(MCPAuthMiddleware)

app.include_router(router)
app.include_router(dashboard_router)


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> Response:
    """Receive Telegram updates via webhook."""
    tg_app = getattr(request.app.state, "tg_app", None)
    if tg_app is None:
        return Response(status_code=503, content="Bot not configured for webhook mode")

    # Verify secret token (set during set_webhook)
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    expected = hashlib.sha256(settings.TELEGRAM_BOT_TOKEN.get_secret_value().encode()).hexdigest()[:32]
    if not hmac.compare_digest(secret, expected):
        return Response(status_code=403, content="Forbidden")

    from telegram import Update

    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    try:
        await tg_app.process_update(update)
    except Exception:
        logger.exception("Error processing Telegram update")
    return Response(status_code=200)


app.mount("/mcp", _mcp_app)

# Serve webapp locally — on prod nginx handles static files
webapp_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "webapp")
if os.path.isdir(webapp_path):
    app.mount("/", StaticFiles(directory=webapp_path, html=True), name="webapp")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)
