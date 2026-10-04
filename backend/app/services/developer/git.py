"""Reading a local git repository through the system ``git`` CLI.

Phase 8 analyses **local** repositories: the user registers a directory on this
machine and NEXUS reports what that repository records. There is no GitHub API,
no token, and no network call anywhere in this module.

Why the CLI and not a library
-----------------------------
GitPython is not in ``requirements.txt`` and is not being added: the dependency
set is frozen and the environment has no network. The system ``git`` binary is
the interface that is already present on every machine NEXUS can run on, it is
what the user's own tooling agrees with, and — the reason that actually decides
it — it has a *stable machine-readable output* that does not require parsing
Python objects from someone else's version of a library.

**Every invocation is an argument list through
:func:`asyncio.create_subprocess_exec`.** There is no ``shell=True`` anywhere in
this file and no command string is ever built. That is not a style preference: a
repository path and a commit message are attacker-influenced text on a
multi-tenant server, and string interpolation into a shell is the classic way a
read-only feature becomes remote code execution. With an argument list, a path
containing ``; rm -rf ~`` is a *filename*.

A broken repository must never break NEXUS
------------------------------------------
Every call this module makes can fail — the path may be a file rather than a
directory, ``git`` may not be installed, the repository may be corrupt, the
working tree may live on a network share that has stopped answering. None of
those may reach the user as a stack trace or take the page down. So the failure
surface is exactly two exception types (:class:`GitCommandError` and
:class:`GitRepositoryError`), every message is a human sentence, and the service
layer turns one into a ``GitScanRun`` row with ``status='error'``.

:func:`sanitize_git_message` is part of that promise rather than a nicety. Git's
own ``stderr`` is a good diagnostic and a bad API response: it is written for
someone sitting at a terminal, it quotes absolute paths, and those paths carry
the server operator's home directory and therefore their username. The scanner
strips those before the text can reach a response body or a log a user reads.

What this module deliberately does **not** build
-------------------------------------------------
* **No diff reading, no patch parsing, no blame.** The contract asks for commit
  *facts* — when, who, how many lines, how many files. Blame would answer "who
  last touched this line", which reads as an attribution of effort, and effort is
  precisely what this product is forbidden to claim from a git timestamp.
* **No working-hours, focus or productivity model.** Nothing here can support
  one: a commit timestamp records when a commit object was written, which is not
  when anybody was working. The module's job is to report the record accurately
  and refuse the rest.
* **No ``git branch --contains`` per commit.** It is a graph walk per commit and
  dominates the scan on any repository of size. Attribution is resolved once, in
  a single traversal, by :func:`_attribute_branches`.
* **No ``shell=True``, no GitPython, no network.** Stated above; stated again
  because all three are the tempting shortcuts.
* **No language *detection* engine.** :data:`LANGUAGE_BY_EXTENSION` is a static
  in-repo map, not a guesser. An extension it does not know is not counted at
  all rather than bucketed into "Other", because "Other" would be a number in a
  chart that nobody can act on and that reads as though it were a language.

Branch attribution is best-effort, on purpose
--------------------------------------------
A commit does not store the branch it was made on. Git knows only where a
branch *is now*. So a commit is attributed to a branch when that branch's head
currently reaches it, resolved in one traversal, and to ``None`` whenever no
branch can be resolved — which includes every commit reachable from no branch at
all, and every commit on a detached HEAD that no ref points at. ``None`` is a
truthful answer; a guess would not be.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "DEFAULT_GIT_TIMEOUT_SECONDS",
    "LANGUAGE_BY_EXTENSION",
    "MAX_COMMITS_PER_SCAN",
    "MAX_SCAN_OUTPUT_BYTES",
    "BranchInfo",
    "CommitInfo",
    "GitCommandError",
    "GitError",
    "GitRepositoryError",
    "RepositorySnapshot",
    "detect_language",
    "language_distribution",
    "parse_path_allowlist",
    "read_repository",
    "resolve_allowlist_roots",
    "run_git",
    "sanitize_git_message",
    "validate_repository_path",
]

#: The executable name. Resolved through ``PATH`` by the OS, deliberately: an
#: absolute path baked in here would stop working the moment git moved, and
#: failing to find git is already handled as a scan error with a sentence.
GIT_EXECUTABLE = "git"

#: How long any single git invocation may take before the scan gives up on it.
#: Thirty seconds is far longer than any of the commands below takes on a
#: repository of the size this feature supports, so hitting it means the
#: filesystem has stopped answering rather than that git is slow. Without the
#: ceiling a hung network share would hold an API request open indefinitely,
#: which is the one way this module could break NEXUS despite every other
#: guarantee in this docstring.
DEFAULT_GIT_TIMEOUT_SECONDS = 30

#: The most commits one scan will return, and git takes the *newest* N.
#:
#: **The truncation is permanent, and it is a ceiling on measurement rather than
#: a page waiting to be picked up.** An incremental scan passes ``--since`` at
#: the repository's stored high-water mark, which is its newest recorded commit,
#: so the commits this leaves out are older than that mark and sit behind both
#: filters at once: no later scan asks for them, and a full re-scan asks for the
#: same newest N. A repository with more history than the ceiling records only
#: these commits, for the life of the registration — which is why the ceiling is
#: a setting (``developer_max_commits_per_scan``) rather than a constant a
#: deployment cannot argue with, and why nothing downstream may describe a
#: truncated repository's history as though it were the whole of it.
MAX_COMMITS_PER_SCAN = 2000

#: Hard ceiling on the bytes one invocation may return. The ``ls-files`` and
#: ``log --numstat`` outputs both scale with repository size, and a repository
#: with a few hundred thousand files would otherwise put tens of megabytes of
#: pipe into this process's memory inside a web worker. Exceeding it is a scan
#: error with a sentence, not an out-of-memory kill of the API process.
MAX_SCAN_OUTPUT_BYTES = 16 * 1024 * 1024

#: Lower ceiling for ``stderr``. Git's diagnostics are a few lines; anything
#: approaching this is a sign of a pathological repository rather than an
#: error worth showing, and a scan error never renders a kilobyte of text.
_MAX_STDERR_BYTES = 8 * 1024

#: The environment git is run with.
#:
#: ``GIT_TERMINAL_PROMPT`` is the important one. A repository with a submodule
#: pointing at a private URL, or a credential helper that prompts, would
#: otherwise block on a terminal prompt forever in a server process with no
#: terminal — the exact hang the timeout above exists for. Turning it off makes
#: git fail immediately instead, which becomes an ordinary scan error.
#: ``LC_ALL=C`` keeps git's own messages (and therefore ours) in one language,
#: and ``GIT_OPTIONAL_LOCKS=0`` stops a read-only scan from taking the index
#: lock and blocking a concurrent git command the user is running themselves.
_GIT_ENVIRONMENT: dict[str, str] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "LC_ALL": "C",
}

#: The commit log format. Six tab-separated fields: full hash, short hash, ISO
#: strict author date, author name, author email, subject. Chosen as separate
#: ``create_subprocess_exec`` arguments rather than one format string so nothing
#: about it is a template, and tab-separated rather than a delimiter of our own
#: because a commit subject may contain anything at all — including the
#: characters a naive parser would have picked.
_COMMIT_FORMAT = "%H%x09%h%x09%aI%x09%an%x09%ae%x09%s"

#: The ref format for the branch listing: short name, tip hash, ISO strict
#: committer date. The same three-tuple shape as :data:`_COMMIT_FORMAT` for the
#: same reason.
_REF_FORMAT = "%(refname:short)%09%(objectname)%09%(committerdate:iso-strict)"

#: The numstat and shortstat prefixes. The ``%x00`` NUL cannot appear in a hash
#: or in a filename, so it is an unambiguous record delimiter even when a commit
#: subject or a path contains a newline — which both can.
_RECORD_FORMAT = "%x00%H"

#: ``git status --porcelain`` records are counted, never interpreted. A rename
#: reports as one ``R  old -> new`` line and an untracked directory collapses to
#: its trailing slash, so the count is "how many changes are pending" rather than
#: "how many kinds of change" — every interpretation of a porcelain line this
#: module could write would be a claim about the user's uncommitted work that the
#: record does not support.

#: Extension to language. Static, in-repo, and short.
#:
#: It is a map rather than a detector because a detector would be guessing. A
#: file named ``config.h`` is C, C++ or Objective-C depending on what is in it,
#: and no extension-based rule can settle that; counting it as C would put a
#: confident-looking number on the dashboard for a guess. An extension missing
#: from this map is therefore **not counted**, and if no extension matches, the
#: repository simply has no ``primary_language`` — which is a true statement,
#: where a number would not be.
#:
#: Lowercase keys, because the lookup lowercases the extension.
LANGUAGE_BY_EXTENSION: dict[str, str] = {
    "py": "Python",
    "ts": "TypeScript",
    "tsx": "TypeScript",
    "js": "JavaScript",
    "jsx": "JavaScript",
    "java": "Java",
    "sql": "SQL",
    "go": "Go",
    "rs": "Rust",
    "rb": "Ruby",
    "c": "C",
    "h": "C",
    "cpp": "C++",
    "hpp": "C++",
    "cs": "C#",
    "php": "PHP",
    "swift": "Swift",
    "kt": "Kotlin",
    "html": "HTML",
    "css": "CSS",
    "sh": "Shell",
    "md": "Markdown",
    "json": "JSON",
    "yml": "YAML",
    "yaml": "YAML",
}

#: A git ``--shortstat`` line, e.g. `` 3 files changed, 12 insertions(+), 4 deletions(-)``.
#: Every clause is optional because git omits the ones that are zero: a commit
#: that only adds files prints ``2 files changed, 9 insertions(+)``. The
#: insertions/deletions groups are captured numerically so a value like
#: ``112 insertions(+)`` parses without stripping punctuation.
_SHORTSTAT_PATTERN = re.compile(
    r"(?P<files>\d+)\s+files?\s+changed"
    r"(?:,\s*(?P<insertions>\d+)\s+insertions?\(\+\))?"
    r"(?:,\s*(?P<deletions>\d+)\s+deletions?\(-\))?"
)

#: ``git log --numstat`` prints ``-\t-\tpath`` for a binary file, because there
#: is no line count to report. Treated as zero changed lines, never as a
#: parse failure: a commit that touched one binary blob and three source files
#: really did change three files' worth of lines, and refusing to count it would
#: lose the three.
_BINARY_NUMSTAT_CELL = "-"


class GitError(Exception):
    """Base class for everything this module can fail with.

    Exists so a caller that genuinely does not care which kind of failure it was
    can write one ``except``. Everything below inherits the same promise: the
    message is a sentence for a human and never a traceback.
    """


class GitCommandError(GitError):
    """A ``git`` invocation failed, timed out, or produced too much output.

    The message carries git's own ``stderr``, sanitized by
    :func:`sanitize_git_message` — which is the point of the class rather than a
    detail of it. The alternative, letting the raw text out, is how an operator's
    home directory and username end up in a user-facing response body.
    """


class GitRepositoryError(GitError):
    """The path is not a readable git repository.

    Raised by :func:`validate_repository_path` for a path that does not exist, is
    not a directory, or has no ``.git`` entry, and by :func:`read_repository` for
    anything it cannot read once validation has passed.
    """


@dataclass(frozen=True, slots=True)
class BranchInfo:
    """One branch as it exists on disk right now.

    ``last_committed_at`` is the *tip's* committer date, not the date the branch
    was created: git does not record branch creation, and inventing one from the
    tip's date would let a repository-growth metric read a two-year-old branch as
    brand new.
    """

    name: str
    head_commit_hash: str | None
    last_committed_at: datetime | None


@dataclass(frozen=True, slots=True)
class CommitInfo:
    """One commit, with the facts the repository records about it.

    ``branch`` is best-effort and nullable by contract — see the module
    docstring. ``additions``, ``deletions`` and ``files_changed`` are zero rather
    than absent when git reports nothing, so a re-scan upserts ``0`` instead of
    dropping the row.
    """

    commit_hash: str
    short_hash: str
    committed_at: datetime
    author_name: str | None
    author_email: str | None
    message: str
    additions: int = 0
    deletions: int = 0
    files_changed: int = 0
    branch: str | None = None


@dataclass(frozen=True, slots=True)
class RepositorySnapshot:
    """Everything one scan read off disk about one repository.

    Deliberately a plain value: it holds no session, no cursor and no file
    handle, so the service that stores it can be tested against a hand-built
    snapshot without touching the filesystem, and the reader can be tested
    without a database.

    ``current_branch`` is ``None`` on a detached HEAD, which is **not an error** —
    checking out a commit is a normal thing to do and the snapshot says so rather
    than refusing. ``default_branch`` is ``None`` for a repository with no
    commits at all, which is also valid: a freshly ``git init``ed directory is a
    repository, and §5 of the contracts requires it to be accepted.
    """

    path: Path
    name: str
    current_branch: str | None
    default_branch: str | None
    branches: tuple[BranchInfo, ...]
    commits: tuple[CommitInfo, ...]
    tracked_file_count: int
    language_distribution: tuple[tuple[str, int], ...]
    primary_language: str | None
    working_tree_dirty: bool
    working_tree_changes: int
    first_commit_at: datetime | None
    latest_commit_at: datetime | None


# ---------------------------------------------------------------------------
# Failure containment
# ---------------------------------------------------------------------------


def sanitize_git_message(message: str, *, home: Path | None = None) -> str:
    """Make one line of git's ``stderr`` safe to show a user.

    Three things happen, in this order because each one only makes sense once
    the one before it has: the home directory is replaced with ``~`` so the
    server operator's username cannot leak through a diagnostic; the text is
    flattened onto one line, because git wraps its messages to the terminal width
    and a wrapped error in a JSON field renders as nonsense; and the result is
    truncated, because there is no reason to return eight kilobytes of scanner
    output to a browser.

    **Only the home directory is scrubbed, not every absolute path.** A user who
    registered ``/srv/repos/billing`` needs to be told about ``/srv/repos/billing``
    when it cannot be read, and a rule that reduced it to ``billing`` would make
    the error unactionable. The leak worth preventing is the *operator's* home
    directory, which appears in git's diagnostics without the user ever having
    mentioned it, so that is the one path rewritten. The residual risk — a
    diagnostic naming some other user's home — is real but small, and it is
    cheaper than the alternative of an error message that cannot identify the
    directory it is about.

    Git never produces a Python traceback, so "never let a traceback through" is
    satisfied here by construction rather than by a filter.

    Args:
        message: Raw ``stderr`` from git.
        home: The home directory to collapse. Defaults to :meth:`Path.home`,
            which is what the caller means in practice; passed explicitly by the
            tests so they do not depend on the machine they run on.

    Returns:
        A single-line sentence, possibly truncated, with the home directory
        rewritten to ``~``.
    """
    if not message:
        return ""
    flattened = " ".join(message.split())

    # `Path.home()` can itself raise on a machine with no resolvable home
    # directory, and a failing *sanitizer* would turn a git error into an
    # unhandled exception — which is exactly the outcome this function exists to
    # prevent.
    with contextlib.suppress(OSError, RuntimeError):
        home_text = str(home if home is not None else Path.home())
        if home_text:
            for spelling in (home_text.replace("\\", "/"), home_text):
                if spelling:
                    flattened = flattened.replace(spelling, "~")

    if len(flattened) > _MAX_STDERR_BYTES:
        flattened = flattened[: _MAX_STDERR_BYTES - 1].rstrip() + "…"
    return flattened.strip()


def _describe_bytes(data: bytes) -> str:
    """Decode subprocess output, replacing anything that is not valid UTF-8.

    Git output is UTF-8 by convention but a filename can hold arbitrary bytes on
    a POSIX filesystem, and ``UnicodeDecodeError`` here would replace a
    successful scan with an unhandled exception.
    """
    return data.decode("utf-8", errors="replace")


async def run_git(
    repo_path: str | os.PathLike[str],
    *args: str,
    timeout: float = DEFAULT_GIT_TIMEOUT_SECONDS,  # noqa: ASYNC109 — see below
) -> str:
    """Run one git command in ``repo_path`` and return its stdout.

    The single choke point for the whole module: every invocation is an argument
    list, ``cwd`` is always the repository, and the three ways a subprocess can
    go wrong — not found, too slow, too much output — are all converted into
    :class:`GitCommandError` with a sentence attached.

    ``stderr`` is read concurrently with ``stdout`` rather than after it. A
    process that fills the stderr pipe while this coroutine is still reading
    stdout blocks forever, and the timeout would then report a hang that was
    actually a deadlock in the reading loop.

    **Where it runs is a platform fact, not a preference.** On Windows a
    ``SelectorEventLoop`` cannot start a subprocess at all — it has no subprocess
    transport — and that is precisely the loop NEXUS runs on, because
    :mod:`app.core.event_loop` selects it so psycopg's reader callbacks work. So
    on that combination the call is handed to :func:`_run_git_on_worker_loop`,
    which runs the *same* implementation on a loop that can spawn one. Everywhere
    else it runs inline. Nothing about the timeout, the output ceiling or the
    error handling differs between the two, which is the point of the split.

    **On the ``noqa`` above.** ``ASYNC109`` asks that a ``timeout`` parameter be
    removed and that the caller wrap the call in ``asyncio.timeout`` instead.
    The Phase 8 contracts freeze this signature — ``run_git(repo_path, *args,
    timeout=...)`` — and the property the rule wants is already true here: the
    body uses ``asyncio.timeout`` directly, so there is no hand-rolled
    ``asyncio.wait_for`` to replace. Renaming the parameter would satisfy the
    linter by breaking a published contract, which is the wrong trade. The rule
    is scoped to this one line rather than switched off for the file.

    Args:
        repo_path: The repository directory. Used as ``cwd``; never interpolated
            into a command string.
        *args: The git subcommand and its arguments, each a separate element.
        timeout: Seconds to wait before giving up.

    Returns:
        ``stdout``, decoded with replacement characters rather than raising on
        a stray byte.

    Raises:
        GitCommandError: If git is not installed, exits non-zero, exceeds
            ``timeout``, or writes more than the output ceiling allows.
    """
    if _running_loop_can_spawn():
        return await _run_git_here(repo_path, args, timeout)

    # Windows, on a SelectorEventLoop — which is the loop NEXUS runs on, because
    # psycopg needs its reader callbacks. See :func:`_run_git_on_a_worker_loop`.
    return await asyncio.to_thread(_run_git_on_worker_loop, repo_path, args, timeout)


async def _run_git_here(
    repo_path: str | os.PathLike[str],
    args: tuple[str, ...],
    timeout: float,  # noqa: ASYNC109 — same reason as run_git's, which calls this
) -> str:
    """The whole of :func:`run_git`, on the loop the caller is already running.

    Split from the entry point so the platform fallback below can run the *same*
    code on a different loop rather than a simplified copy of it. A second,
    shorter implementation of the timeout, the output ceiling and the stderr
    sanitiser would be a second set of rules — and the failure mode of the two
    drifting apart is a subprocess that leaks.
    """
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            GIT_EXECUTABLE,
            *args,
            cwd=str(repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_subprocess_env(),
        )
    except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
        # NotADirectoryError and PermissionError are the OS reporting that the
        # *path* is unusable; they surface from create_subprocess_exec because
        # the working directory is set there.
        raise GitCommandError(
            sanitize_git_message(f"The {GIT_EXECUTABLE} command could not be started ({exc}).")
        ) from exc

    try:
        async with asyncio.timeout(timeout):
            stdout_bytes, stderr_bytes = await asyncio.gather(
                _drain(process.stdout, MAX_SCAN_OUTPUT_BYTES, "output"),
                _drain(process.stderr, _MAX_STDERR_BYTES, "error output"),
            )
            # Both pipes reaching EOF does *not* mean the child has been reaped —
            # it means it closed its descriptors, which on POSIX it may do before
            # it exits and on Windows it may do before the handle is signalled.
            # Without this `process.returncode` is still `None` and every
            # successful command would be reported as a failure with no stderr.
            returncode = await process.wait()
    except TimeoutError:
        await _terminate(process)
        raise GitCommandError(f"The repository scan timed out after {timeout:g} seconds.") from None
    except _OutputTooLargeError as exc:
        await _terminate(process)
        raise GitCommandError(
            f"The repository scan produced more than {exc.limit} bytes of "
            f"{exc.stream_name} and was stopped."
        ) from None

    stderr_text = sanitize_git_message(_describe_bytes(stderr_bytes or b""))
    if returncode != 0:
        raise GitCommandError(stderr_text or f"git {args[0]} exited with status {returncode}.")
    return _describe_bytes(stdout_bytes or b"")


def _running_loop_can_spawn() -> bool:
    """Whether this loop's implementation can start a subprocess.

    **This is a platform fact, not a preference.** On Windows a
    ``SelectorEventLoop`` raises ``NotImplementedError`` from ``subprocess_exec``
    — it has no subprocess transport at all — and NEXUS runs on exactly that loop,
    because :mod:`app.core.event_loop` selects it so that psycopg's
    ``loop.add_reader`` callbacks work. So without this check the git engine
    could not run at all on the platform its developers develop on, and every
    scan would fail with an error that describes a python internal rather than
    the repository.

    The check is a class test rather than a trial call because a trial call would
    have to actually spawn something first, and because an exception-driven
    fallback here would be catching an error that has nothing to do with the
    repository being scanned.
    """
    if os.name != "nt":
        return True
    return not isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)


def _run_git_on_worker_loop(
    repo_path: str | os.PathLike[str], args: tuple[str, ...], timeout: float
) -> str:
    """Run :func:`_run_git_here` on a private loop, from a worker thread.

    Called only on Windows under a ``SelectorEventLoop``, and it does exactly one
    thing: gets the subprocess onto a loop that can spawn one. Everything that
    matters still happens inside :func:`_run_git_here` — the timeout, the output
    ceiling, the kill, the stderr sanitiser — so the fallback cannot be a laxer
    version of the scan. The cost is one thread hop per invocation on a platform
    where there is no alternative, and a thread pool that is already there
    because the app is async.

    The loop is constructed directly rather than through the active policy. The
    policy in this application is :func:`~app.core.event_loop.nexus_loop_factory`,
    which hands back a ``SelectorEventLoop`` — the very loop being worked around —
    so asking the policy would reproduce the problem rather than fix it. On
    POSIX the default loop already supports subprocesses and none of this runs.

    Args:
        repo_path: The repository directory, as ``cwd``.
        args: The git subcommand and its arguments.
        timeout: Seconds to wait before giving up.

    Returns:
        ``stdout``, exactly as :func:`run_git` would have returned it.

    Raises:
        GitCommandError: Whatever :func:`_run_git_here` raises, propagated across
            the thread boundary unchanged.
    """
    loop = _spawn_capable_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(_run_git_here(repo_path, args, timeout))
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


def _spawn_capable_loop() -> asyncio.AbstractEventLoop:
    """A fresh event loop that can start a subprocess on this platform.

    On Windows that is a ``ProactorEventLoop``; everywhere else the default is
    already capable. The Windows branch is guarded by ``os.name`` rather than by
    a ``hasattr`` check because ``asyncio.ProactorEventLoop`` does not exist at
    all on POSIX, and importing the name unconditionally would be an
    ``AttributeError`` waiting to happen.
    """
    if os.name == "nt":
        return asyncio.ProactorEventLoop()
    return asyncio.new_event_loop()


class _OutputTooLargeError(Exception):
    """A pipe wrote past its ceiling. Internal; never surfaces as itself."""

    def __init__(self, limit: int, stream_name: str) -> None:
        super().__init__(f"{stream_name} exceeded {limit} bytes")
        self.limit = limit
        self.stream_name = stream_name


def _subprocess_env() -> dict[str, str]:
    """The child's environment: this process's, with git's own settings applied.

    Copied rather than passed through so the scan cannot be influenced by a
    stray ``GIT_*`` variable in the server's environment, and so an absent
    ``PATH`` still lets the OS find ``git``.
    """
    env = dict(os.environ)
    env.update(_GIT_ENVIRONMENT)
    return env


async def _drain(stream: asyncio.StreamReader | None, limit: int, stream_name: str) -> bytes:
    """Read a pipe to EOF, refusing to buffer more than ``limit`` bytes.

    The limit is checked *while* reading rather than after, so an oversized
    output is abandoned at the ceiling instead of being fully buffered and then
    measured. That distinction is the whole reason this is a function: a
    repository with half a million files would otherwise allocate its entire
    ``ls-files`` output inside a request before anything noticed.
    """
    if stream is None:
        return b""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise _OutputTooLargeError(limit, stream_name)
        chunks.append(chunk)
    return b"".join(chunks)


async def _terminate(process: asyncio.subprocess.Process) -> None:
    """Kill a subprocess and reap it, ignoring anything that goes wrong.

    Called only on the failure paths. A kill that raises because the process has
    already exited is not interesting, and swallowing it here is deliberate: the
    caller is about to raise :class:`GitCommandError` and must not have its
    message replaced by a ``ProcessLookupError``.
    """
    with contextlib.suppress(ProcessLookupError, OSError):
        process.kill()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(process.wait(), timeout=5)


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


def parse_path_allowlist(raw: str) -> tuple[Path, ...]:
    """Parse a comma-separated allowlist of roots into resolved paths.

    ``developer_path_allowlist`` is one settings string, and this turns it into
    something a path can be tested against. An empty string means *no allowlist*
    — every readable absolute path that validates as a work tree — which is the
    documented default and the right one for a local-first application running on
    the user's own machine.

    Roots are resolved rather than used as written, so an allowlist entry and the
    path being tested cannot disagree about what ``..`` means. The resolution is
    **non-strict**, so a root that does not exist *yet* is still a usable root: a
    deployment can list the directory its repositories will live in before anyone
    has created one, and requiring the directory to be present would turn a
    configuration choice into a startup-ordering problem.

    An entry that cannot be parsed at all — one carrying a NUL byte, say — is
    **dropped** rather than fatal. A malformed settings value should not make
    every repository in the deployment unregistrable, and dropping an entry only
    ever *permits less* than the operator intended: the failure mode is a refusal
    with a clear message, never an open door.

    Args:
        raw: The settings value, comma-separated.

    Returns:
        The roots, with blank entries removed. Empty when ``raw`` is blank, which
        the caller reads as "no allowlist configured".
    """
    return resolve_allowlist_roots(raw.split(","))


def resolve_allowlist_roots(entries: Iterable[str | os.PathLike[str]]) -> tuple[Path, ...]:
    """Resolve each allowlist entry to an absolute root, dropping unusable ones.

    The single resolution rule, shared by :func:`parse_path_allowlist` — which
    splits the comma-separated settings value and hands the pieces here — and by
    :func:`validate_repository_path`, which is handed the roots themselves.

    **It is shared because splitting an already-split list is lossy.** A caller
    that passes ``allowlist=[Path("/srv/re,pos")]`` and then joins the entries
    back with commas in order to re-parse them gets two roots out of one: the
    prefix before the comma, and the fragment after it resolved against the
    process's working directory. The operator's root stops permitting itself,
    and a root nobody configured starts permitting something.
    """
    roots: list[Path] = []
    for entry in entries:
        candidate = str(entry).strip()
        if not candidate:
            continue
        try:
            roots.append(Path(candidate).expanduser().resolve())
        except (OSError, RuntimeError, ValueError):
            continue
    return tuple(roots)


def validate_repository_path(
    path: str | os.PathLike[str],
    *,
    allowlist: Sequence[str | os.PathLike[str]] | None = None,
) -> Path:
    """Resolve ``path`` and prove it is a git work tree under the allowlist.

    The order is fixed and each step exists: ``expanduser`` so ``~`` means the
    user's home rather than a literal directory named ``~``; ``resolve(strict=True)``
    so a *relative* path cannot later resolve somewhere else once it is stored
    and re-resolved on a scan days from now; the allowlist check, on the *resolved*
    path so a traversal through ``..`` cannot escape it; ``is_dir`` so a file is
    rejected before anything is run in its place; and the ``.git`` check, which is
    what makes this a work tree rather than a directory that happens to sit inside
    one.

    The resolved absolute path is the return value because that is what gets
    stored. Storing the string the caller typed is how a registered repository
    ends up pointing at a different directory after a working-directory change.

    A bare ``git init``ed directory with **no commits** is accepted. It is a
    repository — ``git status`` works in it, ``git init --bare`` is not one but
    that is not what is being registered — and refusing it would mean the very
    first thing a user does with this feature is told their new project does not
    exist.

    **On the optional ``allowlist``.** The contracts fix this function's signature
    as taking one argument, and it is one argument by default, so every call
    written against the contract works unchanged. The keyword exists because §5 of
    the same contracts also says a configured allowlist must be enforced *here*,
    and an enforcement point with no way to pass it the list would not be one. The
    list is supplied by the caller — the service reads
    ``settings.developer_path_allowlist`` and hands it over — so this module stays
    a filesystem reader and does not reach into configuration itself. ``None``
    means "no allowlist configured", which is the default.

    **An allowlist that resolves to nothing permits nothing.** A configured list
    whose every entry was dropped by :func:`parse_path_allowlist` — an entry
    carrying a NUL byte, a path the platform cannot resolve — has permitted *no*
    directory, and skipping the check because the parse came back empty is the one
    way this function could turn a locked-down deployment into an open one. So the
    test is "did the caller configure a list", not "did it produce a usable
    root": a configured list that produced none denies every path, with the same
    sentence a path outside the roots gets.

    Args:
        path: A local path, absolute or relative, ``~``-prefixed or not.
        allowlist: Permitted roots, or ``None`` for no restriction. A path equal
            to a root is permitted, as is any path underneath one. An empty
            sequence is *not* "no restriction": it is a configured list that
            permits nothing, and it is refused.

    Returns:
        The resolved absolute path to the repository root.

    Raises:
        GitRepositoryError: If the path does not exist, is not a directory, is
            outside the allowlist, or contains no ``.git`` entry. Every message
            names the path the user gave, so the response is actionable.
    """
    try:
        expanded = Path(path).expanduser()
    except (RuntimeError, ValueError) as exc:
        raise GitRepositoryError(f"That path could not be read: {exc}.") from exc

    try:
        resolved = expanded.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise GitRepositoryError(
            f"No directory was found at {sanitize_git_message(str(expanded))}."
        ) from exc

    if allowlist is not None:
        roots = resolve_allowlist_roots(allowlist)
        # `not roots` rather than `if roots`: a configured allowlist whose every
        # entry was dropped has permitted no directory, and letting it through
        # would read the failure of a malformed setting as the absence of one.
        if not roots or not any(resolved == root or root in resolved.parents for root in roots):
            raise GitRepositoryError(
                f"{sanitize_git_message(str(resolved))} is outside the directories this "
                "server is allowed to read repositories from."
            )

    if not resolved.is_dir():
        raise GitRepositoryError(
            f"{sanitize_git_message(str(resolved))} is a file, not a repository directory."
        )

    if not (resolved / ".git").exists():
        raise GitRepositoryError(
            f"{sanitize_git_message(str(resolved))} is not a git repository — "
            "no .git entry was found in it."
        )
    return resolved


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


def detect_language(path: str) -> str | None:
    """The language for a tracked path, or ``None`` when the map does not know it.

    Extension only, lowercased, and case-insensitive on the extension so a
    ``.PY`` file is not skipped on a filesystem that preserves case.

    Args:
        path: A path as ``git ls-files`` printed it.

    Returns:
        The language name, or ``None``. ``None`` means "not counted" — there is
        deliberately no "Other" bucket, because an "Other" row in a language
        chart is a number nobody can act on that reads as though it were a
        language.
    """
    name = path.rsplit("/", 1)[-1]
    head, separator, extension = name.rpartition(".")
    if not separator or not extension or not head:
        # No separator means a file like `Makefile`, and an empty `head` means a
        # dotfile like `.gitignore` — whose "extension" is its whole name. Neither
        # is an extension, and treating `.gitignore` as an extension would look
        # up the word "gitignore" in the map.
        return None
    return LANGUAGE_BY_EXTENSION.get(extension.lower())


def language_distribution(paths: Sequence[str]) -> tuple[tuple[str, int], ...]:
    """Count tracked paths by language, most common first.

    ``Counter.most_common`` is stable, so ties break alphabetically by language
    rather than by whichever path happened to be listed first — a scan of the
    same repository returns the same distribution every time, which is what makes
    a re-scan's diff meaningful.

    Args:
        paths: Every path ``git ls-files`` reported.

    Returns:
        ``(language, file count)`` pairs, descending by count. Empty when nothing
        in the map matched.
    """
    counts = Counter(language for path in paths if (language := detect_language(path)) is not None)
    return tuple(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _parse_iso(value: str) -> datetime | None:
    """Parse git's ``iso-strict`` timestamp, or return ``None``.

    ``iso-strict`` emits a real offset (``2026-01-02T03:04:05+05:30``), which
    :meth:`datetime.fromisoformat` reads directly. A value that does not parse
    becomes ``None`` rather than an exception: git has changed this format
    between major versions, and a repository scanned by an older git should lose
    one timestamp rather than fail the whole scan.
    """
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse_numstat(payload: str) -> dict[str, dict[str, int]]:
    r"""Turn ``git log --numstat`` output into per-commit line counts.

    The output is a series of NUL-prefixed blocks: ``\\x00<hash>`` then one
    ``added<TAB>deleted<TAB>path`` line per file, then a blank line. Binary files
    report ``-`` in both numeric cells and contribute zero, because there is no
    line count to record and refusing to count the commit would lose every other
    file it touched.

    A commit with no files — an empty commit on a branch, or one whose only
    change was already reverted — is absent from this map rather than mapped to
    zeros, which is why the caller falls back to ``0`` rather than expecting an
    entry.

    Args:
        payload: Raw output of the ``--numstat`` pass.

    Returns:
        ``{commit hash: {"files_changed": int, "additions": int,
        "deletions": int}}``.
    """
    records: dict[str, dict[str, int]] = {}
    for hash_value, body in _split_records(payload):
        entry = records.setdefault(hash_value, {"files_changed": 0, "additions": 0, "deletions": 0})
        for line in body.splitlines():
            cells = line.split("\t")
            if len(cells) < 3:
                continue
            entry["files_changed"] += 1
            if cells[0] != _BINARY_NUMSTAT_CELL:
                entry["additions"] += _to_int(cells[0])
            if cells[1] != _BINARY_NUMSTAT_CELL:
                entry["deletions"] += _to_int(cells[1])
    return records


def _parse_shortstat(payload: str) -> dict[str, dict[str, int]]:
    """Turn ``git log --shortstat`` output into per-commit line counts.

    The per-commit *aggregate* line — ``3 files changed, 12 insertions(+),
    4 deletions(-)``. Redundant with numstat on a well-formed repository, and
    kept because it is the form that survives a numstat block whose filename git
    chose to quote and escape: a path containing a tab or a newline splits a
    numstat line into pieces that do not parse, while the aggregate does not
    mention filenames at all.

    Args:
        payload: Raw output of the ``--shortstat`` pass.

    Returns:
        The same shape :func:`_parse_numstat` returns.
    """
    records: dict[str, dict[str, int]] = {}
    for hash_value, body in _split_records(payload):
        match = _SHORTSTAT_PATTERN.search(body)
        if match is None:
            continue
        records[hash_value] = {
            "files_changed": int(match.group("files") or 0),
            "additions": int(match.group("insertions") or 0),
            "deletions": int(match.group("deletions") or 0),
        }
    return records


def _merge_stats(
    numstat: dict[str, dict[str, int]], shortstat: dict[str, dict[str, int]]
) -> dict[str, dict[str, int]]:
    """Combine the two stat passes, numstat winning where both have a value.

    numstat is the primary source because it is per-file and therefore the only
    one of the two that can tell a commit with no line changes from a commit
    whose changes are all deletions. shortstat fills in only the commits numstat
    said nothing about, so a numstat block that failed to parse costs the commit
    its per-file detail rather than its existence.

    Args:
        numstat: Per-file counts, the precise source.
        shortstat: Per-commit aggregates, the fallback.

    Returns:
        One mapping, with shortstat's entries added underneath numstat's.
    """
    merged: dict[str, dict[str, int]] = {key: dict(value) for key, value in shortstat.items()}
    for commit_hash, value in numstat.items():
        merged[commit_hash] = dict(value)
    return merged


def _split_records(payload: str) -> list[tuple[str, str]]:
    """Split NUL-delimited git output into ``(hash, body)`` pairs.

    The leading NUL produces an empty first element, which is why every caller
    skips a blank hash rather than assuming the list is clean.
    """
    blocks: list[tuple[str, str]] = []
    for chunk in payload.split("\x00"):
        if not chunk.strip():
            continue
        head, _, body = chunk.lstrip("\r\n").partition("\n")
        blocks.append((head.strip(), body))
    return blocks


def _to_int(value: str) -> int:
    """Parse a count from git, treating anything unexpected as zero.

    A single unparseable number in one file's numstat line must not fail a scan
    of a thousand commits, and the cost of counting it as zero is one file's
    lines rather than the whole repository.
    """
    try:
        return int(value.strip())
    except (AttributeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Repository reader
# ---------------------------------------------------------------------------


async def _read_current_branch(repo_path: Path) -> str | None:
    """The checked-out branch, or ``None`` on a detached HEAD.

    ``rev-parse --abbrev-ref HEAD`` returns the literal string ``HEAD`` when the
    head is detached and exits non-zero in a repository with no commits at all.
    Both are answered as ``None`` — a detached HEAD is a normal state, not a
    broken repository, and neither is a reason to fail a scan.
    """
    try:
        value = (await run_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")).strip()
    except GitCommandError:
        return None
    if not value or value == "HEAD":
        return None
    return value


async def _read_default_branch(repo_path: Path) -> str | None:
    """The repository's default branch, or ``None`` when it has no commits.

    ``refs/remotes/origin/HEAD`` is the authoritative answer — it is what a clone
    knows is the default — and it only exists if the repository was cloned, which
    a locally ``git init``ed one never was. So there is a second attempt against
    ``HEAD``, and ``None`` is a legitimate result for an empty repository rather
    than a failure.
    """
    for args in (
        ("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"),
        ("symbolic-ref", "--quiet", "HEAD"),
    ):
        try:
            value = (await run_git(repo_path, *args)).strip()
        except GitCommandError:
            continue
        if not value:
            continue
        return value.removeprefix("refs/remotes/origin/").removeprefix("refs/heads/")
    return None


async def _read_branches(repo_path: Path) -> tuple[BranchInfo, ...]:
    """Every local branch, as ``(name, tip hash, tip date)``.

    Only ``refs/heads`` is read. Remote-tracking branches are deliberately
    excluded: they are a record of what someone else's server looked like when
    the clone happened, and a repository's own history is not that.
    """
    try:
        output = await run_git(repo_path, "for-each-ref", f"--format={_REF_FORMAT}", "refs/heads")
    except GitCommandError as exc:
        raise GitRepositoryError(f"The repository's branches could not be read: {exc}") from exc

    branches: list[BranchInfo] = []
    for line in output.splitlines():
        cells = line.split("\t")
        if len(cells) < 2 or not cells[0]:
            continue
        branches.append(
            BranchInfo(
                name=cells[0],
                head_commit_hash=cells[1] or None,
                last_committed_at=_parse_iso(cells[2]) if len(cells) > 2 else None,
            )
        )
    return tuple(branches)


async def _read_commit_rows(
    repo_path: Path, *, since: datetime | None, limit: int
) -> list[tuple[str, str, datetime, str | None, str | None, str]]:
    """The commit table of the scan: ``log`` over ``HEAD``, newest first.

    ``--no-merges`` because a merge commit carries the union of both sides'
    changes, so counting its lines as activity would double-count every line the
    merge brought in and inflate ``change_volume`` against a branch that did no
    work of its own.

    ``--since`` is the whole of incremental scanning: passing the stored
    ``latest_commit_at`` transfers only what is new, which is what keeps a
    re-scan cheap on a repository with years of history.

    Args:
        repo_path: The repository to read.
        since: Only commits at or after this instant, or ``None`` for all of
            them. A naive value is read as UTC, matching
            :func:`app.repositories.risk._as_utc`'s rule about the same ambiguity.
        limit: Cap on rows returned.

    Returns:
        ``(hash, short hash, committed_at, author name, author email, subject)``.

    Raises:
        GitRepositoryError: If git could not produce the log at all.
    """
    args = ["log", "--no-merges", "-n", str(max(1, limit)), f"--pretty=format:{_COMMIT_FORMAT}"]
    if since is not None:
        args.append(f"--since={_as_utc(since).isoformat()}")

    try:
        output = await run_git(repo_path, *args)
    except GitCommandError as exc:
        raise GitRepositoryError(f"The repository's commits could not be read: {exc}") from exc

    rows: list[tuple[str, str, datetime, str | None, str | None, str]] = []
    for line in output.splitlines():
        cells = line.split("\t", 5)
        if len(cells) < 6 or not cells[0]:
            continue
        committed_at = _parse_iso(cells[2])
        if committed_at is None:
            continue
        rows.append(
            (
                cells[0],
                cells[1] or cells[0][:12],
                committed_at,
                cells[3] or None,
                cells[4] or None,
                cells[5],
            )
        )
    return rows


async def _read_stat_records(
    repo_path: Path, *, since: datetime | None, limit: int, shortstat: bool
) -> str:
    """Raw ``--numstat`` or ``--shortstat`` output for the same commit set.

    Both run against the *same* ``--no-merges -n limit --since`` window as
    :func:`_read_commit_rows`. That is what makes the two joinable: if the stats
    pass used a different range, a commit could have a count and no identity, or
    an identity and no count, and the re-scan would write zeros.

    Args:
        repo_path: The repository to read.
        since: Same window as the commit pass.
        limit: Same cap as the commit pass.
        shortstat: ``True`` for the aggregate form, ``False`` for numstat.

    Returns:
        The raw NUL-delimited payload, or ``""`` when git could not produce it.
        A missing stats pass is *not* a scan failure: the commits themselves are
        the fact, and a repository whose diffs cannot be counted should still be
        readable — the counts degrade to zero rather than the whole scan failing.
    """
    args = ["log", "--no-merges", "-n", str(max(1, limit)), f"--pretty=format:{_RECORD_FORMAT}"]
    if shortstat:
        args.append("--shortstat")
    else:
        args.append("--numstat")
    if since is not None:
        args.append(f"--since={_as_utc(since).isoformat()}")
    try:
        return await run_git(repo_path, *args)
    except GitCommandError:
        return ""


async def _attribute_branches(
    repo_path: Path,
    rows: Sequence[tuple[str, str, datetime, str | None, str | None, str]],
    *,
    since: datetime | None,
    limit: int,
) -> dict[str, str]:
    """Map each collected commit hash to the branch it belongs to.

    **Best-effort, and the ``None`` in the result is the design.** Git does not
    record the branch a commit was made on; it only knows where a branch's head
    is now. So "the branch a commit belongs to" can only ever mean *the branch
    whose head currently reaches that commit*, and a commit no branch reaches —
    one on a detached HEAD, one whose only ref was deleted — has no answer at
    all.

    ``git branch --contains`` would answer it exactly, and it is rejected because
    it is a graph traversal per commit: two thousand commits is two thousand
    traversals on a repository where they get expensive. Instead one ``log
    --source`` walk over every local branch tip visits each commit once and git
    itself names the ref that reached it. One traversal, whatever the repository
    size.

    **The walk runs inside the same window as the other two passes.** ``--since``
    and ``-n`` are the arguments :func:`_read_commit_rows` used, which is what
    makes this the third pass over one window rather than a fourth pass over all
    of history: an incremental re-scan that transferred two commits would
    otherwise read every commit in the repository to attribute two of them, so
    the scan would get slower as the repository grew — the opposite of what
    ``--since`` is for, and enough to make a re-scan of a large repository cost
    more than the first scan. Commits outside the window are of no use here
    anyway, since the caller only ever asks about rows the window produced.

    A commit in the window that the windowed walk still does not reach is left
    unattributed, which the caller renders as ``None`` — the honest answer for a
    branch-scoped walk, and the same answer this already gave for a commit no
    branch reaches.

    Where several branches reach the same commit, git names one of them and this
    does not second-guess it: ``--source`` emits each commit once, attributed to
    whichever ref the traversal reached it from, and there is no ordering here
    that would improve on that without a second traversal per commit. What
    matters is that the answer is deterministic for a given repository state, so
    a re-scan reports the same branch for the same commit rather than
    oscillating between two equally-true answers.

    Args:
        repo_path: The repository to read.
        rows: The collected commit rows, newest first.
        since: Same lower bound as the commit pass, or ``None`` for all history.
        limit: Same cap as the commit pass.

    Returns:
        ``{commit hash: branch name}``. Commits git could not attribute are
        simply absent, which the caller renders as ``None``.
    """
    if not rows:
        return {}

    try:
        tips = await run_git(repo_path, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    except GitCommandError:
        return {}

    # Branch *names* rather than tip hashes, because `%S` reports whichever
    # spelling was given on the command line — passing hashes made every
    # attribution come back as a hash, which is not a branch name at all.
    branch_names = sorted(line.strip() for line in tips.splitlines() if line.strip())
    if not branch_names:
        return {}

    args = [
        "log",
        "--no-merges",
        "-n",
        str(max(1, limit)),
        "--source",
        "--pretty=format:%H%x09%S",
    ]
    if since is not None:
        args.append(f"--since={_as_utc(since).isoformat()}")
    try:
        walked = await run_git(
            repo_path,
            *args,
            *(f"refs/heads/{name}" for name in branch_names),
        )
    except GitCommandError:
        return {}

    wanted = {row[0] for row in rows}
    attributed: dict[str, str] = {}
    for line in walked.splitlines():
        cells = line.split("\t", 1)
        if len(cells) < 2:
            continue
        commit_hash, source = cells[0].strip(), cells[1].strip()
        branch = source.removeprefix("refs/heads/")
        if commit_hash in wanted and branch and branch in branch_names:
            # First writer wins. `--source` emits each commit once, naming the
            # ref git reached it from, so there is no competition to resolve —
            # and pinning that here rather than letting a later row overwrite is
            # what keeps the answer stable if that ever changes.
            attributed.setdefault(commit_hash, branch)
    return attributed


async def _read_working_tree(repo_path: Path) -> tuple[bool, int]:
    """``(is dirty, number of pending changes)`` from ``status --porcelain``.

    Counted from the machine format rather than parsed for meaning: the scan
    reports how many changes are pending, not what kind they are, and every
    interpretation of a porcelain line this module could write would be a claim
    about the user's uncommitted work that the record does not support.
    """
    try:
        output = await run_git(repo_path, "status", "--porcelain")
    except GitCommandError:
        # An unreadable status is not a broken repository — it happens on a
        # worktree whose index lock is held, or on a repository that has just
        # been deleted from under the scan. "Clean" would be a lie, so this
        # reports the conservative half only: not dirty.
        return False, 0
    lines = [line for line in output.splitlines() if line.strip()]
    return bool(lines), len(lines)


async def _read_tracked_files(repo_path: Path) -> tuple[int, tuple[tuple[str, int], ...]]:
    """``(tracked file count, language distribution)`` from one ``ls-files``.

    One call rather than two because the file list answers both questions and a
    second pass over a large tree would double the scan's I/O for nothing.
    """
    try:
        output = await run_git(repo_path, "ls-files")
    except GitCommandError as exc:
        raise GitRepositoryError(f"The repository's file list could not be read: {exc}") from exc
    paths = [line for line in output.splitlines() if line.strip()]
    return len(paths), language_distribution(paths)


async def _read_history_span(repo_path: Path) -> tuple[datetime | None, datetime | None]:
    """``(oldest, newest)`` author date in the repository's whole history.

    Read from ``HEAD`` with no ``--since`` on either query. An incremental scan
    passes a ``--since`` and gets a few recent commits; taking its span from
    those would walk ``latest_commit_at`` backwards every time the user
    re-scanned, which is how a repository ends up reporting its newest commit as
    three months old.

    Both ends are ``None`` for a repository with no commits, and a single failed
    query is reported as ``(None, None)`` rather than taking the scan down — the
    commit list itself is the authoritative fact and it is already in hand.
    """
    try:
        newest_raw = await run_git(
            repo_path,
            "log",
            "--no-merges",
            "-n",
            "1",
            "--date=iso-strict",
            "--pretty=format:%aI",
        )
        # `--max-parents=0` rather than `--reverse -n 1`. The reverse flag is
        # applied *after* the `-n 1` limit, so that combination returns the
        # newest commit and silently reports it as the oldest — which made every
        # repository whose whole history fits one window report a zero-day span.
        # `--max-parents=0` selects root commits, which is what "the first
        # commit" means, and git stops the walk there rather than buffering the
        # whole log to reverse it.
        oldest_raw = await run_git(
            repo_path,
            "log",
            "--no-merges",
            "--max-parents=0",
            "--date=iso-strict",
            "--pretty=format:%aI",
        )
    except GitCommandError:
        return None, None
    # A repository with several roots (an imported history) prints one date per
    # root; the earliest of them is the first commit.
    oldest_dates = [
        parsed for parsed in (_parse_iso(line) for line in oldest_raw.splitlines()) if parsed
    ]
    return (min(oldest_dates) if oldest_dates else None), _parse_iso(newest_raw)


def _as_utc(value: datetime) -> datetime:
    """Read a naive instant as UTC; leave an aware one alone.

    The same rule :mod:`app.repositories.risk` applies to a ``timestamptz``: an
    instant with no offset is ambiguous, and resolving it against the machine's
    local zone would make the same recorded commit fall inside or outside a
    window depending on where the server happens to be running.
    """
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


async def read_repository(
    path: str | os.PathLike[str],
    *,
    since: datetime | None = None,
    limit: int = MAX_COMMITS_PER_SCAN,
    allowlist: Sequence[str | os.PathLike[str]] | None = None,
) -> RepositorySnapshot:
    """Read one repository off disk, or raise a sentence.

    Runs a dozen or so small git invocations — one per concern, never a single
    giant porcelain dump — and returns an immutable snapshot of everything it
    found. Nothing is written, nothing is stored, and the caller's only decision
    afterwards is what to do with the value.

    The count varies because two of the queries are conditional: ``symbolic-ref
    refs/remotes/origin/HEAD`` is tried first and only the fallback runs when the
    repository was never cloned, and the commit, stats and attribution passes are
    skipped entirely for a repository with no commits. The *worst* case is a
    repository with a remote, a populated history and several branches, and every
    one of those calls is bounded by :data:`DEFAULT_GIT_TIMEOUT_SECONDS`.

    **A repository with no commits is a successful scan, not a failure.**
    ``git log`` exits non-zero in an empty repository, and treating that as an
    error would mean the first thing a user does with this feature — register the
    directory they just initialised — reported a broken repository. So the commit
    pass is skipped when HEAD does not resolve, and the snapshot comes back with
    no commits, no dates and a ``primary_language`` computed from the tracked
    files (of which an empty repository has none).

    **The allowlist is re-checked here, not only at registration.** The path a
    scan is given is a *stored* path read back days later, and the stored string
    is not a promise about what the path resolves to today: a registered
    directory deleted and replaced by a symlink resolves to wherever that
    symlink points, with no ``.git`` entry of its own to contradict it.
    Registration proved the path was permitted when it was written; only a check
    on the path as it resolves *now* can prove it is still permitted. A caller
    that has an allowlist must pass it here, and a scan refused by it raises the
    same sentence a registration would — which the service turns into an error
    scan run, never a traceback.

    Args:
        path: A local repository path. Validated first, so a caller may pass an
            unvalidated string and get the same guarantee.
        since: Only commits at or after this instant. The service passes the
            stored ``latest_commit_at``, which is what makes a re-scan transfer
            only what is new rather than the whole history again.
        limit: Cap on commits returned, applied by git's own ``-n``. The commits
            left out are older than ``since``, so no later scan reaches them
            either — see :data:`MAX_COMMITS_PER_SCAN`.
        allowlist: Permitted roots, or ``None`` when none is configured. Passed
            through to :func:`validate_repository_path`, which refuses a path
            outside them and refuses an allowlist that resolves to nothing.

    Returns:
        The snapshot.

    Raises:
        GitRepositoryError: If the path is not a git repository, or if a
            repository-level read (branches, commit table, file list) failed.
            Individual *optional* reads degrade instead: branch attribution, the
            working-tree state and the history span all answer "unknown" rather
            than failing a scan whose commits were read perfectly well.
    """
    repo_path = validate_repository_path(path, allowlist=allowlist)
    bounded_limit = max(1, min(int(limit), MAX_COMMITS_PER_SCAN))

    has_commits = await _has_commits(repo_path)
    current_branch = await _read_current_branch(repo_path)
    default_branch = await _read_default_branch(repo_path)
    branches = await _read_branches(repo_path)

    rows: list[tuple[str, str, datetime, str | None, str | None, str]] = []
    if has_commits:
        rows = await _read_commit_rows(repo_path, since=since, limit=bounded_limit)

    stats: dict[str, dict[str, int]] = {}
    if rows:
        numstat_payload, shortstat_payload = await asyncio.gather(
            _read_stat_records(repo_path, since=since, limit=bounded_limit, shortstat=False),
            _read_stat_records(repo_path, since=since, limit=bounded_limit, shortstat=True),
        )
        stats = _merge_stats(_parse_numstat(numstat_payload), _parse_shortstat(shortstat_payload))

    attribution = (
        await _attribute_branches(repo_path, rows, since=since, limit=bounded_limit) if rows else {}
    )

    commits = tuple(
        CommitInfo(
            commit_hash=row[0],
            short_hash=row[1],
            committed_at=row[2],
            author_name=row[3],
            author_email=row[4],
            message=row[5],
            additions=stats.get(row[0], {}).get("additions", 0),
            deletions=stats.get(row[0], {}).get("deletions", 0),
            files_changed=stats.get(row[0], {}).get("files_changed", 0),
            branch=attribution.get(row[0]),
        )
        for row in rows
    )

    tracked_file_count, distribution = await _read_tracked_files(repo_path)
    dirty, change_count = await _read_working_tree(repo_path)
    first_commit_at, latest_commit_at = (
        await _read_history_span(repo_path) if has_commits else (None, None)
    )

    return RepositorySnapshot(
        path=repo_path,
        name=repo_path.name or str(repo_path),
        current_branch=current_branch,
        default_branch=default_branch,
        branches=branches,
        commits=commits,
        tracked_file_count=tracked_file_count,
        language_distribution=distribution,
        primary_language=distribution[0][0] if distribution else None,
        working_tree_dirty=dirty,
        working_tree_changes=change_count,
        first_commit_at=first_commit_at,
        latest_commit_at=latest_commit_at,
    )


async def _has_commits(repo_path: Path) -> bool:
    """Whether ``HEAD`` resolves to a commit.

    The one probe that separates "a repository" from "an empty repository". It
    is deliberately a ``--verify --quiet`` call rather than a check on the output
    of a command that was going to run anyway, because it is the difference
    between skipping the commit pass and reporting a git usage error as though
    the user's project were damaged.
    """
    try:
        return bool((await run_git(repo_path, "rev-parse", "--verify", "--quiet", "HEAD")).strip())
    except GitCommandError:
        return False
