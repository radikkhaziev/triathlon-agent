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

app = FastAPI(title="Triathlon Agent API", version="0.1.0")

_allowed_origins = [settings.WEBAPP_URL] if settings.WEBAPP_URL else []
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET"],
    allow_headers=["Authorization"],
)

app.include_router(router)

# Serve webapp locally — on prod nginx handles static files
webapp_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "webapp")
if os.path.isdir(webapp_path):
    app.mount("/", StaticFiles(directory=webapp_path, html=True), name="webapp")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)
