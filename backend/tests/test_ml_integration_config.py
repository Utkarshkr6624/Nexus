"""The seams around Phase 11 that are not the model's answer.

Everything else in the Phase 11 suite asks *what the classifier said*. This
module asks the four questions that decide whether that answer is reachable at
all in a real deployment, and each of them fails silently when it is wrong:

* **Configuration.** ``ML_ENABLED``, ``ML_DEVICE``, ``ML_CONFIDENCE_THRESHOLD``
  and their four siblings are the published contract. A deployment that spells
  one of them wrong does not get an error — pydantic-settings ignores the unknown
  name and the process boots with the default, so a threshold of 0.55 intended
  by an operator silently becomes 0.90. The tests below read each variable back
  through ``Settings`` and pin the name, the type and the default.
* **Portability.** The default checkpoint path has to be derived from the
  package's own location. One absolute path baked into ``app/core/config.py``
  turns every other checkout — a CI container, a second developer, a moved
  drive — into a deployment whose ``ml_checkpoint_exists`` is False and whose ML
  endpoints answer 503 forever. The scan is deliberately narrow (drive letters,
  UNC shares, ``/Users/``, ``/home/``) and is paired with a positive control,
  because a scan that finds nothing is only evidence if it *can* find something.
* **Lifecycle.** One lifespan loads the model, off the event loop, onto
  ``app.state``, and shuts it down on exit. A second startup hook would be a
  second copy of 703 MiB; a load on the event loop would stall every other route
  for the five seconds it takes to read the weights.
* **Cost and safety of sharing.** The classifier is process state shared by
  every request thread, so this module pins that it holds no per-request state,
  that its forward pass is serialised by the lock its docstring promises, and
  that a user's words never reach a log — the one datum in this system that is
  untrusted free text.

Every test that needs the trained checkpoint is marked ``ml_model`` and skips
with a reason on a clean checkout, because ``backend/ml/artifacts`` is
gitignored: the configuration, lifecycle and policy tests below are the ones
that must still run there.
"""

from __future__ import annotations

import contextlib
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import app.main as app_main
import app.ml.model_loader as model_loader
from app.api.deps import get_authenticated_user, get_current_user
from app.api.deps import get_ml_runtime as provide_ml_runtime
from app.core.config import BACKEND_ROOT, DEFAULT_MODEL_PATH, ML_DEVICES, Settings
from app.ml.classifier import IntentClassifier
from app.ml.exceptions import InferenceError
from app.ml.model_loader import (
    DEFAULT_CHECKPOINT_DIR,
    REQUIRED_CHECKPOINT_FILES,
    SUPPORTED_DEVICE_REQUESTS,
    LoadedModel,
    load_model,
)
from app.ml.router import IntentRouter
from app.ml.runtime import MLRuntime, MLRuntimeStatus, get_ml_runtime
from app.ml.schemas import IntentPrediction, ModelIdentity
from ml.datasets.taxonomy import Intent

#: Every module of the serving-side ML package. Scanned below for hard-coded
#: paths, for configuration reads of its own and for logged fields; a module
#: added to the package joins all three scans without editing this file.
ML_SOURCES: tuple[Path, ...] = tuple(sorted((BACKEND_ROOT / "app" / "ml").glob("*.py")))

#: The routing endpoint's own module. Its docstring promises the submitted text
#: is never logged, so it is scanned alongside the package it delegates to.
ML_ROUTE_SOURCE = BACKEND_ROOT / "app" / "api" / "v1" / "ml.py"

#: The source of the settings module: the single source of truth for every
#: runtime setting, and therefore the one file a hard-coded path would live in.
CONFIG_SOURCE = BACKEND_ROOT / "app" / "core" / "config.py"

#: Where Phase 10 writes the checkpoint. Gitignored, so every test that needs it
#: skips rather than failing on a clean checkout.
CHECKPOINT_DIR = BACKEND_ROOT / "ml" / "artifacts" / "small-model" / "final"

#: The published environment-variable contract: the variable name, the setting it
#: moves, a value deliberately *not* the default, and what the setting becomes.
#: A renamed or dropped variable is a deployment that ignores its operator.
ML_ENVIRONMENT_CONTRACT: tuple[tuple[str, str, str, object], ...] = (
    ("ML_ENABLED", "ml_enabled", "false", False),
    (
        "ML_MODEL_PATH",
        "ml_model_path",
        "/srv/nexus/checkpoints/final",
        "/srv/nexus/checkpoints/final",
    ),
    ("ML_DEVICE", "ml_device", "cpu", "cpu"),
    ("ML_CONFIDENCE_THRESHOLD", "ml_confidence_threshold", "0.55", 0.55),
    ("ML_MAX_INPUT_CHARS", "ml_max_input_chars", "1234", 1234),
    ("ML_REJECT_CREDENTIALS", "ml_reject_credentials", "false", False),
    ("ML_FAIL_FAST", "ml_fail_fast", "true", True),
)

#: Absolute paths that are only ever valid on the machine that wrote them. Three
#: shapes cover a Windows drive letter, a Windows UNC share and the two POSIX
#: home directories a developer checkout lives under.
ABSOLUTE_PATH_PATTERNS: dict[str, re.Pattern[str]] = {
    "windows_drive": re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"),
    "windows_unc": re.compile(r"\\\\[A-Za-z0-9_.-]+\\"),
    "posix_home": re.compile(r"/(?:Users|home)/"),
}

#: A planted second startup hook, used only by the positive control below.
PLANTED_HOOK = '@app.on_event("startup")\nasync def boot() -> None: ...\n'

#: Which modules of the ML boundary are expected to log at all. The three that
#: do not — the value objects, the exception hierarchy and the package facade —
#: have nothing to say; a ``log_event`` appearing in one of them is a smell.
ML_MODULES_THAT_LOG = frozenset(
    {"classifier.py", "model_loader.py", "router.py", "runtime.py", "ml.py"}
)

#: Utterances used for the concurrency and privacy tests. Distinct enough that a
#: classifier answering one class for all of them would be visibly wrong.
CONCURRENT_TEXTS: tuple[str, ...] = (
    "Add a task to draft the migration plan for Friday",
    "Show me the analytics overview for this week",
    "Which risks are flagged on the Phoenix project?",
    "Capture a note about the Q4 roadmap in my knowledge base",
    "What should I keep learning this quarter?",
    "When is the next free slot for a deep work session?",
)

#: Threads for the concurrency tests. More workers than there are utterances per
#: round is the point: without the classifier's lock, two forward passes really
#: do run at once rather than merely appearing to.
CONCURRENCY_WORKERS = 8

#: An utterance standing in for the untrusted free text the ML boundary handles.
PRIVATE_UTTERANCE = "Rotate the staging payments gateway key before Friday"

#: A fragment of that utterance appearing in no constant, log message or module
#: of the ML boundary, so "the text was not logged" cannot pass by accident on a
#: partial match of the whole string.
UTTERANCE_FRAGMENT = "payments gateway"

#: ``log_event`` field names allowed to mention text at all. ``text_chars`` says
#: how much was submitted and reveals none of it.
TEXTUAL_FIELD_ALLOWANCE = frozenset({"text_chars"})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def checkpoint_dir() -> Path:
    """The trained checkpoint, or a skip that says which part of it is missing."""
    if not CHECKPOINT_DIR.is_dir():
        pytest.skip(f"no Phase 10 checkpoint at {CHECKPOINT_DIR}")
    missing = [name for name in REQUIRED_CHECKPOINT_FILES if not (CHECKPOINT_DIR / name).is_file()]
    if missing:
        pytest.skip(f"the Phase 10 checkpoint at {CHECKPOINT_DIR} is incomplete: {missing}")
    return CHECKPOINT_DIR


@pytest.fixture(scope="session")
def shared_classifier(checkpoint_dir: Path) -> IntentClassifier:
    """One loaded classifier for the whole session.

    Reading 703 MiB costs about five seconds, and the concurrency tests need the
    *same* instance across threads — which is the point: the model is process
    state, not a per-test fixture.
    """
    return IntentClassifier(load_model(checkpoint_dir, device="cpu"), threshold=0.90)


@pytest.fixture
def lifespan_collaborators(monkeypatch: pytest.MonkeyPatch):
    """Replace what ``app_main._lifespan`` reaches for, so it needs no database.

    Returns an installer rather than patching eagerly, because each test brings
    its own runtime and its own database probe. ``configure_logging`` is stubbed
    for two reasons: it rewrites the root logger's handlers, which would take
    pytest's log capture with it, and a lifespan that cannot log is not the
    lifespan being tested.
    """

    def install(runtime, *, database_probe=None) -> dict[str, int]:
        probes = {"count": 0}

        async def probe() -> bool:
            probes["count"] += 1
            if isinstance(database_probe, BaseException):
                raise database_probe
            return True if database_probe is None else database_probe

        monkeypatch.setattr(
            app_main, "configure_logging", lambda *_a, **_k: logging.getLogger("app.main")
        )
        monkeypatch.setattr(app_main, "check_database_connection", probe)
        monkeypatch.setattr(app_main, "get_ml_runtime", lambda: runtime)
        # No engine exists: the ASGI clients never run this lifespan, so the
        # module-global the cleanup block reads is None in a test process.
        monkeypatch.setattr(app_main, "db_session", SimpleNamespace(_engine=None))
        return probes

    return install


class _RecordingRuntime:
    """A runtime that records the calls a lifespan makes on it.

    Substituted for :class:`app.ml.runtime.MLRuntime` so the wiring can be tested
    without loading 703 MiB — the assertions are about *when* the lifespan calls
    ``load`` and ``shutdown``, not about what a model returns.
    """

    def __init__(self, *, load_error: Exception | None = None) -> None:
        self._load_error = load_error
        self.calls: list[str] = []
        self.status = MLRuntimeStatus(
            available=load_error is None,
            reason="available" if load_error is None else "load_failed",
        )

    @property
    def events(self) -> list[str]:
        """The call sequence, without the thread each load happened to run on."""
        return [call.split(":", 1)[0] for call in self.calls]

    def load(self) -> MLRuntimeStatus:
        self.calls.append(f"load:{threading.get_ident()}")
        if self._load_error is not None:
            raise self._load_error
        return self.status

    def shutdown(self) -> None:
        self.calls.append("shutdown")


class _InstrumentedLock:
    """A lock that counts how many callers are inside it at the same moment.

    Wraps the real lock the classifier holds, so replacing it changes nothing an
    inference can observe. ``inner=None`` counts without excluding, which is what
    the positive control needs: an instrument around a working lock could never
    report a second holder, and a control that cannot fail proves nothing.
    """

    def __init__(self, inner=None) -> None:
        self._inner = inner
        self._state = threading.Lock()
        self.holders = 0
        self.peak_holders = 0
        self.acquisitions = 0

    def __enter__(self) -> _InstrumentedLock:
        if self._inner is not None:
            self._inner.__enter__()
        with self._state:
            self.acquisitions += 1
            self.holders += 1
            self.peak_holders = max(self.peak_holders, self.holders)
        return self

    def __exit__(self, *exc: object) -> bool:
        with self._state:
            self.holders -= 1
        if self._inner is not None:
            return bool(self._inner.__exit__(*exc))
        return False


def _caller():
    """An admin user, standing in for the identity the ML routes require."""
    from app.models.user import User

    return User(
        email="ml-caller@example.test",
        username="ml-caller",
        hashed_password="not-a-real-hash",  # noqa: S106 - never persisted, never hashed
        role="admin",
    )


def _stub_loaded(*, fail_with: Exception | None = None, hold: threading.Barrier | None = None):
    """A :class:`LoadedModel` carrying no weights, for tests about the harness.

    Built against the *real* torch so the classifier's own tensor handling runs
    unchanged; only the tokenizer and the forward pass are faked. Used to prove
    the concurrency instrument can observe overlap, and to drive the
    inference-failure log line without breaking a real model.
    """
    import torch

    labels = tuple(str(intent) for intent in Intent)

    def tokenizer(
        _text: str,
        *,
        truncation: bool = False,
        add_special_tokens: bool = True,
        max_length: int | None = None,
        padding: bool = False,
        return_tensors: str | None = None,
    ) -> dict[str, object]:
        if fail_with is not None:
            raise fail_with
        ids = [101, 2054, 2003]
        if return_tensors == "pt":
            return {
                "input_ids": torch.tensor([ids]),
                "attention_mask": torch.ones(1, len(ids), dtype=torch.long),
            }
        return {"input_ids": ids}

    def forward(**_inputs: object) -> SimpleNamespace:
        if hold is not None:
            hold.wait(timeout=30)
        return SimpleNamespace(logits=torch.zeros(1, len(labels)))

    return LoadedModel(
        tokenizer=tokenizer,
        model=forward,
        device="cpu",
        id2label=labels,
        identity=ModelIdentity(
            base_model="stub",
            architecture="StubForSequenceClassification",
            device="cpu",
            label_count=len(labels),
            max_sequence_length=128,
            parameter_count=0,
            checkpoint="<stub>",
            load_seconds=0.0,
        ),
        torch_module=torch,
    )


def _source(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def _absolute_paths(source: str) -> set[str]:
    """Every kind of hard-coded absolute path fragment in ``source``."""
    return {name for name, pattern in ABSOLUTE_PATH_PATTERNS.items() if pattern.search(source)}


def _closure_chain(function, depth: int = 3) -> list:
    """Every function reachable from ``function``'s closure, breadth-first.

    FastAPI wraps the lifespan it is handed when a nested router supplies one of
    its own, so the context registered on the app is not the function in
    :mod:`app.main` — it is a closure over it. Comparing identities would be
    asserting an implementation detail of the framework; asking what the wrapper
    closes over asks the question that matters.
    """
    found: list = []
    seen = {function}
    frontier = [function]
    for _ in range(depth):
        following: list = []
        for outer in frontier:
            for cell in outer.__closure__ or ():
                inner = cell.cell_contents
                if callable(inner) and inner not in seen:
                    seen.add(inner)
                    found.append(inner)
                    following.append(inner)
        frontier = following
    return found


def _log_event_fields(source: str) -> list[set[str]]:
    """The keyword field names of every ``log_event(...)`` call in ``source``."""
    calls: list[set[str]] = []
    for match in re.finditer(r"log_event\(", source):
        depth = 1
        index = match.end()
        while index < len(source) and depth:
            if source[index] == "(":
                depth += 1
            elif source[index] == ")":
                depth -= 1
            index += 1
        calls.append(set(re.findall(r"(\w+)\s*=", source[match.end() : index - 1])))
    return calls


def _logged_payload(caplog: pytest.LogCaptureFixture) -> str:
    """Everything a capture of this test could possibly have retained."""
    return "\n".join(
        f"{record.name} {record.getMessage()} {record.__dict__!r}" for record in caplog.records
    )


def _lifespan_source() -> str:
    return _source(app_main.__file__)


# ---------------------------------------------------------------------------
# The published configuration contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_var", "attribute", "raw", "expected"),
    ML_ENVIRONMENT_CONTRACT,
    ids=[contract[0] for contract in ML_ENVIRONMENT_CONTRACT],
)
def test_every_ml_setting_is_read_from_its_documented_environment_variable(
    make_settings, env_var: str, attribute: str, raw: str, expected: object
):
    """The name in the documentation is the name the process reads."""
    untouched = Settings(_env_file=None)
    configured = make_settings(**{env_var: raw})

    assert getattr(configured, attribute) == expected
    # The type matters as much as the value: a string where a float belongs
    # would compare unequal here and still break the router's arithmetic later.
    assert type(getattr(configured, attribute)) is type(getattr(untouched, attribute))
    # Non-vacuity: without the variable the setting holds a different value, so
    # the assertion above is about the environment and not about a default that
    # happens to coincide with the configured one.
    assert getattr(untouched, attribute) != expected


def test_a_misspelled_ml_environment_variable_leaves_the_default_in_place(make_settings):
    """The silent failure this module exists for: a typo is ignored, not reported."""
    settings = make_settings(ML_CONFIDENCE_TRESHOLD="0.10")

    assert settings.ml_confidence_threshold == 0.90


def test_the_documented_ml_defaults_are_the_defaults():
    """Every number a deployment will not set, pinned in one place."""
    settings = Settings(_env_file=None)

    assert settings.ml_enabled is True
    assert settings.ml_fail_fast is False
    assert settings.ml_device == "auto"
    assert settings.ml_confidence_threshold == 0.90
    assert settings.ml_model_path == ""
    assert settings.ml_max_input_chars == 2000
    assert settings.ml_reject_credentials is True


def test_an_unset_model_path_resolves_to_the_default_checkpoint_directory():
    """`""` is the documented spelling of "use the default", not "load nothing"."""
    settings = Settings(_env_file=None)

    assert settings.ml_model_path == ""
    assert settings.ml_resolved_model_path == DEFAULT_MODEL_PATH
    assert settings.ml_resolved_model_path == DEFAULT_CHECKPOINT_DIR


def test_a_whitespace_model_path_is_treated_as_unset():
    """A blank line left in a unit file must not become a path named `"  "`."""
    settings = Settings(_env_file=None, ml_model_path="   ")

    assert settings.ml_resolved_model_path == DEFAULT_MODEL_PATH


def test_a_configured_relative_model_path_resolves_against_the_working_directory():
    """The documented rule for a path an operator writes by hand."""
    settings = Settings(_env_file=None, ml_model_path="checkpoints/final")

    assert settings.ml_resolved_model_path == Path("checkpoints/final").resolve()


def test_every_device_the_configuration_accepts_is_one_the_loader_supports():
    """Two lists, one vocabulary.

    They are written in different modules for different reasons — the loader
    lower-cases its argument and the settings class refuses unknown names — so
    nothing keeps them in step except this test. Drift means ``ML_DEVICE``
    accepts a value the runtime will refuse, and the operator finds out at load
    time rather than at configuration time.
    """
    assert set(ML_DEVICES) == set(SUPPORTED_DEVICE_REQUESTS)


# ---------------------------------------------------------------------------
# Validators: loud at boot, never silent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("threshold", [0.0, -0.1, 1.1, 2.0])
def test_a_confidence_threshold_outside_the_open_unit_interval_is_refused(threshold: float):
    """Zero accepts everything and a value above one refuses everything.

    Both look exactly like a working router, which is the whole argument for
    refusing them at boot rather than clamping them.
    """
    with pytest.raises(ValidationError, match=r"ML_CONFIDENCE_THRESHOLD must be in \(0, 1\]"):
        Settings(_env_file=None, ml_confidence_threshold=threshold)


def test_a_confidence_threshold_of_exactly_one_is_accepted():
    """The rule is the half-open interval ``(0, 1]``, so 1.0 is legal.

    A threshold of one means "only act when the model is certain", which is a
    real deployment choice; refusing it would push an operator into a
    ``0.9999999999`` that is not the same number.
    """
    assert Settings(_env_file=None, ml_confidence_threshold=1.0).ml_confidence_threshold == 1.0


@pytest.mark.parametrize("device", ["tpu", "gpu", "mps"])
def test_a_device_the_runtime_cannot_provide_is_refused(device: str):
    """An unrecognised device silently becomes CPU, and a queue is the report."""
    with pytest.raises(ValidationError, match="ML_DEVICE must be one of"):
        Settings(_env_file=None, ml_device=device)


def test_a_device_name_the_loader_would_have_accepted_is_still_refused():
    """``resolve_device`` lower-cases its argument; ``Settings`` does not.

    ``ML_DEVICE=GPU`` is therefore a boot failure even though the loader itself
    would have honoured it on this CPU-only machine. The asymmetry is real, and
    pinned here so that making the validator forgiving becomes a deliberate edit
    rather than an accident nobody noticed.
    """
    torch_stub = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))

    assert model_loader.resolve_device(torch_stub, "CPU") == "cpu"
    with pytest.raises(ValidationError, match="ML_DEVICE must be one of"):
        Settings(_env_file=None, ml_device="CPU")


@pytest.mark.parametrize("limit", [0, -1, -2000])
def test_a_character_ceiling_that_refuses_every_utterance_is_refused(limit: int):
    with pytest.raises(ValidationError, match="ML_MAX_INPUT_CHARS must be positive"):
        Settings(_env_file=None, ml_max_input_chars=limit)


def test_the_character_ceiling_is_bounded_by_the_trained_context():
    """Past 10 000 characters the text is truncated before the model sees it."""
    assert Settings(_env_file=None, ml_max_input_chars=10_000).ml_max_input_chars == 10_000

    with pytest.raises(ValidationError, match="ML_MAX_INPUT_CHARS must not exceed 10000"):
        Settings(_env_file=None, ml_max_input_chars=10_001)


def test_an_invalid_ml_setting_stops_the_process_at_construction(make_settings):
    """``Settings`` is built once per process, so the refusal is seen at boot."""
    with pytest.raises(ValidationError, match="ML_CONFIDENCE_THRESHOLD must be in"):
        make_settings(ML_CONFIDENCE_THRESHOLD="1.5")


def test_a_valid_ml_setting_set_from_the_environment_is_honoured(make_settings):
    """The positive control for the test above: this path does not always raise."""
    settings = make_settings(ML_CONFIDENCE_THRESHOLD="0.95", ML_DEVICE="cpu")

    assert settings.ml_confidence_threshold == 0.95
    assert settings.ml_device == "cpu"


# ---------------------------------------------------------------------------
# Portability
# ---------------------------------------------------------------------------


def test_the_default_checkpoint_path_is_derived_from_the_package_root():
    """Anchored to the repository, not to the drive the author happened to use."""
    assert BACKEND_ROOT.name == "backend"
    assert BACKEND_ROOT / "app" / "core" / "config.py" == CONFIG_SOURCE
    assert DEFAULT_MODEL_PATH == BACKEND_ROOT / "ml" / "artifacts" / "small-model" / "final"


def test_the_loader_and_the_settings_resolve_the_same_checkpoint_directory():
    """Two modules name the default; nothing keeps them equal except this test."""
    assert DEFAULT_CHECKPOINT_DIR == DEFAULT_MODEL_PATH


def test_the_settings_source_contains_no_hard_coded_absolute_path():
    assert _absolute_paths(_source(CONFIG_SOURCE)) == set()


@pytest.mark.parametrize("path", ML_SOURCES, ids=lambda path: path.name)
def test_no_module_of_the_ml_package_contains_a_hard_coded_absolute_path(path: Path):
    assert _absolute_paths(_source(path)) == set()


def test_the_absolute_path_scan_detects_the_paths_it_claims_to():
    """Positive control: a scan that cannot fail proves nothing when it finds none."""
    assert _absolute_paths('DEFAULT = r"C:\\Users\\ada\\nexus"') == {"windows_drive"}
    assert _absolute_paths('CHECKPOINT = "/home/ada/nexus/ml/artifacts"') == {"posix_home"}
    assert _absolute_paths('CHECKPOINT = "/Users/ada/nexus/ml/artifacts"') == {"posix_home"}
    assert _absolute_paths(r'SHARE = "\\\\build01\\nexus\\final"') == {"windows_unc"}
    assert _absolute_paths('RELATIVE = "ml/artifacts/small-model/final"') == set()


# ---------------------------------------------------------------------------
# Configuration is centralised
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ML_SOURCES, ids=lambda path: path.name)
def test_no_module_of_the_ml_package_reads_the_environment_itself(path: Path):
    """``app.core.config`` is the single source of truth; a second reader is a bug."""
    source = _source(path)

    assert "os.environ" not in source, path
    assert "getenv" not in source, path
    assert "dotenv" not in source, path
    assert "env_file" not in source, path


@pytest.mark.parametrize("path", ML_SOURCES, ids=lambda path: path.name)
def test_no_module_of_the_ml_package_declares_its_own_settings_framework(path: Path):
    source = _source(path)

    assert "BaseSettings" not in source, path
    assert "SettingsConfigDict" not in source, path


def test_the_runtime_is_the_only_module_that_takes_its_configuration_from_settings():
    """One importer means one place an ML setting can be read.

    The other six modules take everything they need as arguments, which is why
    they are testable without a ``Settings`` object in scope at all.
    """
    importers = {path.stem for path in ML_SOURCES if "app.core.config" in _source(path)}

    assert importers == {"runtime"}


def test_the_only_os_use_in_the_ml_package_is_a_filesystem_permission_check():
    """``model_loader`` imports ``os`` — for ``os.access``, never for configuration.

    Recorded exactly rather than as a blanket "no ``os``", because a blanket ban
    would also be satisfied by renaming the import, and the fact worth pinning is
    that the single use is a readability check on a checkpoint file.
    """
    used = {path.name: sorted(set(re.findall(r"\bos\.\w+", _source(path)))) for path in ML_SOURCES}

    assert {name: uses for name, uses in used.items() if uses} == {
        "model_loader.py": ["os.R_OK", "os.access"]
    }


# ---------------------------------------------------------------------------
# Lifespan wiring
# ---------------------------------------------------------------------------


def test_the_lifespan_is_the_apps_only_startup_hook(app):
    """One startup path, installed by ``create_app``.

    A second ``on_event("startup")`` handler would be a second copy of the
    weights, or a load that happens after the first request already needed one.
    """
    context = app.router.lifespan_context

    assert context is not None, "an app with no lifespan runs no ML load at all"
    assert app_main._lifespan in [context, *_closure_chain(context)]

    source = _lifespan_source()
    assert re.search(r"on_event\(|add_event_handler\(", source) is None
    # Non-vacuity: the pattern above is one that would find a planted hook.
    assert re.search(r"on_event\(|add_event_handler\(", PLANTED_HOOK) is not None


def test_the_closure_walk_finds_a_wrapped_lifespan():
    """Positive control: the walk above has to be able to find a wrapper."""

    def inner() -> None: ...

    def outer():
        return inner

    assert inner in _closure_chain(outer)
    assert _closure_chain(lambda: None) == []


async def test_the_lifespan_loads_the_runtime_onto_the_app_state(app, lifespan_collaborators):
    """A route must reach the instance the server loaded, not build its own."""
    runtime = _RecordingRuntime()
    assert runtime.calls == [], "the runtime must be untouched before the lifespan runs"
    lifespan_collaborators(runtime)
    application = app_main.create_app(Settings(_env_file=None))

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(app_main._lifespan(application))

        assert application.state.ml_runtime is runtime
        assert runtime.events == ["load"], "one load, and no shutdown before the exit"

    assert runtime.events == ["load", "shutdown"]


async def test_the_lifespan_loads_the_model_off_the_event_loop(app, lifespan_collaborators):
    """Reading 703 MiB on the loop would stall every other route for five seconds."""
    runtime = _RecordingRuntime()
    lifespan_collaborators(runtime)
    application = app_main.create_app(Settings(_env_file=None))
    loop_thread = threading.get_ident()

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(app_main._lifespan(application))

    load_call, *rest = runtime.calls
    assert [rest[0]] == ["shutdown"]
    assert int(load_call.split(":", 1)[1]) != loop_thread
    assert "run_in_threadpool(runtime.load)" in _lifespan_source()


async def test_a_failed_database_probe_does_not_stop_startup(app, lifespan_collaborators):
    """The probe is advisory: a deployment without Postgres still has to boot."""
    runtime = _RecordingRuntime()
    probes = lifespan_collaborators(runtime, database_probe=OSError("no database here"))
    application = app_main.create_app(Settings(_env_file=None))

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(app_main._lifespan(application))

        assert probes["count"] == 1, "the probe must actually have been attempted"
        assert application.state.ml_runtime is runtime
        assert runtime.status.available is True

    assert runtime.events == ["load", "shutdown"]


async def test_a_failed_model_load_does_not_stop_startup(app, lifespan_collaborators):
    """``ML_FAIL_FAST`` is off by default, so a broken checkpoint is a 503, not a crash.

    The runtime is still attached to ``app.state`` and still shut down on exit,
    so a deployment that repairs its checkpoint does not also have to repair a
    leaked process.
    """
    runtime = _RecordingRuntime(load_error=RuntimeError("the weights are corrupt"))
    lifespan_collaborators(runtime)
    application = app_main.create_app(Settings(_env_file=None))

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(app_main._lifespan(application))

        assert runtime.calls[0].startswith("load:"), "the failing load must have been attempted"
        assert application.state.ml_runtime is runtime
        assert runtime.status.available is False

    assert runtime.calls[-1] == "shutdown"


def test_a_runtime_the_lifespan_attached_is_the_one_the_ml_routes_will_get():
    """``app.state`` wins, so a request can never build a second runtime."""
    runtime = _RecordingRuntime()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ml_runtime=runtime)))

    assert provide_ml_runtime(request) is runtime


def test_a_client_that_never_ran_a_lifespan_falls_back_to_the_module_singleton():
    """``ASGITransport`` never executes a lifespan, and that must not be a 500.

    The fallback is the process-wide singleton rather than an error, which is
    what lets every DB-free client in this suite reach the ML routes at all.
    """
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    resolved = provide_ml_runtime(request)

    assert resolved is get_ml_runtime()
    assert isinstance(resolved.settings, Settings)
    # Compared by value, not by identity: `get_settings` is an `lru_cache`, and
    # this suite's own `settings` fixture clears that cache around every test that
    # asks for it. Asserting `resolved.settings is get_settings()` therefore
    # depends on which tests ran before this one — it passed in isolation and
    # failed in a full run, which is a statement about test ordering rather than
    # about the fallback. What matters is that the fallback hands back the
    # process-wide runtime, and that its configuration is the real one.
    assert resolved.settings.ml_confidence_threshold == Settings().ml_confidence_threshold
    assert provide_ml_runtime(request) is resolved


# ---------------------------------------------------------------------------
# One load, not one per request
# ---------------------------------------------------------------------------


def test_loading_a_runtime_twice_builds_one_classifier(monkeypatch, tmp_path: Path):
    """``load()`` is idempotent: the lifespan and a first request may both call it."""
    settings = Settings(
        _env_file=None, ml_enabled=True, ml_model_path=str(tmp_path), ml_device="cpu"
    )
    runtime = MLRuntime(settings)
    classifier = IntentClassifier(_stub_loaded(), threshold=0.9)
    builds: list[IntentClassifier] = []

    def counting_build(_runtime: MLRuntime) -> IntentClassifier:
        builds.append(classifier)
        return classifier

    monkeypatch.setattr(MLRuntime, "_build_classifier", counting_build)

    first = runtime.load()
    second = runtime.load()

    assert len(builds) == 1, "a second load must not re-read the checkpoint"
    assert first is second
    assert runtime.classifier is classifier


def test_a_failed_load_is_not_retried_by_the_second_call(tmp_path: Path):
    """A missing checkpoint must not cost 703 MiB of failed reads on every request."""
    settings = Settings(
        _env_file=None,
        ml_enabled=True,
        ml_model_path=str(tmp_path / "absent"),
        ml_device="cpu",
    )
    runtime = MLRuntime(settings)

    first = runtime.load()
    second = runtime.load()

    assert first.reason == MLRuntime.REASON_CHECKPOINT_MISSING
    assert second.reason == first.reason
    assert runtime.classifier is None


@pytest.mark.ml_model
async def test_a_session_of_routing_requests_loads_the_model_exactly_once(
    app, offline_client, checkpoint_dir, make_settings, monkeypatch
):
    """The acceptance criterion, measured through the HTTP surface.

    The lifespan loads once and every request after it reuses that instance. A
    regression that loaded per request would leave the counter at seven rather
    than at one, and the assertion taken before any request is sent proves the
    counter is live rather than permanently zero.
    """
    settings = make_settings(ML_MODEL_PATH=str(checkpoint_dir), ML_DEVICE="cpu")
    runtime = MLRuntime(settings)
    loads: list[str] = []
    real_load_model = model_loader.load_model

    def counting_load_model(*args, **kwargs):
        loads.append(str(kwargs.get("device", "auto")))
        return real_load_model(*args, **kwargs)

    monkeypatch.setattr(model_loader, "load_model", counting_load_model)

    async def healthy_probe() -> bool:
        return True

    monkeypatch.setattr(app_main, "configure_logging", lambda *_a, **_k: None)
    monkeypatch.setattr(app_main, "check_database_connection", healthy_probe)
    monkeypatch.setattr(app_main, "get_ml_runtime", lambda: runtime)
    monkeypatch.setattr(app_main, "db_session", SimpleNamespace(_engine=None))

    caller = _caller()
    app.dependency_overrides[get_current_user] = lambda: caller
    app.dependency_overrides[get_authenticated_user] = lambda: caller
    try:
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(app_main._lifespan(app))

            assert len(loads) == 1, f"the lifespan loaded the model {len(loads)} times"
            assert runtime.is_available is True

            for text in CONCURRENT_TEXTS:
                response = await offline_client.post("/api/v1/ml/route", json={"text": text})

                assert response.status_code == 200, response.text
                body = response.json()
                assert body["intent"] in {str(intent) for intent in Intent}
                assert body["status"] in {
                    "accepted",
                    "uncertain",
                    "out_of_scope",
                    "generation_unavailable",
                }

            assert len(loads) == 1, "a request loaded the model again"
    finally:
        app.dependency_overrides.clear()
        app.state.__dict__.pop("ml_runtime", None)


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


@pytest.mark.ml_model
def test_concurrent_predictions_agree_with_the_sequential_answer(shared_classifier):
    """One shared model, many threads, no crossed answers.

    The sequential pass is the control: it proves the utterances really are
    discriminative, so "every concurrent result matched" is not the vacuous
    outcome of a model that answers one class for all of them.
    """
    sequential = [shared_classifier.predict(text) for text in CONCURRENT_TEXTS]
    assert len({prediction.intent for prediction in sequential}) >= 3

    with ThreadPoolExecutor(max_workers=CONCURRENCY_WORKERS) as pool:
        concurrent = list(pool.map(shared_classifier.predict, CONCURRENT_TEXTS * 3))

    taxonomy = {str(intent) for intent in Intent}
    for prediction in concurrent:
        assert isinstance(prediction, IntentPrediction)
        assert prediction.intent in taxonomy
        assert 0.0 <= prediction.confidence <= 1.0
        assert prediction.latency_ms >= 0.0

    for expected, actual in zip(sequential, concurrent[: len(sequential)], strict=True):
        assert actual.intent == expected.intent
        assert actual.confidence == pytest.approx(expected.confidence, abs=1e-6)


def test_the_concurrency_instrument_notices_overlapping_callers():
    """Positive control: eight threads really can hold one lock's instrument at once."""
    instrumented = _InstrumentedLock()
    rendezvous = threading.Barrier(CONCURRENCY_WORKERS, timeout=30)

    def hold() -> None:
        with instrumented:
            rendezvous.wait()

    with ThreadPoolExecutor(max_workers=CONCURRENCY_WORKERS) as pool:
        list(pool.map(lambda _: hold(), range(CONCURRENCY_WORKERS)))

    assert instrumented.peak_holders == CONCURRENCY_WORKERS
    assert instrumented.acquisitions == CONCURRENCY_WORKERS


def test_a_classifier_without_the_lock_would_run_forward_passes_at_once():
    """Positive control for the serialisation test below.

    The stub's forward pass waits on a barrier that only opens if all four
    workers reach it simultaneously, so this raises ``InferenceError`` (the
    barrier breaking) if the predictions were ever serialised. Taking the lock
    away is what makes the concurrency real, which is exactly the condition the
    real classifier's lock prevents.
    """
    classifier = IntentClassifier(_stub_loaded(hold=threading.Barrier(4, timeout=10)))
    classifier._lock = contextlib.nullcontext()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: classifier.predict(CONCURRENT_TEXTS[0]), range(4)))

    assert all(isinstance(result, IntentPrediction) for result in results)
    assert all(result.intent in {str(intent) for intent in Intent} for result in results)


@pytest.mark.ml_model
def test_the_shared_classifier_serialises_its_forward_pass(shared_classifier):
    """703 MiB of shared mutable state, one in-flight pass at a time.

    Combined with the two controls above: the instrument is capable of reporting
    a peak above one, and the stub proves real forward passes would overlap.
    """
    instrumented = _InstrumentedLock(shared_classifier._lock)
    shared_classifier._lock = instrumented
    try:
        with ThreadPoolExecutor(max_workers=CONCURRENCY_WORKERS) as pool:
            predictions = list(pool.map(shared_classifier.predict, CONCURRENT_TEXTS * 2))
    finally:
        shared_classifier._lock = instrumented._inner

    assert instrumented.acquisitions == len(CONCURRENT_TEXTS) * 2
    assert instrumented.peak_holders == 1
    assert all(isinstance(prediction, IntentPrediction) for prediction in predictions)


@pytest.mark.ml_model
def test_the_classifier_stores_no_per_request_state(shared_classifier):
    """Two threads cannot observe each other's tensor, because there is nowhere to put one.

    The attribute *set* is pinned exactly: an instance attribute added to hold a
    request's text or its logits fails here, which is the point of checking the
    keys and not only the values.
    """
    before = dict(vars(shared_classifier))
    assert set(before) == {
        "_loaded",
        "_threshold",
        "_alternative_count",
        "_max_sequence_length",
        "_reject_credentials",
        "_lock",
    }

    predictions = [shared_classifier.predict(text) for text in CONCURRENT_TEXTS * 2]

    after = vars(shared_classifier)
    assert set(after) == set(before)
    for name, value in before.items():
        if name == "_lock":
            continue
        assert after[name] is value, name
    assert all(isinstance(prediction, IntentPrediction) for prediction in predictions)


# ---------------------------------------------------------------------------
# The user's words never reach a log
# ---------------------------------------------------------------------------


@pytest.mark.ml_model
def test_a_routing_decision_is_logged_with_its_metadata_and_not_the_utterance(
    shared_classifier, caplog: pytest.LogCaptureFixture
):
    """The one-line contract: intent, confidence, latency — never the request.

    The ``intent_routed`` record is asserted to exist, so this cannot pass by
    capturing nothing.
    """
    with caplog.at_level(logging.INFO):
        prediction = shared_classifier.predict(PRIVATE_UTTERANCE)
        decision = IntentRouter(threshold=0.90).route(prediction)

    routed = [record for record in caplog.records if record.getMessage() == "intent_routed"]
    assert len(routed) == 1, "the router must log exactly one routing decision"
    record = routed[0]

    assert record.intent == decision.intent
    assert record.confidence == pytest.approx(float(decision.confidence), abs=1e-4)
    assert record.latency_ms >= 0.0
    assert "text" not in record.__dict__

    payload = _logged_payload(caplog)
    assert PRIVATE_UTTERANCE not in payload
    assert UTTERANCE_FRAGMENT not in payload


def test_a_failed_inference_is_logged_as_a_type_and_a_length_and_not_the_text(
    caplog: pytest.LogCaptureFixture,
):
    """The error path is the one that reaches for the text by reflex. It must not."""
    classifier = IntentClassifier(_stub_loaded(fail_with=ValueError("tokenizer exploded")))

    with caplog.at_level(logging.ERROR), pytest.raises(InferenceError):
        classifier.predict(PRIVATE_UTTERANCE)

    failures = [record for record in caplog.records if record.getMessage() == "ml.inference_failed"]
    assert len(failures) == 1, "the failure must be logged exactly once"
    record = failures[0]

    assert record.error == "ValueError"
    assert record.characters == len(PRIVATE_UTTERANCE)
    assert record.label_count == len(Intent)

    payload = _logged_payload(caplog)
    assert PRIVATE_UTTERANCE not in payload
    assert UTTERANCE_FRAGMENT not in payload


@pytest.mark.parametrize("path", [*ML_SOURCES, ML_ROUTE_SOURCE], ids=lambda path: path.name)
def test_no_log_event_in_the_ml_boundary_publishes_the_submitted_text(path: Path):
    """A structural guard standing behind the behavioural tests above.

    ``text_chars`` is the only text-shaped field name permitted: a count says how
    much was submitted and reveals none of it.
    """
    calls = _log_event_fields(_source(path))
    if path.name in ML_MODULES_THAT_LOG:
        assert calls, f"{path.name} logs nothing, so this scan has nothing to check"
    else:
        assert not calls, f"{path.name} has no business logging"

    for fields in calls:
        textual = {name for name in fields if "text" in name.lower() or "utterance" in name.lower()}
        assert textual <= TEXTUAL_FIELD_ALLOWANCE, (path.name, textual)


def test_the_log_field_scan_reads_the_fields_it_claims_to():
    """Positive control: the parser above finds the fields of a known call."""
    calls = _log_event_fields(
        'log_event(\n    logger,\n    logging.INFO,\n    "intent_routed",\n'
        "    intent=decision.intent,\n    text_chars=len(payload.text),\n)\n"
    )

    assert calls == [{"intent", "text_chars"}]
