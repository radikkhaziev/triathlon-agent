import functools
import inspect
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session


@asynccontextmanager
async def _use_session(session: AsyncSession | None = None) -> AsyncGenerator[AsyncSession, None]:
    from .common import get_session

    """Use provided session or create a new one."""
    if session is not None:
        yield session
    else:
        async with get_session() as new_session:
            yield new_session


@contextmanager
def _use_sync_session(session: Session | None = None) -> Generator[Session, None, None]:
    """Use provided sync session or create a new one."""
    from .common import get_sync_session

    if session is not None:
        yield session
    else:
        with get_sync_session() as new_session:
            yield new_session


def with_session(fn):
    """Decorator: injects async `session` kwarg if not provided."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        session = kwargs.get("session")
        async with _use_session(session) as s:
            kwargs["session"] = s
            return await fn(*args, **kwargs)

    wrapper.__signature__ = inspect.signature(fn)
    return wrapper


def with_sync_session(fn):
    """Decorator: injects sync `session` kwarg if not provided."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        session = kwargs.get("session")
        with _use_sync_session(session) as s:
            kwargs["session"] = s
            return fn(*args, **kwargs)

    wrapper.__signature__ = inspect.signature(fn)
    return wrapper


def dual(fn):
    """Mark a sync classmethod for dual async/sync generation.

    Write the method body as plain sync (session.execute, no await).
    __init_subclass__ will create a DualMethod that auto-dispatches
    based on calling context (event loop running → async, otherwise → sync).

    Same name, both contexts::

        @classmethod
        @dual
        def get_active_athletes(cls, *, session):
            result = session.execute(select(cls).where(...))
            return list(result.scalars().all())

        # sync context (Dramatiq actor):
        users = User.get_active_athletes()

        # async context (bot, API):
        users = await User.get_active_athletes()
    """
    fn._dual = True
    return fn
