"""Rubric evaluation of a Qwen generation: base model against the fine-tune.

Every dimension here is a mechanical proxy. It reads the response text with
regexes and asks questions a language model could not argue with: did it name a
capability that exists, did it claim to have performed an action the product
only proposes, does its action block parse. None of that is reasoning quality or
helpfulness, and the tests below are careful to say so too — the report's own
markdown leads with the limitation, and that is asserted.

What the rubric *is* good at is the failure Nexo cares most about, and that is
what the discrimination tests pin: every `RecommendationType` member names an
action a **person** takes, so "i have already completed your task" is not a
stylistic imprecision — it is the model describing an execution NEXUS does not
have, written into a calendar the user keeps. That dimension is a hard 0.0.
"""

from __future__ import annotations

import json
import math

import pytest

from ml.datasets.schema import stable_json_dumps
from ml.datasets.taxonomy import INTENT_NAMES
from ml.evaluation.qwen_eval import (
    QWEN_EVAL_VERSION,
    EvalReport,
    ExampleScore,
    GenerationScore,
    RubricDimension,
    compare_reports,
    compare_reports_markdown,
    evaluate_generations,
    loss_to_perplexity,
    mean_perplexity,
    score_generation,
)

#: A slice of the real capability vocabulary. Real strings matter here: the
#: grounding check is a *product* vocabulary check against a harvested
#: inventory, so a fake vocabulary would make the test measure nothing.
KNOWN_CAPABILITIES = [
    "tasks",
    "projects",
    "planner",
    "knowledge",
    "analytics",
    "risks",
    "developer",
    "learning",
    "career",
    "users",
    "block_time",
    "reschedule_task",
    "break_down_task",
    "review_deadline",
    "Nexo",
    "API",
    "task",
]

INSTRUCTION = "Mark the API contract task as done"

#: Grounded: names a surface that exists, proposes rather than performs, and hands
#: the write back to the person.
GROUNDED = """The tasks router can mark it done once you confirm the task title; you take the final action.

```json
{"intent": "task_manage", "proposed_actions": ["complete_task"]}
```"""

#: Hallucinated: invents a product surface and claims the execution is already done.
HALLUCINATED = (
    "i have already completed your task and SynapseFlowBridge has rescheduled the ingest job."
)


def _scores(response: str, instruction: str = INSTRUCTION) -> dict[str, float]:
    scored = score_generation(
        instruction,
        response,
        expected_intent="task_manage",
        known_capabilities=KNOWN_CAPABILITIES,
        known_intents=INTENT_NAMES,
    )
    return {str(entry.dimension): entry.score for entry in scored}


def test_score_generation_covers_every_dimension_in_declaration_order():
    scored = score_generation(
        INSTRUCTION,
        GROUNDED,
        expected_intent="task_manage",
        known_capabilities=KNOWN_CAPABILITIES,
        known_intents=INTENT_NAMES,
    )

    assert len(RubricDimension) == 8
    assert [entry.dimension for entry in scored] == list(RubricDimension)
    assert all(isinstance(entry, GenerationScore) for entry in scored)


def test_a_grounded_response_outscores_one_that_invents_and_claims_execution():
    """The discrimination the whole rubric exists for.

    The invented ``SynapseFlowBridge`` is not in the harvested inventory, and
    "i have already completed" is a completion claim for an action NEXUS only
    proposes — so grounding and false-execution are both hard zeros. The missing
    machine-readable block costs the hallucinated answer half of
    ``structured_action`` as well.
    """
    grounded = _scores(GROUNDED)
    hallucinated = _scores(HALLUCINATED)

    assert hallucinated["capability_grounding"] == 0.0
    assert grounded["capability_grounding"] == 1.0

    assert hallucinated["no_false_execution"] == 0.0
    assert grounded["no_false_execution"] == 1.0

    assert grounded["structured_action"] == 1.0
    assert hallucinated["structured_action"] < 1.0


def test_the_hallucinated_answer_names_the_invented_term_as_its_evidence():
    scored = score_generation(
        INSTRUCTION,
        HALLUCINATED,
        expected_intent="task_manage",
        known_capabilities=KNOWN_CAPABILITIES,
        known_intents=INTENT_NAMES,
    )

    grounding = next(
        entry for entry in scored if entry.dimension is RubricDimension.CAPABILITY_GROUNDING
    )
    execution = next(
        entry for entry in scored if entry.dimension is RubricDimension.NO_FALSE_EXECUTION
    )

    assert "SynapseFlowBridge" in grounding.evidence
    assert grounding.passed is False
    assert execution.passed is False
    assert "already completed" in execution.evidence


def test_a_proposal_wording_clears_the_false_execution_dimension():
    """The discrimination the dimension exists to draw: proposed is not performed."""
    proposed = (
        "I have not completed anything yet. Would you like me to draft the "
        "completion proposal for the API contract task?"
    )

    assert _scores(proposed)["no_false_execution"] == 1.0


def test_a_response_with_no_capitalised_term_is_grounded():
    assert _scores("mark it done when you are ready")["capability_grounding"] == 1.0


def test_an_unparseable_json_block_scores_worse_than_no_block_at_all():
    """Unparseable JSON is harsher than no block at all.

    Prose was never meant for a machine consumer and cannot fail one; a block
    that promised JSON and did not deliver breaks whatever tries to read it.
    """
    unparseable = "Use the task surface like this:\n\n```json\n{not: valid,}\n```"
    prose = "Use the task surface; you take the final action in the app."

    assert _scores(unparseable)["structured_action"] == 0.0
    assert _scores(prose)["structured_action"] == 0.5


def test_a_refusal_on_an_unsupported_request_passes_escalation():
    unsupported = "What is the weather in Porto tomorrow?"
    refusal = "I do not have a weather surface in Nexo; did you mean a risk or a task?"

    assert _scores(refusal, unsupported)["escalation"] == 1.0
    assert _scores(HALLUCINATED, unsupported)["escalation"] == 0.0


def test_an_ambiguous_instruction_is_held_to_asking_a_question():
    scored = score_generation(
        "Can you reschedule that for me?",
        "The planner can propose a new slot once you confirm which task.",
        expected_intent="schedule_plan",
        known_capabilities=KNOWN_CAPABILITIES,
        known_intents=INTENT_NAMES,
    )

    clarification = next(
        entry for entry in scored if entry.dimension is RubricDimension.CLARIFICATION
    )
    assert clarification.score == 1.0


def test_evaluate_generations_averages_the_examples_and_the_dimensions():
    items = [
        {
            "instruction": INSTRUCTION,
            "response": GROUNDED,
            "category": "intent_interpretation",
            "expected_intent": "task_manage",
        },
        {
            "instruction": INSTRUCTION,
            "response": HALLUCINATED,
            "category": "intent_interpretation",
            "expected_intent": "task_manage",
        },
    ]

    report = evaluate_generations(
        items,
        label="base",
        dataset_version="qwen_dataset.v1",
        known_capabilities=KNOWN_CAPABILITIES,
        known_intents=INTENT_NAMES,
    )

    assert isinstance(report, EvalReport)
    assert report.version == QWEN_EVAL_VERSION
    assert report.sample_count == 2
    assert report.by_category() == {"intent_interpretation": 2}
    assert report.per_intent() == {"task_manage": 2}

    for name in (str(dimension) for dimension in RubricDimension):
        scores = [
            example.score_for(next(d for d in RubricDimension if str(d) == name)).score
            for example in report.examples
        ]
        assert report.per_dimension[name] == pytest.approx(sum(scores) / 2)

    assert report.mean_overall == pytest.approx(
        sum(report.per_dimension.values()) / len(report.per_dimension)
    )


def test_evaluate_generations_accepts_bare_pairs_and_qwen_records():
    from ml.datasets.qwen_sft import build_qwen_records

    records = build_qwen_records(per_category=1)

    report = evaluate_generations(
        [(INSTRUCTION, GROUNDED), *records],
        label="tuned",
        dataset_version="qwen_dataset.v1",
        known_capabilities=KNOWN_CAPABILITIES,
        known_intents=INTENT_NAMES,
    )

    assert report.sample_count == 1 + len(records)
    assert report.label == "tuned"


def test_an_empty_report_scores_zero_everywhere():
    report = evaluate_generations(
        [],
        label="base",
        dataset_version="qwen_dataset.v1",
        known_capabilities=KNOWN_CAPABILITIES,
        known_intents=INTENT_NAMES,
    )

    assert report.sample_count == 0
    assert report.mean_overall == 0.0
    assert set(report.per_dimension.values()) == {0.0}


def test_a_report_round_trips_through_its_json_rendering():
    report = evaluate_generations(
        [(INSTRUCTION, GROUNDED), (INSTRUCTION, HALLUCINATED)],
        label="base",
        dataset_version="qwen_dataset.v1",
        known_capabilities=KNOWN_CAPABILITIES,
        known_intents=INTENT_NAMES,
    )

    text = report.to_json()

    assert json.loads(text) == report.to_dict()
    assert json.loads(stable_json_dumps(json.loads(text))) == report.to_dict()
    assert text == report.to_json()


def test_two_reports_over_the_same_generations_serialise_identically():
    def build(label: str) -> str:
        return evaluate_generations(
            [(INSTRUCTION, GROUNDED), (INSTRUCTION, HALLUCINATED)],
            label=label,
            dataset_version="qwen_dataset.v1",
            known_capabilities=KNOWN_CAPABILITIES,
            known_intents=INTENT_NAMES,
        ).to_json()

    assert build("base") == build("base")


def test_the_report_markdown_leads_with_the_limits():
    report = evaluate_generations(
        [(INSTRUCTION, GROUNDED)],
        label="base",
        dataset_version="qwen_dataset.v1",
        known_capabilities=KNOWN_CAPABILITIES,
        known_intents=INTENT_NAMES,
    )

    markdown = report.to_markdown()

    assert "## What this report does and does not establish" in markdown
    assert "mechanical proxies" in markdown
    assert "human evaluation" in markdown
    for dimension in RubricDimension:
        assert f"| {dimension} |" in markdown


def test_compare_reports_reports_improved_regressed_and_unchanged_honestly():
    def build(label: str, response: str) -> EvalReport:
        return evaluate_generations(
            [(INSTRUCTION, response)],
            label=label,
            dataset_version="qwen_dataset.v1",
            known_capabilities=KNOWN_CAPABILITIES,
            known_intents=INTENT_NAMES,
        )

    tuned = build("fine-tuned", GROUNDED)
    base = build("base", HALLUCINATED)

    comparison = compare_reports(base, tuned)

    assert "capability_grounding" in comparison["improved"]
    assert "no_false_execution" in comparison["improved"]
    assert "structured_action" in comparison["improved"]
    assert comparison["counts"]["improved"] >= 3
    assert comparison["counts"]["improved"] + comparison["counts"]["regressed"] + comparison[
        "counts"
    ]["unchanged"] == len(RubricDimension)
    assert comparison["mean_overall_delta"] == pytest.approx(tuned.mean_overall - base.mean_overall)
    assert comparison["base_label"] == "base"
    assert comparison["tuned_label"] == "fine-tuned"
    assert comparison["base_dataset_version"] == comparison["tuned_dataset_version"]
    assert json.loads(json.dumps(comparison)) == comparison


def test_comparing_a_report_with_itself_reports_nothing_as_moved():
    report = evaluate_generations(
        [(INSTRUCTION, GROUNDED)],
        label="base",
        dataset_version="qwen_dataset.v1",
        known_capabilities=KNOWN_CAPABILITIES,
        known_intents=INTENT_NAMES,
    )

    comparison = compare_reports(report, report)

    assert comparison["improved"] == []
    assert comparison["regressed"] == []
    assert comparison["unchanged"] == [str(dimension) for dimension in RubricDimension]
    assert comparison["mean_overall_delta"] == 0.0


def test_regressed_dimensions_are_reported_as_regressed_not_hidden():
    def build(label: str, response: str) -> EvalReport:
        return evaluate_generations(
            [(INSTRUCTION, response)],
            label=label,
            dataset_version="qwen_dataset.v1",
            known_capabilities=KNOWN_CAPABILITIES,
            known_intents=INTENT_NAMES,
        )

    comparison = compare_reports(build("base", GROUNDED), build("tuned", HALLUCINATED))

    assert "capability_grounding" in comparison["regressed"]
    assert "no_false_execution" in comparison["regressed"]
    assert comparison["per_dimension"]["capability_grounding"]["delta"] == -1.0


def test_compare_reports_markdown_names_the_dimensions_it_moved():
    def build(label: str, response: str) -> EvalReport:
        return evaluate_generations(
            [(INSTRUCTION, response)],
            label=label,
            dataset_version="qwen_dataset.v1",
            known_capabilities=KNOWN_CAPABILITIES,
            known_intents=INTENT_NAMES,
        )

    markdown = compare_reports_markdown(build("base", HALLUCINATED), build("fine-tuned", GROUNDED))

    assert "# Base vs fine-tuned" in markdown
    assert "no_false_execution" in markdown
    assert "mechanical proxies" in markdown


def test_loss_to_perplexity_is_the_exponential_of_the_loss():
    assert loss_to_perplexity(0.0) == 1.0
    assert loss_to_perplexity(1.0) == pytest.approx(math.exp(1.0))
    assert loss_to_perplexity(2.0) == pytest.approx(7.389056)


def test_loss_to_perplexity_clamps_a_negative_loss_and_a_diverged_one():
    """A negative NLL implies a probability above one; a diverged one overflows."""
    assert loss_to_perplexity(-1.0) == 1.0
    assert loss_to_perplexity(-0.0001) == 1.0
    assert loss_to_perplexity(700.0) == math.inf
    assert loss_to_perplexity(10_000.0) == math.inf
    assert loss_to_perplexity(float("nan")) == math.inf


def test_mean_perplexity_is_the_perplexity_of_the_mean_loss():
    assert mean_perplexity([1.0, 1.0]) == pytest.approx(math.exp(1.0))
    assert mean_perplexity([]) == 0.0
    assert mean_perplexity([-1.0]) == 1.0
    assert mean_perplexity([1_000.0]) == math.inf


def test_mean_perplexity_refuses_a_non_finite_loss():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="losses must all be finite"):
            mean_perplexity([1.0, bad])


def test_an_example_score_looks_up_a_dimension_by_name():
    report = evaluate_generations(
        [
            {
                "instruction": INSTRUCTION,
                "response": GROUNDED,
                "category": "intent_interpretation",
                "expected_intent": "task_manage",
            }
        ],
        label="base",
        dataset_version="qwen_dataset.v1",
        known_capabilities=KNOWN_CAPABILITIES,
        known_intents=INTENT_NAMES,
    )

    example = report.examples[0]

    assert isinstance(example, ExampleScore)
    assert example.index == 0
    assert example.expected_intent == "task_manage"
    assert example.score_for(RubricDimension.NO_FALSE_EXECUTION).score == 1.0
    assert example.overall == pytest.approx(
        sum(entry.score for entry in example.scores) / len(RubricDimension)
    )
    assert json.loads(json.dumps(example.to_dict())) == example.to_dict()
