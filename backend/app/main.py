"""NEXUS backend application factory.

Run locally with::

    python run.py

That entrypoint is the supported way to start the API on every OS. It selects
the event loop explicitly because psycopg's async driver is built on
``loop.add_reader``, which asyncio's Windows default (``ProactorEventLoop``)
does not provide: a bare ``uvicorn app.main:app --reload`` starts there and
then fails on every query with ``InterfaceError: Psycopg cannot use the
'ProactorEventLoop' to run in async mode``.

Run in Docker with a single worker; the engine is created per process.

Startup also loads the Phase 11 intent classifier when ``ML_ENABLED`` is on,
which reads ~703 MiB of weights from ``ml/artifacts``. It runs in a worker
thread so the event loop is not blocked while that happens, and it degrades
rather than crashing: without a checkpoint the server still starts and the ML
endpoints answer 503. Set ``ML_FAIL_FAST=true`` to make a failed load stop the
process instead.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.exceptions import install_exception_handlers
from app.core.logging import configure_logging, get_logger, log_event
from app.core.middleware import RateLimitMiddleware, add_request_context_middleware
from app.db import session as db_session
from app.db.session import check_database_connection
from app.ml.runtime import MLRuntime, get_ml_runtime

__all__ = ["app", "create_app"]

logger = get_logger("app.main")

SERVICE_DESCRIPTION = """
NEXUS is a local-first **Personal Intelligence & Decision Platform**: a single
backend for projects, planning, knowledge capture, analytics and machine
learning, with your data on your own machine.

### Conventions

* Every response is JSON. Every non-2xx response uses one error envelope:

  ```json
  {"error": {"code": "not_found", "message": "...", "details": null,
             "request_id": "0f1e..."}}
  ```

  `code` is a stable snake_case identifier — branch on it, not on `message`.

* Authenticated endpoints expect `Authorization: Bearer <access_token>`.
* Every response carries an `X-Request-ID` header; quote it in bug reports.
"""


_OPENAPI_TAGS: list[dict[str, object]] = [
    {
        "name": "meta",
        "description": "Service identity, version and endpoint index.",
    },
    {
        "name": "health",
        "description": (
            "Liveness and readiness. `/health` is dependency-free; "
            "`/api/v1/health` additionally probes the database."
        ),
    },
    {
        "name": "auth",
        "description": "Registration, login, token refresh, logout and identity.",
    },
]


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Stamp the uptime clock, configure logging, probe the database, load ML, dispose on exit.

    Startup does three things beyond the clock, and all three are advisory: the
    database probe, the ML load, and the settings they are both given. A
    deployment without a Postgres instance still serves, and a deployment
    without a Phase 10 checkpoint still serves — it simply answers the ML
    endpoints with 503 and a reason. Only ``ML_FAIL_FAST`` turns the second of
    those into a refusal to boot.

    The ML runtime is stored on ``app.state`` so a route can reach the one
    loaded instance from the request rather than importing it. Note that the
    test suite builds clients on ``ASGITransport`` **without running a
    lifespan**, so ``app.state.ml_runtime`` is absent there; the provider in
    :mod:`app.api.deps` falls back to the module singleton for that case, and
    the shutdown below tolerates a runtime that was never attached.
    """
    # `create_app` may have been handed settings that differ from the global
    # singleton, and those are the ones CORS, the docs URLs and the API prefix
    # were built from — logging and the probe must agree with them.
    settings = getattr(app.state, "settings", None) or get_settings()

    # Stamped here rather than at construction: a reload re-imports this module
    # before the new worker serves anything, so import time is not uptime.
    app.state.start_time = time.monotonic()

    configure_logging(settings, force=True)
    logger.info(
        "service_starting",
        extra={
            "environment": settings.environment,
            "version": settings.app_version,
        },
    )

    # The probe is advisory. It is wrapped here as well as inside
    # `check_database_connection` so that a timeout or any future failure mode
    # degrades the log line instead of preventing the server from starting.
    try:
        database_ready = await check_database_connection()
    except Exception:
        database_ready = False
    log_event(
        logger,
        logging.INFO if database_ready else logging.WARNING,
        "database_probe",
        database="connected" if database_ready else "unavailable",
    )

    # Reading 703 MiB of weights takes seconds of blocked CPU. That happens in a
    # worker thread rather than on the event loop, because a loop stalled during
    # startup is a loop that cannot answer the probe that is waiting on it.
    # The runtime is process state from the global settings singleton, exactly
    # as the engine below is: an app built with its own `Settings` still shares
    # the one model, because two would mean two copies of the weights and two
    # answers to "is ML up" that could disagree.
    runtime = get_ml_runtime()
    app.state.ml_runtime = runtime
    try:
        status = await run_in_threadpool(runtime.load)
    except Exception:
        # Only reachable under ML_FAIL_FAST, where the runtime re-raises by
        # design. Swallowing it here would turn a strict deployment into a
        # silently degraded one and defeat the flag, so the status is read back
        # for the log line and the process exits through the server's own
        # lifespan failure handling.
        status = runtime.status
        log_event(logger, logging.ERROR, "ml_runtime_startup_failed", reason=status.reason)
    # The runtime logs its own attempt with the checkpoint path and the load
    # time; this line answers the different question "was ML ready by the time
    # the server started serving", which is what an operator reads a boot log
    # for. It duplicates nothing, and it is emitted whether the load came from
    # here or from the first request to touch the classifier.
    log_event(
        logger,
        logging.INFO if status.available else logging.WARNING,
        "ml_runtime_ready",
        available=status.available,
        reason=status.reason,
    )

    try:
        yield
    finally:
        # Disposing only an engine that exists: `get_engine()` would build one
        # from the default settings purely to close it again.
        engine = db_session._engine
        if engine is not None:
            await engine.dispose()
        # The same rule for the model: an app whose lifespan never ran has no
        # runtime on its state, and releasing "whatever the singleton is" would
        # tear down a process that never took ownership of it.
        runtime_on_state: MLRuntime | None = getattr(app.state, "ml_runtime", None)
        if runtime_on_state is not None:
            runtime_on_state.shutdown()
        logger.info("service_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured NEXUS application instance."""
    settings = settings or get_settings()

    application = FastAPI(
        title=f"{settings.app_name} API",
        version=settings.app_version,
        description=settings.app_description + SERVICE_DESCRIPTION,
        openapi_url=settings.openapi_url,
        docs_url=settings.docs_url,
        redoc_url=settings.redoc_url,
        lifespan=_lifespan,
        openapi_tags=_OPENAPI_TAGS,
        contact={"name": "NEXUS"},
    )
    application.state.settings = settings
    # `start_time` is deliberately absent: the lifespan stamps it when serving
    # begins, and `/api/v1/health` falls back to its own reference for an app
    # whose lifespan has not run (an in-process client, for instance).

    # Added first so that it lands innermost among the user middleware: inside
    # CORS, so a browser that is refused can read the 429 and its ``Retry-After``
    # rather than see an opaque CORS failure, and immediately above the router,
    # which is the only layer positioned to refuse a request before it runs.
    application.add_middleware(RateLimitMiddleware, settings=settings)

    if settings.cors_origin_list:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID"],
        )

    # Added last so that it wraps CORS and the router: every response then
    # carries X-Request-ID and every request produces exactly one access line.
    add_request_context_middleware(application, settings)

    install_exception_handlers(application)

    application.include_router(api_router, prefix=settings.api_v1_prefix)

    @application.get("/health", tags=["health"], summary="Liveness probe")
    async def health() -> dict[str, str]:
        """Answer without touching the database.

        Liveness must stay green while Postgres is down, otherwise a restart
        loop would be triggered by a dependency failure rather than by a fault
        in this process. Readiness lives at ``/api/v1/health``.
        """
        return {"status": "ok"}

    @application.get("/", tags=["meta"], summary="Service metadata")
    async def root() -> dict[str, object]:
        return {
            "service": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
            "status": "operational",
            "api_version": settings.api_v1_prefix.lstrip("/").replace("/", "."),
            "api_prefix": settings.api_v1_prefix,
            "links": {
                "liveness": "/health",
                "health": f"{settings.api_v1_prefix}/health",
                "docs": settings.docs_url,
                "redoc": settings.redoc_url,
                "openapi": settings.openapi_url,
            },
        }

    return application


app = create_app()
