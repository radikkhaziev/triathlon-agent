import logging
import os

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

    Uses raw ASGI instead of BaseHTTPMiddleware to support SSE streaming.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            token = settings.MCP_AUTH_TOKEN.get_secret_value()
            if token:
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


app = FastAPI(title="Triathlon Agent API", version="0.1.0")

_allowed_origins = [settings.WEBAPP_URL] if settings.WEBAPP_URL else []
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

app.add_middleware(MCPAuthMiddleware)

app.include_router(router)

# Mount MCP server on /mcp (SSE transport for Claude Desktop via mcp-remote)
from mcp_server.server import mcp as mcp_server  # noqa: E402

app.mount("/mcp", mcp_server.sse_app())

# Serve webapp locally — on prod nginx handles static files
webapp_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "webapp")
if os.path.isdir(webapp_path):
    app.mount("/", StaticFiles(directory=webapp_path, html=True), name="webapp")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)
