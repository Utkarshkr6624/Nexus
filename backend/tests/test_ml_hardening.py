r"""§14.4 — what the classifier does with a checkpoint or an utterance it cannot serve.

Three claims are pinned here, and each one is a claim about a *failure*, because
that is where a classifier earns the right to be trusted with a user's words.

**A broken checkpoint reads as a broken checkpoint.** A missing member file, a
corrupt ``model.safetensors`` and a window the weights were not trained for all
have to arrive as a :class:`~app.ml.exceptions.ModelCheckpointError` naming what
is wrong. The alternative — a ``SafetensorError`` or a shape mismatch surfacing
three frames deep as a 503 with an opaque ``detail`` — is an operator debugging a
message written by someone else's library. The corruption tests build a real
directory in ``tmp_path`` and hand it to the real loader: a stubbed
``from_pretrained`` would only prove that the test's own stub raises.

**The user's words never reach a durable record.** The prediction log line
carries intent, confidence, latency and a character *count*. These tests capture
the emitted records and search every formatted field for a canary string, so
this is an assertion about the records that actually happened rather than a
reading of the source.

**Input the classifier cannot serve is a 4xx.** Empty, blank, over-long,
credential-shaped, and — the one this file found — text carrying an unpaired
surrogate. That last case is here because it did *not* behave: a JSON body may
legally contain ``"\\ud800"``, Python decodes it to a ``str`` that satisfies every
length check, and the Rust tokenizer then refuses the encode with a ``TypeError``
about its own signature. Before ``app/ml/classifier.py`` grew the UTF-8 check,
that reached the client as a **500** with a torch-adjacent cause. It is asserted
here at the HTTP boundary because that is the only place the status code exists.

Nothing here needs the 703 MiB checkpoint except the tests marked ``ml_model``,
which skip with a reason on a checkout that has not run Phase 10.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.api.deps import get_authenticated_user
from app.core.config import BACKEND_ROOT, get_settings
from app.core.deps import get_current_user
from app.ml.classifier import MAX_UTTERANCE_CHARACTERS, IntentClassifier
from app.ml.exceptions import InferenceError, InvalidUtteranceError, ModelCheckpointError
from app.ml.model_loader import (
    REQUIRED_CHECKPOINT_FILES,
    _validate_context_length,
    load_model,
)
from app.ml.runtime import MLRuntime
from app.ml.schemas import IntentPrediction, ModelIdentity
from app.models.user import User
from ml.datasets.routing import label_map

ml_model = pytest.mark.ml_model

CHECKPOINT_DIR = BACKEND_ROOT / "ml" / "artifacts" / "small-model" / "final"

#: Appears in exactly one place in the system: the ``text`` of the requests the
#: logging tests send. If it turns up in a log record, the utterance was copied.
CANARY = "zqxjvmark"

#: The checkpoint's own ``max_position_embeddings``. Every DeBERTa-v3 encoder
#: NEXUS ships has this, and the trained window (128) sits well inside it — the
#: ratio is what makes an over-long recorded window an obvious impostor.
CHECKPOINT_POSITIONAL_CAPACITY = 512

#: ``training_state.json`` sits one level above ``final/``, which is where
#: :func:`app.ml.model_loader.load_model` looks for the trained context length.
TRAINED_WINDOW = 128


def _checkpoint_present() -> bool:
    return all((CHECKPOINT_DIR / name).is_file() for name in REQUIRED_CHECKPOINT_FILES)


def _skip_without_checkpoint() -> None:
    if not _checkpoint_present():
        pytest.skip(
            f"no Phase 10 checkpoint at {CHECKPOINT_DIR}; backend/ml/artifacts is gitignored"
        )


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _ExplodingWeights:
    """A ``LoadedModel`` stand-in whose forward pass is a hard failure.

    Every test that wants the *error* path uses this: the moment
    :meth:`IntentClassifier.predict` gets past validation and into inference, the
    model raises and the classifier must turn that into a clean 500 rather than
    letting it escape. ``id2label`` is present because the failure log line
    reads it, and a double without it would raise out of the error path instead.
    """

    max_sequence_length = TRAINED_WINDOW
    id2label = ("unreachable",)

    def __getattr__(self, name: str):
        raise RuntimeError(f"inference reached the model (wanted {name!r})")


class _StubClassifier:
    """A runtime's classifier stand-in that records what it was asked."""

    def __init__(self, prediction: IntentPrediction, identity: ModelIdentity | None) -> None:
        self._prediction = prediction
        self._identity = identity
        self.seen: list[str] = []

    @property
    def identity(self) -> ModelIdentity | None:
        return self._identity

    def predict(self, text: str) -> IntentPrediction:
        self.seen.append(text)
        return self._prediction


def _prediction() -> IntentPrediction:
    return IntentPrediction(
        intent="task_manage",
        confidence=0.987_654,
        alternatives=(("project_manage", 0.006),),
        truncated=False,
        latency_ms=41.5,
    )


def _identity() -> ModelIdentity:
    settings = get_settings()
    return ModelIdentity(
        base_model="microsoft/deberta-v3-base",
        architecture="DebertaV2ForSequenceClassification",
        device="cpu",
        label_count=len(label_map()),
        max_sequence_length=TRAINED_WINDOW,
        parameter_count=184_432_910,
        checkpoint=str(settings.ml_resolved_model_path),
        load_seconds=5.25,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def authorised_client(app, offline_client) -> Iterator[AsyncClient]:
    """An offline client whose identity resolves and whose permission gate is real.

    Overriding the two identity dependencies is what every other ML test in this
    suite does; ``require_permission`` itself stays in place, so a 403 here is
    produced by the real permission table.
    """
    caller = User(username="ada", email="ada@nexus.dev", role="user", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: caller
    app.dependency_overrides[get_authenticated_user] = lambda: caller
    try:
        yield offline_client
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_authenticated_user, None)


@pytest.fixture
def serving_runtime(app, settings) -> Iterator[tuple[MLRuntime, _StubClassifier]]:
    """A runtime that answers, backed by :class:`_StubClassifier`."""
    classifier = _StubClassifier(_prediction(), _identity())
    runtime = MLRuntime(settings, classifier=classifier)
    missing = object()
    previous = getattr(app.state, "ml_runtime", missing)
    app.state.ml_runtime = runtime
    try:
        yield runtime, classifier
    finally:
        app.state.ml_runtime = None if previous is missing else previous


@pytest.fixture
def broken_inference_runtime(app, settings) -> Iterator[MLRuntime]:
    """A runtime whose model raises the instant inference is attempted.

    The 500 case, with a real :class:`IntentClassifier` in front of it so the
    production validation, logging and error-wrapping code is what runs.
    """
    runtime = MLRuntime(settings, classifier=IntentClassifier(_ExplodingWeights()))  # type: ignore[arg-type]
    missing = object()
    previous = getattr(app.state, "ml_runtime", missing)
    app.state.ml_runtime = runtime
    try:
        yield runtime
    finally:
        app.state.ml_runtime = None if previous is missing else previous


def _fake_checkpoint(directory: Path, *, weights: bytes, extra_config: dict | None = None):
    """Write a directory with every required member, and ``weights`` as the model.

    ``tokenizer.json`` and ``tokenizer_config.json`` are copied from the real
    checkpoint rather than fabricated, because the point of these tests is what
    happens *after* the tokenizer succeeds. ``config.json`` is shrunk so a load
    that unexpectedly succeeds costs milliseconds instead of reading 703 MiB.

    Returns the directory.
    """
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copy(CHECKPOINT_DIR / "tokenizer.json", directory / "tokenizer.json")
    shutil.copy(CHECKPOINT_DIR / "tokenizer_config.json", directory / "tokenizer_config.json")

    ids = {str(index): name for name, index in label_map().items()}
    config = json.loads((CHECKPOINT_DIR / "config.json").read_text(encoding="utf-8"))
    config["id2label"] = ids
    config["label2id"] = {name: int(index) for index, name in ids.items()}
    config.update(
        {
            "hidden_size": 64,
            "pooler_hidden_size": 64,
            "intermediate_size": 128,
            "num_hidden_layers": 2,
            "num_attention_heads": 2,
            "vocab_size": 1000,
        }
    )
    config.update(extra_config or {})
    (directory / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (directory / "label_map.json").write_text(json.dumps({"id2label": ids}), encoding="utf-8")
    (directory / "model.safetensors").write_bytes(weights)
    return directory


# ---------------------------------------------------------------------------
# A corrupt model.safetensors is a checkpoint error, not a tensor error
# ---------------------------------------------------------------------------


@ml_model
@pytest.mark.parametrize(
    ("case", "weights"),
    [
        ("garbage", b"this is not a safetensors header at all" * 40),
        ("empty", b""),
        ("zeroes", bytes(4096)),
        ("truncated_header", bytes([0x7F]) + b"\x00" * 200),
    ],
    ids=["garbage", "empty", "zeroes", "truncated_header"],
)
def test_a_corrupted_weights_file_is_a_checkpoint_error_naming_it(tmp_path, case, weights):
    """Four ways a ``model.safetensors`` can be broken, one answer each.

    ``empty`` is the case that matters most operationally: a half-copied or
    interrupted artifact is a zero-length file, and a checkpoint that fails with
    a message about mmap tells an operator nothing.
    """
    _skip_without_checkpoint()
    pytest.importorskip("transformers", reason="transformers is not installed")
    pytest.importorskip("torch", reason="torch is not installed")
    checkpoint = _fake_checkpoint(tmp_path / case, weights=weights)

    with pytest.raises(ModelCheckpointError) as raised:
        load_model(checkpoint, device="cpu")

    assert "model.safetensors" in str(raised.value)
    # The classification must be the checkpoint's, not a generic 503: the whole
    # point of this exception is that the operator is told to re-run Phase 10.
    assert not isinstance(raised.value, InferenceError)


@ml_model
def test_the_checkpoint_error_does_not_leak_the_checkpoint_path(tmp_path):
    """A corruption report names the file; it does not hand out the directory."""
    _skip_without_checkpoint()
    pytest.importorskip("transformers", reason="transformers is not installed")
    pytest.importorskip("torch", reason="torch is not installed")
    checkpoint = _fake_checkpoint(tmp_path / "secret-place", weights=b"garbage" * 100)

    with pytest.raises(ModelCheckpointError) as raised:
        load_model(checkpoint, device="cpu")

    assert str(checkpoint) not in str(raised.value)


@ml_model
def test_a_corrupted_checkpoint_degrades_the_runtime_rather_than_raising(tmp_path):
    """The corruption reaches a caller as a 503 with a machine-readable reason."""
    _skip_without_checkpoint()
    pytest.importorskip("transformers", reason="transformers is not installed")
    pytest.importorskip("torch", reason="torch is not installed")
    checkpoint = _fake_checkpoint(tmp_path / "corrupt", weights=b"nonsense" * 100)

    runtime = MLRuntime(get_settings())
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(
                type(runtime.settings), "ml_resolved_model_path", checkpoint, raising=False
            )
            patch.setattr(type(runtime.settings), "ml_checkpoint_exists", True, raising=False)
            status = runtime.load()
    finally:
        runtime.shutdown()

    assert status.available is False
    assert status.reason == MLRuntime.REASON_CHECKPOINT_MISSING


# ---------------------------------------------------------------------------
# A checkpoint from a different training run must not load silently
# ---------------------------------------------------------------------------


def _config_with_capacity(capacity: int) -> dict:
    return {"max_position_embeddings": capacity}


def test_a_trained_window_wider_than_the_encoder_refuses_the_load():
    """The check the loader was missing: the window against the architecture.

    DeBERTa *stretches* its relative-position embedding past
    ``max_position_embeddings`` rather than refusing, so a checkpoint whose
    ``training_state.json`` records a wider trained window than its own config was
    built for loads cleanly, runs, and returns a confident-looking intent
    computed from positions the encoder was never fitted on. Nothing anywhere
    reports an error.
    """
    with pytest.raises(ModelCheckpointError) as raised:
        _validate_context_length(2048, _config_with_capacity(CHECKPOINT_POSITIONAL_CAPACITY))

    assert "2048" in str(raised.value)
    assert str(CHECKPOINT_POSITIONAL_CAPACITY) in str(raised.value)


def test_a_trained_window_the_encoder_can_reach_is_accepted():
    assert _validate_context_length(TRAINED_WINDOW, _config_with_capacity(512)) is None


def test_a_window_exactly_at_the_capacity_is_accepted():
    """Off-by-one boundary: ``<=`` capacity is fine, one more is not."""
    assert _validate_context_length(512, _config_with_capacity(512)) is None


@pytest.mark.parametrize("capacity", [None, 0, -1, "512", True])
def test_an_architecture_declaring_no_usable_capacity_is_not_guessed_at(capacity):
    """An undeclared capacity is not evidence of a problem, so it is not a refusal.

    ``True`` is in the list deliberately: it is an ``int`` in Python, and a
    checkpoint declaring ``max_position_embeddings: true`` must not be read as a
    capacity of one token.
    """
    assert _validate_context_length(4096, {"max_position_embeddings": capacity}) is None


def test_the_context_length_check_runs_before_torch_is_needed(tmp_path):
    """End-to-end through ``load_model``, with no torch import on the path.

    The refusal has to land before the multi-hundred-megabyte import and before
    the weights are read, so it is asserted on a directory whose ``config.json``
    is the only real file and whose members are placeholders. If the check moved
    after the model load, this test would need 703 MiB to prove the same thing.
    """
    ids = {str(index): name for name, index in label_map().items()}
    checkpoint = tmp_path / "wide-window"
    checkpoint.mkdir()
    for name in REQUIRED_CHECKPOINT_FILES:
        (checkpoint / name).write_bytes(b"")
    config = {
        "id2label": ids,
        "max_position_embeddings": CHECKPOINT_POSITIONAL_CAPACITY,
    }
    (checkpoint / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (checkpoint / "label_map.json").write_text(json.dumps({"id2label": ids}), encoding="utf-8")
    (checkpoint.parent / "training_state.json").write_text(
        json.dumps({"config": {"max_seq_length": 4096}}), encoding="utf-8"
    )

    with pytest.raises(ModelCheckpointError, match="4096"):
        load_model(checkpoint, device="cpu")


def test_a_trained_window_at_the_architectures_capacity_still_loads(tmp_path):
    """The positive control: the check refuses the impostor, not every window."""
    ids = {str(index): name for name, index in label_map().items()}
    checkpoint = tmp_path / "right-window"
    checkpoint.mkdir()
    for name in REQUIRED_CHECKPOINT_FILES:
        (checkpoint / name).write_bytes(b"")
    config = {
        "id2label": ids,
        "max_position_embeddings": CHECKPOINT_POSITIONAL_CAPACITY,
    }
    (checkpoint / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (checkpoint / "label_map.json").write_text(json.dumps({"id2label": ids}), encoding="utf-8")
    (checkpoint.parent / "training_state.json").write_text(
        json.dumps({"config": {"max_seq_length": TRAINED_WINDOW}}), encoding="utf-8"
    )

    # Passes the context-length check, then fails at the placeholder weights.
    # Asserting on *which* error arrives is the whole point: a
    # "different training run" refusal here would mean the check is too eager.
    with pytest.raises(ModelCheckpointError) as raised:
        load_model(checkpoint, device="cpu")

    assert "different training run" not in str(raised.value)


# ---------------------------------------------------------------------------
# Input the classifier cannot serve
# ---------------------------------------------------------------------------


@pytest.fixture
def classifier() -> IntentClassifier:
    """A real classifier over a model that refuses to be reached.

    Everything below the validation boundary is therefore unreachable, which is
    how "refused at validation" is told apart from "refused by everything".
    """
    return IntentClassifier(_ExplodingWeights())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("case", "text"),
    [
        ("empty", ""),
        ("spaces", "     "),
        ("tabs_and_newlines", "\t\r\n  \t"),
        ("nbsp", "\u00a0\u00a0"),
    ],
    ids=["empty", "spaces", "tabs_and_newlines", "nbsp"],
)
def test_blank_utterances_are_refused_before_the_model_is_reached(classifier, case, text):
    with pytest.raises(InvalidUtteranceError) as raised:
        classifier.predict(text)

    assert raised.value.status_code == 422
    assert raised.value.details["reason"] == "blank"


def test_a_ten_thousand_word_paste_is_refused_on_length_not_rescued_by_truncation(classifier):
    """Truncation is not a rescue for an unbounded body.

    The tokenizer would cut 10,000 words to the trained 128 tokens and return a
    perfectly confident prediction from the first dozen words. The bound refuses
    it before that, so the limit is a real one rather than a comment.
    """
    text = " ".join(["migration"] * 10_000)

    with pytest.raises(InvalidUtteranceError) as raised:
        classifier.predict(text)

    assert raised.value.status_code == 422
    assert raised.value.details["max_characters"] == MAX_UTTERANCE_CHARACTERS
    assert raised.value.details["characters"] == len(text)


def test_one_character_past_the_limit_is_refused(classifier):
    """The boundary, so an off-by-one in the comparison cannot hide."""
    at_limit = "a" * MAX_UTTERANCE_CHARACTERS
    past_limit = "a" * (MAX_UTTERANCE_CHARACTERS + 1)

    # At the limit validation passes and the (deliberately exploding) model is
    # reached, which is observable as InferenceError rather than a 422.
    with pytest.raises(InferenceError):
        classifier.predict(at_limit)

    with pytest.raises(InvalidUtteranceError) as raised:
        classifier.predict(past_limit)
    assert raised.value.details["characters"] == MAX_UTTERANCE_CHARACTERS + 1


@pytest.mark.parametrize(
    ("case", "text"),
    [
        ("null_bytes", "add\x00a\x07task"),
        ("ansi_escape", "add a task \x1b[31mfor tomorrow"),
        ("vertical_tab", "add\x0ba task"),
        ("bidi_override", "add a task \u202etomorrow"),
        ("emoji", "add a task \U0001f4cc tomorrow"),
        ("mixed_scripts", "添加任务 里程碑 plan for tomorrow"),
        ("zero_width", "\u200b\u200c\u200d"),
    ],
    ids=[
        "null_bytes",
        "ansi_escape",
        "vertical_tab",
        "bidi_override",
        "emoji",
        "mixed_scripts",
        "zero_width",
    ],
)
def test_awkward_but_encodable_text_is_classified_rather_than_refused(classifier, case, text):
    """The other side of the UTF-8 check: it refuses nothing real.

    Control characters, bidi overrides and emoji are ordinary things people paste
    into a chat box. They must reach the tokenizer; the assertion is that
    validation let them through, which is what makes the surrogate refusal in the
    next test a targeted rule rather than a blanket "reject weird input".
    """
    with pytest.raises(InferenceError):
        classifier.predict(text)


@pytest.mark.parametrize(
    ("case", "text"),
    [
        ("high_surrogate", "add a task " + chr(0xD800) + " tomorrow"),
        ("low_surrogate", "add a task " + chr(0xDFFF) + " tomorrow"),
        ("high_then_low", chr(0xDBFF) + chr(0xDFFF)),
    ],
    ids=["high_surrogate", "low_surrogate", "high_then_low"],
)
def test_an_unpaired_surrogate_is_a_422_not_a_tokenizer_500(classifier, case, text):
    r"""The defect this file exists to pin.

    ``{"text": "add a task \ud800 tomorrow"}`` is legal JSON. Python decodes it
    into a ``str`` that satisfies every length and emptiness check, and the Rust
    tokenizer then refuses the encode with a ``TypeError`` about its own argument
    type — which the classifier used to wrap into a 500. A caller cannot express
    this text in a way any client would catch before sending it, so the server
    has to.
    """
    with pytest.raises(InvalidUtteranceError) as raised:
        classifier.predict(text)

    assert raised.value.status_code == 422
    assert raised.value.details["reason"] == "unencodable"


def test_an_unpaired_surrogate_survives_json_parsing_so_the_check_is_load_bearing():
    """Why the domain check exists rather than a schema validator.

    If the round trip through JSON were lossy the schema would never see the
    surrogate and the classifier's check would be unreachable. It is not lossy.
    """
    wire = json.dumps({"text": "add a task " + chr(0xD800) + " tomorrow"}).encode("utf-8")
    decoded = json.loads(wire)["text"]

    assert chr(0xD800) in decoded
    with pytest.raises(UnicodeEncodeError):
        decoded.encode("utf-8")


def test_credential_shaped_text_is_refused_without_quoting_it(classifier):
    """A pasted key is refused, and the refusal does not become a second copy.

    The screen reuses ``ml.validation.find_credential``, so the detector under
    test here is the one Phase 10 wrote and ``tests/test_ml_secrets.py`` holds
    to account for — this asserts the *classifier's* half of it.
    """
    text = "deploy with ghp_" + "a" * 36

    with pytest.raises(InvalidUtteranceError) as raised:
        classifier.predict(text)

    assert raised.value.details["reason"] == "credential_shaped"
    assert raised.value.details["kind"] == "github_token"
    assert "ghp_" not in raised.value.message


async def test_the_unpaired_surrogate_reaches_the_caller_as_422_over_http(
    authorised_client, broken_inference_runtime
):
    r"""The HTTP edge answers 422, and both layers contributing to that are named.

    Pydantic's own ``str`` validator refuses the value first, with a
    ``string_unicode`` error — so this route was never a 500. The test therefore
    pins the *boundary contract*, not the domain fix: the three tests above are
    what fail without :meth:`IntentClassifier._validate`'s check, because they
    reach the classifier without pydantic in between. This one is here so that
    the claim "an unpaired surrogate is a clean 4xx over HTTP" is asserted rather
    than inferred from the domain tests.

    Sent as a pre-encoded body rather than through httpx's ``json=``: that helper
    serialises with ``ensure_ascii=False`` and cannot put a lone surrogate on the
    wire at all. ``json.dumps``'s default is ``ensure_ascii=True``, which emits
    the six ASCII bytes ``\ud800`` and is what a default-configured client sends.
    """
    body = json.dumps({"text": "add a task " + chr(0xD800) + " tomorrow"}).encode("utf-8")
    assert b"\\ud800" in body

    response = await authorised_client.post(
        "/api/v1/ml/route", content=body, headers={"content-type": "application/json"}
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"


async def test_a_ten_thousand_word_paste_answers_422_over_http(
    authorised_client, broken_inference_runtime
):
    response = await authorised_client.post(
        "/api/v1/ml/route", json={"text": " ".join(["migration"] * 10_000)}
    )

    assert response.status_code == 422, response.text


async def test_an_empty_utterance_answers_422_over_http(authorised_client, serving_runtime):
    response = await authorised_client.post("/api/v1/ml/route", json={"text": ""})

    assert response.status_code == 422, response.text


async def test_a_credential_shaped_utterance_answers_422_over_http(
    authorised_client, broken_inference_runtime
):
    """The screen lives in the real classifier, so this uses the real one.

    ``broken_inference_runtime`` is not a misnomer here: the text is refused at
    validation, so the model that would otherwise explode is never reached, and a
    500 would mean the screen failed to fire.
    """
    response = await authorised_client.post(
        "/api/v1/ml/route", json={"text": "deploy with ghp_" + "a" * 36}
    )

    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Nothing the user wrote reaches a log record
# ---------------------------------------------------------------------------


async def test_a_served_prediction_never_writes_the_utterance_to_a_record(
    authorised_client, serving_runtime, caplog
):
    """The assertion Phase 12 makes in the browser, made here on the server.

    Searching the formatted records rather than the source, so it stays true
    across a refactor of ``log_event``'s arguments, and so a future field added
    to ``ml_route_served`` carrying the text is caught the day it is added.
    """
    caplog.set_level(logging.INFO)
    _runtime, classifier = serving_runtime
    text = f"add a task called {CANARY} for friday"

    response = await authorised_client.post("/api/v1/ml/route", json={"text": text})
    assert response.status_code == 200, response.text
    assert classifier.seen == [text], "the request never reached the classifier"

    served = [record for record in caplog.records if record.getMessage() == "ml_route_served"]
    assert served, [record.getMessage() for record in caplog.records]
    for record in served:
        rendered = " ".join(f"{key}={value}" for key, value in record.__dict__.items())
        assert CANARY not in rendered


async def test_a_failed_prediction_never_writes_the_utterance_to_a_record(
    authorised_client, broken_inference_runtime, caplog
):
    """The 500 path is the one that reaches for an exception.

    An exception carries whatever the tokenizer put in its message.
    """
    caplog.set_level(logging.INFO)

    response = await authorised_client.post(
        "/api/v1/ml/route", json={"text": f"add a task called {CANARY}"}
    )

    assert response.status_code == 500, response.text
    failures = [record for record in caplog.records if record.getMessage() == "ml.inference_failed"]
    assert failures, [record.getMessage() for record in caplog.records]
    for record in failures:
        rendered = " ".join(f"{key}={value}" for key, value in record.__dict__.items())
        assert CANARY not in rendered


async def test_the_log_scanner_would_notice_the_utterance_if_it_were_there(caplog):
    """Proof the two tests above are not vacuously green.

    A scanner that searched for a string no log line could contain would pass
    forever. This one emits the canary in the shape a real leak would take — a
    ``text`` field on the record — and the same search finds it.
    """
    caplog.set_level(logging.INFO)
    logging.getLogger("app.ml.probe").log(logging.INFO, "ml_route_served", extra={"text": CANARY})

    served = [record for record in caplog.records if record.getMessage() == "ml_route_served"]
    assert served
    rendered = " ".join(f"{key}={value}" for key, value in served[0].__dict__.items())
    assert CANARY in rendered


# ---------------------------------------------------------------------------
# An inference failure is a clean 500
# ---------------------------------------------------------------------------


async def test_a_torch_level_failure_inside_the_forward_pass_is_a_clean_500(
    authorised_client, broken_inference_runtime
):
    response = await authorised_client.post("/api/v1/ml/route", json={"text": "add a task"})

    assert response.status_code == 500, response.text
    body = response.json()
    assert body["error"]["code"] == "internal_error"


async def test_the_500_does_not_echo_the_exception_or_the_implementation(
    authorised_client, broken_inference_runtime
):
    """The cause stays server-side.

    ``_ExplodingWeights`` raises ``RuntimeError("inference reached the model
    (wanted 'model')")``; none of that may appear in the body, and neither may a
    torch frame, a checkpoint path or a traceback marker.
    """
    response = await authorised_client.post("/api/v1/ml/route", json={"text": "add a task"})

    raw = response.text
    assert "RuntimeError" not in raw
    assert "inference reached the model" not in raw
    assert "Traceback" not in raw
    assert str(BACKEND_ROOT) not in raw
    assert "torch" not in raw


async def test_the_cause_is_recorded_server_side_with_its_type(
    authorised_client, broken_inference_runtime, caplog
):
    caplog.set_level(logging.INFO)

    await authorised_client.post("/api/v1/ml/route", json={"text": "add a task"})

    failures = [record for record in caplog.records if record.getMessage() == "ml.inference_failed"]
    assert len(failures) == 1, [record.getMessage() for record in caplog.records]
    assert failures[0].error == "RuntimeError"
    assert failures[0].characters == len("add a task")


# ---------------------------------------------------------------------------
# GET /ml/status is a complete and accurate statement of what is loaded
# ---------------------------------------------------------------------------


async def test_the_status_endpoint_reports_the_full_identity(authorised_client, serving_runtime):
    response = await authorised_client.get("/api/v1/ml/status")

    assert response.status_code == 200, response.text
    model = response.json()["model"]
    assert set(model) == {
        "base_model",
        "architecture",
        "device",
        "label_count",
        "max_sequence_length",
        "parameter_count",
        "checkpoint",
        "load_seconds",
    }
    assert model["base_model"] == "microsoft/deberta-v3-base"
    assert model["label_count"] == len(label_map())
    assert model["max_sequence_length"] == TRAINED_WINDOW
    assert model["parameter_count"] > 0


async def test_the_status_endpoint_publishes_the_same_taxonomy_the_router_uses(
    authorised_client, serving_runtime
):
    response = await authorised_client.get("/api/v1/ml/status")

    published = [entry["intent"] for entry in response.json()["intents"]]
    assert published == list(label_map())


async def test_a_degraded_runtime_reports_the_reason_and_no_model(app, authorised_client, settings):
    runtime = MLRuntime(settings)
    missing = object()
    previous = getattr(app.state, "ml_runtime", missing)
    app.state.ml_runtime = runtime
    try:
        response = await authorised_client.get("/api/v1/ml/status")
    finally:
        app.state.ml_runtime = None if previous is missing else previous

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["available"] is False
    assert body["unavailable_reason"] == MLRuntime.REASON_UNLOADED
    assert body["model"] is None
