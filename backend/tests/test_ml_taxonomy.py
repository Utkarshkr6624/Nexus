"""The fourteen-class intent taxonomy the routing classifier predicts.

The label set is the product's routing surface, not a benchmark's: every class
names a router that exists in `backend/app/api/v1` or the abstention fallback.
That makes the taxonomy's *shape* load-bearing rather than descriptive. Fourteen
classes with exactly two of them routed to the 8B model is what keeps
"deterministic before learned" a property of the label set rather than a hope —
widen the large-model classes and every "mark it done" pays generation latency
to be answered by a router that already exists.

The tests below therefore pin the count, the uniqueness, and the *kind* each
class is allowed to reach, rather than the wording of any description. Adding an
intent is a product decision that should fail here loudly; silently reordering
`INTENT_NAMES` would renumber every class index in every manifest already
written.
"""

from __future__ import annotations

import json

import pytest

from ml.datasets.schema import DataValidationError
from ml.datasets.taxonomy import (
    FALLBACK_INTENTS,
    INTENT_NAMES,
    INTENT_SPECS,
    LARGE_MODEL_INTENTS,
    ROUTER_INTENTS,
    TAXONOMY_VERSION,
    UNKNOWN_INTENT,
    DestinationKind,
    Intent,
    intent_spec,
    is_valid_intent,
    taxonomy_as_dict,
)


def test_there_are_exactly_fourteen_intents():
    assert len(Intent) == 14
    assert len(INTENT_SPECS) == 14
    assert len(INTENT_NAMES) == 14


def test_intent_names_are_unique_and_match_the_enum():
    assert len(set(INTENT_NAMES)) == 14
    assert tuple(str(intent) for intent in Intent) == INTENT_NAMES
    assert set(INTENT_NAMES) == {str(intent) for intent in Intent}


def test_every_spec_is_registered_under_its_own_intent():
    assert {str(spec.intent) for spec in INTENT_SPECS} == set(INTENT_NAMES)
    assert len({spec.intent for spec in INTENT_SPECS}) == 14


@pytest.mark.parametrize("spec", INTENT_SPECS, ids=lambda spec: str(spec.intent))
def test_every_spec_carries_a_description_examples_and_keywords(spec):
    """An intent with no examples cannot be generated for; one with no keywords cannot be audited."""
    assert spec.description.strip()
    assert len(spec.description) > 40
    assert spec.destination.strip()
    assert len(spec.examples) >= 3
    assert all(example.strip() for example in spec.examples)
    assert len(spec.keywords) >= 5
    assert all(keyword.strip() for keyword in spec.keywords)
    assert spec.destination_kind in DestinationKind


def test_large_model_intents_are_exactly_code_assist_and_deep_reasoning():
    """Widening this set is the one change that would make the 8B model the default path."""
    assert frozenset({Intent.CODE_ASSIST, Intent.DEEP_REASONING}) == LARGE_MODEL_INTENTS
    assert {str(intent) for intent in LARGE_MODEL_INTENTS} == {"code_assist", "deep_reasoning"}


def test_the_three_destination_groups_partition_the_taxonomy():
    groups = LARGE_MODEL_INTENTS | ROUTER_INTENTS | FALLBACK_INTENTS

    assert len(groups) == 14
    assert frozenset() == ROUTER_INTENTS & LARGE_MODEL_INTENTS
    assert frozenset({Intent.OUT_OF_SCOPE}) == FALLBACK_INTENTS
    assert len(ROUTER_INTENTS) == 11
    assert set(Intent) == ROUTER_INTENTS | LARGE_MODEL_INTENTS | FALLBACK_INTENTS


def test_unknown_intent_is_the_abstention_class():
    """An uncertain prediction must land somewhere visible, not on a write surface."""
    assert UNKNOWN_INTENT is Intent.OUT_OF_SCOPE
    assert intent_spec(str(UNKNOWN_INTENT)).destination_kind is DestinationKind.FALLBACK
    assert intent_spec(str(UNKNOWN_INTENT)).destination == "abstain"


def test_router_intents_name_a_destination_that_names_a_router():
    for spec in INTENT_SPECS:
        if spec.destination_kind is not DestinationKind.ROUTER:
            continue
        assert spec.destination.startswith("api/v1/")


def test_intent_spec_resolves_every_name():
    for index, name in enumerate(INTENT_NAMES):
        assert intent_spec(name).intent is Intent(name)
        assert is_valid_intent(name)
        assert name == str(list(Intent)[index])


def test_intent_spec_refuses_an_unknown_name_rather_than_guessing():
    """Training on a coerced label teaches a class the runtime cannot execute."""
    with pytest.raises(DataValidationError, match="unknown intent"):
        intent_spec("complete_blocked_task")


def test_intent_spec_refuses_a_near_miss_name():
    with pytest.raises(DataValidationError, match="unknown intent"):
        intent_spec("task_mangement")


def test_is_valid_intent_is_false_for_anything_outside_the_taxonomy():
    assert not is_valid_intent("complete_blocked_task")
    assert not is_valid_intent("")
    assert not is_valid_intent("TASK_MANAGE")


def test_taxonomy_as_dict_is_json_ready_and_self_consistent():
    payload = taxonomy_as_dict()

    assert payload["taxonomy_version"] == TAXONOMY_VERSION
    assert payload["unknown_intent"] == str(UNKNOWN_INTENT)
    assert json.loads(json.dumps(payload)) == payload

    listed = [entry["intent"] for entry in payload["intents"]]
    assert listed == list(INTENT_NAMES)

    destinations = payload["destinations"]
    assert destinations["large_model"] == ["code_assist", "deep_reasoning"]
    assert len(destinations["router"]) == 11
    assert destinations["fallback"] == ["out_of_scope"]
    assert sum(len(names) for names in destinations.values()) == 14


def test_taxonomy_as_dict_is_stable_across_calls():
    assert taxonomy_as_dict() == taxonomy_as_dict()
