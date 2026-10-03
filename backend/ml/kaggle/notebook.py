"""Render the Kaggle notebooks that actually train the Phase 10 models.

The local half of this pipeline is stdlib-only by construction, and it has to
stay that way: ``backend/requirements.txt`` carries no ``torch``, so the
notebooks are emitted as **source text** and executed on remote hardware. This
module is the boundary. Nothing here imports ``torch``, ``transformers`` or
``peft``, and nothing here executes a notebook — it builds a document, proves
the document is well-formed, and hands back the JSON text.

Three renderers, one per job:

:func:`render_small_training_notebook`
    Fits the intent router — an ``AutoModelForSequenceClassification`` over the
    ``routing_intent.v1`` dataset — and writes predictions plus a confusion
    matrix for the held-out split.

:func:`render_qwen_training_notebook`
    Segment-wise QLoRA fine-tune of Qwen3-8B. Only the LoRA adapter is ever
    written; the 8B base weights are downloaded, quantised and discarded.

:func:`render_eval_notebook`
    Scores base and fine-tuned Qwen on the same held-out split under the same
    prompts and the same decoding settings, so the delta measures the fine-tune
    rather than a different prompt or a different seed.

Two properties are enforced rather than documented, because both fail silently
otherwise.

**Cell ids are mandatory.** A notebook without ``id`` on every cell draws
nbformat's ``MissingIDFieldWarning`` and is a hard parse error the moment the
minor version moves to 6. Every cell here carries a stable, readable id.

**Resume is real, not nominal.** A Qwen segment that resumes restores the
adapter in trainable mode, the optimizer and scheduler state, and the RNG state
of ``torch``, ``numpy`` and ``random``, then skips exactly the micro-batches a
continuous run would already have consumed. Without the RNG restore a resumed
run differs from an uninterrupted one in dropout and in nothing else you can
easily see; the number still looks plausible, which is what makes it dangerous.

The manifest field names in :data:`RUN_MANIFEST_FIELDS` mirror
``ml.training.manifest``. They are baked into the notebook text because Kaggle
does not import ``ml`` — a notebook that could not write a manifest the local
pipeline could parse would defeat the point.
"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ml.datasets.schema import DatasetError, stable_json_dumps
from ml.preprocessing.normalize import find_credential

#: The nbformat level every rendered notebook declares. Minor 5 is the first
#: version where cell ids exist and are validated.
NBFORMAT_VERSION = 4
NBFORMAT_MINOR = 5

#: Field names of ``run_manifest.json``. Kept in lockstep with
#: ``ml.training.manifest``; the notebook writer refuses to emit a payload
#: whose keys differ from this tuple, so a rename on either side fails loudly
#: instead of producing a manifest nobody can parse.
RUN_MANIFEST_FIELDS: tuple[str, ...] = (
    "artifacts",
    "base_model",
    "created_at_utc",
    "dataset_schema_version",
    "dataset_sha256",
    "dataset_slug",
    "environment",
    "eval_rows",
    "library_versions",
    "metrics",
    "model_output",
    "phase",
    "run_id",
    "seed",
    "train_rows",
    "training",
)

#: Every training knob a rendered notebook reads, with the value used when the
#: config is silent. Version pins are defaults, not a source of truth: Kaggle
#: images drift, and the config is what a run is actually reproduced from.
_CONFIG_DEFAULTS: Mapping[str, Any] = {
    "seed": 3407,
    "torch_version": "2.6.0",
    "transformers_version": "4.51.3",
    "peft_version": "0.14.0",
    "trl_version": "0.16.1",
    "bitsandbytes_version": "0.45.5",
    "accelerate_version": "1.6.0",
    "datasets_version": "3.5.0",
    "small_base_model": "microsoft/deberta-v3-base",
    "small_max_seq_length": 128,
    "small_learning_rate": 2e-5,
    "small_num_train_epochs": 5,
    "small_per_device_train_batch_size": 16,
    "small_weight_decay": 0.01,
    "qwen_base_model": "Qwen/Qwen3-8B",
    "qwen_max_seq_length": 2048,
    "qwen_learning_rate": 2e-4,
    "qwen_num_train_epochs": 1,
    "qwen_per_device_train_batch_size": 1,
    "qwen_gradient_accumulation_steps": 16,
    "qwen_weight_decay": 0.0,
    "lora_r": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "lora_target_modules": [
        "q_proj",
        "k_proj",
        "v_proj",
        "o_proj",
        "gate_proj",
        "up_proj",
        "down_proj",
    ],
    "warmup_ratio": 0.03,
    "lr_scheduler_type": "cosine",
    "logging_every_n_steps": 10,
    "save_every_n_steps": 250,
    "max_grad_norm": 1.0,
    "eval_batch_size": 8,
    "eval_output_dir_name": "qwen_eval",
    "eval_max_rows": 200,
    "eval_max_new_tokens": 512,
}

#: Install order. Fixed, so two runs' logs can be diffed line by line.
_PINNED_PACKAGES: tuple[str, ...] = (
    "torch",
    "transformers",
    "peft",
    "trl",
    "bitsandbytes",
    "accelerate",
    "datasets",
)

_NUMERIC_KEYS = frozenset(
    key for key, value in _CONFIG_DEFAULTS.items() if isinstance(value, (int, float))
)
_STRING_KEYS = frozenset(key for key, value in _CONFIG_DEFAULTS.items() if isinstance(value, str))
_SEQUENCE_KEYS = frozenset(key for key in _CONFIG_DEFAULTS if key.endswith("_target_modules"))

#: Names that belong to a renderer's signature rather than to the training
#: config. They are authoritative as arguments, so a config that also carries
#: one is a disagreement between two places that both look authoritative.
_RESERVED_KEYS = frozenset(
    {"run_id", "dataset_slug", "output_dir_name", "segment_index", "max_steps", "resume_from"}
)

_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SAFE_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}")
_CELL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")

_RULED_OUT = (
    "Nothing here serves a request, loads into the running backend, or replaces a "
    "deterministic service."
)


def _lines(text: str) -> list[str]:
    """Split notebook source into the newline-terminated form nbformat expects.

    Args:
        text: The cell body.

    Returns:
        One entry per line, the last without a trailing newline.
    """
    return text.splitlines(keepends=True)


def _markdown(cell_id: str, source: str) -> dict[str, Any]:
    """Build a markdown cell.

    Args:
        cell_id: Stable identifier, unique within the notebook.
        source: The cell body.

    Returns:
        A cell mapping ready for the notebook document.
    """
    _require_cell_id(cell_id)
    return {
        "cell_type": "markdown",
        "id": cell_id,
        "metadata": {},
        "source": _lines(source),
    }


def _code(cell_id: str, source: str) -> dict[str, Any]:
    """Build a code cell with empty outputs.

    A rendered notebook has never run; an ``execution_count`` or output list
    that claims otherwise is a lie about provenance.

    Args:
        cell_id: Stable identifier, unique within the notebook.
        source: The cell body.

    Returns:
        A cell mapping ready for the notebook document.
    """
    _require_cell_id(cell_id)
    return {
        "cell_type": "code",
        "execution_count": None,
        "id": cell_id,
        "metadata": {},
        "outputs": [],
        "source": _lines(source),
    }


def _require_cell_id(cell_id: str) -> None:
    """Reject an identifier nbformat would refuse.

    Args:
        cell_id: The candidate identifier.

    Raises:
        DatasetError: The identifier is empty, too long, or uses characters
            nbformat does not allow in cell ids.
    """
    if _CELL_ID.fullmatch(cell_id) is None:
        raise DatasetError(
            f"cell id {cell_id!r} must be 1-64 chars of [A-Za-z0-9_-] starting "
            "alphanumeric; nbformat rejects anything else as MissingIDField"
        )


def _require_safe_name(value: str, *, field: str, allow_slash: bool = False) -> str:
    """Validate an identifier that is baked into notebook source and into paths.

    A run id or a directory name that contained a quote or a newline would
    escape the string literal it is interpolated into; a slug with a path
    traversal would escape ``/kaggle/working``. Both are configuration errors
    that must not reach a GPU.

    Args:
        value: The candidate identifier.
        field: The name to quote in an error message.
        allow_slash: Whether ``/`` is permitted, for ``owner/dataset`` slugs.

    Returns:
        The validated value.

    Raises:
        DatasetError: The value is unsafe for the requested shape.
    """
    pattern = _SAFE_SLUG if allow_slash else _SAFE_NAME
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        shape = (
            "letters, digits, '.', '_', '-' and '/'"
            if allow_slash
            else ("letters, digits, '.', '_' and '-'")
        )
        raise DatasetError(f"{field} must be a non-empty name of {shape}, got {value!r}")
    return value


def _require_safe_path(value: str, *, field: str) -> str:
    """Validate a path a notebook will resolve against the mounted inputs.

    Absolute POSIX paths and bare names are both legitimate; a traversal
    segment is not, and neither is a backslash, which is a path separator on the
    one platform these notebooks never run on.

    Args:
        value: The candidate path.
        field: The name to quote in an error message.

    Returns:
        The validated value.

    Raises:
        DatasetError: The value contains a traversal segment, a backslash, or a
            basename that is not a safe name.
    """
    if not isinstance(value, str) or not value:
        raise DatasetError(f"{field} must be a non-empty path, got {value!r}")
    if ".." in value or "\\" in value:
        raise DatasetError(f"{field} must not contain '..' or a backslash, got {value!r}")
    _require_safe_name(Path(value).name, field=field)
    return value


def _require_int(value: int, *, field: str, minimum: int) -> int:
    """Validate an integer knob.

    Args:
        value: The candidate value.
        field: The name to quote in an error message.
        minimum: The smallest acceptable value.

    Returns:
        The validated value.

    Raises:
        DatasetError: The value is not an int at or above ``minimum``.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise DatasetError(f"{field} must be an int >= {minimum}, got {value!r}")
    return value


def _merge_config(config: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fold a caller config over the defaults, rejecting near-miss key names.

    An unknown key is passed through into the rendered notebook rather than
    dropped, so nothing the caller supplied is lost. A key that is *almost* a
    known key is a typo, and a typo that falls back to a default trains a run
    nobody asked for — so ``max_seq_lenght`` is an error while ``label_smoothing``
    is merely carried along. The similarity threshold sits at 0.85 because that
    is where transposition typos (``lenght``, ``epocs``, ``ratioo``) end and
    unrelated names begin; a lower threshold starts rejecting plausible keys an
    ``ml.training`` config will grow.

    Args:
        config: The caller's training configuration.

    Returns:
        A tuple of the merged config and the unrecognised keys.

    Raises:
        DatasetError: A key looks like a typo of a known key, or a value has
            the wrong type or cannot be serialised into the notebook.
    """
    if not isinstance(config, Mapping):
        raise DatasetError(f"config must be a mapping, got {type(config).__name__}")
    known = set(_CONFIG_DEFAULTS)
    candidates = sorted(known | {key.split("_", 1)[-1] for key in known})
    extras: dict[str, Any] = {}
    for key in sorted(config):
        if key in known:
            continue
        if key in _RESERVED_KEYS:
            raise DatasetError(
                f"{key!r} is a render argument, not a training config key; pass it to "
                "the renderer instead of the config"
            )
        close = difflib.get_close_matches(key, candidates, n=1, cutoff=0.85)
        if close:
            raise DatasetError(
                f"config key {key!r} looks like a typo of {close[0]!r}; refusing to "
                "fall back to the default silently"
            )
        extras[key] = config[key]

    merged: dict[str, Any] = {**_CONFIG_DEFAULTS, **config}
    for key in sorted(merged):
        if key in _NUMERIC_KEYS and isinstance(merged[key], bool):
            raise DatasetError(f"config key {key!r} must be numeric, got a bool")
        if key in _STRING_KEYS and not isinstance(merged[key], str):
            raise DatasetError(f"config key {key!r} must be a string, got {merged[key]!r}")
        if key in _SEQUENCE_KEYS:
            value = merged[key]
            if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
                raise DatasetError(f"config key {key!r} must be a list of strings, got {value!r}")
            merged[key] = list(value)
    try:
        stable_json_dumps(merged)
    except (TypeError, ValueError) as exc:
        raise DatasetError(
            f"config is not JSON-serialisable and cannot be baked in: {exc}"
        ) from exc
    return merged, extras


_CELL_INSTALL = """\
# Every notebook pins the same versions. A base-versus-fine-tuned comparison
# only means something when both sides ran on one set of libraries, so the pins
# live in the training config and are baked in here rather than left to whatever
# the Kaggle image happened to ship that week.
"""

_CELL_INSTALL_TAIL = """

import importlib.metadata as metadata
import json
import platform
import sys

# The resolved versions, not the requested ones: a wheel that resolved to
# something else is exactly what a reader of this log ten weeks from now needs.
RESOLVED_VERSIONS = {
    name: metadata.version(name)
    for name in (
        'accelerate',
        'bitsandbytes',
        'datasets',
        'peft',
        'torch',
        'transformers',
        'trl',
    )
}
print(
    json.dumps(
        {
            'packages': RESOLVED_VERSIONS,
            'platform': platform.platform(),
            'python': sys.version,
        },
        indent=2,
        sort_keys=True,
    )
)
"""

_CELL_RUNTIME = """\
from __future__ import annotations

import hashlib
import json
import random
import shutil
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from transformers import set_seed

SEED = RUN_PARAMS['seed']
set_seed(SEED)
BF16_SUPPORTED = bool(torch.cuda.is_available() and torch.cuda.is_bf16_supported())
DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'

WORKING_DIR = Path('/kaggle/working')
OUTPUT_DIR = WORKING_DIR / RUN_PARAMS['output_dir_name']
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


print(f'seed {SEED} | device {DEVICE} | bf16 {BF16_SUPPORTED}')
print(f'output {OUTPUT_DIR}')
"""

_CELL_DATASET = """\
# Kaggle mounts an attached dataset under /kaggle/input/<name>, and a dataset
# pulled by name at run time can surface under /kaggle/datasets. The notebook
# tries both roots and the slug's three other spellings, then fails with the
# paths it tried and the directories that do exist — "dataset not attached" is
# the most common way a Kaggle run dies, and the fix is always visible in that
# listing.


def _slug_candidates(slug):
    tail = slug.rsplit('/', 1)[-1]
    ordered = [slug, slug.replace('/', '-'), tail, tail.replace('-', '_')]
    unique = []
    for candidate in ordered:
        if candidate and candidate not in unique:
            unique.append(candidate)
    return tuple(unique)


def _resolve_dataset_root(slug):
    tried = []
    for root_name in ('input', 'datasets'):
        for candidate in _slug_candidates(slug):
            path = Path('/kaggle') / root_name / candidate
            tried.append(str(path))
            if path.is_dir():
                return path
    present = []
    for base in (Path('/kaggle/input'), Path('/kaggle/datasets')):
        if base.is_dir():
            present.extend(sorted(p.name for p in base.iterdir()))
    raise FileNotFoundError(
        'dataset slug {!r} is not mounted. Tried: {}. Present under /kaggle: {}. '
        'Attach it in the Kaggle UI, or re-render this notebook with the '
        'dataset_slug the dataset was published under.'.format(slug, tried, present)
    )


def _find_split(root, patterns, label):
    for pattern in patterns:
        matches = sorted(root.rglob(pattern))
        if matches:
            return matches[0]
    present = sorted(str(p.relative_to(root)) for p in root.rglob('*.jsonl'))
    raise FileNotFoundError(
        'no {} split under {}. Tried globs: {}. Present jsonl files: {}.'.format(
            label, root, list(patterns), present
        )
    )


def _read_jsonl(path):
    rows = []
    with Path(path).open(encoding='utf-8') as handle:
        for number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError('{}:{}: malformed JSON: {}'.format(path, number, exc)) from exc
            if not isinstance(decoded, dict):
                raise ValueError('{}:{}: expected a JSON object'.format(path, number))
            rows.append(decoded)
    if not rows:
        raise ValueError('{} is empty'.format(path))
    return rows


def _schema_versions(rows):
    return sorted({str(row.get('schema_version', 'unknown')) for row in rows})


DATASET_ROOT = _resolve_dataset_root(RUN_PARAMS['dataset_slug'])
TRAIN_PATH = _find_split(DATASET_ROOT, ('*train*.jsonl',), 'train')
EVAL_PATH = _find_split(
    DATASET_ROOT, ('*eval*.jsonl', '*test*.jsonl', '*valid*.jsonl', '*val*.jsonl'), 'eval'
)
LABEL_FILE = DATASET_ROOT / 'label_map.json'
EVAL_ROWS = _read_jsonl(EVAL_PATH)
"""

_CELL_DATASET_TAIL = """TRAIN_ROWS = _read_jsonl(TRAIN_PATH)
DATASET_SHA256 = {
    'train': sha256_file(TRAIN_PATH),
    'eval': sha256_file(EVAL_PATH),
}
SCHEMA_VERSIONS = _schema_versions(TRAIN_ROWS + EVAL_ROWS)
"""

#: The evaluation run loads only the held-out split. Its manifest therefore
#: reports zero training rows, which is what it actually did.
_CELL_DATASET_EVAL_TAIL = """TRAIN_ROWS = []
TRAIN_PATH = None
DATASET_SHA256 = {'eval': sha256_file(EVAL_PATH)}
SCHEMA_VERSIONS = _schema_versions(EVAL_ROWS)
"""

_CELL_DATASET_REPORT = """
print(f'dataset root  {DATASET_ROOT}')
print(f'train split   {TRAIN_PATH} ({len(TRAIN_ROWS)} rows)')
print(f'eval split    {EVAL_PATH} ({len(EVAL_ROWS)} rows)')
print(f'schemas       {_schema_versions(EVAL_ROWS)}')
"""

_CELL_DATASET_REPORT_EVAL = """
print(f'dataset root  {DATASET_ROOT}')
print(f'eval split    {EVAL_PATH} ({len(EVAL_ROWS)} rows)')
print(f'schemas       {_schema_versions(EVAL_ROWS)}')
"""


_CELL_SMALL_LABELS = """\
# The head is sized from the label map the dataset ships, never from a literal
# here: a hard-coded num_labels and a locally built id map would agree by
# accident today and disagree silently the first time the taxonomy grows.
if LABEL_FILE.is_file():
    raw_map = json.loads(LABEL_FILE.read_text(encoding='utf-8'))
    if not isinstance(raw_map, dict):
        raise ValueError(f'{LABEL_FILE} must contain a JSON object')
    if 'label2id' in raw_map:
        LABEL2ID = {str(k): int(v) for k, v in raw_map['label2id'].items()}
    else:
        LABEL2ID = {str(k): int(v) for k, v in raw_map.items()}
    LABEL_SOURCE = str(LABEL_FILE)
else:
    LABEL2ID = {}
    LABEL_SOURCE = f'derived from the train split ({TRAIN_PATH.name})'

if not LABEL2ID:
    LABEL2ID = {
        intent: index for index, intent in enumerate(sorted({row['intent'] for row in TRAIN_ROWS}))
    }

held_out_only = sorted({row['intent'] for row in EVAL_ROWS} - set(LABEL2ID))
if held_out_only:
    # Held-out intents with no head slot cannot be predicted, so they are called
    # out rather than scored as if the model had missed them by choice.
    print(f'WARNING: eval split carries intents absent from the label map: {held_out_only}')

NUM_LABELS = len(LABEL2ID)
ID2LABEL = {value: key for key, value in LABEL2ID.items()}
if sorted(LABEL2ID.values()) != list(range(NUM_LABELS)):
    raise ValueError(f'label ids must be a dense range 0..{NUM_LABELS - 1}, got {LABEL2ID}')
print(f'label map from {LABEL_SOURCE}')
print(f'num_labels {NUM_LABELS}')
print(json.dumps(LABEL2ID, indent=2, sort_keys=True))
(OUTPUT_DIR / 'label_map.json').write_text(
    json.dumps({'label2id': LABEL2ID, 'id2label': ID2LABEL}, indent=2, sort_keys=True),
    encoding='utf-8',
)
"""

_CELL_SMALL_TOKENIZE = """\
from datasets import Dataset
from transformers import AutoTokenizer

BASE_MODEL = RUN_PARAMS['small_base_model']
MAX_SEQ_LENGTH = RUN_PARAMS['small_max_seq_length']
LEARNING_RATE = RUN_PARAMS['small_learning_rate']
NUM_TRAIN_EPOCHS = RUN_PARAMS['small_num_train_epochs']
PER_DEVICE_TRAIN_BATCH_SIZE = RUN_PARAMS['small_per_device_train_batch_size']
WEIGHT_DECAY = RUN_PARAMS['small_weight_decay']
WARMUP_RATIO = RUN_PARAMS['warmup_ratio']
LR_SCHEDULER_TYPE = RUN_PARAMS['lr_scheduler_type']
LOGGING_EVERY_N_STEPS = RUN_PARAMS['logging_every_n_steps']
EVAL_BATCH_SIZE = RUN_PARAMS['eval_batch_size']

TOKENIZER = AutoTokenizer.from_pretrained(BASE_MODEL)


def _tokenize(rows):
    encoded = TOKENIZER(
        [row['text'] for row in rows],
        truncation=True,
        max_length=MAX_SEQ_LENGTH,
        padding=False,
    )
    encoded['labels'] = [LABEL2ID[row['intent']] for row in rows]
    return encoded


TRAIN_DATASET = Dataset.from_dict(_tokenize(TRAIN_ROWS))
EVAL_DATASET = Dataset.from_dict(_tokenize(EVAL_ROWS))
print(f'train {TRAIN_DATASET} / eval {EVAL_DATASET}')
"""

_CELL_SMALL_TRAIN = """\
from transformers import (
    AutoModelForSequenceClassification,
    DataCollatorWithPadding,
    Trainer,
    TrainingArguments,
)

MODEL = AutoModelForSequenceClassification.from_pretrained(
    BASE_MODEL,
    num_labels=NUM_LABELS,
    id2label=ID2LABEL,
    label2id=LABEL2ID,
)


def _compute_metrics(eval_prediction):
    logits, gold = eval_prediction
    predicted = np.argmax(logits, axis=-1)
    return {'accuracy': float((predicted == gold).mean())}


TRAINING_ARGS = TrainingArguments(
    output_dir=str(OUTPUT_DIR / 'trainer'),
    run_name=RUN_PARAMS['run_id'],
    seed=SEED,
    data_seed=SEED,
    learning_rate=LEARNING_RATE,
    weight_decay=WEIGHT_DECAY,
    num_train_epochs=NUM_TRAIN_EPOCHS,
    per_device_train_batch_size=PER_DEVICE_TRAIN_BATCH_SIZE,
    per_device_eval_batch_size=EVAL_BATCH_SIZE,
    warmup_ratio=WARMUP_RATIO,
    lr_scheduler_type=LR_SCHEDULER_TYPE,
    max_grad_norm=RUN_PARAMS['max_grad_norm'],
    logging_steps=LOGGING_EVERY_N_STEPS,
    eval_strategy='epoch',
    save_strategy='no',
    load_best_model_at_end=True,
    metric_for_best_model='accuracy',
    greater_is_better=True,
    fp16=not BF16_SUPPORTED,
    bf16=BF16_SUPPORTED,
    dataloader_num_workers=2,
    report_to=[],
)

TRAINER = Trainer(
    model=MODEL,
    args=TRAINING_ARGS,
    train_dataset=TRAIN_DATASET,
    eval_dataset=EVAL_DATASET,
    data_collator=DataCollatorWithPadding(tokenizer=TOKENIZER),
    compute_metrics=_compute_metrics,
)
TRAIN_RESULT = TRAINER.train()
print(TRAIN_RESULT)
"""

_CELL_SMALL_SCORE = """\
# Metrics and the confusion matrix are computed here rather than pulled from a
# metrics package: scikit-learn is not on the pin list, and a dependency added
# for one F1 loop is a dependency that can drift underneath the numbers.
PREDICTION_OUTPUT = TRAINER.predict(EVAL_DATASET)
LOGITS = np.asarray(PREDICTION_OUTPUT.predictions)
GOLD_IDS = np.asarray(PREDICTION_OUTPUT.label_ids)
PREDICTED_IDS = LOGITS.argmax(axis=-1)
PROBABILITIES = torch.softmax(torch.from_numpy(LOGITS), dim=-1).numpy()


def _per_label_scores(gold, predicted):
    scores = []
    for label in range(NUM_LABELS):
        hit = (predicted == label) & (gold == label)
        predicted_here = predicted == label
        gold_here = gold == label
        true_positive = int(hit.sum())
        precision = true_positive / int(predicted_here.sum()) if predicted_here.any() else 0.0
        recall = true_positive / int(gold_here.sum()) if gold_here.any() else 0.0
        total = precision + recall
        scores.append(
            {
                'label_id': label,
                'label': ID2LABEL[label],
                'precision': round(precision, 6),
                'recall': round(recall, 6),
                'f1': round(2 * precision * recall / total, 6) if total else 0.0,
                'support': int(gold_here.sum()),
            }
        )
    return scores


PER_LABEL = _per_label_scores(GOLD_IDS, PREDICTED_IDS)
SUPPORTED = [row for row in PER_LABEL if row['support'] > 0]
CONFUSION_MATRIX = [
    [int(((GOLD_IDS == row) & (PREDICTED_IDS == column)).sum()) for column in range(NUM_LABELS)]
    for row in range(NUM_LABELS)
]
METRICS = {
    'accuracy': round(float((PREDICTED_IDS == GOLD_IDS).mean()), 6),
    'macro_f1': round(
        sum(row['f1'] for row in SUPPORTED) / len(SUPPORTED) if SUPPORTED else 0.0, 6
    ),
    'weighted_f1': round(
        sum(row['f1'] * row['support'] for row in SUPPORTED) / GOLD_IDS.size, 6
    )
    if GOLD_IDS.size
    else 0.0,
    'evaluated_rows': int(GOLD_IDS.size),
    'label_map': LABEL2ID,
    'per_label': PER_LABEL,
    'train_loss': round(float(TRAIN_RESULT.training_loss), 6),
}

(OUTPUT_DIR / 'confusion_matrix.json').write_text(
    json.dumps(
        {
            'rows': 'actual',
            'columns': 'predicted',
            'labels': [ID2LABEL[index] for index in range(NUM_LABELS)],
            'matrix': CONFUSION_MATRIX,
            'row_totals': [sum(row) for row in CONFUSION_MATRIX],
            'column_totals': [
                sum(CONFUSION_MATRIX[row][column] for row in range(NUM_LABELS))
                for column in range(NUM_LABELS)
            ],
        },
        indent=2,
        sort_keys=True,
    ),
    encoding='utf-8',
)

with (OUTPUT_DIR / 'predictions.jsonl').open('w', encoding='utf-8') as handle:
    for row, gold_id, predicted_id in zip(EVAL_ROWS, GOLD_IDS, PREDICTED_IDS):
        handle.write(
            json.dumps(
                {
                    'text': row['text'],
                    'actual_intent': ID2LABEL[int(gold_id)],
                    'predicted_intent': ID2LABEL[int(predicted_id)],
                    'correct': bool(gold_id == predicted_id),
                    'predicted_confidence': round(float(PROBABILITIES[int(predicted_id)].max()), 6),
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            + chr(10)
        )

(OUTPUT_DIR / 'metrics.json').write_text(
    json.dumps(METRICS, indent=2, sort_keys=True), encoding='utf-8'
)
print(json.dumps({key: METRICS[key] for key in ('accuracy', 'macro_f1', 'weighted_f1')}, indent=2))
"""

_CELL_SMALL_ARTIFACTS = """\
MODEL_DIR = OUTPUT_DIR / 'model'
TRAINER.save_model(str(MODEL_DIR))
TOKENIZER.save_pretrained(str(MODEL_DIR))
for name in ('optimizer.pt', 'scheduler.pt', 'rng_state.pth', 'trainer_state.json'):
    candidate = OUTPUT_DIR / 'trainer' / name
    if candidate.is_file():
        shutil.copy2(candidate, OUTPUT_DIR / name)

TRAINING_RECORD = {
    'base_model': BASE_MODEL,
    'max_seq_length': MAX_SEQ_LENGTH,
    'learning_rate': LEARNING_RATE,
    'weight_decay': WEIGHT_DECAY,
    'warmup_ratio': WARMUP_RATIO,
    'lr_scheduler_type': LR_SCHEDULER_TYPE,
    'num_train_epochs': NUM_TRAIN_EPOCHS,
    'batch_size': PER_DEVICE_TRAIN_BATCH_SIZE,
    'label_source': LABEL_SOURCE,
    'label_map': LABEL2ID,
    'extra_config': RUN_PARAMS.get('extra_config', {}),
    'completed_steps': int(TRAINER.state.global_step),
    'best_metric': TRAINING_ARGS.metric_for_best_model,
    'best_value': float(TRAINER.state.best_metric or 0.0),
}
write_run_manifest(RUN_MANIFEST)
print(OUTPUT_DIR.name, 'artifacts:', sorted(p.name for p in OUTPUT_DIR.iterdir()))
"""

_CELL_RESOLVE_INPUTS = """\
# A resume checkpoint and an evaluation adapter both arrive the same way: as a
# directory inside an attached dataset. The search is by marker file rather
# than by a fixed path because Kaggle chooses the mount name, and the failure
# message lists what it walked, because "adapter not attached" and "adapter
# attached but empty" produce the same traceback otherwise.


def _mounted_roots():
    roots = []
    for base in (Path('/kaggle/input'), Path('/kaggle/datasets')):
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            roots.append(entry if entry.is_dir() else entry.parent)
    return roots


def _resolve_input_path(name, marker):
    tried = []
    for root in _mounted_roots():
        for candidate in (root / name, root / 'adapter' / name, root / name / 'adapter'):
            tried.append(str(candidate))
            if (candidate / marker).exists():
                return candidate
    for root in _mounted_roots():
        for found in root.rglob(marker):
            if found.parent.name == name or name in found.parts:
                return found.parent
    listing = []
    for root in _mounted_roots():
        listing.extend(
            sorted(str(path.relative_to(root)) for path in root.rglob('*') if path.is_dir())
        )
    raise FileNotFoundError(
        'no directory named {!r} containing {} under the mounted inputs. Tried: {}. '
        'Mounted trees: {}.'.format(name, marker, tried, listing[:80])
    )
"""

_CELL_QWEN_TOKENIZE = r'''\
from datasets import Dataset
from transformers import AutoTokenizer

BASE_MODEL = RUN_PARAMS['qwen_base_model']
MAX_SEQ_LENGTH = RUN_PARAMS['qwen_max_seq_length']
LEARNING_RATE = RUN_PARAMS['qwen_learning_rate']
NUM_TRAIN_EPOCHS = RUN_PARAMS['qwen_num_train_epochs']
PER_DEVICE_TRAIN_BATCH_SIZE = RUN_PARAMS['qwen_per_device_train_batch_size']
GRADIENT_ACCUMULATION_STEPS = RUN_PARAMS['qwen_gradient_accumulation_steps']
WEIGHT_DECAY = RUN_PARAMS['qwen_weight_decay']
WARMUP_RATIO = RUN_PARAMS['warmup_ratio']
LR_SCHEDULER_TYPE = RUN_PARAMS['lr_scheduler_type']
LOGGING_EVERY_N_STEPS = RUN_PARAMS['logging_every_n_steps']
SAVE_EVERY_N_STEPS = RUN_PARAMS['save_every_n_steps']
EVAL_BATCH_SIZE = RUN_PARAMS['eval_batch_size']
SEGMENT_INDEX = RUN_PARAMS['segment_index']
SEGMENT_MAX_STEPS = RUN_PARAMS['max_steps']
RESUME_FROM = RUN_PARAMS['resume_from']
CHECKPOINT_ROOT = OUTPUT_DIR / 'checkpoints'
CHECKPOINT_ROOT.mkdir(parents=True, exist_ok=True)

TOKENIZER = AutoTokenizer.from_pretrained(BASE_MODEL)
if TOKENIZER.pad_token_id is None:
    TOKENIZER.pad_token = TOKENIZER.eos_token


def _render_prompt(record):
    return TOKENIZER.apply_chat_template(
        [
            {'role': 'system', 'content': record['system']},
            {'role': 'user', 'content': record['instruction']},
        ],
        tokenize=False,
        add_generation_prompt=True,
    )


def _completion_only(record):
    """Tokenise one record, masking every prompt position out of the loss."""
    prompt_ids = TOKENIZER(_render_prompt(record), add_special_tokens=False)['input_ids']
    target_ids = TOKENIZER(
        record['response'] + TOKENIZER.eos_token + chr(10), add_special_tokens=False
    )['input_ids']
    input_ids = (prompt_ids + target_ids)[:MAX_SEQ_LENGTH]
    prompt_length = min(len(prompt_ids), len(input_ids))
    if len(input_ids) <= prompt_length:
        # The response was truncated away entirely. Keeping the record would
        # train the model on nothing but masked prompt tokens.
        return None
    return {
        'input_ids': input_ids,
        'attention_mask': [1] * len(input_ids),
        'labels': [-100] * prompt_length + target_ids[: len(input_ids) - prompt_length],
    }


def _build_dataset(rows, label):
    usable = [row for row in rows if isinstance(row.get('instruction'), str)]
    encoded = [pair for pair in map(_completion_only, usable) if pair is not None]
    if not encoded:
        raise ValueError(f'no usable {label} rows survived completion-only masking')
    print(f'{label}: {len(encoded)} usable of {len(usable)} rows')
    return Dataset.from_list(encoded)


TRAIN_FEATURES = _build_dataset(TRAIN_ROWS, 'train')
EVAL_FEATURES = _build_dataset(EVAL_ROWS, 'eval')
print(TRAIN_FEATURES)
'''

_CELL_QWEN_MODEL = """\
from peft import LoraConfig, PeftModel, get_peft_model
from transformers import AutoModelForCausalLM, BitsAndBytesConfig

COMPUTE_DTYPE = torch.bfloat16 if BF16_SUPPORTED else torch.float16
QUANTIZATION = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type='nf4',
    bnb_4bit_use_double_quant=True,
    bnb_4bit_compute_dtype=COMPUTE_DTYPE,
)
BASE = AutoModelForCausalLM.from_pretrained(
    BASE_MODEL,
    quantization_config=QUANTIZATION,
    torch_dtype=COMPUTE_DTYPE,
    device_map={'': 0},
)
BASE.config.use_cache = False

LORA_KWARGS = {
    'r': RUN_PARAMS['lora_r'],
    'lora_alpha': RUN_PARAMS['lora_alpha'],
    'lora_dropout': RUN_PARAMS['lora_dropout'],
    'target_modules': RUN_PARAMS['lora_target_modules'],
    'bias': 'none',
    'task_type': 'CAUSAL_LM',
}


def _resolve_resume(value):
    direct = Path(value)
    if (direct / 'adapter_config.json').is_file() or (direct / 'checkpoint.json').is_file():
        return direct
    return _resolve_input_path(direct.name, 'checkpoint.json')


RESUME_CHECKPOINT = _resolve_resume(RESUME_FROM) if RESUME_FROM else None
if RESUME_CHECKPOINT is not None:
    MODEL = PeftModel.from_pretrained(BASE, str(RESUME_CHECKPOINT), is_trainable=True)
    print(f'resuming trainable adapter from {RESUME_CHECKPOINT}')
else:
    MODEL = get_peft_model(BASE, LoraConfig(**LORA_KWARGS))
    print('starting a fresh adapter')

# Gradient checkpointing plus a frozen base needs the embedding output to carry
# a gradient, or the checkpointed blocks see no trainable input at all.
MODEL.enable_input_require_grads()
MODEL.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
MODEL.print_trainable_parameters()
"""

_CELL_QWEN_ARGS = """\
import trl
from trl import SFTConfig

_SFT_FIELDS = {field.name for field in fields(SFTConfig)}

# trl renamed max_seq_length to max_length and evaluation_strategy to
# eval_strategy. The pin in RUN_PARAMS is the real control; listing both
# spellings and dropping whatever the installed trl does not declare means a
# re-pin fails as a printout of what was ignored rather than as a run that
# quietly trained on the wrong sequence length.
_WANTED_KWARGS = {
    'output_dir': str(CHECKPOINT_ROOT / 'trainer'),
    'per_device_train_batch_size': PER_DEVICE_TRAIN_BATCH_SIZE,
    'per_device_eval_batch_size': EVAL_BATCH_SIZE,
    'gradient_accumulation_steps': GRADIENT_ACCUMULATION_STEPS,
    'gradient_checkpointing': True,
    'optim': 'adamw_torch_fused',
    'lr_scheduler_type': LR_SCHEDULER_TYPE,
    'warmup_ratio': WARMUP_RATIO,
    'learning_rate': LEARNING_RATE,
    'weight_decay': WEIGHT_DECAY,
    'num_train_epochs': NUM_TRAIN_EPOCHS,
    'max_steps': SEGMENT_MAX_STEPS,
    'max_seq_length': MAX_SEQ_LENGTH,
    'max_length': MAX_SEQ_LENGTH,
    'logging_steps': LOGGING_EVERY_N_STEPS,
    'eval_strategy': 'steps',
    'evaluation_strategy': 'steps',
    'eval_steps': SAVE_EVERY_N_STEPS,
    'save_strategy': 'no',
    'bf16': BF16_SUPPORTED,
    'fp16': not BF16_SUPPORTED,
    'max_grad_norm': RUN_PARAMS['max_grad_norm'],
    'report_to': [],
    'remove_unused_columns': False,
    'seed': SEED,
    'data_seed': SEED,
    'packing': False,
}
SFT_ARGS = SFTConfig(**{key: value for key, value in _WANTED_KWARGS.items() if key in _SFT_FIELDS})
print(f'trl {trl.__version__} ignored kwargs: {sorted(set(_WANTED_KWARGS) - _SFT_FIELDS)}')
print(SFT_ARGS)
"""

_CELL_QWEN_TRAINER = '''\
from transformers import DataCollatorForSeq2Seq, TrainerCallback

COLLATOR = DataCollatorForSeq2Seq(tokenizer=TOKENIZER, padding=True, label_pad_token_id=-100)


class PreTokenizedSFTTrainer(trl.SFTTrainer):
    """``trl.SFTTrainer`` with its own tokenisation switched off.

    The prompt/completion boundary is only known to the code that applied the
    chat template. Letting SFTTrainer re-tokenise from text would discard the
    completion-only mask, and the model would be trained to reproduce the
    instruction and the system preamble as readily as the answer.
    """

    def _prepare_dataset(self, dataset, *args, **kwargs):
        return dataset


class NexoCheckpointCallback(TrainerCallback):
    """Write a resumable checkpoint every N optimizer steps and at segment end."""

    def __init__(self, root, model, resumed_step, save_every_n_steps):
        self.root = Path(root)
        self.model = model
        self.resumed_step = int(resumed_step)
        self.save_every_n_steps = int(save_every_n_steps)
        self.last_step = int(resumed_step)
        self.written = []
        self.trainer = None

    def on_step_end(self, args, state, control, **kwargs):
        step = self.resumed_step + int(state.global_step)
        if step == self.last_step or step - self.last_step < self.save_every_n_steps:
            return
        self._write(step, state)
        self.last_step = step
        control.should_save = False

    def on_train_end(self, args, state, control, **kwargs):
        step = self.resumed_step + int(state.global_step)
        if step != self.last_step:
            self._write(step, state)
            self.last_step = step

    def _write(self, step, state):
        destination = self.root / f'step-{step}'
        destination.mkdir(parents=True, exist_ok=True)
        # save_pretrained on a PeftModel emits adapter_model.safetensors and
        # adapter_config.json and nothing else. The 4-bit base weights are never
        # written to disk by this notebook.
        self.model.save_pretrained(str(destination))
        torch.save(self.trainer.optimizer.state_dict(), destination / 'optimizer.pt')
        torch.save(self.trainer.lr_scheduler.state_dict(), destination / 'scheduler.pt')
        torch.save(_capture_rng_state(), destination / 'rng_state.pt')
        trainer_state = asdict(state)
        trainer_state['global_step'] = step
        (destination / 'trainer_state.json').write_text(
            json.dumps(trainer_state, indent=2, sort_keys=True, default=str), encoding='utf-8'
        )
        (destination / 'checkpoint.json').write_text(
            json.dumps(
                {
                    'run_id': RUN_PARAMS['run_id'],
                    'phase': 'phase10-training',
                    'segment_index': SEGMENT_INDEX,
                    'segment_max_steps': SEGMENT_MAX_STEPS,
                    'global_step': step,
                    'base_model': BASE_MODEL,
                    'dataset_slug': RUN_PARAMS['dataset_slug'],
                    'dataset_sha256': DATASET_SHA256,
                    'schema_versions': SCHEMA_VERSIONS,
                    'seed': SEED,
                    'library_versions': RESOLVED_VERSIONS,
                    'micro_batches_per_epoch': MICRO_BATCHES_PER_EPOCH,
                    'resume_micro_batches': int(self.trainer.args.skip_first_batches or 0),
                    'written_at_utc': utc_now(),
                    'files': sorted(path.name for path in destination.iterdir()),
                    'resume_hint': 'pass this step-<n> directory as resume_from to continue',
                },
                indent=2,
                sort_keys=True,
            ),
            encoding='utf-8',
        )
        self.written.append(str(destination))
        print(f'checkpoint written: {destination}')


CHECKPOINT_CALLBACK = NexoCheckpointCallback(
    CHECKPOINT_ROOT, MODEL, 0, SAVE_EVERY_N_STEPS
)
TRAINER = PreTokenizedSFTTrainer(
    model=MODEL,
    args=SFT_ARGS,
    train_dataset=TRAIN_FEATURES,
    eval_dataset=EVAL_FEATURES,
    data_collator=COLLATOR,
    callbacks=[CHECKPOINT_CALLBACK],
)
# The callback needs the optimizer and scheduler the trainer owns, and the
# trainer needs the callback to exist before it starts; the link is made here.
CHECKPOINT_CALLBACK.trainer = TRAINER
'''

_CELL_QWEN_RESUME = """\
# Bit-comparable resume, not nominal resume. Weights are already loaded in
# trainable mode by the model cell; what is left is the optimizer's moment
# accumulators, the schedule's position and the three RNG streams. Restore all
# three and the continuation sees the same dropout draws and the same batches a
# single unbroken run would have seen, which is the whole point of writing a
# checkpoint at all.
MICRO_BATCHES_PER_EPOCH = max(
    1, -(-len(TRAIN_FEATURES) // PER_DEVICE_TRAIN_BATCH_SIZE)
)


def _capture_rng_state():
    state = {
        'torch_rng_state': torch.get_rng_state(),
        'numpy_rng_state': np.random.get_state(),
        'python_rng_state': random.getstate(),
    }
    if torch.cuda.is_available():
        state['cuda_rng_state_all'] = torch.cuda.get_rng_state_all()
    return state


def _restore_rng_state(state):
    torch.set_rng_state(state['torch_rng_state'])
    np.random.set_state(state['numpy_rng_state'])
    random.setstate(state['python_rng_state'])
    if 'cuda_rng_state_all' in state and torch.cuda.is_available():
        torch.cuda.set_rng_state_all(state['cuda_rng_state_all'])


RESUMED_STEP = 0
if RESUME_CHECKPOINT is not None:
    RESUMED_STEP = int(
        json.loads((RESUME_CHECKPOINT / 'trainer_state.json').read_text(encoding='utf-8'))[
            'global_step'
        ]
    )
    CHECKPOINT_CALLBACK.resumed_step = RESUMED_STEP
    CHECKPOINT_CALLBACK.last_step = RESUMED_STEP

    # weights_only=False because an optimizer or scheduler state dict is not
    # always on the allowlist — a LambdaLR carries its lambda objects — and
    # these files were written by this notebook a moment ago, not downloaded.
    optimizer_state = torch.load(
        RESUME_CHECKPOINT / 'optimizer.pt', map_location='cpu', weights_only=False
    )
    scheduler_state = torch.load(
        RESUME_CHECKPOINT / 'scheduler.pt', map_location='cpu', weights_only=False
    )
    rng_state = torch.load(
        RESUME_CHECKPOINT / 'rng_state.pt', map_location='cpu', weights_only=False
    )
    TRAINER.optimizer.load_state_dict(optimizer_state)
    TRAINER.lr_scheduler.load_state_dict(scheduler_state)
    _restore_rng_state(rng_state)

    # Skip exactly the micro-batches a continuous run would already have
    # consumed, aligned down to an optimizer-step boundary so the accumulator
    # is never split across a resume.
    RESUME_MICRO_BATCHES = (RESUMED_STEP * GRADIENT_ACCUMULATION_STEPS) % (
        MICRO_BATCHES_PER_EPOCH
    )
    RESUME_MICRO_BATCHES -= RESUME_MICRO_BATCHES % GRADIENT_ACCUMULATION_STEPS
    TRAINER.args.skip_first_batches = RESUME_MICRO_BATCHES
    print(
        f'resuming at global step {RESUMED_STEP}; skipping {RESUME_MICRO_BATCHES} '
        f'of {MICRO_BATCHES_PER_EPOCH} micro-batches; '
        f'training {SEGMENT_MAX_STEPS} more steps'
    )
else:
    RESUME_MICRO_BATCHES = 0
    print(f'fresh run: training {SEGMENT_MAX_STEPS} steps from step 0')
"""

_CELL_QWEN_TRAIN = """\
TRAIN_RESULT = TRAINER.train()
print(TRAIN_RESULT)

LOG_HISTORY = list(TRAINER.state.log_history)
FINAL_STEP = RESUMED_STEP + int(TRAINER.state.global_step)
EVAL_LOSS = next(
    (row['eval_loss'] for row in reversed(LOG_HISTORY) if 'eval_loss' in row), None
)
TRAIN_LOSS = next((row['loss'] for row in reversed(LOG_HISTORY) if 'loss' in row), None)
print(
    json.dumps(
        {'global_step': FINAL_STEP, 'train_loss': TRAIN_LOSS, 'eval_loss': EVAL_LOSS},
        indent=2,
    )
)
"""

_CELL_QWEN_ARTIFACTS = """\
ADAPTER_DIR = OUTPUT_DIR / 'adapter'
MODEL.save_pretrained(str(ADAPTER_DIR))
TOKENIZER.save_pretrained(str(ADAPTER_DIR))

final_checkpoint = CHECKPOINT_ROOT / f'step-{FINAL_STEP}'
if final_checkpoint.is_dir():
    for name in ('trainer_state.json', 'checkpoint.json'):
        shutil.copy2(final_checkpoint / name, OUTPUT_DIR / name)

TRAINING_RECORD = {
    'base_model': BASE_MODEL,
    'adapter_dir': ADAPTER_DIR.name,
    'segment_index': SEGMENT_INDEX,
    'segment_max_steps': SEGMENT_MAX_STEPS,
    'resumed_from': str(RESUME_CHECKPOINT) if RESUME_CHECKPOINT else None,
    'resumed_at_step': RESUMED_STEP,
    'completed_steps': FINAL_STEP,
    'resume_micro_batches': RESUME_MICRO_BATCHES,
    'save_every_n_steps': SAVE_EVERY_N_STEPS,
    'checkpoints': sorted(
        path.name for path in CHECKPOINT_ROOT.iterdir() if path.is_dir()
    ),
    'max_seq_length': MAX_SEQ_LENGTH,
    'learning_rate': LEARNING_RATE,
    'weight_decay': WEIGHT_DECAY,
    'warmup_ratio': WARMUP_RATIO,
    'lr_scheduler_type': LR_SCHEDULER_TYPE,
    'num_train_epochs': NUM_TRAIN_EPOCHS,
    'batch_size': PER_DEVICE_TRAIN_BATCH_SIZE,
    'gradient_accumulation_steps': GRADIENT_ACCUMULATION_STEPS,
    'optimizer': 'adamw_torch_fused',
    'precision': 'bf16' if BF16_SUPPORTED else 'fp16',
    'gradient_checkpointing': True,
    'packing': False,
    'completion_only_loss': True,
    'lora': dict(LORA_KWARGS, target_modules=list(LORA_KWARGS['target_modules'])),
    'extra_config': RUN_PARAMS.get('extra_config', {}),
}
METRICS = {
    'train_loss': None if TRAIN_LOSS is None else round(float(TRAIN_LOSS), 6),
    'eval_loss': None if EVAL_LOSS is None else round(float(EVAL_LOSS), 6),
    'completed_steps': FINAL_STEP,
    'checkpoints_written': len(CHECKPOINT_CALLBACK.written),
    'trainable_rows': len(TRAIN_FEATURES),
    'eval_rows_encoded': len(EVAL_FEATURES),
}
write_run_manifest(RUN_MANIFEST)
print(OUTPUT_DIR.name, 'artifacts:', sorted(path.name for path in OUTPUT_DIR.iterdir()))
"""

_CELL_EVAL_SETUP = """\
# Base and fine-tuned are shown the same prompt, decoded greedily, from the
# same seed. Every one of those conditions is load-bearing: a delta measured
# under a different system preamble or with sampling on measures the prompt,
# not the adapter.
EVAL_MAX_ROWS = RUN_PARAMS['eval_max_rows']
if len(EVAL_ROWS) > EVAL_MAX_ROWS:
    print(f'evaluating the first {EVAL_MAX_ROWS} of {len(EVAL_ROWS)} held-out rows')
EVAL_ROWS = EVAL_ROWS[:EVAL_MAX_ROWS]

BASE_MODEL = RUN_PARAMS['qwen_base_model']
MAX_NEW_TOKENS = RUN_PARAMS['eval_max_new_tokens']
EVAL_BATCH_SIZE = RUN_PARAMS['eval_batch_size']
ADAPTER_DIR = _resolve_input_path(RUN_PARAMS['adapter_dir_name'], 'adapter_config.json')
print(f'adapter under evaluation: {ADAPTER_DIR}')

from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

TOKENIZER = AutoTokenizer.from_pretrained(BASE_MODEL)
if TOKENIZER.pad_token_id is None:
    TOKENIZER.pad_token = TOKENIZER.eos_token
# Left padding puts the prompt at a fixed width across the batch, so the
# continuation can be sliced off by index instead of by searching for the
# prompt text back inside the decoded output.
TOKENIZER.padding_side = 'left'


def _render_prompt(record):
    return TOKENIZER.apply_chat_template(
        [
            {'role': 'system', 'content': record['system']},
            {'role': 'user', 'content': record['instruction']},
        ],
        tokenize=False,
        add_generation_prompt=True,
    )


PROMPTS = [_render_prompt(row) for row in EVAL_ROWS]
"""

_CELL_EVAL_RUBRIC = r'''\
import re

# A deterministic rubric, not an LLM judge. It is small, reproducible and free,
# and it is honest about what it measures: whether the answer takes an action
# a person can take, refers to the thing it was asked about, stays inside a
# sane length, refuses to claim it already executed anything, and leaks no
# credential. It cannot tell a good sentence from a merely acceptable one.
_ACTION_VERBS = frozenset(
    {
        'add',
        'added',
        'block',
        'blocked',
        'break',
        'complete',
        'completed',
        'confirm',
        'consider',
        'create',
        'created',
        'delay',
        'delete',
        'deleted',
        'move',
        'moved',
        'prioritise',
        'prioritise',
        'prioritize',
        'propose',
        'reduce',
        'reduced',
        'reschedule',
        'rescheduled',
        'review',
        'schedule',
        'scheduled',
        'shorten',
        'split',
        'start',
        'started',
        'suggest',
        'update',
        'updated',
    }
)
_STOPWORDS = frozenset(
    {
        'a',
        'an',
        'and',
        'as',
        'at',
        'be',
        'by',
        'can',
        'do',
        'for',
        'from',
        'how',
        'i',
        'in',
        'is',
        'it',
        'me',
        'my',
        'of',
        'on',
        'or',
        'please',
        'should',
        'that',
        'the',
        'this',
        'to',
        'what',
        'when',
        'which',
        'with',
        'you',
    }
)
_AUTO_EXECUTION = re.compile(
    r"\b(?:i (?:have |'ve )(?:added|created|scheduled|moved|rescheduled|updated|"
    r"completed|blocked|deleted|started)|already (?:added|created|scheduled|"
    r"moved|rescheduled|updated|completed|blocked)|(?:done|completed)[.!]?\s*$)\b",
    re.IGNORECASE,
)
_CREDENTIAL = re.compile(
    r'KAGG(?:LE)?_[A-Za-z0-9_]{16,}|\bhf_[A-Za-z0-9]{20,}|\bsk-[A-Za-z0-9_-]{20,}'
    r'|\bgh[pousr]_[A-Za-z0-9]{20,}|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b'
    r'|-----BEGIN [A-Z ]*PRIVATE KEY-----'
)
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]+")


def _words(text):
    return [word.casefold() for word in _WORD.findall(text)]


def _score_response(instruction, response):
    """Return the rubric's individual verdicts for one response."""
    response_words = _words(response)
    content = [word for word in _words(instruction) if word not in _STOPWORDS]
    response_set = set(response_words)
    echoed = sum(1 for word in content if word in response_set)
    # One echo is enough to have named the subject; the ratio only guards
    # against an answer that name-checks one word and ignores the rest, so a
    # two-word instruction is not held to a proportion it cannot meet.
    names_the_entity = echoed >= 1 and echoed / len(content) >= 0.25 if content else False
    return {
        'takes_an_action': bool(_ACTION_VERBS & response_set),
        'names_the_entity': names_the_entity,
        'offers_no_auto_execution': _AUTO_EXECUTION.search(response) is None,
        'within_length': 5 <= len(response_words) <= 180,
        'no_credential': _CREDENTIAL.search(response) is None,
    }


RUBRIC_KEYS = (
    'takes_an_action',
    'names_the_entity',
    'offers_no_auto_execution',
    'within_length',
    'no_credential',
)
'''

_CELL_EVAL_GENERATE = """\
COMPUTE_DTYPE = torch.bfloat16 if BF16_SUPPORTED else torch.float16
QUANTIZATION = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type='nf4',
    bnb_4bit_use_double_quant=True,
    bnb_4bit_compute_dtype=COMPUTE_DTYPE,
)
MODEL_EVAL = AutoModelForCausalLM.from_pretrained(
    BASE_MODEL,
    quantization_config=QUANTIZATION,
    torch_dtype=COMPUTE_DTYPE,
    device_map={'': 0},
)


def _generate(model, prompts):
    generations = []
    for start in range(0, len(prompts), EVAL_BATCH_SIZE):
        batch = prompts[start : start + EVAL_BATCH_SIZE]
        encoded = TOKENIZER(
            batch, return_tensors='pt', padding=True, add_special_tokens=False
        ).to(model.device)
        prompt_width = encoded['input_ids'].shape[1]
        with torch.no_grad():
            generated = model.generate(
                **encoded,
                max_new_tokens=MAX_NEW_TOKENS,
                do_sample=False,
                num_beams=1,
                pad_token_id=TOKENIZER.pad_token_id,
            )
        for sequence in generated:
            generations.append(
                TOKENIZER.decode(sequence[prompt_width:], skip_special_tokens=True).strip()
            )
        print(f'  generated {len(generations)}/{len(prompts)}')
    return generations


set_seed(SEED)
BASE_GENERATIONS = _generate(MODEL_EVAL, PROMPTS)
print(f'base done: {len(BASE_GENERATIONS)} responses')
"""

_CELL_EVAL_TUNED = """\
from peft import PeftModel

# Attaching in place is deliberate: the base generations were taken from the
# unmodified model, so the two sides differ by the adapter and nothing else.
TUNED_MODEL = PeftModel.from_pretrained(MODEL_EVAL, str(ADAPTER_DIR))
TUNED_MODEL.eval()
set_seed(SEED)
TUNED_GENERATIONS = _generate(TUNED_MODEL, PROMPTS)
print(f'fine-tuned done: {len(TUNED_GENERATIONS)} responses')
"""

_CELL_EVAL_REPORT = """\
def _summarise(generations):
    scores = [_score_response(row['instruction'], text) for row, text in zip(EVAL_ROWS, generations)]
    per_criterion = {
        key: round(sum(1 for score in scores if score[key]) / len(scores), 6) if scores else 0.0
        for key in RUBRIC_KEYS
    }
    strict = sum(1 for score in scores if all(score.values())) / len(scores) if scores else 0.0
    return per_criterion, strict, scores


BASE_CRITERIA, BASE_STRICT, BASE_SCORES = _summarise(BASE_GENERATIONS)
TUNED_CRITERIA, TUNED_STRICT, TUNED_SCORES = _summarise(TUNED_GENERATIONS)

WINS = sum(1 for base, tuned in zip(BASE_SCORES, TUNED_SCORES) if sum(tuned.values()) > sum(base.values()))
LOSSES = sum(
    1 for base, tuned in zip(BASE_SCORES, TUNED_SCORES) if sum(tuned.values()) < sum(base.values())
)
TIES = len(BASE_SCORES) - WINS - LOSSES

EVAL_METRICS = {
    'evaluator': 'deterministic-rubric-v1',
    'evaluated_rows': len(EVAL_ROWS),
    'max_new_tokens': MAX_NEW_TOKENS,
    'decoding': 'greedy',
    'base': {'criteria': BASE_CRITERIA, 'strict_pass_rate': round(BASE_STRICT, 6)},
    'fine_tuned': {'criteria': TUNED_CRITERIA, 'strict_pass_rate': round(TUNED_STRICT, 6)},
    'delta_strict_pass_rate': round(TUNED_STRICT - BASE_STRICT, 6),
    'paired': {'fine_tuned_better': WINS, 'tie': TIES, 'fine_tuned_worse': LOSSES},
}

TRAINING_RECORD = {
    'base_model': BASE_MODEL,
    'adapter_dir_name': RUN_PARAMS['adapter_dir_name'],
    'adapter_path': str(ADAPTER_DIR),
    'eval_max_rows': EVAL_MAX_ROWS,
    'extra_config': RUN_PARAMS.get('extra_config', {}),
}
METRICS = EVAL_METRICS
write_run_manifest(build_run_manifest())

with (OUTPUT_DIR / 'eval_examples.jsonl').open('w', encoding='utf-8') as handle:
    for index, (row, base_text, tuned_text) in enumerate(
        zip(EVAL_ROWS, BASE_GENERATIONS, TUNED_GENERATIONS)
    ):
        handle.write(
            json.dumps(
                {
                    'index': index,
                    'instruction': row['instruction'],
                    'base_response': base_text,
                    'fine_tuned_response': tuned_text,
                    'base_scores': BASE_SCORES[index],
                    'fine_tuned_scores': TUNED_SCORES[index],
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            + chr(10)
        )

print(
    '\\n'.join(
        [
            f'criterion                  base    fine-tuned   delta',
            *[
                '{:<25} {:.3f}   {:.3f}   {:+.3f}'.format(
                    key,
                    BASE_CRITERIA[key],
                    TUNED_CRITERIA[key],
                    TUNED_CRITERIA[key] - BASE_CRITERIA[key],
                )
                for key in RUBRIC_KEYS
            ],
            '{:<25} {:.3f}   {:.3f}   {:+.3f}'.format(
                'strict_pass_rate', BASE_STRICT, TUNED_STRICT, TUNED_STRICT - BASE_STRICT
            ),
            f'paired: better {WINS} / tie {TIES} / worse {LOSSES}',
        ]
    )
)
"""

_CELL_RUN_PARAMS = """\
# Baked in at render time from the training config, never resolved from the
# environment at run time: a notebook that picks up its hyperparameters late
# produces a manifest that describes a run nobody performed.
RUN_PARAMS = __PARAMS__
if RUN_PARAMS.get('extra_config'):
    print(f"config keys this renderer did not recognise: {sorted(RUN_PARAMS['extra_config'])}")
print(json.dumps(RUN_PARAMS, indent=2, sort_keys=True, default=str))
"""

_CELL_MANIFEST_BUILD = """\
# The manifest is what makes a run reproducible from its outputs alone: the
# dataset hash, the resolved library versions, the seed and the
# hyperparameters travel with the artifact. The field names are checked against
# the ones the local pipeline parses, so a rename on either side fails here
# rather than in Phase 11.
RUN_MANIFEST_FIELDS = __FIELDS__


def build_run_manifest():
    return {
        'phase': 'phase10-training',
        'run_id': RUN_PARAMS['run_id'],
        'created_at_utc': utc_now(),
        'seed': SEED,
        'dataset_slug': RUN_PARAMS['dataset_slug'],
        'dataset_schema_version': SCHEMA_VERSIONS[0]
        if len(SCHEMA_VERSIONS) == 1
        else SCHEMA_VERSIONS,
        'dataset_sha256': DATASET_SHA256,
        'train_rows': len(TRAIN_ROWS),
        'eval_rows': len(EVAL_ROWS),
        'base_model': BASE_MODEL,
        'model_output': OUTPUT_DIR.name,
        'library_versions': RESOLVED_VERSIONS,
        'environment': {
            'device': DEVICE,
            'bf16': BF16_SUPPORTED,
            'platform': platform.platform(),
            'python': sys.version,
        },
        'training': TRAINING_RECORD,
        'metrics': METRICS,
        'artifacts': sorted(
            str(path.relative_to(OUTPUT_DIR)) for path in OUTPUT_DIR.rglob('*') if path.is_file()
        ),
    }


def write_run_manifest(payload):
    missing = sorted(set(RUN_MANIFEST_FIELDS) - set(payload))
    unexpected = sorted(set(payload) - set(RUN_MANIFEST_FIELDS))
    if missing or unexpected:
        raise ValueError(
            'run manifest field mismatch: missing={} unexpected={}'.format(missing, unexpected)
        )
    path = OUTPUT_DIR / 'run_manifest.json'
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')
    print(f'wrote {path}')
    return path
"""


def _install_cell(config: Mapping[str, Any]) -> dict[str, Any]:
    """Build the first code cell: pin, install, and print what actually resolved.

    Args:
        config: The merged training config carrying the version pins.

    Returns:
        A code cell.
    """
    pins = " ".join(f"{package}=={config[f'{package}_version']}" for package in _PINNED_PACKAGES)
    source = f"{_CELL_INSTALL}%pip install --quiet {pins}\n{_CELL_INSTALL_TAIL}"
    return _code("pip-install-pins", source)


def _params_cell(params: Mapping[str, Any]) -> dict[str, Any]:
    """Build the cell that pins the run's parameters into the notebook.

    Args:
        params: The run parameters, all JSON-representable.

    Returns:
        A code cell.
    """
    return _code("run-parameters", _CELL_RUN_PARAMS.replace("__PARAMS__", repr(dict(params))))


def _dataset_cells(dataset_slug: str, *, load_train: bool) -> list[dict[str, Any]]:
    """Build the cells that find and load the attached dataset.

    An evaluation run does not need the training split, and loading it would
    put a file into the run that the manifest then has to explain.

    Args:
        dataset_slug: The Kaggle dataset slug, optionally ``owner/name``.
        load_train: Whether to load the train split as well as the eval split.

    Returns:
        Locate, load and report cells, in execution order.

    Raises:
        DatasetError: The slug is not a safe path-shaped name.
    """
    _require_safe_name(dataset_slug, field="dataset_slug", allow_slash=True)
    prologue = _CELL_DATASET.replace("RUN_PARAMS['dataset_slug']", repr(dataset_slug))
    tail = _CELL_DATASET_TAIL if load_train else _CELL_DATASET_EVAL_TAIL
    report = _CELL_DATASET_REPORT if load_train else _CELL_DATASET_REPORT_EVAL
    return [
        _code("locate-dataset", prologue),
        _code("load-splits", tail),
        _code("report-splits", report),
    ]


def _manifest_cell() -> dict[str, Any]:
    """Build the cell that defines the run-manifest writer.

    Returns:
        A code cell.
    """
    source = _CELL_MANIFEST_BUILD.replace("__FIELDS__", repr(list(RUN_MANIFEST_FIELDS)))
    return _code("run-manifest", source)


def _validate_notebook(text: str, *, require_install: bool = True) -> dict[str, Any]:
    """Prove a rendered document is well-formed before handing it back.

    nbformat is not importable here, so the invariants that matter are checked
    directly: the JSON round-trips, every cell carries an id, the ids are
    unique and legal, the Kaggle metadata block is present, the first code cell
    really is the install, and nothing in the text looks like a credential.

    Args:
        text: The serialised notebook.
        require_install: Whether the first code cell must be the pinned
            ``pip install``. Every notebook that *uses* a third-party library sets
            it, because an unpinned import is how a run turns into a rerun. The
            accelerator probe is the deliberate exception: it trains nothing, so it
            installs nothing — and on a session without a network it could not
            install anything even if it wanted to.

    Returns:
        The decoded document.

    Raises:
        DatasetError: Any of those invariants fails.
    """
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DatasetError(f"rendered notebook is not valid JSON: {exc}") from exc
    if json.loads(json.dumps(document)) != document:
        raise DatasetError("rendered notebook does not round-trip through JSON")
    if document.get("nbformat") != NBFORMAT_VERSION or document.get("nbformat_minor") < 5:
        raise DatasetError(
            f"rendered notebook must declare nbformat {NBFORMAT_VERSION}.{NBFORMAT_MINOR}"
        )

    cells = document.get("cells")
    if not isinstance(cells, list) or not cells:
        raise DatasetError("rendered notebook has no cells")
    seen: set[str] = set()
    code_sources: list[str] = []
    for cell in cells:
        cell_id = cell.get("id")
        if not isinstance(cell_id, str) or _CELL_ID.fullmatch(cell_id) is None:
            raise DatasetError(
                f"cell without a usable id would draw MissingIDFieldWarning: {cell_id!r}"
            )
        if cell_id in seen:
            raise DatasetError(f"duplicate cell id {cell_id!r}")
        seen.add(cell_id)
        source = "".join(cell.get("source", []))
        kind = find_credential(source)
        if kind is not None:
            raise DatasetError(
                f"cell {cell_id!r} contains credential-shaped text of kind {kind!r}; "
                "refusing to emit it into a notebook and a log"
            )
        if cell.get("cell_type") == "code":
            code_sources.append(source)

    kaggle = document.get("metadata", {}).get("kaggle")
    if not isinstance(kaggle, dict):
        raise DatasetError("rendered notebook has no metadata.kaggle block")
    for key in ("enable_gpu", "enable_internet"):
        if not isinstance(kaggle.get(key), bool):
            raise DatasetError(f"metadata.kaggle must set {key} to a bool")
    if require_install and (not code_sources or "pip install" not in code_sources[0]):
        raise DatasetError("the first code cell must be the pinned pip install")
    return document


def _document(
    cells: list[dict[str, Any]],
    *,
    dataset_slug: str,
    enable_gpu: bool = True,
    enable_internet: bool = True,
    require_install: bool = True,
) -> str:
    """Assemble, validate and serialise a notebook.

    Args:
        cells: The cells, in execution order.
        dataset_slug: The attached dataset, recorded so Kaggle can bind it.
        enable_gpu: Whether the kernel asks for a GPU.
        enable_internet: Whether the kernel asks for network access.
        require_install: Whether the first code cell must be the pinned install.

    Returns:
        An nbformat 4.5 document as JSON text.

    Raises:
        DatasetError: The document failed validation.
    """
    kaggle: dict[str, Any] = {
        "enable_gpu": enable_gpu,
        "enable_internet": enable_internet,
        "language": "python",
        "keywords": ["nexo", "phase10", "training"],
    }
    if "/" in dataset_slug:
        kaggle["dataset_sources"] = [dataset_slug]
    text = json.dumps(
        {
            "cells": cells,
            "metadata": {
                "kaggle": kaggle,
                "kernelspec": {
                    "display_name": "Python 3",
                    "language": "python",
                    "name": "python3",
                },
                "language_info": {"name": "python", "version": "3.13"},
            },
            "nbformat": NBFORMAT_VERSION,
            "nbformat_minor": NBFORMAT_MINOR,
        },
        indent=1,
        ensure_ascii=False,
    )
    _validate_notebook(text, require_install=require_install)
    return text


def _small_title(base_model: str, dataset_slug: str, run_id: str, output_dir_name: str) -> str:
    """Write the small-model notebook's opening markdown.

    Args:
        base_model: The encoder being fine-tuned.
        dataset_slug: The attached dataset.
        run_id: This run's identifier.
        output_dir_name: The directory the run writes under ``/kaggle/working``.

    Returns:
        Markdown source.
    """
    return f"""\
# Phase 10 — intent router training

Run `{run_id}`, dataset `{dataset_slug}`.

**What this run is.** Supervised training of Nexo's intent router: `{base_model}`
fine-tuned as a sequence classifier over the `routing_intent.v1` dataset. It maps
one user utterance to one intent from a taxonomy that already exists. It does
not discover routes, and it does not decide what Nexo may do — it only labels
what a person asked for.

**What it produces.** Everything under `/kaggle/working/{output_dir_name}/`:
`model/` (weights, tokenizer, `label_map.json`), `predictions.jsonl` and
`confusion_matrix.json` for the held-out split, `metrics.json`, and
`run_manifest.json` carrying the seed, the resolved library versions, the
hyperparameters and the dataset hash.

**What it is not.** {_RULED_OUT} The deterministic services behind
`analytics/scoring.py` and `risk/scoring.py` stay exactly as they are and stay
the fallback; a classifier that cannot be trusted is worse than the rule that
always runs. {run_id} produces an artifact, not a behaviour change.
"""


def _qwen_title(base_model: str, dataset_slug: str, run_id: str, output_dir_name: str) -> str:
    """Write the Qwen notebook's opening markdown.

    Args:
        base_model: The base model being adapted.
        dataset_slug: The attached dataset.
        run_id: This run's identifier.
        output_dir_name: The directory the run writes under ``/kaggle/working``.

    Returns:
        Markdown source.
    """
    return f"""\
# Phase 10 — {base_model.rsplit("/", 1)[-1]} QLoRA fine-tune, run {run_id}

Dataset `{dataset_slug}`, writing to `/kaggle/working/{output_dir_name}/`.

**What this run is.** One segment of a supervised fine-tune of `{base_model}` with
a LoRA adapter over the `qwen_sft.v1` dataset, 4-bit quantised on the fly. The
system preamble is Nexo's own; the model is being taught to answer inside it.

**What it produces.** A LoRA adapter — `adapter_model.safetensors` and
`adapter_config.json` — plus the tokenizer, the trainer state and a
`run_manifest.json`. Resumable checkpoints land under
`{output_dir_name}/checkpoints/step-<n>/`, each holding the adapter, the optimizer
state, the scheduler state and the RNG state of torch, numpy and random.

**What it is not.** {_RULED_OUT} The adapter is an artifact that Phase 11 may or
may not ever load. NEXUS still never auto-executes a recommendation: every one
of the twelve `RecommendationType` values names an action a *person* takes, and
this fine-tune is no more entitled to act on one than the deterministic engine
is.
"""


def _base_weights_notebook(base_model: str) -> str:
    """Write the markdown cell stating what is and is not written to disk.

    Args:
        base_model: The base model whose weights must never be saved.

    Returns:
        Markdown source.
    """
    return f"""\
## The base weights are never written

`{base_model}` is downloaded, quantised to 4-bit NF4 in memory and thrown away
at the end of the session. **No cell in this notebook saves it**, and no output
contains it. Every `save_pretrained` call in this notebook is made on a
`PeftModel`, which emits `adapter_model.safetensors` and `adapter_config.json`
and nothing else — the trainable parameters are the LoRA matrices, a few tens of
megabytes, and they are the entire deliverable.

This is deliberate on three counts. The adapter is small enough to version, to
diff, to attach to a Kaggle dataset for a later evaluation run, and to ship as a
reviewable artifact. Re-uploading 8B of weights per segment would cost more GPU
time than the training did. And a checkpoint directory that quietly holds a full
copy of a base model is a checkpoint directory that ends up in an artifact store
somewhere, which is a supply-chain problem nobody signed up for.

If a checkpoint ever appears to contain anything other than the adapter, treat
it as a bug in this notebook and not as an intended output.
"""


def _resume_notebook(run_id: str) -> str:
    """Write the markdown cell explaining the segment and resume semantics.

    Args:
        run_id: This run's identifier.

    Returns:
        Markdown source.
    """
    return f"""\
## Segments, and what "resume" has to mean

`{run_id}` is one segment of a fine-tune that will not fit in one Kaggle
session. `max_steps` is the number of optimizer steps **this segment**
adds, and `resume_from` is a previous `step-<n>` checkpoint directory — uploaded
as a dataset and mounted under `/kaggle/input`.

A resumed segment restores four things: the adapter in trainable mode, the
optimizer's moment accumulators, the schedule's position, and the RNG state of
`torch`, `numpy` and `random`. It then skips exactly the micro-batches an
unbroken run would already have consumed, floored to an optimizer-step boundary.

All four matter. Weights alone give you a model that has forgotten what step it
was on. Adding the optimizer and scheduler gives you a run whose learning-rate
curve is continuous but whose dropout draws are not, which costs a little
accuracy and shows up in no diagnostic. The skipped batches give you the same
examples in the same order. Together they make a resumed run bit-comparable to
an uninterrupted one — which is the only reason to checkpoint at all, because
otherwise a two-segment run and a one-segment run are simply different runs with
the same name.
"""


def render_small_training_notebook(
    *,
    config: Mapping[str, Any],
    dataset_slug: str,
    run_id: str,
    output_dir_name: str = "nexo_routing_model",
) -> str:
    """Render the notebook that trains the small intent router.

    The notebook locates the attached dataset, reads the label map it ships or
    derives one from the train split, fits an ``AutoModelForSequenceClassification``
    with ``num_labels`` heads, and writes the trained model, its predictions and
    a confusion matrix for the held-out split into ``/kaggle/working``.

    Args:
        config: Training configuration. Keys are those in the module's defaults;
            an unrecognised key is carried into the notebook rather than
            dropped, and a key that looks like a typo of a known one is refused.
        dataset_slug: The Kaggle dataset slug, optionally ``owner/name``.
        run_id: This run's identifier.
        output_dir_name: Directory under ``/kaggle/working`` to write into.

    Returns:
        An nbformat 4.5 document as JSON text.

    Raises:
        DatasetError: The configuration, the identifiers, or the rendered
            document is not acceptable.
    """
    merged, extras = _merge_config(config)
    _require_safe_name(run_id, field="run_id")
    _require_safe_name(output_dir_name, field="output_dir_name")
    base_model = merged["small_base_model"]
    title = _small_title(base_model, dataset_slug, run_id, output_dir_name)

    params = {
        **merged,
        "extra_config": extras,
        "run_id": run_id,
        "dataset_slug": dataset_slug,
        "output_dir_name": output_dir_name,
    }

    cells: list[dict[str, Any]] = [_markdown("what-this-run-is", title.rstrip("\n"))]
    cells.append(_install_cell(merged))
    cells.append(_params_cell(params))
    cells.append(_code("runtime", _CELL_RUNTIME))
    cells.extend(_dataset_cells(dataset_slug, load_train=True))
    cells.append(_code("label-map", _CELL_SMALL_LABELS))
    cells.append(_code("tokenize", _CELL_SMALL_TOKENIZE))
    cells.append(_code("train", _CELL_SMALL_TRAIN))
    cells.append(_code("score-held-out", _CELL_SMALL_SCORE))
    cells.append(_manifest_cell())
    cells.append(_code("write-artifacts", _CELL_SMALL_ARTIFACTS))
    return _document(cells, dataset_slug=dataset_slug)


def render_qwen_training_notebook(
    *,
    config: Mapping[str, Any],
    dataset_slug: str,
    run_id: str,
    segment_index: int,
    max_steps: int,
    resume_from: str | None = None,
    output_dir_name: str = "nexo_qwen_adapter",
) -> str:
    """Render one segment of the Qwen QLoRA fine-tune.

    The notebook installs the pinned stack, loads the attached dataset, masks the
    prompt out of the loss, attaches a fresh or resumed LoRA adapter to the 4-bit
    base model, trains with ``trl.SFTTrainer``, writes a resumable checkpoint
    every ``save_every_n_steps`` and at the end of the segment, and copies the
    adapter plus a ``run_manifest.json`` into ``/kaggle/working``.

    Args:
        config: Training configuration. Keys are those in the module's defaults;
            an unrecognised key is carried into the notebook rather than
            dropped, and a key that looks like a typo of a known one is refused.
        dataset_slug: The Kaggle dataset slug, optionally ``owner/name``.
        run_id: This run's identifier.
        segment_index: Which segment this is, recorded in every checkpoint.
        max_steps: Optimizer steps this segment adds.
        resume_from: A ``step-<n>`` checkpoint directory, either absolute or the
            name of one mounted under ``/kaggle/input``.
        output_dir_name: Directory under ``/kaggle/working`` to write into.

    Returns:
        An nbformat 4.5 document as JSON text.

    Raises:
        DatasetError: The configuration, the identifiers, or the rendered
            document is not acceptable.
    """
    merged, extras = _merge_config(config)
    _require_safe_name(run_id, field="run_id")
    _require_safe_name(output_dir_name, field="output_dir_name")
    _require_int(segment_index, field="segment_index", minimum=0)
    _require_int(max_steps, field="max_steps", minimum=1)
    if resume_from is not None:
        _require_safe_path(resume_from, field="resume_from")
    base_model = merged["qwen_base_model"]

    params = {
        **merged,
        "extra_config": extras,
        "run_id": run_id,
        "dataset_slug": dataset_slug,
        "output_dir_name": output_dir_name,
        "segment_index": segment_index,
        "max_steps": max_steps,
        "resume_from": resume_from,
    }

    cells: list[dict[str, Any]] = [
        _markdown(
            "what-this-run-is",
            _qwen_title(base_model, dataset_slug, run_id, output_dir_name).rstrip("\n"),
        ),
        _markdown("base-weights-are-never-saved", _base_weights_notebook(base_model)),
        _markdown("segments-and-resume", _resume_notebook(run_id)),
        _install_cell(merged),
        _params_cell(params),
        _code("runtime", _CELL_RUNTIME),
    ]
    cells.extend(_dataset_cells(dataset_slug, load_train=True))
    cells.extend(
        [
            _code("locate-adapter-or-resume", _CELL_RESOLVE_INPUTS),
            _code("completion-only-tokenisation", _CELL_QWEN_TOKENIZE),
            _code("base-model-and-adapter", _CELL_QWEN_MODEL),
            _code("training-arguments", _CELL_QWEN_ARGS),
            _code("trainer-and-checkpoint-callback", _CELL_QWEN_TRAINER),
            _code("restore-resume-state", _CELL_QWEN_RESUME),
            _code("train", _CELL_QWEN_TRAIN),
            _manifest_cell(),
            _code("write-artifacts", _CELL_QWEN_ARTIFACTS),
        ]
    )
    return _document(cells, dataset_slug=dataset_slug)


def render_eval_notebook(
    *,
    config: Mapping[str, Any],
    dataset_slug: str,
    run_id: str,
    adapter_dir_name: str,
) -> str:
    """Render the notebook that compares base Qwen against the fine-tuned adapter.

    Both sides see the same held-out prompts, the same system preamble, the same
    greedy decoding and the same seed, so the difference in the report is
    attributable to the adapter and to nothing else. The rubric is deterministic:
    it checks that the answer names the subject, takes an action a person can
    take, offers no claim of having already executed anything, stays inside a
    sane length and leaks no credential.

    Args:
        config: Training configuration, for the pins and the decoding settings.
        dataset_slug: The Kaggle dataset slug holding the held-out split.
        run_id: This evaluation's identifier.
        adapter_dir_name: Name of the mounted directory holding
            ``adapter_config.json``.

    Returns:
        An nbformat 4.5 document as JSON text.

    Raises:
        DatasetError: The configuration, the identifiers, or the rendered
            document is not acceptable.
    """
    merged, extras = _merge_config(config)
    _require_safe_name(run_id, field="run_id")
    _require_safe_name(adapter_dir_name, field="adapter_dir_name")
    output_dir_name = merged["eval_output_dir_name"]
    _require_safe_name(output_dir_name, field="eval_output_dir_name")
    base_model = merged["qwen_base_model"]

    params = {
        **merged,
        "extra_config": extras,
        "run_id": run_id,
        "dataset_slug": dataset_slug,
        "output_dir_name": output_dir_name,
        "adapter_dir_name": adapter_dir_name,
    }

    title = f"""\
# Phase 10 — base vs fine-tuned evaluation

Run `{run_id}`, adapter `{adapter_dir_name}`, dataset `{dataset_slug}`.

**What this run is.** A paired evaluation. The same held-out prompts are put to
`{base_model}` twice — once bare, once with the LoRA adapter attached — decoded
greedily from the same seed, and scored by the same deterministic rubric. The
only difference between the two sides is the adapter.

**What it produces.** `eval_report.json` and `eval_examples.jsonl` under
`/kaggle/working/{output_dir_name}/`, holding per-criterion pass rates, a paired
win/tie/loss count, and every generated response next to the rubric's verdicts on
it. Machine-readable, because a number that only exists in a scrollback buffer
does not survive to the pull request that has to justify the adapter.

**What it is not.** {_RULED_OUT} Nothing here changes routing, and nothing here
is evidence about runtime quality — it measures one rubric, on one split, at one
decoding setting. A rubric pass rate is a floor, not a ceiling.
"""

    cells: list[dict[str, Any]] = [
        _markdown("what-this-run-is", title.rstrip("\n")),
        _install_cell(merged),
        _params_cell(params),
        _code("runtime", _CELL_RUNTIME),
    ]
    cells.extend(_dataset_cells(dataset_slug, load_train=False))
    cells.extend(
        [
            _code("locate-adapter", _CELL_RESOLVE_INPUTS),
            _code("eval-setup", _CELL_EVAL_SETUP),
            _code("rubric", _CELL_EVAL_RUBRIC),
            _code("generate-with-base", _CELL_EVAL_GENERATE),
            _code("generate-with-adapter", _CELL_EVAL_TUNED),
            _manifest_cell(),
            _code("score-and-report", _CELL_EVAL_REPORT),
        ]
    )
    return _document(cells, dataset_slug=dataset_slug, enable_internet=False)


_CELL_GPU_PROBE = '''\
import glob
import json
import os
import platform
import shutil
import socket
import subprocess
from datetime import datetime, timezone

OUT_DIR = "/kaggle/working"
PROBE_NAME = "nexo_gpu_probe.json"


def _resolve(target):
    """Resolve one host name without letting a resolver hang the kernel."""
    try:
        return "dns ok -> " + socket.gethostbyname(target)
    except Exception as exc:  # noqa: BLE001 - any resolver failure is the answer
        return f"{type(exc).__name__}: {exc}"


def _which_nvidia_smi():
    """Find nvidia-smi and ask it what it can see."""
    path = shutil.which("nvidia-smi")
    probe = {"nvidia_smi_path": path, "nvidia_smi": False}
    if path is None:
        return probe
    try:
        completed = subprocess.run(
            [path, "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - a broken binary is a finding, not a crash
        probe["nvidia_smi_error"] = f"{type(exc).__name__}: {exc}"
        return probe
    probe["nvidia_smi"] = completed.returncode == 0
    probe["nvidia_smi_out"] = (completed.stdout or "").strip()[:2000]
    probe["nvidia_smi_err"] = (completed.stderr or "").strip()[:2000]
    return probe


probe = {
    "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "torch_cuda_available": None,
    "device_name": None,
    "total_vram_bytes": None,
    "device_nodes": sorted(glob.glob("/dev/nvidia*")),
    "cpu_count": os.cpu_count(),
    "platform": platform.platform(),
    "kaggle_run_type": os.environ.get("KAGGLE_KERNEL_RUN_TYPE"),
    "internet": {host: _resolve(host) for host in ("huggingface.co", "pypi.org")},
}
probe.update(_which_nvidia_smi())

try:
    import torch

    probe["torch_version"] = torch.__version__
    probe["torch_cuda_available"] = bool(torch.cuda.is_available())
    if probe["torch_cuda_available"]:
        properties = torch.cuda.get_device_properties(0)
        probe["device_name"] = properties.name
        probe["total_vram_bytes"] = int(properties.total_memory)
except Exception as exc:  # noqa: BLE001 - torch may be absent; that is the finding
    probe["torch_error"] = f"{type(exc).__name__}: {exc}"

os.makedirs(OUT_DIR, exist_ok=True)
with open(os.path.join(OUT_DIR, PROBE_NAME), "w", encoding="utf-8") as handle:
    json.dump(probe, handle, indent=2, sort_keys=True)
print("NEXO_GPU_PROBE " + json.dumps(probe, sort_keys=True))
'''


def render_gpu_probe_notebook(*, run_id: str) -> str:
    """Render the notebook that reports what accelerator the kernel actually got.

    Kaggle records an accelerator in a kernel's metadata whether or not one is
    attached, so a metadata read is a record of what was *requested*. This kernel
    is the only party able to testify about the machine it ran on, and its
    testimony is what
    :func:`~ml.training.remote.verify_gpu_available` will accept. It is also the
    cheapest possible use of a session: one cell, no installs, no downloads, a few
    seconds of wall clock.

    The probe deliberately resolves DNS as well as looking for a device, because
    "no accelerator" and "no network" are independent blockers and a report that
    only checks one of them leaves the operator with half the diagnosis.

    Args:
        run_id: This probe's identifier, echoed into the notebook's markdown so a
            log line ties back to a local artifact.

    Returns:
        An nbformat 4.5 document as JSON text.

    Raises:
        DatasetError: ``run_id`` is not a safe name, or the document failed
            validation.
    """
    _require_safe_name(run_id, field="run_id")
    title = f"""\
# Phase 10 — accelerator probe, run {run_id}

This kernel trains nothing. It answers one question — *did the session this run was
pushed to actually receive an accelerator?* — and writes the answer to
`/kaggle/working/nexo_gpu_probe.json`.

**Why it exists.** A pushed kernel's metadata carries `enable_gpu` and a
`machine_shape`, and both are echoed back by the API whether or not a device was
attached. Reading them reports the request. This notebook reports the machine: a
`/dev/nvidia*` device node if one exists, `nvidia-smi`'s own answer if the binary
is on `PATH`, and `torch.cuda.is_available()` from the interpreter that will do the
training. It resolves `huggingface.co` and `pypi.org` too, because an 8B base
model that cannot be fetched and a 4-bit adapter that cannot be fitted are
separate blockers and only one of them is about the GPU.

The file it writes is the sole input to
`ml.training.remote.verify_gpu_available`, which refuses to report `True` without
one behind it.
"""
    cells: list[dict[str, Any]] = [
        _markdown("what-this-run-is", title.rstrip("\n")),
        _code("probe", _CELL_GPU_PROBE),
    ]
    return _document(
        cells, dataset_slug="", enable_gpu=True, enable_internet=True, require_install=False
    )


__all__ = [
    "NBFORMAT_MINOR",
    "NBFORMAT_VERSION",
    "RUN_MANIFEST_FIELDS",
    "render_eval_notebook",
    "render_gpu_probe_notebook",
    "render_qwen_training_notebook",
    "render_small_training_notebook",
]
