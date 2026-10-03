"""The inference interface: raw user text in, one :class:`IntentPrediction` out.

This is the only place in NEXUS that runs the trained model. It deliberately
knows nothing about thresholds-as-policy, destinations, services or HTTP: it
turns a string into a probability distribution over the fourteen intents and
reports which one won, how sure the model was, and what it nearly said instead.
Everything about *acting* on that answer is another module's job, which is what
makes this one testable against the checkpoint alone.

**The text is not transformed.** Phase 10 trained on the raw ``text`` values of
the routing dataset; :func:`ml.preprocessing.normalize.normalize_text` was used
to detect duplicate rows and near-duplicate leakage, and never fed to the
tokenizer. Lowercasing, stripping punctuation or collapsing whitespace here
would look like tidying and is in fact a distribution shift — the model was
fitted on capitalised, punctuated, occasionally mistyped requests and would be
scoring something it has never seen. So ``predict`` hands the caller's string
straight to the tokenizer.

**Tokenisation matches training exactly**: ``truncation=True`` at the trained
context length, and ``token_type_ids`` dropped because DeBERTa-v3 declares no
such input (``type_vocab_size: 0``) and passing it is "accepted and ignored" —
the training code pops it, so this does too.

**One loaded model serves concurrent requests, so the forward pass is serialised
by a lock.** The model is 703 MiB of shared mutable state and the requests arrive
from FastAPI's thread pool; holding a lock across tokenise+forward bounds the
resident set to one in-flight forward pass instead of letting N threads each
allocate their own activation buffers, and it makes ``latency_ms`` mean something.
The lock is deliberately narrow: nothing is logged, no caller state is touched
and no :class:`IntentPrediction` is built while it is held, because a lock held
across logging is a lock that serialises the whole request queue behind a JSON
formatter. Time spent waiting for it is also excluded from ``latency_ms`` —
queueing delay is a capacity fact, not this classifier's inference cost.

**No per-request state is ever stored on the instance.** Everything
:func:`predict` computes lives in locals for the duration of the call, so two
threads predicting two utterances cannot observe each other's tensor.

**The user's words never reach a log.** Predictions are logged as intent,
confidence, latency and character count; a failure inside torch is logged as the
exception *type*. The utterance itself is the one thing in this system that must
not be copied anywhere durable.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from typing import Any

from app.core.exceptions import NexusError
from app.core.logging import get_logger, log_event
from app.ml.exceptions import (
    InferenceError,
    InvalidUtteranceError,
    MLUnavailableError,
)
from app.ml.model_loader import LoadedModel
from app.ml.schemas import IntentPrediction, ModelIdentity
from ml.validation import find_credential

__all__ = [
    "DEFAULT_ALTERNATIVE_COUNT",
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "MAX_BATCH_SIZE",
    "MAX_UTTERANCE_CHARACTERS",
    "IntentClassifier",
]

logger = get_logger(__name__)

#: Confidence below which a prediction should not be acted on without asking.
#: The classifier does not enforce it — :class:`~app.ml.schemas.RoutingDecision`
#: does — but it is carried here so a caller constructing a classifier directly
#: has a value to compare against instead of inventing one. The trained model
#: scores 0.974 accuracy on a balanced held-out split, so a floor meaningfully
#: below 1.0 is what separates "this is a routing decision" from "this is a
#: coin I flipped".
DEFAULT_CONFIDENCE_THRESHOLD = 0.75

#: How many runner-up classes :func:`IntentClassifier.predict` reports. Three is
#: enough for a "did you mean…?" prompt without turning every prediction into a
#: fourteen-way table the caller must render.
DEFAULT_ALTERNATIVE_COUNT = 3

#: Characters, not tokens. The tokeniser truncates at 128 subwords, so a longer
#: request cannot change what the model sees — but an unbounded string is still
#: work the edge accepted, memory the tokeniser held, and a log line the router
#: must never be tempted to quote. This is well above any utterance NEXUS has a
#: surface for, so it rejects abuse rather than real requests.
MAX_UTTERANCE_CHARACTERS = 4000

#: Ceiling on :func:`IntentClassifier.predict_many`. Each item is a separate
#: forward pass through a 183M-parameter encoder, so an unbounded batch is a way
#: for one caller to occupy the serialised model for seconds.
MAX_BATCH_SIZE = 32


class IntentClassifier:
    """A loaded checkpoint, callable on raw text.

    Instances are cheap: they hold no weights of their own, only a reference to a
    shared :class:`~app.ml.model_loader.LoadedModel` and the lock that serialises
    access to it. One loaded model, one classifier, many requests.

    The instance is safe to share across threads. Every request's intermediates
    are local; the only shared mutable state is the model, and the forward pass
    is held under :attr:`_lock`.

    Args:
        loaded: A checkpoint already on a device, from
            :func:`app.ml.model_loader.load_model`.
        threshold: The confidence floor callers should compare against. It is
            reported here for convenience; this class never rejects a prediction
            on confidence grounds, because deciding that a 0.6 is "uncertain"
            is a routing policy rather than a property of the model.
        alternative_count: How many runner-up classes each prediction carries.
        reject_credentials: Whether to refuse credential-shaped text before it
            reaches the tokenizer. See :meth:`_validate`.

    Raises:
        ValueError: ``threshold`` is outside ``[0, 1]`` or
            ``alternative_count`` is negative. Both are configuration mistakes
            worth surfacing at construction rather than at the first request.
    """

    def __init__(
        self,
        loaded: LoadedModel,
        *,
        threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        alternative_count: int = DEFAULT_ALTERNATIVE_COUNT,
        reject_credentials: bool = True,
    ) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"threshold must be a probability, got {threshold!r}")
        if alternative_count < 0:
            raise ValueError(f"alternative_count cannot be negative, got {alternative_count!r}")

        self._loaded = loaded
        self._threshold = float(threshold)
        self._alternative_count = int(alternative_count)
        self._max_sequence_length = loaded.max_sequence_length
        self._reject_credentials = bool(reject_credentials)
        self._lock = threading.Lock()

    @property
    def threshold(self) -> float:
        """The confidence floor carried for callers that want one."""
        return self._threshold

    @property
    def label_count(self) -> int:
        """How many classes this classifier can predict."""
        return len(self._loaded.id2label)

    @property
    def max_sequence_length(self) -> int:
        """The trained context length utterances are truncated to."""
        return self._max_sequence_length

    @property
    def identity(self) -> ModelIdentity | None:
        """The loaded model's :class:`~app.ml.schemas.ModelIdentity`.

        ``None`` after :meth:`close`. Exposed so a diagnostics endpoint can report
        the device, parameter count and load cost without the loader being part of
        its import graph.
        """
        loaded = self._loaded
        return loaded.identity if loaded is not None else None

    def predict(self, text: str) -> IntentPrediction:
        """Classify one utterance.

        The full contract: the text is validated, tokenised raw at the trained
        context length without padding, run under ``torch.inference_mode()``, and
        softmaxed into a distribution over the fourteen intents. ``confidence`` is
        that softmax probability, unrounded — rounding belongs to whatever
        renders it.

        Args:
            text: The user's utterance, exactly as they wrote it.

        Returns:
            The winning intent with its probability, the runner-up classes, a
            ``truncated`` flag saying whether the text was cut to the trained
            context length, and the wall-clock cost of the call.

        Raises:
            InvalidUtteranceError: The text is not a string, is empty or
                whitespace-only, or exceeds :data:`MAX_UTTERANCE_CHARACTERS`. The
                ``details`` carry the limit and the length — never the text.
            InferenceError: The forward pass failed for a reason that is not the
                caller's fault. Logged as an exception type, not as the utterance.
            MLUnavailableError: The classifier has been closed.
        """
        self._validate(text)

        # Snapshot the loaded model rather than reading self._loaded under the
        # lock: a concurrent close() either ran before this line, in which case
        # the check below sees None, or runs after it, in which case this
        # reference keeps the model alive for the whole call.
        loaded = self._loaded
        if loaded is None:
            raise MLUnavailableError(
                "the intent classifier has been unloaded and cannot classify until it is "
                "loaded again"
            )

        try:
            with self._lock:
                started = time.perf_counter()
                # The truncation flag has to be measured *before* truncation, and
                # the only honest way to measure it is to encode without a limit.
                # A second encode costs microseconds against a 183M-parameter
                # forward pass, which is a far better trade than hand-rolling
                # truncation and hoping it matches what the tokenizer does.
                token_count = len(
                    loaded.tokenizer(text, truncation=False, add_special_tokens=True)["input_ids"]
                )
                encoded = loaded.tokenizer(
                    text,
                    truncation=True,
                    max_length=self._max_sequence_length,
                    padding=False,
                    return_tensors="pt",
                )
                # DeBERTa-v3 declares no token_type_ids; the tokenizer emits them
                # anyway and the model ignores them. Dropping them keeps the
                # serving input identical to the training input, where the
                # training code popped the same key for the same reason.
                encoded.pop("token_type_ids", None)
                inputs = {name: tensor.to(loaded.device) for name, tensor in encoded.items()}
                with loaded.torch_module.inference_mode():
                    logits = loaded.model(**inputs).logits
                probabilities = loaded.torch_module.softmax(logits, dim=-1)[0]
                latency_ms = (time.perf_counter() - started) * 1000.0
        except InvalidUtteranceError:
            raise
        except NexusError:
            # A domain error raised inside the lock is already the right shape for
            # the client; wrapping it in InferenceError would turn a 422 into a 500.
            raise
        except Exception as exc:
            log_event(
                logger,
                logging.ERROR,
                "ml.inference_failed",
                error=type(exc).__name__,
                characters=len(text),
                label_count=len(loaded.id2label),
            )
            raise InferenceError() from exc

        return self._to_prediction(
            loaded,
            probabilities,
            latency_ms=latency_ms,
            truncated=token_count > self._max_sequence_length,
        )

    def predict_many(self, texts: Sequence[str]) -> list[IntentPrediction]:
        """Classify several utterances.

        A loop over :meth:`predict` on purpose. A separately batched forward pass
        would be faster per item, but it would be a *second* tokenisation and
        scoring path, and the two would drift the first time one of them was
        fixed and the other was not. Correctness of the served answer is worth
        more here than the throughput of a path only tests use.

        Args:
            texts: The utterances to classify, at most :data:`MAX_BATCH_SIZE`.

        Returns:
            One prediction per input, in the same order.

        Raises:
            InvalidUtteranceError: More than :data:`MAX_BATCH_SIZE` items were
                supplied, or any individual utterance is invalid. ``details``
                give the bound, never the text.
        """
        if len(texts) > MAX_BATCH_SIZE:
            raise InvalidUtteranceError(
                "too many utterances for one batch",
                details={"max_items": MAX_BATCH_SIZE, "items": len(texts)},
            )
        return [self.predict(text) for text in texts]

    def close(self) -> None:
        """Drop the reference to the loaded model so its memory can be freed.

        A 703 MiB encoder is not collected while anything still points at it, so
        a reload that wraps this call is what actually returns the memory. The
        lock is taken because closing during an in-flight forward pass would free
        the tensors out from under a thread that is still reading them.

        Idempotent, and safe to call on a classifier that is no longer serving.
        """
        with self._lock:
            self._loaded = None

    def _validate(self, text: str) -> None:
        """Reject text the classifier should never be asked about.

        Validation lives in the domain rather than in the route schema so that
        every caller — the API, the batch path, a script — is held to the same
        rule.

        Args:
            text: The caller's utterance.

        Raises:
            InvalidUtteranceError: The value is not a string, is blank, is longer
                than :data:`MAX_UTTERANCE_CHARACTERS`, or is credential-shaped
                while screening is on. The message and ``details`` never quote the
                text itself.
        """
        if not isinstance(text, str):
            raise InvalidUtteranceError(
                "the utterance must be a string",
                details={"type": type(text).__name__},
            )
        if not text.strip():
            raise InvalidUtteranceError(
                "the utterance is empty",
                details={"reason": "blank", "max_characters": MAX_UTTERANCE_CHARACTERS},
            )
        if len(text) > MAX_UTTERANCE_CHARACTERS:
            raise InvalidUtteranceError(
                "the utterance is longer than the classifier accepts",
                details={
                    "max_characters": MAX_UTTERANCE_CHARACTERS,
                    "characters": len(text),
                },
            )
        if self._reject_credentials:
            self._reject_credential_shaped(text)

    @staticmethod
    def _reject_credential_shaped(text: str) -> None:
        """Refuse text carrying something that looks like a live secret.

        The screen reuses :func:`ml.validation.find_credential`, which Phase 10
        wrote and tested for exactly this shape of untrusted input and which
        reports the *kind* of credential rather than the value — so a refusal can
        name what tripped it without the refusal itself becoming a second copy of
        the secret.

        It is on by default because the alternative is worse than the false
        positives it costs: a user pasting a live key into a chat box has already
        lost control of that key, and classifying the request only makes the loss
        quieter. A user who genuinely means "change my password to…" can turn the
        screen off with ``ML_REJECT_CREDENTIALS=false``.

        Args:
            text: The caller's utterance, already known to be a non-blank string.

        Raises:
            InvalidUtteranceError: The text contains credential-shaped content.
        """
        kind = find_credential(text)
        if kind is None:
            return
        raise InvalidUtteranceError(
            "the utterance contains credential-shaped text and was not classified",
            details={"reason": "credential_shaped", "kind": kind},
        )

    def _to_prediction(
        self,
        loaded: LoadedModel,
        probabilities: Any,
        *,
        latency_ms: float,
        truncated: bool,
    ) -> IntentPrediction:
        """Turn a probability row into the value the rest of NEXUS consumes.

        Args:
            loaded: The checkpoint the row came from, so the caller and this
                method resolve labels against the same snapshot.
            probabilities: The 1-D softmax row, on whatever device the model ran.
            latency_ms: The measured cost of tokenise and forward.
            truncated: Whether the utterance was cut to the context length.

        Returns:
            The prediction, with the winner first and the runner-ups in descending
            order.

        Raises:
            InferenceError: The row does not have one entry per known class, which
            would mean the loaded head and the validated label order disagree — the
            one thing the load-time check exists to make impossible.
        """
        torch_module = loaded.torch_module
        label_count = len(loaded.id2label)
        if probabilities.shape[-1] != label_count:
            raise InferenceError(
                "the loaded model produced a score for an unexpected number of classes"
            )

        wanted = min(self._alternative_count + 1, label_count)
        scores, indices = torch_module.topk(probabilities, wanted)
        best_index = int(indices[0])
        alternatives = tuple(
            (loaded.id2label[int(index)], float(score))
            for score, index in zip(scores[1:], indices[1:], strict=True)
        )
        return IntentPrediction(
            intent=loaded.id2label[best_index],
            confidence=float(scores[0]),
            alternatives=alternatives,
            truncated=truncated,
            latency_ms=latency_ms,
        )
