"""Kaggle notebook generation — the only place Phase 10 emits training code.

Everything else under ``ml`` runs on a bare interpreter: it builds, validates,
splits, checkpoints, evaluates and reports. Training ``deberta-v3-base`` and
fine-tuning Qwen3-8B does not, and cannot — it needs a GPU and roughly thirty
gigabytes of weights. So the training *programs* live here as source text
rather than as imports: :mod:`ml.kaggle.notebook` renders nbformat 4.5
documents that a Kaggle kernel executes on remote hardware, and the local half
of the pipeline never has to install ``torch`` to test itself.

Three notebooks, one per job: the intent router, the Qwen adapter, and the
base-versus-fine-tuned evaluation that decides whether the adapter was worth
the GPU hours.
"""

from __future__ import annotations

from ml.kaggle.notebook import (
    RUN_MANIFEST_FIELDS,
    render_eval_notebook,
    render_qwen_training_notebook,
    render_small_training_notebook,
)

__all__ = [
    "RUN_MANIFEST_FIELDS",
    "render_eval_notebook",
    "render_qwen_training_notebook",
    "render_small_training_notebook",
]
