"""Turning a Phase 10 checkpoint directory into a loaded, ready-to-call model.

**This module knows about weights and devices, and about nothing else.** There is
no threshold, no fallback policy, no destination and no HTTP concern here — the
loader answers one question, "is there a usable classifier in this process?", and
returns something that can answer "which intent is this?". Every decision about
what to *do* with a prediction belongs to
:mod:`app.ml.router`, and every decision about whether one is worth trusting
belongs to :class:`app.ml.classifier.IntentClassifier`'s caller. Keeping the
split sharp is what makes the loader testable without a request, a user or a
database.

**Every heavy import is inside a function.** ``torch`` and ``transformers`` are
a multi-hundred-megabyte dependency, and the whole point of the lazy import is
that NEXUS boots and serves every other route on a machine that does not have
them. A module-scope ``import torch`` would turn "this deployment has no
classifier" into "this application will not start", which is a strictly worse
failure with a strictly worse error message. :class:`app.ml.exceptions.ModelRuntimeError`
carries the name of the missing package so the operator can install it instead.

**The label map is validated against the taxonomy at load, not assumed.** The
checkpoint's ``id2label`` is the contract between Phase 10 and Phase 11: it maps
a class index to the intent name the rest of the application will look up. A
silent reorder does not raise — it routes ``schedule_plan`` utterances to the
knowledge router with a confident-looking 0.99 and nothing anywhere reports an
error. So the loaded labels are compared index-by-index against
:func:`ml.datasets.routing.label_map`, which is derived from
:class:`ml.datasets.taxonomy.Intent`, and a single disagreement refuses the load.

**The trained context length is checked against the checkpoint's own encoder.** A
checkpoint from a different run that recorded a longer trained window than its
``config.json`` declares room for would otherwise load, score, and quietly be a
different classifier from the one that was measured. DeBERTa stretches its
relative-position embedding past ``max_position_embeddings`` rather than refusing,
so nothing downstream would notice.

**Exception messages never carry an absolute path.** The checkpoint lives on the
server's filesystem and that is deployment information, not client information;
:attr:`app.ml.schemas.ModelIdentity.checkpoint` holds it for the logs and the
authenticated diagnostics endpoint instead, where it is useful.

**CPU is a first-class device.** The trained checkpoint is 703 MiB and the vast
majority of NEXUS deployments will run it on a CPU box, so ``cpu`` is fully
supported and ``auto`` resolves to it. ``cuda`` is honoured when it is asked for
and *fails loudly* when it is unavailable, because an operator who requested a
GPU and silently received a CPU has a capacity problem nobody will notice until
the p99 does.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from app.core.logging import get_logger, log_event
from app.ml.exceptions import ModelCheckpointError, ModelRuntimeError
from app.ml.schemas import ModelIdentity

__all__ = [
    "DEFAULT_BASE_MODEL",
    "MAX_SEQUENCE_LENGTH",
    "REQUIRED_CHECKPOINT_FILES",
    "SUPPORTED_DEVICE_REQUESTS",
    "LoadedModel",
    "default_checkpoint_dir",
    "load_model",
    "resolve_checkpoint_dir",
    "resolve_device",
]

logger = get_logger(__name__)

#: The context length the checkpoint was trained at, from
#: ``ml/configs/small_model.toml`` (``max_seq_length = 128``) and recorded in
#: ``training_state.json``. Tokenising at any other length is a distribution
#: shift the model has never seen: a longer window hands the encoder positions it
#: was not trained to use, and the positional buckets DeBERTa-v3 relies on were
#: calibrated at this width. :func:`load_model` prefers the value recorded
#: alongside the weights and falls back to this constant.
MAX_SEQUENCE_LENGTH = 128

#: Recorded only as a fallback for :attr:`ModelIdentity.base_model` when the
#: checkpoint's own config carries no ``_name_or_path``. It is the encoder the
#: Phase 10 config trained from.
DEFAULT_BASE_MODEL = "microsoft/deberta-v3-base"

#: Where ``ml/train.py`` and ``ml/scripts/train_small_local.py`` write the final
#: weights, resolved relative to this file so it does not depend on the working
#: directory the server happens to be started from.
DEFAULT_CHECKPOINT_DIR = (
    Path(__file__).resolve().parents[2] / "ml" / "artifacts" / "small-model" / "final"
)

#: A directory with all of these is a loadable checkpoint. Listing them is what
#: turns "the tokenizer could not be initialised" into "the checkpoint is
#: missing model.safetensors", which is a sentence an operator can act on.
REQUIRED_CHECKPOINT_FILES: tuple[str, ...] = (
    "config.json",
    "label_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "model.safetensors",
)

#: Written next to the final weights by the Phase 10 training run. Optional: it
#: carries the trained ``max_seq_length`` and base model, but the checkpoint is
#: loadable without it.
_TRAINING_STATE_FILENAME = "training_state.json"

#: Accepted values of the ``device`` argument. Anything else is a configuration
#: mistake and is rejected rather than coerced.
SUPPORTED_DEVICE_REQUESTS: tuple[str, ...] = ("auto", "cpu", "cuda")


@dataclass(frozen=True, slots=True)
class LoadedModel:
    """A checkpoint that is on a device, in eval mode, and ready to classify.

    Frozen because a loaded model is shared state: every request thread reads
    the same tokenizer, the same weights and the same label order, and a
    component that could swap any of them mid-request would be a race rather than
    a feature. Reloading is done by constructing a new instance and dropping the
    old one, which is why :meth:`app.ml.classifier.IntentClassifier.close` exists.

    Attributes:
        tokenizer: The fast ``DebertaV2Tokenizer`` loaded from the checkpoint.
        model: The ``DebertaV2ForSequenceClassification`` in ``eval()`` mode with
            every parameter's ``requires_grad`` cleared.
        device: Resolved device name, ``"cpu"`` or ``"cuda"``.
        id2label: Intent name per class index, ordered by index. The classifier
            resolves a prediction's argmax through this tuple rather than through
            the model's own ``config.id2label`` so that the labels it emits are
            the ones that were validated against the taxonomy at load.
        identity: What loaded, from where, and at what cost, for logs and the
            diagnostics endpoint.
        torch_module: The imported torch module, so the classifier can reach
            ``inference_mode`` and ``softmax`` without importing torch itself.
    """

    tokenizer: Any
    model: Any
    device: str
    id2label: tuple[str, ...]
    identity: ModelIdentity
    torch_module: ModuleType

    @property
    def max_sequence_length(self) -> int:
        """The context length this checkpoint was trained at.

        Read from :class:`ModelIdentity` rather than re-derived, so the number the
        classifier tokenises with and the number reported by the diagnostics
        endpoint cannot disagree.
        """
        return self.identity.max_sequence_length


def default_checkpoint_dir() -> Path:
    """The checkpoint location this deployment expects.

    Returns:
        The ``ml/artifacts/small-model/final`` path. Existence is *not* checked
        here; :func:`resolve_checkpoint_dir` is what turns an absent directory
        into a :class:`~app.ml.exceptions.ModelCheckpointError`.
    """
    return DEFAULT_CHECKPOINT_DIR


def resolve_checkpoint_dir(checkpoint_dir: str | Path | None = None) -> Path:
    """Resolve a checkpoint directory and confirm every required file is usable.

    Args:
        checkpoint_dir: The directory holding the Phase 10 outputs. ``None`` means
            :func:`default_checkpoint_dir`.

    Returns:
        The resolved directory path.

    Raises:
        ModelCheckpointError: The directory does not exist, is not a directory,
            or a required member file is missing or unreadable. The message names
            the kind of problem and the file's *name*, never the absolute path.
    """
    path = Path(checkpoint_dir).expanduser() if checkpoint_dir else default_checkpoint_dir()
    if not path.exists():
        raise ModelCheckpointError("checkpoint directory not found")
    if not path.is_dir():
        raise ModelCheckpointError("checkpoint path is not a directory")

    for name in REQUIRED_CHECKPOINT_FILES:
        member = path / name
        if not member.is_file():
            raise ModelCheckpointError(f"checkpoint is missing {name}")
        # Checked separately from existence because a file that exists but cannot
        # be opened fails deep inside safetensors with a message about mmap.
        if not os.access(member, os.R_OK):
            raise ModelCheckpointError(f"checkpoint file {name} is not readable")
    return path.resolve()


def resolve_device(torch_module: ModuleType, requested: str = "auto") -> str:
    """Choose the device the model will run on.

    Args:
        torch_module: The imported torch module, used only for
            ``torch.cuda.is_available()``.
        requested: ``"auto"``, ``"cpu"`` or ``"cuda"``, case-insensitive.

    Returns:
        ``"cuda"`` when CUDA is available and wanted, otherwise ``"cpu"``.

    Raises:
        ValueError: The request is not one of :data:`SUPPORTED_DEVICE_REQUESTS`.
            A typo in deployment configuration is a mistake to report, not to
            round to a working default.
        ModelRuntimeError: ``"cuda"`` was requested but this torch build reports
            no usable CUDA device. Deliberately not a silent fallback to CPU: an
            operator who asked for a GPU and got a CPU has a capacity problem,
            and hiding it turns a startup error into a latency incident nobody
            connects to this decision.
    """
    choice = requested.strip().lower()
    if choice not in SUPPORTED_DEVICE_REQUESTS:
        raise ValueError(
            f"unsupported device request {requested!r}; expected one of "
            f"{list(SUPPORTED_DEVICE_REQUESTS)}"
        )
    if choice == "cpu":
        return "cpu"

    cuda_available = bool(torch_module.cuda.is_available())
    if choice == "cuda":
        if not cuda_available:
            raise ModelRuntimeError(
                "device 'cuda' was requested but this torch build reports no usable "
                "CUDA device; install a CUDA-enabled torch or request 'cpu'"
            )
        return "cuda"
    return "cuda" if cuda_available else "cpu"


def load_model(
    checkpoint_dir: str | Path | None = None,
    *,
    device: str = "auto",
) -> LoadedModel:
    """Load the intent classifier and return it ready for inference.

    The order of work is chosen so the cheapest and most diagnostic failures
    come first: the directory and its files, then the JSON, then the label
    contract, and only then the hundreds of megabytes of weights. A machine
    without torch learns that the checkpoint is also missing rather than waiting
    through an import attempt first.

    Args:
        checkpoint_dir: Directory holding the Phase 10 outputs, or ``None`` for
            :func:`default_checkpoint_dir`.
        device: ``"auto"``, ``"cpu"`` or ``"cuda"``; see :func:`resolve_device`.

    Returns:
        A :class:`LoadedModel` whose model is in ``eval()`` mode, has every
        parameter's gradient tracking cleared, and sits on the resolved device.

    Raises:
        ModelCheckpointError: The directory, a member file, the JSON, the label
            map, the tokenizer or the weights are unusable.
        ModelRuntimeError: torch or transformers is not installed, or the
            requested device cannot be provided.
    """
    started = time.perf_counter()
    checkpoint = resolve_checkpoint_dir(checkpoint_dir)

    trained = _trained_settings(checkpoint)
    config = _read_json(checkpoint / "config.json", "config.json")
    sidecar = _read_json(checkpoint / "label_map.json", "label_map.json")
    # Before the weights: a checkpoint whose labels disagree with the taxonomy is
    # unusable however well it loads, and saying so in milliseconds beats saying
    # so after a 700 MiB read.
    id2label = _validate_labels(
        _int_keyed_labels(config.get("id2label"), "config.json id2label"),
        sidecar,
    )
    max_sequence_length = _resolve_max_sequence_length(trained)
    # Before the weights, for the same reason as the label check and because the
    # failure it prevents is silent rather than loud.
    _validate_context_length(max_sequence_length, config)

    torch_module = _import_torch()
    transformers = _import_transformers()
    device_name = resolve_device(torch_module, device)

    tokenizer = _load_tokenizer(transformers, checkpoint)
    model = _load_weights(transformers, checkpoint)

    model.eval()
    model.requires_grad_(False)
    model.to(device_name)

    parameter_count = sum(parameter.numel() for parameter in model.parameters())
    load_seconds = time.perf_counter() - started
    identity = ModelIdentity(
        base_model=_resolve_base_model(config, trained),
        architecture=_resolve_architecture(config),
        device=device_name,
        label_count=len(id2label),
        max_sequence_length=max_sequence_length,
        parameter_count=parameter_count,
        checkpoint=str(checkpoint),
        load_seconds=load_seconds,
    )

    log_event(
        logger,
        logging.INFO,
        "ml.model_loaded",
        model=identity.base_model,
        architecture=identity.architecture,
        device=identity.device,
        label_count=identity.label_count,
        max_sequence_length=identity.max_sequence_length,
        parameter_count=identity.parameter_count,
        checkpoint=identity.checkpoint,
        load_seconds=round(identity.load_seconds, 3),
    )
    return LoadedModel(
        tokenizer=tokenizer,
        model=model,
        device=device_name,
        id2label=id2label,
        identity=identity,
        torch_module=torch_module,
    )


def _read_json(path: Path, filename: str) -> dict[str, Any]:
    """Read one checkpoint JSON file.

    Args:
        path: The file to read.
        filename: Its name, used in messages so the operator learns which member
            is broken without being handed the server's directory layout.

    Returns:
        The decoded top-level object.

    Raises:
        ModelCheckpointError: The file cannot be read, is not valid JSON, or is
            not a JSON object.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ModelCheckpointError(f"checkpoint file {filename} could not be read") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelCheckpointError(f"checkpoint file {filename} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ModelCheckpointError(f"checkpoint file {filename} is not a JSON object")
    return payload


def _int_keyed_labels(section: Any, source: str) -> dict[int, str]:
    """Normalise a ``{"0": "task_manage", ...}`` section into integer keys.

    Transformers writes these keys as JSON strings while Python indexes them as
    integers, so the two are normalised here once rather than being compared in
    two shapes at every use site.

    Args:
        section: The value read from the checkpoint JSON.
        source: How to name this section in an error message.

    Returns:
        Class index to intent name.

    Raises:
        ModelCheckpointError: The section is absent, empty, keyed by something
            that is not an index, or maps an index to a non-string label.
    """
    if not isinstance(section, dict) or not section:
        raise ModelCheckpointError(f"{source} is missing from the checkpoint")
    labels: dict[int, str] = {}
    for key, name in section.items():
        try:
            index = int(key)
        except (TypeError, ValueError) as exc:
            raise ModelCheckpointError(f"{source} has a class key that is not an index") from exc
        if not isinstance(name, str) or not name:
            raise ModelCheckpointError(f"{source} maps class {index} to a label that is not a name")
        labels[index] = name
    return labels


def _expected_labels() -> dict[str, int]:
    """The taxonomy's own intent-name to class-index mapping.

    Read from :mod:`ml.datasets.routing` rather than from the taxonomy's members
    directly because that function is the one Phase 10 trained against, and
    comparing two copies of the same fact is how a drift check becomes a second
    source of truth.

    Returns:
        Every intent name with the index the trained head uses for it.

    Raises:
        ModelCheckpointError: The taxonomy package is not importable, so the
            checkpoint cannot be validated against anything. Refusing to load is
            the only safe answer: an unvalidated label map is the failure this
            check exists to prevent.
    """
    try:
        from ml.datasets.routing import label_map
    except ImportError as exc:  # pragma: no cover - depends on the deployment layout
        raise ModelCheckpointError(
            "the ml.datasets.routing taxonomy is not importable, so the checkpoint's "
            "label map cannot be validated against it"
        ) from exc
    return label_map()


def _validate_labels(config_labels: dict[int, str], sidecar: dict[str, Any]) -> tuple[str, ...]:
    """Check the checkpoint's labels against the taxonomy, index by index.

    ``id2label`` is the one section that matters. ``label2id`` is its inverse and
    ``config.num_labels`` is its length, so a checkpoint whose ``id2label``
    matches the taxonomy cannot disagree about either — checking the inverses
    would only add ways for a correct checkpoint to be rejected.

    Args:
        config_labels: The checkpoint's ``config.json`` id2label, integer-keyed.
        sidecar: The decoded ``label_map.json``.

    Returns:
        The label for each class index, dense from zero.

    Raises:
        ModelCheckpointError: The checkpoint and the taxonomy disagree on any
            class, or the two checkpoint files describe different label maps.
    """
    expected_by_index = {index: name for name, index in _expected_labels().items()}

    for index in sorted(set(expected_by_index) | set(config_labels)):
        expected = expected_by_index.get(index)
        actual = config_labels.get(index)
        if expected != actual:
            raise ModelCheckpointError(
                f"checkpoint class {index} is labelled {actual!r} but the intent taxonomy "
                f"expects {expected!r}; this checkpoint was trained against a different "
                "label set and cannot serve the current one"
            )

    sidecar_labels = _int_keyed_labels(sidecar.get("id2label"), "label_map.json id2label")
    if sidecar_labels != config_labels:
        raise ModelCheckpointError(
            "the checkpoint's label_map.json and config.json describe different label maps"
        )

    return tuple(expected_by_index[index] for index in range(len(expected_by_index)))


def _validate_context_length(max_sequence_length: int, config: dict[str, Any]) -> None:
    """Refuse a trained context length the checkpoint's encoder cannot reach.

    The label check above catches a checkpoint trained against a different *label
    set*. This catches a different *window*. DeBERTa's relative-position
    embedding is sized to ``max_position_embeddings`` and is stretched past it
    rather than refused, so a checkpoint whose ``training_state.json`` records a
    longer trained context than its own config was built for loads cleanly, runs a
    forward pass, and returns a confident-looking intent computed from positions
    the encoder has never been fitted on. Nothing raises and nothing reports an
    error — the classifier is simply a different classifier from the one whose
    accuracy was recorded.

    Args:
        max_sequence_length: The trained context length the run recorded.
        config: The checkpoint's own ``config.json``.

    Raises:
        ModelCheckpointError: The trained length exceeds the architecture's
            declared positional capacity.
    """
    capacity = config.get("max_position_embeddings")
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
        # An architecture that declares no capacity is not one this check can
        # reason about, and refusing it would be a guess.
        return
    if max_sequence_length <= capacity:
        return
    raise ModelCheckpointError(
        f"the checkpoint was trained at a context length of {max_sequence_length} tokens but its "
        f"own config declares room for {capacity}; these weights describe a different training "
        "run and cannot serve this one"
    )


def _trained_settings(checkpoint: Path) -> dict[str, Any]:
    """Read the optional ``training_state.json`` the training run left behind.

    Best effort by design: it records facts about the run that the checkpoint
    itself does not always carry, and its absence must not stop a checkpoint that
    is otherwise complete from loading. Every failure mode here degrades to an
    empty mapping, which sends the callers to their constants.

    Args:
        checkpoint: The resolved checkpoint directory; the state file sits beside
            it, one level up, under ``small-model/``.

    Returns:
        The decoded state object, or ``{}`` when it is absent or unreadable.
    """
    state_path = checkpoint.parent / _TRAINING_STATE_FILENAME
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return state if isinstance(state, dict) else {}


def _resolve_max_sequence_length(trained: dict[str, Any]) -> int:
    """The context length to tokenise at.

    Args:
        trained: The training state mapping, possibly empty.

    Returns:
        The ``max_seq_length`` the run recorded, or :data:`MAX_SEQUENCE_LENGTH`
        when it recorded none.
    """
    config = trained.get("config")
    recorded = config.get("max_seq_length") if isinstance(config, dict) else None
    if isinstance(recorded, int) and recorded > 0:
        return recorded
    return MAX_SEQUENCE_LENGTH


def _resolve_base_model(config: dict[str, Any], trained: dict[str, Any]) -> str:
    """Which encoder these weights came from.

    Args:
        config: The checkpoint's ``config.json``.
        trained: The training state mapping.

    Returns:
        The name the run recorded, then the one transformers saved alongside the
        weights, then :data:`DEFAULT_BASE_MODEL`.
    """
    if isinstance(trained.get("base_model"), str):
        return str(trained["base_model"])
    if isinstance(config.get("_name_or_path"), str) and config["_name_or_path"]:
        return str(config["_name_or_path"])
    return DEFAULT_BASE_MODEL


def _resolve_architecture(config: dict[str, Any]) -> str:
    """The architecture class the checkpoint declares.

    Args:
        config: The checkpoint's ``config.json``.

    Returns:
        The single entry of ``architectures``, or ``"unknown"`` when the file
        does not name one. Reported for diagnostics only, so a missing value is
        worth naming rather than refusing to load over.
    """
    architectures = config.get("architectures")
    if isinstance(architectures, list) and architectures and isinstance(architectures[0], str):
        return architectures[0]
    return "unknown"


def _import_torch() -> ModuleType:
    """Import torch, naming the package when it is not installed.

    Returns:
        The imported torch module.

    Raises:
        ModelRuntimeError: torch is not importable in this interpreter.
    """
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - depends on the deployment
        raise ModelRuntimeError(
            "the intent classifier requires 'torch', which is not installed in this "
            f"interpreter ({exc}); every other NEXUS route is unaffected"
        ) from exc
    return torch


def _import_transformers() -> ModuleType:
    """Import transformers, naming the package when it is not installed.

    Returns:
        The imported transformers module.

    Raises:
        ModelRuntimeError: transformers is not importable in this interpreter.
    """
    try:
        import transformers
    except ImportError as exc:  # pragma: no cover - depends on the deployment
        raise ModelRuntimeError(
            "the intent classifier requires 'transformers', which is not installed in "
            f"this interpreter ({exc}); every other NEXUS route is unaffected"
        ) from exc
    return transformers


def _load_tokenizer(transformers: ModuleType, checkpoint: Path) -> Any:
    """Load the checkpoint's fast tokenizer.

    Args:
        transformers: The imported transformers module.
        checkpoint: The resolved checkpoint directory.

    Returns:
        The tokenizer. ``use_fast=True`` is the trained contract: the slow
        ``DebertaV2Tokenizer`` tokenises differently, and a serving path that
        disagreed with the training path would be a silent distribution shift.

    Raises:
        ModelCheckpointError: The tokenizer files exist but do not decode.
    """
    try:
        return transformers.AutoTokenizer.from_pretrained(str(checkpoint), use_fast=True)
    except Exception as exc:
        raise ModelCheckpointError(
            "the checkpoint tokenizer could not be loaded from the checkpoint's own tokenizer files"
        ) from exc


def _load_weights(transformers: ModuleType, checkpoint: Path) -> Any:
    """Load the classification head and its encoder weights.

    Args:
        transformers: The imported transformers module.
        checkpoint: The resolved checkpoint directory.

    Returns:
        The model, untrained in the sense that gradients are disabled by the
        caller — nothing here writes to the weights.

    Raises:
        ModelCheckpointError: The safetensors file does not decode into a model
            whose architecture matches ``config.json``.
    """
    try:
        return transformers.AutoModelForSequenceClassification.from_pretrained(str(checkpoint))
    except Exception as exc:
        raise ModelCheckpointError(
            "the checkpoint weights could not be loaded from model.safetensors"
        ) from exc
