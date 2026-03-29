"""Shared test fixtures: create a dedicated test database if it doesn't exist."""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse, urlunparse

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import data.database as db_module
from config import settings


def _make_test_db_url(original_url: str) -> tuple[str, str]:
    """Return (test_db_async_url, db_name) by appending '_test' to the DB name."""
    parsed = urlparse(original_url)
    original_name = parsed.path.lstrip("/")
    test_name = f"{original_name}_test"
    test_url = urlunparse(parsed._replace(path=f"/{test_name}"))
    return test_url, test_name


def _make_sync_url(async_url: str) -> str:
    """Convert asyncpg URL to psycopg2 for alembic."""
    return async_url.replace("+asyncpg", "")


def _server_url(async_url: str) -> str:
    """URL pointing at the default 'postgres' database (for CREATE/DROP DATABASE)."""
    parsed = urlparse(async_url)
    return urlunparse(parsed._replace(path="/postgres"))


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def _test_db():
    """Create the test database if it doesn't exist, run migrations."""
    test_url, test_name = _make_test_db_url(settings.DATABASE_URL)
    server_url = _server_url(settings.DATABASE_URL)

    # Create DB only if it doesn't exist
    server_engine = create_async_engine(server_url, isolation_level="AUTOCOMMIT")
    async with server_engine.connect() as conn:
        result = await conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": test_name},
        )
        if result.scalar() is None:
            await conn.execute(text(f'CREATE DATABASE "{test_name}"'))
    await server_engine.dispose()

    # Run alembic migrations (idempotent — skips already applied)
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", _make_sync_url(test_url))
    command.upgrade(alembic_cfg, "head")

    yield test_url, test_name


@pytest.fixture(autouse=True)
async def test_session(_test_db, monkeypatch):
    """Patch db_module to use the test database; truncate tables after each test."""
    test_url, _ = _test_db

    engine = create_async_engine(test_url, echo=False)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    monkeypatch.setattr(db_module, "_SessionLocal", factory)
    monkeypatch.setattr(db_module, "_engine", None)

    yield

    # Clean up data between tests (only tables that exist in the DB)
    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
        existing = {row[0] for row in result}
        for table in reversed(db_module.Base.metadata.sorted_tables):
            if table.name in existing:
                await conn.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))

    await engine.dispose()
