"""The security gate: nothing in `ml/` may carry, read or emit a credential.

This is the one test module that exists to fail rather than to describe. The
pipeline reads application source, generates text from templates, and writes
artifacts that get uploaded to a remote kernel, uploaded to a dataset, pasted
into tickets and attached to pull requests. Every one of those is a place a key
has gone before. So three properties are asserted here, and each of them is a
property of the *source*, not of a run:

1. **No file under `backend/ml/` contains credential-shaped text.** Checked with
   the pipeline's own detector, so the rule and the gate are the same rule.
2. **No module ever opens a credential path.** Verified by walking every AST in
   `ml/` and refusing any call whose target is `open`, `read_text`,
   `read_bytes`, `write_text`, `write_bytes` or `Path` construction naming a
   Kaggle credential path. The prohibition on `~/.kaggle/access_token` has to be
   a fact about the code, not a promise in a docstring.
3. **Nothing generated can carry one either.** Every dataset, validation report,
   rendered notebook, run manifest and evaluation report this pipeline produces
   is built here and scanned.

The real token file is never opened by this module, and its contents are not
embedded anywhere in the repository. The assertions below reference it only by
the *name* of the path, which is the whole point: a validator that could not
name the thing it refuses to read could not tell a reader what it is protecting.

Every credential-shaped string in this file is fabricated from repeated
characters. None of them is a real token.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

from ml.preprocessing.normalize import contains_credential, find_credential, redact

#: ``backend/``. The whole Phase 10 package lives under it.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
ML_ROOT = BACKEND_ROOT / "ml"

#: Directories that hold installed packages, compiled bytecode or generated data
#: rather than pipeline source.
EXCLUDED_PARTS = frozenset({".venv", "__pycache__", "artifacts", "datasets"})

#: File extensions the credential sweep reads. Anything else under `ml/` is a
#: binary artifact and is not part of the source contract.
SCANNED_SUFFIXES = frozenset({".py", ".toml", ".json", ".md", ".txt", ".cfg", ".ini"})

#: Fabricated credential-shaped strings, built from repeated characters so they
#: cannot collide with anything real.
FAKE_KAGGLE = "KAGGLE_" + "q" * 32
FAKE_HF = "hf_" + "Ww" * 12
FAKE_ASSIGNED = "api_key = 5b2d9e4c1a7f8306d5c2b"

#: The credential path this pipeline must never read, named only.
CREDENTIAL_PATH_NAME = "~/.kaggle/access_token"

#: The user's home directory as it appears on this machine. Naming it is the
#: assertion; its contents are never read.
#:
#: Derived from `Path.home()` rather than hardcoded. A literal `C:\Users\<name>`
#: would put one contributor's account name into every clone of this repository,
#: and it would go stale the moment the test runs on anyone else's machine — at
#: which point the assertion at line 218 would silently stop testing anything,
#: which is the worst possible failure for a security test.
HOME_PREFIX = str(Path.home())


def _pipeline_files() -> list[Path]:
    """Every source and artifact file under `ml/`, excluding vendored trees.

    Walks with pruned directories rather than ``rglob``: `ml/.venv` holds a
    full torch install, and descending into it would take longer than the whole
    rest of this module.
    """
    found: list[Path] = []
    for directory, subdirectories, names in os.walk(ML_ROOT):
        subdirectories[:] = [name for name in subdirectories if name not in EXCLUDED_PARTS]
        for name in names:
            path = Path(directory) / name
            if path.suffix.lower() in SCANNED_SUFFIXES:
                found.append(path)
    return sorted(found)


def _python_files() -> list[Path]:
    return [path for path in _pipeline_files() if path.suffix == ".py"]


def _all_strings(node: ast.AST) -> list[str]:
    return [
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    ]


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """The ids of every docstring constant, so prose can be told from code."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node,
            ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        ):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


# --------------------------------------------------------------------------
# 1. No credential-shaped text anywhere in the package.
# --------------------------------------------------------------------------


def test_the_package_is_not_empty_of_files_to_scan():
    """A vacuous scan that finds nothing is the most dangerous outcome here."""
    assert len(_pipeline_files()) > 10
    assert ML_ROOT / "__init__.py" in _pipeline_files()


def test_no_file_under_ml_contains_credential_shaped_text():
    offenders = [
        (path, find_credential(path.read_text(encoding="utf-8", errors="replace")))
        for path in _pipeline_files()
    ]
    found = [(path, kind) for path, kind in offenders if kind is not None]

    assert found == [], f"credential-shaped text of kind {found!r}"


def test_the_detector_runs_over_the_pipeline_source_itself():
    """The sweep above is only meaningful if the sweep recognises a real hit."""
    assert contains_credential(f"my token is {FAKE_KAGGLE}")
    assert find_credential(f"my token is {FAKE_KAGGLE}") == "kaggle_token"
    assert find_credential(f"key {FAKE_HF}") == "huggingface_token"
    assert find_credential(FAKE_ASSIGNED) == "assigned_secret"


def test_redact_removes_a_planted_credential_from_an_arbitrary_string():
    planted = f"here is my token {FAKE_KAGGLE} and my api key {FAKE_ASSIGNED}"

    cleaned = redact(planted)

    assert FAKE_KAGGLE not in cleaned
    assert "5b2d9e4c1a7f8306d5c2b" not in cleaned
    assert find_credential(cleaned) is None


# --------------------------------------------------------------------------
# 2. The credential path is named, never read.
# --------------------------------------------------------------------------


def test_the_prohibition_on_the_credential_path_is_written_down():
    """The modules that could plausibly read it say, in prose, that they do not."""
    mentions = [
        path for path in _python_files() if CREDENTIAL_PATH_NAME in path.read_text(encoding="utf-8")
    ]

    assert mentions, "no module documents the prohibition it is supposed to honour"


def test_the_credential_path_is_only_ever_a_path_name_in_prose():
    """The credential path is a docstring mention, never a string literal.

    Every mention of it sits in prose, never in a literal that could be opened,
    hashed, logged or interpolated into a path. Scoped to the *credential* path:
    ``metadata.kaggle`` and ``kaggle.com`` are ordinary strings — the notebook
    metadata block and the kernel URL — and are not what this rule is about.
    """
    offenders: list[tuple[str, str]] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstrings = _docstring_nodes(tree)
        for constant in ast.walk(tree):
            if not isinstance(constant, ast.Constant) or not isinstance(constant.value, str):
                continue
            if id(constant) in docstrings:
                continue
            if "access_token" in constant.value or "~/.kaggle" in constant.value:
                offenders.append((path.name, constant.value[:80]))

    assert offenders == [], f"credential path referenced in code: {offenders}"


def test_no_module_opens_or_writes_a_kaggle_credential_path():
    """The strongest form of the rule: proved by walking every call in `ml/`."""
    readers = {"open", "read_text", "read_bytes", "write_text", "write_bytes"}
    offenders: list[str] = []

    def _describe(node: ast.AST) -> str:
        return ast.unparse(node)

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = ""
            if isinstance(node.func, ast.Attribute):
                name = node.func.attr
            elif isinstance(node.func, ast.Name):
                name = node.func.id
            arguments = " ".join(_describe(argument) for argument in node.args)
            keyword_values = " ".join(_describe(keyword.value) for keyword in node.keywords)
            touched = f"{arguments} {keyword_values}"
            if name in readers and ("kaggle" in touched.lower() or "token" in touched.lower()):
                offenders.append(f"{path.name}: {name}({touched})")

    assert offenders == [], f"ml/ reads or writes a credential path: {offenders}"


def test_the_windows_home_path_of_the_real_token_is_absent_from_the_source():
    """Named, never embedded. The repository does not carry a machine-specific secret path."""
    offenders = [
        path.name
        for path in _python_files()
        if HOME_PREFIX.lower() in path.read_text(encoding="utf-8").lower()
    ]

    assert offenders == []


def test_the_prose_mention_is_the_generic_one_and_carries_no_value():
    """`~/.kaggle/access_token` is a path name in a sentence, not a value being read."""
    path = ML_ROOT / "preprocessing" / "normalize.py"
    text = path.read_text(encoding="utf-8")

    assert CREDENTIAL_PATH_NAME in text
    assert "Nothing in this module reads, opens or imports" in text


# --------------------------------------------------------------------------
# 3. Nothing the pipeline generates can carry one either.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def generated_texts() -> dict[str, list[str]]:
    """Every text this pipeline produces, rendered and ready to be scanned.

    Datasets, validation reports, the three Kaggle notebooks, the capability
    inventory, a run manifest, a checkpoint's metadata and a rubric evaluation:
    the full set of things that get written to disk, uploaded or pasted.
    """
    from ml.datasets.capabilities import build_capability_inventory
    from ml.datasets.qwen_sft import build_qwen_records
    from ml.datasets.routing import build_routing_records
    from ml.datasets.taxonomy import INTENT_NAMES
    from ml.evaluation.qwen_eval import evaluate_generations
    from ml.kaggle.notebook import (
        render_eval_notebook,
        render_qwen_training_notebook,
        render_small_training_notebook,
    )
    from ml.training.checkpoint import CHECKPOINT_FORMAT_VERSION, CheckpointMetadata
    from ml.training.manifest import RunManifest, collect_environment, new_run_id
    from ml.validation import validate_qwen_dataset, validate_routing_dataset

    routing = build_routing_records(per_intent=2)
    qwen = build_qwen_records(per_category=1)

    routing_report = validate_routing_dataset(
        routing, known_intents=INTENT_NAMES, max_class_ratio=3.0
    )
    qwen_report = validate_qwen_dataset(qwen)

    inventory = build_capability_inventory(BACKEND_ROOT / "app")
    capabilities = sorted(
        {*inventory.entities(), *inventory.recommendation_types, *inventory.risk_types}
    )

    config = {"seed": 20260101}
    slug = "example-owner/nexo-phase10-routing"
    notebooks = [
        render_small_training_notebook(config=config, dataset_slug=slug, run_id="small-run"),
        render_qwen_training_notebook(
            config=config,
            dataset_slug=slug,
            run_id="qwen-run",
            segment_index=0,
            max_steps=100,
        ),
        render_eval_notebook(
            config=config, dataset_slug=slug, run_id="eval-run", adapter_dir_name="adapter"
        ),
    ]

    manifest = RunManifest(
        run_id=new_run_id("small", when=__import__("datetime").datetime.now()),
        model_name="microsoft/deberta-v3-base",
        base_model="microsoft/deberta-v3-base",
        model_version="routing_intent.v1",
        dataset_version="routing_dataset.v1",
        dataset_source="ml/datasets/routing",
        schema_version="routing_intent.v1",
        preprocessing_version="nexo_splits.v1",
        code_commit="0" * 40,
        code_dirty=False,
        config={"max_seq_length": 128},
        hyperparameters={"learning_rate": 2e-5},
        seed=20260101,
        environment=collect_environment(),
        started_at="2026-01-01T00:00:00+00:00",
    )

    checkpoint = CheckpointMetadata(
        format_version=CHECKPOINT_FORMAT_VERSION,
        run_id="small-run",
        model_name="microsoft/deberta-v3-base",
        global_step=100,
        epoch=1,
        segment_index=0,
        segments_completed=1,
        dataset_version="routing_dataset.v1",
        dataset_checksum="0" * 64,
        code_commit="0" * 40,
        config={"max_seq_length": 128},
        hyperparameters={},
        seed=20260101,
        created_at="2026-01-01T00:00:00+00:00",
        elapsed_seconds=1.0,
        files={"adapter.safetensors": "adapter.safetensors"},
    )

    evaluation = evaluate_generations(
        [(row["instruction"], row["response"]) for row in qwen[:5]],
        label="base",
        dataset_version="qwen_dataset.v1",
        known_capabilities=capabilities,
        known_intents=INTENT_NAMES,
    )

    texts: dict[str, list[str]] = {
        "routing_dataset": [row["text"] for row in routing]
        + [json.dumps(row, sort_keys=True) for row in routing],
        "qwen_dataset": [
            value for row in qwen for value in (row["instruction"], row["response"], row["system"])
        ]
        + [json.dumps(row, sort_keys=True) for row in qwen],
        "validation_reports": [
            routing_report.to_markdown(),
            qwen_report.to_markdown(),
            json.dumps(routing_report.to_dict(), sort_keys=True),
            json.dumps(qwen_report.to_dict(), sort_keys=True),
        ],
        "notebooks": [*notebooks, *[_all_cell_sources(note) for note in notebooks]],
        "capability_inventory": [json.dumps(inventory.to_dict(), sort_keys=True)],
        "run_manifest": [
            json.dumps(manifest.to_dict(), sort_keys=True),
            manifest.to_markdown(),
        ],
        "checkpoint_metadata": [json.dumps(checkpoint.to_dict(), sort_keys=True)],
        "eval_report": [evaluation.to_json(), evaluation.to_markdown()],
    }
    return texts


def _all_cell_sources(notebook_text: str) -> str:
    document = json.loads(notebook_text)
    return "\n".join("".join(cell["source"]) for cell in document["cells"])


def test_every_generated_artifact_is_scanned(generated_texts):
    """If the fixture ever stops producing a category, the sweep says so."""
    assert set(generated_texts) == {
        "routing_dataset",
        "qwen_dataset",
        "validation_reports",
        "notebooks",
        "capability_inventory",
        "run_manifest",
        "checkpoint_metadata",
        "eval_report",
    }
    assert all(texts for texts in generated_texts.values())


def test_no_generated_dataset_or_report_contains_a_credential(generated_texts):
    offenders = [
        (category, find_credential(text))
        for category, texts in generated_texts.items()
        for text in texts
        if contains_credential(text)
    ]

    assert offenders == [], f"generated artifacts carrying credential-shaped text: {offenders}"


def test_the_scan_is_not_vacuous(generated_texts):
    """A sweep that finds nothing because it reads nothing is worse than no sweep."""
    total = sum(len(texts) for texts in generated_texts.values())
    characters = sum(len(text) for texts in generated_texts.values() for text in texts)

    assert total >= 10
    assert characters > 10_000


def test_the_detector_would_have_caught_a_planted_secret_in_a_report(generated_texts):
    """Positive control: inject the secret into a real report and the gate fires."""
    clean = generated_texts["routing_dataset"][0]
    poisoned = f"{clean} my token is {FAKE_KAGGLE}"

    assert find_credential(clean) is None
    assert find_credential(poisoned) == "kaggle_token"
    assert find_credential(redact(poisoned)) is None


def test_the_validation_gate_rejects_a_dataset_carrying_a_credential():
    from ml.datasets.schema import SCHEMA_VERSION_ROUTING
    from ml.datasets.taxonomy import INTENT_NAMES
    from ml.validation import assert_clean, validate_routing_dataset

    records = [
        {
            "schema_version": SCHEMA_VERSION_ROUTING,
            "text": f"Mark the API contract task as done, token {FAKE_KAGGLE}",
            "intent": "task_manage",
        }
    ]

    report = validate_routing_dataset(records, known_intents=INTENT_NAMES, max_class_ratio=3.0)

    assert not report.passed
    assert "credential_detected" in report.codes()
    assert FAKE_KAGGLE not in json.dumps(report.to_dict())
    with pytest.raises(Exception, match="credential_detected"):
        assert_clean(report)


def test_the_notebook_renderer_refuses_to_emit_a_cell_carrying_a_credential():
    from ml.datasets.schema import DatasetError
    from ml.kaggle.notebook import render_small_training_notebook

    with pytest.raises(DatasetError, match="credential-shaped text"):
        render_small_training_notebook(
            config={"seed": 20260101, "operator_note": FAKE_ASSIGNED},
            dataset_slug="example-owner/nexo-phase10-routing",
            run_id="small-run",
        )


def test_a_credential_found_in_free_text_is_reported_by_kind_only():
    from ml.validation import validate_no_credentials

    report = validate_no_credentials([f"my token is {FAKE_KAGGLE}"])

    assert not report.passed
    payload = json.dumps(report.to_dict())
    assert "kaggle_token" in payload
    assert "q" * 32 not in payload.replace("kaggle_token", "")
