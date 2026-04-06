import functools
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, contextmanager

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import settings

# ---------------------------------------------------------------------------
# Engine / Session helpers
# ---------------------------------------------------------------------------

_async_engine = None
_sync_engine = None

_AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None
_SyncSessionLocal: sessionmaker[Session] | None = None


def _make_sync_url(async_url: str) -> str:
    """Convert asyncpg URL to psycopg2 for sync access."""
    return async_url.replace("+asyncpg", "")


def _get_engine():
    """Return a singleton async SQLAlchemy engine."""
    global _async_engine
    if _async_engine is None:
        _async_engine = create_async_engine(
            settings.DATABASE_URL,
            echo=False,
            pool_size=5,
            max_overflow=10,
            pool_recycle=3600,
        )
    return _async_engine


def _get_sync_engine():
    """Return a singleton sync SQLAlchemy engine."""
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = create_engine(
            _make_sync_url(settings.DATABASE_URL),
            echo=False,
            pool_size=5,
            max_overflow=10,
            pool_recycle=3600,
            pool_pre_ping=True,
        )
    return _sync_engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the async session factory (singleton)."""
    global _AsyncSessionLocal
    if _AsyncSessionLocal is None:
        _AsyncSessionLocal = async_sessionmaker(bind=_get_engine(), expire_on_commit=False)
    return _AsyncSessionLocal


def _get_sync_session_factory() -> sessionmaker[Session]:
    """Return the sync session factory (singleton)."""
    global _SyncSessionLocal
    if _SyncSessionLocal is None:
        _SyncSessionLocal = sessionmaker(bind=_get_sync_engine(), expire_on_commit=False)
    return _SyncSessionLocal


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session with automatic close."""
    factory = _get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        await session.close()


@contextmanager
def get_sync_session():
    """Yield a sync session with automatic close."""
    factory = _get_sync_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


class _DualMethod:
    """Descriptor that auto-dispatches to sync or async based on calling context.

    In sync context (no running event loop): calls sync version directly.
    In async context (event loop running): returns a coroutine (must be awaited).

    Same method name works in both contexts::

        # sync (dramatiq actor):
        rows = Activity.get_for_date(user_id, dt)

        # async (bot, API):
        rows = await Activity.get_for_date(user_id, dt)
    """

    def __init__(self, sync_fn, async_fn):
        self.sync_fn = sync_fn
        self.async_fn = async_fn
        self.__doc__ = sync_fn.__doc__
        self.__name__ = sync_fn.__name__

    def __set_name__(self, owner, name):
        self.__name__ = name

    def __get__(self, obj, objtype=None):
        # Always bind to the class (classmethod behavior)
        import asyncio

        sync_bound = self.sync_fn.__get__(obj, objtype)
        async_bound = self.async_fn.__get__(obj, objtype)

        def dispatch(*args, **kwargs):
            try:
                asyncio.get_running_loop()
                return async_bound(*args, **kwargs)
            except RuntimeError:
                return sync_bound(*args, **kwargs)

        functools.update_wrapper(dispatch, self.sync_fn)
        return dispatch


def _generate_dual_methods(cls, name, sync_body):
    """From a sync function body, generate a DualMethod on cls.

    Single name dispatches to sync or async based on calling context.
    """
    from .decorator import with_session, with_sync_session

    sync_fn = classmethod(with_sync_session(sync_body))

    @functools.wraps(sync_body)
    async def async_impl(*args, session: AsyncSession = None, **kwargs):
        return await session.run_sync(lambda sync_s: sync_body(*args, session=sync_s, **kwargs))

    async_fn = classmethod(with_session(async_impl))

    setattr(cls, name, _DualMethod(sync_fn, async_fn))


# Session API (async + sync mirrors):
#
# 1. get_session() / get_sync_session() — context manager, creates a new session:
#
#       async with get_session() as session:           # async
#           result = await session.execute(select(User))
#
#       with get_sync_session() as session:            # sync
#           result = session.execute(select(User))
#
# 2. @with_session / @with_sync_session — decorator, injects session if not provided:
#
#       @with_session                                  # async
#       async def get_user(*, session: AsyncSession = None):
#           result = await session.execute(...)
#
#       @with_sync_session                             # sync
#       def get_user_sync(*, session: Session = None):
#           result = session.execute(...)
#
#       # Standalone — session created internally:
#       user = await User.get_by_pk(1)
#
#       # Shared session — multiple operations in one transaction:
#       async with get_session() as session:
#           user = await User.get_by_pk(1, session=session)
#           user.role = "owner"
#           await session.commit()
#
# 3. @dual — write sync body, auto-dispatch by context:
#
#       @classmethod
#       @dual
#       def get_active_athletes(cls, *, session: Session):
#           result = session.execute(select(cls).where(...))
#           return list(result.scalars().all())
#
#       # Same name works in both contexts:
#       users = User.get_active_athletes()              # sync (no event loop)
#       users = await User.get_active_athletes()        # async (event loop running)
#
#       # Shared session:
#       async with get_session() as session:
#           users = await User.get_active_athletes(session=session)
#
#       with get_sync_session() as session:
#           users = User.get_active_athletes(session=session)
#


class Base(DeclarativeBase):

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for attr_name in list(cls.__dict__):
            val = cls.__dict__[attr_name]
            if isinstance(val, classmethod):
                inner = val.__func__
                if getattr(inner, "_dual", False):
                    _generate_dual_methods(cls, attr_name, inner)
