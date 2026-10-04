"""Data access for local git repositories, their commits, branches and scans.

The repository owns SQL only. It never raises a *domain* error: a foreign id is
answered with an empty result or ``None``, never an exception, so the route
above can turn it into a 404 without this layer having an opinion about what a
404 means. The two guards it does carry are about the *caller's* code rather than
about anybody's data — an unrecognised scan status, and a write to a column that
is not in the phase's write sets — and both raise :class:`ValueError`.

**Every read filters on ``user_id`` in the ``WHERE`` clause**, never by filtering
a loaded page afterwards. A row the caller may not see is then never loaded at
all, and another account's repository is answered with ``None`` — identically to
an id nobody ever issued, which is what keeps the endpoints from becoming an
existence oracle for other people's repository ids.

Two design decisions carry the file.

**The re-scan upserts, and it says which branch of the upsert it took.**
``uq_git_commits_repo_hash`` and ``uq_git_branches_repo_name`` are full unique
constraints, so both writes are a single ``INSERT ... ON CONFLICT DO UPDATE`` —
no advisory lock and no select-then-write, because PostgreSQL arbitrates the
whole batch in one statement. ``xmax = 0`` is what distinguishes an inserted row
from a refreshed one, and :meth:`DeveloperRepository.upsert_commits` counts the
two separately because the scan run records them separately: ``commits_added``
on a re-scan of an unchanged repository is the number that proves the re-scan
was idempotent. A commit is immutable once observed, so the update branch
rewrites the *observations* (the author, the line counts, and the branch
attribution a later scan may have resolved) and never the identity or the
author date, which git itself cannot change.

**Two write sets, and one of them is deliberately short.** ``PATCH
/developer/repositories/{id}`` edits metadata and nothing else, so
:meth:`DeveloperRepository.update_repository` accepts only
:data:`_EDITABLE_REPOSITORY_COLUMNS` — ``local_path`` is *not* among them, and
passing it raises rather than moving a repository to a directory whose history
was never scanned. A path change would leave the row describing a directory
while its commits describe the old one, and nothing in the schema could detect
it. The scan's own writes are a separate set,
:data:`_SCAN_STATE_COLUMNS`, because they are the only thing permitted to move
the counters, the branch names and the last-scan columns.

Smaller rules, inherited from Phase 6 and 7 rather than invented here. ``updated_at``
is written explicitly into both ``UPDATE`` statements, because
:class:`~app.db.base.TimestampMixin`'s ``onupdate`` is something SQLAlchemy
applies to statements it generates itself and an explicit one costs nothing while
removing the question. And a naive ``datetime`` handed to this module is read as
UTC rather than passed through, because PostgreSQL would otherwise interpret it
in whatever ``TimeZone`` the connection was configured with — the same silent
drift :mod:`app.repositories.analytics` refuses when it buckets days.

What the aggregate reads are for
--------------------------------
:meth:`DeveloperRepository.commit_totals` and
:meth:`DeveloperRepository.commit_rows` are **column projections**, not ORM
reads, for a reason the test suite leans on: a session that wrote the rows and
then reads them back through the ORM is handed whatever the identity map cached,
so an idempotency assertion would compare a stale object to a fresh one and pass
for the wrong reason. Both return tuples, and neither can be mistaken for a
model the caller might ``session.add()``.

The one thing this module deliberately cannot do is turn any of these numbers
into a statement about a person. ``active_days`` counts UTC dates that carry a
commit; it is not a measure of effort, focus or hours, and the column it reads
(``git_commits.committed_at``) is a fact git recorded rather than an inference
about a human.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.developer import GitBranch, GitCommit, GitRepository, GitScanRun
from app.models.enums import GitScanStatus, validate_git_scan_status
from app.repositories.analytics import utc_day

__all__ = ["CommitTotals", "CommitUpsertResult", "DeveloperRepository"]

#: Page size ceiling. The request layer validates ``?limit=`` and rejects a value
#: above its own bound, and this is the second lock on the same door rather than
#: a second policy: a repository timeline is a paginated list of rows, not a
#: report, and a caller that could ask for a million commits in one statement
#: would be reading through the index one enormous slice at a time.
_MAX_PAGE_SIZE = 200
#: What a caller that says nothing gets. Small on purpose — the dashboard's
#: repository list is a sidebar, not a table.
_DEFAULT_PAGE_SIZE = 50
#: The scan-run history read by default. Ten is the number of recent attempts
#: that answers "is this repository readable, and when did it last work".
_DEFAULT_SCAN_RUN_LIMIT = 10
#: Ceiling for the projection read. Bounded for the same reason as the page
#: size, and larger because this one is served to the metrics layer over a
#: window rather than to a paginated list.
_MAX_FACT_ROWS = 20_000

#: The only columns a metadata PATCH may write. Notably **not** ``local_path``:
#: see the module docstring for why a registered path is immutable.
_EDITABLE_REPOSITORY_COLUMNS: frozenset[str] = frozenset(
    {
        "description",
        "is_active",
        "name",
        "primary_language",
        "project_id",
    }
)

#: The columns a completed scan may write on its repository. Split from the set
#: above so that "who moved the commit count" is a question with one answer.
_SCAN_STATE_COLUMNS: frozenset[str] = frozenset(
    {
        "branch_count",
        "commit_count",
        "current_branch",
        "default_branch",
        "first_commit_at",
        "last_scan_error",
        "last_scan_status",
        "last_scanned_at",
        "latest_commit_at",
        "primary_language",
        "working_tree_dirty",
    }
)

#: Columns a naive ``datetime`` in :data:`_SCAN_STATE_COLUMNS` has to be read as
#: UTC before it reaches a ``timestamptz`` column. Aware values pass through.
_UTC_SCAN_STATE_COLUMNS: frozenset[str] = frozenset(
    {
        "first_commit_at",
        "last_scanned_at",
        "latest_commit_at",
    }
)

#: What a caller must supply for each commit in
#: :meth:`DeveloperRepository.upsert_commits`. Everything else has a default in
#: the schema or is optional evidence.
_REQUIRED_COMMIT_FIELDS: frozenset[str] = frozenset(
    {"commit_hash", "committed_at", "message", "short_hash"}
)
#: What a caller may supply, checked here rather than left to PostgreSQL so a
#: misspelled key is reported against the field the caller actually meant.
_OPTIONAL_COMMIT_FIELDS: frozenset[str] = frozenset(
    {
        "additions",
        "author_email",
        "author_name",
        "branch",
        "deletions",
        "files_changed",
    }
)

#: Required and optional keys for a branch in
#: :meth:`DeveloperRepository.upsert_branches`.
_REQUIRED_BRANCH_FIELDS: frozenset[str] = frozenset({"name"})
_OPTIONAL_BRANCH_FIELDS: frozenset[str] = frozenset(
    {"head_commit_hash", "is_current", "is_default", "last_committed_at"}
)

#: The commit columns a re-scan refreshes. The identity and the author date are
#: excluded: git cannot change either, and rewriting ``committed_at`` from a
#: second reader's parse would make two scans of one repository disagree about
#: when a commit happened. ``committed_at`` is still *required* on the way in —
#: it is what an inserted row is stamped with — it is just never rewritten by the
#: conflict branch, so the value a commit was first seen with is the value it
#: keeps.
_REFRESHED_COMMIT_COLUMNS: tuple[str, ...] = (
    "short_hash",
    "message",
    "author_name",
    "author_email",
    "additions",
    "deletions",
    "files_changed",
    "branch",
)

#: The branch columns a re-scan refreshes. ``name`` is the conflict key itself
#: and ``user_id`` belongs to the owner, not to the repository's state.
_REFRESHED_BRANCH_COLUMNS: tuple[str, ...] = (
    "is_current",
    "is_default",
    "head_commit_hash",
    "last_committed_at",
)


@dataclass(frozen=True, slots=True)
class CommitUpsertResult:
    """What one batch of observed commits did to storage.

    ``inserted`` and ``updated`` are counted separately rather than collapsed
    into one number, because the scan run stores both and the difference is the
    evidence that a re-scan was idempotent: on a repository that has not
    changed, ``inserted`` is 0 and ``updated`` is every commit git returned.
    """

    #: Commits whose hash had not been seen before.
    inserted: int
    #: Commits already stored and refreshed. Nothing about a stored commit's
    #: identity changed; only its observations were rewritten.
    updated: int

    @property
    def total(self) -> int:
        """How many commits the scan reported, inserted plus updated."""
        return self.inserted + self.updated


@dataclass(frozen=True, slots=True)
class CommitTotals:
    """Aggregates over one owner's commits in one window, in a single statement.

    Every field is a count or a recorded instant. ``first_committed_at`` and
    ``latest_committed_at`` are ``None`` when the window holds no commit, which
    is different from a window whose commits are all empty: there is no
    "earliest commit on the first of the month", there is no commit at all.
    """

    #: Commits in the window.
    commits: int
    #: Distinct repositories those commits landed in. Not the number registered:
    #: a repository with no activity in the window contributes nothing here.
    repositories: int
    #: Distinct UTC calendar days carrying at least one commit. A count of days
    #: a commit was *recorded on*, never a count of days anyone worked.
    active_days: int
    additions: int
    deletions: int
    files_changed: int
    first_committed_at: datetime | None
    latest_committed_at: datetime | None


def _as_utc(instant: datetime | None) -> datetime | None:
    """Read a caller-supplied instant as UTC when it carries no offset.

    A naive ``datetime`` handed to a ``timestamptz`` column is interpreted in
    the *session's* ``TimeZone`` by PostgreSQL, so the same ``committed_at``
    would land at a different instant depending on which connection ran the
    insert — and a commit at 23:30 UTC could then be stored on the previous
    day, changing ``active_days``. Attaching UTC here makes the value a property
    of the caller rather than of the connection. An already-aware value is
    returned untouched, offset and all.
    """
    if instant is None or instant.tzinfo is not None:
        return instant
    return instant.replace(tzinfo=UTC)


def _bounded(limit: int, offset: int) -> tuple[int, int]:
    """Clamp a page request to something a paginated list can serve.

    Both ends are clamped rather than rejected. The request layer is where
    ``?limit=500`` is a 422, and this is the belt to that braces: a caller that
    bypasses it still cannot ask for an unbounded slice. ``limit`` is floored at
    one because ``LIMIT 0`` returns nothing and a list route answering with an
    empty page for every request would look like an outage.
    """
    return max(1, min(int(limit), _MAX_PAGE_SIZE)), max(0, int(offset))


def _reject_unknown_columns(
    values: Mapping[str, Any], allowed: frozenset[str], *, what: str
) -> None:
    """Refuse a write to a column outside the set the caller was given.

    Raises:
        ValueError: If ``values`` names a column outside ``allowed``. Every such
            set is the *definition* of what the corresponding write may touch —
            the metadata patch, the scan state, the commit fields — so a key
            outside it is a programming error, and reporting it here beats a
            silent no-op or an ``IntegrityError`` from storage with no mention of
            which field was wrong.
    """
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"{what} may only write {', '.join(sorted(allowed))}; got {unknown}.")


def _commit_rows_from(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate and normalise the commit mappings one upsert will write.

    Every row comes back carrying **every** optional column, defaulted. That is
    not tidiness: a multi-row ``INSERT`` has to give each row the same key set,
    and SQLAlchemy renders a missing key as a bound parameter the column's server
    default cannot fill — so a batch where one commit has an author and the next
    does not would not compile. Filling them here makes "no author recorded" a
    value rather than an omission, which is what it is.

    Raises:
        ValueError: If a mapping omits a required field or names one that is not
            a commit column. Both are mistakes about the *calling* code — the
            fields come from the git engine's own dataclasses — and catching them
            here means the caller is told which commit was malformed instead of
            receiving a constraint error about a column.
    """
    rows: list[dict[str, Any]] = []
    for position, commit in enumerate(values):
        missing = sorted(_REQUIRED_COMMIT_FIELDS - set(commit))
        if missing:
            raise ValueError(f"Commit at position {position} is missing {missing}.")
        _reject_unknown_columns(
            commit,
            _REQUIRED_COMMIT_FIELDS | _OPTIONAL_COMMIT_FIELDS,
            what=f"A commit at position {position}",
        )
        row: dict[str, Any] = {
            "author_name": None,
            "author_email": None,
            "additions": 0,
            "deletions": 0,
            "files_changed": 0,
            "branch": None,
            **commit,
        }
        row["committed_at"] = _as_utc(row["committed_at"])
        rows.append(row)
    return rows


def _branch_rows_from(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate and normalise the branch mappings one upsert will write.

    Every row comes back carrying every optional column, for the same
    multi-insert reason as :func:`_commit_rows_from`.

    Raises:
        ValueError: If a mapping omits ``name`` or names a column that is not a
            branch column. Same reasoning as :func:`_commit_rows_from`.
    """
    rows: list[dict[str, Any]] = []
    for position, branch in enumerate(values):
        missing = sorted(_REQUIRED_BRANCH_FIELDS - set(branch))
        if missing:
            raise ValueError(f"Branch at position {position} is missing {missing}.")
        _reject_unknown_columns(
            branch,
            _REQUIRED_BRANCH_FIELDS | _OPTIONAL_BRANCH_FIELDS,
            what=f"A branch at position {position}",
        )
        row: dict[str, Any] = {
            "is_current": False,
            "is_default": False,
            "head_commit_hash": None,
            "last_committed_at": None,
            **branch,
        }
        row["last_committed_at"] = _as_utc(row["last_committed_at"])
        rows.append(row)
    return rows


def _commit_window_filters(
    owner_id: uuid.UUID,
    *,
    repository_id: uuid.UUID | None,
    since: datetime | None,
    until: datetime | None,
    branch: str | None,
) -> list[Any]:
    """Build the one ``WHERE`` clause the commit reads share.

    The commit list, its total and the two aggregate reads all filter the same
    set — "which commits does this window match" — and a filter written out three
    times is three chances for a dashboard's tile to disagree with the chart
    beneath it.

    The window is half-open: ``since`` is inclusive and ``until`` is exclusive.
    A closed window would double-count a commit landing exactly on the boundary
    between two adjacent windows, which for a day-bucketed chart is the boundary
    that matters most.

    Args:
        owner_id: Whose commits, always asserted rather than filtered after.
        repository_id: Narrow to one repository, or ``None`` for all of them.
        since: Inclusive lower bound on ``committed_at``.
        until: Exclusive upper bound on ``committed_at``.
        branch: Narrow to one branch name. Commits with no resolved branch are
            excluded by this filter rather than treated as belonging to it.

    Returns:
        The predicates, owner first, so ``ix_git_commits_user_committed`` serves
        the owner-and-window probe and ``ix_git_commits_repo_committed`` serves
        the narrowed one.
    """
    filters: list[Any] = [GitCommit.user_id == owner_id]
    if repository_id is not None:
        filters.append(GitCommit.repository_id == repository_id)
    if since is not None:
        filters.append(GitCommit.committed_at >= _as_utc(since))
    if until is not None:
        filters.append(GitCommit.committed_at < _as_utc(until))
    if branch is not None:
        filters.append(GitCommit.branch == branch)
    return filters


class DeveloperRepository:
    """Persistence for Phase 8: repositories, commits, branches and scan runs.

    Every read is owner-scoped and every write carries the owner id explicitly,
    including the two upserts, where the id is also part of the row the conflict
    target resolves against — so a commit can never be adopted by a different
    account even if a caller passed a mismatched pair.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Repositories
    # ------------------------------------------------------------------

    async def count_repositories(
        self, owner_id: uuid.UUID, *, is_active: bool | None = None
    ) -> int:
        """How many repositories this owner has registered.

        The cap check behind ``developer_max_repositories``: registering one more
        than the limit allows has to be answerable *before* the insert rather
        than by counting rows afterwards and reporting a confusing conflict.

        Args:
            owner_id: Whose repositories to count.
            is_active: Count only active repositories, or every row when
                ``None``. The cap counts every row, deactivated ones included —
                a repository the user paused still occupies its slot, and a cap
                that silently ignored it would be a number that could change
                without anything being registered.

        Returns:
            The count, zero for an owner who has registered nothing.
        """
        filters: list[Any] = [GitRepository.user_id == owner_id]
        if is_active is not None:
            filters.append(GitRepository.is_active.is_(is_active))
        return int(
            await self.session.scalar(
                select(func.count()).select_from(GitRepository).where(*filters)
            )
        )

    async def list_repositories(
        self,
        owner_id: uuid.UUID,
        *,
        project_id: uuid.UUID | None = None,
        is_active: bool | None = None,
        limit: int = _DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[list[GitRepository], int]:
        """This owner's repositories and the total the filters match.

        Ordered by ``name`` so the list is stable between requests, with ``id``
        as a final tiebreaker: two repositories may legitimately share a
        directory name in different parents, and without a total order a caller
        paging through would see one twice and miss another.

        The page and its total come from one statement — a window count over the
        same rows — so the header and the list cannot describe two different
        snapshots. The empty-page case has no row to carry the window count and
        is counted separately, re-asserting the owner predicate rather than
        trusting the caller's.

        Args:
            owner_id: Whose repositories to list.
            project_id: Narrow to the repositories linked to one project, for
                ``GET /developer/projects/{project_id}``. ``None`` means every
                repository, including those not linked to any project.
            is_active: Narrow to active or inactive repositories. ``None`` means
                both — the default repository list is not filtered.
            limit: Page size, clamped to :data:`_MAX_PAGE_SIZE`.
            offset: Rows to skip, floored at zero.

        Returns:
            The page of rows and the total number of rows the filters match.
        """
        page_size, skip = _bounded(limit, offset)
        filters: list[Any] = [GitRepository.user_id == owner_id]
        if project_id is not None:
            filters.append(GitRepository.project_id == project_id)
        if is_active is not None:
            filters.append(GitRepository.is_active.is_(is_active))

        statement = (
            select(GitRepository, func.count().over().label("total"))
            .where(*filters)
            .order_by(GitRepository.name.asc(), GitRepository.id.asc())
            .limit(page_size)
            .offset(skip)
        )
        rows = list((await self.session.execute(statement)).all())
        if rows:
            return [row[0] for row in rows], int(rows[0].total)
        total = int(
            await self.session.scalar(
                select(func.count()).select_from(GitRepository).where(*filters)
            )
        )
        return [], total

    async def get_repository(
        self, owner_id: uuid.UUID, repository_id: uuid.UUID
    ) -> GitRepository | None:
        """One repository, or ``None`` if it is not this owner's.

        The route turns ``None`` into a 404. That is deliberate and is the rule
        Phase 6 settled: a foreign id is *not* a 403, because a 403 would
        confirm the id exists and turn the endpoint into a probe for which
        repository ids are real. Nothing is raised here and nothing about another
        account's row is loaded.
        """
        result = await self.session.execute(
            select(GitRepository).where(
                GitRepository.id == repository_id,
                GitRepository.user_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_repository_by_path(
        self, owner_id: uuid.UUID, local_path: str
    ) -> GitRepository | None:
        """The repository this owner already registered at ``local_path``.

        The lookup behind the ``uq_git_repositories_owner_path`` conflict: the
        registration route wants to say "you have already registered this
        directory" in a sentence a person can act on, and an
        :class:`~sqlalchemy.exc.IntegrityError` carries a constraint name rather
        than a message.

        Scoped to the owner on purpose. Two accounts on the same machine may
        each register the same directory — the constraint is per account — so a
        global lookup would report a conflict that does not exist for this user.
        """
        result = await self.session.execute(
            select(GitRepository).where(
                GitRepository.user_id == owner_id,
                GitRepository.local_path == local_path,
            )
        )
        return result.scalar_one_or_none()

    async def create_repository(
        self,
        owner_id: uuid.UUID,
        *,
        name: str,
        local_path: str,
        description: str | None = None,
        project_id: uuid.UUID | None = None,
    ) -> GitRepository:
        """Register one local repository for one account.

        Only the user-declared columns are writable here. The snapshot columns —
        branch names, counts, commit range, last-scan status — belong to
        :meth:`update_scan_state`, because they are facts about a *scan* and
        registering a path observes nothing. A row that arrived with them filled
        in would claim a repository had been read when it had not, and
        ``last_scanned_at IS NULL`` is exactly how "never scanned" is answered.

        The path is stored as given, which means the caller must hand over the
        *resolved absolute* path the git engine's validator produced. The
        repository does not resolve it again: a second resolution would be a
        second opinion about where a directory is, taken at a later moment.

        Args:
            owner_id: Whose repository this is.
            name: The user-facing label. Free text; a directory name by default.
            local_path: The resolved absolute path of the work tree.
            description: Optional user note.
            project_id: Optional project to link it to.

        Returns:
            The stored row, refreshed so its server defaults — the counters, the
            booleans, both timestamps — carry the database's answer rather than
            being unset on the object handed back.

        Raises:
            :class:`~sqlalchemy.exc.IntegrityError`: If this owner already has a
                row at ``local_path``. Surfaced rather than swallowed because the
                route turns it into a 409 with a message naming the directory;
                the caller that wants the friendly wording asks
                :meth:`get_repository_by_path` first.
        """
        repository = GitRepository(
            id=uuid.uuid4(),
            user_id=owner_id,
            name=name,
            local_path=local_path,
            description=description,
            project_id=project_id,
        )
        self.session.add(repository)
        await self.session.commit()
        await self.session.refresh(repository)
        return repository

    async def update_repository(
        self, owner_id: uuid.UUID, repository_id: uuid.UUID, values: Mapping[str, Any]
    ) -> GitRepository | None:
        """Edit a repository's metadata, and nothing else.

        One owner-scoped ``UPDATE ... RETURNING`` rather than a read followed by
        a write, so another account's row is never loaded, only *not* updated —
        and the returned ``None`` is what the route turns into a 404.

        ``values`` is checked against :data:`_EDITABLE_REPOSITORY_COLUMNS`, which
        deliberately omits ``local_path``: a repository cannot be moved. Moving it
        would leave the row's counters, its first and latest commit and its
        recorded history describing a directory this account never scanned, and
        nothing in the schema could notice.

        A key mapped to ``None`` is written as SQL ``NULL``, which is how a
        description or a project link is cleared.

        Args:
            owner_id: Whose repository this is.
            repository_id: The repository to edit.
            values: Column name to new value. Only the five metadata columns are
                accepted.

        Returns:
            The updated row, or ``None`` when no row matched — a foreign or
            unknown id, answered identically to a foreign one.

        Raises:
            ValueError: If ``values`` is empty, or names a column outside the
                editable set — including ``local_path``.
        """
        _reject_unknown_columns(values, _EDITABLE_REPOSITORY_COLUMNS, what="A repository edit")
        if not values:
            raise ValueError("A repository edit must change at least one field.")

        writes: dict[str, Any] = dict(values)
        # Written explicitly rather than left to TimestampMixin's `onupdate`,
        # which a Core UPDATE only applies because SQLAlchemy generated the
        # statement itself. See the module docstring.
        writes["updated_at"] = func.now()
        statement = (
            update(GitRepository)
            .where(
                GitRepository.id == repository_id,
                GitRepository.user_id == owner_id,
            )
            .values(**writes)
            .returning(GitRepository)
        )
        result = await self.session.execute(statement.execution_options(populate_existing=True))
        row = result.scalar_one_or_none()
        await self.session.commit()
        return row

    async def update_scan_state(
        self, owner_id: uuid.UUID, repository_id: uuid.UUID, values: Mapping[str, Any]
    ) -> GitRepository | None:
        """Write what a finished scan observed onto its repository.

        The only path that may move the counters, the branch names, the commit
        range, ``working_tree_dirty`` and the three last-scan columns, and it is
        called once per scan — after the attempt has finished, so it writes an
        outcome rather than a state in progress.

        A failed scan writes ``last_scan_status='error'`` and a human
        ``last_scan_error``, and a successful one writes ``None`` for the error —
        which is how a recovered repository stops reporting the last failure.
        Leaving a stale error in place would be the worse default: the row would
        claim to be broken while the last scan says otherwise.

        ``first_commit_at`` and ``latest_commit_at`` are written from whatever
        the caller measured rather than being recomputed here. They are a *range*
        over observed history, and a re-scan that only saw new commits cannot
        widen the range by reading forward — only the scan engine knows the
        repository's earliest commit.

        Args:
            owner_id: Whose repository this is.
            repository_id: The repository the scan read.
            values: Column name to value, restricted to
                :data:`_SCAN_STATE_COLUMNS`. Naive datetimes are read as UTC.

        Returns:
            The updated row, or ``None`` when no row matched — which for a scan
            service is the signal that the repository disappeared underneath it,
            and which must be reported rather than swallowed.

        Raises:
            ValueError: If ``values`` is empty or names a column outside the
                scan-state set. A scan writing ``created_at`` or ``local_path``
                through here would be a bug about what a scan observes.
        """
        _reject_unknown_columns(values, _SCAN_STATE_COLUMNS, what="A scan state write")
        if not values:
            raise ValueError("A scan state write must change at least one column.")

        writes: dict[str, Any] = {
            key: (_as_utc(value) if key in _UTC_SCAN_STATE_COLUMNS else value)
            for key, value in values.items()
        }
        writes["updated_at"] = func.now()
        statement = (
            update(GitRepository)
            .where(
                GitRepository.id == repository_id,
                GitRepository.user_id == owner_id,
            )
            .values(**writes)
            .returning(GitRepository)
        )
        result = await self.session.execute(statement.execution_options(populate_existing=True))
        row = result.scalar_one_or_none()
        await self.session.commit()
        return row

    async def delete_repository(self, owner_id: uuid.UUID, repository_id: uuid.UUID) -> bool:
        """Remove one repository and everything observed under it.

        The commits, branches and scan runs go with it through the ``ON DELETE
        CASCADE`` the schema declares, in one statement — no reading the children
        first, which would be a round trip per table and a window in which a
        concurrent scan could insert into a repository that is being deleted.

        Owner-scoped, so a foreign id deletes nothing and reports the same
        ``False`` an unknown id does.

        Returns:
            ``True`` when a row was deleted, ``False`` when none matched.
        """
        result = await self.session.execute(
            delete(GitRepository).where(
                GitRepository.id == repository_id,
                GitRepository.user_id == owner_id,
            )
        )
        await self.session.commit()
        return bool(result.rowcount)

    # ------------------------------------------------------------------
    # Commits
    # ------------------------------------------------------------------

    async def list_commits(
        self,
        owner_id: uuid.UUID,
        *,
        repository_id: uuid.UUID | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        branch: str | None = None,
        limit: int = _DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[list[GitCommit], int]:
        """This owner's commits in a window, newest first, and the total.

        Ordered by ``committed_at`` descending with ``id`` as a tiebreaker, so
        paging is stable: ``committed_at`` is second-resolution and two commits
        can share it exactly, which would otherwise let a row appear on two
        pages and another on none.

        The filters are the ones :meth:`commit_totals` and
        :meth:`commit_rows` take, built by the same
        :func:`_commit_window_filters` — a list describing a different set from
        the chart above it is worse than no chart.

        Args:
            owner_id: Whose commits to list.
            repository_id: Narrow to one repository, or ``None`` for the
                account-wide timeline.
            since: Inclusive lower bound on ``committed_at``.
            until: Exclusive upper bound on ``committed_at``.
            branch: Narrow to one branch name. Commits with no resolved branch
                are excluded rather than folded into this one.
            limit: Page size, clamped to :data:`_MAX_PAGE_SIZE`.
            offset: Rows to skip, floored at zero.

        Returns:
            The page of rows and the total number of rows the filters match.
        """
        page_size, skip = _bounded(limit, offset)
        filters = _commit_window_filters(
            owner_id,
            repository_id=repository_id,
            since=since,
            until=until,
            branch=branch,
        )
        statement = (
            select(GitCommit, func.count().over().label("total"))
            .where(*filters)
            .order_by(GitCommit.committed_at.desc(), GitCommit.id.desc())
            .limit(page_size)
            .offset(skip)
        )
        rows = list((await self.session.execute(statement)).all())
        if rows:
            return [row[0] for row in rows], int(rows[0].total)
        total = int(
            await self.session.scalar(select(func.count()).select_from(GitCommit).where(*filters))
        )
        return [], total

    async def get_commit(self, owner_id: uuid.UUID, commit_id: uuid.UUID) -> GitCommit | None:
        """One commit, or ``None`` if it is not this owner's.

        The same 404-not-403 rule as :meth:`get_repository`, for the same
        reason: the two answers must be indistinguishable or the route becomes
        an existence oracle.
        """
        result = await self.session.execute(
            select(GitCommit).where(
                GitCommit.id == commit_id,
                GitCommit.user_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_commits(
        self,
        owner_id: uuid.UUID,
        repository_id: uuid.UUID,
        commits: Iterable[Mapping[str, Any]],
    ) -> CommitUpsertResult:
        """Store the commits one scan observed, idempotently.

        **This is what makes re-scanning safe.** The git CLI returns the same
        commits every time it is run against an unchanged repository, so the
        write has to be an upsert keyed on ``uq_git_commits_repo_hash`` or the
        dashboard's commit counts would double every time the user pressed the
        scan button. One ``INSERT ... ON CONFLICT DO UPDATE`` for the whole
        batch, arbitrated by that constraint — no select-then-write, and no
        advisory lock, because the conflict target is a *full* unique constraint
        rather than the partial one Phase 7 needed for live risks. A commit is
        not an episode: the same commit is the same commit forever.

        ``xmax = 0`` is what tells the two outcomes apart, and the counts are
        reported separately because the scan run records them separately.
        ``commits_added`` on a re-scan of an unchanged repository is the figure
        that proves the re-scan was idempotent, and it can only be that figure
        if this method distinguishes the branches.

        The update branch rewrites observations and never identity:
        ``committed_at`` and ``commit_hash`` are git's own facts about the
        object, and a second reader disagreeing about them would mean one of the
        two scans parsed the repository differently. ``branch`` *is* refreshed,
        because attribution is best-effort and a later scan may resolve a branch
        an earlier one could not.

        Args:
            owner_id: Whose commits these are.
            repository_id: The repository they were read from. It is part of
                every row's conflict key, so a commit can never be moved between
                repositories by this call.
            commits: Mappings keyed by column name. ``commit_hash``,
                ``short_hash``, ``committed_at`` and ``message`` are required;
                ``author_name``, ``author_email``, ``additions``, ``deletions``,
                ``files_changed`` and ``branch`` are optional. The ``id``,
                ``user_id``, ``repository_id`` and ``created_at`` columns are
                written here, not by the caller.

        Returns:
            A :class:`CommitUpsertResult` counting the inserted and the refreshed
            rows. An empty batch returns zeros without touching the database.

        Raises:
            ValueError: If a mapping omits a required field or names a column
                that is not a commit column.
        """
        rows = _commit_rows_from(list(commits))
        if not rows:
            return CommitUpsertResult(inserted=0, updated=0)

        insert = pg_insert(GitCommit).values(
            [
                {
                    "id": uuid.uuid4(),
                    "user_id": owner_id,
                    "repository_id": repository_id,
                    **row,
                }
                for row in rows
            ]
        )
        statement = insert.on_conflict_do_update(
            index_elements=[GitCommit.repository_id, GitCommit.commit_hash],
            set_={name: insert.excluded[name] for name in _REFRESHED_COMMIT_COLUMNS},
        ).returning(literal_column("xmax = 0").label("was_inserted"))
        # `populate_existing` for the reason given in `_upsert_indexed_risk` of
        # the risk repository: a commit this session already holds — from the
        # list the scan read first, say — would otherwise keep its cached
        # attributes while the row behind it moved.
        result = await self.session.execute(statement.execution_options(populate_existing=True))
        flags = [bool(flag) for flag in result.scalars().all()]
        await self.session.commit()
        inserted = sum(flags)
        return CommitUpsertResult(inserted=inserted, updated=len(flags) - inserted)

    async def commit_totals(
        self,
        owner_id: uuid.UUID,
        *,
        repository_id: uuid.UUID | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        branch: str | None = None,
    ) -> CommitTotals:
        """Every aggregate the metrics layer needs, in one statement.

        One grouped-free aggregate rather than seven: each additional round trip
        is another chance for the chart and its header to describe different
        windows, and this is the read behind the whole summary screen.

        ``active_days`` counts distinct **UTC** calendar days carrying at least
        one commit, bucketed through :func:`app.repositories.analytics.utc_day` so
        the answer is a property of the query rather than of whichever
        connection ran it. It is a count of days a commit was recorded on. It is
        not a count of days anybody worked, and nothing in this method can be
        read as one.

        An empty window returns zeros with ``None`` for the two instants, which is
        a different answer from a window whose commits are all empty — and the
        metrics module is expected to render the difference as "not enough data"
        rather than as a zero.

        Args:
            owner_id: Whose commits to aggregate.
            repository_id: Narrow to one repository, or ``None`` for all of them.
            since: Inclusive lower bound on ``committed_at``.
            until: Exclusive upper bound on ``committed_at``.
            branch: Narrow to one branch name.

        Returns:
            The :class:`CommitTotals` for the filtered set.
        """
        filters = _commit_window_filters(
            owner_id,
            repository_id=repository_id,
            since=since,
            until=until,
            branch=branch,
        )
        statement = select(
            func.coalesce(func.count(GitCommit.id), 0),
            func.count(func.distinct(GitCommit.repository_id)),
            func.count(func.distinct(utc_day(GitCommit.committed_at))),
            func.coalesce(func.sum(GitCommit.additions), 0),
            func.coalesce(func.sum(GitCommit.deletions), 0),
            func.coalesce(func.sum(GitCommit.files_changed), 0),
            func.min(GitCommit.committed_at),
            func.max(GitCommit.committed_at),
        ).where(*filters)
        row = (await self.session.execute(statement)).one()
        return CommitTotals(
            commits=int(row[0]),
            repositories=int(row[1]),
            active_days=int(row[2]),
            additions=int(row[3]),
            deletions=int(row[4]),
            files_changed=int(row[5]),
            first_committed_at=row[6],
            latest_committed_at=row[7],
        )

    async def commit_rows(
        self,
        owner_id: uuid.UUID,
        *,
        repository_id: uuid.UUID | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        branch: str | None = None,
        limit: int = _MAX_FACT_ROWS,
    ) -> list[tuple[Any, ...]]:
        """Flat commit tuples for bucketing, in one statement.

        A **column projection**, so the rows never enter the identity map and a
        session that wrote them reads back what the database holds rather than
        what it cached. This is the read behind the activity series and the
        feature vector: eight columns, no entity, one round trip.

        The columns are the ones a chart or a feature extractor needs and no
        others — the author name and e-mail are deliberately **not** selected,
        because nothing in the product needs a person's name per commit and a
        projection is the cheapest place to keep that out of a wide read.

        Args:
            owner_id: Whose commits to read.
            repository_id: Narrow to one repository, or ``None`` for all of them.
            since: Inclusive lower bound on ``committed_at``.
            until: Exclusive upper bound on ``committed_at``.
            branch: Narrow to one branch name.
            limit: Ceiling on rows returned, clamped to
                :data:`_MAX_FACT_ROWS`. Ordered newest first, so the ceiling
                keeps the most recent window rather than an arbitrary slice.

        Returns:
            ``(commit_hash, short_hash, committed_at, repository_id, branch,
            additions, deletions, files_changed)`` tuples, newest first.
        """
        ceiling = max(1, min(int(limit), _MAX_FACT_ROWS))
        filters = _commit_window_filters(
            owner_id,
            repository_id=repository_id,
            since=since,
            until=until,
            branch=branch,
        )
        result = await self.session.execute(
            select(
                GitCommit.commit_hash,
                GitCommit.short_hash,
                GitCommit.committed_at,
                GitCommit.repository_id,
                GitCommit.branch,
                GitCommit.additions,
                GitCommit.deletions,
                GitCommit.files_changed,
            )
            .where(*filters)
            .order_by(GitCommit.committed_at.desc(), GitCommit.id.desc())
            .limit(ceiling)
        )
        return [tuple(row) for row in result.all()]

    async def branch_first_seen(
        self,
        owner_id: uuid.UUID,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        repository_id: uuid.UUID | None = None,
    ) -> list[tuple[str, datetime]]:
        """``(branch, earliest commit in the window)`` per branch that has one.

        The ``repository_growth`` metric's raw material: a branch whose earliest
        recorded commit falls inside the window is a branch that was first seen
        inside it. Returning the pair rather than a count is deliberate — the
        metric has to be able to say *which* branches, and a count computed here
        would be a number with no way to check it.

        Commits with no resolved branch are absent from the result rather than
        grouped under a placeholder name: "branch unknown" is not a branch, and
        a growth metric that counted it would be counting something nobody
        created.

        Args:
            owner_id: Whose commits to read.
            since: Inclusive lower bound on ``committed_at``.
            until: Exclusive upper bound on ``committed_at``.
            repository_id: Narrow to one repository, or ``None`` for all of them.

        Returns:
            The pairs, ordered by branch name so the result is stable.
        """
        filters = _commit_window_filters(
            owner_id,
            repository_id=repository_id,
            since=since,
            until=until,
            branch=None,
        )
        filters.append(GitCommit.branch.is_not(None))
        result = await self.session.execute(
            select(GitCommit.branch, func.min(GitCommit.committed_at))
            .where(*filters)
            .group_by(GitCommit.branch)
            .order_by(GitCommit.branch.asc())
        )
        return [(str(name), first) for name, first in result.all() if name is not None]

    # ------------------------------------------------------------------
    # Branches
    # ------------------------------------------------------------------

    async def list_branches(
        self,
        owner_id: uuid.UUID,
        repository_id: uuid.UUID,
        *,
        limit: int = _MAX_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[list[GitBranch], int]:
        """One repository's branches, current first then alphabetical, and the total.

        A repository is *not* verified here to belong to ``owner_id``; the branch
        query asserts ``user_id`` itself, so a foreign ``repository_id`` returns
        an empty page rather than another account's branches. Callers that need
        the 404 check :meth:`get_repository` first.

        The current branch leads because it is what a repository page is about,
        and ``name`` follows because a branch list with no order is a set.
        ``id`` breaks ties between two branches whose names differ only by case,
        which git allows on a case-sensitive filesystem.

        Args:
            owner_id: Whose branches to list.
            repository_id: The repository whose branches to list.
            limit: Page size, clamped to :data:`_MAX_PAGE_SIZE`.
            offset: Rows to skip, floored at zero.

        Returns:
            The page of rows and the total number the repository has.
        """
        page_size, skip = _bounded(limit, offset)
        filters: list[Any] = [
            GitBranch.user_id == owner_id,
            GitBranch.repository_id == repository_id,
        ]
        statement = (
            select(GitBranch, func.count().over().label("total"))
            .where(*filters)
            .order_by(GitBranch.is_current.desc(), GitBranch.name.asc(), GitBranch.id.asc())
            .limit(page_size)
            .offset(skip)
        )
        rows = list((await self.session.execute(statement)).all())
        if rows:
            return [row[0] for row in rows], int(rows[0].total)
        total = int(
            await self.session.scalar(select(func.count()).select_from(GitBranch).where(*filters))
        )
        return [], total

    async def get_branch(self, owner_id: uuid.UUID, branch_id: uuid.UUID) -> GitBranch | None:
        """One branch, or ``None`` if it is not this owner's.

        Owner-scoped like every other single-row read, so a foreign id and an
        unknown id are the same answer.
        """
        result = await self.session.execute(
            select(GitBranch).where(
                GitBranch.id == branch_id,
                GitBranch.user_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_branches(
        self,
        owner_id: uuid.UUID,
        repository_id: uuid.UUID,
        branches: Iterable[Mapping[str, Any]],
    ) -> int:
        """Store the branches one scan observed, idempotently.

        The commit upsert's shape against ``uq_git_branches_repo_name``: a
        re-scan sees the same branch names and must update rather than duplicate,
        which matters here because ``git_repositories.branch_count`` is written
        from what this method saw. A repository whose branch list doubled on
        every scan would report a branch count nothing can corroborate.

        The update branch refreshes the head hash, the two flags and the head's
        committer date. It does not touch ``name`` — that is the conflict key —
        or ``user_id``, which belongs to the owner rather than to the
        repository's state.

        Args:
            owner_id: Whose branches these are.
            repository_id: The repository they were read from.
            branches: Mappings keyed by column name. ``name`` is required;
                ``head_commit_hash``, ``is_current``, ``is_default`` and
                ``last_committed_at`` are optional and fall back to the schema's
                defaults.

        Returns:
            How many branch rows the upsert wrote, inserted plus refreshed. An
            empty batch returns 0 without touching the database.

        Raises:
            ValueError: If a mapping omits ``name`` or names a column that is
                not a branch column.
        """
        rows = _branch_rows_from(list(branches))
        if not rows:
            return 0

        insert = pg_insert(GitBranch).values(
            [
                {
                    "id": uuid.uuid4(),
                    "user_id": owner_id,
                    "repository_id": repository_id,
                    **row,
                }
                for row in rows
            ]
        )
        statement = insert.on_conflict_do_update(
            index_elements=[GitBranch.repository_id, GitBranch.name],
            set_={name: insert.excluded[name] for name in _REFRESHED_BRANCH_COLUMNS},
        )
        await self.session.execute(statement.execution_options(populate_existing=True))
        await self.session.commit()
        return len(rows)

    async def delete_branches_absent(
        self, owner_id: uuid.UUID, repository_id: uuid.UUID, names: Sequence[str]
    ) -> int:
        """Drop the branches a scan did not see, and report how many went.

        ``names`` is the set the scan *did* report, so a branch deleted on disk
        stops appearing in the repository's listing. Without this the listing
        would grow monotonically and quietly claim a branch that no longer
        exists — a factual error of exactly the kind this phase is not allowed
        to tell.

        The commits those branches carried are **not** deleted: a commit is an
        observation about the repository's history, not about a branch pointer
        that has since moved, and it is what every metric reads.

        Args:
            owner_id: Whose branches to delete.
            repository_id: The repository they belong to.
            names: Branch names the scan reported. An **empty** sequence means
                the scan saw no branches at all, so every branch row for the
                repository goes — which is the correct reading for a repository
                whose branches were all deleted, and the reason an empty
                sequence cannot mean "no filter".

        Returns:
            How many branch rows were deleted, zero when ``names`` covers all of
            them.
        """
        statement = delete(GitBranch).where(
            GitBranch.user_id == owner_id,
            GitBranch.repository_id == repository_id,
        )
        if names:
            statement = statement.where(GitBranch.name.notin_(list(names)))
        result = await self.session.execute(statement)
        await self.session.commit()
        return int(result.rowcount or 0)

    # ------------------------------------------------------------------
    # Scan runs
    # ------------------------------------------------------------------

    async def record_scan_run(
        self,
        owner_id: uuid.UUID,
        repository_id: uuid.UUID,
        *,
        status: str | GitScanStatus,
        commits_discovered: int = 0,
        commits_added: int = 0,
        branches_discovered: int = 0,
        duration_ms: int = 0,
        error: str | None = None,
        scanned_at: datetime | None = None,
    ) -> GitScanRun:
        """Write the one row describing a finished scan attempt.

        Every attempt gets a row, including the ones that failed — that is the
        table's reason for existing. A failed scan recorded only as a message on
        the repository row would lose the history of a repository that was broken
        for a week and then fixed, and that history is the difference between
        "this repository is unreadable" and "this repository was unreadable until
        Tuesday".

        ``duration_ms`` is the wall-clock cost of the git call. It is recorded
        because it is the only signal that a repository has become expensive to
        read, and it is explicitly not a measure of anybody's work.

        Args:
            owner_id: Whose scan this is.
            repository_id: The repository that was read.
            status: ``ok`` or ``error``, validated against
                :class:`~app.models.enums.GitScanStatus`.
            commits_discovered: What git returned.
            commits_added: How many of those were new to storage. On a re-scan
                of an unchanged repository this is 0, which is the figure that
                shows the scan was idempotent.
            branches_discovered: What ``for-each-ref`` reported.
            duration_ms: How long the CLI call took, in milliseconds.
            error: A human sentence for a failed scan. Never a traceback.
            scanned_at: When the attempt finished. Defaults to the database
                clock, which is the right default: the attempt's own clock is
                not evidence of when it happened.

        Returns:
            The stored row, refreshed so the server defaults carry the
            database's answer.

        Raises:
            ValueError: If ``status`` is not a known scan status, or a count is
                negative. Both are statements about the calling code — the status
                comes from the enum and the counts from the engine's own output —
                and both would otherwise be rejected by a check constraint with
                no mention of which value was wrong.
        """
        status_value = validate_git_scan_status(status).value
        counts = {
            "commits_discovered": commits_discovered,
            "commits_added": commits_added,
            "branches_discovered": branches_discovered,
            "duration_ms": duration_ms,
        }
        negative = sorted(name for name, value in counts.items() if int(value) < 0)
        if negative:
            raise ValueError(f"A scan run cannot record negative counts: {negative}.")

        run = GitScanRun(
            id=uuid.uuid4(),
            user_id=owner_id,
            repository_id=repository_id,
            status=status_value,
            commits_discovered=int(commits_discovered),
            commits_added=int(commits_added),
            branches_discovered=int(branches_discovered),
            duration_ms=int(duration_ms),
            error=error,
        )
        # Left to the column's server default when the caller supplies nothing:
        # `scanned_at` is a server default rather than a Python default so that
        # the instant comes from the database clock, which is the clock the rest
        # of the schema is written against.
        if scanned_at is not None:
            run.scanned_at = _as_utc(scanned_at)
        self.session.add(run)
        await self.session.commit()
        # `scanned_at` and `created_at` are server defaults, so re-read rather
        # than hand back a row whose timestamps are still unset.
        await self.session.refresh(run)
        return run

    async def list_scan_runs(
        self,
        owner_id: uuid.UUID,
        *,
        repository_id: uuid.UUID | None = None,
        limit: int = _DEFAULT_SCAN_RUN_LIMIT,
    ) -> list[GitScanRun]:
        """Recent scan attempts, newest first.

        The scan history panel's one read. ``scanned_at`` is the ordering column
        rather than ``created_at`` because it is the instant the attempt belongs
        to; ``id`` breaks ties so a page through a busy afternoon is stable.

        Args:
            owner_id: Whose scan runs to read.
            repository_id: Narrow to one repository, or ``None`` for every
                repository the owner has scanned.
            limit: Ceiling on rows, clamped to :data:`_MAX_PAGE_SIZE`.

        Returns:
            The rows, newest first.
        """
        page_size = max(1, min(int(limit), _MAX_PAGE_SIZE))
        filters: list[Any] = [GitScanRun.user_id == owner_id]
        if repository_id is not None:
            filters.append(GitScanRun.repository_id == repository_id)
        result = await self.session.execute(
            select(GitScanRun)
            .where(*filters)
            .order_by(GitScanRun.scanned_at.desc(), GitScanRun.id.desc())
            .limit(page_size)
        )
        return list(result.scalars().all())

    async def latest_scan_run(
        self, owner_id: uuid.UUID, repository_id: uuid.UUID
    ) -> GitScanRun | None:
        """The most recent attempt for one repository, or ``None``.

        What a repository detail page shows before it re-renders, and what a
        caller compares a freshly written run against. Owner-scoped, so a
        foreign repository reads as "never scanned" rather than as somebody
        else's failure.
        """
        result = await self.session.execute(
            select(GitScanRun)
            .where(
                GitScanRun.user_id == owner_id,
                GitScanRun.repository_id == repository_id,
            )
            .order_by(GitScanRun.scanned_at.desc(), GitScanRun.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
