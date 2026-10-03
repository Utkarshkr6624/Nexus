"""Resumable checkpoints as plain files, so this half needs no torch.

A checkpoint is a directory: the payload files the remote notebook handed back,
plus one ``checkpoint.json`` describing them. That is the whole format, and the
reason is practical rather than aesthetic.

**The machine that resumes a run is not the machine that trains it.** The GPU
notebook serialises tensors with ``torch.save`` and copies the results out; this
module never imports torch and never unpickles anything. It writes the bytes it
is given, hashes what it wrote, records the step, and later answers "which
checkpoint is the furthest I got, and is it whole?". That question has to be
answerable on a laptop, in CI, and on the local machine that has no torch at
all — which is the machine that decides whether a run is worth resuming. A
design that made that answer require importing the library the resume is meant
to avoid installing would answer it nowhere.

**``checkpoint.json`` is written last, and that is the whole crash story.** A
process killed mid-save leaves payload files and no metadata, and a directory
with no metadata is not a checkpoint — it is debris. :func:`is_resumable` and
:func:`latest_checkpoint` both refuse it rather than resuming from a state
whose contents nobody can enumerate. Corrupting a run by resuming from a
half-written one is worse than restarting it, because the failure surfaces hours
later as a nonsense loss curve.

**The metadata never trusts the filesystem, and the filesystem never overwrites
the metadata.** Each file is written to a temporary name and moved into place,
so a reader never observes a partial payload; the metadata is moved into place
last, which makes its presence the commit point for the whole directory.

The payload bytes themselves are opaque here. :func:`save_checkpoint` takes
already-serialised blobs — a ``.safetensors`` from the notebook, or anything
else — and records them under logical names. The optional ``save_sidecar`` hook
is where a notebook that *does* have torch writes real state such as optimiser
moments or RNG state; whatever it writes is the notebook's responsibility,
because only it knows what those files are.
"""

from __future__ import annotations

import json
import os
import re
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ml.datasets.schema import stable_json_dumps

#: Version of the on-disk checkpoint directory. Bump when the metadata shape or
#: the meaning of a field changes; :func:`load_checkpoint` refuses anything else
#: rather than guessing, for the same reason ``ml.datasets.schema`` refuses an
#: unknown record schema.
CHECKPOINT_FORMAT_VERSION = "nexo_checkpoint.v1"

#: The commit point of a checkpoint directory. Written last, and its presence is
#: what makes the surrounding files a checkpoint rather than debris.
_METADATA_FILENAME = "checkpoint.json"

#: Logical key under which the caller's ``extra`` mapping is registered.
_EXTRA_KEY = "extra"
_EXTRA_FILENAME = "extra.json"

#: Suffix given to a tensor blob whose logical name carries no extension of its
#: own. The format makes no claim about what the bytes are; ``.bin`` is simply
#: the honest default for "some serialisation we are not parsing".
_DEFAULT_SUFFIX = ".bin"

#: Logical names become filenames inside the checkpoint directory, so they are
#: restricted to characters that cannot address anything but that directory.
#: No separators, no traversal, no absolute paths, no drive letters.
_SAFE_NAME = re.compile(r"[A-Za-z0-9._-]+")


class CheckpointError(Exception):
    """A checkpoint could not be written, read or trusted.

    Distinct from a generic OS error so that resuming can tell "this directory
    is not a checkpoint" (skip it, warn, carry on from the previous one) from
    "this process cannot write to disk" (stop).
    """


def _require_str(raw: Mapping[str, Any], key: str, where: str) -> str:
    """Read a required string field, or raise.

    Args:
        raw: The decoded metadata object.
        key: The field name.
        where: A human description of the source, used in the error message.

    Returns:
        The string value.

    Raises:
        CheckpointError: The field is absent or not a string.
    """
    value = raw.get(key)
    if not isinstance(value, str):
        raise CheckpointError(f"{where}: {key!r} must be a string, got {value!r}")
    return value


def _require_int(raw: Mapping[str, Any], key: str, where: str) -> int:
    """Read a required integer field, or raise.

    Booleans are rejected explicitly: ``True`` is an ``int`` in Python, and a
    metadata file written by a buggy producer should fail loudly rather than
    silently resuming at step 1.

    Args:
        raw: The decoded metadata object.
        key: The field name.
        where: A human description of the source, used in the error message.

    Returns:
        The integer value.

    Raises:
        CheckpointError: The field is absent or not an integer.
    """
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CheckpointError(f"{where}: {key!r} must be an integer, got {value!r}")
    return value


def _require_mapping(raw: Mapping[str, Any], key: str, where: str) -> dict[str, Any]:
    """Read a required object field, or raise.

    Args:
        raw: The decoded metadata object.
        key: The field name.
        where: A human description of the source, used in the error message.

    Returns:
        The field as a plain dict.

    Raises:
        CheckpointError: The field is absent or not an object.
    """
    value = raw.get(key)
    if not isinstance(value, Mapping):
        raise CheckpointError(f"{where}: {key!r} must be an object, got {value!r}")
    return dict(value)


@dataclass(frozen=True, slots=True)
class CheckpointMetadata:
    """The ``checkpoint.json`` record: what this directory is and where it sits.

    The resume-relevant fields are ``global_step``, ``epoch``,
    ``segment_index`` and ``segments_completed`` — the trainer processes a
    dataset in segments, so a resume has to know which segment the run died in
    as well as how far it got, or it restarts the epoch with a shuffle it has
    already consumed.

    ``dataset_version`` and ``dataset_checksum`` are both here so a resume can
    refuse to continue a run whose data changed underneath it. Resuming onto a
    different dataset is worse than not resuming: the result looks like a
    continuation and is not one.

    ``files`` maps a logical name to a filename inside the checkpoint
    directory — ``{"adapter.safetensors": "adapter.safetensors"}``. The
    indirection exists so a consumer asks for ``state.directory /
    state.metadata.files["adapter.safetensors"]`` and never hardcodes a
    filename this module chose.
    """

    format_version: str
    run_id: str
    model_name: str
    global_step: int
    epoch: int
    segment_index: int
    segments_completed: int
    dataset_version: str
    dataset_checksum: str
    code_commit: str
    config: Mapping[str, Any]
    hyperparameters: Mapping[str, Any]
    seed: int
    created_at: str
    elapsed_seconds: float
    files: Mapping[str, str]

    def __post_init__(self) -> None:
        """Detach the mutable mappings the caller handed in."""
        for name in ("config", "hyperparameters", "files"):
            object.__setattr__(self, name, dict(getattr(self, name)))

    def to_dict(self) -> dict[str, Any]:
        """Serialise as a JSON-ready mapping, format version included.

        Returns:
            The metadata as plain JSON-serialisable data.
        """
        return {
            "format_version": self.format_version,
            "run_id": self.run_id,
            "model_name": self.model_name,
            "global_step": self.global_step,
            "epoch": self.epoch,
            "segment_index": self.segment_index,
            "segments_completed": self.segments_completed,
            "dataset_version": self.dataset_version,
            "dataset_checksum": self.dataset_checksum,
            "code_commit": self.code_commit,
            "config": dict(self.config),
            "hyperparameters": dict(self.hyperparameters),
            "seed": self.seed,
            "created_at": self.created_at,
            "elapsed_seconds": self.elapsed_seconds,
            "files": dict(self.files),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CheckpointMetadata:
        """Rebuild metadata from its serialised form.

        An unrecognised ``format_version`` is refused rather than parsed on a
        best-effort basis. Guessing at a layout we do not know is how a run
        resumes from state it never wrote, and the resulting loss curve is
        believable enough to be shipped.

        Args:
            raw: A decoded ``checkpoint.json`` object.

        Returns:
            The parsed metadata.

        Raises:
            CheckpointError: A field is missing, mistyped, or the format
                version is not one this module understands.
        """
        where = f"{_METADATA_FILENAME} (format {raw.get('format_version')!r})"
        format_version = _require_str(raw, "format_version", where)
        if format_version != CHECKPOINT_FORMAT_VERSION:
            raise CheckpointError(
                f"{where}: unsupported checkpoint format, expected {CHECKPOINT_FORMAT_VERSION!r}"
            )
        files = _require_mapping(raw, "files", where)
        for logical, filename in files.items():
            if not isinstance(filename, str):
                raise CheckpointError(f"{where}: files[{logical!r}] must be a string")
        return cls(
            format_version=format_version,
            run_id=_require_str(raw, "run_id", where),
            model_name=_require_str(raw, "model_name", where),
            global_step=_require_int(raw, "global_step", where),
            epoch=_require_int(raw, "epoch", where),
            segment_index=_require_int(raw, "segment_index", where),
            segments_completed=_require_int(raw, "segments_completed", where),
            dataset_version=_require_str(raw, "dataset_version", where),
            dataset_checksum=_require_str(raw, "dataset_checksum", where),
            code_commit=_require_str(raw, "code_commit", where),
            config=_require_mapping(raw, "config", where),
            hyperparameters=_require_mapping(raw, "hyperparameters", where),
            seed=_require_int(raw, "seed", where),
            created_at=_require_str(raw, "created_at", where),
            elapsed_seconds=float(raw.get("elapsed_seconds", 0.0)),
            files=files,
        )


@dataclass(frozen=True, slots=True)
class CheckpointState:
    """A loaded checkpoint: where it is and what it says about itself.

    The payload is not decoded. ``metadata.files`` says which files hold it and
    ``directory`` says where they are; what those bytes *mean* is the caller's
    business, because this module has no torch and must not pretend otherwise.
    """

    directory: Path
    metadata: CheckpointMetadata


def _filename_for(name: str) -> str:
    """Turn a logical tensor name into a filename inside the directory.

    A name that already carries an extension is used verbatim, so a notebook
    handing back ``adapter.safetensors`` gets a file called
    ``adapter.safetensors`` rather than ``adapter.safetensors.bin``.

    Args:
        name: The logical name.

    Returns:
        The filename to write inside the checkpoint directory.

    Raises:
        CheckpointError: The name is empty, hidden, or contains anything that
            could address a path outside the checkpoint directory.
    """
    if not name or name.startswith(".") or ".." in name or not _SAFE_NAME.fullmatch(name):
        raise CheckpointError(f"unusable tensor name {name!r}: expected an identifier")
    return name if Path(name).suffix else f"{name}{_DEFAULT_SUFFIX}"


def _write_atomic(path: Path, payload: bytes) -> None:
    """Write bytes so a reader never observes a partial file.

    Args:
        path: Destination inside an existing directory.
        payload: The bytes to write.

    Raises:
        CheckpointError: The file could not be written.
    """
    temporary = path.with_name(f"{path.name}.partial")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise CheckpointError(f"cannot write {path}: {exc}") from exc


def save_checkpoint(
    directory: Path,
    *,
    metadata: CheckpointMetadata,
    tensors: Mapping[str, bytes],
    extra: Mapping[str, Any] | None = None,
    save_sidecar: Callable[[Path], None] | None = None,
) -> CheckpointState:
    """Write one checkpoint directory and return what was actually written.

    ``tensors`` holds **already-serialised** blobs. This module never calls
    ``torch.save`` and never unpickles: the notebook serialises on the machine
    that has torch, and the bytes travel as files. A name without an extension
    gets ``.bin``; a name with one keeps it.

    Order of operations is the crash contract. Payload files and the sidecar's
    output are written first; ``checkpoint.json`` is written last. A directory
    that exists without its metadata is therefore an interrupted write, and the
    readers refuse it — see :func:`is_resumable`.

    ``extra`` is for the small scalar state a resume needs that is not a tensor:
    optimizer step counters, RNG state, the loss history so far. It is written
    as ``extra.json`` and registered in ``files`` under the logical name
    ``"extra"``, so a reader reaches it as
    ``state.directory / state.metadata.files["extra"]``.

    ``save_sidecar`` is the remote notebook's hook, called with the checkpoint
    directory once the declared payloads are in place and before the metadata
    commits the directory. It exists so a process that *does* have torch can
    write its optimiser and RNG state into the same directory, where the local
    half can still see that the checkpoint is complete. Files the sidecar adds
    beyond the declared ``tensors`` names are invisible to the readers, so a
    notebook that needs them validated should hand their bytes over as tensors
    instead.

    Args:
        directory: Destination. Created if absent; existing files with the same
            names are replaced.
        metadata: The record describing this checkpoint. Its ``files`` map is
            merged with the names written here and the result is what lands in
            ``checkpoint.json``, so the returned state always describes the
            directory as it now stands rather than as the caller predicted it.
        tensors: Logical name to serialised bytes.
        extra: Optional JSON-serialisable state written alongside the tensors.
        save_sidecar: Optional hook for writing framework-native state.

    Returns:
        The saved state, with metadata reflecting every file written.

    Raises:
        CheckpointError: A name is unusable, or the directory could not be
            written.
    """
    written: dict[str, str] = {}
    for logical, filename in metadata.files.items():
        written[logical] = _filename_for(filename)

    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CheckpointError(f"cannot create {directory}: {exc}") from exc

    for logical, payload in sorted(tensors.items()):
        filename = _filename_for(logical)
        _write_atomic(directory / filename, payload)
        written[logical] = filename

    if extra is not None:
        _write_atomic(
            directory / _EXTRA_FILENAME,
            (stable_json_dumps(dict(extra)) + "\n").encode("utf-8"),
        )
        written[_EXTRA_KEY] = _EXTRA_FILENAME

    if save_sidecar is not None:
        save_sidecar(directory)

    resolved = replace(metadata, files=written)
    _write_atomic(
        directory / _METADATA_FILENAME,
        (stable_json_dumps(resolved.to_dict()) + "\n").encode("utf-8"),
    )
    return CheckpointState(directory=directory, metadata=resolved)


def _rejection_reason(directory: Path) -> str | None:
    """Say why a directory cannot be resumed, or None when it can.

    Every check here answers the same question — *is this directory a complete
    checkpoint?* — and none of them guess. A missing metadata file, an
    unparseable one, a foreign format version and an absent payload file are all
    rejections, and each gets its own sentence so the warning says which.

    Args:
        directory: A candidate checkpoint directory.

    Returns:
        A human sentence describing the defect, or None when the directory is
        resumable.
    """
    if not directory.is_dir():
        return "not a directory"
    metadata_path = directory / _METADATA_FILENAME
    if not metadata_path.is_file():
        return f"no {_METADATA_FILENAME}"
    try:
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return f"{_METADATA_FILENAME} is unreadable ({exc})"
    if not isinstance(raw, dict):
        return f"{_METADATA_FILENAME} is not a JSON object"
    if raw.get("format_version") != CHECKPOINT_FORMAT_VERSION:
        return f"format version {raw.get('format_version')!r} is not {CHECKPOINT_FORMAT_VERSION!r}"
    files = raw.get("files")
    if not isinstance(files, Mapping):
        return f"{_METADATA_FILENAME} declares no file map"
    for logical, filename in files.items():
        if not isinstance(filename, str):
            return f"{_METADATA_FILENAME}: files[{logical!r}] is not a string"
        try:
            relative = _filename_for(filename)
        except CheckpointError as exc:
            return f"{_METADATA_FILENAME}: {exc}"
        payload = directory / relative
        if not payload.is_file():
            return f"payload {relative!r} is missing"
        if payload.stat().st_size == 0:
            return f"payload {relative!r} is empty"
    try:
        CheckpointMetadata.from_dict(raw)
    except CheckpointError as exc:
        return str(exc)
    return None


def is_resumable(directory: Path) -> bool:
    """Whether a directory is a complete checkpoint that a resume may trust.

    Quiet by design: this is the predicate a caller polls, and a warning on
    every poll of an obviously-empty directory trains people to ignore warnings.
    :func:`latest_checkpoint` is where a rejection is reported.

    Args:
        directory: A candidate checkpoint directory.

    Returns:
        True when the directory carries a parseable, current-format metadata
        file and every file it declares.
    """
    return _rejection_reason(Path(directory)) is None


def load_checkpoint(directory: Path) -> CheckpointState:
    """Load one checkpoint the caller has already chosen.

    Unlike :func:`latest_checkpoint`, a refusal here is an error. The caller
    named this directory explicitly, so there is no other checkpoint to fall
    back to and silently returning nothing would hide a decision that needs
    making.

    Args:
        directory: A checkpoint directory.

    Returns:
        The loaded state.

    Raises:
        CheckpointError: The directory is not a complete checkpoint.
    """
    path = Path(directory)
    reason = _rejection_reason(path)
    if reason is not None:
        raise CheckpointError(f"{path} is not a resumable checkpoint: {reason}")
    raw = json.loads((path / _METADATA_FILENAME).read_text(encoding="utf-8"))
    return CheckpointState(directory=path, metadata=CheckpointMetadata.from_dict(raw))


def _candidate_directories(root: Path) -> list[Path]:
    """List the subdirectories of a checkpoint root, deepest name first sorted.

    Args:
        root: The directory holding this run's checkpoints.

    Returns:
        Child directories, sorted. Empty when the root does not exist or is not
        a directory — a root that has not been created yet simply has no
        checkpoints in it, which is not an error.
    """
    try:
        return sorted(child for child in root.iterdir() if child.is_dir())
    except OSError:
        return []


def list_checkpoints(root: Path) -> tuple[CheckpointMetadata, ...]:
    """Every complete checkpoint under a root, oldest step first.

    Incomplete directories are skipped silently; :func:`latest_checkpoint`
    reports them. This is the call a report makes when it wants to show a
    training curve, and a curve does not care about the debris of an
    interrupted save.

    Args:
        root: The directory holding this run's checkpoints.

    Returns:
        The metadata of each resumable checkpoint, ordered by ``global_step``.
    """
    found: list[CheckpointMetadata] = []
    for candidate in _candidate_directories(Path(root)):
        if _rejection_reason(candidate) is not None:
            continue
        raw = json.loads((candidate / _METADATA_FILENAME).read_text(encoding="utf-8"))
        found.append(CheckpointMetadata.from_dict(raw))
    return tuple(sorted(found, key=lambda item: item.global_step))


def latest_checkpoint(root: Path) -> CheckpointState | None:
    """Find the furthest checkpoint under a root that is safe to resume from.

    A directory that fails any completeness check is *ignored and reported*: the
    warning names it and the reason, because a truncated save that silently
    drops out of the scan is indistinguishable from a run that never reached
    that step, and only one of those is a bug worth chasing.

    Ties on ``global_step`` are broken by directory name, so a root holding two
    copies of the same step always yields the same one.

    Args:
        root: The directory holding this run's checkpoints.

    Returns:
        The checkpoint with the highest ``global_step``, or None when the root
        holds no complete checkpoint.
    """
    best: CheckpointState | None = None
    for candidate in _candidate_directories(Path(root)):
        reason = _rejection_reason(candidate)
        if reason is not None:
            warnings.warn(
                f"ignoring checkpoint directory {candidate}: {reason}",
                RuntimeWarning,
                stacklevel=2,
            )
            continue
        raw = json.loads((candidate / _METADATA_FILENAME).read_text(encoding="utf-8"))
        state = CheckpointState(directory=candidate, metadata=CheckpointMetadata.from_dict(raw))
        if best is None or (
            state.metadata.global_step,
            state.directory.name,
        ) > (best.metadata.global_step, best.directory.name):
            best = state
    return best


__all__ = [
    "CHECKPOINT_FORMAT_VERSION",
    "CheckpointError",
    "CheckpointMetadata",
    "CheckpointState",
    "is_resumable",
    "latest_checkpoint",
    "list_checkpoints",
    "load_checkpoint",
    "save_checkpoint",
]
