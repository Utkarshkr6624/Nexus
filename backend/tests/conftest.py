"""Shared pytest fixtures for the NEXUS backend suite.

Three things happen here that the application does not do for itself:

* **Event loop.** ``pytest-asyncio`` 1.4 has no ``asyncio_default_test_loop_factory``
  ini option, so the loop is selected by installing a policy whose
  ``new_event_loop`` returns :func:`app.core.event_loop.nexus_loop_factory`.
  Without this, psycopg's async driver refuses the Windows default
  ``ProactorEventLoop`` and every database-backed test errors. The factory must
  therefore construct the loop itself; ``asyncio.new_event_loop()`` would route
  back through this policy and recurse until the stack ran out.
* **Database.** The ``nexus_test`` database is created if missing — with the same
  extensions ``scripts/create_test_database.py`` installs — and brought to
  ``head`` with Alembic. ``Base.metadata.create_all`` is deliberately never
  used: a schema built from the models would prove nothing about the migration.
* **Isolation.** The app's engine is redirected at the test database for the whole
  session so API tests never touch the development database, and every managed
  table is truncated before each test — repository methods commit, so that
  truncation is the only thing standing between two tests.
* **Exclusivity.** Truncation is only safe while one session has the database.
  A session takes a PostgreSQL advisory lock named after the test database and
  holds it until it finishes, so a second session against the same database is
  told so and stops instead of manufacturing phantom failures in the first one.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import sys
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from urllib.parse import unquote, urlsplit

import psycopg
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from httpx import ASGITransport, AsyncClient
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.core.event_loop import nexus_loop_factory
from app.db import session as app_db_session
from app.models import Base

#: Kept in sync with docker/postgres/init/10_extensions.sql so a test run on a
#: native PostgreSQL exercises the same features as the container stack.
EXTENSIONS = ("pg_trgm", "unaccent")


class _NexusEventLoopPolicy(asyncio.DefaultEventLoopPolicy):
    """Route every loop created by the suite through :func:`nexus_loop_factory`."""

    def new_event_loop(self) -> asyncio.AbstractEventLoop:
        return nexus_loop_factory()


# Installed at import time, before pytest-asyncio's session-scoped
# ``event_loop_policy`` fixture snapshots it into the ``asyncio.Runner``.
asyncio.set_event_loop_policy(_NexusEventLoopPolicy())

BACKEND_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    """Fresh settings for the current test.

    ``get_settings`` is an ``lru_cache`` singleton; clearing it before and after
    means a test that monkeypatches the environment cannot leak into the next.
    """
    get_settings.cache_clear()
    try:
        yield get_settings()
    finally:
        get_settings.cache_clear()


@pytest.fixture
def make_settings(monkeypatch: pytest.MonkeyPatch):
    """Build a :class:`Settings` from explicit environment overrides."""

    def _factory(**overrides: str) -> Settings:
        for key, value in overrides.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        return Settings()

    yield _factory
    # Without this the mutated settings would stay cached for every later test.
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Test database
# ---------------------------------------------------------------------------


def _sync_url(async_uri: str) -> str:
    """Strip SQLAlchemy's driver suffix so psycopg can consume the URI."""
    return async_uri.replace("postgresql+psycopg://", "postgresql://", 1)


def _database_name(async_uri: str) -> str:
    return unquote(urlsplit(_sync_url(async_uri)).path).lstrip("/")


def _url_for_database(async_uri: str, database: str) -> str:
    parts = urlsplit(_sync_url(async_uri))
    return parts._replace(path=f"/{database}").geturl()


def _ensure_database_exists(test_uri: str) -> None:
    """Create the test database when it is missing.

    A stock PostgreSQL installation — including the official container image —
    ships only ``postgres``, ``template0`` and ``template1``, so the suite has to
    provision its own database. ``CREATE DATABASE`` cannot run inside a
    transaction, hence AUTOCOMMIT.
    """
    database = _database_name(test_uri)
    maintenance_uri = _url_for_database(test_uri, "postgres")
    with psycopg.connect(maintenance_uri, autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
        ).fetchone()
        if not exists:
            connection.execute(f'CREATE DATABASE "{database}"')
    _ensure_extensions(test_uri, database)


def _ensure_extensions(test_uri: str, database: str) -> None:
    """Enable the project's extensions inside ``database``, warning on failure.

    Mirrors ``scripts/create_test_database.py`` so a native PostgreSQL run
    exercises the same features as the container. Not fatal: a build without the
    contrib modules has no ``pg_trgm.control`` to install from, and the test
    database is still perfectly usable. ``CREATE EXTENSION`` cannot run inside a
    transaction either, hence AUTOCOMMIT.
    """
    unavailable: list[str] = []
    with psycopg.connect(_url_for_database(test_uri, database), autocommit=True) as connection:
        for extension in EXTENSIONS:
            try:
                connection.execute(
                    sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(extension))
                )
            except psycopg.Error as exc:
                unavailable.append(f"{extension} ({str(exc).splitlines()[0]})")
    if unavailable:
        print(
            f"[warn] extensions not installed in the test database: {', '.join(unavailable)}\n"
            "       This PostgreSQL build has no contrib modules; the search tests\n"
            "       that need pg_trgm/unaccent will skip. Install\n"
            "       postgresql-contrib to run them.",
            file=sys.stderr,
            flush=True,
        )


@contextlib.contextmanager
def _preserved_logging() -> Iterator[None]:
    """Restore the stdlib logging tree after Alembic's ``fileConfig`` runs.

    ``migrations/env.py`` calls ``logging.config.fileConfig``; letting that run
    mid-suite would swap out pytest's capture handlers behind its back.
    """
    names = ("", "alembic", "sqlalchemy", "sqlalchemy.engine")
    saved = {}
    for name in names:
        logger = logging.getLogger(name)
        saved[name] = (
            list(logger.handlers),
            logger.level,
            logger.propagate,
            logger.disabled,
        )
    try:
        yield
    finally:
        for name, (handlers, level, propagate, disabled) in saved.items():
            logger = logging.getLogger(name)
            logger.handlers[:] = handlers
            logger.setLevel(level)
            logger.propagate = propagate
            logger.disabled = disabled


def _alembic_config(test_uri: str) -> AlembicConfig:
    config = AlembicConfig(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    # ``%`` is a ConfigParser interpolation character, so it must be doubled for
    # the value to survive a round trip through the ini parser.
    config.set_main_option("sqlalchemy.url", test_uri.replace("%", "%%"))
    return config


def _assert_separate_test_database(test_uri: str, app_uri: str) -> None:
    """Abort the session unless the test database is not the application one.

    ``truncated_database`` runs ``TRUNCATE ... RESTART IDENTITY CASCADE``, so a
    ``TEST_DATABASE_URL`` left pointing at the application database would empty
    real data. ``scripts/create_test_database.py`` refuses the same collision,
    but the conftest that actually truncates has to refuse it too — that script
    is optional and may never have been run.
    """
    database = _database_name(test_uri)
    if database == _database_name(app_uri):
        pytest.exit(
            f'TEST_DATABASE_URL resolves to the application database "{database}".\n'
            "The suite truncates every managed table there, so it refuses to run.\n"
            "Point TEST_DATABASE_URL at a separate database (nexus -> nexus_test),\n"
            "or drop it so it is derived from POSTGRES_DB.",
            returncode=1,
        )


# ---------------------------------------------------------------------------
# Exclusive use of the test database
# ---------------------------------------------------------------------------

#: Mixed into every advisory-lock key. Two processes that disagreed on this
#: string would take different locks for the same database and the guard below
#: would quietly protect nothing, so it is part of the contract rather than a
#: value to tune.
_LOCK_NAMESPACE = "nexus-backend-pytest-session"

#: How long a second session keeps re-trying before it gives up. Long enough
#: that a session which is already finishing gives its lock back first, short
#: enough that a genuine conflict reads as a conflict instead of as a hang.
_LOCK_RETRY_ATTEMPTS = 20
_LOCK_RETRY_INTERVAL_SECONDS = 0.25

#: Ceiling on the probe's own connection attempt. Without it a run against a
#: host that silently drops packets spends minutes in the connect before the
#: session-start probe gives up and hands the problem to ``test_database_url``,
#: which turns "no database here" from an error into a stall.
_LOCK_CONNECT_TIMEOUT_SECONDS = 5

#: The locks this session holds, by database name. Keyed rather than a single
#: slot so the probe at session start and the fixture that enforces the guard
#: can both ask without the second call blocking on the first one's lock.
_SESSION_LOCKS: dict[str, _SessionDatabaseLock] = {}


class SessionLockError(RuntimeError):
    """Another pytest session already holds the test database.

    Named without the ``Test`` prefix deliberately: pytest collects classes that
    start with one out of test modules, and importing this into a test would
    turn it into a zero-test class that pytest warns about.
    """


class _SessionDatabaseLock:
    """One held advisory lock, and the connection that is holding it."""

    def __init__(self, database: str, connection: psycopg.Connection) -> None:
        self.database = database
        self._connection = connection

    def release(self) -> None:
        """Give the lock back and close the connection that holds it.

        The unlock is explicit rather than left to the close so the ordering is
        visible: the lock is released while this process is still healthy,
        rather than whenever the socket happens to be reaped.
        """
        with contextlib.suppress(psycopg.Error):
            self._connection.execute(
                "SELECT pg_advisory_unlock(%s::int, %s::int)",
                _database_lock_key(self.database),
            )
        self._connection.close()


def _async_url(sync_uri: str) -> str:
    """Put SQLAlchemy's driver suffix back, which :func:`_sync_url` strips."""
    return sync_uri.replace("postgresql://", "postgresql+psycopg://", 1)


def _database_lock_key(database: str) -> tuple[int, int]:
    """The two-``int4`` advisory-lock key that names ``database``.

    PostgreSQL advisory locks are cluster-wide: the lock table is shared by
    every database on the server, so a key derived from a constant alone would
    have session A on ``nexus_test`` excluding session B on some unrelated
    database. Folding the database name into the key is what makes the exclusion
    per-database, which is the granularity the suite actually wants.
    """
    digest = hashlib.blake2b(f"{_LOCK_NAMESPACE}:{database}".encode(), digest_size=8).digest()
    return (
        int.from_bytes(digest[:4], "big", signed=True),
        int.from_bytes(digest[4:], "big", signed=True),
    )


def _suggest_alternative_database(database: str) -> str:
    """A neighbouring name to offer when the database in use is already taken."""
    if database.endswith("_test"):
        return f"{database}_2"
    return f"{database}_test"


def _lock_holder_pid(connection: psycopg.Connection, database: str) -> int | None:
    """The backend PID holding ``database``'s lock, or ``None`` if nobody is.

    Best effort only. The PID is what lets the operator tell one stuck session
    from another, but a session that releases between the failed try and this
    query is a session that has just gone away, not an error worth raising.
    """
    first, second = _database_lock_key(database)
    row = connection.execute(
        "SELECT pid FROM pg_locks"
        " WHERE locktype = 'advisory' AND classid = %s AND objid = %s AND granted",
        (first, second),
    ).fetchone()
    return None if row is None else row[0]


def _lock_conflict_message(test_uri: str, database: str, holder_pid: int | None) -> str:
    """The operator-facing explanation, including the exact command to run."""
    alternative = _async_url(_url_for_database(test_uri, _suggest_alternative_database(database)))
    holder = f" (backend PID {holder_pid})" if holder_pid is not None else ""
    return (
        f'Another pytest session already holds the test database "{database}"{holder}.\n'
        "\n"
        "The suite truncates every managed table before each test, so two sessions\n"
        "on one database destroy each other's rows. Everything that follows reads\n"
        'like a defect in the product — "username already taken", foreign key\n'
        'violations, deadlocks, "could not refresh instance" — and none of it is.\n'
        "\n"
        "Wait for the other session to finish, or give this one its own database:\n"
        "\n"
        f"    TEST_DATABASE_URL={alternative}\n"
    )


def _take_session_lock(
    test_uri: str, *, attempts: int = _LOCK_RETRY_ATTEMPTS
) -> _SessionDatabaseLock:
    """Take the advisory lock naming the database ``test_uri`` points at.

    ``pg_try_advisory_lock`` rather than ``pg_advisory_lock``, because a plain
    blocking lock would park the second session for as long as the first one ran
    — indistinguishable, from the outside, from a hung test run. The caller
    polls a bounded number of times and reports the conflict itself, so what
    reaches the operator is a sentence about test databases rather than a query
    timeout or a driver traceback.

    The lock is taken from the maintenance database rather than from the target
    for two reasons: advisory locks are cluster-wide, so the key and not the
    connection's database is what scopes the exclusion; and a first run has to
    be able to claim the lock before the test database exists, which is exactly
    when it is most worth holding.
    """
    database = _database_name(test_uri)
    connection = psycopg.connect(
        _url_for_database(test_uri, "postgres"),
        autocommit=True,
        connect_timeout=_LOCK_CONNECT_TIMEOUT_SECONDS,
        # Named so that a holder reported by PID can be told apart from any other
        # connection to the server in ``pg_stat_activity``, which is where the
        # next person looks when the message names a PID.
        application_name="nexus-pytest-session-lock",
    )
    first, second = _database_lock_key(database)
    try:
        for attempt in range(attempts):
            acquired = connection.execute(
                "SELECT pg_try_advisory_lock(%s::int, %s::int)", (first, second)
            ).fetchone()[0]
            if acquired:
                return _SessionDatabaseLock(database, connection)
            if attempt + 1 < attempts:
                time.sleep(_LOCK_RETRY_INTERVAL_SECONDS)
        raise SessionLockError(
            _lock_conflict_message(test_uri, database, _lock_holder_pid(connection, database))
        )
    except BaseException:
        # Closing is the release: if the lock was taken and something failed
        # afterwards, the session-scope lock would otherwise outlive the attempt
        # and block every later run until this process died.
        connection.close()
        raise


def _acquire_session_lock(test_uri: str) -> _SessionDatabaseLock:
    """Hold the test database for this session, or end the session.

    Idempotent per database, which is what lets both callers use it: the probe
    in :func:`pytest_sessionstart` and the ``test_database_url`` fixture that
    enforces the guard before anything truncates.
    """
    database = _database_name(test_uri)
    held = _SESSION_LOCKS.get(database)
    if held is not None:
        return held
    try:
        lock = _take_session_lock(test_uri)
    except SessionLockError as conflict:
        pytest.exit(str(conflict), returncode=1)
    _SESSION_LOCKS[database] = lock
    return lock


def _release_session_locks() -> None:
    while _SESSION_LOCKS:
        _, lock = _SESSION_LOCKS.popitem()
        lock.release()


def _test_database_uri() -> str:
    """The database this session would test against, read from the environment."""
    get_settings.cache_clear()
    return get_settings().test_sqlalchemy_database_uri


def pytest_sessionstart(session: pytest.Session) -> None:
    """Claim the test database before any test in this session can truncate it.

    Best effort in one narrow sense: a PostgreSQL this session will never use
    is not an error. ``tests/test_risk_scoring.py`` is required to run with the
    database stopped, and that has to keep working. An unreachable server is
    therefore passed over here and surfaces later from ``test_database_url``,
    where the guard is not optional and where the connection failure is
    reported as itself.

    What the probe buys is position. The second session learns about the
    conflict in its first few seconds rather than part-way through a run that has
    already spent minutes manufacturing errors indistinguishable from defects.
    """
    with contextlib.suppress(psycopg.OperationalError):
        _acquire_session_lock(_test_database_uri())


def pytest_sessionfinish(session: pytest.Session, exitstatus: object) -> None:
    """Hand the lock back so the next session can start without waiting.

    PostgreSQL drops the lock when the connection closes either way, so this is
    about ordering rather than about recovering from a crash: the lock is
    released while this process is still in a known state.
    """
    _release_session_locks()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """Claim the database exclusively, create it if needed, migrate it to ``head``."""
    get_settings.cache_clear()
    settings = get_settings()
    uri = settings.test_sqlalchemy_database_uri
    _assert_separate_test_database(uri, settings.sqlalchemy_database_uri)
    # Before the migration, not after it: two sessions racing to bring the same
    # schema up is the same collision one schema version further along.
    _acquire_session_lock(uri)
    _ensure_database_exists(uri)
    with _preserved_logging():
        command.upgrade(_alembic_config(uri), "head")
    return uri


@pytest.fixture(scope="session")
def engine(test_database_url: str) -> Iterator[AsyncEngine]:
    """Session-wide engine on the test database, installed as the app's engine.

    ``NullPool`` keeps no connections between checkouts. That matters here
    because ``pytest.ini`` scopes the asyncio loop to a single test, so a pooled
    connection opened under one loop would be reused under the next.
    """
    previous_engine = app_db_session._engine
    previous_factory = app_db_session._session_factory

    test_engine = create_async_engine(test_database_url, poolclass=NullPool)
    app_db_session.configure(test_engine)
    try:
        yield test_engine
    finally:
        app_db_session._engine = previous_engine
        app_db_session._session_factory = previous_factory
        with contextlib.suppress(RuntimeError):
            asyncio.run(test_engine.dispose(), loop_factory=nexus_loop_factory)


@pytest.fixture
async def unreachable_engine(test_database_url: str) -> AsyncIterator[AsyncEngine]:
    """A real engine the server refuses, fast.

    The database probe builds its ``AsyncConnection`` lazily and aborts inside
    ``__aenter__``, so the connection is left unstarted: closing one in that
    state raises ``AsyncContextNotStarted``, and an exception raised out of a
    ``finally`` escapes the probe's own handler. Only a genuine engine reaches
    that path, so a hand-written stub cannot stand in for it.

    The refusal is made fast by naming a database the server does not have,
    rather than by pointing at a closed port: on Windows the connect to a
    refused port hangs until the probe's own ``asyncio.timeout`` fires, so the
    test would spend its entire budget proving nothing about the connection.
    """
    absent_database = f"{_database_name(test_database_url)}_absent"
    engine = create_async_engine(
        _async_url(_url_for_database(test_database_url, absent_database)), poolclass=NullPool
    )
    try:
        yield engine
    finally:
        with contextlib.suppress(RuntimeError):
            await engine.dispose()


@pytest.fixture
async def truncated_database(engine: AsyncEngine) -> None:
    """Empty every managed table so each test starts from a known state."""
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    if not tables:
        return
    async with engine.connect() as connection:
        await connection.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
        await connection.commit()


@pytest.fixture
async def db_session(engine: AsyncEngine, truncated_database: None) -> AsyncIterator[AsyncSession]:
    """A session on the test database, discarded after the test.

    The repository methods call ``session.commit()``, so writes made by a test
    are committed and outlive the session — the ``rollback()`` below only
    discards whatever a test left uncommitted. Isolation comes from
    ``truncated_database``, which empties every managed table before each test.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


@pytest.fixture
def app():
    """The real application object.

    ``ASGITransport`` does not run the lifespan, so nothing here may assume the
    startup or shutdown hooks have fired; the engine is installed by the
    ``engine`` fixture instead, which is what those hooks would have done.
    """
    from app.main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def offline_client(app) -> AsyncIterator[AsyncClient]:
    """A client with no database fixture in scope, for DB-free assertions."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://nexus.test") as http_client:
        yield http_client


@pytest.fixture
async def non_raising_client(app) -> AsyncIterator[AsyncClient]:
    """A client that observes the rendered 5xx response instead of the exception.

    :class:`httpx.ASGITransport` defaults to ``raise_app_exceptions=True``, so
    an exception escaping the app is re-raised inside the test and the response
    the user would have received is never visible. That is the behaviour a test
    wants when it is asserting on the bug, but it makes the application's own
    error handling — the ``internal_error`` catch-all handler — impossible to
    test: with the default transport the handler's output can never be observed.

    Use this fixture for anything that deliberately raises, and the default
    clients everywhere else: ``offline_client`` when no database is needed,
    ``client`` when the route does. Like the others, it depends on ``app`` alone,
    so a database-backed test can add ``truncated_database`` to its signature.
    """
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://nexus.test") as http_client:
        yield http_client


@pytest.fixture
async def client(app, engine: AsyncEngine, truncated_database: None) -> AsyncIterator[AsyncClient]:
    """An :class:`AsyncClient` wired to the app in-process, over a clean database.

    ``ASGITransport`` does not run the lifespan; the ``engine`` fixture has
    already put the test database behind ``app.db.session`` so the handlers find
    a reachable, migrated database without any startup hook having fired.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://nexus.test") as http_client:
        yield http_client


@pytest.fixture
def assert_error_envelope():
    """Assert the shared error envelope and return its ``error`` object."""

    def _assert(response, *, status_code: int, code: str) -> dict:
        assert response.status_code == status_code, response.text
        payload = response.json()
        assert set(payload) == {"error"}, payload
        error = payload["error"]
        assert set(error) == {"code", "message", "details", "request_id"}, error
        assert error["code"] == code
        assert isinstance(error["message"], str) and error["message"]
        assert error["request_id"] == response.headers["X-Request-ID"]
        return error

    return _assert
