"""The single entry point for Phase 10: discover, build, split, train, evaluate.

Run it from ``backend/`` either as ``python -m ml.train`` or through the Makefile
(``make ml-prepare``, ``make ml-all``). Every stage is individually selectable;
with no stage flag the whole pipeline runs in order. ``--dry-run`` prints the plan
and exits.

**The stages, and what each one is answerable for.**

``prepare``
    Harvests the capability inventory from ``app/`` by parsing the source (never
    importing it), cross-checks ``num_labels`` against the intent taxonomy,
    builds the routing corpus, refuses to continue unless every validator passes,
    splits it without leakage and writes the JSONL splits, the label map, the
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

**Why there are exactly three.** NEXUS runs one trained model: a
``deberta-v3-base`` intent router over fourteen Nexo surfaces. There is no
second model to train, no remote accelerator to talk to and no adapter to
evaluate, so a stage that could only exist for a model this project does not run
would be scaffolding that reports progress without producing anything. The
earlier Phase 10 draft carried Qwen3-8B QLoRA stages; they were removed with the
model, not disabled, because dead code that looks live is worse than its absence.

**The resume contract.** ``--resume`` is passed through to the local trainer,
which restores the furthest checkpoint under ``artifacts/small-model/checkpoints``
after checking it belongs to the current data (same checksum, same dataset
version). Without ``--resume`` a stage that finds a resumable checkpoint says so
and starts fresh, rather than silently continuing a run whose provenance nobody
asked for. Nothing is deleted on either path.

**Phase 10 produces artifacts. It never loads a model into the running
application.** Nothing in this module imports ``app``, and nothing here is on a
request path. Serving this checkpoint is Phase 11's job, and the deterministic
engines in ``app.services`` remain the fallback that the learned router is
measured against until then.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ml.datasets.capabilities import build_capability_inventory, save_inventory
from ml.datasets.routing import ROUTING_DATASET_VERSION, build_routing_dataset, label_map
from ml.datasets.schema import (
    SCHEMA_VERSION_ROUTING,
    DatasetError,
    sha256_file,
    stable_json_dumps,
    write_jsonl,
)
from ml.datasets.taxonomy import INTENT_NAMES, TAXONOMY_VERSION
from ml.evaluation.metrics import evaluate
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
from ml.validation import (
    ValidationReport,
    assert_clean,
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


#: Where downloaded kernel output lands. ``verify_gpu_available`` reads the probe
#: out of this directory, so the path has to be passed to it explicitly rather
#: than inferred: its default is ``artifacts/remote`` relative to the working
#: directory, and the orchestrator does not control the working directory of the
#: process that imports it.
REMOTE_DIRNAME = "remote"


class PipelineError(Exception):
    """A stage cannot proceed, and saying why beats a stack trace.

    Distinct from :class:`~ml.datasets.schema.DatasetError`, which means the data
    itself is wrong: a ``PipelineError`` is about the machine, the configuration
    or the environment, and the operator can fix it without editing a dataset.
    """


class StageStatus(StrEnum):
    """How a stage ended.

    ``BLOCKED`` exists so "this cannot run here, and here is exactly why" is not
    forced to masquerade as either success or failure.
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
        prefix: The corpus name, always ``"routing"`` today.
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

    assert_clean(routing_report)
    _say(f"routing      {len(routing_records)} rows, validation PASSED")

    split_config = SplitConfig(seed=pipeline.seed)
    routing_buckets, routing_counts = _split_records(
        routing_records,
        key_field="text",
        label_field="intent",
        duplicate_field="text",
        config=split_config,
    )

    leakage_report = validate_splits(
        {name: [row["text"] for row in routing_buckets[name]] for name in SPLIT_NAMES},
        duplicate_key_fn=near_duplicate_key,
    )
    _write_report(leakage_report, pipeline.reports_dir, "routing_splits")
    assert_clean(leakage_report)

    artifacts: dict[str, str] = {}
    written: list[Path] = []
    for name in SPLIT_NAMES:
        path = _split_path(pipeline.datasets_dir, "routing", name)
        count = write_jsonl(path, routing_buckets[name])
        written.append(path)
        _say(f"split        routing_{name}.jsonl  {count} rows")
        artifacts[f"routing_{name}"] = str(path)

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
        detail=f"{len(routing_records)} routing rows {dict(routing_counts)}",
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


STAGES = (
    Stage("prepare", "--prepare", "build, validate and split the routing corpus", stage_prepare),
    Stage(
        "train-small",
        "--train-small",
        "fine-tune the routing classifier locally",
        stage_train_small,
    ),
    Stage(
        "evaluate", "--evaluate", "score the classifier on the held-out test split", stage_evaluate
    ),
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
            "and report. With no stage flag, runs every stage."
        ),
    )
    parser.add_argument("--prepare", action="store_true", help="build, validate and split datasets")
    parser.add_argument(
        "--train-small", action="store_true", help="fine-tune the routing classifier locally"
    )
    parser.add_argument("--evaluate", action="store_true", help="score the trained classifier")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="resume the classifier from its latest checkpoint",
    )
    parser.add_argument("--all", action="store_true", help="run every stage (the default)")
    parser.add_argument("--seed", type=int, default=20260101, help="master seed for the corpus")
    parser.add_argument(
        "--per-intent",
        type=int,
        default=200,
        help=(
            "routing examples per intent (default 200). Higher values need wider slot pools: "
            "the builder raises rather than pad the corpus when a family cannot produce that "
            "many distinct utterances, which is the intended signal to add templates, not to "
            "lower the number"
        ),
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
        schema_version=SCHEMA_VERSION_ROUTING,
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
