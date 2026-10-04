"""Phase 8 service: register local repositories, read them, and report the facts.

What this module is for
-----------------------
:mod:`app.services.developer.git` knows how to run ``git``; :mod:`app.services.developer.metrics`
knows how to turn rows into numbers. Neither knows about a database, an account
or a request. This module is the seam: it owns the path from "this user typed a
directory" to "here is a ``GitRepositoryRead``", and everything in between —
ownership, validation, idempotent writes, event emission and wire assembly.

Three rules shape it, and each one has an obvious cheap alternative that is wrong.

**A broken repository must never break NEXUS.** :meth:`DeveloperIntelligenceService.scan_repository`
catches every failure the git engine can raise and writes a ``GitScanRun`` with
``status='error'`` and a human sentence. It returns that row. There is no code
path here where a deleted directory, a corrupt ``.git`` or an unreachable network
share becomes a 500, because the alternative — letting the exception out — is one
unlucky ``POST /scan`` away from taking the page down. ``OSError`` is caught
alongside the engine's own failures for the same reason: a directory removed
between registration and scan surfaces as an OS error, not as a
``GitRepositoryError``.

**A re-scan is a no-op, not an append.** The git CLI returns the same hashes for
an unchanged repository, so every write here is an upsert keyed on the schema's
unique constraints. ``commits_added`` on a re-scan is 0 and that is the figure
that proves it, which is why the scan run records the inserted and discovered
counts separately rather than one "commits seen" number.

**Absence is not zero, on either side of the wire.** A metric that could not be
computed becomes ``available=False`` with a reason and a null ``value``; a
repository with no commits gets ``repository_age_days = None`` rather than 0,
because 0 would assert it was created today. Both directions matter, and the
second one is inside a feature vector where a fabricated zero is
indistinguishable from an observed one once it reaches a trainer. The sharpest
case is a repository that has **never been scanned**: publishing a row of zeros
for it would tell a model "this developer committed nothing", when the truth is
"we have not looked". The feature vector omits those repositories entirely
(:func:`_has_measurement`) — the schema has no ``available`` column to mark them
with, and a row that exists is a row that was measured.

Why the event feed is written this way
--------------------------------------
A scan emits **one** ``REPOSITORY_SCANNED`` row, on success and on failure
alike — the attempt is a fact whether or not it worked, and the metadata
carries the status and the sentence. It does *not* emit a row per commit: a
first scan of a repository with two thousand commits would write two thousand
history entries, and :data:`_MAX_COMMIT_EVENTS` bounds the rest. ``COMMIT_DETECTED``
is reserved for commits *after* the stored ``latest_commit_at``, which is the
repository's high-water mark and therefore exactly "commits the previous scan had
not seen". ``BRANCH_CREATED`` and ``BRANCH_CHANGED`` are emitted only when the
branch listing read before the write was complete, because a partial page cannot
tell a new branch from a page boundary — inventing a branch that was already
there is exactly the factual error this phase exists to avoid.

The three inputs the metrics cannot derive for themselves
--------------------------------------------------------
:func:`app.services.developer.metrics.build_metrics` takes three things only the
service knows: which branches were *first* recorded inside the window (from the
whole-history ``min(committed_at)`` per branch, compared against the window — not
from the in-window minimum, which would call every active branch new), what the
window's end instant is (the database clock, never ``datetime.now``), and
whether per-file history exists at all. That last one is a real limitation rather
than an oversight; see :data:`_UNRECORDED_FILE_PATH`.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger, log_event
from app.models.developer import GitCommit, GitRepository
from app.models.enums import ActivityEvent, GitScanStatus
from app.repositories.developer import DeveloperRepository
from app.repositories.project import ProjectRepository
from app.schemas.developer import (
    FEATURE_SCHEMA_VERSION,
    BranchListRead,
    BranchRead,
    CommitListRead,
    CommitRead,
    DeveloperActivityBucketRead,
    DeveloperActivityRead,
    DeveloperFeatureValues,
    DeveloperFeatureVectorRead,
    DeveloperMetricRead,
    DeveloperSummaryRead,
    ProjectDeveloperRead,
    RepositoryFeatureVectorRead,
    RepositoryListRead,
    RepositoryRead,
    ScanRunRead,
)
from app.services.activity_service import ActivityService
from app.services.developer.git import (
    GitError,
    GitRepositoryError,
    RepositorySnapshot,
    parse_path_allowlist,
    read_repository,
    sanitize_git_message,
    validate_repository_path,
)
from app.services.developer.metrics import (
    ACTIVITY_GRANULARITIES,
    MOMENTUM_COMPARISON_DAYS,
    ActivityGranularity,
    DeveloperMetric,
    MetricSample,
    activity_series,
    bucket_for,
    build_metrics,
)

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.user import User

__all__ = ["DeveloperIntelligenceService"]

logger = get_logger(__name__)

#: The page size a repository list gets when a caller names none. Matches
#: :mod:`app.repositories.developer`'s own default so the service's fallback and
#: the repository's are the same number rather than two that can drift.
_DEFAULT_PAGE_SIZE = 50
#: The page size used for internal "read everything this account owns" sweeps.
#: The repository caps any page at 200 and ``developer_max_repositories`` defaults
#: to 100, so this reads the whole set on a default deployment. A deployment that
#: raises the repository cap above 200 would see the summary and the feature
#: vector cover only the first 200 repositories; raising this constant above the
#: repository's own ceiling would not help, because the repository clamps.
_OWNERSHIP_PAGE_SIZE = 200
#: How many ``COMMIT_DETECTED`` rows one scan may append. A first scan of a mature
#: repository discovers thousands of commits; the feed exists so a person can see
#: what happened, and a scan that writes two thousand rows is a denial of service
#: against the feed rather than a record of one.
_MAX_COMMIT_EVENTS = 20
#: How many ``BRANCH_CREATED`` / ``BRANCH_CHANGED`` rows one scan may append.
#: Repositories with more branches than this have a branch listing, not a feed.
_MAX_BRANCH_EVENTS = 20
#: The window a repository's own history is projected over when the feature
#: vector is extracted: 7 and 30 days are the schema's fixed column names and are
#: not derived from the requested window.
_FEATURE_WEEK_DAYS = 7
_FEATURE_MONTH_DAYS = 30

_REPOSITORY_NOT_FOUND = "That repository was not found."
_PROJECT_NOT_FOUND = "That project was not found."

#: The placeholder path a commit's unstored file list collapses to.
#:
#: Phase 8 stores ``files_changed`` as a count and deliberately stores **no
#: per-file rows** (see the module docstring of :mod:`app.models.developer`), so
#: ``MetricSample.file_paths`` cannot be filled with real paths. Leaving it empty
#: would make ``metrics.maintenance_activity`` count **zero commits forever** —
#: a measured zero that is simply false, which is the one outcome this phase may
#: never produce. So a commit that touched at least one file reports this
#: placeholder, and the metric is always built with ``last_touched_before=None``
#: so that it renders the caveat in its own user-facing sentence: the figure is
#: an upper bound measured without the preceding ninety days of file history, and
#: not a claim that those files were all stale.
_UNRECORDED_FILE_PATH = "<file names are not stored per commit>"


def _as_utc(value: datetime | None) -> datetime | None:
    """Read a naive instant as UTC; leave an aware one alone.

    The same rule :mod:`app.repositories.developer` applies before writing a
    ``timestamptz``. A commit at 23:30 UTC would otherwise bucket onto the
    previous day on a connection configured for another zone, which moves
    ``active_days`` — and ``active_days`` is one of the eight numbers this phase
    promises to be reproducible.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _elapsed_ms(started: float) -> int:
    """Milliseconds since ``perf_counter()`` was sampled, never negative."""
    return max(0, int((perf_counter() - started) * 1000))


def _metric_read(metric: DeveloperMetric) -> DeveloperMetricRead:
    """Map a pure :class:`DeveloperMetric` onto the wire shape.

    The one place ``value`` becomes nullable, and the conversion is deliberately
    conditional on ``available``: an unavailable metric carries a meaningless
    ``0.0`` internally because the dataclass requires a float, and a client that
    renders it would be showing a fabricated zero. A *measured* zero keeps both
    its value and its ``available=True``, because "no commits were recorded in
    this window" is a true sentence and must survive the trip.
    """
    return DeveloperMetricRead(
        key=metric.key,
        label=metric.label,
        value=metric.value if metric.available else None,
        unit=metric.unit,
        definition=metric.definition,
        window_days=metric.window_days,
        source=metric.source,
        explanation=metric.explanation,
        available=metric.available,
        reason_if_unavailable=metric.reason_if_unavailable,
    )


class DeveloperIntelligenceService:
    """Register local git repositories, scan them, and report what they recorded.

    One instance per request, built from repositories rather than from a session,
    exactly like :class:`~app.services.risk.detection.RiskDetectionService`. It
    holds no state between calls, so two concurrent scans of the same account are
    both correct: the upsert arbitrates through the schema's unique constraints
    rather than through anything this class remembers.
    """

    def __init__(
        self,
        repositories: DeveloperRepository,
        projects: ProjectRepository,
        activity: ActivityService | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Wire the service.

        Args:
            repositories: Phase 8 persistence. Everything the service reads or
                writes about repositories, commits, branches and scan runs goes
                through it, so every statement is owner-scoped in one place.
            projects: Project persistence, used for two things only: proving a
                ``project_id`` belongs to the caller before it is written onto a
                repository row, and reading the name a project view carries.
            activity: The history sink. ``None`` runs every lifecycle rule with
                nowhere to record them, which is a real mode for exercising the
                service against a hand-built snapshot and not a mode the API
                layer uses — see :func:`app.api.deps.get_activity_service`.
            settings: Resolved from the environment when not supplied. Supplies
                the repository cap, the scan's commit ceiling and the window
                bounds this phase reads.
        """
        self.repositories = repositories
        self.projects = projects
        self.activity = activity
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------
    # Registration and metadata
    # ------------------------------------------------------------------

    async def register_repository(
        self,
        *,
        owner: User,
        local_path: str,
        name: str | None = None,
        description: str | None = None,
        project_id: uuid.UUID | None = None,
        is_active: bool = True,
    ) -> RepositoryRead:
        """Validate a path, prove it is a work tree, and store it.

        **Validation happens before the write, not after it.** A row that points
        at a directory that is not a repository would fail on every future scan
        and would already be on the dashboard by the time anyone found out, so
        :func:`validate_repository_path` resolves the path, proves a ``.git``
        entry exists and — when ``developer_path_allowlist`` is configured —
        proves the resolved path is under one of the roots. The *resolved*
        absolute path is what gets stored, so a relative path cannot quietly
        resolve somewhere else once a different working directory runs the scan.

        A bare ``git init`` with no commits is a valid registration. It is the
        very first thing a user does with this feature and refusing it would tell
        them their new project does not exist.

        Args:
            owner: The account registering it. Every subsequent read of this row
                is scoped to them in a predicate rather than a post-filter.
            local_path: The directory holding the work tree. May be absolute,
                relative or ``~``-prefixed; it is expanded and resolved here.
            name: A label for it. Defaults to the directory name, which is a
                fact about the path rather than a machine inference about the
                user. It is never re-derived afterwards.
            description: An optional note.
            project_id: Optional project to link it to. Another account's
                project is *not found*, never a permission error.
            is_active: Whether it appears on the dashboard immediately.

        Returns:
            The stored repository, with its server defaults filled in.

        Raises:
            ValidationError: If the path is not a readable git work tree, is
                outside the configured allowlist, or names no project. The
                message is the git engine's own human sentence, so no row is
                ever created that fails on every future scan.
            ConflictError: If this account has already registered the path, or
                has reached ``developer_max_repositories``.
        """
        # The engine raises ``GitRepositoryError`` for a path that is not a work
        # tree. It is a bare ``Exception`` subclass with no registered handler,
        # so letting it escape would reach the client as an unhandled 500 — the
        # opposite of what this method promises, and of the rule that a bad
        # repository never breaks NEXUS. Re-raise it as the 422 the docstring
        # describes, carrying the engine's own human sentence.
        try:
            resolved = validate_repository_path(
                local_path,
                allowlist=self._path_allowlist(),
            )
        except GitRepositoryError as error:
            raise ValidationError(str(error)) from error
        stored_path = str(resolved)
        if project_id is not None and (
            await self.projects.get_by_id_for_user(project_id, owner.id) is None
        ):
            raise NotFoundError(_PROJECT_NOT_FOUND)

        registered = await self.repositories.count_repositories(owner.id)
        if registered >= self.settings.developer_max_repositories:
            raise ConflictError(
                f"This account already has the maximum of "
                f"{self.settings.developer_max_repositories} repositories registered."
            )
        if await self.repositories.get_repository_by_path(owner.id, stored_path) is not None:
            raise ConflictError("That repository is already registered for this account.")

        repository = await self.repositories.create_repository(
            owner.id,
            name=name or resolved.name or stored_path,
            local_path=stored_path,
            description=description,
            project_id=project_id,
        )
        if not is_active:
            repository = (
                await self.repositories.update_repository(
                    owner.id, repository.id, {"is_active": False}
                )
                or repository
            )

        await self._record_event(
            ActivityEvent.REPOSITORY_REGISTERED,
            owner=owner,
            project_id=project_id,
            metadata={"repository_id": str(repository.id)},
        )
        return RepositoryRead.model_validate(repository)

    async def list_repositories(
        self,
        *,
        owner: User,
        project_id: uuid.UUID | None = None,
        is_active: bool | None = None,
        limit: int = _DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> RepositoryListRead:
        """One page of this account's repositories, with the active split beside it.

        The active and inactive counts come from the repository rather than from
        the page, because a browser-side tally can only count the rows it happens
        to hold and would understate a band that continues onto page two.

        Args:
            owner: Whose repositories to list.
            project_id: Narrow to the repositories linked to one project.
            is_active: Narrow to active or inactive ones; ``None`` means both.
            limit: Page size, clamped by the repository.
            offset: Rows to skip, floored at zero.

        Returns:
            The page, its total, and the active/inactive split across every
            matching row.
        """
        rows, total = await self.repositories.list_repositories(
            owner.id,
            project_id=project_id,
            is_active=is_active,
            limit=limit,
            offset=offset,
        )
        if project_id is None and is_active is None:
            # Only the unfiltered listing has a meaningful split to report; with a
            # filter in play one of the two halves is always zero and the client
            # already knows which filter it sent.
            active = await self.repositories.count_repositories(owner.id, is_active=True)
        else:
            active = sum(1 for row in rows if row.is_active) if total == len(rows) else 0
        return RepositoryListRead(
            items=[RepositoryRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=max(0, offset),
            active_count=active,
            inactive_count=max(0, total - active),
        )

    async def get_repository(self, *, owner: User, repository_id: uuid.UUID) -> RepositoryRead:
        """One repository, or a 404 that does not confirm whose it was.

        Another account's repository is answered with the same ``NotFoundError``
        as an id nobody has ever issued. A 403 here would confirm the id exists
        and turn the endpoint into a probe for which repository ids are real.

        Args:
            owner: The caller.
            repository_id: The repository to read.

        Returns:
            The repository row as the wire shape.

        Raises:
            NotFoundError: If no row with that id belongs to this account.
        """
        return RepositoryRead.model_validate(
            await self._owned_repository(owner=owner, repository_id=repository_id)
        )

    async def update_repository(
        self, *, owner: User, repository_id: uuid.UUID, values: Mapping[str, Any]
    ) -> RepositoryRead:
        """Edit a repository's metadata. Never its path.

        The caller passes **only the fields the user actually set** — the route
        builds that mapping with ``model_dump(exclude_unset=True)`` — because
        ``None`` means "write SQL NULL" here and would otherwise clear a name the
        user never mentioned.

        ``local_path`` is refused by the repository's write set, which is the
        design rather than an omission: moving a repository would leave its
        counters, its commit range and its recorded history describing a
        directory this account never scanned, and nothing in the schema could
        notice.

        Args:
            owner: The caller.
            repository_id: The repository to edit.
            values: Column name to new value, restricted to the editable set.

        Returns:
            The updated repository.

        Raises:
            ValidationError: If ``values`` is empty.
            NotFoundError: If the repository is not this account's, or a supplied
                ``project_id`` belongs to somebody else.
        """
        if not values:
            raise ValidationError("A repository edit must change at least one field.")
        project_id = values.get("project_id")
        if project_id is not None and (
            await self.projects.get_by_id_for_user(project_id, owner.id) is None
        ):
            raise NotFoundError(_PROJECT_NOT_FOUND)

        repository = await self.repositories.update_repository(owner.id, repository_id, values)
        if repository is None:
            raise NotFoundError(_REPOSITORY_NOT_FOUND)

        await self._record_event(
            ActivityEvent.REPOSITORY_UPDATED,
            owner=owner,
            project_id=repository.project_id,
            metadata={"repository_id": str(repository.id)},
        )
        return RepositoryRead.model_validate(repository)

    async def delete_repository(self, *, owner: User, repository_id: uuid.UUID) -> None:
        """Remove a repository and everything observed under it.

        The commits, branches and scan runs go with it through the schema's
        ``ON DELETE CASCADE``. They are not merely deactivated: a row that keeps
        its history while claiming the repository no longer exists would leave
        the account's metrics reading commits from a work tree the user has
        explicitly removed.

        Args:
            owner: The caller.
            repository_id: The repository to remove.

        Raises:
            NotFoundError: If no row with that id belongs to this account —
                identically to an id nobody has issued.
        """
        repository = await self._owned_repository(owner=owner, repository_id=repository_id)
        if not await self.repositories.delete_repository(owner.id, repository.id):
            raise NotFoundError(_REPOSITORY_NOT_FOUND)

        await self._record_event(
            ActivityEvent.REPOSITORY_REMOVED,
            owner=owner,
            project_id=repository.project_id,
            metadata={"repository_id": str(repository_id)},
        )

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    async def scan_repository(
        self, *, owner: User, repository_id: uuid.UUID, full: bool = False
    ) -> ScanRunRead:
        """Read one repository off disk and store what it found.

        **Synchronous, and that is the design.** A scan is a dozen bounded git
        invocations against a local directory; there is no background scheduler in
        NEXUS and Phase 8 does not add one. The route returns this row with 200
        whether the scan worked or not.

        The write is incremental: ``since`` is the repository's stored
        ``latest_commit_at``, so a re-scan of an unchanged repository transfers
        nothing and reports ``commits_added=0``. That high-water mark is also why
        :data:`~app.services.developer.git.MAX_COMMITS_PER_SCAN` truncates a
        repository's history permanently rather than a scan at a time — the
        commits past the ceiling are older than the mark, so nothing asks for
        them again.

        **The configured allowlist is enforced on the scan, not only at
        registration.** The path scanned here is a *stored* path read back days
        later, and a stored string is not a promise about what the path resolves
        to today: delete the registered directory and put a symlink in its place
        and the same string now resolves somewhere else entirely. Registration
        proved the path was permitted on the day it was written; only a check
        against the path as it resolves now proves it is still permitted. The
        refusal is a sentence that becomes an ``error`` scan run, like any other
        unreadable repository — never a traceback.

        Args:
            owner: The caller. Also the owner recorded on every commit, branch,
                scan run and event this method writes.
            repository_id: The repository to read.
            full: Read the whole history rather than only what landed since the
                last scan. Needed after a rewrite of history, where the stored
                high-water mark points at a commit that no longer exists.

        Returns:
            The ``GitScanRun`` describing the attempt. On failure it carries
            ``status='error'`` and a human sentence — never a traceback.

        **A failed scan is logged as well as stored.** ``git_scan_failed``
        records the repository, the exception *type* and the duration. The row
        written here is the answer a user sees on one repository; the log line is
        the answer an operator needs when every scan on a deployment is failing
        at once, which is the case the repository list cannot surface.

        Raises:
            NotFoundError: If the repository is not this account's.
        """
        repository = await self._owned_repository(owner=owner, repository_id=repository_id)
        known_branches = await self._known_branch_heads(owner.id, repository.id)
        started = perf_counter()
        try:
            snapshot = await read_repository(
                repository.local_path,
                since=None if full else _as_utc(repository.latest_commit_at),
                limit=self.settings.developer_max_commits_per_scan,
                allowlist=self._path_allowlist(),
            )
        except (GitError, OSError) as exc:
            log_event(
                logger,
                logging.WARNING,
                "git_scan_failed",
                error=type(exc).__name__,
                owner_id=str(owner.id),
                repository_id=str(repository.id),
                duration_ms=_elapsed_ms(started),
            )
            return await self._failed_scan(
                owner=owner, repository=repository, message=str(exc), started=started
            )
        return await self._store_snapshot(
            owner=owner,
            repository=repository,
            snapshot=snapshot,
            known_branches=known_branches,
            started=started,
        )

    async def scan_history(
        self, *, owner: User, repository_id: uuid.UUID, limit: int = 10
    ) -> tuple[ScanRunRead, ...]:
        """A repository's recent scan attempts, newest first.

        The history is the point: a repository whose directory moved shows a run
        of errors followed by the run that succeeded, and "this was unreadable
        until Tuesday" is a different statement from "this is unreadable".

        Args:
            owner: The caller.
            repository_id: The repository whose attempts to read.
            limit: Ceiling on rows returned.

        Returns:
            The runs, newest first.

        Raises:
            NotFoundError: If the repository is not this account's.
        """
        repository = await self._owned_repository(owner=owner, repository_id=repository_id)
        runs = await self.repositories.list_scan_runs(
            owner.id, repository_id=repository.id, limit=limit
        )
        return tuple(ScanRunRead.model_validate(run) for run in runs)

    async def _failed_scan(
        self, *, owner: User, repository: GitRepository, message: str, started: float
    ) -> ScanRunRead:
        """Turn a failed scan into two rows and a 200.

        The repository row carries ``last_scan_status='error'`` and the sentence,
        so the repository list can say what is wrong without anybody re-running
        the scan; the ``GitScanRun`` carries the same sentence with the duration,
        so the history can show when it started working again.

        Args:
            owner: The caller, and the owner of every row written.
            repository: The repository whose scan failed.
            message: The raw failure text. Sanitised here, because git's stderr
                quotes absolute paths and the server operator's home directory
                carries their username.
            started: The ``perf_counter()`` sample taken before the CLI ran.

        Returns:
            The stored error run.
        """
        sentence = sanitize_git_message(message) or "The repository could not be read."
        await self.repositories.update_scan_state(
            owner.id,
            repository.id,
            {
                "last_scanned_at": await self._now(),
                "last_scan_status": GitScanStatus.ERROR.value,
                "last_scan_error": sentence,
            },
        )
        run = await self.repositories.record_scan_run(
            owner.id,
            repository.id,
            status=GitScanStatus.ERROR,
            error=sentence,
            duration_ms=_elapsed_ms(started),
        )
        await self._record_event(
            ActivityEvent.REPOSITORY_SCANNED,
            owner=owner,
            project_id=repository.project_id,
            metadata={
                "repository_id": str(repository.id),
                "status": GitScanStatus.ERROR.value,
                "commits_added": 0,
                "branches_discovered": 0,
                "error": sentence,
            },
        )
        return ScanRunRead.model_validate(run)

    async def _store_snapshot(
        self,
        *,
        owner: User,
        repository: GitRepository,
        snapshot: RepositorySnapshot,
        known_branches: tuple[dict[str, str | None], bool],
        started: float,
    ) -> ScanRunRead:
        """Persist one successful scan and describe what was genuinely new.

        The order is deliberate: commits and branches are written before the
        repository's own counters move, so a reader that arrives mid-scan sees a
        repository whose counters still describe the *previous* scan rather than
        counters for commits that are not stored yet. The scan run is written
        last, because a row saying "this attempt finished" must never be visible
        before the work it describes.

        ``commit_count`` accumulates ``repository.commit_count + inserted`` rather
        than being set to what this scan returned. An incremental scan returns
        only the commits after the stored high-water mark, so assigning it
        directly would make the repository's commit count *fall* every time the
        user pressed the button on an active repository.

        Args:
            owner: The caller, and the owner of every row written.
            repository: The row this scan is about, as it was before the write.
            snapshot: Everything the git CLI read off disk.
            known_branches: ``({branch name: head hash}, complete)`` read before
                the upsert, so a new branch and a moved head can be told apart
                from an unchanged one.
            started: The ``perf_counter()`` sample taken before the CLI ran.

        Returns:
            The stored run.
        """
        owner_id = owner.id
        # Read the high-water mark BEFORE any write. ``update_scan_state`` is an
        # ``UPDATE ... RETURNING GitRepository`` with ``populate_existing=True``
        # against the same identity-mapped object this method was handed, so it
        # overwrites ``repository.latest_commit_at`` with the value this scan is
        # about to store. Reading it afterwards yields the newest commit's own
        # timestamp, and ``committed_at > high_water_mark`` can never hold — so
        # ``COMMIT_DETECTED`` would silently never be emitted at all.
        high_water_mark = _as_utc(repository.latest_commit_at)
        commits = await self.repositories.upsert_commits(
            owner_id,
            repository.id,
            [
                {
                    "commit_hash": commit.commit_hash,
                    "short_hash": commit.short_hash,
                    "committed_at": commit.committed_at,
                    "message": commit.message,
                    "author_name": commit.author_name,
                    "author_email": commit.author_email,
                    "additions": max(0, commit.additions),
                    "deletions": max(0, commit.deletions),
                    "files_changed": max(0, commit.files_changed),
                    "branch": commit.branch,
                }
                for commit in snapshot.commits
            ],
        )
        names = [branch.name for branch in snapshot.branches]
        await self.repositories.upsert_branches(
            owner_id,
            repository.id,
            [
                {
                    "name": branch.name,
                    "head_commit_hash": branch.head_commit_hash,
                    "is_current": branch.name == snapshot.current_branch,
                    "is_default": branch.name == snapshot.default_branch,
                    "last_committed_at": branch.last_committed_at,
                }
                for branch in snapshot.branches
            ],
        )
        await self.repositories.delete_branches_absent(owner_id, repository.id, names)

        await self.repositories.update_scan_state(
            owner_id,
            repository.id,
            {
                "branch_count": len(snapshot.branches),
                "commit_count": repository.commit_count + commits.inserted,
                "current_branch": snapshot.current_branch,
                "default_branch": snapshot.default_branch,
                "first_commit_at": _as_utc(snapshot.first_commit_at),
                "latest_commit_at": _as_utc(snapshot.latest_commit_at),
                "primary_language": snapshot.primary_language,
                "working_tree_dirty": snapshot.working_tree_dirty,
                "last_scanned_at": await self._now(),
                "last_scan_status": GitScanStatus.OK.value,
                # Cleared, not left alone: a stale error on a repository whose
                # last scan succeeded would claim a working repository is broken.
                "last_scan_error": None,
            },
        )
        run = await self.repositories.record_scan_run(
            owner_id,
            repository.id,
            status=GitScanStatus.OK,
            commits_discovered=commits.total,
            commits_added=commits.inserted,
            branches_discovered=len(snapshot.branches),
            duration_ms=_elapsed_ms(started),
        )

        await self._record_event(
            ActivityEvent.REPOSITORY_SCANNED,
            owner=owner,
            project_id=repository.project_id,
            metadata={
                "repository_id": str(repository.id),
                "status": GitScanStatus.OK.value,
                "commits_discovered": commits.total,
                "commits_added": commits.inserted,
                "branches_discovered": len(snapshot.branches),
                "duration_ms": _elapsed_ms(started),
            },
        )
        await self._record_scan_findings(
            owner=owner,
            repository=repository,
            snapshot=snapshot,
            high_water_mark=high_water_mark,
            known_branches=known_branches,
        )
        return ScanRunRead.model_validate(run)

    async def _record_scan_findings(
        self,
        *,
        owner: User,
        repository: GitRepository,
        snapshot: RepositorySnapshot,
        high_water_mark: datetime | None,
        known_branches: tuple[dict[str, str | None], bool],
    ) -> None:
        """Emit the events for what this scan found that the last one had not.

        **Only genuinely new facts.** ``COMMIT_DETECTED`` is reserved for commits
        strictly after the stored ``latest_commit_at``, which is the repository's
        high-water mark and therefore exactly the set the previous scan had not
        seen; ``BRANCH_CHANGED`` needs a head hash that actually moved. A re-scan
        of an unchanged repository emits ``REPOSITORY_SCANNED`` and nothing else.

        Branch events are skipped entirely when the pre-write listing was not
        complete: a repository with more branches than one page cannot be told
        apart from a new one by name, and emitting a "created" event for a branch
        that has existed for two years is precisely the factual error this phase
        exists to prevent.

        ``FILE_ACTIVITY_DETECTED`` reports uncommitted changes as a state of the
        working tree at scan time — a count of pending entries, never a claim
        about how long anyone had been working on them.

        Args:
            owner: The caller.
            repository: The repository that was scanned.
            snapshot: What the CLI read.
            high_water_mark: The stored ``latest_commit_at`` before this scan.
            known_branches: ``({name: head hash}, complete)`` read before the write.
        """
        if self.activity is None:
            return

        for commit in [
            commit
            for commit in snapshot.commits
            if high_water_mark is None or _as_utc(commit.committed_at) > high_water_mark
        ][:_MAX_COMMIT_EVENTS]:
            await self._record_event(
                ActivityEvent.COMMIT_DETECTED,
                owner=owner,
                project_id=repository.project_id,
                metadata={
                    "repository_id": str(repository.id),
                    "commit_hash": commit.commit_hash,
                    "additions": max(0, commit.additions),
                    "deletions": max(0, commit.deletions),
                    "files_changed": max(0, commit.files_changed),
                },
            )

        previous, complete = known_branches
        if complete:
            created = [branch for branch in snapshot.branches if branch.name not in previous][
                :_MAX_BRANCH_EVENTS
            ]
            for branch in created:
                await self._record_event(
                    ActivityEvent.BRANCH_CREATED,
                    owner=owner,
                    project_id=repository.project_id,
                    metadata={
                        "repository_id": str(repository.id),
                        "branch": branch.name,
                        "head_commit_hash": branch.head_commit_hash,
                    },
                )
            moved = [
                branch
                for branch in snapshot.branches
                if branch.name in previous and previous[branch.name] != branch.head_commit_hash
            ][:_MAX_BRANCH_EVENTS]
            for branch in moved:
                await self._record_event(
                    ActivityEvent.BRANCH_CHANGED,
                    owner=owner,
                    project_id=repository.project_id,
                    metadata={
                        "repository_id": str(repository.id),
                        "branch": branch.name,
                        "head_commit_hash": branch.head_commit_hash,
                    },
                )

        if snapshot.working_tree_changes > 0:
            await self._record_event(
                ActivityEvent.FILE_ACTIVITY_DETECTED,
                owner=owner,
                project_id=repository.project_id,
                metadata={
                    "repository_id": str(repository.id),
                    "pending_changes": snapshot.working_tree_changes,
                },
            )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def commits(
        self,
        *,
        owner: User,
        repository_id: uuid.UUID | None = None,
        branch: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = _DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> CommitListRead:
        """The commit timeline — account-wide, or one repository's history.

        Args:
            owner: The caller.
            repository_id: Narrow to one repository. Another account's id is a
                404 rather than an empty page, so the endpoint cannot be used to
                discover that the id exists.
            branch: Narrow to one branch name. Commits with no resolved branch
                are excluded rather than folded into it — "branch unknown" is not
                a branch.
            since: Inclusive lower bound on ``committed_at``.
            until: Exclusive upper bound.
            limit: Page size.
            offset: Rows to skip.

        Returns:
            One page of commits, newest first, with the total the filters match.

        Raises:
            NotFoundError: If ``repository_id`` is not this account's.
        """
        scope = None
        if repository_id is not None:
            scope = (await self._owned_repository(owner=owner, repository_id=repository_id)).id
        rows, total = await self.repositories.list_commits(
            owner.id,
            repository_id=scope,
            branch=branch,
            since=_as_utc(since),
            until=_as_utc(until),
            limit=limit,
            offset=offset,
        )
        return CommitListRead(
            items=[CommitRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=max(0, offset),
        )

    async def branches(
        self, *, owner: User, repository_id: uuid.UUID, limit: int = 200, offset: int = 0
    ) -> BranchListRead:
        """One repository's branches, current first then alphabetical.

        Args:
            owner: The caller.
            repository_id: The repository whose branches to list.
            limit: Page size.
            offset: Rows to skip.

        Returns:
            One page of branches.

        Raises:
            NotFoundError: If the repository is not this account's.
        """
        repository = await self._owned_repository(owner=owner, repository_id=repository_id)
        rows, total = await self.repositories.list_branches(
            owner.id, repository.id, limit=limit, offset=offset
        )
        return BranchListRead(
            items=[BranchRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=max(0, offset),
        )

    async def summary(self, *, owner: User, window_days: int | None = None) -> DeveloperSummaryRead:
        """The dashboard's headline counts, and one factual sentence about them.

        Counts only, over a window whose length is carried beside them so that
        the sentence underneath can be true. There is no score here and no
        comparison rendered as a verdict.

        ``has_data`` is the cold-start flag: it is false when nothing has ever
        been recorded, so a dashboard of zeroes reads as an absence rather than
        as a finding about the account.

        Args:
            owner: The caller.
            window_days: Length of the window. Defaults to
                ``developer_default_window_days`` and is rejected above
                ``developer_max_window_days``.

        Returns:
            The summary.

        Raises:
            ValidationError: If the window is longer than the configured ceiling.
                Silently shortening it would make the returned
                ``window_days`` disagree with what the caller asked for.
        """
        days, window_start, window_end = await self._resolve_window(window_days)
        repositories, total_repositories = await self.repositories.list_repositories(
            owner.id, limit=_OWNERSHIP_PAGE_SIZE
        )
        history = await self.repositories.commit_totals(owner.id)
        window = await self.repositories.commit_totals(
            owner.id, since=window_start, until=window_end
        )
        has_data = history.commits > 0
        return DeveloperSummaryRead(
            repository_count=total_repositories,
            active_repository_count=sum(1 for row in repositories if row.is_active),
            commit_count=history.commits,
            commits_in_window=window.commits,
            active_days=window.active_days,
            change_volume=window.additions + window.deletions,
            repositories_touched=window.repositories,
            window_days=days,
            window_start=window_start,
            window_end=window_end,
            latest_commit_at=_as_utc(history.latest_committed_at),
            last_scanned_at=_max_optional(row.last_scanned_at for row in repositories),
            has_data=has_data,
            summary=_summary_sentence(
                repository_count=total_repositories,
                commits_in_window=window.commits,
                repositories_touched=window.repositories,
                change_volume=window.additions + window.deletions,
                window_days=days,
                has_data=has_data,
            ),
        )

    async def read_activity(
        self,
        *,
        owner: User,
        window_days: int | None = None,
        granularity: str | None = None,
        repository_id: uuid.UUID | None = None,
    ) -> DeveloperActivityRead:
        """Commits bucketed day, week or month, with every gap zero-filled.

        Named ``read_activity`` rather than ``activity`` because ``self.activity``
        is the optional event sink, exactly as in
        :class:`~app.services.risk.detection.RiskDetectionService`. An instance
        attribute of that name would shadow a method of the same name, and the
        read behind ``GET /developer/activity`` would silently become ``None``.

        The buckets are dense by construction because
        :func:`app.services.developer.metrics.activity_series` emits one per
        period across the whole range. A chart built only from the periods that
        have commits would skip a quiet Tuesday, and a reader counting the bars
        would see five active days and read them as consecutive.

        Args:
            owner: The caller.
            window_days: Length of the range. Defaults to
                ``developer_default_window_days``.
            granularity: ``day``, ``week`` or ``month``. Defaults to
                ``developer_activity_granularity_default``.
            repository_id: Narrow to one repository.

        Returns:
            The series, with the window and the scope that produced it.

        Raises:
            ValidationError: If the window exceeds the configured ceiling or the
                granularity is not one of the three known values. Both are facts
                about the request, and both are better refused than rendered as
                an empty chart.
            NotFoundError: If ``repository_id`` is not this account's.
        """
        days, window_start, window_end = await self._resolve_window(window_days)
        step = _granularity(granularity or self.settings.developer_activity_granularity_default)
        scope = None
        if repository_id is not None:
            scope = (await self._owned_repository(owner=owner, repository_id=repository_id)).id

        samples = await self._samples(
            owner_id=owner.id,
            since=window_start,
            until=window_end,
            repository_id=scope,
        )
        series = activity_series(
            samples,
            window_start=window_start,
            window_end=window_end,
            granularity=step.value,
            repository_id=str(scope) if scope is not None else None,
        )
        grouped: dict[str, list[MetricSample]] = {}
        for sample in samples:
            grouped.setdefault(bucket_for(sample.committed_at, step.value), []).append(sample)

        buckets: list[DeveloperActivityBucketRead] = []
        starts = [bucket.start for bucket in series.buckets]
        for index, bucket in enumerate(series.buckets):
            inside = grouped.get(bucket.label, ())
            bucket_end = starts[index + 1] if index + 1 < len(starts) else window_end
            buckets.append(
                DeveloperActivityBucketRead(
                    bucket_start=bucket.start,
                    bucket_end=bucket_end,
                    commits=bucket.commit_count,
                    additions=sum(sample.additions for sample in inside),
                    deletions=sum(sample.deletions for sample in inside),
                    files_changed=sum(sample.files_changed for sample in inside),
                    repository_count=len(bucket.repository_ids),
                )
            )
        return DeveloperActivityRead(
            granularity=series.granularity,
            window_days=days,
            window_start=window_start,
            window_end=window_end,
            repository_id=scope,
            buckets=buckets,
            total_commits=sum(bucket.commit_count for bucket in series.buckets),
        )

    async def metrics(
        self,
        *,
        owner: User,
        window_days: int | None = None,
        repository_id: uuid.UUID | None = None,
    ) -> tuple[DeveloperMetricRead, ...]:
        """The eight metrics, each fully explained, over one window.

        Every metric is computed by :func:`app.services.developer.metrics.build_metrics`
        from rows this method read, so all eight describe the same window and the
        same rows. The three inputs the pure module deliberately refuses to derive
        are supplied here: the window's end instant (the database clock), the
        branches first recorded inside the window, and the fact that no per-file
        history exists.

        Args:
            owner: The caller.
            window_days: Length of the window.
            repository_id: Narrow to one repository.

        Returns:
            Exactly eight metrics, in contract order. One that could not be
            measured comes back with ``value=None`` and a reason — never
            omitted, because a client indexing by key would render a hole.

        Raises:
            ValidationError: If the window exceeds the configured ceiling.
            NotFoundError: If ``repository_id`` is not this account's.
        """
        _days, window_start, window_end = await self._resolve_window(window_days)
        scope = None
        if repository_id is not None:
            scope = (await self._owned_repository(owner=owner, repository_id=repository_id)).id

        # Momentum compares the last seven days against the seven before, so the
        # read has to reach further back than the requested window whenever the
        # window is shorter than the comparison.
        read_since = min(window_start, window_end - timedelta(days=2 * MOMENTUM_COMPARISON_DAYS))
        samples = await self._samples(
            owner_id=owner.id, since=read_since, until=window_end, repository_id=scope
        )
        growth = await self._branch_growth(
            owner_id=owner.id,
            samples=samples,
            window_start=window_start,
            window_end=window_end,
            repository_id=scope,
        )
        enriched = [
            replace(sample, first_seen_branches=growth)
            if window_start <= sample.committed_at < window_end
            else sample
            for sample in samples
        ]
        computed = build_metrics(
            enriched,
            window_start=window_start,
            window_end=window_end,
            last_touched_before=None,
        )
        return tuple(_metric_read(metric) for metric in computed)

    async def features(
        self, *, owner: User, window_days: int | None = None
    ) -> DeveloperFeatureVectorRead:
        """The ML-ready feature vector: an account row and one row per repository.

        **An extractor, not a model.** Named numbers under a schema version, so a
        later trainer knows what each column meant. Nothing here is a prediction,
        a probability or a fitted parameter, and nothing in this phase trains,
        loads or serves one.

        ``repository_age_days`` and ``inactivity_days`` are null for a repository
        with no commits. Zero would assert it was created today, and inside a
        training matrix a fabricated zero is indistinguishable from an observed
        one.

        **A repository that has never been successfully scanned has no row at
        all** — see :func:`_has_measurement`. Publishing seven zeros for it would
        claim "this repository recorded no commits in the last seven days", which
        is not what happened: nobody has looked. A row that exists means the scan
        ran and found nothing, which is a real measurement; a row that is absent
        means there is no measurement, which is what the schema has no column to
        say. A scanned-and-empty repository keeps its row and its real zeros, so
        the ``available`` semantics of ``/learning``-style surfaces survive here
        in the only form this schema can express them.

        The account-level row aggregates the recorded commits and nothing else, so
        it says what the *record* contains rather than what the account did; an
        unscanned repository contributes no commits to it, exactly as a directory
        NEXUS has never been pointed at contributes nothing.

        Two reads serve the whole vector: one flat projection of the account's
        recorded commits, which yields every per-repository figure as well as
        the account's, and one exact aggregate for the account's own commit
        range. The projection is bounded by the repository's ceiling, so a
        repository with more recorded commits than that has its age measured from
        the projection rather than from the aggregate.

        Args:
            owner: The caller.
            window_days: The window ``commit_frequency`` is computed over.

        Returns:
            The vector, stamped with ``developer_features.v1`` and the database
            clock's ``generated_at``.

        Raises:
            ValidationError: If the window exceeds the configured ceiling.
        """
        days = self._window_days(window_days)
        now = await self._now()
        window_start = now - timedelta(days=days)
        repositories, _total = await self.repositories.list_repositories(
            owner.id, limit=_OWNERSHIP_PAGE_SIZE
        )
        rows = await self.repositories.commit_rows(owner.id)
        history = await self.repositories.commit_totals(owner.id)
        samples = _samples_from_rows(rows)

        per_repository: list[RepositoryFeatureVectorRead] = []
        for repository in repositories:
            if not _has_measurement(repository):
                # Absent, not zeroed. See the docstring: an unscanned repository
                # has no figures, and a row of zeros would be a claim about a
                # repository nobody has read.
                continue
            scoped = [sample for sample in samples if sample.repository_id == str(repository.id)]
            values = _feature_values(
                scoped,
                now=now,
                window_start=window_start,
                window_days=days,
                first_seen=min((sample.committed_at for sample in scoped), default=None),
                last_seen=max((sample.committed_at for sample in scoped), default=None),
                project_linked=repository.project_id is not None,
            )
            per_repository.append(
                RepositoryFeatureVectorRead(repository_id=repository.id, **values.model_dump())
            )
        return DeveloperFeatureVectorRead(
            schema_version=FEATURE_SCHEMA_VERSION,
            generated_at=now,
            window_days=days,
            features=_feature_values(
                samples,
                now=now,
                window_start=window_start,
                window_days=days,
                first_seen=_as_utc(history.first_committed_at),
                last_seen=_as_utc(history.latest_committed_at),
                project_linked=any(row.project_id is not None for row in repositories),
            ),
            repositories=per_repository,
        )

    async def project_view(
        self, *, owner: User, project_id: uuid.UUID, window_days: int | None = None
    ) -> ProjectDeveloperRead:
        """The developer figures for one project and the repositories behind them.

        The repositories are carried alongside the counts so a project page can
        list them without a second request and cannot show a total that
        disagrees with the rows beneath it.

        The history figures come from **one grouped statement covering every
        repository in the project**, not one aggregate per repository. A project
        with fifteen repositories is a page a user opens, and issuing sixteen
        round trips to render it is the shape of a screen that gets slower the
        more work somebody does. The grouped read is the same arithmetic — the
        per-repository counts summed, the per-repository latest instants folded
        into one maximum — so nothing about the answer changed, only how many
        trips it takes to reach it.

        Args:
            owner: The caller.
            project_id: The project to describe. Another account's is a 404.
            window_days: Length of the window the in-window figures cover.

        Returns:
            The project view.

        Raises:
            ValidationError: If the window exceeds the configured ceiling.
            NotFoundError: If the project is not this account's.
        """
        days, window_start, window_end = await self._resolve_window(window_days)
        project = await self.projects.get_by_id_for_user(project_id, owner.id)
        if project is None:
            raise NotFoundError(_PROJECT_NOT_FOUND)

        repositories, total = await self.repositories.list_repositories(
            owner.id, project_id=project_id, limit=_OWNERSHIP_PAGE_SIZE
        )
        identifiers = {row.id for row in repositories}
        totals = await _commit_totals_by_repository(
            self.repositories.session, owner_id=owner.id, repository_ids=identifiers
        )
        history_total = sum(count for count, _latest_at in totals.values())
        latest = _max_optional(_latest_at for _count, _latest_at in totals.values())

        window_rows = await self.repositories.commit_rows(
            owner.id, since=window_start, until=window_end
        )
        scoped = [row for row in window_rows if row[3] in identifiers]
        commits_in_window = len(scoped)
        active_days = len({_as_utc(row[2]).date() for row in scoped if row[2] is not None})
        change_volume = sum(int(row[5] or 0) + int(row[6] or 0) for row in scoped)
        has_data = history_total > 0
        return ProjectDeveloperRead(
            project_id=project_id,
            project_name=project.name,
            repositories=[RepositoryRead.model_validate(row) for row in repositories],
            repository_count=total,
            commit_count=history_total,
            commits_in_window=commits_in_window,
            active_days=active_days,
            change_volume=change_volume,
            window_days=days,
            window_start=window_start,
            window_end=window_end,
            latest_commit_at=_as_utc(latest),
            has_data=has_data,
            summary=_summary_sentence(
                repository_count=total,
                commits_in_window=commits_in_window,
                repositories_touched=len({row[3] for row in scoped}),
                change_volume=change_volume,
                window_days=days,
                has_data=has_data,
            ),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _owned_repository(self, *, owner: User, repository_id: uuid.UUID) -> GitRepository:
        """Resolve ``repository_id`` through the scoped lookup, or 404.

        Another account's repository is not a permission error here; it is a row
        that does not exist, identically to an id nobody has ever issued.
        """
        repository = await self.repositories.get_repository(owner.id, repository_id)
        if repository is None:
            raise NotFoundError(_REPOSITORY_NOT_FOUND)
        return repository

    def _path_allowlist(self) -> tuple[Path, ...] | None:
        """The configured repository roots, or ``None`` when none are configured.

        **The distinction between "not configured" and "configured to nothing" is
        the whole point of returning a tuple that may be empty.** ``or None`` — the
        obvious one-liner — reads "the operator wrote an allowlist, every entry in
        it was unusable, therefore there is no allowlist" and hands the deployment
        an open door because one of its settings values had a NUL byte in it.
        :func:`~app.services.developer.git.validate_repository_path` treats a
        configured-but-empty list as *permit nothing*, so the honest translation
        of the setting is passed straight through, unfiltered.

        Both registration and the scan path call this, which is what keeps a
        registered repository inside the configured roots for as long as the row
        exists — registration alone would only prove it about the path as it
        resolved on the day it was written.

        Returns:
            The parsed roots, ``None`` only when the setting is blank, and an
            empty tuple when the setting names roots that could not be parsed.
        """
        raw = self.settings.developer_path_allowlist
        if not raw or not raw.strip():
            return None
        return parse_path_allowlist(raw)

    async def _known_branch_heads(
        self, owner_id: uuid.UUID, repository_id: uuid.UUID
    ) -> tuple[dict[str, str | None], bool]:
        """``{branch name: head hash}`` read before a scan writes.

        The boolean reports whether the listing was **complete**. A repository
        with more branches than one page cannot be told apart from a new one by
        name, so the scan emits no branch events at all rather than inventing
        some.
        """
        rows, total = await self.repositories.list_branches(
            owner_id, repository_id, limit=_OWNERSHIP_PAGE_SIZE
        )
        return {row.name: row.head_commit_hash for row in rows}, len(rows) >= total

    async def _branch_growth(
        self,
        *,
        owner_id: uuid.UUID,
        samples: Sequence[MetricSample],
        window_start: datetime,
        window_end: datetime,
        repository_id: uuid.UUID | None,
    ) -> frozenset[str]:
        """Branch names first recorded inside the window.

        Whole-history ``min(committed_at)`` per branch, compared against the
        window — deliberately **not** the in-window minimum, which would report
        every branch that merely had a commit in the window as brand new and turn
        ``repository_growth`` into a restatement of ``commit_activity``.

        Resolved **per repository**, because branch names are only unique inside
        one: two repositories both having ``main`` is two branches, not one, and
        grouping ``main`` across both would date the branch by whichever
        repository happened to start it first — so a repository created last week
        with its own ``main`` would report no growth at all.

        The grouping happens in the database, in one statement, for every
        repository at once. One query per repository was the obvious way to write
        this and it made the statement count grow with the number of repositories
        a user owns — including on the account-wide path, which is the one every
        ``/learning``-style read of this service sits behind. Grouping by
        ``(repository_id, branch)`` gives exactly the per-repository minima the
        loop produced, so the metric's answer is unchanged and its cost is not.

        Args:
            owner_id: Whose branches to read.
            samples: The commits in the window, from which the repositories to
                resolve are taken when the call is not already scoped.
            window_start: Inclusive start of the window.
            window_end: Exclusive end of the window.
            repository_id: One repository to resolve, or ``None`` for every
                repository touched inside the window.

        Returns:
            The branch names first recorded inside the window, as a set.
        """
        scopes = (
            [repository_id]
            if repository_id is not None
            else [
                uuid.UUID(sample.repository_id)
                for sample in samples
                if window_start <= sample.committed_at < window_end
            ]
        )
        first_seen = await _branch_first_seen_by_repository(
            self.repositories.session,
            owner_id=owner_id,
            repository_ids=set(dict.fromkeys(scopes)),
        )
        names: set[str] = set()
        for branches in first_seen.values():
            for name, first in branches.items():
                moment = _as_utc(first)
                if moment is not None and window_start <= moment < window_end:
                    names.add(name)
        return frozenset(names)

    async def _samples(
        self,
        *,
        owner_id: uuid.UUID,
        since: datetime | None,
        until: datetime | None,
        repository_id: uuid.UUID | None = None,
    ) -> list[MetricSample]:
        """Build :class:`MetricSample` values from the flat commit projection."""
        rows = await self.repositories.commit_rows(
            owner_id, repository_id=repository_id, since=since, until=until
        )
        return _samples_from_rows(rows)

    def _window_days(self, window_days: int | None) -> int:
        """Resolve a requested window length, refusing one above the ceiling.

        Rejected rather than clamped: silently shortening it would make the
        ``window_days`` on the response disagree with what the caller asked for,
        which is the kind of quiet disagreement this codebase treats as a bug.
        """
        requested = (
            self.settings.developer_default_window_days if window_days is None else int(window_days)
        )
        ceiling = max(1, self.settings.developer_max_window_days)
        if requested > ceiling:
            raise ValidationError(f"A window may span at most {ceiling} days.")
        return max(1, requested)

    async def _resolve_window(self, window_days: int | None) -> tuple[int, datetime, datetime]:
        """``(days, inclusive start, exclusive end)`` anchored on the DB clock.

        Half-open at the top so a commit landing exactly on the boundary belongs
        to one window rather than two, and anchored on ``now()`` rather than on
        the host clock so the figure sits on the same timeline as the rows it
        reads.
        """
        days = self._window_days(window_days)
        end = await self._now()
        return days, end - timedelta(days=days), end

    async def _now(self) -> datetime:
        """The database clock.

        Never ``datetime.now()``: a host whose clock drifts from the server's
        would file a commit in the wrong day, and the day a commit landed is
        exactly the figure ``active_days`` counts.
        """
        value = await self.repositories.session.scalar(select(func.now()))
        return _as_utc(value) if isinstance(value, datetime) else datetime.now(UTC)

    async def _record_event(
        self,
        event: ActivityEvent,
        *,
        owner: User,
        project_id: uuid.UUID | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Append one history row, or return immediately when there is no sink.

        Metadata is ids, counts and status strings only. A repository name is the
        user's own words and belongs on the row the event points at; duplicating
        it into a feed nobody asked for is how a summary view ends up quoting
        stale text.
        """
        if self.activity is None:
            return
        await self.activity.record(
            event.value, user_id=owner.id, project_id=project_id, metadata=metadata
        )


def _granularity(value: str) -> ActivityGranularity:
    """Coerce a requested bucket size, refusing anything unknown.

    Raises:
        ValidationError: If it is not ``day``, ``week`` or ``month``. Guessing one
            would silently return a chart nobody asked for.
    """
    try:
        return ActivityGranularity(value)
    except ValueError:
        raise ValidationError(
            f"Unsupported granularity {value!r}; expected one of "
            f"{', '.join(ACTIVITY_GRANULARITIES)}."
        ) from None


def _samples_from_rows(rows: Sequence[tuple[Any, ...]]) -> list[MetricSample]:
    """Reduce ``commit_rows`` tuples to the fields the metrics read.

    ``commit_rows`` projects ``(hash, short hash, committed_at, repository_id,
    branch, additions, deletions, files_changed)``. The per-file list is the one
    thing it cannot carry — the schema stores a count — so a commit that touched
    at least one file reports :data:`_UNRECORDED_FILE_PATH` and the maintenance
    metric says so in its own sentence.
    """
    samples: list[MetricSample] = []
    for row in rows:
        committed_at = _as_utc(row[2])
        if committed_at is None:
            continue
        files_changed = int(row[7] or 0)
        samples.append(
            MetricSample(
                committed_at=committed_at,
                repository_id=str(row[3]),
                branch=row[4],
                additions=max(0, int(row[5] or 0)),
                deletions=max(0, int(row[6] or 0)),
                files_changed=files_changed,
                file_paths=(_UNRECORDED_FILE_PATH,) if files_changed > 0 else (),
            )
        )
    return samples


def _has_measurement(repository: GitRepository) -> bool:
    """Whether this repository's figures were actually measured.

    **A successful scan, and only a successful scan.** ``last_scanned_at is
    None`` means the row was written and nobody has ever read the directory, and
    ``last_scan_status == 'error'`` means the last attempt to read it failed, so
    whatever commits are in the table are older than the last look. Either way
    the repository's activity figures are unknown rather than zero, and the
    feature vector must not publish them.

    The distinction this makes is the whole point of the Phase 10 precondition:
    a scanned repository with no commits *did* record no commits, and its zeros
    are measurements; an unscanned one was never looked at, and its zeros would
    be a claim about a developer that NEXUS has no evidence for.

    Args:
        repository: The repository row as the repository listing returned it.

    Returns:
        ``True`` when the repository has a completed, successful scan behind it.
    """
    return (
        repository.last_scanned_at is not None
        and repository.last_scan_status == GitScanStatus.OK.value
    )


async def _commit_totals_by_repository(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    repository_ids: Collection[uuid.UUID],
) -> dict[uuid.UUID, tuple[int, datetime | None]]:
    """``{repository id: (recorded commits, newest commit instant)}`` in one statement.

    The grouped form of :meth:`~app.repositories.developer.DeveloperRepository.commit_totals`,
    and the reason it exists here rather than in the repository layer is that the
    per-repository loop it replaces issued one aggregate per repository: a project
    page's cost grew with the number of repositories on it. ``GROUP BY
    repository_id`` gives the same per-repository answers the loop read, so the
    caller can still sum the counts and fold the instants — it just does it in the
    database rather than in a loop.

    Ownership is a predicate on the statement, never a filter applied to the rows
    afterwards: another account's commits must not be counted here and then
    discarded, they must not be read at all.

    Args:
        session: The session to read through.
        owner_id: Whose commits to count. Always asserted in the ``WHERE`` clause.
        repository_ids: The repositories to count, already narrowed by the caller
            to the ones the caller owns.

    Returns:
        One entry per repository that has at least one recorded commit. A
        repository in ``repository_ids`` with none recorded is **absent**, which
        is the same answer ``commit_totals`` gives for it — zero commits, no
        instants — and sums and maxima treat it identically.
    """
    if not repository_ids:
        return {}
    rows = await session.execute(
        select(
            GitCommit.repository_id,
            func.coalesce(func.count(GitCommit.id), 0),
            func.max(GitCommit.committed_at),
        )
        .where(
            GitCommit.user_id == owner_id,
            GitCommit.repository_id.in_(repository_ids),
        )
        .group_by(GitCommit.repository_id)
    )
    return {row[0]: (int(row[1] or 0), _as_utc(row[2])) for row in rows.all()}


async def _branch_first_seen_by_repository(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    repository_ids: Collection[uuid.UUID],
) -> dict[uuid.UUID, dict[str, datetime]]:
    """``{repository id: {branch name: earliest recorded commit}}``, one statement.

    Whole history, deliberately: the question is when a branch was *first
    recorded*, so narrowing the read to a window would make every branch that
    merely had a commit inside the window look brand new. Commits with no
    resolved branch are excluded rather than grouped under a placeholder name,
    which is the repository layer's own rule and is kept here so the two reads
    cannot disagree about what a branch is.

    Args:
        session: The session to read through.
        owner_id: Whose branches to read. Always asserted in the ``WHERE`` clause.
        repository_ids: The repositories to resolve. Grouping inside the database
            rather than in a loop is what keeps the statement count constant in
            the number of repositories an account owns.

    Returns:
        One entry per repository that has a branch on a recorded commit, mapping
        each of its branches to the earliest commit recorded for it.
    """
    if not repository_ids:
        return {}
    rows = await session.execute(
        select(GitCommit.repository_id, GitCommit.branch, func.min(GitCommit.committed_at))
        .where(
            GitCommit.user_id == owner_id,
            GitCommit.repository_id.in_(repository_ids),
            GitCommit.branch.is_not(None),
        )
        .group_by(GitCommit.repository_id, GitCommit.branch)
    )
    grouped: dict[uuid.UUID, dict[str, datetime]] = {}
    for repository_row, name, first in rows.all():
        moment = _as_utc(first)
        if name and moment is not None:
            grouped.setdefault(repository_row, {})[name] = moment
    return grouped


def _feature_values(
    samples: Sequence[MetricSample],
    *,
    now: datetime,
    window_start: datetime,
    window_days: int,
    first_seen: datetime | None,
    last_seen: datetime | None,
    project_linked: bool,
) -> DeveloperFeatureValues:
    """The eleven feature names, as observed facts about recorded commits.

    ``commit_frequency`` is recorded commits per day over the window — a rate of
    events, never a rate of work. The two nullable figures are null exactly when
    the subject has no commits, because zero would assert it committed today.
    """
    week_start = now - timedelta(days=_FEATURE_WEEK_DAYS)
    month_start = now - timedelta(days=_FEATURE_MONTH_DAYS)
    weekly = [s for s in samples if week_start <= s.committed_at < now]
    monthly = [s for s in samples if month_start <= s.committed_at < now]
    windowed = [s for s in samples if window_start <= s.committed_at < now]
    return DeveloperFeatureValues(
        commits_last_7d=len(weekly),
        commits_last_30d=len(monthly),
        active_days_7d=len({s.committed_at.date() for s in weekly}),
        active_days_30d=len({s.committed_at.date() for s in monthly}),
        files_changed_7d=sum(s.files_changed for s in weekly),
        additions_7d=sum(s.additions for s in weekly),
        deletions_7d=sum(s.deletions for s in weekly),
        repository_age_days=None if first_seen is None else max(0, (now - first_seen).days),
        inactivity_days=None if last_seen is None else max(0, (now - last_seen).days),
        commit_frequency=round(len(windowed) / max(1, window_days), 4),
        project_association=project_linked,
    )


def _summary_sentence(
    *,
    repository_count: int,
    commits_in_window: int,
    repositories_touched: int,
    change_volume: int,
    window_days: int,
    has_data: bool,
) -> str:
    """One factual sentence describing the counts.

    Three registers, chosen by what is actually true rather than by what would
    read best: nothing registered, registered but never committed to, and
    measured activity. Every sentence carries its own figures and none of them
    claims working time, focus, productivity or effort — a commit timestamp
    cannot support any of those.
    """
    if repository_count == 0:
        return "No repository has been registered yet, so there is no recorded history to report."
    if not has_data:
        return (
            f"{repository_count} repositor{'y is' if repository_count == 1 else 'ies are'} "
            "registered, and none of them has recorded a commit yet."
        )
    return (
        f"{commits_in_window} commit{'' if commits_in_window == 1 else 's'} were recorded "
        f"across {repositories_touched} repositor"
        f"{'y' if repositories_touched == 1 else 'ies'} in the last {window_days} days, "
        f"changing {change_volume} line{'' if change_volume == 1 else 's'}."
    )


def _max_optional(values: Any) -> datetime | None:
    """The latest aware instant in a possibly-empty iterable, or ``None``."""
    latest: datetime | None = None
    for value in values:
        latest = _latest(latest, value)
    return _as_utc(latest)


def _latest(current: datetime | None, candidate: datetime | None) -> datetime | None:
    """The later of two instants, tolerating ``None`` on either side."""
    moment = _as_utc(candidate)
    if moment is None:
        return current
    return moment if current is None or moment > current else current
