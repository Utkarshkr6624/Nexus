"""Health endpoints and request-correlation behaviour."""

from __future__ import annotations

import uuid

import pytest

from app.api.v1 import health as health_api
from app.db import session as app_db_session
from app.db.session import check_database_connection
from tests.conftest import _preserved_logging

#: Every test below that reaches PostgreSQL is marked ``integration``: those
#: taking ``client``, and the degraded-readiness probe, which needs a server
#: that can refuse it. The liveness and header tests deliberately take
#: ``offline_client`` and stay outside that marker.
integration = pytest.mark.integration


async def test_liveness_answers_without_touching_the_database(offline_client, monkeypatch):
    """``/health`` must stay green while Postgres is unreachable.

    Liveness feeds a restart loop, so a database fault must never fail it —
    that is asserted by making any database access an error, not by inspection.
    """

    def _explode(*args, **kwargs):
        raise AssertionError("/health must not touch the database")

    monkeypatch.setattr(app_db_session, "get_engine", _explode)
    monkeypatch.setattr(app_db_session, "get_session_factory", _explode)
    monkeypatch.setattr(app_db_session, "check_database_connection", _explode)

    response = await offline_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_response_carries_a_request_id(offline_client):
    response = await offline_client.get("/health")

    request_id = response.headers["X-Request-ID"]
    assert uuid.UUID(request_id)


async def test_client_supplied_request_id_is_echoed(offline_client):
    supplied = "11111111-2222-4333-8444-555555555555"

    response = await offline_client.get("/health", headers={"X-Request-ID": supplied})

    assert response.headers["X-Request-ID"] == supplied


async def test_client_supplied_request_id_reaches_the_error_body(
    offline_client, assert_error_envelope
):
    supplied = "abcdef01-2345-4678-89ab-cdef01234567"

    response = await offline_client.get(
        "/api/v1/does-not-exist", headers={"X-Request-ID": supplied}
    )

    error = assert_error_envelope(response, status_code=404, code="not_found")
    assert error["request_id"] == supplied


async def test_a_database_fault_degrades_readiness_without_failing_it(offline_client, monkeypatch):
    """The endpoint reporting that the service is degraded is itself healthy.

    The probe is imported into the router's own namespace, so that is the name
    the patch has to replace; patching ``app.db.session`` would leave the real
    probe in place and reach for the database this test exists to avoid.
    """

    async def _unavailable() -> bool:
        return False

    monkeypatch.setattr(health_api, "check_database_connection", _unavailable)

    response = await offline_client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"]["status"] == "unavailable"
    assert body["database"]["latency_ms"] >= 0


@integration
async def test_an_unreachable_database_degrades_readiness_through_the_real_probe(
    non_raising_client, unreachable_engine, monkeypatch
):
    """The same degraded answer, this time from the probe the router really calls.

    The test above replaces ``health_api.check_database_connection`` itself, so
    the router never runs the probe and any fault inside it is untestable from
    there — a probe that let an exception escape would turn this endpoint's 500
    into a passing test. Here only the engine underneath is replaced, so the
    router, the probe, and the failure path all execute, and
    ``non_raising_client`` means a regression shows up as the 500 a caller would
    actually have received rather than as a traceback out of the transport.
    """
    monkeypatch.setattr(app_db_session, "get_engine", lambda: unreachable_engine)

    response = await non_raising_client.get("/api/v1/health")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"]["status"] == "unavailable"
    assert body["database"]["latency_ms"] >= 0


@integration
async def test_service_metadata(client):
    response = await client.get("/")

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "NEXUS"
    assert body["api_prefix"] == "/api/v1"
    assert body["status"] == "operational"


@integration
async def test_detailed_health_reports_the_database(client):
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "status",
        "app",
        "version",
        "environment",
        "database",
        "uptime_seconds",
        "timestamp",
    }
    assert body["status"] == "healthy"
    assert body["app"] == "NEXUS"
    assert set(body["database"]) == {"status", "latency_ms"}
    assert body["database"]["status"] == "connected"
    assert body["database"]["latency_ms"] >= 0
    assert body["uptime_seconds"] >= 0
    assert body["timestamp"].endswith("Z")


@integration
async def test_app_lifespan_runs_without_error(app, engine):
    """Startup and shutdown hooks must work when the server actually runs.

    ``ASGITransport`` skips the lifespan, so it is driven manually here. The
    engine fixture stands in for the process-wide engine the lifespan expects,
    and the logging tree is saved and restored because the startup hook calls
    ``configure_logging(..., force=True)``.
    """
    del engine  # requested for its side effect: installs the app-wide engine
    with _preserved_logging():
        async with app.router.lifespan_context(app):
            assert app.state.settings is not None
            assert await check_database_connection() is True
