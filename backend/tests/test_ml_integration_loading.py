"""Loading the Phase 10 checkpoint into the Phase 11 process, and every way it fails.

Phase 11's whole serving story rests on four claims that are only as true as the
code that loads the model, and this module pins all four:

* **the label contract.** ``config.json``'s ``id2label`` is the map from a class
  index to the intent name the rest of NEXUS looks up. A silent reorder does not
  raise — it routes ``schedule_plan`` utterances into the knowledge service with
  a confident-looking 0.99 and nothing anywhere reports an error. So the loader
  compares the checkpoint's labels against
  :func:`ml.datasets.routing.label_map` index by index and refuses a load that
  disagrees, and these tests prove that check bites by feeding it deliberately
  corrupted checkpoints.
* **diagnosable failure.** A missing, unreadable or half-copied checkpoint has to
  read as *the checkpoint is missing model.safetensors*, not as a tokenizer
  mmap error three frames deeper. One test per required file name keeps the error
  message from quietly becoming a class name.
* **the lazy import.** ``torch`` is a multi-hundred-megabyte dependency that only
  a minority of deployments need. A module-scope ``import torch`` would turn
  "this deployment has no classifier" into "this application will not start", so
  the property is measured in a clean subprocess rather than in the interpreter
  the test session has already polluted.
* **explicit degradation.** A fresh clone has never run Phase 10 — the checkpoint
  is gitignored — and that is a supported state, not a broken install. The app
  boots, every non-ML route works, and the ML endpoints answer 503 with a
  machine-readable reason.

Everything that needs the 703 MiB of weights is marked ``ml_model`` and skips
with a reason on a checkout that has not run Phase 10. Everything else runs
offline, without torch, which is why the policy tests use a stub classifier: the
point of those is the decision, not the encoder.
"""

from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from app.core.config import BACKEND_ROOT, DEFAULT_MODEL_PATH
from app.ml import model_loader
from app.ml.exceptions import MLUnavailableError, ModelCheckpointError, ModelRuntimeError
from app.ml.model_loader import (
    DEFAULT_BASE_MODEL,
    MAX_SEQUENCE_LENGTH,
    REQUIRED_CHECKPOINT_FILES,
    SUPPORTED_DEVICE_REQUESTS,
    LoadedModel,
    default_checkpoint_dir,
    resolve_checkpoint_dir,
    resolve_device,
)
from app.ml.runtime import MLRuntime, MLRuntimeStatus, get_ml_runtime, reset_ml_runtime
from ml.datasets.routing import label_map
from ml.datasets.taxonomy import INTENT_NAMES, TAXONOMY_VERSION, Intent

#: Where Phase 10 wrote the trained checkpoint. ``backend/ml/artifacts`` is
#: gitignored, so this is absent on any checkout that has not run training — every
#: test that needs it says so and skips rather than failing.
CHECKPOINT_DIR = BACKEND_ROOT / "ml" / "artifacts" / "small-model" / "final"

#: The Phase 10 run leaves these beside ``final/``; ``training_state.json`` is what
#: the loader prefers over its own constants for the trained sequence length.
TRAINING_STATE_PATH = CHECKPOINT_DIR.parent / "training_state.json"

#: The head this checkpoint was built with. It counts the classification head's
#: extra 14-way projection on top of the 183M encoder, so it is 184,432,910 and
#: not a round number — a change here means the checkpoint on disk is not the one
#: the training run measured, and every accuracy figure quoted for Phase 11 is
#: about a different model.
EXPECTED_PARAMETER_COUNT = 184_432_910

#: The label order Phase 10 trained against, written out here so the taxonomy is
#: checked against a pinned fact as well as against itself. Reordering this tuple
#: is the single change that would misroute every request while every test still
#: passed, because both the loader and the tests read the taxonomy.
EXPECTED_CLASS_ORDER: tuple[str, ...] = (
    "task_manage",
    "project_manage",
    "schedule_plan",
    "knowledge_capture",
    "knowledge_lookup",
    "analytics_insight",
    "risk_query",
    "developer_intel",
    "learning_track",
    "career_track",
    "account_admin",
    "code_assist",
    "deep_reasoning",
    "out_of_scope",
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _checkpoint_is_present() -> bool:
    return all((CHECKPOINT_DIR / name).is_file() for name in REQUIRED_CHECKPOINT_FILES)


def _skip_without_checkpoint() -> None:
    if not CHECKPOINT_DIR.is_dir():
        pytest.skip(
            f"no Phase 10 checkpoint at {CHECKPOINT_DIR}; backend/ml/artifacts is "
            "gitignored, so run `python -m ml.train` to produce it"
        )
    if not _checkpoint_is_present():
        missing = [
            name for name in REQUIRED_CHECKPOINT_FILES if not (CHECKPOINT_DIR / name).is_file()
        ]
        pytest.skip(f"the Phase 10 checkpoint at {CHECKPOINT_DIR} is missing {missing}")


@pytest.fixture(scope="session")
def torch_module() -> types.ModuleType:
    """The real torch, or a skip.

    Imported once per session because the import alone costs seconds and the
    device tests are the only ones that need it.
    """
    return pytest.importorskip("torch", reason="torch is not installed in this interpreter")


@pytest.fixture(scope="session")
def loaded_model() -> LoadedModel:
    """The real checkpoint, loaded on the CPU, once per session.

    Session-scoped because :func:`app.ml.model_loader.load_model` reads 703 MiB.
    A function-scoped fixture would pay that on every test and turn a five second
    suite into a two minute one; a module-level load would make collection depend
    on the checkpoint being present.
    """
    _skip_without_checkpoint()
    pytest.importorskip("torch", reason="torch is not installed in this interpreter")
    pytest.importorskip("transformers", reason="transformers is not installed")
    # "cpu" rather than "auto" so the assertions below are deterministic on a
    # workstation that happens to have a GPU; the "auto" policy is covered by its
    # own tests against the real torch and against a stub that reports CUDA.
    return model_loader.load_model(CHECKPOINT_DIR, device="cpu")


@pytest.fixture(autouse=True)
def _isolated_runtime_singleton():
    """Tear the process-wide runtime down around every test in this module."""
    reset_ml_runtime()
    try:
        yield
    finally:
        reset_ml_runtime()


def _fake_torch(*, cuda_available: bool) -> types.ModuleType:
    """A stand-in for the torch module, exposing only what ``resolve_device`` reads.

    Lets the CUDA-available branch be exercised on a CPU-only build without a
    GPU, which is the only way this suite can run everywhere.
    """
    return types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: cuda_available),
    )


def _write_checkpoint(
    directory: Path,
    *,
    id2label: dict[str, str] | None = None,
    label_map_json: dict[str, Any] | str | None = None,
    config_text: str | None = None,
) -> Path:
    """Build a checkpoint directory whose label files are real and whose bytes are not.

    Deliberately *not* a copy of the trained artifact: every test that uses this
    is about what happens before 703 MiB of weights are read, so the fake has to
    fail (or, for the positive control, get past) the loader's cheap checks
    without dragging the real encoder into ``tmp_path``.
    """
    labels = dict(id2label or {str(i): name for i, name in enumerate(INTENT_NAMES)})
    directory.mkdir(parents=True, exist_ok=True)
    if config_text is None:
        config_text = json.dumps(
            {
                "architectures": ["DebertaV2ForSequenceClassification"],
                "model_type": "deberta-v2",
                "id2label": labels,
            }
        )
    (directory / "config.json").write_text(config_text, encoding="utf-8")

    if label_map_json is None:
        sidecar: Any = {"id2label": labels}
    else:
        sidecar = label_map_json
    sidecar_text = sidecar if isinstance(sidecar, str) else json.dumps(sidecar)
    (directory / "label_map.json").write_text(sidecar_text, encoding="utf-8")

    # Present but empty: enough for resolve_checkpoint_dir, useless to
    # transformers, which is exactly what the "gets past validation" control wants.
    (directory / "tokenizer.json").write_text("{}", encoding="utf-8")
    (directory / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (directory / "model.safetensors").write_bytes(b"")
    return directory


class _StubClassifier:
    """An :class:`~app.ml.classifier.IntentClassifier` stand-in for lifecycle tests.

    Records ``close()`` so the runtime's release can be asserted without loading
    anything: the behaviour under test is *when the runtime drops the reference*,
    not what the reference does with it.
    """

    def __init__(self, device: str = "cpu") -> None:
        self.identity = types.SimpleNamespace(device=device)
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def _complete_directory(directory: Path) -> Path:
    """A directory holding every required file, with contents nobody reads."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_CHECKPOINT_FILES:
        (directory / name).write_bytes(b"placeholder")
    return directory


# ---------------------------------------------------------------------------
# Checkpoint resolution
# ---------------------------------------------------------------------------


def test_the_default_checkpoint_directory_is_built_from_the_loader_s_own_location():
    """It is resolved from ``__file__``, so a checkout moved to another drive works.

    The negative half matters as much: a module-level literal like
    ``Path("E:/Nexo/backend/ml/...")`` would satisfy "is an absolute path" while
    breaking every other machine, so the assertion is that the constant equals a
    path recomputed from this very file.
    """
    module_root = Path(model_loader.__file__).resolve().parents[2]

    assert default_checkpoint_dir() == module_root / "ml" / "artifacts" / "small-model" / "final"
    assert default_checkpoint_dir().is_absolute()
    assert module_root == BACKEND_ROOT


def test_the_default_checkpoint_directory_is_the_one_settings_resolve_to(make_settings):
    """Two answers to "where would we load from" is how a deployment loads the wrong one.

    ``app.core.config`` and ``app.ml.model_loader`` both hold a default. If they
    drifted, the runtime would report a checkpoint that the loader never opens.
    """
    settings = make_settings(ML_MODEL_PATH="")

    assert settings.ml_resolved_model_path == default_checkpoint_dir()
    assert default_checkpoint_dir() == DEFAULT_MODEL_PATH


def test_a_configured_relative_model_path_resolves_against_the_working_directory():
    """An operator who wrote a relative path meant it relative to the process.

    Only the *default* is anchored to the repository, so a unit file can point at
    a mounted volume without knowing where the checkout lives.
    """
    from app.core.config import Settings

    settings = Settings(ml_model_path=str(Path("ml") / "artifacts" / "elsewhere"))

    assert settings.ml_resolved_model_path == (Path.cwd() / "ml" / "artifacts" / "elsewhere")
    assert settings.ml_resolved_model_path != default_checkpoint_dir()


@pytest.mark.ml_model
def test_a_present_checkpoint_resolves_to_its_own_resolved_path(loaded_model):
    resolved = resolve_checkpoint_dir(CHECKPOINT_DIR)

    assert resolved == CHECKPOINT_DIR.resolve()
    assert resolved == default_checkpoint_dir().resolve()
    assert resolved == Path(loaded_model.identity.checkpoint)


def test_a_checkpoint_directory_that_does_not_exist_is_refused(tmp_path):
    absent = tmp_path / "small-model" / "final"

    with pytest.raises(ModelCheckpointError, match="checkpoint directory not found"):
        resolve_checkpoint_dir(absent)

    assert not absent.exists(), "the control must be a path that genuinely is absent"


def test_a_checkpoint_path_that_is_a_file_is_refused(tmp_path):
    """A misconfigured path pointing at ``model.safetensors`` must not be walked."""
    target = tmp_path / "final"
    target.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ModelCheckpointError, match="checkpoint path is not a directory"):
        resolve_checkpoint_dir(target)


def test_a_complete_checkpoint_directory_resolves(tmp_path):
    """The positive control for the missing-file tests below.

    If this ever fails, the per-file tests would pass for the wrong reason —
    every directory they build might be rejected as incomplete regardless of
    which file was taken out.
    """
    complete = _complete_directory(tmp_path / "final")

    assert resolve_checkpoint_dir(complete) == complete.resolve()
    assert len(REQUIRED_CHECKPOINT_FILES) == 5


@pytest.mark.parametrize("missing_name", REQUIRED_CHECKPOINT_FILES, ids=REQUIRED_CHECKPOINT_FILES)
def test_a_checkpoint_missing_one_required_file_names_that_file(tmp_path, missing_name):
    """The message is the operator's runbook: which file is missing, by name.

    Asserting only the exception class would pass for a loader that raised
    ``ModelCheckpointError("nope")``, which tells an operator nothing.
    """
    directory = tmp_path / "final"
    _complete_directory(directory)
    (directory / missing_name).unlink()

    with pytest.raises(ModelCheckpointError, match=f"checkpoint is missing {missing_name}"):
        resolve_checkpoint_dir(directory)

    assert not (directory / missing_name).exists()


def test_an_empty_directory_is_reported_as_missing_its_first_required_file(tmp_path):
    empty = tmp_path / "final"
    empty.mkdir()

    with pytest.raises(
        ModelCheckpointError, match=f"checkpoint is missing {REQUIRED_CHECKPOINT_FILES[0]}"
    ):
        resolve_checkpoint_dir(empty)


# ---------------------------------------------------------------------------
# Corrupted and incompatible checkpoints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", ["config.json", "label_map.json"])
def test_a_checkpoint_whose_json_does_not_parse_is_refused_by_name(tmp_path, filename):
    """Truncated writes are the common corruption; the message must name the file.

    Both files are covered because they are read by different calls, and a fix
    that wraps only one of them would leave the other reporting a bare
    ``JSONDecodeError`` from three frames down.
    """
    directory = _write_checkpoint(tmp_path / "final")
    (directory / filename).write_text("{not json at all", encoding="utf-8")

    with pytest.raises(ModelCheckpointError, match=f"checkpoint file {filename} is not valid JSON"):
        model_loader.load_model(directory)


def test_a_checkpoint_whose_json_is_not_an_object_is_refused(tmp_path):
    directory = _write_checkpoint(tmp_path / "final")
    (directory / "config.json").write_text('["task_manage"]', encoding="utf-8")

    with pytest.raises(
        ModelCheckpointError, match=r"checkpoint file config\.json is not a JSON object"
    ):
        model_loader.load_model(directory)


def test_a_checkpoint_whose_labels_are_reordered_against_the_taxonomy_is_refused(tmp_path):
    """The silent-reorder guard. Swapping two adjacent classes is the realistic bug.

    Nothing else about the checkpoint is wrong: it parses, it is complete, and its
    ``label_map.json`` agrees with its own ``config.json``. Only the comparison
    against the taxonomy can catch it, and if it fails to, every request lands on
    a service chosen by a coin.
    """
    reordered = list(INTENT_NAMES)
    reordered[3], reordered[4] = reordered[4], reordered[3]
    directory = _write_checkpoint(
        tmp_path / "final",
        id2label={str(i): name for i, name in enumerate(reordered)},
    )

    with pytest.raises(ModelCheckpointError) as caught:
        model_loader.load_model(directory)

    message = str(caught.value)
    assert "checkpoint class 3 is labelled 'knowledge_lookup'" in message
    assert "but the intent taxonomy expects 'knowledge_capture'" in message
    assert "trained against a different label set" in message


def test_a_checkpoint_whose_class_was_renamed_is_refused(tmp_path):
    """A name Phase 10 never emitted routes to nothing even when the index is right."""
    labels = {str(index): name for index, name in enumerate(INTENT_NAMES)}
    labels["9"] = "professional_development"
    directory = _write_checkpoint(tmp_path / "final", id2label=labels)

    with pytest.raises(ModelCheckpointError) as caught:
        model_loader.load_model(directory)

    message = str(caught.value)
    assert "checkpoint class 9 is labelled 'professional_development'" in message
    assert "expects 'career_track'" in message


def test_a_checkpoint_with_one_class_too_few_is_refused(tmp_path):
    """Thirteen classes against a fourteen-class taxonomy is not a near miss."""
    labels = {str(i): name for i, name in enumerate(INTENT_NAMES[:13])}
    directory = _write_checkpoint(tmp_path / "final", id2label=labels)

    with pytest.raises(ModelCheckpointError) as caught:
        model_loader.load_model(directory)

    message = str(caught.value)
    assert "checkpoint class 13 is labelled None" in message
    assert "expects 'out_of_scope'" in message


def test_a_checkpoint_whose_label_map_sidecar_disagrees_with_its_config_is_refused(tmp_path):
    """Two files describing two label maps means at least one of them is wrong.

    This is the cross-check *within* the checkpoint, and it is the only thing
    standing between a hand-edited ``label_map.json`` and a loader that trusts
    whichever file it read first.
    """
    config_labels = {str(i): name for i, name in enumerate(INTENT_NAMES)}
    drifted = dict(config_labels)
    drifted["2"] = "project_manage"
    directory = _write_checkpoint(
        tmp_path / "final",
        id2label=config_labels,
        label_map_json={"id2label": drifted},
    )

    with pytest.raises(
        ModelCheckpointError,
        match=r"label_map\.json and config\.json describe different label maps",
    ):
        model_loader.load_model(directory)


def test_a_checkpoint_whose_config_declares_no_id2label_is_refused(tmp_path):
    """Transformers can save a config with the section absent; there is no default."""
    directory = _write_checkpoint(
        tmp_path / "final",
        config_text=json.dumps({"architectures": ["DebertaV2ForSequenceClassification"]}),
    )

    with pytest.raises(ModelCheckpointError, match=r"config\.json id2label is missing"):
        model_loader.load_model(directory)


def test_a_checkpoint_whose_class_key_is_not_an_index_is_refused(tmp_path):
    """``LABEL_0`` instead of ``0``: JSON keys are strings and the loader says so."""
    directory = _write_checkpoint(
        tmp_path / "final",
        config_text=json.dumps({"id2label": {"LABEL_0": "task_manage"}}),
    )

    with pytest.raises(ModelCheckpointError, match="has a class key that is not an index"):
        model_loader.load_model(directory)


def test_a_correctly_labelled_fake_checkpoint_is_refused_only_at_the_tokenizer(
    tmp_path, torch_module
):
    """The non-vacuity control for every refusal above.

    If this raised a label error, then the fake directory itself was malformed
    and the reorder/rename tests would have been proving that their *fixture* was
    broken rather than that the check works. Getting as far as the tokenizer says
    the label contract was validated and passed.
    """
    assert torch_module.__version__  # the control only means something with torch present
    directory = _write_checkpoint(tmp_path / "final")

    with pytest.raises(ModelCheckpointError, match="tokenizer could not be loaded"):
        model_loader.load_model(directory)


def test_the_label_validation_failure_does_not_leak_the_checkpoint_path(tmp_path):
    """The path is deployment information; a client-facing message must not carry it.

    ``ModelCheckpointError`` renders through the shared error envelope, and
    ``ml_resolved_model_path`` is a server filesystem layout.
    """
    directory = _write_checkpoint(tmp_path / "some-deployment-specific-name")
    with pytest.raises(ModelCheckpointError) as caught:
        model_loader.load_model(directory)

    message = str(caught.value)
    assert "some-deployment-specific-name" not in message
    assert str(tmp_path) not in message


# ---------------------------------------------------------------------------
# Label mapping, against the real checkpoint
# ---------------------------------------------------------------------------


@pytest.mark.ml_model
def test_the_loaded_model_has_one_class_per_taxonomy_intent(loaded_model):
    assert len(loaded_model.id2label) == 14 == len(INTENT_NAMES)
    assert len(set(loaded_model.id2label)) == 14


@pytest.mark.ml_model
@pytest.mark.parametrize("intent", INTENT_NAMES, ids=INTENT_NAMES)
def test_every_class_index_resolves_to_the_intent_phase_10_trained(loaded_model, intent):
    """``id2label[i] == label_map()[intent]``, checked one intent at a time.

    Parametrised so a single reorder is reported as one failing intent rather than
    as one failing tuple fifteen lines long.
    """
    index = label_map()[intent]

    assert loaded_model.id2label[index] == intent
    assert index == EXPECTED_CLASS_ORDER.index(intent)


@pytest.mark.ml_model
def test_the_loaded_label_set_is_exactly_the_taxonomy(loaded_model):
    assert set(loaded_model.id2label) == set(INTENT_NAMES)


def test_the_taxonomy_version_the_checkpoint_was_trained_against_has_not_changed():
    """``label_map.json`` and this string are the two facts Phase 10 recorded.

    A bump to ``TAXONOMY_VERSION`` that does not re-run training invalidates every
    accuracy number quoted for Phase 11, so the string is pinned here rather than
    only read.
    """
    assert TAXONOMY_VERSION == "nexo_intents.v1"
    assert len(Intent) == 14
    assert tuple(str(intent) for intent in Intent) == EXPECTED_CLASS_ORDER
    assert tuple(INTENT_NAMES) == EXPECTED_CLASS_ORDER


@pytest.mark.ml_model
def test_the_loaded_checkpoint_and_the_taxonomy_agree_on_class_order(loaded_model):
    """A positive control read off the real artifact rather than off a fixture.

    The other label tests would all still pass against a taxonomy the checkpoint
    happens to match because both were edited together; this compares the loaded
    tuple to the pinned order above, which nothing in the repository controls.
    """
    assert loaded_model.id2label == EXPECTED_CLASS_ORDER
    assert {index: name for name, index in label_map().items()} == dict(
        enumerate(loaded_model.id2label)
    )


# ---------------------------------------------------------------------------
# The loaded model is an inference-only object
# ---------------------------------------------------------------------------


@pytest.mark.ml_model
def test_the_loaded_model_is_in_evaluation_mode(loaded_model):
    assert loaded_model.model.training is False


@pytest.mark.ml_model
def test_no_loaded_parameter_can_accumulate_a_gradient(loaded_model):
    trainable = [
        name for name, parameter in loaded_model.model.named_parameters() if parameter.requires_grad
    ]

    assert trainable == []
    assert list(loaded_model.model.parameters()), "the model must actually have parameters"


@pytest.mark.ml_model
def test_the_loaded_model_has_the_parameter_count_the_training_run_recorded(loaded_model):
    total = sum(parameter.numel() for parameter in loaded_model.model.parameters())

    assert total == EXPECTED_PARAMETER_COUNT
    assert loaded_model.identity.parameter_count == EXPECTED_PARAMETER_COUNT


@pytest.mark.ml_model
def test_the_loaded_model_reports_the_trained_context_length(loaded_model):
    assert MAX_SEQUENCE_LENGTH == 128
    assert loaded_model.max_sequence_length == MAX_SEQUENCE_LENGTH
    assert loaded_model.identity.max_sequence_length == 128


@pytest.mark.ml_model
def test_the_loaded_model_reports_the_encoder_and_device_it_actually_used(loaded_model):
    assert loaded_model.identity.base_model == DEFAULT_BASE_MODEL == "microsoft/deberta-v3-base"
    assert loaded_model.identity.architecture == "DebertaV2ForSequenceClassification"
    assert loaded_model.device == "cpu"
    assert loaded_model.identity.device == "cpu"


@pytest.mark.ml_model
def test_the_loaded_model_reaches_torch_through_the_loader_not_its_own_import(loaded_model):
    """The loader hands the module to the classifier so ``app.ml.classifier`` stays torch-free."""
    import torch

    assert loaded_model.torch_module is torch


@pytest.mark.ml_model
def test_the_loaded_checkpoint_agrees_with_the_training_run_it_came_from(loaded_model):
    """Cross-check the served model against the artifacts Phase 10 left behind.

    Skipped on a checkout without the run's side files: they are optional by
    design, and their absence is not evidence of a defect.
    """
    if not TRAINING_STATE_PATH.is_file():
        pytest.skip(
            f"no training_state.json at {TRAINING_STATE_PATH}; the Phase 10 run did not leave one"
        )
    state = json.loads(TRAINING_STATE_PATH.read_text(encoding="utf-8"))

    assert state["config"]["max_seq_length"] == MAX_SEQUENCE_LENGTH
    assert state["max_seq_length"] == MAX_SEQUENCE_LENGTH
    assert state["parameter_count"] == EXPECTED_PARAMETER_COUNT
    assert state["num_labels"] == len(INTENT_NAMES)
    assert state["base_model"] == loaded_model.identity.base_model
    assert state["label2id"] == label_map()
    assert loaded_model.id2label == tuple(INTENT_NAMES)


# ---------------------------------------------------------------------------
# Device selection
# ---------------------------------------------------------------------------


def test_the_supported_device_requests_are_the_documented_three():
    """The tuple is what :class:`app.core.config.Settings` validates ``ML_DEVICE`` against."""
    from app.core.config import ML_DEVICES

    assert SUPPORTED_DEVICE_REQUESTS == ("auto", "cpu", "cuda")
    assert ML_DEVICES == SUPPORTED_DEVICE_REQUESTS


def test_cpu_is_selected_when_cpu_is_asked_for_even_where_a_gpu_exists():
    """An explicit choice is honoured; ``auto`` is the only thing that adapts."""
    assert resolve_device(_fake_torch(cuda_available=True), "cpu") == "cpu"
    assert resolve_device(_fake_torch(cuda_available=False), "cpu") == "cpu"


def test_auto_selects_the_gpu_when_the_build_reports_one():
    assert resolve_device(_fake_torch(cuda_available=True), "auto") == "cuda"


def test_auto_selects_the_cpu_when_the_build_reports_no_gpu():
    assert resolve_device(_fake_torch(cuda_available=False), "auto") == "cpu"


def test_auto_resolves_to_cpu_on_this_cpu_only_build(torch_module):
    """The documented deployment default, asserted against the interpreter running it.

    Skipped rather than failed on a machine that does have a GPU: ``auto``
    answering ``cuda`` there is correct behaviour, not a regression.
    """
    if torch_module.cuda.is_available():
        pytest.skip("this torch build sees a CUDA device, so 'auto' correctly resolves to it")

    assert resolve_device(torch_module, "auto") == "cpu"


def test_auto_resolves_to_cpu_against_the_real_torch_build(torch_module):
    """The stub-based ``auto`` tests would pass even if the real call site were wrong."""
    expected = "cuda" if torch_module.cuda.is_available() else "cpu"

    assert resolve_device(torch_module, "auto") == expected


@pytest.mark.parametrize("requested", ["cuda", "CUDA", "  cuda  "])
def test_cuda_is_honoured_when_the_build_reports_a_device(requested):
    """Case and padding are tolerated so a unit file cannot fail on whitespace."""
    assert resolve_device(_fake_torch(cuda_available=True), requested) == "cuda"


def test_requesting_cuda_on_a_build_without_one_fails_loudly_rather_than_falling_back(torch_module):
    """A silent CPU fallback hides a capacity problem until the p99 does.

    An operator who asked for a GPU and silently received a CPU has deployed a
    capacity problem nobody will connect to this decision, so the request is an
    error naming the fix.
    """
    if torch_module.cuda.is_available():
        pytest.skip("this build has a CUDA device, so 'cuda' is satisfiable here")

    with pytest.raises(ModelRuntimeError) as caught:
        resolve_device(torch_module, "cuda")

    message = str(caught.value)
    assert "device 'cuda' was requested" in message
    assert "no usable" in message and "CUDA device" in message
    assert "request 'cpu'" in message


@pytest.mark.parametrize(
    "requested",
    ["gpu", "", "   ", "cpu:0", "mps", "CPU0", "auto,cuda"],
    ids=lambda value: value or "empty",
)
def test_an_unrecognised_device_request_is_refused_rather_than_rounded(requested):
    """A typo in deployment configuration is a mistake to report at boot."""
    with pytest.raises(ValueError, match="unsupported device request"):
        resolve_device(_fake_torch(cuda_available=True), requested)


def test_an_unrecognised_device_request_names_the_accepted_values():
    """The error has to tell the operator what to write instead."""
    with pytest.raises(ValueError) as caught:
        resolve_device(_fake_torch(cuda_available=False), "gpu")

    assert "['auto', 'cpu', 'cuda']" in str(caught.value)


# ---------------------------------------------------------------------------
# The lazy torch import
# ---------------------------------------------------------------------------


#: Written in front of every probe's answer so the parent can find it even when
#: torch, transformers or the application have printed something of their own to
#: stdout first. Parsing "whatever the subprocess printed" would make this test a
#: hostage to a third party's logging.
_PROBE_MARKER = "__NEXUS_PROBE__"

_LAZY_IMPORT_PROBE = f"""
import json
import sys

import app.ml
import app.ml.classifier
import app.ml.runtime
import app.main

leaked = sorted(name for name in sys.modules if name.split(".")[0] in {{"torch", "transformers"}})
print({_PROBE_MARKER!r} + json.dumps(leaked))
"""


def _run_probe(source: str) -> Any:
    """Run ``source`` in a clean interpreter rooted at the backend directory.

    The probe prints one marked JSON line; the parent reads that line rather than
    parsing or ``eval``-ing the whole of stdout.
    """
    # shell, and `source` is a literal defined in this file rather than input.
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", source],
        cwd=str(BACKEND_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, f"probe failed:\n{completed.stdout}\n{completed.stderr}"
    answers = [
        line[len(_PROBE_MARKER) :]
        for line in completed.stdout.splitlines()
        if line.startswith(_PROBE_MARKER)
    ]
    assert answers, f"probe printed no marked answer:\n{completed.stdout}\n{completed.stderr}"
    return json.loads(answers[-1])


def test_importing_the_application_does_not_import_torch_or_transformers():
    """The property that lets NEXUS boot and serve every other route without torch.

    Measured in a subprocess: this session has already imported torch for other
    tests, and ``sys.modules`` in-process could never answer the question. The
    probe imports the ML package, the classifier, the runtime and the whole
    application — ``app.main`` pulls in every router that depends on
    ``get_ml_runtime`` — and reports which heavy modules ended up loaded.
    """
    leaked = _run_probe(_LAZY_IMPORT_PROBE)

    assert leaked == [], f"importing the application pulled in {leaked}"


def test_the_probe_would_notice_torch_if_it_were_imported(torch_module):
    """Non-vacuity: the same probe, with torch imported, must report it.

    Without this the assertion above could be satisfied by a probe that never
    works — a typo'd module name, a broken ``cwd``, a subprocess that imports a
    stub — and would then pass forever.
    """
    leaked = _run_probe(
        "import json\n"
        "import sys\n"
        "import torch\n"
        "import transformers\n"
        "import app.ml\n"
        "import app.main\n"
        f"print({_PROBE_MARKER!r} + json.dumps(sorted(\n"
        "    n for n in sys.modules if n.split('.')[0] in {'torch', 'transformers'}\n"
        ")))\n"
    )

    assert "torch" in leaked
    assert "transformers" in leaked


def test_loading_a_model_is_what_actually_imports_torch():
    """The lazy import is not "torch is never imported" — it is "not at import time".

    Recorded here as the boundary the other test measures: the first thing in the
    process that may pull torch in is a call to ``load_model``, and by then the
    application has already booted.
    """
    source = (
        "import json\n"
        "import sys\n"
        "import app.ml.model_loader as loader\n"
        "before = 'torch' in sys.modules\n"
        "try:\n"
        f"    loader.load_model({str(CHECKPOINT_DIR)!r})\n"
        "except Exception:\n"
        "    pass\n"
        f"print({_PROBE_MARKER!r} + json.dumps([before, 'torch' in sys.modules]))\n"
    )
    if not CHECKPOINT_DIR.is_dir():
        pytest.skip(f"no Phase 10 checkpoint at {CHECKPOINT_DIR}")
    pytest.importorskip("torch", reason="torch is not installed in this interpreter")

    before, after = _run_probe(source)

    assert before is False, "importing model_loader alone must not import torch"
    assert after is True, "load_model is where torch is allowed to arrive"


# ---------------------------------------------------------------------------
# Runtime degradation
# ---------------------------------------------------------------------------


def _runtime_settings(tmp_path: Path, **overrides: Any):
    from app.core.config import Settings

    return Settings(ml_model_path=str(tmp_path / "absent"), **overrides)


def test_the_runtime_reasons_are_the_closed_vocabulary_the_api_promises():
    """``reason`` is what a health check branches on and what a 503 body carries.

    Renaming one of these is a breaking change for every caller that string-
    compares, so the values are pinned rather than merely described.
    """
    assert MLRuntime.REASON_UNLOADED == "not_loaded"
    assert MLRuntime.REASON_AVAILABLE == "available"
    assert MLRuntime.REASON_DISABLED == "disabled"
    assert MLRuntime.REASON_CHECKPOINT_MISSING == "checkpoint_missing"
    assert MLRuntime.REASON_RUNTIME_MISSING == "runtime_missing"
    assert MLRuntime.REASON_LOAD_FAILED == "load_failed"
    assert MLRuntime.REASON_STOPPED == "stopped"
    assert (
        len(
            {
                MLRuntime.REASON_UNLOADED,
                MLRuntime.REASON_AVAILABLE,
                MLRuntime.REASON_DISABLED,
                MLRuntime.REASON_CHECKPOINT_MISSING,
                MLRuntime.REASON_RUNTIME_MISSING,
                MLRuntime.REASON_LOAD_FAILED,
                MLRuntime.REASON_STOPPED,
            }
        )
        == 7
    )


def test_a_fresh_runtime_reports_itself_as_not_loaded(tmp_path):
    runtime = MLRuntime(_runtime_settings(tmp_path))

    assert runtime.is_available is False
    assert runtime.classifier is None
    assert runtime.status == MLRuntimeStatus(
        available=False,
        reason=MLRuntime.REASON_UNLOADED,
        checkpoint=str(runtime.settings.ml_resolved_model_path),
        device=runtime.settings.ml_device,
    )


def test_a_runtime_built_around_a_classifier_starts_available(tmp_path):
    """The seam that lets the lifecycle be tested without reading 703 MiB."""
    stub = _StubClassifier()

    runtime = MLRuntime(_runtime_settings(tmp_path), classifier=stub)

    assert runtime.is_available is True
    assert runtime.classifier is stub
    assert runtime.status.reason == MLRuntime.REASON_AVAILABLE
    assert runtime.status.available is True


def test_switching_ml_off_degrades_without_raising(tmp_path):
    """An operator asking for no model must not get a boot failure.

    ``ml_enabled=False`` is a decision, not a failure, so it degrades with the
    ``disabled`` reason even under ``ML_FAIL_FAST``.
    """
    runtime = MLRuntime(_runtime_settings(tmp_path, ml_enabled=False, ml_fail_fast=True))

    status = runtime.load()

    assert status.reason == MLRuntime.REASON_DISABLED
    assert status.available is False
    assert status.detail == "ML integration is switched off (ML_ENABLED=false)."
    assert runtime.is_available is False
    assert runtime.classifier is None


def test_a_missing_checkpoint_degrades_without_raising(tmp_path):
    """The state of every fresh clone: the app boots and the rest of the API works."""
    runtime = MLRuntime(_runtime_settings(tmp_path, ml_fail_fast=False))

    status = runtime.load()

    assert status.reason == MLRuntime.REASON_CHECKPOINT_MISSING
    assert status.available is False
    assert status.checkpoint == str(runtime.settings.ml_resolved_model_path)
    assert "absent" in status.detail, "the detail must name the configured path for the log"
    assert runtime.is_available is False
    assert runtime.classifier is None


def test_the_degraded_status_serialises_every_field_a_health_check_reads(tmp_path):
    runtime = MLRuntime(_runtime_settings(tmp_path, ml_fail_fast=False))
    runtime.load()

    payload = runtime.status.to_dict()

    assert set(payload) == {
        "available",
        "reason",
        "detail",
        "checkpoint",
        "device",
        "load_seconds",
    }
    assert payload["available"] is False
    assert payload["reason"] == MLRuntime.REASON_CHECKPOINT_MISSING
    assert isinstance(payload["load_seconds"], float)


def test_a_missing_checkpoint_raises_under_fail_fast(tmp_path):
    """``ML_FAIL_FAST`` is the switch that turns a quiet 503 into a boot failure.

    The raised message is the generic client-facing one and the diagnosis is on
    ``__cause__``, so both are asserted: the caller learns the class is
    ``ModelCheckpointError`` and the operator's log gets the path that is missing.
    """
    runtime = MLRuntime(_runtime_settings(tmp_path, ml_fail_fast=True))

    with pytest.raises(ModelCheckpointError) as caught:
        runtime.load()

    assert str(caught.value) == "The trained intent classifier checkpoint is unavailable."
    assert isinstance(caught.value.__cause__, ModelCheckpointError)
    assert "no checkpoint directory at" in str(caught.value.__cause__)
    assert runtime.is_available is False


def test_a_missing_torch_build_degrades_with_the_runtime_reason(tmp_path, monkeypatch):
    """Checkpoint errors and runtime errors send an operator to different pages.

    The checkpoint directory is complete here, so the only thing left to fail is
    the runtime — which is exactly the distinction the two reasons encode.
    """
    from app.core.config import Settings

    settings = Settings(ml_model_path=str(tmp_path), ml_fail_fast=False)
    for name in REQUIRED_CHECKPOINT_FILES:
        (tmp_path / name).write_bytes(b"{}")
    assert settings.ml_checkpoint_exists is True

    def _no_torch(self):
        raise ModelRuntimeError("the intent classifier requires 'torch'")

    monkeypatch.setattr(MLRuntime, "_build_classifier", _no_torch)
    runtime = MLRuntime(settings)

    status = runtime.load()

    assert status.reason == MLRuntime.REASON_RUNTIME_MISSING
    assert "torch" in status.detail


def test_a_runtime_missing_raises_under_fail_fast(tmp_path, monkeypatch):
    """The same runtime failure, under ``ML_FAIL_FAST``, is a boot failure."""
    from app.core.config import Settings

    settings = Settings(ml_model_path=str(tmp_path), ml_fail_fast=True)
    for name in REQUIRED_CHECKPOINT_FILES:
        (tmp_path / name).write_bytes(b"{}")

    def _no_torch(self):
        raise ModelRuntimeError("the intent classifier requires 'torch'")

    monkeypatch.setattr(MLRuntime, "_build_classifier", _no_torch)
    runtime = MLRuntime(settings)

    with pytest.raises(ModelRuntimeError) as caught:
        runtime.load()

    assert str(caught.value) == "The intent classifier runtime could not be initialised."
    assert isinstance(caught.value.__cause__, ModelRuntimeError)
    assert "requires 'torch'" in str(caught.value.__cause__)


def test_an_unexpected_load_failure_is_reported_as_our_bug_not_a_missing_checkpoint(
    tmp_path, monkeypatch
):
    """``load_failed`` is a different runbook page from ``checkpoint_missing``.

    A bare ``RuntimeError`` escaping the loader is our wiring, not the operator's
    deployment, and the reason string is the only thing telling them apart.
    """
    from app.core.config import Settings

    settings = Settings(ml_model_path=str(tmp_path), ml_fail_fast=False)
    for name in REQUIRED_CHECKPOINT_FILES:
        (tmp_path / name).write_bytes(b"{}")

    def _explode(self):
        raise RuntimeError("defect in the wiring")

    monkeypatch.setattr(MLRuntime, "_build_classifier", _explode)
    runtime = MLRuntime(settings)

    status = runtime.load()

    assert status.reason == MLRuntime.REASON_LOAD_FAILED
    assert status.reason != MLRuntime.REASON_CHECKPOINT_MISSING
    assert "defect in the wiring" in status.detail


def test_an_unexpected_load_failure_raises_under_fail_fast(tmp_path, monkeypatch):
    from app.core.config import Settings

    settings = Settings(ml_model_path=str(tmp_path), ml_fail_fast=True)
    for name in REQUIRED_CHECKPOINT_FILES:
        (tmp_path / name).write_bytes(b"{}")

    def _explode(self):
        raise RuntimeError("defect in the wiring")

    monkeypatch.setattr(MLRuntime, "_build_classifier", _explode)
    runtime = MLRuntime(settings)

    with pytest.raises(MLUnavailableError):
        runtime.load()


def test_a_second_load_does_not_read_the_checkpoint_again(tmp_path, monkeypatch):
    """703 MiB twice would double the resident set for nothing.

    The counter is the assertion: the lifespan calls ``load`` at startup and a
    route reached without one may call it again, and either ordering must produce
    one model.
    """
    from app.core.config import Settings

    settings = Settings(ml_model_path=str(tmp_path))
    for name in REQUIRED_CHECKPOINT_FILES:
        (tmp_path / name).write_bytes(b"{}")

    builds: list[str] = []

    def _build(self):
        builds.append(self.settings.ml_resolved_model_path.name)
        return _StubClassifier()

    monkeypatch.setattr(MLRuntime, "_build_classifier", _build)
    runtime = MLRuntime(settings)

    first = runtime.load()
    first_classifier = runtime.classifier
    second = runtime.load()

    assert builds == [tmp_path.name]
    assert second.available is True
    assert second.reason == MLRuntime.REASON_AVAILABLE
    assert runtime.classifier is first_classifier
    assert second.load_seconds == first.load_seconds


def test_a_second_load_after_a_failure_does_not_retry(tmp_path, monkeypatch):
    """A route hitting a 703 must not re-read a checkpoint just found missing."""
    from app.core.config import Settings

    settings = Settings(ml_model_path=str(tmp_path), ml_fail_fast=False)
    for name in REQUIRED_CHECKPOINT_FILES:
        (tmp_path / name).write_bytes(b"{}")

    attempts: list[int] = []

    def _explode(self):
        attempts.append(1)
        raise ModelCheckpointError("checkpoint file model.safetensors is not readable")

    monkeypatch.setattr(MLRuntime, "_build_classifier", _explode)
    runtime = MLRuntime(settings)

    first = runtime.load()
    second = runtime.load()

    assert attempts == [1]
    assert second == first
    assert second.reason == MLRuntime.REASON_CHECKPOINT_MISSING


def test_shutdown_releases_the_classifier_and_makes_the_runtime_reloadable(tmp_path, monkeypatch):
    """A gigabyte of resident memory has to come back when the process stops."""
    from app.core.config import Settings

    settings = Settings(ml_model_path=str(tmp_path))
    for name in REQUIRED_CHECKPOINT_FILES:
        (tmp_path / name).write_bytes(b"{}")

    builds: list[int] = []

    def _build(self):
        builds.append(1)
        return _StubClassifier()

    monkeypatch.setattr(MLRuntime, "_build_classifier", _build)
    runtime = MLRuntime(settings)
    runtime.load()
    stub = runtime.classifier

    runtime.shutdown()

    assert stub.close_calls == 1
    assert runtime.classifier is None
    assert runtime.is_available is False
    assert runtime.status.reason == MLRuntime.REASON_STOPPED

    runtime.load()
    assert builds == [1, 1], "a stopped runtime must go back to the filesystem, not stay stale"


def test_shutdown_is_safe_on_a_runtime_that_never_loaded(tmp_path):
    """The lifespan's ``finally`` calls it unconditionally, including after a boot failure."""
    runtime = MLRuntime(_runtime_settings(tmp_path, ml_fail_fast=False))
    runtime.load()

    runtime.shutdown()
    runtime.shutdown()

    assert runtime.status.reason == MLRuntime.REASON_STOPPED


def test_shutdown_survives_a_classifier_that_raises_on_close(tmp_path, monkeypatch):
    """Shutdown runs in the lifespan's ``finally``; it may not replace a clean stop."""
    from app.core.config import Settings

    class _ExplodingClassifier(_StubClassifier):
        def close(self) -> None:
            raise RuntimeError("close failed")

    settings = Settings(ml_model_path=str(tmp_path))
    for name in REQUIRED_CHECKPOINT_FILES:
        (tmp_path / name).write_bytes(b"{}")

    monkeypatch.setattr(
        MLRuntime,
        "_build_classifier",
        lambda self: _ExplodingClassifier(),
    )
    runtime = MLRuntime(settings)
    runtime.load()

    runtime.shutdown()

    assert runtime.classifier is None
    assert runtime.status.reason == MLRuntime.REASON_STOPPED


def test_the_process_wide_runtime_is_a_singleton_until_it_is_reset():
    """Two runtimes would mean two copies of the weights and two answers to 'is ML up'."""
    first = get_ml_runtime()
    second = get_ml_runtime()

    assert first is second

    reset_ml_runtime()

    assert get_ml_runtime() is not first


def test_the_runtime_reads_its_checkpoint_location_from_its_settings(tmp_path):
    """``MLRuntime`` never hard-codes a path; it reads the one answer Settings gives."""
    from app.core.config import Settings

    runtime = MLRuntime(Settings(ml_model_path=str(tmp_path / "absent")))

    assert runtime.settings.ml_resolved_model_path == (tmp_path / "absent").resolve()
    assert runtime.settings.ml_checkpoint_exists is False
    assert runtime.status.checkpoint == str(tmp_path / "absent")
