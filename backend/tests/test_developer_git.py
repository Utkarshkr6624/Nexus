"""The git engine, exercised against real repositories built in the test.

These tests run the system ``git`` binary against a repository created inside a
temporary directory, because there is no honest way to test a CLI wrapper with a
mock: a mock asserts the *arguments this implementation would have passed*, which
proves nothing about whether git accepts them. Every command in
:mod:`app.services.developer.git` is driven here against a real repository with a
known history, and the figures are derived from what was committed rather than
recorded from a previous run.

What is asserted, and why each of these three groups matters:

* **The failure surface.** A broken repository must never break NEXUS, so every
  way the reader can fail is exercised and must produce one of the two declared
  exception types carrying a human sentence — no ``FileNotFoundError``, no
  ``UnicodeDecodeError``, no traceback. The path tests are the ones that would
  otherwise let a plain 500 reach the user.
* **The empty repository.** ``git init`` with no commits is a *valid* repository
  and §5 of the contracts requires it to be accepted. It is also the case that
  made every naive reader fail, because ``git log`` exits non-zero in it and a
  reader that treats that as an error tells a user their brand-new project is
  broken.
* **The scan semantics.** Half-open windows, UTC dates, summed line changes,
  ``--no-merges``, and an incremental ``--since`` that transfers only what is new.
  Each of these is a decision the contracts fix, and each is the kind that is
  invisible in a demo and wrong in production.

``pytestmark = pytest.mark.integration`` is deliberate and slightly unusual here:
these tests touch no PostgreSQL. They are marked anyway because they depend on a
**system ``git`` binary existing and behaving like git**, which is the same class
of external dependency the marker is for, and because a machine without git should
skip the file rather than report thirty failures that look like product defects.

Every timestamp is asserted as an offset-aware value derived from what the test
itself committed. Nothing here reads ``datetime.now()``: a test that reads the
wall clock starts failing the day someone moves timezone, and none of these
figures depend on when the suite runs.
"""

from __future__ import annotations

import ast
import asyncio
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.services.developer import git as git_engine
from app.services.developer.git import (
    DEFAULT_GIT_TIMEOUT_SECONDS,
    LANGUAGE_BY_EXTENSION,
    MAX_COMMITS_PER_SCAN,
    MAX_SCAN_OUTPUT_BYTES,
    GitCommandError,
    GitRepositoryError,
    detect_language,
    language_distribution,
    parse_path_allowlist,
    read_repository,
    run_git,
    sanitize_git_message,
    validate_repository_path,
)

pytestmark = pytest.mark.integration


def _git_available() -> bool:
    """Whether a ``git`` binary this module can actually run exists.

    Checked with the same executable name the module uses, so a machine with only
    a ``git.exe`` on ``PATH`` and one with nothing at all are both handled
    correctly rather than half of them.
    """
    from shutil import which

    return which("git") is not None


requires_git = pytest.mark.skipif(
    not _git_available(), reason="the system git binary is not on PATH"
)


def _commit(
    repo: Path,
    message: str,
    *,
    files: dict[str, str] | None = None,
    when: str | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> None:
    """Write files, stage them, and commit at a fixed instant.

    ``when`` is exported as ``GIT_AUTHOR_DATE`` and ``GIT_COMMITTER_DATE`` rather
    than passed as ``git commit --date``, because git has an author-date flag and
    no committer-date one — setting only the author date would leave the committer
    date on the wall clock, and ``%(committerdate:iso-strict)`` on the branch
    listing would then disagree with every assertion about branch dates.

    A fixture whose dates drifted with the wall clock would make every window
    assertion in this file depend on the hour the suite ran.
    """
    for name, content in (files or {}).items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    if when is not None and monkeypatch is not None:
        monkeypatch.setenv("GIT_AUTHOR_DATE", when)
        monkeypatch.setenv("GIT_COMMITTER_DATE", when)

    asyncio.run(run_git(repo, "add", "-A"))
    asyncio.run(run_git(repo, "commit", "-m", message, "--no-gpg-sign"))


def _init(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Create an empty ``main``-branched repository with a fixed identity.

    A fixture identity rather than the machine's global git config, so the scanned
    author name and email are the same on a laptop, in CI and on a build agent
    whose ``~/.gitconfig`` happens to say something else.
    """
    root.mkdir(parents=True, exist_ok=True)
    asyncio.run(run_git(root, "init", "-b", "main", "."))
    asyncio.run(run_git(root, "config", "user.email", "test@example.invalid"))
    asyncio.run(run_git(root, "config", "user.name", "Test Person"))
    del monkeypatch


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository on ``main`` with two dated commits and three tracked files.

    ``main.py`` and ``util.py`` are Python, ``page.ts`` is TypeScript, and
    ``notes.txt`` — written but deliberately never staged — is there so the
    language distribution has something to *not* count.

    The two commits are 10 days and 3 days before a fixed reference instant. The
    first writes three one-line files, so it changes 3 lines across 2 files; the
    second appends one line to ``util.py``, so it changes 1 line in 1 file. Those
    figures are derived from the writes above rather than recorded from a run,
    which is what makes a change to how the stats are counted show up as a wrong
    number instead of as a moved baseline.
    """
    reference = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    root = tmp_path / "demo"
    _init(root, monkeypatch)

    _commit(
        root,
        "Add the entry point and a helper",
        files={
            "main.py": "print(1)\n",
            "util.py": "VALUE = 2\n",
            "page.ts": "export const x = 1;\n",
        },
        when=(reference - timedelta(days=10)).isoformat(),
        monkeypatch=monkeypatch,
    )
    _commit(
        root,
        "Extend the helper",
        files={"util.py": "VALUE = 2\nMORE = 3\n"},
        when=(reference - timedelta(days=3)).isoformat(),
        monkeypatch=monkeypatch,
    )
    # Left untracked on purpose: it must not appear in `ls-files`, and therefore
    # must not change the language distribution or the tracked file count.
    (root / "notes.txt").write_text("scratch\n", encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Async fixtures
#
# The two helpers below exist because the ones above cannot be used from an
# ``async def`` test: they call ``asyncio.run``, which raises inside a running
# loop. A test that needs a repository of its own and also needs to read it has
# to build it with ``await``.
# ---------------------------------------------------------------------------


async def _init_async(root: Path) -> None:
    """Create an empty ``main``-branched repository, awaiting each git call.

    ``git init`` is given the path rather than run inside it, so the directory is
    created by git and this helper contains no blocking filesystem call of its
    own — a synchronous ``Path.mkdir`` at the top of an async function is exactly
    the kind of thing the engine module goes out of its way not to do.

    The identity is a fixture rather than the machine's global config, so the
    scanned author is the same everywhere.
    """
    await run_git(root.parent, "init", "-b", "main", str(root))
    await run_git(root, "config", "user.email", "test@example.invalid")
    await run_git(root, "config", "user.name", "Test Person")


async def _commit_at_async(repo: Path, message: str, *, name: str, body: str, when: datetime):
    """Write a file, stage it, and commit it at a fixed instant.

    Both dates are exported to the environment rather than passed as flags,
    because git has an author-date flag and no committer-date one. That matters
    more than it looks: ``git log --since`` filters on the *committer* date, so a
    commit with only its author date moved would fall outside a window built from
    author dates — which is exactly what the windowing tests below assert about.
    """
    stamp = when.isoformat()
    keys = ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(dict.fromkeys(keys, stamp))
    try:
        (repo / name).write_text(body, encoding="utf-8")
        await run_git(repo, "add", "-A")
        await run_git(repo, "commit", "-m", message, "--no-gpg-sign")
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


@requires_git
def test_a_git_work_tree_validates_and_the_resolved_path_is_what_comes_back(repo: Path):
    """A real repository resolves to its absolute path.

    The absolute path is the return value *because it is what gets stored*: a
    caller that stored the string it was given would have a registered repository
    that resolves somewhere else after the server's working directory changes.
    """
    resolved = validate_repository_path(repo)

    assert resolved.is_absolute()
    assert resolved == repo.resolve()
    assert (resolved / ".git").exists()


@requires_git
def test_a_relative_path_is_resolved_rather_than_stored_as_given(repo: Path, monkeypatch):
    """``./demo`` and the absolute path name the same repository.

    Pinned because the stored value is what the scan re-resolves days later, and
    a relative path left in storage is how a registered repository ends up
    pointing somewhere else.
    """
    monkeypatch.chdir(repo.parent)

    assert validate_repository_path("./demo") == repo.resolve()
    assert validate_repository_path("demo") == repo.resolve()


@requires_git
def test_a_home_relative_path_is_expanded_before_it_is_resolved(tmp_path: Path, monkeypatch):
    """``~`` means the home directory, not a directory literally named ``~``.

    A user typing ``~/code/project`` expects their home directory. Without the
    expansion the path is resolved relative to the server's working directory and
    fails, and the error message says so in a way that looks like the user's
    directory is missing.
    """
    (tmp_path / "somewhere").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    # The directory exists under the fake home but has no `.git`, so the specific
    # "not a git repository" failure is what proves the path was *expanded*: taken
    # literally, `~/somewhere` would not exist at all and the message would be
    # "No directory was found" instead.
    with pytest.raises(GitRepositoryError, match="is not a git repository"):
        validate_repository_path("~/somewhere")


@requires_git
def test_a_path_that_does_not_exist_names_the_path_the_user_gave(tmp_path: Path):
    """A missing directory is refused with the path, not a bare OS error.

    The message has to carry the path back: a user who mistyped a directory needs
    to be told which one, and ``FileNotFoundError`` carries it only as a traceback
    nobody should be shown.
    """
    missing = tmp_path / "not-here"

    with pytest.raises(GitRepositoryError, match="No directory was found"):
        validate_repository_path(missing)


@requires_git
def test_a_file_is_refused_before_anything_is_run_in_its_place(tmp_path: Path):
    """A regular file is a file, not a repository directory.

    Checked before git runs, because ``cwd=<a file>`` is an OS error that would
    surface as a confusing "could not start" rather than as "that is a file".
    """
    target = tmp_path / "a-file.txt"
    target.write_text("not a repository\n", encoding="utf-8")

    with pytest.raises(GitRepositoryError, match="is a file, not a repository directory"):
        validate_repository_path(target)


@requires_git
def test_a_directory_that_is_not_a_repository_is_refused(tmp_path: Path):
    """A directory with no ``.git`` entry is refused by name.

    The check is on the work tree rather than on running git, so an ordinary
    folder cannot be registered and then fail every scan.
    """
    plain = tmp_path / "just-a-folder"
    plain.mkdir()

    with pytest.raises(GitRepositoryError, match=r"no \.git entry was found"):
        validate_repository_path(plain)


@requires_git
def test_a_bare_repository_with_no_commits_is_valid_rather_than_an_error(tmp_path: Path):
    """``git init`` with nothing committed is a repository and must be accepted.

    This is the case §5 of the contracts names. Refusing it would mean the very
    first thing a user does with this feature — register the directory they just
    initialised — is told their new project does not exist. The scan of it comes
    back empty in the next test rather than raising here.
    """
    empty = tmp_path / "brand-new"
    empty.mkdir()
    asyncio.run(run_git(empty, "init", "-b", "main", "."))

    assert validate_repository_path(empty) == empty.resolve()


# ---------------------------------------------------------------------------
# The empty repository scans successfully
# ---------------------------------------------------------------------------


@requires_git
async def test_a_repository_with_no_commits_scans_to_an_empty_snapshot(tmp_path: Path):
    """The empty repository is a successful scan, not a failure.

    ``git log`` exits non-zero in a repository with no commits. A reader that
    treats that as an error reports the user's brand-new project as broken, so
    the commit pass is skipped instead and the snapshot comes back with nothing in
    it. ``default_branch`` is still ``main``: ``symbolic-ref HEAD`` answers even
    with no commits, and a user who has run ``git init -b main`` should see that.
    """
    empty = tmp_path / "brand-new"
    empty.mkdir()
    await run_git(empty, "init", "-b", "main", ".")
    await run_git(empty, "config", "user.email", "test@example.invalid")
    await run_git(empty, "config", "user.name", "Test Person")

    snapshot = await read_repository(empty)

    assert snapshot.commits == ()
    assert snapshot.branches == ()
    assert snapshot.first_commit_at is None
    assert snapshot.latest_commit_at is None
    assert snapshot.current_branch is None
    assert snapshot.default_branch == "main"
    assert snapshot.tracked_file_count == 0
    assert snapshot.primary_language is None
    assert snapshot.working_tree_dirty is False


# ---------------------------------------------------------------------------
# Reading a populated repository
# ---------------------------------------------------------------------------


@requires_git
async def test_a_two_commit_repository_reports_both_commits_newest_first(repo: Path):
    """The commit list is newest first, with the fields the scanner promised.

    Two commits, both inside the window, so the count is 2. ``--no-merges``
    excludes nothing here because there are no merges.
    """
    snapshot = await read_repository(repo)

    assert len(snapshot.commits) == 2
    assert snapshot.commits[0].message == "Extend the helper"
    assert snapshot.commits[1].message == "Add the entry point and a helper"
    assert snapshot.commits[0].author_name == "Test Person"
    assert snapshot.commits[0].author_email == "test@example.invalid"
    assert all(commit.committed_at.tzinfo is not None for commit in snapshot.commits)


@requires_git
async def test_each_commit_carries_its_own_line_counts(repo: Path):
    """The first commit changes 3 lines across 2 files; the second 2 lines in 1.

    The first commit writes three one-line files, so ``3 insertions`` across
    ``3 files changed``. The second appends one line to ``util.py``, so
    ``1 insertion`` across ``1 file changed``. Both figures come from what this
    test wrote, so a change to how the stats are counted shows up here as a wrong
    number rather than as a moved baseline.
    """
    snapshot = await read_repository(repo)
    by_message = {commit.message: commit for commit in snapshot.commits}

    first = by_message["Add the entry point and a helper"]
    assert (first.additions, first.deletions, first.files_changed) == (3, 0, 3)

    second = by_message["Extend the helper"]
    assert (second.additions, second.deletions, second.files_changed) == (1, 0, 1)


@requires_git
async def test_the_history_span_covers_the_whole_history_not_just_the_window(repo: Path):
    """``first_commit_at`` is 10 days before the reference and ``latest`` 3 days.

    Both ends come from a ``--since``-free query. An incremental scan sees only
    the commits it asked for, and taking the span from those would walk
    ``latest_commit_at`` backwards on every re-scan until the repository reported
    its newest commit as months old.
    """
    reference = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    snapshot = await read_repository(repo)

    assert snapshot.latest_commit_at == reference - timedelta(days=3)
    assert snapshot.first_commit_at == reference - timedelta(days=10)
    assert snapshot.first_commit_at < snapshot.latest_commit_at


@requires_git
async def test_tracked_files_and_languages_come_from_ls_files_not_the_working_tree(
    repo: Path,
):
    """Three tracked files, two languages, and the untracked file ignored.

    ``notes.txt`` is on disk and deliberately not staged, so counting the working
    tree instead of the index would report 4 files and a third language. Python
    has two tracked files and TypeScript one, so Python is primary.
    """
    snapshot = await read_repository(repo)

    assert snapshot.tracked_file_count == 3
    assert snapshot.language_distribution == (("Python", 2), ("TypeScript", 1))
    assert snapshot.primary_language == "Python"
    assert (repo / "notes.txt").exists()


@requires_git
async def test_an_untracked_file_makes_the_working_tree_dirty(repo: Path):
    """One porcelain line is one pending change, and ``dirty`` is true.

    Counted from the machine format rather than parsed for meaning: the scan
    reports *how many* changes are pending, and every interpretation of a
    porcelain line this module could write would be a claim about the user's
    uncommitted work that the record does not support.
    """
    snapshot = await read_repository(repo)

    assert snapshot.working_tree_dirty is True
    assert snapshot.working_tree_changes == 1


@requires_git
async def test_a_clean_working_tree_is_not_dirty(tmp_path: Path):
    """A committed-only repository reports zero pending changes.

    The counterpart to the previous test, because a scanner that always reported
    "1 change" would be indistinguishable from one that cannot read the status at
    all.
    """
    clean = tmp_path / "clean"
    clean.mkdir()
    await run_git(clean, "init", "-b", "main", ".")
    await run_git(clean, "config", "user.email", "test@example.invalid")
    await run_git(clean, "config", "user.name", "Test Person")
    (clean / "a.py").write_text("x = 1\n", encoding="utf-8")
    await run_git(clean, "add", "-A")
    await run_git(clean, "commit", "-m", "Only commit", "--no-gpg-sign")

    snapshot = await read_repository(clean)

    assert snapshot.working_tree_dirty is False
    assert snapshot.working_tree_changes == 0


@requires_git
async def test_a_detached_head_is_none_rather_than_an_error(repo: Path):
    """Checking out a commit is a normal thing to do, not a broken repository.

    ``rev-parse --abbrev-ref HEAD`` returns the literal string ``HEAD`` when the
    head is detached, so it is translated to ``None``. The scan still succeeds:
    the commits are all still readable and they are what the product is about.
    """
    await run_git(repo, "checkout", "--detach", "HEAD")

    snapshot = await read_repository(repo)

    assert snapshot.current_branch is None
    assert len(snapshot.commits) == 2
    # `symbolic-ref HEAD` is the fallback for the default branch, and a detached
    # head has no symbolic HEAD to read — so the default is genuinely unknown
    # here and `None` says so. Reporting the stale "main" would be a guess.
    assert snapshot.default_branch is None


# ---------------------------------------------------------------------------
# Incremental scanning
# ---------------------------------------------------------------------------


@requires_git
async def test_scanning_with_a_since_past_the_newest_commit_transfers_nothing(repo: Path):
    """A re-scan of an unchanged repository finds no new commits.

    This is what makes re-scanning idempotent. The service passes the stored
    ``latest_commit_at``; a repository with nothing new must produce zero commits,
    which is what lets the scan upsert nothing rather than duplicating a row per
    commit per scan.
    """
    full = await read_repository(repo)
    assert full.latest_commit_at is not None

    # One second *past* the newest commit, because git's `--since` is inclusive:
    # passing the newest commit's own instant returns that commit, which is the
    # other half of the boundary rule and is asserted separately below.
    incremental = await read_repository(repo, since=full.latest_commit_at + timedelta(seconds=1))

    assert incremental.commits == ()


@requires_git
async def test_scanning_with_a_since_before_the_newest_commit_transfers_only_that_one(
    repo: Path,
):
    """A commit after the ``since`` is transferred; earlier ones are not.

    The cutoff is 5 days before the reference, which is after the first commit
    (10 days) and before the second (3 days). One commit crosses it, so exactly
    one is transferred.
    """
    reference = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)

    incremental = await read_repository(repo, since=reference - timedelta(days=5))

    assert len(incremental.commits) == 1
    assert incremental.commits[0].message == "Extend the helper"


@requires_git
async def test_an_incremental_scan_does_not_move_the_history_span_backwards(repo: Path):
    """The span still covers the whole history after an incremental scan.

    The trap: the commit pass is filtered by ``--since``, so taking the span from
    its results would report ``first == latest`` for a re-scan of a repository
    whose new commits all landed on one day — and the stored ``first_commit_at``
    would be overwritten with a recent date, destroying the repository's age.
    """
    reference = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)

    incremental = await read_repository(repo, since=reference - timedelta(days=5))

    assert incremental.first_commit_at == reference - timedelta(days=10)
    assert incremental.latest_commit_at == reference - timedelta(days=3)


@requires_git
async def test_the_limit_caps_the_commits_returned(repo: Path):
    """``limit=1`` returns the newest commit only.

    The limit is applied by git's own ``-n``, which is what keeps a repository
    with a decade of history from being read into memory by one request.
    """
    snapshot = await read_repository(repo, limit=1)

    assert len(snapshot.commits) == 1
    assert snapshot.commits[0].message == "Extend the helper"


@requires_git
async def test_the_commits_the_ceiling_leaves_out_are_never_returned_by_any_scan(
    tmp_path: Path,
):
    """A repository larger than the ceiling records its newest commits, permanently.

    Expected figures, derived from the fixture rather than from a run: five
    commits are made at 10, 8, 6, 4 and 2 days before a fixed reference. A scan
    with ``limit=2`` therefore returns exactly two commits — the ones at 2 and 4
    days, newest first — and the three older ones are in neither it nor any
    scan that follows it. The follow-up scan is given ``since`` at the stored
    high-water mark, which is that newest commit's own instant, so it is asking
    for commits *at or after* 2 days ago: git's ``--since`` is inclusive, so the
    newest commit itself comes back and the three older ones do not. One row,
    not four.
    This is the honest consequence of a ceiling applied to a newest-first log,
    and it is asserted rather than left to a comment. The alternative reading —
    that the next scan picks the rest up — is not how ``-n`` behaves and never
    was: the commits below the ceiling are older than the mark, so no later
    scan asks for them and a full re-scan asks for the same newest two.
    """
    reference = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    root = tmp_path / "oversized"
    await _init_async(root)
    for days in (10, 8, 6, 4, 2):
        await _commit_at_async(
            root,
            f"Step at {days} days",
            name="a.py",
            body=f"x = {days}\n",
            when=reference - timedelta(days=days),
        )

    everything = await read_repository(root)
    below_the_ceiling = {commit.commit_hash for commit in everything.commits[2:]}
    assert len(everything.commits) == 5

    capped = await read_repository(root, limit=2)

    assert [commit.committed_at for commit in capped.commits] == [
        everything.commits[0].committed_at,
        everything.commits[1].committed_at,
    ]

    incremental = await read_repository(root, since=capped.commits[0].committed_at, limit=2)

    # `--since` is inclusive, so the newest commit comes back — and the upsert
    # that stores it is a no-op. What does not come back is anything older.
    assert [commit.commit_hash for commit in incremental.commits] == [
        capped.commits[0].commit_hash
    ], "one row, the newest commit itself, and not the three the ceiling left out"
    assert below_the_ceiling.isdisjoint(commit.commit_hash for commit in capped.commits)
    full = await read_repository(root, limit=MAX_COMMITS_PER_SCAN)
    assert below_the_ceiling.issubset({commit.commit_hash for commit in full.commits}), (
        "no ceiling, no truncation: all five are there, which is what makes the "
        "ceiling a statement about what NEXUS records rather than about the "
        "repository's history"
    )


# ---------------------------------------------------------------------------
# Branches
# ---------------------------------------------------------------------------


@requires_git
async def test_a_branch_carries_its_tip_hash_and_its_tip_date(repo: Path):
    """The listing reads ``refs/heads`` with the name, tip and tip date.

    ``last_committed_at`` is the *tip's* date and not a branch creation date:
    git does not record when a branch was created, and deriving one from the tip
    would let a two-year-old branch read as brand new in the repository-growth
    metric.
    """
    reference = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    snapshot = await read_repository(repo)

    assert len(snapshot.branches) == 1
    branch = snapshot.branches[0]
    assert branch.name == "main"
    assert branch.head_commit_hash == snapshot.commits[0].commit_hash
    assert branch.last_committed_at == reference - timedelta(days=3)


@requires_git
async def test_every_commit_on_a_single_branch_repository_is_attributed_to_it(repo: Path):
    """Both commits are attributed to ``main``.

    Attribution is best-effort by contract, but on the ordinary case it must
    actually work: a scanner that left ``branch`` null on every commit in a
    one-branch repository would be discarding information git had to give.
    """
    snapshot = await read_repository(repo)

    assert {commit.branch for commit in snapshot.commits} == {"main"}


@requires_git
async def test_a_commit_reachable_from_no_branch_is_attributed_to_nothing(
    tmp_path: Path,
):
    """A detached commit reachable from no ref yields ``None``, not a guess.

    After checking out a commit and detaching, the tip is on no branch — unless
    another branch still reaches it, which this fixture avoids by branching first.
    The scanner must return ``None`` here rather than inventing the nearest branch,
    and the scan must still succeed.
    """
    root = tmp_path / "orphan"
    root.mkdir()
    await run_git(root, "init", "-b", "main", ".")
    await run_git(root, "config", "user.email", "test@example.invalid")
    await run_git(root, "config", "user.name", "Test Person")
    for subject, name in (("First", "a.py"), ("Second", "b.py")):
        (root / name).write_text("x = 1\n", encoding="utf-8")
        await run_git(root, "add", "-A")
        await run_git(root, "commit", "-m", subject, "--no-gpg-sign")
    # Detach onto the *first* commit, then delete `main` so nothing reaches it.
    await run_git(root, "checkout", "--detach", "HEAD~1")
    await run_git(root, "branch", "-D", "main")

    snapshot = await read_repository(root)

    assert snapshot.branches == ()
    assert snapshot.current_branch is None
    assert snapshot.commits  # the commit is still readable
    assert all(commit.branch is None for commit in snapshot.commits)


@requires_git
async def test_branch_attribution_reads_only_the_commits_the_window_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """An incremental scan attributes its commits without walking the history.

    Expected figures, derived from the fixture rather than from a run: five
    commits are made at 10, 8, 6, 4 and 2 days before a fixed reference — the
    last two on a second branch — and the scan is given ``since`` three days
    before the reference with a ceiling of ten. Exactly one commit falls inside
    that window (the one at 2 days), so the attribution pass must read exactly
    one commit. Without ``-n`` and ``--since`` on that pass it reads all five,
    which is the whole incremental-scan claim undone: a re-scan of a repository
    with years of history would cost more than the first one, every time.

    The commit is on the second branch on purpose. A windowed walk that could
    not reach a branch's own tip would attribute nothing, and ``None`` is a
    legal-looking answer, so the test would pass with the fix made too narrow.
    The walk is also recorded rather than replaced: git still runs, so the
    arguments asserted here are arguments git accepted.
    """
    reference = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    since = reference - timedelta(days=3)
    root = tmp_path / "windowed"
    await _init_async(root)

    for days in (10, 8, 6):
        await _commit_at_async(
            root,
            f"Main step {days}",
            name="a.py",
            body=f"x = {days}\n",
            when=reference - timedelta(days=days),
        )
    await run_git(root, "checkout", "-b", "feature")
    for days in (4, 2):
        await _commit_at_async(
            root,
            f"Feature step {days}",
            name="b.py",
            body=f"y = {days}\n",
            when=reference - timedelta(days=days),
        )

    walked: dict[str, object] = {}
    real_run_git = git_engine.run_git

    async def recording_run_git(repo_path, *args, **kwargs):
        output = await real_run_git(repo_path, *args, **kwargs)
        if "--source" in args:
            walked["args"] = args
            walked["commits"] = sum(1 for line in output.splitlines() if line.strip())
        return output

    monkeypatch.setattr(git_engine, "run_git", recording_run_git)

    snapshot = await read_repository(root, since=since, limit=10)

    assert [commit.message for commit in snapshot.commits] == ["Feature step 2"]
    assert snapshot.commits[0].branch == "feature"
    assert walked["commits"] == 1, "one commit is inside the window, not five"
    args = list(walked["args"])
    assert args[args.index("-n") + 1] == "10", "the attribution pass takes the same ceiling"
    assert f"--since={since.isoformat()}" in args, "the attribution pass takes the same window"


# ---------------------------------------------------------------------------
# Failure containment
# ---------------------------------------------------------------------------


@requires_git
async def test_a_failing_git_command_raises_git_command_error_with_its_stderr(repo: Path):
    """A non-zero exit becomes one exception carrying git's own diagnosis.

    The reader of ``git log`` in an empty repository hits exactly this, and the
    service turns the message into a human sentence on a ``GitScanRun`` row.
    """
    with pytest.raises(GitCommandError) as caught:
        await run_git(repo, "cat-file", "-p", "definitely-not-a-ref")

    message = str(caught.value)
    assert "definitely-not-a-ref" in message
    assert "Traceback" not in message
    assert "\n" not in message


@requires_git
async def test_a_timeout_kills_the_process_and_says_so(repo: Path):
    """A command that outstays its budget raises a sentence, not a hang.

    The timeout is the one guarantee that keeps a stopped network share from
    holding an API request open forever. A tenth of a second against a real git
    invocation is enough to trip it while still being long enough for the process
    to start.
    """
    with pytest.raises(GitCommandError, match="timed out"):
        await run_git(repo, "log", "--all", timeout=0.000001)


@requires_git
async def test_a_timeout_error_names_the_budget_it_exceeded(repo: Path):
    """The message says how long it waited, so an operator can retune the setting.

    ``developer_git_timeout_seconds`` is configurable, and a scan that fails
    without saying which budget it blew past gives that setting nothing to tune
    against.
    """
    with pytest.raises(GitCommandError, match="seconds"):
        await run_git(repo, "log", "--all", timeout=0.000001)


@requires_git
async def test_the_default_budget_is_thirty_seconds_and_the_commit_cap_is_two_thousand():
    """The frozen safety constants have the values the contracts name.

    Asserted because they are the contract: a timeout that silently became five
    minutes would be a much larger blast radius than anyone reviewing the diff
    would notice.
    """
    assert DEFAULT_GIT_TIMEOUT_SECONDS == 30
    assert MAX_COMMITS_PER_SCAN == 2000
    assert MAX_SCAN_OUTPUT_BYTES == 16 * 1024 * 1024


@requires_git
async def test_the_output_ceiling_is_a_scan_error_rather_than_a_memory_exhaustion(
    repo: Path,
):
    """Output past the ceiling is refused rather than buffered.

    ``git ls-files`` is run against a repository with a real file in it while the
    ceiling is temporarily lowered to a handful of bytes, so the refusal path is
    exercised without writing a repository large enough to need it. The message
    names the limit, because "the scan produced too much output" with no figure
    tells an operator nothing.
    """
    import app.services.developer.git as engine

    original = engine.MAX_SCAN_OUTPUT_BYTES
    engine.MAX_SCAN_OUTPUT_BYTES = 8
    try:
        with pytest.raises(GitCommandError, match="more than 8 bytes"):
            await run_git(repo, "ls-files")
    finally:
        engine.MAX_SCAN_OUTPUT_BYTES = original


@requires_git
async def test_a_repository_deleted_between_validation_and_the_scan_is_an_error_not_a_crash(
    tmp_path: Path,
):
    """A repository that stops being one between validation and the scan fails cleanly.

    Reading a local path means the filesystem can change underneath the scan —
    a removable drive, a deleted project, a network share that dropped, a ``.git``
    directory someone removed. Validation passes, and then the scan finds nothing
    to read. The outcome must be one of the two declared exceptions, never an
    unhandled OS error, because an unhandled one is the 500 that takes the page
    down.

    The ``.git`` entry is what is removed rather than the whole directory: Windows
    marks the files inside ``.git`` read-only, so deleting the directory itself is
    a permission dance that would say more about the filesystem than about the
    scanner. Losing ``.git`` is the same test with none of that.
    """
    doomed = tmp_path / "doomed"
    doomed.mkdir()
    await run_git(doomed, "init", "-b", "main", ".")

    assert validate_repository_path(doomed) == doomed.resolve()

    import shutil

    shutil.rmtree(doomed / ".git")

    with pytest.raises(GitRepositoryError, match=r"no \.git entry was found"):
        await read_repository(doomed)


@requires_git
async def test_a_path_given_as_a_string_is_accepted(tmp_path: Path, repo: Path):
    """``str`` and ``Path`` are both accepted, because callers hold both.

    The route layer reads the path out of a JSON body as a string and the service
    may hold a ``Path``; refusing one of them would push a conversion into the
    caller and make the signature a trap.
    """
    from_str = await read_repository(str(repo))
    from_path = await read_repository(repo)

    assert from_str.path == from_path.path
    assert len(from_str.commits) == len(from_path.commits)


# ---------------------------------------------------------------------------
# stderr sanitising
# ---------------------------------------------------------------------------


def test_the_home_directory_in_a_git_error_is_replaced_with_a_tilde(tmp_path: Path):
    """An absolute path under the home directory becomes ``~/...``.

    This is the leak worth preventing: git quotes absolute paths in its
    diagnostics without the user having mentioned any of them, and those paths
    carry the server operator's username into a response body and a log a user
    reads.
    """
    home = tmp_path / "home"
    message = f"fatal: could not read from '{home / 'projects' / 'billing'}'"

    cleaned = sanitize_git_message(message, home=home)

    assert str(home) not in cleaned
    assert cleaned.startswith("fatal: could not read from '~")
    # The tail of the path survives: a user has to be able to tell *which*
    # repository the message is about.
    assert cleaned.endswith("projects" + os.sep + "billing'")


def test_a_path_outside_the_home_directory_is_left_readable(tmp_path: Path):
    """A repository path the user named is *not* stripped.

    A user who registered ``/srv/repos/billing`` needs to be told about
    ``/srv/repos/billing`` when it cannot be read. Reducing every absolute path
    to its last segment would make the error unactionable, which is a different
    failure from the leak above and not one worth trading for it.
    """
    message = "fatal: not a git repository: '/srv/repos/billing'"

    assert "/srv/repos/billing" in sanitize_git_message(message, home=tmp_path)


def test_a_multi_line_git_message_is_flattened_onto_one_line(tmp_path: Path):
    """Git wraps its diagnostics to the terminal width; a response field must not.

    A newline inside a JSON string renders as nothing at all in most clients, so
    the user would see half a sentence and no path.
    """
    message = "fatal: unable to access\nhttps://example.invalid/repo.git\n"

    cleaned = sanitize_git_message(message, home=tmp_path)

    assert "\n" not in cleaned
    assert cleaned.startswith("fatal: unable to access https://example.invalid/repo.git")


def test_a_very_long_git_message_is_truncated(tmp_path: Path):
    """Eight kilobytes of scanner output is not a reason to return it all.

    The limit is the one that keeps a pathological repository from putting a huge
    scanner dump into a response body.
    """
    cleaned = sanitize_git_message("x" * 40_000, home=tmp_path)

    assert len(cleaned) <= 8 * 1024
    assert cleaned.endswith("…")


def test_an_empty_git_message_sanitises_to_an_empty_string(tmp_path: Path):
    """No stderr is no sentence, and the caller falls back to its own wording.

    A command that fails silently still has to produce a usable error, so
    :func:`run_git` supplies a status-based message when the sanitised text comes
    back empty.
    """
    assert sanitize_git_message("", home=tmp_path) == ""


def test_no_python_traceback_can_reach_a_user_through_a_git_failure(repo: Path) -> None:
    """The failure message is a sentence, and git never produces a traceback.

    Asserted structurally: the reader raises the two declared exception types, so
    the service that catches them renders ``str(exc)`` and nothing else. There is
    no third error type for it to miss.
    """
    assert issubclass(GitCommandError, Exception)
    assert issubclass(GitRepositoryError, Exception)
    assert not issubclass(GitCommandError, GitRepositoryError)
    assert not issubclass(GitRepositoryError, GitCommandError)


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("app/main.py", "Python"),
        ("web/page.tsx", "TypeScript"),
        ("web/page.ts", "TypeScript"),
        ("src/index.jsx", "JavaScript"),
        ("src/index.js", "JavaScript"),
        ("db/schema.sql", "SQL"),
        ("cmd/main.go", "Go"),
        ("lib/lib.rs", "Rust"),
        ("app/Main.PY", "Python"),
        ("web/style.css", "CSS"),
        ("ops/deploy.sh", "Shell"),
        ("api/openapi.yaml", "YAML"),
    ],
)
def test_a_known_extension_maps_to_its_language(path: str, expected: str):
    """The map covers the extensions the contracts list, case-insensitively.

    The uppercase case is asserted because a filesystem that preserves case can
    hold ``Main.PY``, and a lowercase-only lookup would silently drop it from the
    distribution — making a repository look less Python than it is.
    """
    assert detect_language(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "notes.txt",
        "Makefile",
        ".gitignore",
        "archive.tar.gz",
        "no-extension-here",
        "",
    ],
)
def test_an_unknown_extension_is_not_counted_at_all(path: str):
    """An unknown extension yields ``None``, not an "Other" bucket.

    "Other" would be a number on a chart that nobody can act on and that reads as
    though it were a language. Not counting it is a smaller, truthful answer.
    """
    assert detect_language(path) is None


def test_the_language_distribution_is_ordered_by_count_then_alphabetically():
    """Most common first, ties broken by name.

    ``Counter.most_common`` preserves insertion order among ties, which would make
    the distribution depend on the order ``ls-files`` happened to print — and
    therefore change between machines. Sorting the ties is what makes a re-scan's
    diff meaningful.
    """
    paths = ["b.py", "a.go", "c.py", "d.rs", "e.py"]

    assert language_distribution(paths) == (("Python", 3), ("Go", 1), ("Rust", 1))


def test_a_repository_with_no_recognised_files_has_no_language_distribution():
    """Nothing matching means no distribution, and no primary language.

    ``primary_language`` is ``None`` rather than "Other", which is the same
    distinction as the metric's ``available=False``: absence of measurement, not a
    measurement of absence.
    """
    assert language_distribution(["notes.txt", "Makefile"]) == ()
    assert language_distribution([]) == ()


def test_the_language_map_holds_no_extension_mapping_to_two_languages():
    """The map is a plain extension-to-language table with no aliases or conflicts.

    ``TypeScript`` appears twice (``ts`` and ``tsx``), which is the map doing its
    job; what must not happen is two *extensions* sharing a key, which is
    impossible in a dict and is asserted here as a reminder that the values are
    free to repeat while the keys are not.
    """
    assert len(LANGUAGE_BY_EXTENSION) == len(set(LANGUAGE_BY_EXTENSION))
    assert LANGUAGE_BY_EXTENSION["tsx"] == LANGUAGE_BY_EXTENSION["ts"] == "TypeScript"
    assert LANGUAGE_BY_EXTENSION["c"] == LANGUAGE_BY_EXTENSION["h"] == "C"


# ---------------------------------------------------------------------------
# The path allowlist
# ---------------------------------------------------------------------------


@requires_git
def test_a_path_under_an_allowlist_root_is_accepted(repo: Path):
    """The repository's own directory is a permitted root.

    Equality and containment are both permitted, because an allowlist entry is
    usually the directory holding the repository rather than the repository.
    """
    assert validate_repository_path(repo, allowlist=[repo.parent]) == repo.resolve()
    assert validate_repository_path(repo, allowlist=[repo]) == repo.resolve()


@requires_git
def test_a_path_outside_every_allowlist_root_is_refused(repo: Path, tmp_path: Path):
    """A repository under a directory the operator did not list is refused.

    This is the control that stops the feature from reading any directory on the
    machine. The message names the path and the restriction rather than saying
    only "forbidden", because a user registering a local repository needs to know
    *where* the server is willing to look.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    with pytest.raises(GitRepositoryError, match="outside the directories"):
        validate_repository_path(repo, allowlist=[elsewhere])


@requires_git
def test_a_traversal_out_of_the_allowlist_is_refused(repo: Path):
    """``..`` out of a permitted root does not escape the check.

    The check runs on the *resolved* path, so a caller cannot reach ``/etc`` by
    registering ``/allowed/../../../etc``. Pinned because checking the string
    before resolving it is the natural mistake to make, and it is invisible on
    any machine where the paths happen to be canonical already.
    """
    with pytest.raises(GitRepositoryError, match="outside the directories"):
        validate_repository_path(repo, allowlist=[repo.parent / "somewhere-else"])


def test_an_empty_allowlist_means_no_restriction() -> None:
    """The documented default: every readable absolute path is allowed.

    A local-first application running on the user's own machine should not have to
    configure anything to register their own project, and an empty string must not
    accidentally mean "nothing is allowed" — which would be the more surprising
    reading of the same value.
    """
    assert parse_path_allowlist("") == ()
    assert parse_path_allowlist("   ") == ()


def test_a_comma_separated_allowlist_parses_into_roots(tmp_path: Path) -> None:
    """Two entries, whitespace stripped, blank entries dropped.

    The value is one settings string, so the parsing has to tolerate the trailing
    comma a hand-edited ``.env`` inevitably picks up. The roots are built from
    ``tmp_path`` rather than written as POSIX literals, because on Windows a
    leading ``/`` resolves drive-relatively and the assertion would be testing the
    platform's path rules instead of the parsing.
    """
    first = tmp_path / "repos"
    second = tmp_path / "code"
    first.mkdir()
    second.mkdir()

    roots = parse_path_allowlist(f"  {first} , {second} ,")

    assert roots == (first.resolve(), second.resolve())


@requires_git
def test_an_allowlist_entry_is_never_split_on_a_comma_inside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A root whose own name contains a comma still permits the repository in it.

    The comma is the settings value's separator, so splitting the *value* on it
    is right. It is not a separator inside an entry: a caller that hands
    :func:`validate_repository_path` the roots themselves — which is what the
    service does, having already parsed the setting — used to have them joined
    back with commas and re-split. A root called ``code,old`` came back as two,
    the prefix before the comma and the fragment after it resolved against the
    process's working directory. The operator's root stopped permitting itself,
    and a directory nobody configured started permitting something.
    """
    root = tmp_path / "code,old"
    _init(root, monkeypatch)

    assert validate_repository_path(root, allowlist=[root]) == root.resolve()

    with pytest.raises(GitRepositoryError, match="outside the directories"):
        validate_repository_path(root, allowlist=[tmp_path / "somewhere-else"])


def test_an_allowlist_root_that_does_not_exist_yet_is_still_usable(tmp_path: Path) -> None:
    """A root that has not been created is normalised, not discarded.

    Resolution here is non-strict on purpose: a deployment can name the directory
    its repositories will live in before anyone has created one. Requiring the
    directory to exist would turn a configuration choice into a startup-ordering
    problem, and a root dropped for being absent would silently *widen* the set of
    paths that get refused the next time the deployment is restarted.
    """
    future = tmp_path / "repos" / "not-created-yet"

    assert parse_path_allowlist(str(future)) == (future.resolve(),)


@requires_git
def test_an_allowlist_whose_every_entry_failed_to_parse_permits_nothing(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configured allowlist that resolved to no root denies every path.

    ``parse_path_allowlist`` drops an entry it cannot resolve rather than
    failing the whole setting, which is right for one bad entry — dropping it
    only ever permits *less*. But when the last entry goes too, what remains
    permits nothing, and the check that consumed the empty result is skipped
    entirely: the operator who wrote an allowlist gets an open door because
    every entry in it was unusable. So a configured list that produced no roots
    is a list that permits no paths, with the same sentence a path outside the
    roots gets.

    The failure itself is simulated. A malformed entry is not portable to
    provoke — on Windows every unusable string still resolves to something —
    and what is under test is this module's branch, not the filesystem's. The
    replacement raises only for the marked entry, so the repository under test
    resolves through the real ``Path.resolve``.
    """
    original_resolve = Path.resolve

    def exploding_resolve(self: Path, *args, **kwargs):
        if "unresolvable-entry" in str(self):
            raise OSError("this entry could not be resolved")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", exploding_resolve)

    assert parse_path_allowlist("unresolvable-entry") == ()
    with pytest.raises(GitRepositoryError, match="outside the directories"):
        validate_repository_path(repo, allowlist=["unresolvable-entry"])


@requires_git
def test_an_empty_allowlist_sequence_is_a_configured_list_not_an_absent_one(repo: Path) -> None:
    """``[]`` is a caller that supplied an allowlist and named no roots in it.

    ``None`` is the documented "no allowlist configured" and stays unrestricted.
    An empty sequence is not the same statement: something was configured and
    it permits nothing, so every path is outside it. Collapsing the two is how a
    deployment that meant to lock itself down ends up reading any directory on
    the machine.
    """
    with pytest.raises(GitRepositoryError, match="outside the directories"):
        validate_repository_path(repo, allowlist=[])
    assert validate_repository_path(repo, allowlist=None) == repo.resolve()


@requires_git
async def test_reading_a_repository_enforces_the_allowlist_the_scan_was_given(
    tmp_path: Path,
) -> None:
    """A scan refuses a repository outside the allowlist it was handed.

    Registration is where an allowlist is enforced today, and registration is
    the wrong *only* place for it: the path a scan reads is a stored string
    resolved days later, and a stored string is not a promise about what it
    resolves to now. So the engine takes the list as an argument and applies it
    on the way in, which means a scan of a path that is no longer permitted
    fails as a sentence instead of quietly reading somewhere else.

    The control below is the same read with no allowlist configured, which is
    the default and must keep working: the fix is that the list is honoured
    when there is one, not that scanning is restricted always.
    """
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    await _init_async(outside)

    with pytest.raises(GitRepositoryError, match="outside the directories"):
        await read_repository(outside, allowlist=[allowed])

    assert (await read_repository(outside)).tracked_file_count == 0


@requires_git
async def test_a_registered_path_that_became_a_symlink_elsewhere_is_refused_at_scan_time(
    tmp_path: Path,
) -> None:
    """A repository that moved out of the allowlist by symlink is not read.

    The registered directory is deleted and replaced by a link pointing at a
    real repository outside the permitted root. The stored string is unchanged
    and still exists, so every check that reads the string rather than what it
    resolves to would pass it — and the scan would then read, in full, a
    directory the operator never allowed. The refusal is the same sentence a
    registration outside the root gets.

    Skipped where the platform will not create a directory symlink: the
    behaviour under test needs the path to resolve somewhere other than where it
    was, and a machine that cannot express that cannot demonstrate it. The test
    above pins the same check without needing the symlink.
    """
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    registered = allowed / "demo"
    await _init_async(registered)
    assert (await read_repository(registered, allowlist=[allowed])).path == registered.resolve()

    elsewhere = tmp_path / "elsewhere"
    await _init_async(elsewhere)
    try:
        shutil.rmtree(registered)
        registered.symlink_to(elsewhere, target_is_directory=True)
    except (OSError, NotImplementedError) as error:  # pragma: no cover - platform
        pytest.skip(f"this platform will not create a directory symlink: {error}")

    with pytest.raises(GitRepositoryError, match="outside the directories"):
        await read_repository(registered, allowlist=[allowed])
    assert (await read_repository(registered)).path == elsewhere.resolve(), (
        "without an allowlist the same path is read, which is why the configured "
        "one has to be passed through the scan"
    )


# ---------------------------------------------------------------------------
# The subprocess contract
# ---------------------------------------------------------------------------


def _module_tree() -> ast.Module:
    """The parsed source of :mod:`app.services.developer.git`.

    Parsed rather than read as text because the interesting assertions are about
    *syntax* — is there a ``shell=True`` keyword argument — and a substring search
    cannot tell an argument from the module docstring that explains why the
    argument is a good idea.
    """
    from app.services.developer import git

    assert git.__file__ is not None
    return ast.parse(Path(git.__file__).read_text(encoding="utf-8"))


def _called_names(tree: ast.Module) -> set[str]:
    """Every function name that appears anywhere in a call in the module."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def test_no_call_in_this_module_builds_a_shell_command_string() -> None:
    """No call anywhere in the module passes ``shell=True``.

    A repository path and a commit subject are attacker-influenced text on a
    multi-tenant server. Interpolating either into a shell string is the classic
    way a read-only feature becomes remote code execution; with an argument list,
    a path containing ``; rm -rf ~`` is just a filename.

    Asserted over the parsed module rather than by review, because a review is a
    snapshot and this is a property of the code as it stands.
    """
    tree = _module_tree()

    shell_arguments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and any(
            keyword.arg == "shell" and ast.literal_eval(keyword.value) is True
            for keyword in node.keywords
        )
    ]

    assert shell_arguments == []
    assert {"create_subprocess_shell", "system", "popen", "Popen"} & _called_names(tree) == set()


def test_the_module_starts_git_through_exactly_one_subprocess_primitive() -> None:
    """One way in, and it is the argument-list one.

    Every git invocation goes through :func:`run_git`, so this is where the whole
    surface is pinned: a second call site added later would be a subprocess
    launched without the timeout, the output ceiling or the stderr sanitiser that
    make this module safe.
    """
    tree = _module_tree()
    names = _called_names(tree)

    assert "create_subprocess_exec" in names
    for banned in ("Popen", "call", "check_call", "check_output", "run", "getoutput"):
        assert banned not in names, banned


def test_the_module_never_imports_a_git_library_or_a_shell() -> None:
    """No GitPython, and no ``subprocess`` import at all.

    The dependency set is frozen and the environment has no network, so a GitPython
    import would not even install. And ``asyncio.create_subprocess_exec`` is the
    only subprocess primitive the module is allowed to reach for; importing
    ``subprocess`` alongside it is how the ``shell=True`` regression gets in.
    """
    imported: set[str] = set()
    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])

    assert "subprocess" not in imported
    assert not any(name.lower().startswith("git") for name in imported)


def test_the_git_environment_never_waits_for_a_credential_prompt() -> None:
    """``GIT_TERMINAL_PROMPT`` is off, so a private URL fails instead of blocking.

    A repository with a submodule pointing at a private URL, or a credential
    helper that prompts, would otherwise block on a terminal prompt forever in a
    server process that has no terminal — which is exactly the hang the timeout
    exists for, except the timeout would then report a hang that has no cause
    visible anywhere.
    """
    from app.services.developer.git import _subprocess_env

    env = _subprocess_env()

    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_OPTIONAL_LOCKS"] == "0"
    assert "PATH" in env or os.name == "nt"
