"""The single entry point for Phase 10: discover, build, split, train, evaluate.

Run it from ``backend/`` either as ``python -m ml.train`` or through the Makefile
(``make ml-prepare``, ``make ml-all``). Every stage is individually selectable;
with no stage flag the whole local pipeline runs in order. ``--dry-run`` prints
the plan and exits.

**The stages, and what each one is answerable for.**

``prepare``
    Harvests the capability inventory from ``app/`` by parsing the source (never
    importing it), cross-checks ``num_labels`` against the intent taxonomy,
    builds both corpora, refuses to continue unless every validator passes,
    splits them without leakage and writes the JSONL splits, the label map, the
    inventory, the split assignment and a manifest carrying a sha256 for every
    file it emitted. Stdlib-only: it runs on the backend interpreter with no
    torch anywhere in sight.
``train-small``
    Delegates to ``ml.scripts.train_small_local`` **in a subprocess under the ML
    interpreter**. The training loop is not duplicated here — it lives in one
    place and this stage is the caller, which is what keeps "which interpreter
    am I in" answerable from the command line.
``evaluate``
    Runs the trained classifier over the held-out test split and scores it with
    :func:`ml.evaluation.metrics.evaluate`, so the report, the manifest and the
    trainer's own validation numbers all come from the same code.
``train-qwen``
    Probes honestly first, and materialises the segmented QLoRA notebook plus its
    input bundle whether or not the probe succeeds. See below.
``qwen-status``
    Reports what the Kaggle CLI can see, and the state of a kernel if one was
    pushed.

**The resume contract.** ``--resume`` is passed through to the local trainer,
which restores the furthest checkpoint under ``artifacts/small-model/checkpoints``
after checking it belongs to the current data (same checksum, same dataset
version). Without ``--resume`` a stage that finds a resumable checkpoint says so
and starts fresh, rather than silently continuing a run whose provenance nobody
asked for. Nothing is deleted on either path.

**Why the Qwen stage cannot train here, stated plainly.** The local machine is
an RTX 3050 with 4 GB of VRAM; ``Qwen/Qwen3-8B`` under QLoRA needs roughly 4 GB
for the 4-bit base weights alone, before activations, the optimiser moments or
the allocator's fragmentation. The Kaggle path is authenticated but the account's
kernels are not a place this pipeline can count on an accelerator. So the stage
prints the numbers, records itself **BLOCKED** — not failed, not faked — and
still writes the notebook and the adapter input bundle, so the run is one
``kaggle kernels push`` away on hardware that can hold it. A fabricated success
here would be worse than no run at all: the artifact would be trusted.

**Phase 10 produces artifacts. It never loads a model into the running
application.** Nothing in this module imports ``app``, and nothing here is on a
request path. Serving these checkpoints is Phase 11's job, and the deterministic
engines in ``app.services`` remain the fallback that learned code is measured
against until then.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ml.datasets.capabilities import build_capability_inventory, save_inventory
from ml.datasets.qwen_sft import QWEN_DATASET_VERSION, build_qwen_dataset
from ml.datasets.routing import ROUTING_DATASET_VERSION, build_routing_dataset, label_map
from ml.datasets.schema import (
    SCHEMA_VERSION_QWEN,
    SCHEMA_VERSION_ROUTING,
    DatasetError,
    sha256_file,
    stable_json_dumps,
    write_jsonl,
)
from ml.datasets.taxonomy import INTENT_NAMES, TAXONOMY_VERSION
from ml.evaluation.metrics import evaluate
from ml.kaggle.notebook import (
    render_eval_notebook,
    render_gpu_probe_notebook,
    render_qwen_training_notebook,
)
from ml.preprocessing.normalize import near_duplicate_key
from ml.preprocessing.splits import (
    SPLIT_CONFIG_VERSION,
    SplitConfig,
    assign_leakage_free_splits,
)
from ml.scripts.train_small_local import (
    ARTIFACT_DIRNAME,
    CHECKPOINTS_DIRNAME,
    FINAL_DIRNAME,
    LABEL_MAP_FILENAME,
    STATE_FILENAME,
)
from ml.training.checkpoint import latest_checkpoint
from ml.training.manifest import (
    RunManifest,
    collect_environment,
    git_revision,
    new_run_id,
)
from ml.training.remote import (
    GPU_PROBE_FILENAME,
    KAGGLE_REF_PREFIX,
    TERMINAL_STATES,
    KaggleClient,
    RemoteError,
    render_kernel_metadata,
    slugify,
    verify_gpu_available,
)
from ml.validation import (
    ValidationReport,
    assert_clean,
    validate_qwen_dataset,
    validate_routing_dataset,
    validate_splits,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ml.training.config import PipelineConfig

__all__ = [
    "DEFAULT_CONFIG_DIR",
    "STAGES",
    "PipelineError",
    "Stage",
    "StageOutcome",
    "StageStatus",
    "main",
]

#: Where the TOML files live, relative to ``backend/``. The same directory the
#: local trainer defaults to, so both halves of the pipeline read one file.
DEFAULT_CONFIG_DIR = Path("ml/configs")

#: Canonical split order, matching :mod:`ml.preprocessing.splits` so a file
#: written here and a split named there cannot disagree.
SPLIT_NAMES: tuple[str, str, ...] = ("train", "validation", "test")

#: Run order for ``--all`` and for the no-flag default. ``train-small`` precedes
#: ``evaluate`` because the report is about a model that exists.
STAGES: tuple[Stage, ...]

#: Largest tolerated ratio of the biggest routing class to the smallest one.
#: The generated corpus is balanced by construction, so this is a tripwire
#: against a future generator regressing rather than a tuned threshold.
MAX_CLASS_RATIO = 5.0

#: Version of the dataset manifest this writer emits.
DATASET_MANIFEST_VERSION = "nexo_dataset_manifest.v1"

#: Qwen artifact layout under ``--artifacts-dir``.
QWEN_DIRNAME = "qwen"
QWEN_RUN_FILENAME = "qwen_run.json"
QWEN_NOTEBOOK_FILENAME = "qwen_training_notebook.ipynb"
QWEN_INPUT_DIRNAME = "input"

#: Where downloaded kernel output lands. ``verify_gpu_available`` reads the probe
#: out of this directory, so the path has to be passed to it explicitly rather
#: than inferred: its default is ``artifacts/remote`` relative to the working
#: directory, and the orchestrator does not control the working directory of the
#: process that imports it.
REMOTE_DIRNAME = "remote"

#: Slug the probe kernel is published under. The ``gpu-probe`` suffix is part of
#: the Phase 10 prefix, so it is greppable alongside every other Phase 10 kernel,
#: and a re-run pushes a new *version* of one kernel rather than accumulating
#: ``nexo-phase10-probe-7`` in the account.
GPU_PROBE_KERNEL_SLUG = f"{KAGGLE_REF_PREFIX}-gpu-probe"

#: How long to wait for the probe before giving up. The probe is one cell and no
#: installs; anything past a few minutes is a queue, not a computation.
PROBE_POLL_SECONDS = 20
PROBE_TIMEOUT_SECONDS = 3600

#: Polling cadence and ceiling for a QLoRA segment. A 250-step segment of an 8B
#: model at a 2048-token context legitimately runs for hours on a single T4, so the
#: ceiling is hours rather than minutes. It is a ceiling rather than a patience:
#: a run that hits it is reported as still-running, never as finished.
QWEN_POLL_SECONDS = 60
QWEN_TIMEOUT_SECONDS = 43200

#: Environment variable :func:`~ml.training.remote.verify_gpu_available` reads to
#: find the probe. Set per-call rather than globally so the answer depends on
#: ``--artifacts-dir`` and not on whatever the shell happened to export.
REMOTE_ARTIFACT_ENV = "NEXO_REMOTE_ARTIFACT_DIR"

#: VRAM reserved on top of the frozen 4-bit weights for activations, optimiser
#: moments and allocator fragmentation. With gradient checkpointing and a
#: micro-batch of 1 this is generous; without it, on a 24 GB card, it would be
#: small. It is a floor for "can this run start at all", not a tuning target.
VRAM_HEADROOM_BYTES = 2 * 1024**3

#: Bytes per parameter at 4 bits. NF4's scale factors make the real figure a
#: few percent above this, so the floor stated from it is slightly optimistic
#: rather than optimistic by a factor.
Q4_BYTES_PER_PARAM = 0.5

#: ``Qwen/Qwen3-8B`` -> ``8e9``. Used only to state the memory floor in numbers
#: rather than in adjectives; a name without a size falls back to 8B, which is
#: the model this project actually configures.
_PARAM_SCALE = re.compile(r"(\d+(?:\.\d+)?)\s*([BbMm])\b")


class PipelineError(Exception):
    """A stage cannot proceed, and saying why beats a stack trace.

    Distinct from :class:`~ml.datasets.schema.DatasetError`, which means the data
    itself is wrong: a ``PipelineError`` is about the machine, the configuration
    or the environment, and the operator can fix it without editing a dataset.
    """


class StageStatus(StrEnum):
    """How a stage ended.

    ``BLOCKED`` exists so "this cannot run here, and here is exactly why" is not
    forced to masquerade as either success or failure. It is what the Qwen stage
    records on a machine that cannot hold an 8B model in 4 bits.
    """

    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class Stage:
    """One selectable unit of work."""

    name: str
    flag: str
    description: str
    run: Callable[[Pipeline], StageOutcome]


@dataclass(frozen=True, slots=True)
class StageOutcome:
    """What one stage did, for the console summary and the run manifest.

    ``artifacts`` is keyed by logical name so the manifest can hash every file a
    stage claims to have produced; an unlisted file is invisible to a reviewer
    checking the manifest against the directory.
    """

    stage: str
    status: StageStatus
    detail: str
    duration_seconds: float = 0.0
    artifacts: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the pipeline summary.

        Returns:
            A JSON-ready mapping with sorted artifacts.
        """
        return {
            "stage": self.stage,
            "status": str(self.status),
            "detail": self.detail,
            "duration_seconds": round(self.duration_seconds, 3),
            "artifacts": dict(sorted(self.artifacts.items())),
        }


@dataclass(frozen=True, slots=True)
class Pipeline:
    """Everything a stage needs, resolved once and passed down.

    Every path is absolute by the time a stage sees it, and the backend root is
    the working directory of every subprocess, so a stage behaves identically
    whether it was launched from ``backend/``, from a Makefile, or from a
    symlinked checkout.
    """

    config: PipelineConfig
    config_dir: Path
    small_model_config: Path
    backend_root: Path
    datasets_dir: Path
    artifacts_dir: Path
    reports_dir: Path
    seed: int
    per_intent: int
    per_category: int
    resume: bool
    verbose: bool

    @property
    def small_model_dir(self) -> Path:
        """The routing classifier's artifact directory."""
        return self.artifacts_dir / ARTIFACT_DIRNAME

    @property
    def checkpoint_root(self) -> Path:
        """Where the local trainer writes resumable checkpoints."""
        return self.small_model_dir / CHECKPOINTS_DIRNAME

    @property
    def qwen_dir(self) -> Path:
        """The Qwen QLoRA run's artifact directory."""
        return self.artifacts_dir / QWEN_DIRNAME

    @property
    def remote_dir(self) -> Path:
        """Downloaded remote kernel output, including the accelerator probe."""
        return self.artifacts_dir / REMOTE_DIRNAME


def _say(message: str = "") -> None:
    """Print one line of run output, flushed so a piped log keeps its order.

    Args:
        message: The line.
    """
    print(message, flush=True)


def _banner(stage: Stage) -> None:
    """Announce a stage with a rule on either side.

    Args:
        stage: The stage about to run.
    """
    _say("")
    _say("=" * 78)
    _say(f"  {stage.name}: {stage.description}")
    _say("=" * 78)


def _backend_root() -> Path:
    """Find ``backend/`` from this file rather than from the working directory.

    The ``ml`` package sits directly under the backend root, so the parent of the
    package directory *is* ``backend/`` and the working directory is irrelevant.
    That is the difference between ``make ml-prepare`` and ``python -m ml.train``
    behaving identically.

    Returns:
        The backend root.
    """
    return Path(__file__).resolve().parents[1]


def _under(root: Path, path: Path) -> Path:
    """Resolve a command-line path against the backend root.

    A relative ``--datasets-dir`` is a path *to the backend*, not to whatever
    directory the operator happened to be standing in when they typed it. The
    defaults are relative, so this is what keeps ``python -m ml.train`` writing
    to the same place whether it was launched by the Makefile or by hand from a
    shell that had cd'd somewhere else first.

    Args:
        root: The backend root.
        path: The path as given.

    Returns:
        An absolute path.
    """
    return path if path.is_absolute() else root / path


def _pipeline_config(config_dir: Path) -> tuple[PipelineConfig, Path]:
    """Load the pipeline config and locate the small-model TOML beside it.

    ``ml.training.config`` is imported here rather than at module scope because
    it is the one module in the package that needs pydantic. Keeping the import
    here means a stage that never reads a config — and the failure message is
    the thing that tells an operator which interpreter to use — does not fail at
    collection time on an interpreter without pydantic.

    Args:
        config_dir: Directory holding the TOML files.

    Returns:
        The validated config and the small-model TOML's path.

    Raises:
        PipelineError: pydantic is not importable in this interpreter.
        OSError: A TOML file is missing or unreadable.
        pydantic.ValidationError: A configured value is out of range.
    """
    try:
        from ml.training.config import SMALL_MODEL_CONFIG_FILE, load_config
    except ImportError as exc:  # pragma: no cover - depends on the interpreter
        raise PipelineError(
            "ml.training.config needs pydantic, which this interpreter does not carry. "
            "Run ml.train under backend/.venv; the ML virtualenv is for the trainer "
            f"subprocess, not the orchestrator ({exc})"
        ) from exc
    return load_config(config_dir), config_dir / SMALL_MODEL_CONFIG_FILE


def _torch_interpreter() -> Path | None:
    """Find an interpreter that can import torch.

    ``sys.executable`` wins when it has torch, because a caller who deliberately
    launched the orchestrator from the ML environment should not be overridden.
    Otherwise the virtualenv sitting next to this package is used, which is where
    ``make ml-train-small`` expects to find it.

    Returns:
        The interpreter, or None when no candidate exists.
    """
    if importlib.util.find_spec("torch") is not None:
        return Path(sys.executable)
    package_dir = Path(__file__).resolve().parent
    for candidate in (
        package_dir / ".venv" / "Scripts" / "python.exe",
        package_dir / ".venv" / "bin" / "python",
    ):
        if candidate.is_file():
            return candidate
    return None


def _require_torch_interpreter() -> Path:
    """Return the ML interpreter or explain where it was expected.

    Returns:
        The interpreter.

    Raises:
        PipelineError: Neither this interpreter nor the package-local virtualenv
            can import torch.
    """
    found = _torch_interpreter()
    if found is not None:
        return found
    package_dir = Path(__file__).resolve().parent
    expected = package_dir / ".venv" / "Scripts" / "python.exe"
    raise PipelineError(
        "no interpreter with torch was found. Training runs under the ML virtualenv; "
        f"expected {expected} (or {package_dir / '.venv' / 'bin' / 'python'} on POSIX), "
        "or launch ml.train from an interpreter that already has torch"
    )


#: Emitted as ``-c`` into the ML interpreter rather than imported here. torch
#: lives in exactly one environment in this repository, so any question about
#: devices has to be answered by that interpreter; asking it in a subprocess is
#: what lets the orchestrator stay torch-free on the backend interpreter.
_TORCH_PROBE = """
import json

try:
    import torch
except Exception as exc:
    print(json.dumps({"torch": False, "error": type(exc).__name__}))
else:
    info = {"torch": True, "torch_version": torch.__version__}
    info["cuda"] = bool(torch.cuda.is_available())
    if info["cuda"]:
        props = torch.cuda.get_device_properties(0)
        info["device_name"] = props.name
        info["total_vram_bytes"] = int(props.total_memory)
    print(json.dumps(info))
"""


def _probe_accelerator(interpreter: Path | None) -> dict[str, Any]:
    """Ask an interpreter what accelerators it can actually see.

    ``torch.cuda.is_available()`` is the only honest question, and it is asked of
    the interpreter that owns torch. ``None`` for anything unanswered is the same
    convention the feature vectors use: unmeasured is not zero.

    Args:
        interpreter: The interpreter to ask, or None when there is none.

    Returns:
        A mapping with at least ``torch``; keys describing the device only
        appear when one was actually visible.
    """
    if interpreter is None:
        return {"torch": False, "error": "no interpreter with torch"}
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [str(interpreter), "-c", _TORCH_PROBE],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"torch": False, "error": type(exc).__name__}
    if completed.returncode != 0:
        return {"torch": False, "error": "probe exited non-zero"}
    try:
        decoded = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"torch": False, "error": "unreadable probe output"}
    return decoded if isinstance(decoded, dict) else {"torch": False, "error": "bad probe shape"}


def _parameter_count(base_model: str) -> int:
    """Read the parameter count out of a checkpoint's name.

    Only used to state a memory floor in numbers. A name that carries no size
    falls back to 8B, which is the model ``qwen_qlora.toml`` configures; the
    point of the number is to make the blocker concrete, not to be a catalogue.

    Args:
        base_model: The HuggingFace model id.

    Returns:
        The parameter count.
    """
    match = _PARAM_SCALE.search(base_model.rsplit("/", 1)[-1])
    if match is None:
        return 8_000_000_000
    scale = float(match.group(1))
    return int(scale * (1_000_000_000 if match.group(2).upper() == "B" else 1_000_000))


def _required_vram_bytes(base_model: str) -> int:
    """The VRAM floor for a 4-bit QLoRA run on ``base_model``.

    Args:
        base_model: The HuggingFace model id.

    Returns:
        Bytes of frozen weights plus the activation headroom.
    """
    weights = int(_parameter_count(base_model) * Q4_BYTES_PER_PARAM)
    return weights + VRAM_HEADROOM_BYTES


def _gib(value: int | float) -> float:
    """Render a byte count in gibibytes, the unit a GPU's spec sheet uses.

    Args:
        value: A count in bytes.

    Returns:
        The count in GiB.
    """
    return float(value) / 1024**3


def _describe_accelerator(probe: Mapping[str, Any], required: int) -> str:
    """Phrase an accelerator probe as one sentence a reader can check.

    Args:
        probe: The probe mapping.
        required: The VRAM floor in bytes.

    Returns:
        A human sentence, including the shortfall when there is one.
    """
    if not probe.get("torch"):
        return f"no torch in any interpreter ({probe.get('error', 'unknown')}); need {_gib(required):.1f} GiB"
    if not probe.get("cuda"):
        return f"torch {probe.get('torch_version', '?')} sees no CUDA device; need {_gib(required):.1f} GiB"
    present = int(probe.get("total_vram_bytes", 0))
    verdict = "enough" if present >= required else "not enough"
    return (
        f"{probe.get('device_name', 'unknown device')} with {_gib(present):.1f} GiB VRAM "
        f"({verdict} for the {_gib(required):.1f} GiB a 4-bit QLoRA run needs)"
    )


@contextmanager
def _remote_artifacts_at(directory: Path) -> Iterator[None]:
    """Point the remote helpers at ``directory`` for the duration of the block.

    :func:`~ml.training.remote.verify_gpu_available` and the artifact reader share
    one environment variable rather than taking a path, because the module is
    also imported by callers that have no ``Pipeline``. Scoping the assignment to
    a context manager keeps two concurrent runs in one process from reading each
    other's probe — and, more to the point, keeps the answer a function of
    ``--artifacts-dir`` rather than of whatever the operator's shell exported
    hours earlier.

    Args:
        directory: Where downloaded kernel output lives.

    Yields:
        None.
    """
    previous = os.environ.get(REMOTE_ARTIFACT_ENV)
    os.environ[REMOTE_ARTIFACT_ENV] = str(directory)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(REMOTE_ARTIFACT_ENV, None)
        else:
            os.environ[REMOTE_ARTIFACT_ENV] = previous


def _remote_gpu_verdict(pipeline: Pipeline) -> tuple[bool, str]:
    """Ask whether a remote run has *evidence* of an accelerator.

    Delegated to
    :func:`~ml.training.remote.verify_gpu_available`, which accepts only a probe a
    kernel wrote about the machine it actually ran on. Kernel metadata is not
    admissible: it records what was requested, and on this account the request is
    honoured with a CPU-only batch image, so reading it would report a T4 that was
    never there.

    Args:
        pipeline: The resolved pipeline, for the artifact directory.

    Returns:
        ``(available, reason)``. Never ``True`` without a probe behind it, and
        never ``False`` without a sentence saying why.
    """
    with _remote_artifacts_at(pipeline.remote_dir):
        return verify_gpu_available()


def _read_remote_probe(pipeline: Pipeline) -> dict[str, Any] | None:
    """Read the accelerator probe a remote kernel wrote, if one is present.

    Args:
        pipeline: The resolved pipeline.

    Returns:
        The decoded probe, or None when no kernel has written one.
    """
    try:
        decoded = json.loads((pipeline.remote_dir / GPU_PROBE_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _kernel_workdir(pipeline: Pipeline, name: str) -> Path:
    """Create and return a directory holding one pushed kernel.

    Kaggle zips the whole push directory, so this directory holds the notebook,
    the ``kernel-metadata.json`` and nothing else. Anything extra would travel to
    the remote and come back in the download.

    Args:
        pipeline: The resolved pipeline.
        name: Subdirectory under ``artifacts/remote``.

    Returns:
        The directory, created.
    """
    workdir = pipeline.remote_dir / name
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def stage_probe_remote(pipeline: Pipeline) -> StageOutcome:
    """Push the accelerator probe and record what the session actually was.

    This is the stage that makes every other claim about the remote checkable. It
    pushes a one-cell kernel that asks the machine four questions — is there a
    ``/dev/nvidia*`` device, does ``nvidia-smi`` answer, does torch see CUDA, and
    does DNS resolve — waits for it to finish, downloads the answer, and stores it
    under ``--artifacts-dir/remote``.

    It is cheap by design: one cell, no installs, no downloads. Running it before
    a QLoRA push costs a minute and converts "Kaggle should give us a GPU" from an
    assumption into a file on disk. It is also the only stage that is *expected* to
    report ``BLOCKED`` on a well-configured account — a BLOCKED verdict here means
    the probe ran and found nothing, which is the most useful thing it can do.

    Args:
        pipeline: The resolved pipeline.

    Returns:
        The outcome. ``PASSED`` when the kernel ran whatever it found, with the
        finding in ``detail``; ``BLOCKED`` when the CLI could not be used.
    """
    started = time.monotonic()
    try:
        environment = KaggleClient().probe()
    except (RemoteError, OSError) as exc:
        return StageOutcome(
            stage="probe-remote",
            status=StageStatus.BLOCKED,
            detail=f"kaggle CLI unusable: {type(exc).__name__}: {exc}",
            duration_seconds=time.monotonic() - started,
        )
    if not environment.username:
        return StageOutcome(
            stage="probe-remote",
            status=StageStatus.BLOCKED,
            detail=(
                "the kaggle CLI cannot identify the account, so no kernel ref can be "
                "composed. Authenticate with `kaggle auth login` and rerun"
            ),
            duration_seconds=time.monotonic() - started,
        )

    run_id = new_run_id("gpu-probe", when=datetime.now(UTC), seed=pipeline.seed)
    ref = f"{environment.username.lower()}/{GPU_PROBE_KERNEL_SLUG}"
    workdir = _kernel_workdir(pipeline, "gpu-probe")
    notebook_path = workdir / "gpu_probe.ipynb"
    notebook_path.write_text(render_gpu_probe_notebook(run_id=run_id), encoding="utf-8")
    metadata_path = workdir / "kernel-metadata.json"
    _write_json(
        metadata_path,
        render_kernel_metadata(
            ref=ref,
            title=GPU_PROBE_KERNEL_SLUG,
            code_file=notebook_path.name,
            dataset_sources=(),
            enable_gpu=True,
        ),
    )
    _say(f"pushing      {ref} (enable_gpu=true, T4x2 requested)")
    _say("             the request is what gets recorded; the probe is what reports the machine")

    client = KaggleClient()
    try:
        kernel = client.push_kernel(workdir)
        _say(f"pushed       version {kernel.version} -> {kernel.url}")
        status = client.wait_for_kernel(
            kernel.ref,
            poll_seconds=PROBE_POLL_SECONDS,
            max_seconds=PROBE_TIMEOUT_SECONDS,
            on_poll=lambda observed: _say(f"status       {observed.state}"),
        )
        if status.state != "COMPLETE":
            raise RemoteError(
                command=f"kaggle kernels output {kernel.ref}",
                returncode=None,
                stderr=f"probe kernel ended {status.state}: {status.raw}",
            )
        output_dir = client.kernel_output(kernel.ref, pipeline.remote_dir)
    except RemoteError as exc:
        return StageOutcome(
            stage="probe-remote",
            status=StageStatus.FAILED,
            detail=str(exc),
            duration_seconds=time.monotonic() - started,
            artifacts={"workdir": str(workdir)},
        )

    probe_path = pipeline.remote_dir / GPU_PROBE_FILENAME
    available, reason = _remote_gpu_verdict(pipeline)
    probe = _read_remote_probe(pipeline)
    if probe is not None:
        _say(f"machine      {probe.get('platform', 'unknown platform')}")
        _say(f"cpus         {probe.get('cpu_count')}")
        _say(
            f"torch        {probe.get('torch_version')} (cuda: {probe.get('torch_cuda_available')})"
        )
        _say(f"nvidia-smi   {probe.get('nvidia_smi_path') or 'not on PATH'}")
        _say(f"devices      {probe.get('device_nodes') or 'no /dev/nvidia*'}")
        for host, result in sorted((probe.get("internet") or {}).items()):
            _say(f"dns {host:<16} {result}")
    _say(f"verdict      {'GPU available' if available else 'no GPU'}: {reason}")

    artifacts = {
        "notebook": str(notebook_path),
        "kernel_metadata": str(metadata_path),
        "kernel": kernel.ref,
        "output_dir": str(output_dir),
    }
    if probe_path.is_file():
        artifacts["gpu_probe"] = str(probe_path)
    _write_json(
        pipeline.qwen_dir / "gpu_probe_run.json",
        {
            "run_id": run_id,
            "kernel_ref": kernel.ref,
            "kernel_version": kernel.version,
            "kernel_url": kernel.url,
            "kernel_state": status.state,
            "requested": {"enable_gpu": True, "gpu_type_option": "T4x2"},
            "observed": probe,
            "gpu_available": available,
            "verdict": reason,
            "checked_at": datetime.now(UTC).isoformat(),
        },
    )
    return StageOutcome(
        stage="probe-remote",
        status=StageStatus.PASSED,
        detail=f"{kernel.ref} v{kernel.version} completed; {reason}",
        duration_seconds=time.monotonic() - started,
        artifacts=artifacts,
    )


def _split_records(
    records: Sequence[Mapping[str, Any]],
    *,
    key_field: str,
    label_field: str | None,
    duplicate_field: str,
    config: SplitConfig,
) -> tuple[dict[str, list[Mapping[str, Any]]], Mapping[str, int]]:
    """Partition records into the three splits without leakage.

    Args:
        records: The corpus, in build order. Order is part of the determinism
            contract.
        key_field: The unique field naming each record.
        label_field: The field to stratify on, or None.
        duplicate_field: The text field the near-duplicate key reads.
        config: The partition and the seed.

    Returns:
        The per-split record lists and the split sizes.

    Raises:
        ml.datasets.schema.DataValidationError: A key is missing or duplicated,
            or the partition is incomplete.
    """
    result = assign_leakage_free_splits(
        records,
        key_field=key_field,
        label_field=label_field,
        duplicate_key_fn=lambda row: near_duplicate_key(row[duplicate_field]),
        config=config,
    )
    buckets: dict[str, list[Mapping[str, Any]]] = {name: [] for name in SPLIT_NAMES}
    for record in records:
        buckets[result.assignments[str(record[key_field])]].append(record)
    return buckets, dict(result.counts)


def _write_report(report: ValidationReport, reports_dir: Path, stem: str) -> str:
    """Write a validation report as JSON and Markdown, both named by ``stem``.

    Args:
        report: The report to write.
        reports_dir: Destination directory.
        stem: Filename stem, e.g. ``"routing_dataset"``.

    Returns:
        The Markdown path, which is what ``make ml-validate`` prints.
    """
    json_path = reports_dir / f"{stem}.json"
    md_path = reports_dir / f"{stem}.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(stable_json_dumps(report.to_dict()) + "\n", encoding="utf-8")
    md_path.write_text(report.to_markdown(), encoding="utf-8")
    return str(md_path)


def _split_path(datasets_dir: Path, prefix: str, split: str) -> Path:
    """Name one prepared split file.

    Both the writer and the evaluator go through here: an evaluator that spelled
    the filename itself would quietly score a stale split the moment either side
    renamed a corpus.

    Args:
        datasets_dir: The prepared-split directory.
        prefix: The corpus name, ``"routing"`` or ``"qwen"``.
        split: One of :data:`SPLIT_NAMES`.

    Returns:
        The path to that split file.
    """
    return datasets_dir / f"{prefix}_{split}.jsonl"


def _line_count(path: Path) -> int:
    """Count the non-empty lines in a JSONL file.

    Args:
        path: The file.

    Returns:
        The number of records.
    """
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _write_json(path: Path, payload: Any) -> str:
    """Write canonical JSON with a trailing newline.

    Args:
        path: Destination file. Parent directories are created.
        payload: Any JSON-serialisable value.

    Returns:
        ``str(path)``, so callers can collect artifact names inline.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
    return str(path)


def _provenance_breakdown(records: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Count records by provenance.

    Every row this pipeline generates is synthetic, and the manifest says so
    explicitly rather than leaving a reader to infer it from a single-valued
    column. The day a hand-written row enters a corpus, it will show up here.

    Args:
        records: The corpus.

    Returns:
        Provenance name to row count, sorted.
    """
    counter = Counter(str(record.get("provenance", "unknown")) for record in records)
    return dict(sorted(counter.items()))


def stage_prepare(pipeline: Pipeline) -> StageOutcome:
    """Build, validate and split both corpora, and write everything they need.

    Args:
        pipeline: The resolved pipeline.

    Returns:
        The outcome, with every emitted file named.

    Raises:
        PipelineError: ``app/`` is missing, or the configured label count
            disagrees with the taxonomy.
        ml.datasets.schema.DatasetError: The inventory cannot be harvested or a
            file cannot be written.
        ml.datasets.schema.DataValidationError: A validator found an error.
    """
    started = time.monotonic()
    app_dir = pipeline.backend_root / "app"
    inventory = build_capability_inventory(app_dir)
    _say(
        f"inventory    {inventory.total_routes()} routes, {len(inventory.entities())} domains, "
        f"{len(inventory.recommendation_types)} recommendation types, "
        f"{len(inventory.risk_types)} risk types, {len(inventory.permissions)} permissions"
    )

    labels = label_map()
    declared = pipeline.config.small_model.num_labels
    if declared != len(INTENT_NAMES):
        raise PipelineError(
            f"small_model.toml declares num_labels={declared} but the taxonomy has "
            f"{len(INTENT_NAMES)} intents ({INTENT_NAMES[0]}..{INTENT_NAMES[-1]}). A head "
            "sized for the wrong count does not fail, it just never predicts the missing class"
        )
    if set(labels) != set(INTENT_NAMES):
        raise PipelineError("the label map and the taxonomy disagree; refusing to write splits")

    routing_examples, routing_stats = build_routing_dataset(
        seed=pipeline.seed,
        per_intent=pipeline.per_intent,
        capability_inventory=inventory,
    )
    routing_records = [example.to_dict() for example in routing_examples]
    routing_report = validate_routing_dataset(
        routing_records, known_intents=INTENT_NAMES, max_class_ratio=MAX_CLASS_RATIO
    )
    _write_report(routing_report, pipeline.reports_dir, "routing_dataset")

    qwen_examples, qwen_stats = build_qwen_dataset(
        seed=pipeline.seed,
        per_category=pipeline.per_category,
        capability_inventory=inventory,
    )
    qwen_records = [example.to_dict() for example in qwen_examples]
    qwen_report = validate_qwen_dataset(qwen_records)
    _write_report(qwen_report, pipeline.reports_dir, "qwen_dataset")

    assert_clean(routing_report, qwen_report)
    _say(f"routing      {len(routing_records)} rows, validation PASSED")
    _say(f"qwen         {len(qwen_records)} rows, validation PASSED")

    split_config = SplitConfig(seed=pipeline.seed)
    routing_buckets, routing_counts = _split_records(
        routing_records,
        key_field="text",
        label_field="intent",
        duplicate_field="text",
        config=split_config,
    )
    qwen_buckets, qwen_counts = _split_records(
        qwen_records,
        key_field="instruction",
        label_field=None,
        duplicate_field="instruction",
        config=split_config,
    )

    # Each corpus is audited separately. Leakage is a within-partition property:
    # a routing utterance and a qwen instruction that happen to share a bag of
    # words are two rows of two different training sets, and pooling them into
    # one audit reports that collision as leakage no model can commit and no
    # split can fix.
    leakage_reports = []
    for stem, buckets, text_field in (
        ("routing", routing_buckets, "text"),
        ("qwen", qwen_buckets, "instruction"),
    ):
        report = validate_splits(
            {name: [row[text_field] for row in buckets[name]] for name in SPLIT_NAMES},
            duplicate_key_fn=near_duplicate_key,
        )
        _write_report(report, pipeline.reports_dir, f"{stem}_splits")
        leakage_reports.append(report)
    assert_clean(*leakage_reports)

    artifacts: dict[str, str] = {}
    written: list[Path] = []
    for prefix, buckets in (("routing", routing_buckets), ("qwen", qwen_buckets)):
        for name in SPLIT_NAMES:
            path = _split_path(pipeline.datasets_dir, prefix, name)
            count = write_jsonl(path, buckets[name])
            written.append(path)
            _say(f"split        {prefix}_{name}.jsonl  {count} rows")
            artifacts[f"{prefix}_{name}"] = str(path)

    inventory_path = pipeline.datasets_dir / "capability_inventory.json"
    save_inventory(inventory, inventory_path)
    artifacts["capability_inventory"] = str(inventory_path)

    labels_path = pipeline.datasets_dir / LABEL_MAP_FILENAME
    _write_json(labels_path, {"label2id": labels, "taxonomy_version": TAXONOMY_VERSION})
    artifacts["label_map"] = str(labels_path)

    splits_path = pipeline.datasets_dir / "splits.json"
    _write_json(
        splits_path,
        {
            "split_config_version": SPLIT_CONFIG_VERSION,
            "seed": pipeline.seed,
            "train_fraction": split_config.train,
            "validation_fraction": split_config.validation,
            "test_fraction": split_config.test,
            "group_by": split_config.group_by,
            "routing": {
                "counts": routing_counts,
                "key_field": "text",
                "assignments": {
                    row["text"]: name for name in SPLIT_NAMES for row in routing_buckets[name]
                },
            },
            "qwen": {
                "counts": qwen_counts,
                "key_field": "instruction",
                "assignments": {
                    row["instruction"]: name for name in SPLIT_NAMES for row in qwen_buckets[name]
                },
            },
        },
    )
    artifacts["splits"] = str(splits_path)

    manifest_path = pipeline.datasets_dir / "dataset_manifest.json"
    manifest_payload = {
        "dataset_manifest_version": DATASET_MANIFEST_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "seed": pipeline.seed,
        "taxonomy_version": TAXONOMY_VERSION,
        "routing": {
            "dataset_version": ROUTING_DATASET_VERSION,
            "schema_version": SCHEMA_VERSION_ROUTING,
            "total": routing_stats.total,
            "per_label": dict(sorted(routing_stats.per_intent.items())),
            "per_split": routing_counts,
            "provenance": _provenance_breakdown(routing_records),
        },
        "qwen": {
            "dataset_version": QWEN_DATASET_VERSION,
            "schema_version": SCHEMA_VERSION_QWEN,
            "total": qwen_stats.total,
            "per_category": dict(sorted(qwen_stats.per_category.items())),
            "per_split": qwen_counts,
            "provenance": _provenance_breakdown(qwen_records),
        },
        "capability_inventory_version": inventory.inventory_version,
        "routes": inventory.total_routes(),
        "files": {
            path.name: {
                "rows": _line_count(path) if path.suffix == ".jsonl" else None,
                "sha256": sha256_file(path),
            }
            for path in [*written, inventory_path, labels_path, splits_path]
        },
    }
    _write_json(manifest_path, manifest_payload)
    artifacts["dataset_manifest"] = str(manifest_path)
    _say(
        f"manifest     {manifest_path} "
        f"({len(manifest_payload['files'])} files hashed, seed {pipeline.seed})"
    )
    return StageOutcome(
        stage="prepare",
        status=StageStatus.PASSED,
        detail=(
            f"{len(routing_records)} routing rows {dict(routing_counts)}, "
            f"{len(qwen_records)} qwen rows {dict(qwen_counts)}"
        ),
        duration_seconds=time.monotonic() - started,
        artifacts=artifacts,
    )


def stage_train_small(pipeline: Pipeline) -> StageOutcome:
    """Run the local classifier trainer in a subprocess under the ML interpreter.

    The training loop is not reimplemented here. ``ml.scripts.train_small_local``
    owns it because torch belongs to one environment and the orchestrator does
    not; duplicating the loop would put the same optimiser in two files with two
    chances to drift.

    Args:
        pipeline: The resolved pipeline.

    Returns:
        The outcome, naming the artifacts the trainer wrote.

    Raises:
        PipelineError: No torch interpreter, or the trainer exited non-zero.
    """
    started = time.monotonic()
    interpreter = _require_torch_interpreter()
    _say(f"interpreter  {interpreter}")

    if not pipeline.resume:
        existing = (
            latest_checkpoint(pipeline.checkpoint_root)
            if pipeline.checkpoint_root.is_dir()
            else None
        )
        if existing is not None:
            _say(
                f"resume       not requested; a resumable checkpoint exists at "
                f"{existing.directory} (step {existing.metadata.global_step}). Starting fresh "
                "so the run's provenance is exactly what was asked for. Pass --resume to continue it"
            )
    command = [
        str(interpreter),
        "-m",
        "ml.scripts.train_small_local",
        "--data-dir",
        str(pipeline.datasets_dir),
        "--artifacts-dir",
        str(pipeline.artifacts_dir),
        "--config",
        str(pipeline.small_model_config),
        "--seed",
        str(pipeline.seed),
    ]
    if pipeline.resume:
        command.append("--resume")
    if pipeline.verbose:
        _say(f"command      {' '.join(command)}")
    _say("delegating   ml.scripts.train_small_local")
    completed = subprocess.run(command, cwd=str(pipeline.backend_root), check=False)  # noqa: S603
    if completed.returncode != 0:
        raise PipelineError(
            f"ml.scripts.train_small_local exited {completed.returncode}; see the log above"
        )

    state_path = pipeline.small_model_dir / STATE_FILENAME
    detail = f"trainer exited 0; state at {state_path}"
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        detail = (
            f"{state['steps']} steps, validation accuracy "
            f"{state['validation']['accuracy']:.4f} (macro F1 {state['validation']['macro_f1']:.4f})"
        )
    return StageOutcome(
        stage="train-small",
        status=StageStatus.PASSED,
        detail=detail,
        duration_seconds=time.monotonic() - started,
        artifacts={
            "final_model": str(pipeline.small_model_dir / FINAL_DIRNAME),
            "training_state": str(state_path),
        },
    )


_INFER_SCRIPT = r"""
import json
import sys
from pathlib import Path

import torch
import transformers

model_dir, data_path, out_path, batch, max_len = sys.argv[1:6]
batch, max_len = int(batch), int(max_len)
rows = [json.loads(line) for line in Path(data_path).read_text(encoding="utf-8").splitlines() if line.strip()]
tokenizer = transformers.AutoTokenizer.from_pretrained(model_dir)
model = transformers.AutoModelForSequenceClassification.from_pretrained(model_dir).eval()
id2label = {int(k): v for k, v in json.loads((Path(model_dir) / "config.json").read_text(encoding="utf-8"))["id2label"].items()}
lines = []
with torch.no_grad():
    for start in range(0, len(rows), batch):
        window = rows[start : start + batch]
        encoded = tokenizer(
            [row["text"] for row in window],
            truncation=True,
            max_length=max_len,
            padding="max_length",
            return_tensors="pt",
        )
        encoded.pop("token_type_ids", None)
        predicted = model(**encoded).logits.argmax(dim=-1).tolist()
        for row, index in zip(window, predicted):
            lines.append(json.dumps({"text": row["text"], "intent": row["intent"], "predicted": id2label[int(index)]}, sort_keys=True))
Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"predicted {len(rows)} rows")
"""


def stage_evaluate(pipeline: Pipeline) -> StageOutcome:
    """Score the trained classifier on the held-out test split.

    Inference needs torch and therefore runs in a subprocess; the scoring runs
    here, through :func:`ml.evaluation.metrics.evaluate`, so the numbers in this
    report, in ``metrics.json`` and in the run manifest come from one
    implementation rather than three.

    A missing model or a missing split is BLOCKED, not FAILED: there is nothing
    broken, only nothing trained yet, and a first ``--evaluate`` on a fresh clone
    should say so and exit 0.

    Args:
        pipeline: The resolved pipeline.

    Returns:
        The outcome naming the metrics, report and predictions.

    Raises:
        PipelineError: The inference subprocess failed.
    """
    started = time.monotonic()
    model_dir = pipeline.small_model_dir / FINAL_DIRNAME
    test_path = _split_path(pipeline.datasets_dir, "routing", "test")
    if not model_dir.is_dir():
        return StageOutcome(
            stage="evaluate",
            status=StageStatus.BLOCKED,
            detail=(
                f"no trained model at {model_dir}. Run --train-small first; evaluate scores "
                "a checkpoint, it does not train one"
            ),
            duration_seconds=time.monotonic() - started,
        )
    if not test_path.is_file():
        return StageOutcome(
            stage="evaluate",
            status=StageStatus.BLOCKED,
            detail=f"no test split at {test_path}. Run --prepare first",
            duration_seconds=time.monotonic() - started,
        )
    interpreter = _require_torch_interpreter()
    predictions_path = pipeline.small_model_dir / "predictions_test.jsonl"
    predictions_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(interpreter),
        "-c",
        _INFER_SCRIPT,
        str(model_dir),
        str(test_path),
        str(predictions_path),
        str(pipeline.config.small_model.per_device_eval_batch_size),
        str(pipeline.config.small_model.max_seq_length),
    ]
    if pipeline.verbose:
        _say(f"command      {' '.join(command[:2])} <inference script>")
    completed = subprocess.run(command, cwd=str(pipeline.backend_root), check=False)  # noqa: S603
    if completed.returncode != 0:
        raise PipelineError(f"inference exited {completed.returncode}; see the log above")

    rows = [
        json.loads(line)
        for line in predictions_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise PipelineError(f"inference wrote no predictions to {predictions_path}")
    taxonomy_labels = label_map()
    # Sorted by class index, not alphabetically: the taxonomy order is what the
    # confusion matrix and every per-class table are read against, and it is the
    # order the head's logits actually correspond to.
    labels = sorted(taxonomy_labels, key=taxonomy_labels.__getitem__)
    metrics = evaluate(
        [row["intent"] for row in rows],
        [row["predicted"] for row in rows],
        labels,
    )
    metrics_path = _write_json(
        pipeline.small_model_dir / "metrics.json",
        {
            "split": "test",
            "rows": len(rows),
            "label_order": labels,
            **metrics.to_dict(),
        },
    )
    report_path = pipeline.small_model_dir / "metrics.md"
    report_path.write_text(metrics.to_markdown(), encoding="utf-8")
    _say(
        f"metrics      accuracy {metrics.accuracy:.4f} macro_f1 {metrics.macro_f1:.4f} "
        f"weighted_f1 {metrics.weighted_f1:.4f} over {len(rows)} held-out rows"
    )
    return StageOutcome(
        stage="evaluate",
        status=StageStatus.PASSED,
        detail=f"{metrics.accuracy:.4f} accuracy, {metrics.macro_f1:.4f} macro F1 on {len(rows)} rows",
        duration_seconds=time.monotonic() - started,
        artifacts={
            "metrics_json": metrics_path,
            "metrics_markdown": str(report_path),
            "predictions": str(predictions_path),
        },
    )


def _notebook_config(pipeline: Pipeline) -> dict[str, Any]:
    """Flatten the pipeline config into the notebook renderer's parameter block.

    Both the training notebook and the evaluation notebook take the same
    configuration mapping, and both must be rendered from the same resolved TOMLs —
    a notebook rendered from a different config than the one the run manifest
    records is a run nobody can reproduce. Extracting the mapping is what keeps
    that a single decision rather than two copies that can drift.

    Args:
        pipeline: The resolved pipeline.

    Returns:
        The configuration mapping.
    """
    qwen = pipeline.config.qwen
    small = pipeline.config.small_model
    return {
        "seed": pipeline.seed,
        "small_base_model": small.base_model,
        "small_max_seq_length": small.max_seq_length,
        "qwen_base_model": qwen.base_model,
        "qwen_max_seq_length": qwen.max_seq_length,
        "qwen_learning_rate": qwen.learning_rate,
        "qwen_num_train_epochs": qwen.num_train_epochs,
        "qwen_per_device_train_batch_size": qwen.per_device_train_batch_size,
        "qwen_gradient_accumulation_steps": qwen.gradient_accumulation_steps,
        "qwen_weight_decay": qwen.weight_decay,
        "lora_r": qwen.lora_r,
        "lora_alpha": qwen.lora_alpha,
        "lora_dropout": qwen.lora_dropout,
        "lora_target_modules": list(qwen.lora_target_modules),
        "warmup_ratio": qwen.warmup_ratio,
        "lr_scheduler_type": qwen.lr_scheduler_type,
        "max_grad_norm": qwen.max_grad_norm,
        "save_every_n_steps": qwen.save_every_n_steps,
        "eval_batch_size": small.per_device_eval_batch_size,
    }


def _materialise_qwen_bundle(
    pipeline: Pipeline,
    run_id: str,
    blocker: str,
    probe: Mapping[str, Any],
    *,
    dataset_slug: str | None = None,
    status: StageStatus = StageStatus.BLOCKED,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Write the notebook and its input bundle, whether or not a run is possible.

    The whole point of a BLOCKED stage is that the operator is one command away,
    so the artifacts are produced *before* the verdict is reached rather than
    after a decision that might never come.

    Args:
        pipeline: The resolved pipeline.
        run_id: The qwen run identifier, also the notebook's ``run_id``.
        blocker: The sentence explaining why no training can happen here.
        probe: The local accelerator probe, recorded alongside.
        dataset_slug: The published dataset to mount. Defaults to the unprefixed
            slug this pipeline would publish under, so a notebook rendered on a
            blocked run still names the dataset a later run will attach.
        status: The stage verdict to record beside the artifacts.
        extra: Additional fields for the run record, such as a pushed kernel ref.

    Returns:
        Logical name to path, for the manifest.

    Raises:
        ml.datasets.schema.DatasetError: The notebook could not be rendered or a
            bundle file could not be copied.
        OSError: A bundle source is missing.
    """
    qwen = pipeline.config.qwen
    segment_dir = pipeline.qwen_dir / "segment-000"
    input_dir = segment_dir / QWEN_INPUT_DIRNAME
    input_dir.mkdir(parents=True, exist_ok=True)

    bundle = {}
    for source in (
        _split_path(pipeline.datasets_dir, "qwen", "train"),
        _split_path(pipeline.datasets_dir, "qwen", "validation"),
        pipeline.datasets_dir / LABEL_MAP_FILENAME,
    ):
        name = source.name
        if source.is_file():
            destination = input_dir / name
            shutil.copyfile(source, destination)
            bundle[name] = str(destination)
        else:
            _say(f"bundle       missing {source}; run --prepare before pushing the kernel")

    dataset_slug = dataset_slug or f"nexo-phase10/{QWEN_DATASET_VERSION}"
    notebook = render_qwen_training_notebook(
        config=_notebook_config(pipeline),
        dataset_slug=dataset_slug,
        run_id=run_id,
        segment_index=0,
        max_steps=qwen.segment_steps,
    )
    notebook_path = segment_dir / QWEN_NOTEBOOK_FILENAME
    notebook_path.write_text(notebook, encoding="utf-8")

    record_path = pipeline.qwen_dir / QWEN_RUN_FILENAME
    _write_json(
        record_path,
        {
            "run_id": run_id,
            "dataset_slug": dataset_slug,
            "notebook": notebook_path.name,
            "notebook_sha256": sha256_file(notebook_path),
            "segment_index": 0,
            "max_steps": qwen.segment_steps,
            "base_model": qwen.base_model,
            "seed": pipeline.seed,
            "status": str(status),
            "blocker": blocker,
            "local_accelerator": dict(probe),
            "required_vram_bytes": _required_vram_bytes(qwen.base_model),
            "generated_at": datetime.now(UTC).isoformat(),
            **dict(extra or {}),
        },
    )
    _say(f"notebook     {notebook_path}")
    _say(f"bundle       {input_dir}")
    return {"notebook": str(notebook_path), "run_record": str(record_path), **bundle}


def _publish_qwen_dataset(
    client: KaggleClient, pipeline: Pipeline, input_dir: Path, *, purpose: str = "train"
) -> str:
    """Publish a QLoRA bundle as a Kaggle dataset.

    Publishing before pushing is what makes the training kernel reproducible from
    the URL alone: the notebook mounts a slug, not a file, and the slug's revision
    is what a later segment or an evaluation run has to cite. It also keeps the
    bundle out of the kernel's own push directory, because Kaggle zips that whole
    directory and a corpus would then travel twice.

    ``purpose`` selects the slug rather than the contents, so the evaluation
    bundle — the same corpus plus the adapter it is scored against — versions as a
    *different* dataset instead of silently becoming revision N of the training
    one. A mount that changes meaning between revisions is a mount nobody can
    reason about after the fact.

    Args:
        client: The Kaggle client.
        pipeline: The resolved pipeline.
        input_dir: The directory holding the splits, and for the evaluation bundle
            the adapter directory too.
        purpose: ``"train"`` or ``"eval"``.

    Returns:
        The published slug as ``<username>/<slug>``.

    Raises:
        PipelineError: The bundle holds no JSONL split, so there is nothing to
            publish.
        RemoteError: The CLI is missing, or both create and version failed.
    """
    if not any(input_dir.glob("*.jsonl")):
        raise PipelineError(
            f"{input_dir} holds no JSONL split, so there is nothing to publish. "
            "Run --prepare before training"
        )
    slug = slugify(f"{KAGGLE_REF_PREFIX}-qwen-dataset-{purpose}")
    published = client.create_or_version_dataset(
        local_dir=input_dir,
        slug=slug,
        description=(
            f"Nexo Phase 10 Qwen {purpose} bundle, generated by ml.datasets.qwen_sft "
            f"({QWEN_DATASET_VERSION}, schema {SCHEMA_VERSION_QWEN}). Synthetic; see the "
            "run manifest for the seed and per-category counts."
            + (
                " Carries the LoRA adapter the paired base-vs-fine-tuned evaluation scores."
                if purpose == "eval"
                else ""
            )
        ),
        dir_name=slug,
    )
    return f"{client.probe().username.lower()}/{published.slug}"


def _push_qwen_kernel(
    pipeline: Pipeline,
    *,
    run_id: str,
    dataset_slug: str,
    blocker: str,
    probe: Mapping[str, Any],
    poll_seconds: int,
    max_seconds: int,
) -> StageOutcome:
    """Push one Qwen QLoRA segment and block until the kernel finishes.

    Pushing and waiting is deliberately one call. A QLoRA segment runs for hours
    and writes resumable checkpoints every ``save_every_n_steps``; a caller that
    pushed and returned would leave the operator polling by hand and would have no
    record of whether the run finished, failed, or was cancelled. Waiting here also
    means the stage's verdict is the kernel's terminal state rather than "accepted
    for execution", which is not a claim anyone should publish.

    Args:
        pipeline: The resolved pipeline.
        run_id: This segment's identifier, also the notebook's ``run_id``.
        dataset_slug: The published dataset to mount.
        blocker: The reason the stage would otherwise be blocked, carried into the
            run record so a reader sees why the push happened.
        probe: The local accelerator probe.
        poll_seconds: Delay between status polls.
        max_seconds: Ceiling on the wait.

    Returns:
        The outcome. ``PASSED`` on COMPLETE, ``FAILED`` on any other terminal
        state or on a CLI failure — with the output downloaded either way when the
        kernel produced any, because a failed segment's partial checkpoints are
        the input to the next one.

    Raises:
        ml.datasets.schema.DatasetError: The notebook could not be rendered.
    """
    client = KaggleClient()
    artifacts = _materialise_qwen_bundle(
        pipeline, run_id, blocker, probe, dataset_slug=dataset_slug, status=StageStatus.PASSED
    )
    notebook_path = Path(artifacts["notebook"])
    workdir = _kernel_workdir(pipeline, f"qwen-{run_id}")
    pushed_notebook = workdir / notebook_path.name
    shutil.copyfile(notebook_path, pushed_notebook)
    kernel_slug = slugify(f"{KAGGLE_REF_PREFIX}-{run_id.lower()}")
    ref = f"{client.probe().username.lower()}/{kernel_slug}"
    _write_json(
        workdir / "kernel-metadata.json",
        render_kernel_metadata(
            ref=ref,
            title=kernel_slug,
            code_file=pushed_notebook.name,
            dataset_sources=(dataset_slug,),
            enable_gpu=True,
        ),
    )
    _say(
        f"pushing      {ref} (dataset {dataset_slug}, max_steps {pipeline.config.qwen.segment_steps})"
    )
    try:
        kernel = client.push_kernel(workdir)
        _say(f"pushed       version {kernel.version} -> {kernel.url}")
        _say("             checkpoints land in the kernel output; rerun with --resume to continue")
        status = client.wait_for_kernel(
            kernel.ref,
            poll_seconds=poll_seconds,
            max_seconds=max_seconds,
            on_poll=lambda observed: _say(f"status       {observed.state}"),
        )
    except RemoteError as exc:
        _say(f"error        {exc}")
        _write_json(
            pipeline.qwen_dir / QWEN_RUN_FILENAME,
            {
                **json.loads((pipeline.qwen_dir / QWEN_RUN_FILENAME).read_text(encoding="utf-8")),
                "status": str(StageStatus.FAILED),
                "kernel_ref": ref,
                "error": str(exc),
            },
        )
        return StageOutcome(
            stage="train-qwen",
            status=StageStatus.FAILED,
            detail=str(exc),
            artifacts=artifacts,
        )

    output_dir = pipeline.qwen_dir / "segment-000" / "output"
    try:
        client.kernel_output(kernel.ref, output_dir)
        artifacts["kernel_output"] = str(output_dir)
        _say(f"output       {output_dir}")
    except RemoteError as exc:
        _say(f"error        the kernel finished but its output could not be downloaded: {exc}")

    _write_json(
        pipeline.qwen_dir / QWEN_RUN_FILENAME,
        {
            **json.loads((pipeline.qwen_dir / QWEN_RUN_FILENAME).read_text(encoding="utf-8")),
            "status": str(StageStatus.PASSED if status.state == "COMPLETE" else StageStatus.FAILED),
            "kernel_ref": kernel.ref,
            "kernel_version": kernel.version,
            "kernel_url": kernel.url,
            "kernel_state": status.state,
            "finished_at": datetime.now(UTC).isoformat(),
        },
    )
    ok = status.state in TERMINAL_STATES and status.state == "COMPLETE"
    return StageOutcome(
        stage="train-qwen",
        status=StageStatus.PASSED if ok else StageStatus.FAILED,
        detail=(
            f"segment 0 of {run_id} finished {status.state}; adapter and checkpoints under "
            f"{output_dir}"
            if ok
            else f"segment 0 of {run_id} ended {status.state}: {status.raw}"
        ),
        artifacts=artifacts,
    )


def stage_train_qwen(pipeline: Pipeline) -> StageOutcome:
    """Run one Qwen QLoRA segment, on any accelerator that can actually hold it.

    Two accelerators are candidates and both are probed before anything is
    uploaded: the local torch interpreter, and — via a probe a remote kernel wrote
    about the machine it ran on — the Kaggle session. Local first because a
    shortcut that works costs nothing; remote second because it is the only one
    that can hold an 8B model in 4 bits.

    **When neither can, this records BLOCKED and says why, in numbers and with
    evidence.** That is not a shrug. The notebook and its input bundle are still
    written, so the run is one ``kaggle kernels push`` away on hardware that can
    hold it, and the blocking reason is stored in ``qwen_run.json`` next to the
    probe that established it. The alternative — pushing a training kernel at a
    CPU batch image and reporting whatever comes back — would burn GPU quota,
    produce no adapter, and put a number in the report that means nothing.

    Args:
        pipeline: The resolved pipeline.

    Returns:
        The outcome. ``BLOCKED`` when neither candidate holds the model, with the
        notebook and bundle written either way.
    """
    started = time.monotonic()
    qwen = pipeline.config.qwen
    required = _required_vram_bytes(qwen.base_model)
    interpreter = _torch_interpreter()
    probe = _probe_accelerator(interpreter)
    _say(f"local        {_describe_accelerator(probe, required)}")
    local_fits = bool(probe.get("cuda")) and int(probe.get("total_vram_bytes", 0)) >= required

    remote_ok, remote_reason = _remote_gpu_verdict(pipeline)
    remote_probe = _read_remote_probe(pipeline)
    if remote_probe is None:
        _say(
            "remote       no accelerator probe on disk. Run --probe-remote to push one; "
            "without it the remote cannot be called either way"
        )
    else:
        _say(f"remote       {remote_reason}")
        if not remote_ok:
            _say(
                f"             kernel metadata records the request, not the machine: "
                f"requested GPU, observed torch {remote_probe.get('torch_version')} with "
                f"cuda={remote_probe.get('torch_cuda_available')}, "
                f"devices={remote_probe.get('device_nodes') or 'none'}"
            )

    run_id = new_run_id(
        "qwen", when=datetime.now(UTC), dataset_version=QWEN_DATASET_VERSION, seed=pipeline.seed
    )
    if not local_fits and not remote_ok:
        blocker = (
            f"{qwen.base_model} under 4-bit QLoRA needs about {_gib(required):.1f} GiB "
            f"({_gib(int(_parameter_count(qwen.base_model) * Q4_BYTES_PER_PARAM)):.1f} GiB of frozen "
            f"4-bit weights plus {_gib(VRAM_HEADROOM_BYTES):.1f} GiB of activations and optimiser "
            f"state). Local: {_describe_accelerator(probe, required)}. Remote: {remote_reason}. "
            "No training was attempted and no adapter exists."
        )
        _say("")
        _say(f"BLOCKED      {blocker}")
        artifacts = _materialise_qwen_bundle(
            pipeline,
            run_id,
            blocker,
            probe,
            extra={"remote_probe": remote_probe, "remote_verdict": remote_reason},
        )
        return StageOutcome(
            stage="train-qwen",
            status=StageStatus.BLOCKED,
            detail=blocker,
            duration_seconds=time.monotonic() - started,
            artifacts=artifacts,
        )

    # The bundle is materialised *before* the publish, not after. Publishing an
    # input directory that has not been populated yet uploads a dataset of
    # nothing, and the notebook then mounts a corpus that does not exist — a
    # failure that surfaces as a training run with zero examples rather than as
    # the missing `--prepare` it actually is.
    local_artifacts = _materialise_qwen_bundle(
        pipeline, run_id, "", probe, status=StageStatus.PASSED
    )
    input_dir = pipeline.qwen_dir / "segment-000" / QWEN_INPUT_DIRNAME
    if not any(input_dir.glob("*.jsonl")):
        blocker = (
            f"{input_dir} holds no JSONL split, so there is nothing to train on. "
            "Run --prepare before --train-qwen"
        )
        return StageOutcome(
            stage="train-qwen",
            status=StageStatus.BLOCKED,
            detail=blocker,
            duration_seconds=time.monotonic() - started,
            artifacts=local_artifacts,
        )
    try:
        client = KaggleClient()
        slug = _publish_qwen_dataset(client, pipeline, input_dir)
        _say(f"dataset      published {slug}")
    except (RemoteError, PipelineError, OSError) as exc:
        blocker = f"the QLoRA bundle could not be published: {type(exc).__name__}: {exc}"
        _say("")
        _say(f"BLOCKED      {blocker}")
        return StageOutcome(
            stage="train-qwen",
            status=StageStatus.BLOCKED,
            detail=blocker,
            duration_seconds=time.monotonic() - started,
        )

    return _push_qwen_kernel(
        pipeline,
        run_id=run_id,
        dataset_slug=slug,
        blocker=(
            f"pushed to a remote accelerator verified by probe: {remote_reason}"
            if not local_fits
            else f"local accelerator: {_describe_accelerator(probe, required)}"
        ),
        probe=probe,
        poll_seconds=QWEN_POLL_SECONDS,
        max_seconds=QWEN_TIMEOUT_SECONDS,
    )


def _adapter_dir_name(pipeline: Pipeline) -> str | None:
    """Find the LoRA adapter a previous segment left behind.

    The evaluation compares ``Qwen3-8B`` against ``Qwen3-8B + adapter``, so the
    adapter is not optional in the same way a checkpoint is: without one there is
    no second side, and a report that compared the base model against itself
    would be worse than no report.

    Args:
        pipeline: The resolved pipeline.

    Returns:
        The directory name under ``artifacts/qwen/`` holding
        ``adapter_config.json``, or None when no segment has produced one.
    """
    root = pipeline.qwen_dir
    if not root.is_dir():
        return None
    candidates = sorted(
        (path.parent for path in root.rglob("adapter_config.json")),
        key=lambda path: len(path.parts),
    )
    return candidates[0].name if candidates else None


def stage_eval_qwen(pipeline: Pipeline) -> StageOutcome:
    """Render the base-vs-fine-tuned evaluation and push it if a GPU is available.

    The comparison is paired by construction: the same held-out prompts, the same
    system preamble, the same greedy decoding and the same seed go to both sides,
    so the only variable is the adapter. The rubric is deterministic — subject
    named, action a person can take, no claim of having already executed anything,
    sane length, no leaked credential — because a comparison whose scorer is a
    model is a comparison that cannot be reproduced.

    Without an adapter this stage is **BLOCKED**, and says so. There is no
    fallback to "evaluate the base model anyway": that would produce a number
    labelled fine-tuned that measures nothing, which is the specific failure this
    whole phase is built to avoid.

    Args:
        pipeline: The resolved pipeline.

    Returns:
        The outcome.
    """
    started = time.monotonic()
    run_id = new_run_id(
        "qwen-eval",
        when=datetime.now(UTC),
        dataset_version=QWEN_DATASET_VERSION,
        seed=pipeline.seed,
    )
    adapter = _adapter_dir_name(pipeline)
    if adapter is None:
        detail = (
            f"no LoRA adapter under {pipeline.qwen_dir}, so there is nothing to compare the "
            f"base model against. Run --train-qwen on hardware that can hold "
            f"{pipeline.config.qwen.base_model} first; a base-versus-base comparison would be "
            "reported as a fine-tuning result and is not one"
        )
        _say(f"BLOCKED      {detail}")
        return StageOutcome(
            stage="eval-qwen",
            status=StageStatus.BLOCKED,
            detail=detail,
            duration_seconds=time.monotonic() - started,
        )

    remote_ok, remote_reason = _remote_gpu_verdict(pipeline)
    if not remote_ok:
        detail = (
            f"adapter {adapter} is present but no accelerator is available to run the paired "
            f"evaluation: {remote_reason}. The adapter is a remote artifact; evaluate it on the "
            "same kind of session that produced it"
        )
        _say(f"BLOCKED      {detail}")
        return StageOutcome(
            stage="eval-qwen",
            status=StageStatus.BLOCKED,
            detail=detail,
            duration_seconds=time.monotonic() - started,
            artifacts={"adapter": str(pipeline.qwen_dir / adapter)},
        )

    eval_dir = pipeline.qwen_dir / "eval-000"
    eval_dir.mkdir(parents=True, exist_ok=True)

    # Both the held-out split and the adapter have to reach the kernel. The
    # notebook resolves both from an attached dataset, so the dataset is staged
    # and published here rather than assumed — a kernel pushed with no
    # dataset_sources would fail inside the notebook's own "is the input
    # attached?" check, long after the push was reported as accepted.
    eval_input = eval_dir / "input"
    eval_input.mkdir(parents=True, exist_ok=True)
    for split in ("test", "validation"):
        source = _split_path(pipeline.datasets_dir, "qwen", split)
        if source.is_file():
            shutil.copyfile(source, eval_input / source.name)
        else:
            _say(f"eval input   missing {source}; run --prepare before --eval-qwen")
    adapter_source = pipeline.qwen_dir / adapter
    staged_adapter = eval_input / adapter
    shutil.copytree(adapter_source, staged_adapter, dirs_exist_ok=True)

    try:
        client = KaggleClient()
        slug = _publish_qwen_dataset(client, pipeline, eval_input, purpose="eval")
    except (RemoteError, PipelineError, OSError) as exc:
        detail = f"the evaluation bundle could not be published: {type(exc).__name__}: {exc}"
        _say(f"BLOCKED      {detail}")
        return StageOutcome(
            stage="eval-qwen",
            status=StageStatus.BLOCKED,
            detail=detail,
            duration_seconds=time.monotonic() - started,
        )
    _say(f"dataset      published {slug}")

    notebook_path = eval_dir / "qwen_eval.ipynb"
    notebook_path.write_text(
        render_eval_notebook(
            config=_notebook_config(pipeline),
            dataset_slug=slug,
            run_id=run_id,
            adapter_dir_name=adapter,
        ),
        encoding="utf-8",
    )
    workdir = _kernel_workdir(pipeline, f"qwen-eval-{run_id.lower()}")
    pushed = workdir / notebook_path.name
    shutil.copyfile(notebook_path, pushed)
    kernel_slug = slugify(f"{KAGGLE_REF_PREFIX}-{run_id.lower()}")
    ref = f"{client.probe().username.lower()}/{kernel_slug}"
    _write_json(
        workdir / "kernel-metadata.json",
        render_kernel_metadata(
            ref=ref,
            title=kernel_slug,
            code_file=pushed.name,
            dataset_sources=(slug,),
            enable_gpu=True,
        ),
    )
    _say(f"pushing      {ref} (adapter {adapter})")
    try:
        kernel = client.push_kernel(workdir)
        status = client.wait_for_kernel(
            kernel.ref,
            poll_seconds=QWEN_POLL_SECONDS,
            max_seconds=QWEN_TIMEOUT_SECONDS,
            on_poll=lambda observed: _say(f"status       {observed.state}"),
        )
        output_dir = (
            client.kernel_output(kernel.ref, eval_dir / "output")
            if status.state == "COMPLETE"
            else eval_dir / "output"
        )
    except RemoteError as exc:
        return StageOutcome(
            stage="eval-qwen",
            status=StageStatus.FAILED,
            detail=str(exc),
            duration_seconds=time.monotonic() - started,
            artifacts={"notebook": str(notebook_path)},
        )
    _say(f"output       {output_dir}")
    ok = status.state == "COMPLETE"
    return StageOutcome(
        stage="eval-qwen",
        status=StageStatus.PASSED if ok else StageStatus.FAILED,
        detail=(
            f"paired evaluation of {pipeline.config.qwen.base_model} against {adapter} finished; "
            f"report under {output_dir}"
            if ok
            else f"paired evaluation ended {status.state}: {status.raw}"
        ),
        duration_seconds=time.monotonic() - started,
        artifacts={"notebook": str(notebook_path), "output": str(output_dir)},
    )


def stage_qwen_status(pipeline: Pipeline) -> StageOutcome:
    """Report what the Kaggle CLI can see and, if one was pushed, its state.

    Args:
        pipeline: The resolved pipeline.

    Returns:
        The outcome; BLOCKED when the CLI is unusable, since an unauthenticated
        machine is a legitimate state and not a failure.
    """
    started = time.monotonic()
    try:
        environment = KaggleClient().probe()
    except (RemoteError, OSError) as exc:
        return StageOutcome(
            stage="qwen-status",
            status=StageStatus.BLOCKED,
            detail=f"kaggle CLI unusable: {type(exc).__name__}: {exc}",
            duration_seconds=time.monotonic() - started,
        )
    quota = "unknown" if environment.gpu_quota_hours is None else f"{environment.gpu_quota_hours:g}"
    parts = [
        f"username {environment.username or 'unknown'}",
        f"authenticated {environment.authenticated}",
        f"cli {environment.kaggle_cli_version or 'unknown'}",
        f"gpu quota hours {quota}",
    ]
    _say("environment  " + ", ".join(parts))

    client = KaggleClient()
    available, reason = _remote_gpu_verdict(pipeline)
    _say(f"accelerator  {'yes' if available else 'no'}: {reason}")
    parts.append(f"accelerator {'available' if available else 'absent'}")

    record_path = pipeline.qwen_dir / QWEN_RUN_FILENAME
    ref = None
    if record_path.is_file():
        record = json.loads(record_path.read_text(encoding="utf-8"))
        ref = record.get("kernel_ref")
        _say(f"last run     {record.get('run_id')} status {record.get('status')}")
        if record.get("blocker"):
            _say(f"blocker      {record['blocker']}")
    if ref:
        try:
            status = client.kernel_status(ref)
        except RemoteError as exc:
            status = None
            _say(f"kernel       status unavailable: {exc}")
        if status is not None:
            _say(f"kernel       {status.ref} is {status.state}")
            parts.append(f"kernel {status.ref} {status.state}")
    else:
        _say("kernel       none pushed by this pipeline; the rendered notebook is the handoff")
        parts.append("kernel none pushed")

    # The account hosts more than Phase 10, so only the prefixed slugs are
    # listed. This is the "is my run still out there?" question, and the answer
    # nobody can get from the local artifact directory.
    kernels = client.list_kernels()
    if kernels:
        _say(f"phase 10 kernels ({len(kernels)}):")
        for kernel in kernels[:10]:
            _say(f"  {kernel.ref:<52} v{kernel.version or '?'}")
    else:
        _say("phase 10 kernels  none listed")

    checkpoints = (
        sorted((pipeline.artifacts_dir / QWEN_DIRNAME).glob("checkpoints/step-*"))
        if (pipeline.artifacts_dir / QWEN_DIRNAME).is_dir()
        else []
    )
    if checkpoints:
        _say(f"segments     {len(checkpoints)} remote segment directories recorded locally")
        parts.append(f"{len(checkpoints)} segment(s)")
    return StageOutcome(
        stage="qwen-status",
        status=StageStatus.PASSED,
        detail="; ".join(parts),
        duration_seconds=time.monotonic() - started,
    )


STAGES = (
    Stage("prepare", "--prepare", "build, validate and split both corpora", stage_prepare),
    Stage(
        "train-small",
        "--train-small",
        "fine-tune the routing classifier locally",
        stage_train_small,
    ),
    Stage(
        "evaluate", "--evaluate", "score the classifier on the held-out test split", stage_evaluate
    ),
    Stage("train-qwen", "--train-qwen", "fine-tune Qwen3-8B under QLoRA", stage_train_qwen),
    Stage(
        "probe-remote",
        "--probe-remote",
        "push the accelerator probe and record what the session actually was",
        stage_probe_remote,
    ),
    Stage(
        "eval-qwen",
        "--eval-qwen",
        "compare the base model against the fine-tuned adapter",
        stage_eval_qwen,
    ),
    Stage("qwen-status", "--qwen-status", "report the remote kernel state", stage_qwen_status),
)


def build_parser() -> argparse.ArgumentParser:
    """Describe the command line.

    Every path default matches what ``ml/configs/*.toml`` and ``.gitignore``
    already assume, so a bare ``python -m ml.train`` writes where the project
    expects to find the output and nothing surprising lands in a tracked
    directory.

    Returns:
        The parser, ready for ``parse_args``.
    """
    parser = argparse.ArgumentParser(
        prog="ml.train",
        description=(
            "Phase 10 pipeline: prepare datasets, train the routing classifier, evaluate it, "
            "and drive the Qwen QLoRA run. With no stage flag, runs every stage."
        ),
    )
    parser.add_argument("--prepare", action="store_true", help="build, validate and split datasets")
    parser.add_argument(
        "--train-small", action="store_true", help="fine-tune the routing classifier locally"
    )
    parser.add_argument("--train-qwen", action="store_true", help="fine-tune Qwen3-8B under QLoRA")
    parser.add_argument(
        "--probe-remote",
        action="store_true",
        help="push a one-cell Kaggle kernel that records whether the session got a GPU",
    )
    parser.add_argument(
        "--eval-qwen",
        action="store_true",
        help="compare the base Qwen against the fine-tuned adapter on the held-out split",
    )
    parser.add_argument("--qwen-status", action="store_true", help="report the remote kernel state")
    parser.add_argument("--evaluate", action="store_true", help="score the trained classifier")
    parser.add_argument(
        "--resume", action="store_true", help="resume the classifier from its latest checkpoint"
    )
    parser.add_argument("--all", action="store_true", help="run every stage (the default)")
    parser.add_argument("--seed", type=int, default=20260101, help="master seed for both corpora")
    parser.add_argument(
        "--per-intent",
        type=int,
        default=150,
        help=(
            "routing examples per intent (default 150). Higher values need wider slot pools: "
            "the builder raises rather than pad the corpus when a family cannot produce that "
            "many distinct utterances, which is the intended signal to add templates, not to "
            "lower the number"
        ),
    )
    parser.add_argument(
        "--per-category", type=int, default=40, help="qwen examples per category (default 40)"
    )
    parser.add_argument(
        "--config-dir", type=Path, default=DEFAULT_CONFIG_DIR, help="directory holding the TOMLs"
    )
    parser.add_argument(
        "--datasets-dir", type=Path, default=Path("ml/datasets"), help="prepared split directory"
    )
    parser.add_argument(
        "--artifacts-dir", type=Path, default=Path("ml/artifacts"), help="model outputs"
    )
    parser.add_argument(
        "--reports-dir", type=Path, default=Path("ml/reports"), help="validation and run reports"
    )
    parser.add_argument("--dry-run", action="store_true", help="print the stage plan and exit")
    parser.add_argument("--verbose", action="store_true", help="print the commands each stage runs")
    return parser


def selected_stages(args: argparse.Namespace) -> tuple[Stage, ...]:
    """Decide which stages this invocation asks for.

    Args:
        args: The parsed arguments.

    Returns:
        The stages in pipeline order. Stage flags are a set, not a sequence: a
        caller asking for ``--evaluate --prepare`` wants both, and getting the
        second before the first is not a preference anyone has.

    Raises:
        argparse.ArgumentError: ``--resume`` was passed without ``--train-small``.
    """
    chosen = {stage.flag for stage in STAGES if getattr(args, stage.flag[2:].replace("-", "_"))}
    if args.resume and "--train-small" not in chosen:
        chosen.add("--train-small")
    if not chosen or args.all:
        return STAGES
    return tuple(stage for stage in STAGES if stage.flag in chosen)


def _print_plan(stages: Sequence[Stage], pipeline: Pipeline | None) -> None:
    """Print the stage plan, before any of it runs.

    Args:
        stages: The stages that would run.
        pipeline: The resolved pipeline, or None when the configuration could
            not be loaded and the paths below are therefore unknown.
    """
    _say("Phase 10 plan")
    _say("-" * 78)
    for index, stage in enumerate(stages, start=1):
        _say(f"  {index}. {stage.name:<13} {stage.description}")
    _say("-" * 78)
    if pipeline is None:
        _say("  (configuration could not be loaded, so no paths are resolved)")
        return
    _say(f"  seed         {pipeline.seed}")
    _say(f"  datasets     {pipeline.datasets_dir}")
    _say(f"  artifacts    {pipeline.artifacts_dir}")
    _say(f"  reports      {pipeline.reports_dir}")


def _run_stage(stage: Stage, pipeline: Pipeline) -> StageOutcome:
    """Run one stage, converting an expected failure into a recorded outcome.

    The point is that a stage which cannot proceed says *why* in the summary
    rather than aborting the whole pipeline; the caller still sees the failure
    through the exit code.

    Args:
        stage: The stage.
        pipeline: The resolved pipeline.

    Returns:
        The outcome. FAILED for a raised error, PASSED otherwise.
    """
    _banner(stage)
    started = time.monotonic()
    try:
        return stage.run(pipeline)
    except (PipelineError, DatasetError, OSError, ValueError) as exc:
        _say(f"error        {type(exc).__name__}: {exc}")
        return StageOutcome(
            stage=stage.name,
            status=StageStatus.FAILED,
            detail=f"{type(exc).__name__}: {exc}",
            duration_seconds=time.monotonic() - started,
        )


def _manifest_for(pipeline: Pipeline, outcome: StageOutcome) -> RunManifest:
    """Build the run manifest recording one stage.

    ``evaluation`` carries the stage verdict because it is the free-form block
    every other field type refuses to be: a manifest that cannot say whether its
    stage passed, failed or was blocked cannot answer the only question a reader
    opens it with.

    Args:
        pipeline: The resolved pipeline.
        outcome: What the stage did.

    Returns:
        The manifest.
    """
    commit, dirty = git_revision(pipeline.backend_root)
    base = {
        "prepare": ("dataset-build", "ml.datasets.routing", ROUTING_DATASET_VERSION),
        "train-small": (
            "routing-classifier",
            pipeline.config.small_model.base_model,
            ROUTING_DATASET_VERSION,
        ),
        "evaluate": (
            "routing-classifier-eval",
            pipeline.config.small_model.base_model,
            ROUTING_DATASET_VERSION,
        ),
        "train-qwen": ("qwen-qlora", pipeline.config.qwen.base_model, QWEN_DATASET_VERSION),
        "probe-remote": (
            "accelerator-probe",
            pipeline.config.qwen.base_model,
            QWEN_DATASET_VERSION,
        ),
        "qwen-status": ("qwen-qlora", pipeline.config.qwen.base_model, QWEN_DATASET_VERSION),
        "eval-qwen": ("qwen-qlora-eval", pipeline.config.qwen.base_model, QWEN_DATASET_VERSION),
    }[outcome.stage]
    checksums = {
        name: sha256_file(Path(path))
        for name, path in outcome.artifacts.items()
        if isinstance(path, str) and Path(path).is_file()
    }
    return RunManifest(
        run_id=new_run_id(outcome.stage, when=datetime.now(UTC), seed=pipeline.seed),
        model_name=base[0],
        base_model=base[1],
        model_version="v1",
        dataset_version=base[2],
        dataset_source=f"ml.datasets (seed {pipeline.seed})",
        schema_version=f"{SCHEMA_VERSION_ROUTING} + {SCHEMA_VERSION_QWEN}",
        preprocessing_version=SPLIT_CONFIG_VERSION,
        code_commit=commit,
        code_dirty=dirty,
        config={
            "stage": outcome.stage,
            "datasets_dir": str(pipeline.datasets_dir),
            "artifacts_dir": str(pipeline.artifacts_dir),
        },
        hyperparameters={
            "per_intent": pipeline.per_intent,
            "per_category": pipeline.per_category,
            "resume": pipeline.resume,
        },
        seed=pipeline.seed,
        environment=collect_environment(),
        started_at=datetime.now(UTC).isoformat(),
        finished_at=datetime.now(UTC).isoformat(),
        duration_seconds=round(outcome.duration_seconds, 3),
        evaluation={"status": str(outcome.status), "detail": outcome.detail},
        artifacts={name: path for name, path in outcome.artifacts.items() if isinstance(path, str)},
        checksums=checksums,
    )


def _print_summary(outcomes: Sequence[StageOutcome], reports_dir: Path) -> None:
    """Print the PASS/FAIL/BLOCKED table and write it next to the reports.

    Args:
        outcomes: One entry per stage that ran.
        reports_dir: Where ``pipeline_summary.json`` goes.
    """
    width = max((len(outcome.stage) for outcome in outcomes), default=6)
    _say("")
    _say("=" * 78)
    _say(f"  SUMMARY  ({len(outcomes)} stage(s))")
    _say("=" * 78)
    for outcome in outcomes:
        _say(f"  {outcome.stage:<{width}}  {outcome.status!s:<8}  {outcome.detail}")
    blocked = sum(1 for outcome in outcomes if outcome.status is StageStatus.BLOCKED)
    failed = sum(1 for outcome in outcomes if outcome.status is StageStatus.FAILED)
    verdict = "FAIL" if failed else "PASS"
    note = f" ({blocked} blocked)" if blocked else ""
    _say("-" * 78)
    _say(f"  {verdict}{note}")
    _write_json(
        reports_dir / "pipeline_summary.json",
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "verdict": verdict,
            "blocked": blocked,
            "failed": failed,
            "stages": [outcome.to_dict() for outcome in outcomes],
        },
    )
    _say(f"  summary written to {reports_dir / 'pipeline_summary.json'}")


def main(argv: list[str] | None = None) -> int:
    """Run the requested stages and report the outcome.

    Args:
        argv: Command-line arguments, or None to read ``sys.argv[1:]``.

    Returns:
        0 when every requested stage passed or was blocked — a blocked stage is
        a documented impossibility, not a failure — and 1 when any stage failed.
    """
    args = build_parser().parse_args(argv)
    stages = selected_stages(args)
    backend_root = _backend_root()
    config_dir = _under(backend_root, args.config_dir)

    try:
        config, small_model_config = _pipeline_config(config_dir)
    except PipelineError as exc:
        if not args.dry_run:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        config = None
        small_model_config = config_dir / "small_model.toml"

    if config is None:
        _print_plan(stages, None)
        return 0

    pipeline = Pipeline(
        config=config,
        config_dir=config_dir,
        small_model_config=small_model_config,
        backend_root=backend_root,
        datasets_dir=_under(backend_root, args.datasets_dir),
        artifacts_dir=_under(backend_root, args.artifacts_dir),
        reports_dir=_under(backend_root, args.reports_dir),
        seed=args.seed if args.seed is not None else config.seed,
        per_intent=args.per_intent,
        per_category=args.per_category,
        resume=args.resume,
        verbose=args.verbose,
    )
    _print_plan(stages, pipeline)
    if args.dry_run:
        _say("")
        _say("dry run: nothing executed.")
        return 0

    _say("")
    _say(
        f"git {git_revision(backend_root)[0][:12]}  "
        f"torch {collect_environment()['torch_version'] or 'not in this interpreter'}"
    )
    outcomes: list[StageOutcome] = []
    for stage in stages:
        outcome = _run_stage(stage, pipeline)
        outcomes.append(outcome)
        manifest = _manifest_for(pipeline, outcome)
        manifest_dir = pipeline.reports_dir / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / f"{manifest.run_id}.json").write_text(
            stable_json_dumps(manifest.to_dict()) + "\n", encoding="utf-8"
        )
        (manifest_dir / f"{manifest.run_id}.md").write_text(
            manifest.to_markdown(), encoding="utf-8"
        )

    _print_summary(outcomes, pipeline.reports_dir)
    return 1 if any(outcome.status is StageStatus.FAILED for outcome in outcomes) else 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
