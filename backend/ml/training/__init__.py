"""Training runs, checkpoints and the manifests that record them.

Everything here is stdlib-only so that the orchestrator can load a config, read
a checkpoint and write a run manifest without ``torch`` anywhere in the import
graph. The classifier's own training loop lives in
:mod:`ml.scripts.train_small_local` and runs in a subprocess under the ML
virtualenv; this package is what surrounds it.
"""

from __future__ import annotations

__all__: list[str] = []
