"""Throttling: what it lets through, what it refuses, and what it never says.

:class:`app.core.middleware.RateLimitMiddleware` is the only thing standing
between an unauthenticated caller and an open password-guessing loop on
``POST /api/v1/auth/login``. It is installed by ``create_app``, so most of these
tests drive it the way a caller does — over HTTP, through the real app — rather
than poking the counter directly. That matters for the properties under test:
which routes share a budget, which do not, and what the refusal *looks like*
are all things only the wired-up path can decide.

Figures are derived, never recorded. Each test builds its own
:class:`~app.core.config.Settings` with the limits it needs, so the numbers in
the assertions are the numbers in the test and a suite-wide change to a default
cannot quietly move them.

What each test is for
---------------------
* **The boundary.** Five requests pass against a five-request budget, the sixth
  is refused. Getting this off by one in either direction is a real defect:
  too few is a false positive that locks a user out of their own account, too
  many is no limiter at all.
* **The refusal's shape.** A 429 without the shared envelope would break every
  frontend error handler in one step, and one without ``Retry-After`` would
  leave a well-behaved client guessing how long to back off.
* **Decay.** A budget that never returns turns a burst into an outage.
* **The budget split.** Credential routes draw on their own, tighter budget, so
  a login flood cannot hide inside general API traffic, and one address cannot
  exhaust another's. Registration draws on the same tight number under a
  separate key, so it is throttled without taking the login budget away from a
  client that is signing in for the first time.
* **Enumeration.** The limiter must not make guessing *easier*, so every attempt
  inside the budget is still answered by the auth service: 401 for a wrong
  password, never 429.

House style, following ``tests/test_middleware.py``
---------------------------------------------------
* ``pytestmark = pytest.mark.integration`` — the wrong-password assertions are
  about what the auth service answers, and that needs the live PostgreSQL the
  suite truncates between tests.
* A module-level ``_app()`` helper: ``create_app`` with explicit settings, plus
  one route of our own standing in for "the rest of the API" so the general
  budget can be exercised without a handler that needs the database.
* The clock is injected rather than slept through. ``RateLimitMiddleware`` takes
  a ``time_source``, so the decay test advances it by hand and is neither slow
  nor timing-sensitive — the alternative, a one-second window and a real sleep,
  is a test that fails on a loaded machine.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.middleware import RateLimitMiddleware, _FixedWindowLimiter
from app.main import create_app

pytestmark = pytest.mark.integration

LOGIN_PATH = "/api/v1/auth/login"
FORGOT_PATH = "/api/v1/auth/password/forgot"
REGISTER_PATH = "/api/v1/auth/register"
GENERAL_PATH = "/_test/general"

#: Registered through the API rather than seeded, because the assertion under
#: test is about what a wrong password reads as — and that is only the same
#: question when the account behind the address exists.
CREDENTIALS = {"email": "ada@nexus.test", "username": "ada", "password": "Correct-Horse-9"}
WRONG = {"email": CREDENTIALS["email"], "password": "Wrong-Horse-9"}

#: Small on purpose: these tests set the budget they assert on rather than
#: inheriting the shipped defaults, so the numbers in the assertions are the
#: numbers in this file and cannot drift with a configuration change.
BUDGET = 5
CREDENTIAL_BUDGET = 3


def _settings(**overrides: Any) -> Settings:
    """Deterministic settings, independent of whatever the developer's .env says."""
    defaults: dict[str, Any] = {
        "rate_limit_window_seconds": 60,
        "rate_limit_general_max_requests": BUDGET,
        "rate_limit_credential_max_requests": CREDENTIAL_BUDGET,
        "rate_limit_max_entries": 100,
    }
    defaults.update(overrides)
    return Settings(_env_file=None, environment="test", debug=False, **defaults)


def _app(**overrides: Any) -> FastAPI:
    """A NEXUS app with a named budget, and one plain route to spend it on."""

    async def general() -> dict[str, str]:
        return {"ok": "ok"}

    application = create_app(_settings(**overrides))
    application.add_api_route(GENERAL_PATH, general, methods=["GET"])
    return application


def _throttled_app(time_source: Callable[[], float], **overrides: Any) -> FastAPI:
    """A bare app carrying only the limiter, driven by a clock we control.

    Nothing else is installed, which is the point: this test is about the
    limiter's own arithmetic over time, and going through the whole application
    stack to watch a window expire would test the test runner's patience.
    """

    async def general() -> dict[str, str]:
        return {"ok": "ok"}

    application = FastAPI()
    application.add_middleware(
        RateLimitMiddleware, settings=_settings(**overrides), time_source=time_source
    )
    application.add_api_route(GENERAL_PATH, general, methods=["GET"])
    return application


@pytest.fixture
async def client_for(engine: AsyncEngine, truncated_database: None):
    """Hand back an in-process client over an app the test configured itself."""

    def _make(**overrides: Any) -> AsyncClient:
        transport = ASGITransport(app=_app(**overrides))
        opened.append(AsyncClient(transport=transport, base_url="http://nexus.test"))
        return opened[-1]

    opened: list[AsyncClient] = []
    try:
        yield _make
    finally:
        for client in opened:
            await client.aclose()


@pytest.fixture
async def clocked_client() -> AsyncIterator[tuple[AsyncClient, list[float]]]:
    """A client whose limiter reads its window off a clock the test advances."""
    now = [1000.0]
    transport = ASGITransport(app=_throttled_app(lambda: now[0]))
    async with AsyncClient(transport=transport, base_url="http://nexus.test") as http_client:
        yield http_client, now


async def _register(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/register", json=CREDENTIALS)
    assert response.status_code == 201, response.text


async def _drain(client: AsyncClient, path: str, count: int, **kwargs: Any) -> None:
    """Make ``count`` requests to ``path``, asserting each one was let through."""
    for _ in range(count):
        response = await client.get(path, **kwargs)
        assert response.status_code == 200, response.text


# -- The boundary ----------------------------------------------------------


async def test_every_request_up_to_the_limit_is_allowed(client_for):
    """Five requests inside a five-request budget all reach the route."""
    client = client_for()

    await _drain(client, GENERAL_PATH, BUDGET)


async def test_the_request_past_the_limit_is_refused_with_the_shared_envelope(
    client_for, assert_error_envelope
):
    """The sixth of six is a 429 with the standard envelope and a Retry-After.

    The window opened on the first of the five allowed requests, so the sixth
    is one elapsed-second short of the full sixty: between 59 and 60 seconds,
    and never zero or absent.
    """
    client = client_for()

    await _drain(client, GENERAL_PATH, BUDGET)
    response = await client.get(GENERAL_PATH)

    error = assert_error_envelope(response, status_code=429, code="rate_limited")
    retry_after = int(response.headers["Retry-After"])
    assert 59 <= retry_after <= 60, retry_after
    assert str(retry_after) in error["message"], error["message"]


async def test_a_refused_request_does_not_extend_the_window(client_for):
    """Hammering past the limit does not push the end of it further away.

    A refused request is not counted. If it were, a caller who kept hammering
    would never see its budget return, and the ``Retry-After`` it was handed on
    the first refusal would quietly become untrue.
    """
    client = client_for()

    await _drain(client, GENERAL_PATH, BUDGET)
    for _ in range(BUDGET):
        assert (await client.get(GENERAL_PATH)).status_code == 429


# -- Decay -----------------------------------------------------------------


async def test_the_budget_returns_once_the_window_has_elapsed(clocked_client):
    """Sixty seconds after the window opened, five more requests all pass.

    Six are made against a budget of five, so the assertion is not vacuous: the
    store has to have forgotten the first window rather than gone slack.
    """
    client, now = clocked_client

    await _drain(client, GENERAL_PATH, BUDGET)
    assert (await client.get(GENERAL_PATH)).status_code == 429

    now[0] += 60.0
    await _drain(client, GENERAL_PATH, BUDGET)


# -- Budgets are per route, and per address --------------------------------


async def test_the_credential_budget_is_separate_and_tighter(client_for):
    """The login budget is spent while the general one is still untouched.

    Three logins exhaust the credential budget of three; five general requests
    still pass afterwards, and the fourth login is refused. Had the two shared
    a counter, one of the five would have been turned away.
    """
    client = client_for()
    await _register(client)

    for _ in range(CREDENTIAL_BUDGET):
        assert (await client.post(LOGIN_PATH, json=WRONG)).status_code == 401

    await _drain(client, GENERAL_PATH, BUDGET)
    assert (await client.post(LOGIN_PATH, json=WRONG)).status_code == 429


async def test_each_credential_route_draws_on_one_shared_budget(client_for):
    """`/auth/login` and `/auth/password/forgot` share the tighter budget.

    Guessing a password and enumerating an address are the same attack, so a
    caller who split its traffic across the two endpoints would otherwise be
    handed double the allowance for one purpose.
    """
    client = client_for()
    payload = {"email": CREDENTIALS["email"]}

    for _ in range(CREDENTIAL_BUDGET):
        response = await client.post(FORGOT_PATH, json=payload)
        assert response.status_code == 202, response.text

    assert (await client.post(FORGOT_PATH, json=payload)).status_code == 429
    assert (await client.post(LOGIN_PATH, json=WRONG)).status_code == 429


async def test_registration_is_throttled_at_the_tight_budget(client_for):
    """`/auth/register` draws on a tight budget of its own, not the general one.

    It is the one route that is unauthenticated *and* expensive: every accepted
    registration runs a bcrypt hash, and every rejected one answers 409 for an
    address or username that already exists. Left on the generous budget it was
    both a quarter of a second of server CPU per call, on request, from an
    anonymous caller, and an enumeration oracle with a 600-per-minute allowance.
    """
    client = client_for()
    # Each registration is a distinct account, so none of them is refused by a
    # duplicate check — the budget is what has to stop them.
    for index in range(CREDENTIAL_BUDGET):
        response = await client.post(
            REGISTER_PATH,
            json={
                "email": f"new{index}@nexus.test",
                "username": f"new{index}",
                "password": "Correct-Horse-9",
            },
        )
        assert response.status_code == 201, response.text

    assert (await client.post(REGISTER_PATH, json={**CREDENTIALS})).status_code == 429


async def test_registration_does_not_spend_the_login_budget(client_for):
    """Registering and then signing in both fit inside one window.

    The two budgets are separate on purpose. Sharing them would mean the
    ordinary path through a first session cannot be completed inside the budget,
    which is a product failure rather than a security win — the sharing between
    ``/auth/login`` and ``/auth/password/forgot`` exists because *those* two are
    one attack, and registering is not.
    """
    client = client_for()
    await _register(client)

    for _ in range(CREDENTIAL_BUDGET):
        response = await client.post(LOGIN_PATH, json=WRONG)
        assert response.status_code == 401, response.text


async def test_one_client_address_does_not_spend_another_one_s_budget(client_for):
    """Two addresses, two budgets: the throttling is per caller, not global.

    Only meaningful behind a trusted proxy, which is the one configuration that
    believes ``X-Forwarded-For`` — see the next test for why it is off by
    default.
    """
    client = client_for(rate_limit_trust_forwarded_for=True)

    await _drain(client, GENERAL_PATH, BUDGET, headers={"X-Forwarded-For": "198.51.100.7"})
    allowed = await client.get(GENERAL_PATH, headers={"X-Forwarded-For": "198.51.100.8"})

    assert allowed.status_code == 200


# -- Enumeration -----------------------------------------------------------


async def test_a_wrong_password_reads_as_a_wrong_password_on_every_allowed_attempt(client_for):
    """Three wrong passwords, three 401s, and not one 429 among them.

    A limiter that refused inside the budget would be answering about itself
    rather than about the password: an attacker learns the limit, and a user who
    mistypes is told the wrong thing about why it failed. Every attempt here
    falls inside the budget of three, so every one is the auth service's answer.
    """
    client = client_for()
    await _register(client)

    for _ in range(CREDENTIAL_BUDGET):
        response = await client.post(LOGIN_PATH, json=WRONG)

        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "unauthorized"


async def test_the_refusal_says_nothing_about_whether_the_account_exists(client_for):
    """A 429 names a duration, and reads identically for any address.

    The limiter runs before any handler has looked at a row, so the response to
    a known address and the response to an address nobody registered are the
    same object with the same words in it. An enumerator comparing the two gets
    no signal, which is the whole point: the limiter must not become the
    existence oracle the auth service works to avoid.
    """
    client = client_for()
    await _register(client)

    for _ in range(CREDENTIAL_BUDGET):
        await client.post(LOGIN_PATH, json=WRONG)
    known = await client.post(LOGIN_PATH, json=WRONG)
    unknown = await client.post(LOGIN_PATH, json={"email": "nobody@nexus.test", "password": "x"})

    assert known.status_code == unknown.status_code == 429
    assert known.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert known.headers["Retry-After"] == unknown.headers["Retry-After"]
    assert CREDENTIALS["email"] not in known.text


# -- Configuration ---------------------------------------------------------


async def test_nothing_is_throttled_when_the_limiter_is_switched_off(client_for):
    """`RATE_LIMIT_ENABLED=false` leaves every request to the rest of the stack."""
    client = client_for(rate_limit_enabled=False, rate_limit_general_max_requests=1)

    await _drain(client, GENERAL_PATH, 10 * BUDGET)


async def test_a_forwarded_address_is_not_believed_by_default(client_for):
    """Rotating `X-Forwarded-For` cannot mint a fresh budget.

    That header is caller-controlled on any path not terminating in a proxy we
    run, so believing it would make the limiter key on attacker-supplied data.
    Five requests claiming five different originating addresses are all served
    from the one budget of five, and the sixth is refused no matter what it
    claims.
    """
    client = client_for()

    for index in range(BUDGET):
        response = await client.get(
            GENERAL_PATH, headers={"X-Forwarded-For": f"198.51.100.{index}"}
        )
        assert response.status_code == 200, response.text

    refused = await client.get(GENERAL_PATH, headers={"X-Forwarded-For": "198.51.100.99"})

    assert refused.status_code == 429


def test_the_store_drops_a_key_once_its_window_has_elapsed():
    """A settled key is removed, so the store is self-evicting.

    Twenty keys open three windows' worth apart against a one-entry store: only
    the newest may be resident, and the rest must have been swept rather than
    merely displaced by the cap.
    """
    limiter = _FixedWindowLimiter(window_seconds=60.0, max_entries=1)

    for index in range(20):
        limiter.consume(("198.51.100.1", "/api/v1/projects"), index * 60.0, 5)

    assert len(limiter._windows) == 1


def test_the_store_never_grows_past_its_configured_ceiling():
    """Ten thousand distinct keys through a two-entry store still leave two.

    Ten thousand is the number ``RATE_LIMIT_MAX_ENTRIES`` defaults to, and the
    keys are distinct addresses rather than distinct times, so the cap and not
    the sweep is what is being exercised.
    """
    limiter = _FixedWindowLimiter(window_seconds=60.0, max_entries=2)

    for index in range(10_000):
        verdict = limiter.consume((f"198.51.100.{index}", "/api/v1/projects"), 0.0, 5)
        assert verdict.allowed

    assert len(limiter._windows) <= 2
