"""Small training helpers: early stopping and a JSONL + console metrics logger (no external services, no network)."""
from __future__ import annotations

import json
import time
from pathlib import Path


class EarlyStopping:
    """Counts evaluations without validation-loss improvement. patience == 0 disables it. Resumable via state()/load()."""

    def __init__(self, patience: int, min_delta: float = 0.0):
        self.patience, self.min_delta = patience, min_delta
        self.best = float("inf")
        self.best_step = -1
        self.bad = 0

    def update(self, step: int, value: float) -> tuple[bool, bool]:
        """Returns (improved, should_stop)."""
        improved = value < self.best - self.min_delta
        if improved:
            self.best, self.best_step, self.bad = value, step, 0
        else:
            self.bad += 1
        return improved, (self.patience > 0 and self.bad >= self.patience)

    def state(self) -> dict:
        return {"best": self.best, "best_step": self.best_step, "bad": self.bad}

    def load(self, d: dict) -> None:
        self.best = d.get("best", float("inf"))
        self.best_step = d.get("best_step", -1)
        self.bad = d.get("bad", 0)


class MetricsLogger:
    def __init__(self, path: Path, echo: bool = True):
        self.path, self.echo, self.t0 = path, echo, time.time()
        path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, **kv) -> None:
        kv["t"] = round(time.time() - self.t0, 2)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(kv, default=float) + "\n")
        if self.echo:
            print("  " + " ".join(f"{k}={v:.5g}" if isinstance(v, float) else f"{k}={v}" for k, v in kv.items()), flush=True)
