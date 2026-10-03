"""Phase 10 — the Nexo ML training pipeline.

NEXUS runs **one** trained model: a ``deberta-v3-base`` intent router that
decides which of fourteen Nexo surfaces should handle an utterance. Everything
under ``ml`` is **stdlib-only**. The backend's pinned ``requirements.txt``
carries no ``torch``, no ``transformers`` and no ``scikit-learn``, and Phase 10
deliberately does not add any: building, validating, splitting, checkpointing,
evaluating and reporting all have to keep running on a bare interpreter, on a
contributor's laptop and inside CI, without a multi-gigabyte download standing
between them and a test run.

The one heavy piece is the classifier's training loop, in
:mod:`ml.scripts.train_small_local`, which runs under a separate ML
virtualenv and is invoked as a subprocess so ``torch`` never has to be
importable from the orchestrator. It trains on CPU in about 66 minutes
(2,800 rows, 5 epochs, 615 steps, measured), which is why this project needs no
remote GPU at all.

The layout follows the pipeline stages:

============================  ====================================================
:mod:`ml.datasets`            schemas, the capability inventory, the intent
                              taxonomy and the routing-corpus builder
:mod:`ml.preprocessing`       normalisation and deterministic, leakage-aware
                              splitting
:mod:`ml.validation`          the data-integrity gate that fails training safely
:mod:`ml.training`            typed configuration, run manifests and checkpoints
:mod:`ml.evaluation`          confusion matrix, precision, recall and F1
============================  ====================================================

Nothing here loads a model into the running application. That is Phase 11's
job, and Phase 10 stops at the artifact.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
