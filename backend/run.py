"""Development and container entrypoint for the NEXUS backend.

``python run.py`` is the supported way to start the API. It exists so that the
event loop is selected in exactly one place: psycopg's async driver cannot run
on the ProactorEventLoop that asyncio defaults to on Windows, and
``app.core.event_loop`` resolves that platform difference for the server, the
Alembic migrations and the test suite alike.

Configuration comes from the repository-root ``.env`` (``NEXUS_HOST``,
``NEXUS_PORT``, ``NEXUS_RELOAD``) and from the process environment, which wins;
everything else is read through ``app.core.config``.
"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

#: The repository root — the directory holding the shared ``.env``. Derived from
#: this file's own location rather than from the working directory, so
#: ``python backend/run.py`` and ``python run.py`` find the same file; this is
#: the same anchoring ``app.core.config`` uses for ``BACKEND_ROOT``.
REPO_ROOT = Path(__file__).resolve().parents[1]

#: uvicorn resolves ``--loop`` as an import path, so the factory has to be
#: importable by name rather than passed as a callable.
LOOP_TARGET = "app.core.event_loop:nexus_loop_factory"

_TRUTHY = {"1", "true", "yes", "on"}


def main() -> None:
    """Start the API server from environment configuration."""
    # `load_dotenv` is called here rather than relying on pydantic-settings:
    # `Settings` populates its own fields but never writes to `os.environ`, so
    # without this the NEXUS_* values in `.env` are invisible below. Already-set
    # variables are not overridden, so a one-off `NEXUS_PORT=9001 python run.py`
    # still wins.
    load_dotenv(REPO_ROOT / ".env")

    uvicorn.run(
        "app.main:app",
        host=os.getenv("NEXUS_HOST", "127.0.0.1"),
        port=int(os.getenv("NEXUS_PORT", "8000")),
        reload=os.getenv("NEXUS_RELOAD", "false").lower() in _TRUTHY,
        # The request-context middleware already emits one access log line per
        # request; uvicorn's own would be a duplicate.
        access_log=False,
        loop=LOOP_TARGET,
    )


if __name__ == "__main__":
    main()
