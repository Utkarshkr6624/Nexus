"""Train the routing classifier locally, on CPU, in the ML virtualenv.

This is the Phase 10 trainer for ``microsoft/deberta-v3-base``, written to be
run directly rather than orchestrated::

    ml/.venv/Scripts/python.exe -m ml.scripts.train_small_local

**Why it is a standalone script and not a step of ``ml.train``.** torch lives in
exactly one environment in this repository — ``ml/.venv`` — because it is a
multi-hundred-megabyte dependency the backend test suite has no reason to
install. Everything else in ``ml/`` is stdlib-only and runs under
``backend/.venv``. A run therefore has a seam in it: the datasets, the configs,
the validators and the checkpoint reader are shared, and the loop that calls
``model.backward()`` is not. This script is the loop, kept on its own so that
"which interpreter am I in" is answered by the command line rather than by
reading the orchestrator.

**Which is why every torch import is inside a function.** The test suite
collects the whole ``ml`` package under the backend interpreter, so a
module-scope ``import torch`` here would break collection on an interpreter
that cannot have torch. The module-level imports below are stdlib and ``ml``
only, which is also what keeps ``import ml.scripts.train_small_local`` working on
a machine that has never downloaded a checkpoint.

**The label map is read, never assumed.** ``num_labels`` comes from
``label_map.json``, cross-checked against :func:`ml.datasets.routing.label_map`,
which is the taxonomy's own index order. Hardcoding fourteen would agree with
the taxonomy today and disagree silently the first time an intent is added — and
a head that is one class short does not fail, it just never predicts the new
class.

**The parameter count is measured, then asserted.** ``deberta-v3-base`` is a
183M-parameter encoder and the brief requires a router between 100M and 300M,
because the router has to be resident inside the request path. That band is a
constraint on which checkpoint may serve traffic, so a run that lands outside it
stops rather than producing an artifact that would be rejected at load time.

**Checkpoints are the ones the rest of the pipeline already reads.** Each
checkpoint directory carries model weights, tokenizer, optimiser state and RNG
state alongside a ``checkpoint.json`` built by
:class:`ml.training.checkpoint.CheckpointMetadata`, so
:func:`ml.training.checkpoint.latest_checkpoint` can find it and
:func:`~ml.training.checkpoint.load_checkpoint` validates it. The metadata is
written last by that module, which is what makes a half-written checkpoint
directory refuse to resume rather than resume from debris.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import ModuleType
from typing import Any

from ml.datasets.routing import ROUTING_DATASET_VERSION
from ml.datasets.routing import label_map as taxonomy_label_map
from ml.datasets.schema import (
    DataValidationError,
    read_jsonl,
    sha256_file,
    sha256_text,
    stable_json_dumps,
)
from ml.evaluation.metrics import evaluate
from ml.training.checkpoint import (
    CHECKPOINT_FORMAT_VERSION,
    CheckpointMetadata,
    CheckpointState,
    latest_checkpoint,
    save_checkpoint,
)
from ml.training.manifest import git_revision, new_run_id

__all__ = ["DeviceChoice", "TrainingError", "main"]

#: Reproducible batch order, never a security-relevant source of randomness.
#: Aliased rather than annotated at the use site so the intent is stated once, in
#: the one place a reader of ``random`` usage will look.
_SeededRandom = random.Random

#: Split files the prepare step leaves in the datasets directory. Fixed rather
#: than globbed, because a run that silently picked up a differently named file
#: would be training on data nobody asked for.
TRAIN_FILENAME = "routing_train.jsonl"
VALIDATION_FILENAME = "routing_validation.jsonl"
LABEL_MAP_FILENAME = "label_map.json"

#: Artifact layout under ``--artifacts-dir``. Flat and boring on purpose: the
#: serving path in Phase 11 loads ``final/`` and nothing else, and the report
#: writers look for ``checkpoints/``.
ARTIFACT_DIRNAME = "small-model"
CHECKPOINTS_DIRNAME = "checkpoints"
FINAL_DIRNAME = "final"
STATE_FILENAME = "training_state.json"

#: Optimiser and RNG filenames inside a checkpoint directory. They are declared
#: in the metadata's ``files`` map so the completeness check a resume performs
#: covers them: a checkpoint missing its optimiser state would otherwise look
#: complete and resume into a silently different run.
OPTIMIZER_FILENAME = "optimizer.pt"
RNG_FILENAME = "rng_state.pt"

#: Longest and shortest context an encoder classifier is served at. The same
#: bounds :class:`ml.training.config.SmallModelConfig` enforces, repeated here
#: because this script reads the TOML with the stdlib ``tomllib`` rather than
#: through pydantic — see :func:`_read_settings`.
MIN_SEQ_LENGTH = 32
MAX_SEQ_LENGTH = 512

#: Optimiser-step interval at which a line goes to stdout. Fixed rather than
#: configurable because the CLI surface is a contract, and a run nobody can watch
#: is a run that gets killed without anyone noticing it was making progress.
PROGRESS_EVERY_N_STEPS = 10

#: The 100M-300M band from the brief, used when the TOML does not declare one.
#: It is a property of the serving path — the router must be resident in a
#: request handler — not of whatever checkpoint happens to be configured today.
DEFAULT_PARAMETER_BAND = (100_000_000, 300_000_000)

#: Gradient clipping ceiling. A single clipped step is a normal thing to happen
#: on a small corpus with a fresh classification head; the alternative is a
#: first batch whose gradient norm is in the thousands quietly wrecking the
#: pretrained encoder before warmup has done anything.
MAX_GRAD_NORM = 1.0


class TrainingError(Exception):
    """The run cannot proceed, and saying why beats a stack trace.

    Every one of these is a decision made before or instead of training: a
    dataset that is not where it was said to be, a label map that disagrees with
    the taxonomy, a checkpoint that belongs to different data. They are raised
    rather than recovered from because each one invalidates the artifact a
    recovery would produce.
    """


class DeviceChoice(StrEnum):
    """Where to put the model. ``auto`` picks CUDA only when it really exists.

    The enum exists because ``--device`` is a closed set whose spelling has to
    survive into the run manifest: a run recorded as ``device="gup"`` because a
    string slipped through is a run nobody can repeat.
    """

    AUTO = "auto"
    CPU = "cpu"
    CUDA = "cuda"


@dataclass(frozen=True, slots=True)
class SmallTrainingSettings:
    """The resolved hyper-parameters for one local run.

    ``declared_num_labels`` and ``declared_parameter_count`` are what the TOML
    *claims*. The run measures the real parameter count and stops when the two
    would put the artifact on different sides of the band, which keeps the TOML a
    checked expectation rather than an unchecked constant.
    """

    base_model: str
    max_seq_length: int
    learning_rate: float
    weight_decay: float
    num_train_epochs: int
    per_device_train_batch_size: int
    warmup_ratio: float
    weighting_strategy: str
    seed: int
    save_every_n_steps: int
    eval_every_n_steps: int
    declared_num_labels: int | None
    declared_parameter_count: int | None
    parameter_count_band: tuple[int, int]
    raw: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise as the ``config`` block of a manifest or checkpoint.

        Returns:
            A JSON-ready mapping of every field, including the raw TOML table so
            a reader sees what was on disk rather than only what this dataclass
            chose to interpret.
        """
        return {
            "base_model": self.base_model,
            "max_seq_length": self.max_seq_length,
            "learning_rate": self.learning_rate,
            "weight_decay": self.weight_decay,
            "num_train_epochs": self.num_train_epochs,
            "per_device_train_batch_size": self.per_device_train_batch_size,
            "warmup_ratio": self.warmup_ratio,
            "weighting_strategy": self.weighting_strategy,
            "seed": self.seed,
            "save_every_n_steps": self.save_every_n_steps,
            "eval_every_n_steps": self.eval_every_n_steps,
            "declared_num_labels": self.declared_num_labels,
            "declared_parameter_count": self.declared_parameter_count,
            "parameter_count_band": list(self.parameter_count_band),
            "toml": dict(self.raw),
        }


@dataclass(frozen=True, slots=True)
class LabelledSplit:
    """One prepared split: texts, integer labels, and the digest it came with."""

    name: str
    texts: tuple[str, ...]
    labels: tuple[int, ...]
    path: Path
    checksum: str

    def __len__(self) -> int:
        """Row count, so callers can write ``len(split)``."""
        return len(self.texts)


@dataclass(frozen=True, slots=True)
class TrainingData:
    """Everything read off disk before a single parameter is touched."""

    label2id: Mapping[str, int]
    train: LabelledSplit
    validation: LabelledSplit
    dataset_version: str
    label_map_source: str

    @property
    def id2label(self) -> dict[int, str]:
        """The inverse of the label map, for reporting and for the scorecard."""
        return {value: key for key, value in self.label2id.items()}

    @property
    def checksum(self) -> str:
        """One digest over both splits, so a resume can refuse changed data.

        Two runs hashing the same pair of splits consumed the same rows;
        hashing them separately would let a resume continue onto a train split
        that changed while the validation split did not.
        """
        return sha256_text(f"train={self.train.checksum}\nvalidation={self.validation.checksum}")

    def class_counts(self) -> dict[int, int]:
        """How many training rows carry each class id.

        Returns:
            Label id to row count, over the training split only. The validation
            split is deliberately excluded: weights derived from held-out data
            are a quiet form of leakage.
        """
        counts: dict[int, int] = {}
        for label in self.train.labels:
            counts[label] = counts.get(label, 0) + 1
        return counts


@dataclass(frozen=True, slots=True)
class LossPoint:
    """One optimiser step's mean loss, for the curve and for the resume state."""

    step: int
    epoch: int
    loss: float
    learning_rate: float
    elapsed_seconds: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise as a flat JSON object.

        Returns:
            The point as JSON-ready data.
        """
        return {
            "step": self.step,
            "epoch": self.epoch,
            "loss": self.loss,
            "learning_rate": self.learning_rate,
            "elapsed_seconds": self.elapsed_seconds,
        }


def build_parser() -> argparse.ArgumentParser:
    """Describe the command line.

    Every flag defaults to the value in ``ml/configs/small_model.toml`` rather
    than to a literal here, so the file a reviewer reads to ask "why this
    learning rate" is the file the run actually used. A flag that was not passed
    leaves the configured value alone; a flag that was passed overrides it and
    is recorded in the training state.

    Returns:
        The parser, ready for ``parse_args``.
    """
    parser = argparse.ArgumentParser(
        prog="ml.scripts.train_small_local",
        description=(
            "Train the routing classifier locally (CPU by default). Run it with "
            "ml\\.venv\\Scripts\\python.exe, which is the only interpreter that has torch."
        ),
    )
    parser.add_argument(
        "--data-dir", type=Path, default=Path("ml/datasets"), help="prepared splits directory"
    )
    parser.add_argument(
        "--artifacts-dir", type=Path, default=Path("ml/artifacts"), help="where to write output"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("ml/configs/small_model.toml"),
        help="the small-model hyperparameters file",
    )
    parser.add_argument("--epochs", type=int, default=None, help="override num_train_epochs")
    parser.add_argument(
        "--batch-size", type=int, default=None, help="override per_device_train_batch_size"
    )
    parser.add_argument("--learning-rate", type=float, default=None, help="override learning_rate")
    parser.add_argument("--seed", type=int, default=None, help="override the configured seed")
    parser.add_argument(
        "--max-steps",
        type=int,
        default=0,
        help="stop after this many optimiser steps; 0 runs the configured epochs",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="restore the furthest valid checkpoint under the artifacts directory",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=0,
        help="cap torch's CPU thread count; 0 leaves torch's own default alone",
    )
    parser.add_argument(
        "--device",
        choices=[str(item) for item in DeviceChoice],
        default=str(DeviceChoice.AUTO),
        help="auto resolves to cuda only when torch can actually see a device",
    )
    return parser


def _read_settings(config_path: Path) -> SmallTrainingSettings:
    """Read and range-check ``small_model.toml`` with the standard library.

    The TOML is decoded with :mod:`tomllib` rather than through
    :func:`ml.training.config.load_config`, because that path builds the whole
    :class:`~ml.training.config.PipelineConfig` and therefore needs pydantic — a
    backend dependency the torch virtualenv has no reason to carry. This script's
    dependency surface is deliberately stdlib plus ``ml`` plus torch, so adding a
    model to the pipeline does not mean reconciling two dependency sets. The
    ranges below are the ones
    :class:`~ml.training.config.SmallModelConfig` enforces, checked here rather
    than trusted, because a config that cannot train should stop the run before
    it allocates a model.

    Args:
        config_path: The TOML file.

    Returns:
        The resolved settings.

    Raises:
        TrainingError: The file is missing, is not valid TOML, or declares a
            value that cannot train.
    """
    try:
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except OSError as exc:
        raise TrainingError(f"cannot read config {config_path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise TrainingError(f"{config_path} is not valid TOML: {exc}") from exc

    def as_int(name: str, default: int) -> int:
        value = raw.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise TrainingError(f"{config_path}: {name!r} must be an integer, got {value!r}")
        return value

    def as_float(name: str, default: float) -> float:
        value = raw.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TrainingError(f"{config_path}: {name!r} must be a number, got {value!r}")
        return float(value)

    def as_optional_int(name: str) -> int | None:
        value = raw.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    base_model = str(raw.get("base_model", "")).strip()
    if not base_model:
        raise TrainingError(f"{config_path}: base_model must name a checkpoint")

    band = raw.get("parameter_count_band")
    settings = SmallTrainingSettings(
        base_model=base_model,
        max_seq_length=as_int("max_seq_length", 128),
        learning_rate=as_float("learning_rate", 2e-5),
        weight_decay=as_float("weight_decay", 0.01),
        num_train_epochs=as_int("num_train_epochs", 3),
        per_device_train_batch_size=as_int("per_device_train_batch_size", 16),
        warmup_ratio=as_float("warmup_ratio", 0.1),
        weighting_strategy=str(raw.get("weighting_strategy", "none")),
        seed=as_int("seed", 20260101),
        save_every_n_steps=as_int("save_every_n_steps", 100),
        eval_every_n_steps=as_int("eval_every_n_steps", 100),
        declared_num_labels=as_optional_int("num_labels"),
        declared_parameter_count=as_optional_int("parameter_count"),
        parameter_count_band=(
            (int(band[0]), int(band[1]))
            if isinstance(band, list) and len(band) == 2
            else DEFAULT_PARAMETER_BAND
        ),
        raw=dict(raw),
    )
    _validate_settings(settings, config_path)
    return settings


def _validate_settings(settings: SmallTrainingSettings, config_path: Path) -> None:
    """Refuse settings that would train badly rather than slowly.

    Args:
        settings: The parsed settings.
        config_path: The file they came from, for the error message.

    Raises:
        TrainingError: A value is outside the range the encoder can be trained at.
    """
    where = str(config_path)
    if not MIN_SEQ_LENGTH <= settings.max_seq_length <= MAX_SEQ_LENGTH:
        raise TrainingError(
            f"{where}: max_seq_length must be between {MIN_SEQ_LENGTH} and {MAX_SEQ_LENGTH}, "
            f"got {settings.max_seq_length}"
        )
    if settings.learning_rate <= 0:
        raise TrainingError(
            f"{where}: learning_rate must be positive, got {settings.learning_rate}"
        )
    if settings.weight_decay < 0:
        raise TrainingError(
            f"{where}: weight_decay must not be negative, got {settings.weight_decay}"
        )
    if settings.per_device_train_batch_size < 1:
        raise TrainingError(
            f"{where}: per_device_train_batch_size must be at least 1, "
            f"got {settings.per_device_train_batch_size}"
        )
    if settings.num_train_epochs < 1:
        raise TrainingError(
            f"{where}: num_train_epochs must be at least 1, got {settings.num_train_epochs}"
        )
    if not 0.0 <= settings.warmup_ratio < 1.0:
        raise TrainingError(f"{where}: warmup_ratio must be in [0, 1), got {settings.warmup_ratio}")
    if settings.save_every_n_steps < 1 or settings.eval_every_n_steps < 1:
        raise TrainingError(
            f"{where}: save_every_n_steps and eval_every_n_steps must be at least 1"
        )
    if settings.weighting_strategy not in ("none", "balanced"):
        raise TrainingError(
            f"{where}: weighting_strategy must be 'none' or 'balanced', "
            f"got {settings.weighting_strategy!r}"
        )
    low, high = settings.parameter_count_band
    if low > high:
        raise TrainingError(
            f"{where}: parameter_count_band is inverted: {list(settings.parameter_count_band)}"
        )


def _resolve_overrides(
    args: argparse.Namespace, settings: SmallTrainingSettings
) -> SmallTrainingSettings:
    """Apply command-line overrides to the configured settings.

    :func:`dataclasses.replace` keeps the resolved settings frozen and hashable,
    so an override shows up as a difference between two values rather than as a
    mutation a later reader cannot account for.

    Args:
        args: The parsed arguments.
        settings: The configured settings.

    Returns:
        The settings the run will actually use.

    Raises:
        TrainingError: An override is out of range.
    """
    overrides: dict[str, Any] = {}
    if args.epochs is not None:
        if args.epochs < 1:
            raise TrainingError(f"--epochs must be at least 1, got {args.epochs}")
        overrides["num_train_epochs"] = args.epochs
    if args.batch_size is not None:
        if args.batch_size < 1:
            raise TrainingError(f"--batch-size must be at least 1, got {args.batch_size}")
        overrides["per_device_train_batch_size"] = args.batch_size
    if args.learning_rate is not None:
        if args.learning_rate <= 0:
            raise TrainingError(f"--learning-rate must be positive, got {args.learning_rate}")
        overrides["learning_rate"] = args.learning_rate
    if args.seed is not None:
        if args.seed < 0:
            raise TrainingError(f"--seed must not be negative, got {args.seed}")
        overrides["seed"] = args.seed
    return replace(settings, **overrides) if overrides else settings


def _read_label_map(data_dir: Path) -> tuple[dict[str, int], str]:
    """Resolve the intent-name to class-index map.

    ``label_map.json`` is preferred, in either of the two shapes the pipeline
    produces: a flat ``{"intent": 0}`` table, or the notebook's
    ``{"label2id": {...}, "id2label": {...}}`` envelope. Both are accepted
    because the difference between them is bookkeeping, not meaning.

    When the file is absent the map falls back to
    :func:`ml.datasets.routing.label_map`, which is the taxonomy's own order —
    routers first, then the two large-model classes, then abstention. That
    fallback is not a convenience: it means a run with no label map still agrees
    with the class indices its confusion matrix will be read against.

    The result is cross-checked against the taxonomy map either way, and a
    disagreement stops the run. Two sources of class indices that quietly differ
    produce a model that scores well against one map and serves wrongly against
    the other.

    Args:
        data_dir: Directory holding the prepared splits.

    Returns:
        The label map and a human description of where it came from.

    Raises:
        TrainingError: The file exists but is unreadable, malformed, or
            disagrees with the taxonomy.
    """
    taxonomy = {str(name): int(index) for name, index in taxonomy_label_map().items()}
    path = data_dir / LABEL_MAP_FILENAME
    if not path.is_file():
        print(f"label map    no {LABEL_MAP_FILENAME}; using ml.datasets.routing.label_map()")
        return taxonomy, "ml.datasets.routing.label_map()"
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrainingError(f"{path} is unreadable: {exc}") from exc
    if not isinstance(decoded, dict):
        raise TrainingError(f"{path} must contain a JSON object")
    payload = decoded.get("label2id", decoded)
    if not isinstance(payload, dict):
        raise TrainingError(f"{path}: 'label2id' must be a JSON object")
    resolved: dict[str, int] = {}
    for key, value in payload.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise TrainingError(f"{path}: label {key!r} must map to an integer, got {value!r}")
        resolved[str(key)] = value
    if not resolved:
        raise TrainingError(f"{path} declares no labels")
    if resolved != taxonomy:
        only_file = sorted(set(resolved) - set(taxonomy))
        only_taxonomy = sorted(set(taxonomy) - set(resolved))
        renumbered = sorted(
            (name, resolved[name], taxonomy[name])
            for name in set(resolved) & set(taxonomy)
            if resolved[name] != taxonomy[name]
        )
        raise TrainingError(
            f"{path} disagrees with ml.datasets.routing.label_map(); only in the file: "
            f"{only_file}, only in the taxonomy: {only_taxonomy}, renumbered: {renumbered}"
        )
    print(f"label map    {path}")
    return resolved, str(path)


def _read_split(path: Path, *, name: str, label2id: Mapping[str, int]) -> LabelledSplit:
    """Read one prepared JSONL split and attach integer labels.

    An intent with no slot in the label map is an error rather than a dropped
    row. A silently dropped row shrinks the training set by an amount no manifest
    mentions, and the resulting model is then scored against a dataset it was not
    trained on.

    Args:
        path: The JSONL file.
        name: Human split name, used in messages.
        label2id: Intent name to class index.

    Returns:
        The loaded split.

    Raises:
        TrainingError: The file is missing, malformed, empty, or carries an
            intent the label map does not know.
    """
    if not path.is_file():
        raise TrainingError(f"{name} split not found: {path}")
    try:
        records = read_jsonl(path)
    except DataValidationError as exc:
        raise TrainingError(f"{path}: {exc}") from exc
    texts: list[str] = []
    labels: list[int] = []
    for number, record in enumerate(records, start=1):
        text = record.get("text")
        intent = record.get("intent")
        if not isinstance(text, str) or not text.strip():
            raise TrainingError(f"{path}:{number}: row has no usable 'text'")
        if not isinstance(intent, str) or intent not in label2id:
            raise TrainingError(f"{path}:{number}: intent {intent!r} has no slot in the label map")
        texts.append(text)
        labels.append(label2id[intent])
    if not texts:
        raise TrainingError(f"{path} is empty")
    return LabelledSplit(
        name=name,
        texts=tuple(texts),
        labels=tuple(labels),
        path=path,
        checksum=sha256_file(path),
    )


def load_training_data(data_dir: Path, settings: SmallTrainingSettings) -> TrainingData:
    """Read the label map and both splits from the prepared datasets directory.

    Args:
        data_dir: Directory holding ``routing_train.jsonl``,
            ``routing_validation.jsonl`` and optionally ``label_map.json``.
        settings: The resolved settings, used for the configured label-count
            cross-check.

    Returns:
        The loaded data.

    Raises:
        TrainingError: A file is missing, a label is unknown, the label ids are
            not a dense range, or the configured ``num_labels`` disagrees with
            the label map.
    """
    label2id, source = _read_label_map(data_dir)
    ids = sorted(label2id.values())
    if ids != list(range(len(label2id))):
        raise TrainingError(f"label ids must be a dense range 0..{len(label2id) - 1}, got {ids}")
    if settings.declared_num_labels is not None and settings.declared_num_labels != len(label2id):
        raise TrainingError(
            f"config declares num_labels={settings.declared_num_labels} but the label map "
            f"carries {len(label2id)} classes"
        )
    train = _read_split(data_dir / TRAIN_FILENAME, name="train", label2id=label2id)
    validation = _read_split(data_dir / VALIDATION_FILENAME, name="validation", label2id=label2id)
    print(f"train        {train.path} ({len(train)} rows, sha256 {train.checksum[:12]})")
    print(
        f"validation   {validation.path} ({len(validation)} rows, "
        f"sha256 {validation.checksum[:12]})"
    )
    print(f"num_labels   {len(label2id)} (from {source})")
    return TrainingData(
        label2id=label2id,
        train=train,
        validation=validation,
        dataset_version=ROUTING_DATASET_VERSION,
        label_map_source=source,
    )


def _load_torch() -> ModuleType:
    """Import torch, or explain that this is the wrong interpreter.

    The failure this guards against is specific and expensive: a person runs the
    script with the backend virtualenv, gets an ``ImportError`` from deep inside
    a training stack, and has to work out from the message that the fix is a
    different ``python.exe``. So the message names the right one.

    Returns:
        The imported torch module.

    Raises:
        TrainingError: torch is not installed in this interpreter.
    """
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - depends on the interpreter
        raise TrainingError(
            "torch is not installed in this interpreter. Training runs under "
            "ml\\.venv\\Scripts\\python.exe; the backend interpreter deliberately does not "
            f"carry it ({exc})"
        ) from exc
    return torch


def _resolve_device(torch: ModuleType, requested: str, threads: int) -> str:
    """Choose the device and optionally cap the CPU thread pool.

    ``auto`` resolves to CUDA only when torch can actually see a device. The
    alternative — defaulting to ``cuda`` because the machine might have a GPU —
    is how a run dies at step 1 on a laptop with a stale driver.

    Args:
        torch: The imported torch module.
        requested: One of the :class:`DeviceChoice` values.
        threads: Thread cap; 0 leaves torch's own default alone, because torch
            derives a count from the core count that is usually better than one
            a person typed.

    Returns:
        The device string to hand to ``model.to``.

    Raises:
        TrainingError: CUDA was demanded and is not available.
    """
    if threads > 0:
        torch.set_num_threads(threads)
    available = bool(torch.cuda.is_available())
    if requested == str(DeviceChoice.CUDA) and not available:
        raise TrainingError("--device cuda was requested but torch sees no CUDA device")
    if requested == str(DeviceChoice.AUTO):
        return "cuda" if available else "cpu"
    return requested


def _seed_everything(seed: int) -> None:
    """Seed every generator that can influence the run.

    Python's ``random`` drives the batch order and torch drives the
    initialisation and dropout. numpy is seeded too when it happens to be
    importable — it is a transformers dependency rather than a declared one, so
    failing to import it is not a reason to stop a run.

    Args:
        seed: The run seed.
    """
    random.seed(seed)
    try:
        import numpy
    except ImportError:  # pragma: no cover - numpy ships with transformers
        return
    numpy.random.seed(seed % (2**32))


def _git_root() -> Path:
    """Find the nearest ancestor directory holding ``.git``.

    Returns:
        The work-tree root, or this file's own directory when there is no work
        tree to find. ``ml.training.manifest.git_revision`` degrades to
        ``("unknown", False)`` rather than raising, so a source tarball still
        trains — it just records that it cannot name a commit.
    """
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / ".git").exists():
            return candidate
    return here.parent


def _lr_schedule(total_steps: int, warmup_steps: int) -> Callable[[int], float]:
    """Build the learning-rate multiplier for one run.

    Linear warmup, then linear decay to zero. Decaying to zero rather than to a
    floor is deliberate for a short schedule on a small corpus: the last epoch is
    where a template-trained router starts memorising, and a rate that has
    already reached zero cannot memorise anything.

    Args:
        total_steps: Optimiser steps the run will take.
        warmup_steps: Steps spent ramping up to the full rate.

    Returns:
        A callable mapping a step index to a multiplier in ``[0, 1]``.
    """
    decay_steps = max(1, total_steps - warmup_steps)

    def multiplier(step: int) -> float:
        if step < warmup_steps:
            return (step + 1) / max(1, warmup_steps)
        return max(0.0, (total_steps - step) / decay_steps)

    return multiplier


def _class_weights(torch: ModuleType, data: TrainingData, strategy: str) -> Any:
    """Build the optional inverse-frequency loss weights.

    The weights come from the training split only. Deriving them from the whole
    corpus would put held-out rows into the loss, which is a quieter version of
    the leakage the splitter exists to prevent.

    Args:
        torch: The imported torch module.
        data: The loaded data.
        strategy: ``"balanced"`` or ``"none"``.

    Returns:
        A 1-D tensor of per-class weights, or None when no weighting was asked
        for. None is the honest answer for a corpus that is already balanced by
        construction, which this one is — so the configured strategy says what it
        wants and this function does not second-guess it.
    """
    if strategy != "balanced":
        return None
    counts = data.class_counts()
    total = float(sum(counts.values()))
    classes = len(data.label2id)
    return torch.tensor(
        [total / (classes * counts.get(index, 1)) for index in range(classes)],
        dtype=torch.float32,
    )


def _count_parameters(model: Any) -> int:
    """Count every parameter the model owns, trainable or frozen.

    ``requires_grad`` is deliberately not consulted. The band in the brief is a
    statement about what has to be resident when the router serves a request,
    and a frozen parameter still occupies memory in that process.

    Args:
        model: A ``torch.nn.Module``.

    Returns:
        The total parameter count.
    """
    return sum(parameter.numel() for parameter in model.parameters())


def _parameter_groups(model: Any, weight_decay: float) -> tuple[list[Any], list[Any]]:
    """Split parameters into decayed and non-decayed groups.

    Biases and layer norms are excluded from weight decay — the standard recipe,
    and on a corpus this small the difference between an encoder that settles
    and one whose embeddings keep drifting.

    Args:
        model: The model.
        weight_decay: The decay to apply to the eligible group.

    Returns:
        The ``(decay, no_decay)`` parameter lists.
    """
    decay: list[Any] = []
    no_decay: list[Any] = []
    for name, parameter in model.named_parameters():
        if not parameter.requires_grad:
            continue
        target = no_decay if name.endswith(("bias", "LayerNorm.weight")) else decay
        target.append(parameter)
    return decay, no_decay


def _encode_all(tokenizer: Any, texts: Sequence[str], *, max_seq_length: int) -> dict[str, Any]:
    """Tokenise a split into padded columns.

    Padding is to ``max_length`` rather than to each batch's maximum so the whole
    split is one rectangular tensor and the batch order alone determines what a
    step sees. At 128 positions and a few thousand rows the memory cost is a
    rounding error, and it buys a resume that does not depend on how some
    particular batch happened to shape up.

    ``token_type_ids`` is dropped: DeBERTa-v3 declares no such input, and passing
    it anyway would be "accepted and ignored", which is how a batch silently
    loses the field it was supposed to carry.

    Args:
        tokenizer: A HuggingFace tokenizer.
        texts: The utterances.
        max_seq_length: Truncation and padding length.

    Returns:
        Columns of equal-length lists.
    """
    encoded = tokenizer(
        list(texts),
        truncation=True,
        max_length=max_seq_length,
        padding="max_length",
    )
    return {key: list(value) for key, value in encoded.items() if key != "token_type_ids"}


def _to_device(torch: ModuleType, rows: Sequence[int], device: str) -> Any:
    """Build one int64 tensor of token ids on the target device.

    Args:
        torch: The imported torch module.
        rows: The token ids for a batch.
        device: The device string.

    Returns:
        A 1-D ``int64`` tensor.
    """
    return torch.as_tensor(list(rows), dtype=torch.long, device=device)


def _batch_tensors(
    torch: ModuleType,
    columns: Mapping[str, Sequence[int]],
    indices: Sequence[int],
    labels: Sequence[int],
    device: str,
) -> dict[str, Any]:
    """Assemble the input dict for one batch.

    Args:
        torch: The imported torch module.
        columns: The tokenised split.
        indices: Row indices in this batch.
        labels: Gold labels for the whole split.
        device: The device string.

    Returns:
        A dict of tensors on ``device``, with ``labels`` included.
    """
    return {
        key: _to_device(torch, [column[index] for index in indices], device)
        for key, column in columns.items()
    } | {"labels": _to_device(torch, [labels[index] for index in indices], device)}


def _evaluate_split(
    torch: ModuleType,
    model: Any,
    *,
    columns: Mapping[str, Sequence[int]],
    labels: Sequence[int],
    batch_size: int,
    device: str,
    id2label: Mapping[int, str],
) -> dict[str, float]:
    """Score the validation split.

    Reported through :func:`ml.evaluation.metrics.evaluate` rather than a
    hand-rolled accuracy, so the numbers in ``training_state.json`` are the same
    shape the eval report and the run manifest will carry, computed by the same
    code. Accuracy alone would also be misleading here: the classes are balanced,
    so a model that had collapsed onto three intents would still score respectably.

    Args:
        torch: The imported torch module.
        model: The model, put into eval mode for the duration.
        columns: Tokenised validation columns.
        labels: Integer gold labels.
        batch_size: Rows per forward pass.
        device: The device string.
        id2label: Class index to intent name.

    Returns:
        ``{"loss", "accuracy", "macro_f1", "weighted_f1"}``.
    """
    model.eval()
    total_loss = 0.0
    predictions: list[int] = []
    with torch.no_grad():
        for start in range(0, len(labels), batch_size):
            stop = min(start + batch_size, len(labels))
            batch = _batch_tensors(torch, columns, range(start, stop), labels, device)
            gold = batch.pop("labels")
            output = model(**batch)
            total_loss += float(torch.nn.functional.cross_entropy(output.logits, gold)) * (
                stop - start
            )
            predictions.extend(int(value) for value in output.logits.argmax(dim=-1).tolist())
    gold_ids = list(labels)
    metrics = evaluate(
        [id2label[value] for value in gold_ids],
        [id2label[value] for value in predictions],
        [id2label[value] for value in sorted(id2label)],
    )
    return {
        "loss": total_loss / max(1, len(gold_ids)),
        "accuracy": metrics.accuracy,
        "macro_f1": metrics.macro_f1,
        "weighted_f1": metrics.weighted_f1,
    }


def _rng_blob(torch: ModuleType) -> dict[str, Any]:
    """Capture every generator whose state affects the next batch.

    Args:
        torch: The imported torch module.

    Returns:
        A serialisable mapping of RNG states. CUDA state is included only when
        there is CUDA state to include.
    """
    blob: dict[str, Any] = {
        "python": random.getstate(),
        "torch_cpu": torch.get_rng_state(),
    }
    if torch.cuda.is_available():
        blob["torch_cuda"] = torch.cuda.get_rng_state_all()
    return blob


def _restore_rng(torch: ModuleType, blob: Mapping[str, Any]) -> None:
    """Put the generators back where a checkpoint left them.

    Args:
        torch: The imported torch module.
        blob: The blob written by :func:`_rng_blob`.
    """
    random.setstate(blob["python"])
    torch.set_rng_state(blob["torch_cpu"])
    if "torch_cuda" in blob and torch.cuda.is_available():
        torch.cuda.set_rng_state_all(blob["torch_cuda"])


def _weights_filename(directory: Path) -> str:
    """Find the weight file ``save_pretrained`` actually wrote.

    Args:
        directory: The checkpoint directory.

    Returns:
        The filename, relative to ``directory``.

    Raises:
        TrainingError: No recognised weight file is present.
    """
    for candidate in ("model.safetensors", "pytorch_model.bin"):
        if (directory / candidate).is_file():
            return candidate
    present = sorted(item.name for item in directory.iterdir() if item.is_file())
    raise TrainingError(
        f"{directory}: save_pretrained wrote no recognisable weights; present: {present}"
    )


def _save_checkpoint(
    *,
    torch: ModuleType,
    directory: Path,
    model: Any,
    tokenizer: Any,
    optimiser: Any,
    metadata: CheckpointMetadata,
    loss_curve: Sequence[LossPoint],
    run_id: str,
    label2id: Mapping[str, int],
    batch_size: int,
    max_seq_length: int,
    steps_per_epoch: int,
) -> CheckpointState:
    """Write one complete checkpoint directory.

    The order here matters and is the whole crash story. The payloads go down
    first and ``checkpoint.json`` is written afterwards by
    :func:`~ml.training.checkpoint.save_checkpoint`, which is what makes the
    presence of that file the commit point. A process killed halfway through
    leaves files without metadata, and
    :func:`~ml.training.checkpoint.latest_checkpoint` refuses a directory like
    that instead of resuming from state nobody can enumerate.

    The optimiser and RNG blobs are declared in the metadata's ``files`` map even
    though ``save_checkpoint`` did not write them, so the completeness check a
    resume performs covers them too.

    Args:
        torch: The imported torch module.
        directory: Destination checkpoint directory.
        model: The model to save.
        tokenizer: The tokenizer to save alongside it.
        optimiser: The optimiser whose state makes the run resumable.
        metadata: The metadata to write.
        loss_curve: The losses recorded so far.
        run_id: The run identifier.
        label2id: The class map this checkpoint's head is sized for.
        batch_size: Batch size in force.
        max_seq_length: Sequence length in force.
        steps_per_epoch: Steps one full epoch takes.

    Returns:
        The state that was written.

    Raises:
        TrainingError: The model wrote weights under a name the resume path
            cannot read.
    """
    directory.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(directory)
    tokenizer.save_pretrained(directory)
    weights = _weights_filename(directory)
    torch.save(optimiser.state_dict(), directory / OPTIMIZER_FILENAME)
    torch.save(_rng_blob(torch), directory / RNG_FILENAME)
    state = save_checkpoint(
        directory,
        metadata=replace(
            metadata,
            files={
                "model": weights,
                "optimizer": OPTIMIZER_FILENAME,
                "rng_state": RNG_FILENAME,
            },
        ),
        tensors={},
        extra={
            "run_id": run_id,
            "label2id": dict(label2id),
            "batch_size": batch_size,
            "max_seq_length": max_seq_length,
            "steps_per_epoch": steps_per_epoch,
            "loss_curve": [point.to_dict() for point in loss_curve],
        },
    )
    return state


def _build_metadata(
    *,
    run_id: str,
    base_model: str,
    global_step: int,
    epoch: int,
    steps_per_epoch: int,
    data: TrainingData,
    settings: SmallTrainingSettings,
    commit: str,
    batch_size: int,
    elapsed_seconds: float,
) -> CheckpointMetadata:
    """Build the ``checkpoint.json`` record for one checkpoint.

    ``segment_index`` and ``segments_completed`` carry the epoch geometry rather
    than being left at zero, because a resume has to know which epoch the run
    died in as well as how far it got: those are the two fields
    :func:`ml.training.checkpoint.latest_checkpoint` hands to a trainer that
    wants to regenerate the same shuffle.

    Args:
        run_id: The run identifier.
        base_model: The base checkpoint name.
        global_step: Optimiser steps completed.
        epoch: The epoch the run is in.
        steps_per_epoch: Steps one full epoch takes.
        data: The loaded data, for its version and checksum.
        settings: The resolved settings.
        commit: The git revision.
        batch_size: Batch size in force.
        elapsed_seconds: Seconds since the run started.

    Returns:
        The metadata, ready for
        :func:`~ml.training.checkpoint.save_checkpoint`.
    """
    return CheckpointMetadata(
        format_version=CHECKPOINT_FORMAT_VERSION,
        run_id=run_id,
        model_name=f"{base_model}/routing-intent",
        global_step=global_step,
        epoch=epoch,
        segment_index=global_step % max(1, steps_per_epoch),
        segments_completed=global_step // max(1, steps_per_epoch),
        dataset_version=data.dataset_version,
        dataset_checksum=data.checksum,
        code_commit=commit,
        config=settings.to_dict(),
        hyperparameters={
            "learning_rate": settings.learning_rate,
            "weight_decay": settings.weight_decay,
            "per_device_train_batch_size": batch_size,
            "max_seq_length": settings.max_seq_length,
            "warmup_ratio": settings.warmup_ratio,
            "weighting_strategy": settings.weighting_strategy,
        },
        seed=settings.seed,
        created_at=datetime.now(UTC).isoformat(),
        elapsed_seconds=elapsed_seconds,
        files={},
    )


def _write_checkpoint(
    *,
    torch: ModuleType,
    directory: Path,
    model: Any,
    tokenizer: Any,
    optimiser: Any,
    run_id: str,
    global_step: int,
    epoch: int,
    steps_per_epoch: int,
    data: TrainingData,
    settings: SmallTrainingSettings,
    commit: str,
    loss_curve: Sequence[LossPoint],
    batch_size: int,
) -> CheckpointState:
    """Write one checkpoint and return where it landed.

    Args:
        torch: The imported torch module.
        directory: The checkpoint directory to create.
        model: The model being trained.
        tokenizer: The tokenizer in use.
        optimiser: The optimiser in use.
        run_id: The run identifier.
        global_step: Optimiser steps completed.
        epoch: The current epoch.
        steps_per_epoch: Steps one full epoch takes.
        data: The loaded data.
        settings: The resolved settings.
        commit: The git revision.
        loss_curve: Losses recorded so far.
        batch_size: Batch size in force.

    Returns:
        The state that was written.
    """
    metadata = _build_metadata(
        run_id=run_id,
        base_model=settings.base_model,
        global_step=global_step,
        epoch=epoch,
        steps_per_epoch=steps_per_epoch,
        data=data,
        settings=settings,
        commit=commit,
        batch_size=batch_size,
        elapsed_seconds=loss_curve[-1].elapsed_seconds if loss_curve else 0.0,
    )
    return _save_checkpoint(
        torch=torch,
        directory=directory,
        model=model,
        tokenizer=tokenizer,
        optimiser=optimiser,
        metadata=metadata,
        loss_curve=loss_curve,
        run_id=run_id,
        label2id=data.label2id,
        batch_size=batch_size,
        max_seq_length=settings.max_seq_length,
        steps_per_epoch=steps_per_epoch,
    )


def _resume_from(
    root: Path,
    *,
    expected_checksum: str,
    expected_dataset_version: str,
) -> CheckpointState | None:
    """Find the furthest trustworthy checkpoint under a root.

    :func:`ml.training.checkpoint.latest_checkpoint` already refuses a directory
    that is missing its metadata or any file it declares, and warns about each
    one. What this adds is the second question: is this checkpoint even about
    *this* run? A checkpoint whose ``dataset_checksum`` differs was trained on
    different rows, and continuing into it produces something that looks like a
    continuation and is not one.

    Args:
        root: The checkpoints directory.
        expected_checksum: Digest of the splits about to be loaded.
        expected_dataset_version: Dataset version about to be loaded.

    Returns:
        The state to resume from, or None when there is nothing to resume.

    Raises:
        TrainingError: A checkpoint exists but belongs to different data.
    """
    state = latest_checkpoint(root)
    if state is None:
        return None
    metadata = state.metadata
    if metadata.dataset_checksum != expected_checksum:
        raise TrainingError(
            f"refusing to resume from {state.directory}: it was trained on a different "
            f"dataset (checksum {metadata.dataset_checksum[:12]}, current "
            f"{expected_checksum[:12]})"
        )
    if metadata.dataset_version != expected_dataset_version:
        raise TrainingError(
            f"refusing to resume from {state.directory}: dataset version "
            f"{metadata.dataset_version!r} does not match {expected_dataset_version!r}"
        )
    return state


def _restore(
    torch: ModuleType,
    state: CheckpointState,
    *,
    model: Any,
    optimiser: Any,
) -> tuple[int, int, int, list[LossPoint]]:
    """Restore model, optimiser and RNG from a checkpoint.

    The batch offset is returned rather than recomputed later because the
    shuffle is a pure function of ``(seed, epoch)``: restoring the step index
    only means something if the epoch is regenerated identically. Skipping the
    batches already consumed is what makes the resumed loss curve continuous with
    the pre-crash one instead of quietly training on them twice.

    Args:
        torch: The imported torch module.
        state: The checkpoint to restore, already validated by
            :func:`~ml.training.checkpoint.load_checkpoint`.
        model: The model to restore into.
        optimiser: The optimiser to restore into.

    Returns:
        ``(global_step, epoch, batches_to_skip, loss_curve)``.

    Raises:
        TrainingError: The checkpoint is missing a file its metadata declares,
            or its tensors cannot be read.
    """
    files = state.metadata.files
    for logical in ("model", "optimizer", "rng_state", "extra"):
        if logical not in files:
            raise TrainingError(f"{state.directory}: checkpoint does not carry {logical!r}")
    _load_weights(torch, model, state.directory / files["model"])
    optimiser.load_state_dict(
        torch.load(state.directory / files["optimizer"], map_location="cpu", weights_only=True)
    )
    _restore_rng(
        torch,
        torch.load(state.directory / files["rng_state"], map_location="cpu", weights_only=True),
    )
    extra = json.loads((state.directory / files["extra"]).read_text(encoding="utf-8"))
    loss_curve = [
        LossPoint(
            step=int(point["step"]),
            epoch=int(point["epoch"]),
            loss=float(point["loss"]),
            learning_rate=float(point["learning_rate"]),
            elapsed_seconds=float(point["elapsed_seconds"]),
        )
        for point in extra.get("loss_curve", [])
    ]
    steps_per_epoch = max(1, int(extra.get("steps_per_epoch", 1)))
    global_step = state.metadata.global_step
    skipped = global_step % steps_per_epoch
    epoch = state.metadata.epoch + (1 if global_step > 0 and skipped == 0 else 0)
    return global_step, epoch, skipped, loss_curve


def _load_weights(torch: ModuleType, model: Any, path: Path) -> None:
    """Load a checkpoint's weights into an already-constructed model.

    The raw state dict is read rather than ``save_pretrained`` output being
    re-instantiated from, because the classification head is randomly
    initialised here and has to be replaced either way. Reading the tensors keeps
    the fresh-run and resumed-run paths identical right up to the moment the
    weights are applied.

    Args:
        torch: The imported torch module.
        model: The model to load into.
        path: The ``.safetensors`` or ``.bin`` file.

    Raises:
        TrainingError: The weights do not match the model being built.
    """
    if path.suffix == ".safetensors":
        from safetensors.torch import load_file

        state = load_file(str(path))
    else:
        state = torch.load(path, map_location="cpu", weights_only=True)
    try:
        model.load_state_dict(state)
    except (RuntimeError, KeyError) as exc:
        raise TrainingError(f"cannot restore weights from {path}: {exc}") from exc


def _epoch_batches(size: int, *, batch_size: int, seed: int, epoch: int) -> list[tuple[int, ...]]:
    """Shuffle one epoch into batch index tuples.

    The seed is derived per epoch so the order is a pure function of
    ``(seed, epoch)`` and a resumed run regenerates exactly the sequence the
    crashed run was walking — which is the property that makes skipping batches
    correct rather than approximately right.

    Args:
        size: Number of rows in the split.
        batch_size: Rows per optimiser step.
        seed: The run seed.
        epoch: The epoch index.

    Returns:
        The batches, in order.
    """
    rng = _SeededRandom(seed * 1000003 + epoch)
    order = list(range(size))
    rng.shuffle(order)
    return [tuple(order[start : start + batch_size]) for start in range(0, size, batch_size)]


def _log(message: str) -> None:
    """Print one line of run output, flushed so a piped log stays in order.

    Args:
        message: The line.
    """
    print(message, flush=True)


def _run(args: argparse.Namespace) -> int:
    """Do the whole run: read, build, train, checkpoint, save, report.

    Args:
        args: The parsed arguments.

    Returns:
        Process exit status; 0 on a completed run.

    Raises:
        TrainingError: Anything that would invalidate the artifact.
    """
    settings = _resolve_overrides(args, _read_settings(args.config))
    data = load_training_data(args.data_dir, settings)

    torch = _load_torch()
    import transformers

    device = _resolve_device(torch, args.device, args.threads)
    _seed_everything(settings.seed)
    started = time.monotonic()
    started_at = datetime.now(UTC)
    commit, dirty = git_revision(_git_root())
    run_id = new_run_id(
        "small", when=started_at, dataset_version=data.dataset_version, seed=settings.seed
    )

    artifact_root = args.artifacts_dir / ARTIFACT_DIRNAME
    checkpoint_root = artifact_root / CHECKPOINTS_DIRNAME
    _log(f"run id       {run_id}")
    _log(f"device       {device}  (torch {torch.__version__}, threads {torch.get_num_threads()})")
    _log(f"transformers {transformers.__version__}")
    _log(f"code commit  {commit}{' (dirty)' if dirty else ''}")
    _log(f"base model   {settings.base_model}")

    tokenizer = transformers.AutoTokenizer.from_pretrained(settings.base_model, use_fast=True)
    train_columns = _encode_all(tokenizer, data.train.texts, max_seq_length=settings.max_seq_length)
    validation_columns = _encode_all(
        tokenizer, data.validation.texts, max_seq_length=settings.max_seq_length
    )
    _log(
        f"tokenized    train {len(data.train)} rows, validation {len(data.validation)} rows, "
        f"max_seq_length {settings.max_seq_length}"
    )

    model = transformers.AutoModelForSequenceClassification.from_pretrained(
        settings.base_model,
        num_labels=len(data.label2id),
        id2label={
            index: name for name, index in sorted(data.label2id.items(), key=lambda item: item[1])
        },
        label2id=dict(data.label2id),
    )
    parameters = _count_parameters(model)
    low, high = settings.parameter_count_band
    _log(f"parameters   {parameters:,}")
    if not low <= parameters <= high:
        raise TrainingError(
            f"{settings.base_model} has {parameters:,} parameters, outside the required band "
            f"[{low:,}, {high:,}]"
        )
    # The published checkpoint stores fp16 weights, which a CPU run would
    # otherwise train in: half precision has no usable optimiser state on this
    # hardware, and every fp16 master weight is a rounding error applied to the
    # embedding table on every update. Upcasting once is cheaper than the
    # accuracy it costs to leave it.
    model = model.float()
    model.to(device)

    decay, no_decay = _parameter_groups(model, settings.weight_decay)
    optimiser = torch.optim.AdamW(
        [
            {"params": decay, "weight_decay": settings.weight_decay},
            {"params": no_decay, "weight_decay": 0.0},
        ],
        lr=settings.learning_rate,
    )
    weights = _class_weights(torch, data, settings.weighting_strategy)
    if weights is not None:
        weights = weights.to(device)

    batch_size = settings.per_device_train_batch_size
    steps_per_epoch = max(1, -(-len(data.train) // batch_size))
    total_steps = steps_per_epoch * settings.num_train_epochs
    warmup_steps = int(total_steps * settings.warmup_ratio)
    scheduler = torch.optim.lr_scheduler.LambdaLR(
        optimiser, _lr_schedule(total_steps, warmup_steps)
    )
    _log(
        f"schedule     {settings.num_train_epochs} epochs x {steps_per_epoch} steps = "
        f"{total_steps} steps (warmup {warmup_steps}), batch {batch_size}, "
        f"lr {settings.learning_rate}"
    )
    if args.max_steps:
        _log(f"step cap     {args.max_steps} (--max-steps; the full schedule is {total_steps})")

    resume_state = (
        _resume_from(
            checkpoint_root,
            expected_checksum=data.checksum,
            expected_dataset_version=data.dataset_version,
        )
        if args.resume
        else None
    )
    loss_curve: list[LossPoint] = []
    global_step = 0
    first_epoch = 0
    batches_to_skip = 0
    if resume_state is None:
        _log("resuming     no - starting fresh")
    else:
        global_step, first_epoch, batches_to_skip, loss_curve = _restore(
            torch, resume_state, model=model, optimiser=optimiser
        )
        _log(
            f"resuming     yes - {resume_state.directory} at step {global_step}, "
            f"epoch {first_epoch}, batch {batches_to_skip} of {steps_per_epoch}"
        )
    # A resumed run's clock does not start at zero: the pre-crash process spent
    # real time, and a loss curve whose time axis restarts mid-run draws a step
    # discontinuity that never happened.
    elapsed_offset = loss_curve[-1].elapsed_seconds if loss_curve else 0.0

    initial = _evaluate_split(
        torch,
        model,
        columns=validation_columns,
        labels=data.validation.labels,
        batch_size=batch_size,
        device=device,
        id2label=data.id2label,
    )
    _log(
        f"eval         step {global_step} loss {initial['loss']:.4f} "
        f"accuracy {initial['accuracy']:.4f} macro_f1 {initial['macro_f1']:.4f}"
    )

    limit = args.max_steps if args.max_steps else None
    model.train()
    stopped_early = False
    for epoch in range(first_epoch, settings.num_train_epochs):
        # The skip is applied by slicing the *full* epoch rather than by
        # truncating it first. Truncating first would drop the batches a resume
        # still owes the run, and the resumed schedule would quietly start the
        # next epoch early — a continuation in name only.
        epoch_batches = _epoch_batches(
            len(data.train),
            batch_size=batch_size,
            seed=settings.seed,
            epoch=epoch,
        )
        skipped_here = batches_to_skip if epoch == first_epoch else 0
        if limit is not None:
            epoch_batches = epoch_batches[: skipped_here + max(0, limit - global_step)]
        for position, batch in enumerate(epoch_batches, start=1):
            if position <= skipped_here:
                continue
            inputs = _batch_tensors(torch, train_columns, batch, data.train.labels, device)
            gold = inputs.pop("labels")
            output = model(**inputs)
            # The class weights are cast to the logit dtype rather than assumed
            # to match it: torch refuses a float32 weight tensor against half
            # logits, and a run that dies at step 1 for a dtype is a run that
            # never produces the curve it was written to produce.
            loss = torch.nn.functional.cross_entropy(
                output.logits,
                gold,
                weight=None if weights is None else weights.to(output.logits.dtype),
            )
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), MAX_GRAD_NORM)
            optimiser.step()
            scheduler.step()
            optimiser.zero_grad(set_to_none=True)
            global_step += 1
            loss_curve.append(
                LossPoint(
                    step=global_step,
                    epoch=epoch,
                    loss=float(loss.detach()),
                    learning_rate=float(scheduler.get_last_lr()[0]),
                    elapsed_seconds=time.monotonic() - started + elapsed_offset,
                )
            )
            if global_step % PROGRESS_EVERY_N_STEPS == 0:
                _log(
                    f"step {global_step}/{total_steps}  epoch {epoch}  "
                    f"loss {loss_curve[-1].loss:.4f}  lr {loss_curve[-1].learning_rate:.2e}  "
                    f"elapsed {loss_curve[-1].elapsed_seconds:.1f}s"
                )
            if global_step % settings.eval_every_n_steps == 0:
                scores = _evaluate_split(
                    torch,
                    model,
                    columns=validation_columns,
                    labels=data.validation.labels,
                    batch_size=batch_size,
                    device=device,
                    id2label=data.id2label,
                )
                _log(
                    f"eval         step {global_step} loss {scores['loss']:.4f} "
                    f"accuracy {scores['accuracy']:.4f} macro_f1 {scores['macro_f1']:.4f}"
                )
                model.train()
            if global_step % settings.save_every_n_steps == 0:
                _write_checkpoint(
                    torch=torch,
                    directory=checkpoint_root / f"step-{global_step}",
                    model=model,
                    tokenizer=tokenizer,
                    optimiser=optimiser,
                    run_id=run_id,
                    global_step=global_step,
                    epoch=epoch,
                    steps_per_epoch=steps_per_epoch,
                    data=data,
                    settings=settings,
                    commit=commit,
                    loss_curve=loss_curve,
                    batch_size=batch_size,
                )
                _log(f"checkpoint   {checkpoint_root / f'step-{global_step}'}")

        if limit is not None and global_step >= limit:
            stopped_early = True
            break

    final = _evaluate_split(
        torch,
        model,
        columns=validation_columns,
        labels=data.validation.labels,
        batch_size=batch_size,
        device=device,
        id2label=data.id2label,
    )
    _log(
        f"final        loss {final['loss']:.4f} accuracy {final['accuracy']:.4f} "
        f"macro_f1 {final['macro_f1']:.4f}"
    )

    final_dir = artifact_root / FINAL_DIRNAME
    final_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(final_dir)
    tokenizer.save_pretrained(final_dir)
    (final_dir / LABEL_MAP_FILENAME).write_text(
        stable_json_dumps(
            {
                "label2id": dict(sorted(data.label2id.items(), key=lambda item: item[1])),
                "id2label": {str(key): value for key, value in sorted(data.id2label.items())},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    duration = time.monotonic() - started + elapsed_offset
    state = {
        "run_id": run_id,
        "base_model": settings.base_model,
        "device": device,
        "torch_version": torch.__version__,
        "transformers_version": transformers.__version__,
        "parameter_count": parameters,
        "parameter_count_band": [low, high],
        "seed": settings.seed,
        "steps": global_step,
        "planned_steps": total_steps,
        "epochs": len({point.epoch for point in loss_curve}),
        "steps_per_epoch": steps_per_epoch,
        "batch_size": batch_size,
        "max_seq_length": settings.max_seq_length,
        "duration_seconds": round(duration, 3),
        "stopped_early": stopped_early,
        "resumed": resume_state is not None,
        "resumed_from": str(resume_state.directory) if resume_state else None,
        "loss_curve": [point.to_dict() for point in loss_curve],
        "first_loss": loss_curve[0].loss if loss_curve else None,
        "final_loss": loss_curve[-1].loss if loss_curve else None,
        "validation": final,
        "initial_validation": initial,
        "num_labels": len(data.label2id),
        "label2id": dict(sorted(data.label2id.items(), key=lambda item: item[1])),
        "dataset_version": data.dataset_version,
        "label_map_source": data.label_map_source,
        "dataset_checksum": data.checksum,
        "train_rows": len(data.train),
        "validation_rows": len(data.validation),
        "train_checksum": data.train.checksum,
        "validation_checksum": data.validation.checksum,
        "code_commit": commit,
        "code_dirty": dirty,
        "config": settings.to_dict(),
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "artifacts": {
            "final": str(final_dir),
            "checkpoints": str(checkpoint_root),
            "state": str(artifact_root / STATE_FILENAME),
        },
    }
    state_path = artifact_root / STATE_FILENAME
    state_path.write_text(stable_json_dumps(state) + "\n", encoding="utf-8")
    _log(f"final model  {final_dir}")
    _log(f"state        {state_path}")
    _log(
        f"done         {global_step} steps in {duration:.1f}s, parameters {parameters:,}, "
        f"device {device}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the local routing trainer.

    Args:
        argv: Command-line arguments, or None to read ``sys.argv[1:]``.

    Returns:
        Process exit status. 0 means the run finished and wrote its artifacts;
        1 means it stopped on a :class:`TrainingError`, whose message has already
        been printed to stderr.
    """
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except TrainingError as exc:
        print(f"error: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
