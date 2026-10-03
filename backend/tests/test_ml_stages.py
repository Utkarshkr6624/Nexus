"""Tests for the Phase 10 pipeline's remote stages and their honesty contract.

**Why these tests exist and why they never touch the network.** The remote stages
are the only part of Phase 10 whose outcome depends on a machine nobody in the
repository can inspect, and the failure mode they exist to prevent is the quiet
one: a stage that reports a fine-tune it did not perform. So every test here runs
against a stubbed :class:`~ml.training.remote.KaggleClient` and asserts on the
*recorded verdict* — ``qwen_run.json``, ``gpu_probe_run.json``, the rendered
notebook — rather than on console output. A test that only checked stdout would
still pass if the record on disk said something else.

Three properties are asserted throughout, and they are the ones that matter:

1. **BLOCKED is a first-class outcome**, distinct from FAILED. An impossible run
   is documented; a broken run is a bug.
2. **BLOCKED still produces artifacts.** The notebook and its input bundle are
   written before the verdict is reached, so a blocked run is one push away on
   hardware that can hold the model rather than a run that has to be rebuilt.
3. **Nothing claims a fine-tune that did not happen.** There is no code path that
   records an adapter, a step count or a base-versus-fine-tuned metric without a
   kernel having actually run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from ml import train
from ml.kaggle.notebook import render_gpu_probe_notebook
from ml.training.config import load_config
from ml.training.remote import (
    GPU_PROBE_FILENAME,
    KaggleEnvironment,
    KernelRef,
    KernelStatus,
    RemoteError,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = BACKEND_ROOT / "ml" / "configs"

#: A probe as a GPU kernel would have written it: a device node present.
GPU_PROBE = {
    "recorded_at": "2099-01-01T00:00:00Z",
    "nvidia_smi": True,
    "device_nodes": ["/dev/nvidia0"],
    "torch_cuda_available": True,
    "device_name": "Tesla T4",
    "total_vram_bytes": 16 * 1024**3,
}

#: A probe as this account's sessions actually wrote it.
CPU_PROBE = {
    "recorded_at": "2099-01-01T00:00:00Z",
    "nvidia_smi": False,
    "device_nodes": [],
    "torch_cuda_available": False,
    "device_name": None,
    "total_vram_bytes": None,
    "internet": {"huggingface.co": "dns ok", "pypi.org": "dns ok"},
}

ENVIRONMENT = KaggleEnvironment(
    username="ExampleOwner",
    kaggle_cli_version="2.2.4",
    gpu_quota_hours=30.0,
    authenticated=True,
)


def _pipeline(tmp_path: Path, **overrides: Any) -> train.Pipeline:
    """Build a pipeline rooted in ``tmp_path`` with no real accelerator."""
    config, small_model_config = train._pipeline_config(CONFIG_DIR)
    return train.Pipeline(
        config=config,
        config_dir=CONFIG_DIR,
        small_model_config=small_model_config,
        backend_root=BACKEND_ROOT,
        datasets_dir=tmp_path / "datasets",
        artifacts_dir=tmp_path / "artifacts",
        reports_dir=tmp_path / "reports",
        seed=20260101,
        per_intent=150,
        per_category=40,
        resume=False,
        verbose=False,
        **overrides,
    )


class StubClient:
    """A :class:`KaggleClient` that records calls instead of making them.

    Every method the stages use is present. Anything not stubbed raises, because a
    stage reaching an unexpected remote call is a defect worth failing on rather
    than something to paper over with a permissive mock.
    """

    def __init__(self, *, state: str = "COMPLETE", publishes: bool = True) -> None:
        self.state = state
        self.publishes = publishes
        self.calls: list[str] = []
        self.fail_on: str | None = None

    def probe(self) -> KaggleEnvironment:
        """Report an authenticated account."""
        self.calls.append("probe")
        return ENVIRONMENT

    def push_kernel(self, workdir: Path) -> KernelRef:
        """Accept the push and return a fixed ref and version."""
        self.calls.append("push_kernel")
        if self.fail_on == "push_kernel":
            raise RemoteError(command="kaggle kernels push", returncode=1, stderr="boom")
        return KernelRef(ref="example-owner/nexo-phase10-x", version=7, url="https://kaggle/x")

    def wait_for_kernel(self, ref: str, **_: Any) -> KernelStatus:
        """Return the state the test configured, immediately."""
        self.calls.append("wait_for_kernel")
        return KernelStatus(ref=ref, state=self.state, raw=self.state)

    def kernel_output(self, ref: str, dest: Path) -> Path:
        """Create the download destination without downloading anything."""
        self.calls.append("kernel_output")
        dest.mkdir(parents=True, exist_ok=True)
        return dest

    def kernel_logs(self, ref: str, dest: Path) -> Path:
        """Create the log destination without downloading anything."""
        self.calls.append("kernel_logs")
        dest.mkdir(parents=True, exist_ok=True)
        return dest

    def kernel_status(self, ref: str) -> KernelStatus:
        """Return the state the test configured."""
        self.calls.append("kernel_status")
        return KernelStatus(ref=ref, state=self.state, raw=self.state)

    def list_kernels(self) -> tuple[KernelRef, ...]:
        """One Phase 10 kernel, as the prefixed listing would return."""
        self.calls.append("list_kernels")
        return (KernelRef(ref="example-owner/nexo-phase10-gpu-probe", version=2, url="u"),)

    def create_or_version_dataset(self, *, slug: str, **_: Any):
        """Record the slug the caller asked to publish under, and echo it back."""
        self.calls.append("create_or_version_dataset")
        self.published_slugs = getattr(self, "published_slugs", [])
        self.published_slugs.append(slug)
        if not self.publishes:
            raise RemoteError(command="kaggle datasets create", returncode=1, stderr="no quota")
        from ml.training.remote import DatasetRef

        return DatasetRef(slug=slug, version=2)

    def delete_kernel(self, ref: str) -> None:
        """Record the deletion; nothing to delete remotely."""
        self.calls.append("delete_kernel")


@pytest.fixture
def stub_client(monkeypatch: pytest.MonkeyPatch):
    """Install a :class:`StubClient` and hand it back for inspection."""
    created: list[StubClient] = []

    def install(**kwargs: Any) -> StubClient:
        client = StubClient(**kwargs)
        created.append(client)
        monkeypatch.setattr(train, "KaggleClient", lambda *a, **k: client)
        return client

    return install


@pytest.fixture
def no_local_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report a CPU-only local interpreter without importing torch."""
    monkeypatch.setattr(
        train,
        "_probe_accelerator",
        lambda interpreter: {"torch": True, "torch_version": "2.14.1+cpu", "cuda": False},
    )
    monkeypatch.setattr(train, "_torch_interpreter", lambda: Path("python"))


def _write_probe(pipeline: train.Pipeline, probe: dict[str, Any]) -> None:
    pipeline.remote_dir.mkdir(parents=True, exist_ok=True)
    (pipeline.remote_dir / GPU_PROBE_FILENAME).write_text(json.dumps(probe), encoding="utf-8")


# ---------------------------------------------------------------------------
# The probe notebook
# ---------------------------------------------------------------------------


def test_the_probe_notebook_is_a_valid_notebook():
    nbformat = pytest.importorskip("nbformat", reason="nbformat is not a backend dependency")

    notebook = nbformat.reads(render_gpu_probe_notebook(run_id="probe-1"), as_version=4)
    nbformat.validate(notebook)


def test_the_probe_notebook_asks_the_machine_four_questions():
    """A device node, nvidia-smi, torch's own answer, and DNS."""
    rendered = render_gpu_probe_notebook(run_id="probe-1")
    assert "/dev/nvidia*" in rendered
    assert "nvidia-smi" in rendered
    assert "torch.cuda.is_available()" in rendered
    assert "gethostbyname" in rendered


def test_the_probe_notebook_writes_the_file_the_verifier_reads():
    """The filename is a contract between two modules; a rename must not pass."""
    rendered = render_gpu_probe_notebook(run_id="probe-1")
    assert GPU_PROBE_FILENAME in rendered
    assert "/kaggle/working" in rendered


def test_the_probe_notebook_installs_nothing():
    """It must run on a session with no network, so it may not pip install."""
    assert "pip install" not in render_gpu_probe_notebook(run_id="probe-1")


def test_the_probe_notebook_is_deterministic():
    assert render_gpu_probe_notebook(run_id="probe-1") == render_gpu_probe_notebook(
        run_id="probe-1"
    )


# ---------------------------------------------------------------------------
# probe-remote
# ---------------------------------------------------------------------------


def test_probe_remote_records_what_the_kernel_actually_found(tmp_path, stub_client, no_local_gpu):
    stub_client()
    pipeline = _pipeline(tmp_path)

    outcome = train.stage_probe_remote(pipeline)

    assert outcome.status is train.StageStatus.PASSED
    assert "v7 completed" in outcome.detail
    record = json.loads((pipeline.qwen_dir / "gpu_probe_run.json").read_text(encoding="utf-8"))
    assert record["kernel_ref"] == "example-owner/nexo-phase10-x"
    assert record["requested"]["enable_gpu"] is True


def test_probe_remote_distinguishes_a_requested_gpu_from_a_present_one(
    tmp_path, stub_client, no_local_gpu
):
    """The whole point: metadata says GPU, the machine says otherwise."""
    stub_client()
    pipeline = _pipeline(tmp_path)
    _write_probe(pipeline, CPU_PROBE)

    outcome = train.stage_probe_remote(pipeline)

    record = json.loads((pipeline.qwen_dir / "gpu_probe_run.json").read_text(encoding="utf-8"))
    assert record["gpu_available"] is False
    assert record["observed"]["torch_cuda_available"] is False
    assert "no /dev/nvidia" in outcome.detail


def test_probe_remote_accepts_a_kernel_that_reports_a_device(tmp_path, stub_client, no_local_gpu):
    stub_client()
    pipeline = _pipeline(tmp_path)
    _write_probe(pipeline, GPU_PROBE)

    train.stage_probe_remote(pipeline)

    record = json.loads((pipeline.qwen_dir / "gpu_probe_run.json").read_text(encoding="utf-8"))
    assert record["gpu_available"] is True


def test_probe_remote_fails_rather_than_claims_success_when_the_kernel_errors(
    tmp_path, stub_client, no_local_gpu
):
    stub_client(state="ERROR")
    pipeline = _pipeline(tmp_path)

    outcome = train.stage_probe_remote(pipeline)

    assert outcome.status is train.StageStatus.FAILED
    assert not (pipeline.qwen_dir / "gpu_probe_run.json").exists()


def test_probe_remote_is_blocked_not_failed_when_the_cli_is_missing(
    tmp_path, monkeypatch, no_local_gpu
):
    def unusable(self) -> KaggleEnvironment:
        raise RemoteError(command="kaggle --version", returncode=None, stderr="not found")

    monkeypatch.setattr(train.KaggleClient, "probe", unusable)
    pipeline = _pipeline(tmp_path)

    outcome = train.stage_probe_remote(pipeline)

    assert outcome.status is train.StageStatus.BLOCKED


def test_probe_remote_never_pushes_when_the_account_is_unknown(tmp_path, monkeypatch, no_local_gpu):
    monkeypatch.setattr(
        train.KaggleClient,
        "probe",
        lambda self: KaggleEnvironment("", None, None, False),
    )
    pipeline = _pipeline(tmp_path)

    outcome = train.stage_probe_remote(pipeline)

    assert outcome.status is train.StageStatus.BLOCKED
    assert "Authenticate" in outcome.detail


# ---------------------------------------------------------------------------
# train-qwen
# ---------------------------------------------------------------------------


def test_train_qwen_is_blocked_and_says_how_much_vram_it_needs(tmp_path, stub_client, no_local_gpu):
    stub_client()
    pipeline = _pipeline(tmp_path)
    _write_probe(pipeline, CPU_PROBE)

    outcome = train.stage_train_qwen(pipeline)

    assert outcome.status is train.StageStatus.BLOCKED
    assert "GiB" in outcome.detail
    assert "Qwen/Qwen3-8B" in outcome.detail


def test_train_qwen_records_the_blocked_verdict_rather_than_only_printing_it(
    tmp_path, stub_client, no_local_gpu
):
    stub_client()
    pipeline = _pipeline(tmp_path)
    _write_probe(pipeline, CPU_PROBE)

    train.stage_train_qwen(pipeline)

    record = json.loads((pipeline.qwen_dir / "qwen_run.json").read_text(encoding="utf-8"))
    assert record["status"] == "BLOCKED"
    assert record["base_model"] == "Qwen/Qwen3-8B"
    assert record["required_vram_bytes"] > 0
    assert record["remote_probe"]["device_nodes"] == []


def test_a_blocked_run_still_writes_the_notebook_and_the_bundle(
    tmp_path, stub_client, no_local_gpu
):
    """The whole design: blocked means one push away, not start over."""
    stub_client()
    pipeline = _pipeline(tmp_path)
    _write_probe(pipeline, CPU_PROBE)

    train.stage_train_qwen(pipeline)

    notebook = pipeline.qwen_dir / "segment-000" / "qwen_training_notebook.ipynb"
    assert notebook.is_file()
    assert (pipeline.qwen_dir / "segment-000" / "input").is_dir()
    rendered = json.loads(notebook.read_text(encoding="utf-8"))
    assert rendered["metadata"]["kaggle"]["enable_gpu"] is True


def test_train_qwen_pushes_when_a_probe_verifies_a_remote_accelerator(
    tmp_path, stub_client, monkeypatch
):
    client = stub_client()
    monkeypatch.setattr(
        train,
        "_probe_accelerator",
        lambda interpreter: {"torch": True, "torch_version": "2.14.1+cpu", "cuda": False},
    )
    monkeypatch.setattr(train, "_torch_interpreter", lambda: Path("python"))
    pipeline = _pipeline(tmp_path)
    _write_probe(pipeline, GPU_PROBE)
    _seed_dataset(pipeline)

    outcome = train.stage_train_qwen(pipeline)

    assert outcome.status is train.StageStatus.PASSED
    assert "create_or_version_dataset" in client.calls
    assert "push_kernel" in client.calls
    assert "wait_for_kernel" in client.calls


def test_train_qwen_refuses_to_push_an_empty_bundle(tmp_path, stub_client, monkeypatch):
    """Publishing a corpus of nothing would produce a kernel that trains on nothing."""
    client = stub_client()
    monkeypatch.setattr(
        train,
        "_probe_accelerator",
        lambda interpreter: {"torch": True, "torch_version": "2.14.1+cpu", "cuda": False},
    )
    monkeypatch.setattr(train, "_torch_interpreter", lambda: Path("python"))
    pipeline = _pipeline(tmp_path)
    _write_probe(pipeline, GPU_PROBE)

    outcome = train.stage_train_qwen(pipeline)

    assert outcome.status is train.StageStatus.BLOCKED
    assert "push_kernel" not in client.calls
    assert "--prepare" in outcome.detail


def test_train_qwen_fails_when_the_kernel_errors(tmp_path, stub_client, monkeypatch):
    stub_client(state="ERROR")
    monkeypatch.setattr(
        train,
        "_probe_accelerator",
        lambda interpreter: {"torch": True, "torch_version": "2.14.1+cpu", "cuda": False},
    )
    monkeypatch.setattr(train, "_torch_interpreter", lambda: Path("python"))
    pipeline = _pipeline(tmp_path)
    _write_probe(pipeline, GPU_PROBE)
    _seed_dataset(pipeline)

    outcome = train.stage_train_qwen(pipeline)

    assert outcome.status is train.StageStatus.FAILED
    record = json.loads((pipeline.qwen_dir / "qwen_run.json").read_text(encoding="utf-8"))
    assert record["kernel_state"] == "ERROR"


def _seed_splits(pipeline: train.Pipeline) -> None:
    """Write one row per prepared split, as ``--prepare`` would."""
    for split in ("train", "validation", "test"):
        path = pipeline.datasets_dir / f"qwen_{split}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"instruction": "x"}\n', encoding="utf-8")


def _bundle(tmp_path: Path) -> train.Pipeline:
    """A pipeline whose bundle directory holds one publishable row."""
    pipeline = _pipeline(tmp_path)
    bundle = pipeline.qwen_dir / "segment-000" / "input"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "qwen_train.jsonl").write_text('{"instruction": "x"}\n', encoding="utf-8")
    return pipeline


def _seed_dataset(pipeline: train.Pipeline) -> None:
    """Write one row per split so the publish step has something to publish."""
    _seed_splits(pipeline)
    input_dir = pipeline.qwen_dir / "segment-000" / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    for split in ("train", "validation"):
        (input_dir / f"qwen_{split}.jsonl").write_text('{"instruction": "x"}\n', encoding="utf-8")


# ---------------------------------------------------------------------------
# eval-qwen
# ---------------------------------------------------------------------------


def test_eval_qwen_refuses_to_compare_the_base_model_against_itself(tmp_path, stub_client):
    """The one comparison that must never be published as a fine-tuning result."""
    stub_client()
    pipeline = _pipeline(tmp_path)

    outcome = train.stage_eval_qwen(pipeline)

    assert outcome.status is train.StageStatus.BLOCKED
    assert "base-versus-base" in outcome.detail


def test_eval_qwen_is_blocked_when_an_adapter_exists_but_no_accelerator_does(tmp_path, stub_client):
    stub_client()
    pipeline = _pipeline(tmp_path)
    _write_adapter(pipeline, "nexo_qwen_adapter")
    _write_probe(pipeline, CPU_PROBE)

    outcome = train.stage_eval_qwen(pipeline)

    assert outcome.status is train.StageStatus.BLOCKED
    assert "nexo_qwen_adapter" in outcome.detail


def test_eval_qwen_pushes_the_paired_comparison_when_it_can(tmp_path, stub_client):
    client = stub_client()
    pipeline = _pipeline(tmp_path)
    _write_adapter(pipeline, "nexo_qwen_adapter")
    _write_probe(pipeline, GPU_PROBE)
    _seed_splits(pipeline)

    outcome = train.stage_eval_qwen(pipeline)

    assert outcome.status is train.StageStatus.PASSED
    assert "push_kernel" in client.calls
    assert (pipeline.qwen_dir / "eval-000" / "qwen_eval.ipynb").is_file()


def test_eval_qwen_attaches_the_dataset_its_notebook_resolves_from(tmp_path, stub_client):
    """A kernel pushed with no dataset_sources fails inside the notebook.

    That failure lands long after the push was reported as accepted, which is
    exactly the class of problem the stage's own assertions prevent.
    """
    stub_client()
    pipeline = _pipeline(tmp_path)
    _write_adapter(pipeline, "nexo_qwen_adapter")
    _write_probe(pipeline, GPU_PROBE)
    _seed_splits(pipeline)

    train.stage_eval_qwen(pipeline)

    workdir = next((pipeline.remote_dir).glob("qwen-eval-*"))
    metadata = json.loads((workdir / "kernel-metadata.json").read_text(encoding="utf-8"))
    assert metadata["dataset_sources"], "the eval notebook resolves its split from a mount"
    assert (pipeline.qwen_dir / "eval-000" / "input" / "nexo_qwen_adapter").is_dir()


def test_the_eval_bundle_is_a_different_dataset_from_the_training_one(tmp_path):
    """Otherwise the eval mount silently becomes revision N of the training data."""
    client = StubClient()
    pipeline = _bundle(tmp_path)
    bundle = pipeline.qwen_dir / "segment-000" / "input"

    train_slug = train._publish_qwen_dataset(client, pipeline, bundle, purpose="train")
    eval_slug = train._publish_qwen_dataset(client, pipeline, bundle, purpose="eval")

    assert train_slug != eval_slug


def _write_adapter(pipeline: train.Pipeline, name: str) -> None:
    adapter = pipeline.qwen_dir / name
    adapter.mkdir(parents=True, exist_ok=True)
    (adapter / "adapter_config.json").write_text('{"r": 16}\n', encoding="utf-8")


def test_the_adapter_is_found_wherever_a_segment_left_it(tmp_path):
    pipeline = _pipeline(tmp_path)
    _write_adapter(pipeline, "deep-nested-name")

    assert train._adapter_dir_name(pipeline) == "deep-nested-name"


def test_no_adapter_anywhere_reports_none(tmp_path):
    pipeline = _pipeline(tmp_path)

    assert train._adapter_dir_name(pipeline) is None


# ---------------------------------------------------------------------------
# Stage selection and the artifact-directory seam
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flag", "expected"),
    [
        ("--prepare", "prepare"),
        ("--train-small", "train-small"),
        ("--evaluate", "evaluate"),
        ("--train-qwen", "train-qwen"),
        ("--probe-remote", "probe-remote"),
        ("--eval-qwen", "eval-qwen"),
        ("--qwen-status", "qwen-status"),
    ],
)
def test_every_stage_is_reachable_from_the_command_line(flag, expected):
    args = train.build_parser().parse_args([flag])
    assert expected in {stage.name for stage in train.selected_stages(args)}


def test_stage_flags_select_a_set_not_a_sequence():
    """Ask for two stages and get both, in pipeline order, not in flag order."""
    args = train.build_parser().parse_args(["--evaluate", "--prepare"])

    assert [stage.name for stage in train.selected_stages(args)] == ["prepare", "evaluate"]


def test_no_flag_runs_every_stage():
    names = [stage.name for stage in train.selected_stages(train.build_parser().parse_args([]))]

    assert names == [stage.name for stage in train.STAGES]


def test_the_remote_artifact_directory_is_scoped_and_restored(tmp_path, monkeypatch):
    """Two runs in one process must not read each other's probe."""
    monkeypatch.delenv(train.REMOTE_ARTIFACT_ENV, raising=False)
    with train._remote_artifacts_at(tmp_path / "a"):
        assert os.environ[train.REMOTE_ARTIFACT_ENV] == str(tmp_path / "a")
        with train._remote_artifacts_at(tmp_path / "b"):
            assert os.environ[train.REMOTE_ARTIFACT_ENV] == str(tmp_path / "b")
        assert os.environ[train.REMOTE_ARTIFACT_ENV] == str(tmp_path / "a")
    assert train.REMOTE_ARTIFACT_ENV not in os.environ


def test_an_exported_remote_directory_survives_the_scope(tmp_path, monkeypatch):
    monkeypatch.setenv(train.REMOTE_ARTIFACT_ENV, "/somewhere/else")
    with train._remote_artifacts_at(tmp_path):
        pass

    assert os.environ[train.REMOTE_ARTIFACT_ENV] == "/somewhere/else"


def test_the_verdict_is_not_available_without_a_probe_on_disk(tmp_path):
    pipeline = _pipeline(tmp_path)
    pipeline.remote_dir.mkdir(parents=True, exist_ok=True)

    available, reason = train._remote_gpu_verdict(pipeline)

    assert available is False
    assert "not yet probed" in reason


def test_a_configured_pipeline_can_load_the_two_model_configs():
    """Both halves read one directory; a missing half must fail loudly."""
    config = load_config(CONFIG_DIR)

    assert config.small_model.num_labels == 14
    assert config.qwen.base_model == "Qwen/Qwen3-8B"
    assert (CONFIG_DIR / "small_model.toml").is_file()
