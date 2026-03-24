import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes import router
from config import settings

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
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
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
    """Run MCP sub-app lifespan (initializes streamable HTTP task group)."""
    async with _mcp_app.router.lifespan_context(_mcp_app):
        yield


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

app.mount("/mcp", _mcp_app)

# Serve webapp locally — on prod nginx handles static files
webapp_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "webapp")
if os.path.isdir(webapp_path):
    app.mount("/", StaticFiles(directory=webapp_path, html=True), name="webapp")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)
