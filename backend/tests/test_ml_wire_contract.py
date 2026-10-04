"""The ML wire contract, pinned for the frontend that reads it.

The frontend types these responses by convention — `frontend/src/types/ml.ts` is
a hand-written mirror, because TypeScript cannot import a Pydantic model. A
response body is only typed by convention, so **a mismatch between the two fails
at runtime and not at compile time**: the field reads `undefined`, and a
diagnostics panel silently reports an absent classifier as a working one.

That is not hypothetical. Phase 12's first draft of the status type declared
`reason` and `detail` where this endpoint has always published a single
`unavailable_reason`, and invented a `taxonomy_version`'s absence — so it would
have rendered every healthy deployment as broken. Nothing caught it except a
human reading the two files side by side.

So the field sets are pinned here. Adding, renaming or removing a field on
either side fails this test, which is the point: the change *should* be
deliberate, and it should be a change to both files in the same commit.
"""

from __future__ import annotations

from app.schemas.ml import (
    IntentRouteRead,
    MLStatusRead,
    ModelIdentityRead,
    RoutingDecisionRead,
    ServiceTargetRead,
)

#: Mirrors the interfaces in `frontend/src/types/ml.ts`. Keep the two in step.
STATUS_FIELDS = frozenset(MLStatusRead.model_fields)
IDENTITY_FIELDS = frozenset(ModelIdentityRead.model_fields)
INTENT_ROUTE_FIELDS = frozenset(IntentRouteRead.model_fields)
DECISION_FIELDS = frozenset(RoutingDecisionRead.model_fields)
SERVICE_TARGET_FIELDS = frozenset(ServiceTargetRead.model_fields)


def test_the_status_response_publishes_exactly_the_fields_the_frontend_declares() -> None:
    assert {
        "enabled",
        "available",
        "unavailable_reason",
        "model",
        "threshold",
        "taxonomy_version",
        "intents",
    } == STATUS_FIELDS


def test_the_unavailable_reason_is_nullable_and_not_split_in_two() -> None:
    """The reason is one field, not a code plus a message.

    A caller branches on ``available`` and reads ``unavailable_reason`` as an
    opaque term. Splitting it into a code and a prose detail would invite a
    client to display the detail as an error message, and the detail carries a
    filesystem path when the checkpoint is missing.
    """
    assert "reason" not in STATUS_FIELDS
    assert "detail" not in STATUS_FIELDS

    reason = MLStatusRead.model_fields["unavailable_reason"]
    assert reason.default is None, "an available classifier must not carry a reason"


def test_the_model_identity_publishes_exactly_the_fields_the_frontend_declares() -> None:
    assert {
        "base_model",
        "architecture",
        "device",
        "label_count",
        "max_sequence_length",
        "parameter_count",
        "checkpoint",
        "load_seconds",
    } == IDENTITY_FIELDS


def test_an_intent_route_carries_the_service_and_the_entrypoint() -> None:
    """Both are nullable, and both being null is meaningful.

    A generation intent has no service and no first call; that absence is the
    answer, not a hole in the response.
    """
    assert {
        "intent",
        "description",
        "destination",
        "destination_kind",
        "service",
        "entrypoint",
    } == INTENT_ROUTE_FIELDS
    assert IntentRouteRead.model_fields["service"].default is None
    assert IntentRouteRead.model_fields["entrypoint"].default is None


def test_the_routing_decision_publishes_exactly_the_fields_the_frontend_declares() -> None:
    assert {
        "intent",
        "confidence",
        "threshold",
        "status",
        "destination",
        "destination_kind",
        "target",
        "reason",
        "alternatives",
    } == DECISION_FIELDS


def test_the_service_target_carries_the_three_fields_a_caller_dispatches_on() -> None:
    assert {"service", "module", "entrypoint"} == SERVICE_TARGET_FIELDS
