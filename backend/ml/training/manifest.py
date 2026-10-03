"""The record of what a training run *was*.

A fine-tune that cannot be described cannot be repeated, and one that cannot be
repeated cannot be believed. This module writes the single document that makes a
run reproducible: which dataset, which base model, which commit, which seed,
which environment, which artifacts, and the checksum of every one of them.

**The fields exist because each one has a failure it prevents.** ``code_commit``
and ``code_dirty`` together answer "was this the code I think it was?" — a clean
sha from a dirty tree is a lie, so dirtiness is recorded rather than hidden.
``dataset_checksum`` answers "the same rows?". ``seed`` answers "the same split
and the same initialisation?". ``environment`` answers "the same library
versions?", which is the question that decides whether a laptop reproducing a
notebook's accuracy is an achievement or a coincidence.

**Deterministic before learned.** Nothing here decides anything about model
behaviour. A manifest describes a run; it never ranks one against another or
feeds a prediction. The deterministic engines in
``app.services.analytics.scoring`` and ``app.services.risk.scoring`` remain the
fallback that learned code is measured against, and nothing in this module can
displace them.

**Secrets are not recorded, and are not printable either.** A manifest names
artifacts and hashes them; it never carries a credential value. The Markdown
renderer still runs its output through
:func:`ml.preprocessing.normalize.redact`, because the one field nobody
scrutinises — a stray environment note in ``config`` — is exactly where one
would land, and a run report is a thing people paste into tickets.

Nothing here imports a third-party package. ``torch`` is *probed* for presence
with :func:`importlib.util.find_spec` and its version is read from installed
distribution metadata, so this module behaves identically on the torch-less
laptop where the manifest is written and the GPU box where it is not.
"""

from __future__ import annotations

import platform
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import metadata as importlib_metadata
from importlib import util as importlib_util
from pathlib import Path
from typing import Any

from ml.datasets.schema import sha256_file, sha256_text
from ml.preprocessing.normalize import redact

#: Version of the run-manifest record shape. Bump on any field rename. Written
#: into every manifest so a reader knows what it is holding before it trusts it.
MANIFEST_VERSION = "nexo_run_manifest.v1"

#: Placeholder for a commit that could not be read. Chosen over raising because
#: provenance metadata is *decorative to the run's correctness* and mandatory to
#: its honesty: a run that cannot name its commit must still record that fact,
#: and a manifest writer that aborted on a missing git would turn an absent git
#: into an absent run record.
UNKNOWN_COMMIT = "unknown"

#: Wall-clock ceiling on one git invocation. ``git status`` on a large work tree
#: or a network-mounted repository can hang, and a manifest must not become the
#: thing that blocks a training run.
GIT_TIMEOUT_SECONDS = 15.0

#: Resolved through ``PATH``, as ``git`` is on every machine that has it. Kept
#: as a module constant rather than an inline literal so the executable is a
#: named, greppable decision instead of a string buried in a call.
GIT_EXECUTABLE = "git"


def new_run_id(
    prefix: str,
    *,
    when: datetime,
    dataset_version: str = "",
    seed: int = 0,
) -> str:
    """Build a run identifier that sorts chronologically.

    The shape is ``{prefix}-{UTC timestamp}-{8 hex}``, e.g.
    ``small-20260101T131405Z-ab12cd34``. Sorting the directory of a run's
    artifacts therefore sorts it in the order the runs happened, with no
    timestamp parsing — the property that matters when someone lists what was
    trained, when.

    The eight hex characters are the head of a sha256 over the prefix, the
    dataset version, the seed and the timestamp. They are not entropy: two runs
    with an identical identity hash to the same value, which is the useful
    direction — it makes "same inputs, same run" checkable without re-deriving
    anything. The timestamp is inside the digest precisely so that two runs a
    second apart do not collide.

    A naive ``when`` is read as local time and converted, because the caller
    usually has ``datetime.now()`` in hand and forcing an explicit zone would
    buy nothing.

    Args:
        prefix: A short human discriminator — the model family, e.g. ``"small"``
            or ``"dataset-build"``.
        when: The instant the run started.
        dataset_version: The dataset the run consumes, folded into the digest so
            a rerun on different data gets a different id.
        seed: The run's seed, folded into the digest for the same reason.

    Returns:
        The run identifier.
    """
    stamp = when.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    digest = sha256_text("|".join((prefix, dataset_version, str(seed), stamp)))
    return f"{prefix}-{stamp}-{digest[:8]}"


def _git_output(repo_root: Path, *args: str) -> str | None:
    """Run one git command and return its stdout, or None on any failure.

    The single choke point for git access. There is no ``shell=True`` here and
    no command string is ever built: ``repo_root`` is attacker-adjacent only in
    the sense that any path could be, and an argument list is the only form in
    which a path with a space or a semicolon in it is still just a path.

    Args:
        repo_root: The directory to run git in.
        args: The git subcommand and its arguments.

    Returns:
        Stripped stdout, or None when git is missing, is not a repository, or
        did not finish inside :data:`GIT_TIMEOUT_SECONDS`.
    """
    try:
        completed = subprocess.run(  # noqa: S603 — argv is fixed, shell is never used
            [GIT_EXECUTABLE, *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def git_revision(repo_root: Path) -> tuple[str, bool]:
    """Report which commit a tree is at, and whether it has been edited.

    Provenance degrades rather than fails. A tarball extracted outside git, a
    Docker layer with no ``.git``, a CI image without the git binary — none of
    these are reasons to refuse to write a manifest, so all of them come back as
    ``(UNKNOWN_COMMIT, False)``. The *dirty* flag is reported ``False`` alongside
    an unknown commit deliberately: ``False`` means "the tree matched that
    commit", and asserting that about a commit nobody can name would be the
    exact lie this field exists to prevent. ``UNKNOWN_COMMIT`` is the signal to
    distrust it.

    Args:
        repo_root: The repository root, or any directory inside the work tree.

    Returns:
        ``(commit_sha, dirty)``. The sha is the full 40-character
        ``rev-parse HEAD``; ``dirty`` is True when ``git status --porcelain``
        reports anything at all, including untracked files.
    """
    commit = _git_output(repo_root, "rev-parse", "HEAD")
    if not commit:
        return UNKNOWN_COMMIT, False
    status = _git_output(repo_root, "status", "--porcelain")
    if status is None:
        return commit, False
    return commit, bool(status)


def collect_environment() -> dict[str, Any]:
    """Describe the interpreter and the training libraries it can see.

    The torch probe is deliberately two-step and non-importing.
    :func:`importlib.util.find_spec` answers *is it there* without executing it,
    and :func:`importlib.metadata.version` answers *which one* by reading
    installed distribution metadata. Importing torch to ask its version would
    cost seconds and a gigabyte of resident memory in a module whose entire job
    is to describe the machine — and on a laptop with a stale CUDA install it
    can raise before it can answer.

    A torch that is installed but whose metadata is missing records
    ``torch_version = None`` rather than a guess. ``None`` here is the same
    contract the feature vectors use: unmeasured is not zero.

    Returns:
        A JSON-ready mapping of environment facts.
    """
    spec = importlib_util.find_spec("torch")
    available = spec is not None
    version: str | None = None
    if available:
        try:
            version = importlib_metadata.version("torch")
        except importlib_metadata.PackageNotFoundError:
            version = None
    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "torch_available": available,
        "torch_version": version,
    }


def checksum_file(path: Path) -> str:
    """Hash a file for the manifest's checksum table.

    Args:
        path: The file to hash.

    Returns:
        The lowercase hex sha256 digest.

    Raises:
        ml.datasets.schema.DatasetError: The file cannot be read.
    """
    return sha256_file(path)


@dataclass(frozen=True, slots=True)
class RunManifest:
    """Everything needed to describe, repeat and trust one training run.

    Written twice in a run's life: once when it starts, with ``finished_at``,
    ``duration_seconds``, ``evaluation`` and ``artifacts`` empty, and once when
    it ends. The first copy is the reason a crashed run still leaves evidence —
    a training job killed at hour nine should be able to say which dataset and
    which commit it was on.

    ``code_dirty`` is recorded next to ``code_commit`` rather than folded into
    it because the two answer different questions and only together mean
    anything: the sha says which tree, the flag says whether that tree was still
    the one being edited while it ran.

    ``artifacts`` maps a logical name to a **repository-relative** path and
    ``checksums`` maps the same logical name to a sha256. Keys are matched
    between the two, so a reader can verify every artifact from the manifest
    alone without knowing the directory layout that produced it.

    The mapping fields are copied on construction. A manifest is a frozen record
    of what happened; if it held a reference to the caller's hyperparameter dict,
    the run could be edited after the fact by code that still holds the dict,
    which would defeat the entire document.
    """

    run_id: str
    model_name: str
    base_model: str
    model_version: str
    dataset_version: str
    dataset_source: str
    schema_version: str
    preprocessing_version: str
    code_commit: str
    code_dirty: bool
    config: Mapping[str, Any]
    hyperparameters: Mapping[str, Any]
    seed: int
    environment: Mapping[str, Any]
    started_at: str
    finished_at: str | None = None
    duration_seconds: float | None = None
    checkpoints: tuple[Mapping[str, Any], ...] = ()
    evaluation: Mapping[str, Any] = field(default_factory=dict)
    artifacts: Mapping[str, str] = field(default_factory=dict)
    checksums: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Detach the mutable mappings the caller handed in."""
        for name in ("config", "hyperparameters", "environment", "evaluation", "artifacts"):
            object.__setattr__(self, name, dict(getattr(self, name)))
        object.__setattr__(self, "checksums", dict(self.checksums))
        object.__setattr__(self, "checkpoints", tuple(dict(entry) for entry in self.checkpoints))

    def to_dict(self) -> dict[str, Any]:
        """Serialise as a JSON-ready mapping, manifest version included.

        Keys are emitted in field order; the writer is responsible for canonical
        JSON if the result is hashed, which
        :func:`ml.datasets.schema.stable_json_dumps` provides.

        Returns:
            The manifest as plain JSON-serialisable data.
        """
        return {
            "manifest_version": MANIFEST_VERSION,
            "run_id": self.run_id,
            "model_name": self.model_name,
            "base_model": self.base_model,
            "model_version": self.model_version,
            "dataset_version": self.dataset_version,
            "dataset_source": self.dataset_source,
            "schema_version": self.schema_version,
            "preprocessing_version": self.preprocessing_version,
            "code_commit": self.code_commit,
            "code_dirty": self.code_dirty,
            "config": dict(self.config),
            "hyperparameters": dict(self.hyperparameters),
            "seed": self.seed,
            "environment": dict(self.environment),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "checkpoints": [dict(entry) for entry in self.checkpoints],
            "evaluation": dict(self.evaluation),
            "artifacts": dict(self.artifacts),
            "checksums": dict(self.checksums),
        }

    def to_markdown(self) -> str:
        """Render the manifest as the run report body.

        This is the form that gets read by a human and pasted into a ticket, so
        it is a table of named fields rather than a JSON dump: what matters is
        that *any* artifact listed can be verified against the checksum on the
        same line.

        The result passes through
        :func:`ml.preprocessing.normalize.redact`. The manifest's own fields
        are names, paths and hashes, but ``config`` and ``environment`` are
        free-form and an operator note is a perfectly ordinary place for a
        token to end up.

        Returns:
            Markdown text.
        """
        lines: list[str] = [
            f"# Run manifest `{self.run_id}`",
            "",
            "## Model",
        ]
        lines += _table(
            (
                ("model_name", self.model_name),
                ("base_model", self.base_model),
                ("model_version", self.model_version),
            )
        )
        lines += ["", "## Data"]
        lines += _table(
            (
                ("dataset_version", self.dataset_version),
                ("dataset_source", self.dataset_source),
                ("schema_version", self.schema_version),
                ("preprocessing_version", self.preprocessing_version),
                ("seed", self.seed),
            )
        )
        lines += ["", "## Code"]
        lines += _table(
            (
                ("code_commit", self.code_commit),
                ("code_dirty", self.code_dirty),
            )
        )
        lines += ["", "## Timing"]
        lines += _table(
            (
                ("started_at", self.started_at),
                ("finished_at", self.finished_at or "not finished"),
                ("duration_seconds", self.duration_seconds),
            )
        )
        lines += ["", "## Configuration"]
        lines += _table(sorted(self.config.items()))
        lines += ["", "## Hyperparameters"]
        lines += _table(sorted(self.hyperparameters.items()))
        lines += ["", "## Artifacts"]
        lines += _table(
            (name, f"{path} ({self.checksums.get(name, 'no checksum')})")
            for name, path in sorted(self.artifacts.items())
        )
        lines += ["", "## Checkpoints"]
        if self.checkpoints:
            lines += _table(
                ("step", entry.get("global_step", "unknown")) for entry in self.checkpoints
            )
        else:
            lines += ["_No checkpoints recorded._"]
        lines += ["", "## Evaluation"]
        lines += (
            _table(sorted(self.evaluation.items())) if self.evaluation else ["_Not evaluated._"]
        )
        lines += ["", "## Environment"]
        lines += _table(sorted(self.environment.items()))
        return redact("\n".join(lines) + "\n")


def _cell(value: Any) -> str:
    """Render one value for a Markdown table cell.

    Args:
        value: The value to render.

    Returns:
        A single-line string. Pipes are escaped so a value containing one cannot
        break the surrounding row, and floats are shortened to a precision that
        survives being read.
    """
    text = f"{value:.6g}" if isinstance(value, float) else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _table(rows: Sequence[tuple[str, Any]]) -> list[str]:
    """Render named values as a two-column Markdown table.

    Args:
        rows: The label and value pairs, already in the order they should read.

    Returns:
        Markdown lines including the header row.
    """
    lines = ["| field | value |", "| --- | --- |"]
    lines.extend(f"| {name} | {_cell(value)} |" for name, value in rows)
    return lines


__all__ = [
    "GIT_TIMEOUT_SECONDS",
    "MANIFEST_VERSION",
    "UNKNOWN_COMMIT",
    "RunManifest",
    "checksum_file",
    "collect_environment",
    "git_revision",
    "new_run_id",
]
