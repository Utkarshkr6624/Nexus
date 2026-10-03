"""Training runs, checkpoints and the Kaggle driver that hosts them.

The heavy half of Phase 10 — ``deberta-v3-base`` and the QLoRA fine-tune of
``Qwen3-8B`` — needs an accelerator, so it runs on Kaggle kernels driven from
here through :mod:`ml.training.remote`. The notebooks those kernels run are the
only place ``torch`` is imported; they never import this package back.
"""

from __future__ import annotations

__all__: list[str] = []
