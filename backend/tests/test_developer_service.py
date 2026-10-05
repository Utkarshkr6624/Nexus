"""The Phase 8 service end to end: what it stores, what it refuses, what it repeats.

:meth:`app.services.developer.service.DeveloperIntelligenceService` is the only
seam between "a user typed a directory" and "here is a repository". Everything
interesting about Phase 8 is decided there and nowhere else, so this file drives
it directly rather than through HTTP — the route layer has its own file, and a
test that went through it would be testing the router as much as the service.

What the file is for
--------------------
``app/services/developer/git.py`` and ``app/services/developer/metrics.py`` have
their own tests, over the pure functions. Neither of those can decide the things
below, because each of them is a statement about *storage* and *time*:

**What registration refuses.** The path is validated before it is stored, so a
directory that is not a work tree never becomes a row that fails on every future
scan. The per-account cap and the ``(user_id, local_path)`` uniqueness live here
too, and both are checked by counting rows rather than by asking the database to
enforce them.

**What a scan writes and what a second scan does not.** A scan is idempotent
because every write is an upsert keyed on the schema's unique constraints. The
figure that proves it is ``commits_added == 0`` on a re-scan, and the figures that
prove the *counter* did not regress are the stored ``commit_count`` and the set
of stored commit hashes — which is also why every row below is read back through
an explicit column projection rather than through the ORM.

**What the event feed records and what it refuses to repeat.** A scan writes one
``REPOSITORY_SCANNED`` row every time, and ``COMMIT_DETECTED`` /
``BRANCH_CREATED`` / ``BRANCH_CHANGED`` only for facts the previous scan had not
seen. The assertions are made on *counts by type* rather than on an ordered list,
because the feed's ordering is the database's to choose and the claim being tested
is about which facts were new, not about which row happened to be written first.

**What a broken repository does.** It becomes a ``status='error'`` scan run
carrying a human sentence. It never raises, and it never removes the history the
earlier scans recorded. A scan service that let a deleted directory out of its
``except`` clause would be one unlucky ``POST /scan`` away from a 500.

**What absence looks like on the way out.** A metric that could not be measured
comes back ``available=False`` with a reason and a null value; a metric that
measured zero comes back ``value=0.0, available=True``. Those are different
answers, and a feature vector's ``repository_age_days`` is ``null`` — never 0 —
for a repository that has never recorded a commit, because 0 would claim it was
created today.

House style, deliberately
-------------------------
Follows ``tests/test_risk_detection.py``:

* ``pytestmark = pytest.mark.integration`` — every test here needs the live
  PostgreSQL the suite truncates between tests.
* Services are hand-wired in a module-level ``_service()`` helper that mirrors
  the way ``app.api.deps`` will wire them, so a collaborator cannot quietly be
  ``None``. The activity sink in particular is always the real one: passing
  ``activity=None`` would make every event assertion below pass vacuously.
* Rows are read back through **explicit column tuples**, never ORM entities. This
  session is also the one that wrote the rows, so an entity read would hand back
  whatever the identity map cached and an idempotency assertion would compare
  stale objects to fresh ones and pass for the wrong reason.
* The clock is read from the database through ``_db_now``, never from
  ``date.today()``, because the service resolves every window from ``func.now()``
  and the two must describe the same day.

Every expected figure in this file is derived in the test's own docstring rather
than recorded from a run, and the fixtures are pinned to whole days so the
arithmetic holds at any hour.

Two defects these tests found, and their fixes
-------------------------------------------------
Both were found while writing this file and both have since been fixed in the
service. They are recorded here because the tests below are what pins the
corrected behaviour, and because the shape of each failure is the kind that a
later refactor could reintroduce silently.

1. **Registration raised the wrong exception type.**
   :meth:`~app.services.developer.service.DeveloperIntelligenceService.register_repository`
   promised ``ValidationError`` for a path that is not a work tree, and the route
   that turns that into a 422 depends on the promise. What it actually raised was
   :exc:`app.services.developer.git.GitRepositoryError`, a bare ``Exception``
   subclass that no exception handler is registered for — so a bad path reached
   the client as an unhandled 500 instead of a 422, which is the opposite of the
   "a broken repository must never break NEXUS" rule the same module states. The
   service now catches it and re-raises ``ValidationError`` carrying the engine's
   own sentence. :func:`test_a_path_that_is_not_a_work_tree_is_refused_and_stores_no_row`
   pins the 422.

2. **``COMMIT_DETECTED`` was never emitted.**
   :meth:`~app.services.developer.service.DeveloperIntelligenceService._store_snapshot`
   read ``high_water_mark=_as_utc(repository.latest_commit_at)`` *after* calling
   ``update_scan_state``. That call is an ``UPDATE ... RETURNING GitRepository``
   run with ``populate_existing=True`` against the same identity-mapped object the
   lookup returned, so by then ``latest_commit_at`` already held the **new** value
   and every commit failed the strict ``committed_at > high_water_mark`` test. No
   commit event was written, on a first scan as well as an incremental one. The
   mark is now captured before the write.
   :func:`test_a_scan_records_new_facts_once_and_nothing_for_an_unchanged_repository`
   pins the events a scan now writes.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
import subprocess
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.activity import ActivityLog
from app.models.developer import GitBranch, GitCommit, GitRepository, GitScanRun
from app.models.enums import ActivityEvent, GitScanStatus
from app.models.user import User
from app.repositories.activity import ActivityRepository
from app.repositories.developer import DeveloperRepository
from app.repositories.project import ProjectRepository
from app.services.activity_service import ActivityService
from app.services.developer.metrics import DEVELOPER_METRICS, NOT_ENOUGH_DATA
from app.services.developer.service import DeveloperIntelligenceService
from tests.analytics_fixtures import AnalyticsSeed, register_user

pytestmark = pytest.mark.integration

#: The window the metric fixture uses. Thirty days is the configured default and
#: is the value every explanation sentence below is written against, so the
#: arithmetic stays whole: ``4/30`` is 0.1333 rather than a recurring fraction.
WINDOW = 30


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def _service(
    session: AsyncSession, *, settings: Settings | None = None
) -> DeveloperIntelligenceService:
    """A developer service wired the way ``app.api.deps`` will wire it.

    Every collaborator is the real one. The activity sink in particular: it is
    what ``REPOSITORY_REGISTERED``, ``REPOSITORY_SCANNED``, ``COMMIT_DETECTED``,
    ``BRANCH_CREATED`` and ``BRANCH_CHANGED`` are written through, and this file
    asserts on all five, so a ``None`` sink would make a third of it vacuous.

    Args:
        session: The test session. Every repository is built on the same one,
            which is why the reads below go through explicit columns.
        settings: Supplied only by the cap test, which needs a deployment whose
            ``developer_max_repositories`` is two rather than the configured
            hundred.
    """
    return DeveloperIntelligenceService(
        repositories=DeveloperRepository(session),
        projects=ProjectRepository(session),
        activity=ActivityService(ActivityRepository(session)),
        settings=settings,
    )


async def _owner(session: AsyncSession, username: str = "ada") -> User:
    """One account, inserted directly.

    Direct rather than through the API because these tests drive the service, not
    a route, and ``register_user`` writes no activity events — which matters,
    because the event assertions below count event types and a registration row
    would move them.
    """
    return await register_user(session, username=username)


async def _db_now(session: AsyncSession) -> datetime:
    """The database's clock, as an aware UTC instant.

    The same read :meth:`DeveloperIntelligenceService._now` performs. Fixtures are
    placed relative to this rather than to ``datetime.now()`` so that a commit the
    service will read back sits inside the window the service will compute.

    Normalised to UTC rather than returned as it arrived, because ``now()`` comes
    back labelled with the *connection's* ``TimeZone``. It is the same instant
    either way, so the arithmetic below is unaffected — but a caller that takes
    ``.date()`` off the result would otherwise get the server-local day while the
    service bucketed by the UTC one.
    """
    value = await session.scalar(select(func.now()))
    if not isinstance(value, datetime):
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


# ---------------------------------------------------------------------------
# Real git repositories
# ---------------------------------------------------------------------------

#: Identity and safety flags passed to every ``git`` call below.
#:
#: ``-c user.*`` so a commit never depends on whatever global config the machine
#: running the suite happens to have; ``-c core.autocrlf=false`` so the line
#: counts git reports are the line counts the fixture wrote rather than the ones
#: a Windows checkout rewrote; and ``-c safe.directory=*`` so a ``tmp_path``
#: owned by a different user on a build agent is still readable. This is test
#: setup, not a product behaviour — the engine under test passes no ``-c`` flags
#: at all.
_GIT_FLAGS: tuple[str, ...] = (
    "-c",
    "safe.directory=*",
    "-c",
    "user.name=Ada Lovelace",
    "-c",
    "user.email=ada@nexus.test",
    "-c",
    "core.autocrlf=false",
    "-c",
    "core.safecrlf=false",
    "-c",
    "commit.gpgsign=false",
)


def _git(repo: Path, *args: str, when: datetime | None = None) -> str:
    """Run one git command inside ``repo`` and return its stdout.

    ``when`` sets both the author and the committer date, which is how the
    fixtures below place a commit on a chosen day without sleeping: git records
    whatever the environment tells it, and the assertions are about those
    recorded instants rather than about the wall clock.

    A real repository is built rather than a mocked subprocess because a mock
    only proves that *this* implementation passed *these* arguments; what has to
    hold is that git's own output parses into the stored rows.

    Args:
        repo: The working directory. Must already exist.
        *args: The subcommand and its arguments, each a separate element.
        when: The instant to record on the commit, if any.

    Returns:
        The command's stdout, stripped.

    Raises:
        CalledProcessError: If git exits non-zero. A fixture that cannot build
            the repository it is about to register should fail loudly rather than
            hand the test an empty snapshot.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if when is not None:
        env["GIT_AUTHOR_DATE"] = when.isoformat()
        env["GIT_COMMITTER_DATE"] = when.isoformat()
    completed = subprocess.run(  # noqa: S603 — test fixture, argv is fixed
        ["git", *_GIT_FLAGS, *args],  # noqa: S607 — the fixture resolves git on PATH
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _init(root: Path, name: str) -> Path:
    """Create and return an initialised, commit-less work tree called ``name``.

    ``symbolic-ref HEAD`` is set explicitly rather than relying on
    ``init.defaultBranch``: git's default branch name has been ``master`` and
    ``main`` in different versions and under different configurations, and every
    assertion about ``current_branch`` and ``is_default`` below would otherwise
    depend on the git that happens to be installed.
    """
    repo = root / name
    repo.mkdir(parents=True)
    _git(repo, "init", "--quiet")
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    return repo


def _commit(repo: Path, filename: str, message: str, *, when: datetime, body: str) -> str:
    r"""Write ``body`` to ``filename``, commit it, and return the full hash.

    ``body`` is written with an explicit newline so the file's line count is the
    number of lines the fixture asked for on every platform — with the default
    text-mode translation a Windows run would store ``\\r\\n`` and the
    derived ``additions`` figures would be describing a different file.

    Args:
        repo: The work tree.
        filename: Path relative to the repository root.
        message: The commit subject.
        when: The instant git should record on the commit.
        body: The whole new content of the file.

    Returns:
        The full commit hash.
    """
    target = repo / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8", newline="\n")
    _git(repo, "add", filename, when=when)
    _git(repo, "commit", "--quiet", "--no-gpg-sign", "-m", message, when=when)
    return _git(repo, "rev-parse", "HEAD")


def _force_rmtree(path: Path) -> None:
    """Delete ``path`` and everything under it, read-only files included.

    Git marks its object files read-only on Windows so a stray tool cannot corrupt
    the object store, which makes a plain ``shutil.rmtree`` fail with
    ``PermissionError`` on exactly the deletion the broken-repository fixture
    needs. The retry clears the read-only bit first; ``onexc`` rather than the
    removed ``onerror`` because the suite runs on Python 3.13.
    """

    def _retry(function, target, _exc_info) -> None:
        with contextlib.suppress(OSError):
            os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
        function(target)

    shutil.rmtree(path, onexc=_retry)


# ---------------------------------------------------------------------------
# Reading stored rows back
# ---------------------------------------------------------------------------

#: The columns every stored repository is read back through. Listed in full so a
#: column added to the model shows up here rather than as a silently unasserted
#: field.
_REPOSITORY_COLUMNS = (
    GitRepository.id,
    GitRepository.name,
    GitRepository.local_path,
    GitRepository.is_active,
    GitRepository.current_branch,
    GitRepository.default_branch,
    GitRepository.branch_count,
    GitRepository.commit_count,
    GitRepository.first_commit_at,
    GitRepository.latest_commit_at,
    GitRepository.primary_language,
    GitRepository.working_tree_dirty,
    GitRepository.last_scanned_at,
    GitRepository.last_scan_status,
    GitRepository.last_scan_error,
)

_COMMIT_COLUMNS = (
    GitCommit.id,
    GitCommit.commit_hash,
    GitCommit.short_hash,
    GitCommit.committed_at,
    GitCommit.message,
    GitCommit.author_name,
    GitCommit.author_email,
    GitCommit.additions,
    GitCommit.deletions,
    GitCommit.files_changed,
    GitCommit.branch,
)

_BRANCH_COLUMNS = (
    GitBranch.name,
    GitBranch.is_current,
    GitBranch.is_default,
    GitBranch.head_commit_hash,
    GitBranch.last_committed_at,
)

_SCAN_COLUMNS = (
    GitScanRun.status,
    GitScanRun.commits_discovered,
    GitScanRun.commits_added,
    GitScanRun.branches_discovered,
    GitScanRun.duration_ms,
    GitScanRun.error,
)


async def _repositories(session: AsyncSession, owner_id: uuid.UUID) -> list[dict[str, object]]:
    """Every stored repository for one account, as plain mappings."""
    result = await session.execute(
        select(*_REPOSITORY_COLUMNS)
        .where(GitRepository.user_id == owner_id)
        .order_by(GitRepository.local_path.asc())
    )
    return [dict(row._mapping) for row in result.all()]


async def _repository(session: AsyncSession, repository_id: uuid.UUID) -> dict[str, object]:
    """The single stored repository row, asserting there is exactly one."""
    result = await session.execute(
        select(*_REPOSITORY_COLUMNS).where(GitRepository.id == repository_id)
    )
    row = result.one_or_none()
    assert row is not None, f"no stored repository with id {repository_id}"
    return dict(row._mapping)


async def _commits(
    session: AsyncSession, owner_id: uuid.UUID, *, repository_id: uuid.UUID | None = None
) -> list[dict[str, object]]:
    """Stored commits, newest first."""
    filters = [GitCommit.user_id == owner_id]
    if repository_id is not None:
        filters.append(GitCommit.repository_id == repository_id)
    result = await session.execute(
        select(*_COMMIT_COLUMNS).where(*filters).order_by(GitCommit.committed_at.desc())
    )
    return [dict(row._mapping) for row in result.all()]


async def _branches(
    session: AsyncSession, owner_id: uuid.UUID, repository_id: uuid.UUID
) -> list[dict[str, object]]:
    """Stored branches, by name."""
    result = await session.execute(
        select(*_BRANCH_COLUMNS)
        .where(GitBranch.user_id == owner_id, GitBranch.repository_id == repository_id)
        .order_by(GitBranch.name.asc())
    )
    return [dict(row._mapping) for row in result.all()]


async def _scan_runs(
    session: AsyncSession, owner_id: uuid.UUID, repository_id: uuid.UUID
) -> list[dict[str, object]]:
    """Stored scan runs, oldest first.

    Oldest first so "the first scan" and "the second scan" are index positions
    rather than a comparison against whatever ``list_scan_runs`` happened to
    order by.
    """
    result = await session.execute(
        select(*_SCAN_COLUMNS)
        .where(GitScanRun.user_id == owner_id, GitScanRun.repository_id == repository_id)
        .order_by(GitScanRun.scanned_at.asc(), GitScanRun.id.asc())
    )
    return [dict(row._mapping) for row in result.all()]


#: The eight Phase 8 event types. Named in full rather than filtered on a prefix
#: so an event added to the reconciliation shows up in this tuple and fails the
#: assertion rather than being filtered past it.
_DEVELOPER_EVENTS = (
    ActivityEvent.REPOSITORY_REGISTERED.value,
    ActivityEvent.REPOSITORY_UPDATED.value,
    ActivityEvent.REPOSITORY_SCANNED.value,
    ActivityEvent.REPOSITORY_REMOVED.value,
    ActivityEvent.COMMIT_DETECTED.value,
    ActivityEvent.BRANCH_CREATED.value,
    ActivityEvent.BRANCH_CHANGED.value,
    ActivityEvent.FILE_ACTIVITY_DETECTED.value,
)


async def _events(session: AsyncSession, user_id: uuid.UUID) -> list[dict[str, object]]:
    """This account's Phase 8 history rows, with their metadata.

    Filtered to the eight developer events rather than to the whole feed: the
    assertions are about the reconciliation this phase writes, and a feed that
    also carried unrelated events would make every count a substring search.

    Ordering is left to the query and never asserted on. The claim under test is
    "this fact was recorded" and "this fact was not recorded again", which counts
    answer; the order the database chose to write eight transactions in is not a
    fact about the product.
    """
    result = await session.execute(
        select(ActivityLog.event_type, ActivityLog.metadata_).where(
            ActivityLog.user_id == user_id,
            ActivityLog.event_type.in_(_DEVELOPER_EVENTS),
        )
    )
    return [{"event_type": str(row[0]), "metadata": row[1]} for row in result.all()]


def _event_counts(events: list[dict[str, object]]) -> Counter[str]:
    """``{event type: how many}`` for one pass's history rows."""
    counts: Counter[str] = Counter()
    for event in events:
        counts[str(event["event_type"])] += 1
    return counts


# ---------------------------------------------------------------------------
# Seeding recorded history without touching the filesystem
# ---------------------------------------------------------------------------

#: The commit rows the metrics fixture stores. Every field is chosen so the eight
#: figures are whole numbers:
#:
#: ==========  =========  =========  ========  ========  =====  ======  =====
#: day offset  additions  deletions  files     messages  branch  commits  note
#: ==========  =========  =========  ========  ========  =====  ======  =====
#: 1           10         0          1         A         main   1        same day as B
#: 1           0          4          1         B         main   1        same day as A
#: 2           7          1          1         C         main   1        own day
#: 5           0          0          0         D         main   1        own day, no files
#: 12          9          3          1         E         feat   1        own day, 1 repo
#: ==========  =========  =========  ========  ========  =====  ======  =====
#:
#: So: 5 commits, 1 repository, 34 changed lines (26 added + 8 deleted), 4 active
#: days (A and B share day 1, so five commits land on four dates), 4 of the 5
#: commits touching at least one file (A, B, C, E — D touches none), and a
#: momentum of 4 commits in the last 7 days against 1 in the 7 before (E, on day
#: 12, is the only one in that band).
_COMMIT_FIXTURE: tuple[tuple[int, int, int, int, str, str], ...] = (
    (1, 10, 0, 1, "A", "main"),
    (1, 0, 4, 1, "B", "main"),
    (2, 7, 1, 1, "C", "main"),
    (5, 0, 0, 0, "D", "main"),
    (12, 9, 3, 1, "E", "feature/alpha"),
)


async def _seed_commits(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    repository_id: uuid.UUID,
) -> list[dict[str, object]]:
    """Store the five-commit fixture above, on the days its table names.

    Written through the repository's own upsert rather than through a scan
    because the point of the metrics fixture is the rows, and a scan would make
    every metric assertion depend on git's line counting as well.
    """
    now = await _db_now(session)
    rows = [
        {
            "commit_hash": f"{offset:02d}{letter}{index}",
            "short_hash": f"{offset:02d}{letter}{index}",
            "committed_at": now - timedelta(days=offset),
            "message": f"commit {letter}",
            "author_name": "Ada Lovelace",
            "author_email": "ada@nexus.test",
            "additions": additions,
            "deletions": deletions,
            "files_changed": files,
            "branch": branch,
        }
        for index, (offset, additions, deletions, files, letter, branch) in enumerate(
            _COMMIT_FIXTURE, start=1
        )
    ]
    await DeveloperRepository(session).upsert_commits(owner_id, repository_id, rows)
    return rows


async def _registered_repository(
    session: AsyncSession, tmp_path: Path, owner: User, *, name: str = "nexus-service"
) -> tuple[DeveloperIntelligenceService, str]:
    """Register one initialised work tree and return ``(service, repository id)``.

    The shared first step of the storage fixtures. No commits: the point of the
    shared part is only that a validated path became a row, so a scan test can
    start from "registered, never read" and every figure below is one the test
    itself produced.
    """
    service = _service(session)
    repo = _init(tmp_path, name)
    registered = await service.register_repository(owner=owner, local_path=str(repo))
    return service, str(registered.id)


# ---------------------------------------------------------------------------
# (a) Registration
# ---------------------------------------------------------------------------


async def test_registering_a_repository_stores_the_resolved_path_and_a_fresh_row(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """One row, absolute path, directory name, no counters, one event.

    The stored ``local_path`` is the *resolved* absolute directory, not the string
    that was typed: a relative path stored as given would resolve somewhere else
    the next time a scan ran from a different working directory. The name defaults
    to the directory name because that is a fact about the path rather than an
    inference about the user.

    Every counter is zero and ``last_scanned_at`` is null. That is the whole point
    of ``last_scanned_at IS NULL`` being "never scanned" rather than a status
    word: registering a path observes nothing, so a row that arrived with its
    counters filled would be claiming a repository had been read when it had not.
    """
    owner = await _owner(db_session)
    repo = _init(tmp_path, "nexus-service")

    registered = await _service(db_session).register_repository(owner=owner, local_path=str(repo))

    assert registered.local_path == str(repo.resolve())
    assert registered.name == "nexus-service"
    assert registered.is_active is True
    assert registered.commit_count == 0
    assert registered.branch_count == 0
    assert registered.last_scanned_at is None
    assert registered.current_branch is None
    assert registered.default_branch is None
    assert registered.primary_language is None

    stored = await _repository(db_session, registered.id)
    assert stored["local_path"] == str(repo.resolve())
    assert stored["name"] == "nexus-service"
    assert stored["commit_count"] == 0
    assert stored["branch_count"] == 0
    assert stored["last_scanned_at"] is None

    events = await _events(db_session, owner.id)
    assert _event_counts(events) == {ActivityEvent.REPOSITORY_REGISTERED.value: 1}
    assert events[0]["metadata"] == {"repository_id": str(registered.id)}


async def test_two_accounts_may_register_the_same_local_path_but_one_account_may_not(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """A second registration by the owner is a conflict; a second account's is fine.

    ``uq_git_repositories_owner_path`` is deliberately per-account. The path is a
    local directory on one machine, and two accounts watching the same directory
    is not contention — a global unique index on the path would have made it one.
    The half that is genuinely contended is the same account registering the same
    directory twice, which would give one work tree two rows whose commit counts
    would then disagree with each other.
    """
    ada = await _owner(db_session, "ada")
    grace = await _owner(db_session, "grace")
    repo = _init(tmp_path, "shared")
    service = _service(db_session)

    first = await service.register_repository(owner=ada, local_path=str(repo))
    again = await service.register_repository(owner=grace, local_path=str(repo))

    assert first.id != again.id
    with pytest.raises(ConflictError) as already:
        await service.register_repository(owner=ada, local_path=str(repo))
    assert str(already.value) == "That repository is already registered for this account."

    assert [row["id"] for row in await _repositories(db_session, ada.id)] == [first.id]
    assert [row["id"] for row in await _repositories(db_session, grace.id)] == [again.id]


async def test_a_path_that_is_not_a_work_tree_is_refused_and_stores_no_row(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """A directory with no ``.git``, and a directory that does not exist, are both refused.

    Both refusals happen **before** the write, which is what the assertion is
    really about: the account's repository count stays at zero and the history
    feed stays empty, so no row exists that would fail on every future scan and
    would already be on the dashboard by the time anyone found out.

    Each message names the path the user gave, because the two refusals are
    different facts — "there is nothing there" and "that is not a repository" —
    with different remediations.

    **On the exception type.** The engine raises
    :exc:`app.services.developer.git.GitRepositoryError`, a bare ``Exception``
    subclass that no exception handler is registered for. The service catches it
    and re-raises :class:`app.core.exceptions.ValidationError` — which is what the
    route turns into the 422 the contract describes, and what its own docstring
    promises. Letting the engine's exception escape would surface as an
    unhandled 500 and take the page down with it.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    plain = tmp_path / "not-a-repository"
    plain.mkdir()
    missing = tmp_path / "never-created"

    with pytest.raises(ValidationError) as not_a_repo:
        await service.register_repository(owner=owner, local_path=str(plain))
    with pytest.raises(ValidationError) as no_directory:
        await service.register_repository(owner=owner, local_path=str(missing))

    assert "no .git entry was found in it" in str(not_a_repo.value)
    assert "No directory was found at" in str(no_directory.value)
    assert await _repositories(db_session, owner.id) == []
    assert await _events(db_session, owner.id) == []


async def test_a_relative_path_is_resolved_before_it_is_stored(
    db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registering ``relative/repo`` stores the absolute directory, not the string.

    This is the only reason :func:`~app.services.developer.git.validate_repository_path`
    returns a ``Path`` rather than a bool. Storing the string the caller typed is
    how a registered repository ends up pointing at a different directory after
    the working directory changes — which for a server process is every
    deployment since the first one.

    The assertion is that the stored path is *absolute and equal to the resolved
    directory*, rather than a substring check: ``str(path).startswith(str(repo))``
    would also pass for ``.../nexus-service-backup``.
    """
    owner = await _owner(db_session)
    repo = _init(tmp_path, "nexus-service")
    monkeypatch.chdir(tmp_path)

    registered = await _service(db_session).register_repository(
        owner=owner, local_path="./nexus-service"
    )

    assert Path(registered.local_path).is_absolute()
    assert Path(registered.local_path) == repo.resolve()
    assert (await _repository(db_session, registered.id))["local_path"] == str(repo.resolve())


async def test_the_repository_cap_refuses_the_registration_that_would_exceed_it(
    db_session: AsyncSession, tmp_path: Path, make_settings
) -> None:
    """At the cap of two, the third registration is a conflict and names the limit.

    The cap counts **every** row, not the active ones. A repository the user
    paused still occupies its slot, and a cap that silently ignored it would be a
    number that could change without anything being registered — so the fixture
    deactivates the first repository and asserts that the third is still refused.
    That is the boundary a naive ``WHERE is_active`` would lose.

    Grace registering one repository of her own at the same time is the other
    half: the cap is per account, so Ada reaching hers cannot stop Grace.
    """
    settings = make_settings(DEVELOPER_MAX_REPOSITORIES="2")
    ada = await _owner(db_session, "ada")
    grace = await _owner(db_session, "grace")
    service = _service(db_session, settings=settings)

    for index in ("one", "two"):
        repo = _init(tmp_path, index)
        await service.register_repository(owner=ada, local_path=str(repo))
    paused = (await _repositories(db_session, ada.id))[0]
    await service.update_repository(
        owner=ada, repository_id=uuid.UUID(str(paused["id"])), values={"is_active": False}
    )
    grace_repo = _init(tmp_path, "grace-repo")
    await service.register_repository(owner=grace, local_path=str(grace_repo))

    third = _init(tmp_path, "three")
    with pytest.raises(ConflictError) as at_cap:
        await service.register_repository(owner=ada, local_path=str(third))

    assert str(at_cap.value) == (
        "This account already has the maximum of 2 repositories registered."
    )
    assert len(await _repositories(db_session, ada.id)) == 2
    assert len(await _repositories(db_session, grace.id)) == 1


async def test_registering_against_another_accounts_project_is_not_found(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """A project id that belongs to somebody else is not found, never forbidden.

    ``project_id`` is user input, so trusting it would let one account attach its
    repository to another account's project. The project is therefore resolved
    through an owner-scoped lookup before anything is written, and a miss raises
    the same not-found the repository layer uses. A 403 would confirm the project
    id exists.
    """
    ada = await _owner(db_session, "ada")
    grace = await _owner(db_session, "grace")
    ada_project = await AnalyticsSeed(db_session, ada).project(name="Atlas")
    repo = _init(tmp_path, "nexus-service")
    service = _service(db_session)

    with pytest.raises(NotFoundError) as foreign:
        await service.register_repository(
            owner=grace, local_path=str(repo), project_id=ada_project.id
        )

    assert str(foreign.value) == "That project was not found."
    assert await _repositories(db_session, grace.id) == []


# ---------------------------------------------------------------------------
# (b) Ownership
# ---------------------------------------------------------------------------


async def test_another_accounts_repository_is_not_found_and_its_rows_never_appear(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Ada's repository, scanned and full of commits, is invisible to Grace — and 404.

    Every entry point is exercised, because each one could have been written with
    the owner predicate missing and the *detail* would still have been private:
    the detail read, the list, the commit timeline, the branch list, and the
    scoped metrics. Ownership is a predicate in the ``WHERE`` clause rather than a
    filter over a loaded page, so the foreign row is never loaded at all.

    The foreign id and an id nobody ever issued raise the **same message**. That
    is what stops the endpoint being an existence oracle: if the two answers
    differed, a caller could enumerate other people's repository ids by comparing
    them.
    """
    ada = await _owner(db_session, "ada")
    grace = await _owner(db_session, "grace")
    service, ada_repository = await _registered_repository(db_session, tmp_path, ada)
    now = await _db_now(db_session)
    ada_path = tmp_path / "nexus-service"
    _commit(ada_path, "app.py", "first", when=now - timedelta(days=2), body="one\ntwo\nthree\n")
    await service.scan_repository(owner=ada, repository_id=uuid.UUID(ada_repository))
    stored_commits = await _commits(db_session, ada.id)
    assert len(stored_commits) == 1, "the fixture must have produced a commit to hide"

    with pytest.raises(NotFoundError) as detail:
        await service.get_repository(owner=grace, repository_id=uuid.UUID(ada_repository))
    with pytest.raises(NotFoundError) as unknown:
        await service.get_repository(owner=grace, repository_id=uuid.uuid4())
    with pytest.raises(NotFoundError) as branches:
        await service.branches(owner=grace, repository_id=uuid.UUID(ada_repository))
    with pytest.raises(NotFoundError) as scoped_metrics:
        await service.metrics(owner=grace, repository_id=uuid.UUID(ada_repository))
    with pytest.raises(NotFoundError) as scan:
        await service.scan_repository(owner=grace, repository_id=uuid.UUID(ada_repository))
    with pytest.raises(NotFoundError) as remove:
        await service.delete_repository(owner=grace, repository_id=uuid.UUID(ada_repository))

    assert str(detail.value) == str(unknown.value) == "That repository was not found."
    assert str(branches.value) == str(scoped_metrics.value) == str(detail.value)
    assert str(scan.value) == str(remove.value) == str(detail.value)

    # And every *unscoped* read is empty rather than carrying Ada's rows.
    grace_repositories = await service.list_repositories(owner=grace)
    assert grace_repositories.total == 0
    assert grace_repositories.items == []
    grace_commits = await service.commits(owner=grace)
    assert grace_commits.total == 0
    assert grace_commits.items == []
    grace_metrics = await service.metrics(owner=grace)
    assert {metric.key for metric in grace_metrics} == set(DEVELOPER_METRICS)
    assert all(metric.value in (0.0, None) for metric in grace_metrics)
    grace_summary = await service.summary(owner=grace)
    assert grace_summary.has_data is False
    assert grace_summary.summary == (
        "No repository has been registered yet, so there is no recorded history to report."
    )

    # Ada's rows are untouched by any of it.
    assert [row["id"] for row in await _repositories(db_session, ada.id)] == [
        uuid.UUID(ada_repository)
    ]
    assert [row["commit_hash"] for row in await _commits(db_session, ada.id)] == [
        stored_commits[0]["commit_hash"]
    ]


# ---------------------------------------------------------------------------
# (c) Scanning
# ---------------------------------------------------------------------------


async def test_scanning_a_repository_stores_its_commits_and_its_branches(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Two commits, two branches, and the counts git itself reported.

    Derived from the fixture. Commit A adds a new file of three lines, so git's
    numstat reports ``3 insertions``, ``0 deletions``, ``1 file changed``. Commit B
    rewrites the same file with two extra lines, so it reports ``2 insertions``,
    ``0 deletions``, ``1 file changed`` — additions are counted against the new
    content, not against the whole file. Change volume for the repository is
    therefore 5 lines, and the stored ``commit_count`` is 2 with
    ``branch_count`` 2 (the branch ``feature`` is created at commit B's tip and so
    has no commit of its own).

    ``current_branch`` and ``default_branch`` are both ``main``: HEAD points at it
    and git's own ``symbolic-ref HEAD`` resolves to it, the fallback being used
    because a locally initialised repository has no ``refs/remotes/origin/HEAD``.

    The branch each commit is attributed to is asserted as *membership* rather
    than as an exact name. Attribution is explicitly best-effort — a commit
    belongs to whichever branch head currently reaches it, and both heads reach
    commit B — so pinning a name here would pin a property git does not promise.
    """
    owner = await _owner(db_session)
    service, repository_id = await _registered_repository(db_session, tmp_path, owner)
    repo = tmp_path / "nexus-service"
    now = await _db_now(db_session)
    _commit(repo, "app.py", "first", when=now - timedelta(days=3), body="one\ntwo\nthree\n")
    _commit(
        repo, "app.py", "second", when=now - timedelta(days=1), body="one\ntwo\nthree\nfour\nfive\n"
    )
    _git(repo, "branch", "feature")

    run = await service.scan_repository(owner=owner, repository_id=uuid.UUID(repository_id))

    assert run.status == GitScanStatus.OK.value
    assert run.commits_discovered == 2
    assert run.commits_added == 2
    assert run.branches_discovered == 2
    assert run.error is None
    assert run.duration_ms >= 0

    commits = await _commits(db_session, owner.id)
    assert len(commits) == 2
    newest, oldest = commits
    assert newest["message"] == "second"
    assert (newest["additions"], newest["deletions"], newest["files_changed"]) == (2, 0, 1)
    assert oldest["message"] == "first"
    assert (oldest["additions"], oldest["deletions"], oldest["files_changed"]) == (3, 0, 1)
    assert oldest["author_name"] == "Ada Lovelace"
    assert oldest["author_email"] == "ada@nexus.test"
    assert oldest["commit_hash"].startswith(oldest["short_hash"])
    # Membership only — see the docstring.
    assert {newest["branch"], oldest["branch"]} <= {"main", "feature"}

    branches = await _branches(db_session, owner.id, uuid.UUID(repository_id))
    assert [row["name"] for row in branches] == ["feature", "main"]
    by_name = {row["name"]: row for row in branches}
    assert (by_name["main"]["is_current"], by_name["main"]["is_default"]) == (True, True)
    assert (by_name["feature"]["is_current"], by_name["feature"]["is_default"]) == (False, False)
    assert by_name["main"]["head_commit_hash"] == newest["commit_hash"]
    assert by_name["feature"]["head_commit_hash"] == newest["commit_hash"]

    stored = await _repository(db_session, uuid.UUID(repository_id))
    assert stored["commit_count"] == 2
    assert stored["branch_count"] == 2
    assert stored["current_branch"] == "main"
    assert stored["default_branch"] == "main"
    assert stored["primary_language"] == "Python"
    assert stored["working_tree_dirty"] is False
    assert stored["last_scan_status"] == GitScanStatus.OK.value
    assert stored["last_scan_error"] is None
    assert stored["last_scanned_at"] is not None
    assert stored["first_commit_at"] is not None
    assert stored["latest_commit_at"] is not None


async def test_rescanning_an_unchanged_repository_adds_nothing_and_regresses_nothing(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """A second scan: nothing added, the same two rows, the same counters.

    This is the idempotency claim, and it is asserted from both sides. The
    *counters* say the scan added nothing — ``commits_added == 0``, the figure
    the service's own docstring names as the proof — and the *rows* say it did
    not duplicate or replace anything: the same two commit ids, the same two
    branch rows, the same hashes. A service that set ``commit_count`` from what
    the second scan returned would also report 0 here and pass the counter
    assertion while the stored count collapsed from 2 to 0, so the stored
    ``commit_count == 2`` is asserted explicitly; it is the only assertion that
    catches that.

    ``commits_discovered`` is **1**, not 0, and the figure is worth being precise
    about. The scan is incremental: it passes the stored ``latest_commit_at`` to
    git as ``--since``, and git's ``--since`` admits the commit landing exactly on
    that instant. So the boundary commit is transferred again and the upsert
    recognises it. The older commit is not transferred at all, which is what
    makes an unchanged repository cost one row rather than its whole history —
    and ``commits_added == 0`` against ``commits_discovered == 1`` is exactly the
    shape the schema documents: two counters that differ because the upsert
    worked, not because anything went wrong.
    """
    owner = await _owner(db_session)
    service, repository_id = await _registered_repository(db_session, tmp_path, owner)
    repo = tmp_path / "nexus-service"
    now = await _db_now(db_session)
    _commit(repo, "app.py", "first", when=now - timedelta(days=3), body="one\ntwo\nthree\n")
    _commit(repo, "app.py", "second", when=now - timedelta(days=1), body="one\ntwo\nthree\nfour\n")
    _git(repo, "branch", "feature")
    identifier = uuid.UUID(repository_id)

    await service.scan_repository(owner=owner, repository_id=identifier)
    first_commits = await _commits(db_session, owner.id)
    first_repository = await _repository(db_session, identifier)

    second = await service.scan_repository(owner=owner, repository_id=identifier)
    second_commits = await _commits(db_session, owner.id)
    second_repository = await _repository(db_session, identifier)

    assert second.status == GitScanStatus.OK.value
    assert second.commits_discovered == 1
    assert second.commits_added == 0
    assert second.branches_discovered == 2
    assert len(second_commits) == len(first_commits) == 2
    assert [row["id"] for row in second_commits] == [row["id"] for row in first_commits]
    assert [row["commit_hash"] for row in second_commits] == [
        row["commit_hash"] for row in first_commits
    ]
    assert second_repository["commit_count"] == first_repository["commit_count"] == 2
    assert second_repository["branch_count"] == 2
    assert second_repository["latest_commit_at"] == first_repository["latest_commit_at"]
    assert len(await _branches(db_session, owner.id, identifier)) == 2
    assert len(await _scan_runs(db_session, owner.id, identifier)) == 2


async def test_a_scan_records_new_facts_once_and_nothing_for_an_unchanged_repository(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Three passes over one repository, and what each one adds to the history feed.

    The branch half of the contract holds, and it is asserted over three passes
    with the feed counted by type after each. Derived from the fixture:

    * pass 1 sees two commits on two branches, so it writes one
      ``REPOSITORY_SCANNED`` and two ``BRANCH_CREATED``;
    * pass 2 sees an unchanged work tree, so it adds exactly one
      ``REPOSITORY_SCANNED`` and nothing else — the branch listing is complete
      and every name and head hash is the same, so neither ``BRANCH_CREATED`` nor
      ``BRANCH_CHANGED`` fires;
    * pass 3 is handed a third commit on ``main`` and a new branch ``hotfix`` at
      that commit's tip, so it adds one ``REPOSITORY_SCANNED``, one
      ``BRANCH_CHANGED`` (main's head moved from the second commit to the third)
      and one ``BRANCH_CREATED`` (hotfix, which no previous scan saw).

    **``COMMIT_DETECTED`` counts the commits that landed since the previous
    scan.** It is 2 on the first pass and 0 on the second. The mark it is
    compared against is captured *before* the scan-state write, in
    :meth:`~app.services.developer.service.DeveloperIntelligenceService._store_snapshot`:
    reading it afterwards would compare each commit against the newest commit's
    own timestamp, because ``update_scan_state`` runs with
    ``populate_existing=True`` against the same identity-mapped object and has
    by then overwritten ``latest_commit_at`` — which is why the count would be
    0 on every pass if the mark were read late.
    """
    owner = await _owner(db_session)
    service, repository_id = await _registered_repository(db_session, tmp_path, owner)
    repo = tmp_path / "nexus-service"
    identifier = uuid.UUID(repository_id)
    now = await _db_now(db_session)
    _commit(repo, "app.py", "first", when=now - timedelta(days=3), body="one\ntwo\nthree\n")
    _commit(repo, "app.py", "second", when=now - timedelta(days=2), body="one\ntwo\nthree\nfour\n")
    _git(repo, "branch", "feature")

    await service.scan_repository(owner=owner, repository_id=identifier)
    first = _event_counts(await _events(db_session, owner.id))
    assert first == {
        ActivityEvent.REPOSITORY_REGISTERED.value: 1,
        ActivityEvent.REPOSITORY_SCANNED.value: 1,
        ActivityEvent.BRANCH_CREATED.value: 2,
        ActivityEvent.COMMIT_DETECTED.value: 2,
    }

    await service.scan_repository(owner=owner, repository_id=identifier)
    second = _event_counts(await _events(db_session, owner.id))
    assert second == {
        ActivityEvent.REPOSITORY_REGISTERED.value: 1,
        ActivityEvent.REPOSITORY_SCANNED.value: 2,
        ActivityEvent.BRANCH_CREATED.value: 2,
        ActivityEvent.COMMIT_DETECTED.value: 2,
    }

    _commit(
        repo, "app.py", "third", when=now - timedelta(hours=6), body="one\ntwo\nthree\nfour\nfive\n"
    )
    _git(repo, "branch", "hotfix")
    await service.scan_repository(owner=owner, repository_id=identifier)
    third = _event_counts(await _events(db_session, owner.id))

    assert third == {
        ActivityEvent.REPOSITORY_REGISTERED.value: 1,
        ActivityEvent.REPOSITORY_SCANNED.value: 3,
        ActivityEvent.BRANCH_CREATED.value: 3,
        ActivityEvent.BRANCH_CHANGED.value: 1,
        ActivityEvent.COMMIT_DETECTED.value: 3,
    }
    stored = await _repository(db_session, identifier)
    assert stored["commit_count"] == 3
    assert stored["branch_count"] == 3


async def test_a_work_tree_that_lost_its_git_directory_scans_as_an_error_row_not_an_exception(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """A broken work tree becomes a sentence; nothing raises and nothing is lost.

    The ``.git`` entry is deleted while the directory survives, which is the
    shape of a half-removed checkout or a ``git init`` that was rolled back. The
    scan catches it, stores a ``status='error'`` run, and returns that run — so
    the caller gets a row rather than a 500.

    Four separate claims are asserted, because a scan service can fail any of them
    independently:

    * the **sentence** names the directory and is one line, and carries no
      traceback — git's diagnostics are written for a terminal, and one is not a
      user-facing response;
    * the **repository row** says so too, so the repository list can explain
      itself without anybody re-running the scan;
    * the **earlier history survives**: two commits and two branches are still
      stored, because a failed scan observed nothing and has no reason to forget;
    * the **counters do not move**, because only a successful scan writes them.
    """
    owner = await _owner(db_session)
    service, repository_id = await _registered_repository(db_session, tmp_path, owner)
    repo = tmp_path / "nexus-service"
    identifier = uuid.UUID(repository_id)
    now = await _db_now(db_session)
    _commit(repo, "app.py", "first", when=now - timedelta(days=3), body="one\ntwo\nthree\n")
    _commit(repo, "app.py", "second", when=now - timedelta(days=1), body="one\ntwo\nthree\nfour\n")
    _git(repo, "branch", "feature")
    await service.scan_repository(owner=owner, repository_id=identifier)
    assert (await _repository(db_session, identifier))["commit_count"] == 2

    _force_rmtree(repo / ".git")
    run = await service.scan_repository(owner=owner, repository_id=identifier)

    assert run.status == GitScanStatus.ERROR.value
    assert isinstance(run.error, str) and run.error
    assert "\n" not in run.error
    assert "Traceback" not in run.error
    assert "no .git entry was found in it" in run.error
    assert run.commits_discovered == 0
    assert run.commits_added == 0
    assert run.branches_discovered == 0

    stored = await _repository(db_session, identifier)
    assert stored["last_scan_status"] == GitScanStatus.ERROR.value
    assert stored["last_scan_error"] == run.error
    assert stored["commit_count"] == 2
    assert stored["branch_count"] == 2
    assert len(await _commits(db_session, owner.id)) == 2
    assert len(await _branches(db_session, owner.id, identifier)) == 2

    runs = await service.scan_history(owner=owner, repository_id=identifier)
    assert [row.status for row in runs] == [
        GitScanStatus.ERROR.value,
        GitScanStatus.OK.value,
    ]
    counts = _event_counts(await _events(db_session, owner.id))
    assert counts[ActivityEvent.REPOSITORY_SCANNED.value] == 2
    errors = [
        event
        for event in await _events(db_session, owner.id)
        if event["event_type"] == ActivityEvent.REPOSITORY_SCANNED.value
    ]
    assert (
        sum(1 for event in errors if event["metadata"]["status"] == GitScanStatus.ERROR.value) == 1
    )


# ---------------------------------------------------------------------------
# (d) Windows, active days and the activity series
# ---------------------------------------------------------------------------


async def test_a_commit_window_is_inclusive_at_the_start_and_exclusive_at_the_end(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """A commit landing on ``since`` is in; a commit landing on ``until`` is out.

    The window is half-open, and the boundary is the one that matters: for a
    day-bucketed chart the boundary between two adjacent windows is midnight, and
    a closed interval would count that one commit in both — which is how
    ``active_days`` ends up reporting more days than the window has.

    Four commits are stored a second either side of each bound, so the expected
    ``total`` is 2 and the two excluded hashes are named explicitly rather than
    left to a count. The list route is used rather than the metrics module because
    it passes the caller's bounds straight to SQL, so the assertion does not
    depend on the instant the service reads its own clock at.
    """
    owner = await _owner(db_session)
    service, repository_id = await _registered_repository(db_session, tmp_path, owner)
    identifier = uuid.UUID(repository_id)
    base = await _db_now(db_session)
    lower = base - timedelta(days=10)
    upper = base - timedelta(days=5)
    moments = {
        "before": lower - timedelta(seconds=1),
        "at-lower": lower,
        "before-upper": upper - timedelta(seconds=1),
        "at-upper": upper,
    }
    await DeveloperRepository(db_session).upsert_commits(
        owner.id,
        identifier,
        [
            {
                "commit_hash": label,
                "short_hash": label,
                "committed_at": moment,
                "message": label,
                "additions": 1,
                "deletions": 0,
                "files_changed": 1,
                "branch": "main",
            }
            for label, moment in moments.items()
        ],
    )

    windowed = await service.commits(owner=owner, since=lower, until=upper)

    assert windowed.total == 2
    assert {row.commit_hash for row in windowed.items} == {"at-lower", "before-upper"}


async def test_the_eight_metrics_measure_one_window_and_say_why_they_decline(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Five commits over four days give eight exact figures, one of them a ratio.

    Every figure is derived from the fixture table in :data:`_COMMIT_FIXTURE` and
    from ``WINDOW = 30``:

    * ``commit_activity`` **5** — five commits inside the 30-day window;
    * ``repository_activity`` **1** — all five belong to one repository;
    * ``change_volume`` **34** — ``(10+4) + (7+1) + 0 + (9+3)``, additions and
      deletions *summed* rather than netted, so ``26 added and 8 deleted``;
    * ``active_days`` **4** — days 1, 2, 5 and 12. A and B share day 1, so five
      commits land on four dates, and that is exactly the distinction between
      this metric and ``commit_activity``;
    * ``consistency`` **0.1333** — ``4/30``, rounded to four places. A ratio
      between 0 and 1, never a percentage and never a statement about habit;
    * ``repository_growth`` **2** — ``main`` and ``feature/alpha``, whose
      earliest *recorded* commits (day 12 and day 1) both fall inside the
      window. Whole-history earliest, not the in-window minimum: the in-window
      minimum would report every active branch as brand new and turn this metric
      into a restatement of ``commit_activity``;
    * ``maintenance_activity`` **4 of 5** — every commit that touched at least
      one file (A, B, C, E); D touched none. The figure is an upper bound
      measured without the preceding 90 days of per-file history, which is why
      the explanation says so instead of claiming those files were stale;
    * ``recent_momentum`` **4.0** — commits in the last 7 days are A, B, C and D
      (offsets 1, 1, 2 and 5) and the seven days before hold only E (offset 12),
      so ``4/1``. Its window is anchored on the database clock rather than on
      the requested one, so a 30-day summary still answers about the trailing
      fortnight.

    The declarations are the part worth reading: every metric carries a figure in
    its explanation, every one declares the window it measured, and every one is
    available with no reason attached. The decline is asserted separately, on an
    account that genuinely has nothing to divide.
    """
    owner = await _owner(db_session)
    service, repository_id = await _registered_repository(db_session, tmp_path, owner)
    identifier = uuid.UUID(repository_id)
    await _seed_commits(db_session, owner_id=owner.id, repository_id=identifier)

    metrics = await service.metrics(owner=owner, window_days=WINDOW)
    by_key = {metric.key: metric for metric in metrics}

    assert tuple(metric.key for metric in metrics) == tuple(key.value for key in DEVELOPER_METRICS)
    assert by_key["commit_activity"].value == 5.0
    assert by_key["commit_activity"].explanation == (
        "5 commit(s) were recorded in the 30-day window."
    )
    assert by_key["repository_activity"].value == 1.0
    assert by_key["change_volume"].value == 34.0
    assert by_key["change_volume"].explanation == (
        "34 changed line(s) were recorded in the 30-day window: 26 added and 8 deleted."
    )
    assert by_key["active_days"].value == 4.0
    assert by_key["consistency"].value == 0.1333
    assert by_key["consistency"].unit == "ratio"
    assert by_key["repository_growth"].value == 2.0
    assert by_key["maintenance_activity"].value == 4.0
    assert "4 of the 5 commit(s)" in by_key["maintenance_activity"].explanation
    assert by_key["recent_momentum"].value == 4.0
    assert by_key["recent_momentum"].explanation == (
        "4 commit(s) were recorded in the last 7 days against 1 in the 7 days before, "
        "a ratio of 4.0."
    )
    assert all(metric.available for metric in metrics)
    assert all(metric.reason_if_unavailable is None for metric in metrics)
    # Seven of the eight describe the requested window. Momentum is the exception
    # and declares 14, because its two halves are seven days each and comparing
    # unequal windows would measure the difference in window length as much as the
    # difference in activity.
    assert {metric.key: metric.window_days for metric in metrics} == {
        "commit_activity": WINDOW,
        "repository_activity": WINDOW,
        "change_volume": WINDOW,
        "active_days": WINDOW,
        "consistency": WINDOW,
        "repository_growth": WINDOW,
        "maintenance_activity": WINDOW,
        "recent_momentum": 14,
    }
    assert all(any(character.isdigit() for character in metric.explanation) for metric in metrics)


async def test_a_measured_zero_is_available_and_an_unmeasurable_ratio_is_not(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """An account with no commits: seven measured zeros and one honest decline.

    This is the pair the contracts care about most, and the two halves are very
    different objects.

    The seven **measured** metrics report ``value=0.0`` with ``available=True``
    and no reason. "No commits were recorded in this window" is a true sentence
    and a useful one — it is the answer for someone who registered a repository
    yesterday and has not committed to it yet.

    ``recent_momentum`` reports ``value=None``, ``available=False`` and the shared
    reason. It divides commits in the last seven days by the commits in the seven
    before, and on an account with no commits that denominator is zero: ``0/0``
    would be a number about a comparison nobody can make, and ``N/0`` would be
    infinity, which a chart would render as infinite growth. Either would be an
    invention.

    A metric that was silently omitted would fail the last assertion instead —
    exactly eight keys, always, because a client indexing by key would otherwise
    render a hole where a card belongs.
    """
    owner = await _owner(db_session)
    service, _repository_id = await _registered_repository(db_session, tmp_path, owner)

    metrics = await service.metrics(owner=owner, window_days=WINDOW)
    by_key = {metric.key: metric for metric in metrics}

    assert len(metrics) == 8
    assert by_key["commit_activity"].value == 0.0
    assert by_key["commit_activity"].available is True
    assert by_key["commit_activity"].reason_if_unavailable is None
    assert by_key["repository_activity"].value == 0.0
    assert by_key["change_volume"].value == 0.0
    assert by_key["active_days"].value == 0.0
    assert by_key["consistency"].value == 0.0
    assert by_key["repository_growth"].value == 0.0
    assert by_key["maintenance_activity"].value == 0.0

    momentum = by_key["recent_momentum"]
    assert momentum.available is False
    assert momentum.value is None
    assert momentum.reason_if_unavailable == NOT_ENOUGH_DATA
    assert momentum.window_days == 14


async def test_the_activity_series_zero_fills_every_day_in_the_window(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Two commits five days apart in a five-day window produce a dense, gap-free series.

    The zero-fill is the property, and it is asserted structurally rather than by
    counting buckets: the buckets are ascending, adjacent exactly one day apart,
    and the run covers every UTC date from the window's first day to its last. A
    chart built only from the days that carry commits would skip the quiet ones,
    and a reader counting the bars would see two active days and read them as
    consecutive.

    The count is asserted as a difference rather than as a length, because a
    five-day window opening at 23:00 buckets differently from one opening at
    midnight: ``labels[-1] - labels[0]`` is five whole days either way, and that
    is the claim being made — every day between the first and the last is present.

    Weekly granularity is checked on the same fixture: every bucket starts on a
    Monday, because a week starting on Sunday makes "last week" mean two different
    things depending on who is reading it.
    """
    owner = await _owner(db_session)
    service, repository_id = await _registered_repository(db_session, tmp_path, owner)
    identifier = uuid.UUID(repository_id)
    now = await _db_now(db_session)
    await DeveloperRepository(db_session).upsert_commits(
        owner.id,
        identifier,
        [
            {
                "commit_hash": f"day-{offset}",
                "short_hash": f"day-{offset}",
                "committed_at": now - timedelta(days=offset),
                "message": f"commit on day {offset}",
                "additions": 5,
                "deletions": 1,
                "files_changed": 1,
                "branch": "main",
            }
            for offset in (1, 4)
        ],
    )

    series = await service.read_activity(owner=owner, window_days=5, granularity="day")

    assert series.granularity == "day"
    assert series.window_days == 5
    assert series.total_commits == 2
    labels = [bucket.bucket_start.date() for bucket in series.buckets]
    assert labels == sorted(labels)
    # ``astimezone(UTC)`` on the window edges, because they arrive labelled with the
    # *connection's* ``TimeZone`` while ``bucket_start`` was floored in UTC by the
    # service. On this server the two labels are ``Asia/Calcutta`` and ``UTC`` for
    # the very same instants, so a bare ``.date()`` would read a local day against a
    # UTC one and put the window's first day a day out of step with its own first
    # bucket for the five and a half hours a day the two calendars disagree.
    assert labels[0] == series.window_start.astimezone(UTC).date()
    assert labels[-1] == series.window_end.astimezone(UTC).date()
    assert (labels[-1] - labels[0]).days == 5
    assert all(later - earlier == timedelta(days=1) for earlier, later in pairwise(labels))
    assert [bucket.commits for bucket in series.buckets if bucket.commits] == [1, 1]
    assert len(series.buckets) >= 5
    assert all(bucket.commits in (0, 1) for bucket in series.buckets)
    busy = next(bucket for bucket in series.buckets if bucket.commits)
    assert (busy.additions, busy.deletions, busy.files_changed, busy.repository_count) == (
        5,
        1,
        1,
        1,
    )

    weekly = await service.read_activity(owner=owner, window_days=30, granularity="week")
    assert weekly.granularity == "week"
    assert all(bucket.bucket_start.weekday() == 0 for bucket in weekly.buckets)
    assert weekly.total_commits == 2


# ---------------------------------------------------------------------------
# (e) The ML-ready feature vector
# ---------------------------------------------------------------------------


async def test_the_feature_vector_is_null_not_zero_for_a_repository_with_no_commits(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """A scanned-but-empty repository reports ``null`` age and ``null`` inactivity.

    Zero would assert "committed today", and inside a training matrix a fabricated
    zero is indistinguishable from an observed one once a later trainer has
    consumed it. So the two nullable features are null, and the *counts* beside
    them are genuine measured zeros — a different answer about a different
    question, and the two are asserted separately so the test cannot pass with the
    nullable values quietly coerced to 0.

    The repository that counts as "empty" here is one that **was scanned** and
    found nothing. A repository that was registered and never scanned is a
    different fact — nobody has looked — and it is given no row at all rather
    than a row of the same seven zeros, because a trainer reading that row would
    conclude the developer committed nothing rather than that NEXUS never
    measured. Both are asserted here: the scanned-and-empty repository keeps real
    zeros, and the never-scanned one is absent.

    The account-level row is the control. The third repository does have commits,
    so the account row's age and inactivity are integers, and
    ``project_association`` is false because no repository was linked to a
    project. The vector is stamped ``developer_features.v1`` because that string
    is the contract with whatever trains on it later: a v2 must not be able to
    typecheck against v1's column meanings.

    Nothing here is a model. No prediction, no probability, no fitted parameter.
    """
    owner = await _owner(db_session)
    service, unscanned_id = await _registered_repository(
        db_session, tmp_path, owner, name="never-scanned"
    )
    empty_repo = _init(tmp_path, "scanned-and-empty")
    empty = await service.register_repository(owner=owner, local_path=str(empty_repo))
    await service.scan_repository(owner=owner, repository_id=empty.id)
    used_repo = _init(tmp_path, "with-history")
    used = await service.register_repository(owner=owner, local_path=str(used_repo))
    now = await _db_now(db_session)
    _commit(used_repo, "app.py", "first", when=now - timedelta(days=4), body="one\ntwo\n")
    await service.scan_repository(owner=owner, repository_id=used.id)

    vector = await service.features(owner=owner, window_days=WINDOW)

    assert vector.schema_version == "developer_features.v1"
    assert vector.window_days == WINDOW
    assert vector.generated_at is not None
    by_id = {row.repository_id: row for row in vector.repositories}
    assert set(by_id) == {empty.id, used.id}
    assert uuid.UUID(unscanned_id) not in by_id, (
        "a repository nobody has scanned has no figures, and a row of zeros for "
        "it would be a claim about the developer rather than about the scan"
    )

    silent = by_id[empty.id]
    assert silent.commits_last_7d == 0
    assert silent.commits_last_30d == 0
    assert silent.active_days_7d == 0
    assert silent.active_days_30d == 0
    assert silent.files_changed_7d == 0
    assert silent.additions_7d == 0
    assert silent.deletions_7d == 0
    assert silent.repository_age_days is None
    assert silent.inactivity_days is None
    assert silent.commit_frequency == 0.0
    assert silent.project_association is False

    busy = by_id[used.id]
    assert busy.commits_last_7d == 1
    assert busy.commits_last_30d == 1
    assert busy.additions_7d == 2
    assert isinstance(busy.repository_age_days, int) and busy.repository_age_days >= 4
    assert isinstance(busy.inactivity_days, int) and busy.inactivity_days >= 0

    # The account row aggregates both repositories, and its age comes from the
    # recorded history rather than from a registration date.
    assert vector.features.commits_last_30d == 1
    assert isinstance(vector.features.repository_age_days, int)
    assert isinstance(vector.features.inactivity_days, int)
    assert vector.features.project_association is False
