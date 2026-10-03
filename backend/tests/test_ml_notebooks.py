"""The Kaggle notebooks that actually train the Phase 10 models.

The local half of the pipeline is stdlib-only and has to stay that way, so the
notebooks are emitted as *source text* and executed on remote hardware. This
module is the boundary, and everything about it is a place where a silent
failure would not fail: a notebook missing `id` on a cell parses today and
becomes a hard error at nbformat minor 6; a notebook that saved the 8B base
weights would fill an artifact store and upload hundreds of gigabytes a
segment; a notebook whose resume restored only the weights would produce a
number that looks plausible and is not comparable to the uninterrupted run it
claims to continue.

The tests therefore check the rendered *document* — valid JSON, nbformat 4.5,
every cell identified, GPU and internet requested — and check the rendered
*text* for the three properties the Qwen notebook is built around: it resumes,
it saves only the adapter, and it checkpoints.
"""

from __future__ import annotations

import ast
import json

import pytest

from ml.kaggle.notebook import (
    NBFORMAT_MINOR,
    NBFORMAT_VERSION,
    RUN_MANIFEST_FIELDS,
    render_eval_notebook,
    render_qwen_training_notebook,
    render_small_training_notebook,
)
from ml.preprocessing.normalize import find_credential

CONFIG = {"seed": 20260101, "lora_r": 16, "lora_alpha": 32}
DATASET_SLUG = "example-owner/nexo-phase10-routing"
RUN_ID = "small-20260101T131405Z-abcd1234"


def _render_all() -> dict[str, dict]:
    """Render all three notebooks and decode them."""
    small = render_small_training_notebook(config=CONFIG, dataset_slug=DATASET_SLUG, run_id=RUN_ID)
    qwen = render_qwen_training_notebook(
        config=CONFIG,
        dataset_slug=DATASET_SLUG,
        run_id="qwen-20260101T131405Z-abcd1234",
        segment_index=0,
        max_steps=250,
    )
    evaluation = render_eval_notebook(
        config=CONFIG,
        dataset_slug=DATASET_SLUG,
        run_id="eval-20260101T131405Z-abcd1234",
        adapter_dir_name="adapter",
    )
    return {
        "small": json.loads(small),
        "qwen": json.loads(qwen),
        "eval": json.loads(evaluation),
    }


@pytest.fixture(scope="module")
def notebooks() -> dict[str, dict]:
    return _render_all()


def test_each_rendered_notebook_is_valid_json():
    for name, renderer in (
        (
            "small",
            lambda: render_small_training_notebook(
                config=CONFIG, dataset_slug=DATASET_SLUG, run_id=RUN_ID
            ),
        ),
        (
            "qwen",
            lambda: render_qwen_training_notebook(
                config=CONFIG,
                dataset_slug=DATASET_SLUG,
                run_id="qwen-20260101T131405Z-abcd1234",
                segment_index=0,
                max_steps=250,
            ),
        ),
        (
            "eval",
            lambda: render_eval_notebook(
                config=CONFIG,
                dataset_slug=DATASET_SLUG,
                run_id="eval-20260101T131405Z-abcd1234",
                adapter_dir_name="adapter",
            ),
        ),
    ):
        text = renderer()
        assert json.loads(text) is not None, name
        assert json.loads(json.dumps(json.loads(text))) == json.loads(text), name


def test_each_notebook_declares_nbformat_4_5(notebooks):
    for name, document in notebooks.items():
        assert document["nbformat"] == NBFORMAT_VERSION, name
        assert document["nbformat"] == 4, name
        assert document["nbformat_minor"] >= 5, name
        assert document["nbformat_minor"] == NBFORMAT_MINOR, name


def test_every_cell_carries_a_unique_legal_id(notebooks):
    """A cell without an id draws MissingIDFieldWarning and is a parse error at minor 6."""
    for name, document in notebooks.items():
        ids = [cell.get("id") for cell in document["cells"]]
        assert document["cells"], name
        assert all(isinstance(cell_id, str) and cell_id for cell_id in ids), name
        assert len(set(ids)) == len(ids), name
        for cell_id in ids:
            assert cell_id[0].isalnum(), (name, cell_id)
            assert all(character.isalnum() or character in "_-" for character in cell_id), (
                name,
                cell_id,
            )


def test_every_cell_is_a_well_formed_markdown_or_code_cell(notebooks):
    for name, document in notebooks.items():
        for cell in document["cells"]:
            assert cell["cell_type"] in {"markdown", "code"}, (name, cell["id"])
            assert isinstance(cell["source"], list), (name, cell["id"])
            assert "".join(cell["source"]).strip(), (name, cell["id"])
            if cell["cell_type"] == "code":
                # A rendered notebook has never run; claiming otherwise is a lie.
                assert cell["execution_count"] is None, (name, cell["id"])
                assert cell["outputs"] == [], (name, cell["id"])


def test_the_kaggle_metadata_enables_the_gpu(notebooks):
    for name, document in notebooks.items():
        kaggle = document["metadata"]["kaggle"]
        assert kaggle["enable_gpu"] is True, name
        assert kaggle["language"] == "python", name
        assert kaggle["dataset_sources"] == [DATASET_SLUG], name


def test_the_training_notebooks_ask_for_internet_because_they_pip_install(notebooks):
    """Without a network the pinned stack cannot be resolved, so the run is over."""
    for name in ("small", "qwen"):
        kaggle = notebooks[name]["metadata"]["kaggle"]
        assert kaggle["enable_internet"] is True, name


def test_the_evaluation_notebook_deliberately_declines_internet_access(notebooks):
    """Base and tuned generations are local to the kernel; a network is a risk, not a need."""
    kaggle = notebooks["eval"]["metadata"]["kaggle"]

    assert kaggle["enable_gpu"] is True
    assert kaggle["enable_internet"] is False


def test_the_first_code_cell_pins_the_installed_versions(notebooks):
    for name, document in notebooks.items():
        code_cells = [cell for cell in document["cells"] if cell["cell_type"] == "code"]
        first = "".join(code_cells[0]["source"])

        assert "%pip install --quiet" in first, name
        assert "torch==" in first, name
        assert "transformers==" in first, name
        assert "RESOLVED_VERSIONS" in first, name


def test_no_rendered_notebook_contains_credential_shaped_text(notebooks):
    """The renderer refuses to emit one; this asserts the rule survives any edit."""
    for name, document in notebooks.items():
        for cell in document["cells"]:
            assert find_credential("".join(cell["source"])) is None, (name, cell["id"])
        assert find_credential(json.dumps(document)) is None, name


def test_the_run_parameters_are_baked_in_rather_than_resolved_at_run_time(notebooks):
    for name, document in notebooks.items():
        params_cell = next(cell for cell in document["cells"] if cell.get("id") == "run-parameters")
        source = "".join(params_cell["source"])

        assert "RUN_PARAMS = " in source, name
        assert str(RUN_ID) in source or "qwen-20260101" in source or "eval-20260101" in source, name
        assert DATASET_SLUG in source, name


def test_the_run_manifest_fields_match_the_local_pipeline_contract(notebooks):
    """Kaggle cannot import ``ml``, so a rename on either side has to fail here."""
    for name, document in notebooks.items():
        manifest_cell = next(cell for cell in document["cells"] if cell.get("id") == "run-manifest")
        source = "".join(manifest_cell["source"])
        declared = ast.literal_eval(
            source.split("RUN_MANIFEST_FIELDS = ", 1)[1].split("\n", 1)[0].strip()
        )

        assert tuple(declared) == RUN_MANIFEST_FIELDS, name


def test_the_router_notebook_trains_and_scores_a_held_out_split(notebooks):
    source = _all_code(notebooks["small"])

    assert "AutoModelForSequenceClassification" in source
    assert "LABEL2ID" in source
    assert "confusion_matrix.json" in source
    assert "predictions.jsonl" in source
    assert "metrics.json" in source


def test_the_qwen_notebook_masks_the_prompt_out_of_the_loss(notebooks):
    """Otherwise the model is trained to reproduce the instruction and the preamble."""
    document = notebooks["qwen"]
    ids = [cell.get("id") for cell in document["cells"]]
    source = _all_code(document)

    assert "completion-only-tokenisation" in ids
    assert "-100" in source, "prompt positions must be masked out of the loss"
    assert "label_pad_token_id=-100" in source


def test_the_qwen_notebook_says_resume_is_real(notebooks):
    """Weights alone give a model that has forgotten what step it was on."""
    source = _all_code(notebooks["qwen"])
    markdown = _all_markdown(notebooks["qwen"])

    assert "resume" in markdown.lower()
    assert "RESUME_FROM" in source
    assert "_restore_rng_state" in source
    assert "TRAINER.optimizer.load_state_dict" in source
    assert "TRAINER.lr_scheduler.load_state_dict" in source
    assert "skip_first_batches" in source
    assert "RESUMED_STEP" in source


def test_the_qwen_notebook_saves_the_adapter_and_never_the_base_weights(notebooks):
    """A checkpoint holding a full base model is a supply-chain problem nobody signed up for."""
    source = _all_code(notebooks["qwen"])
    markdown = _all_markdown(notebooks["qwen"])

    assert "The base weights are never written" in markdown
    assert "adapter_model.safetensors" in markdown
    assert "PeftModel" in source

    save_calls = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith("MODEL.save_pretrained")
        or line.strip().startswith("BASE.save_pretrained")
        or line.strip().startswith("model.save_pretrained")
    ]
    assert save_calls, "the adapter must be written somewhere"
    assert all("BASE" not in call for call in save_calls), save_calls


def test_the_qwen_notebook_checkpoints_on_a_cadence_and_at_segment_end(notebooks):
    source = _all_code(notebooks["qwen"])

    assert "NexoCheckpointCallback" in source
    assert "on_step_end" in source
    assert "on_train_end" in source
    assert "save_every_n_steps" in source
    assert "optimizer.pt" in source
    assert "scheduler.pt" in source
    assert "rng_state.pt" in source
    assert "checkpoint.json" in source
    assert "SAVE_EVERY_N_STEPS" in source


def test_the_qwen_notebook_refuses_to_train_the_base_weights(notebooks):
    source = _all_code(notebooks["qwen"])

    assert "load_in_4bit=True" in source
    assert "gradient_checkpointing_enable" in source
    assert "PACKING" not in source.upper() or "packing" in source


def test_the_evaluation_notebook_scores_both_sides_under_identical_conditions(notebooks):
    source = _all_code(notebooks["eval"])

    assert "do_sample=False" in source
    assert "BASE_GENERATIONS" in source
    assert "TUNED_GENERATIONS" in source
    assert "TUNED_MODEL = PeftModel.from_pretrained(MODEL_EVAL" in source
    assert "delta_strict_pass_rate" in source
    assert "paired" in source


def test_the_evaluation_notebook_rubric_is_deterministic_not_a_model_judge(notebooks):
    document = notebooks["eval"]
    source = _all_code(document)

    assert "deterministic-rubric-v1" in source
    assert "deterministic rubric, not an LLM judge" in source
    assert "pairs" not in source, "the rubric must not call out to a judge model"


def test_rendering_is_deterministic():
    first = render_small_training_notebook(config=CONFIG, dataset_slug=DATASET_SLUG, run_id=RUN_ID)
    second = render_small_training_notebook(config=CONFIG, dataset_slug=DATASET_SLUG, run_id=RUN_ID)

    assert first == second


def test_an_unknown_config_key_is_carried_into_the_notebook_rather_than_dropped():
    text = render_small_training_notebook(
        config={**CONFIG, "label_smoothing": 0.1},
        dataset_slug=DATASET_SLUG,
        run_id=RUN_ID,
    )

    assert "label_smoothing" in text


@pytest.mark.parametrize("typo", ["max_seq_lenght", "small_learning_ratee", "seeed"])
def test_a_config_key_that_looks_like_a_typo_is_refused(typo):
    from ml.datasets.schema import DatasetError

    with pytest.raises(DatasetError, match="looks like a typo"):
        render_small_training_notebook(
            config={**CONFIG, typo: 1}, dataset_slug=DATASET_SLUG, run_id=RUN_ID
        )


def test_a_render_argument_may_not_be_smuggled_in_through_the_config():
    from ml.datasets.schema import DatasetError

    with pytest.raises(DatasetError, match="is a render argument"):
        render_small_training_notebook(
            config={**CONFIG, "run_id": "smuggled"},
            dataset_slug=DATASET_SLUG,
            run_id=RUN_ID,
        )


def test_an_unsafe_identifier_is_refused_before_it_reaches_a_gpu():
    from ml.datasets.schema import DatasetError

    with pytest.raises(DatasetError, match="run_id must be"):
        render_small_training_notebook(
            config=CONFIG, dataset_slug=DATASET_SLUG, run_id="'; import os"
        )

    with pytest.raises(DatasetError, match="dataset_slug must be"):
        render_small_training_notebook(config=CONFIG, dataset_slug="../escape", run_id=RUN_ID)

    with pytest.raises(DatasetError, match="resume_from must not contain"):
        render_qwen_training_notebook(
            config=CONFIG,
            dataset_slug=DATASET_SLUG,
            run_id="qwen-run",
            segment_index=0,
            max_steps=10,
            resume_from="../../etc/passwd",
        )


def test_segment_bounds_are_checked():
    from ml.datasets.schema import DatasetError

    with pytest.raises(DatasetError, match="segment_index must be an int >= 0"):
        render_qwen_training_notebook(
            config=CONFIG,
            dataset_slug=DATASET_SLUG,
            run_id="qwen-run",
            segment_index=-1,
            max_steps=10,
        )

    with pytest.raises(DatasetError, match="max_steps must be an int >= 1"):
        render_qwen_training_notebook(
            config=CONFIG,
            dataset_slug=DATASET_SLUG,
            run_id="qwen-run",
            segment_index=0,
            max_steps=0,
        )


def _all_code(document: dict) -> str:
    return "\n".join(
        "".join(cell["source"]) for cell in document["cells"] if cell["cell_type"] == "code"
    )


def _all_markdown(document: dict) -> str:
    return "\n".join(
        "".join(cell["source"]) for cell in document["cells"] if cell["cell_type"] == "markdown"
    )
