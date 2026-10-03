"""Directly runnable entry points for the Phase 10 training pipeline.

These are the scripts a person runs by hand rather than the orchestrator they
are invoked from. They exist because the training halves of the pipeline run
under a different interpreter than the rest of the repository: torch lives in
``ml/.venv`` and nowhere else, while the dataset builders, validators, configs
and checkpoint readers are stdlib-only and run under the backend's own
interpreter. A single entry point cannot span both, so each model gets a script
that is honest about the split — it imports only stdlib and ``ml`` at module
scope, and pulls torch, transformers and safetensors in lazily inside
functions, so ``import ml.scripts.train_small_local`` succeeds on an
interpreter that has never heard of torch at all.

That laziness is a constraint, not an optimisation: the test suite collects the
whole ``ml`` package under the backend interpreter, and a module-scope torch
import would make that collection fail on import rather than on a missing
feature.
"""

from __future__ import annotations

__all__: list[str] = []
