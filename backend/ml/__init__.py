"""Phase 10 — the Nexo ML training pipeline.

Everything under ``ml`` is **stdlib-only**. The backend's pinned
``requirements.txt`` carries no ``torch``, no ``transformers`` and no
``scikit-learn``, and Phase 10 deliberately does not add any: the local half of
this pipeline — building, validating, splitting, checkpointing, evaluating and
reporting — has to keep running on a bare interpreter, on a contributor's
laptop and inside CI, without a 2 GB download standing between them and a test
run.

The heavy half — training ``deberta-v3-base`` and the QLoRA fine-tune of
Qwen3-8B — runs on remote GPUs and is driven from here through
:mod:`ml.training.remote`. Those notebooks are the only place ``torch`` is
imported, and they never import this package's training code back.

The layout follows the pipeline stages:

============================  ====================================================
:mod:`ml.datasets`            schemas, the capability inventory, the intent
                              taxonomy and the three dataset builders
:mod:`ml.preprocessing`       normalisation and deterministic, leakage-aware
                              splitting
:mod:`ml.validation`          the data-integrity gate that fails training safely
:mod:`ml.training`            typed configuration, run manifests, checkpoints and
                              the Kaggle driver
:mod:`ml.evaluation`          metrics and the Qwen rubric evaluator
============================  ====================================================

Nothing here loads a model into the running application. That is Phase 11's
job, and Phase 10 stops at the artifact.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
