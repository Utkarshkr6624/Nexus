"""Runs suites through a generator, scores deterministically, returns per-item results (+ run metadata)."""
import hashlib
import json
import platform
from importlib import metadata
from pathlib import Path

from .build_suites import DATA, ROOT, load_tool_defs, resolve_tools
from .scorers import REFUSAL, score_item
from .toolparse import mentions_tool_call

SUITES = ["instruction_following", "multi_turn", "nexus_facts", "tool_awareness", "capability_boundaries", "safety", "general", "testsplit"]


def load_suite(name, data_dir=DATA):
    p = Path(data_dir) / f"{name}.jsonl"
    if not p.exists():
        raise FileNotFoundError(f"{p} missing - run `python -m pipeline.eval.build_suites` first")
    with open(p, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def subsample(items, n, seed):
    if n is None or n >= len(items):
        return items
    keyed = sorted(items, key=lambda it: hashlib.md5(f"{seed}:{it['id']}".encode()).hexdigest())
    return sorted(keyed[:n], key=lambda it: it["id"])


def run_items(gen, items, defs):
    """Never passes the oracle / check / reference to the generator: it only sees `messages` and `tools`."""
    res = []
    for it in items:
        tools = resolve_tools(it, defs)
        msgs = json.loads(json.dumps(it["messages"]))          # deep copy: a generator must not mutate the item
        err = None
        try:
            out = gen(msgs, tools or None)
            if not isinstance(out, str):
                raise TypeError(f"generator returned {type(out).__name__}, expected str")
        except Exception as e:  # noqa: BLE001
            out, err = "", f"{type(e).__name__}: {e}"[:200]
        s = score_item(it, out, tools)
        if err:
            s["pass"] = False
        res.append({"id": it["id"], "suite": it["suite"], "category": it["category"], "pass": s["pass"], "scored": s["scored"], "checks": s["checks"], "info": s["info"],
                    "flags": {"refused": REFUSAL.search(out) is not None, "tool_markup": mentions_tool_call(out)}, "gen_error": err, "output": out[:600]})
    return res


def library_versions():
    v = {}
    for p in ("torch", "transformers", "peft", "trl", "bitsandbytes", "accelerate", "tokenizers"):
        try:
            v[p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            pass
    return v


def build_meta(gen, *, suites, seed, max_items, dataset_version, manifest, extra=None):
    m = dict(gen.describe()) if hasattr(gen, "describe") else {"model_kind": "model", "model_id": "unknown"}
    m.update({"dataset_version": dataset_version, "seed": seed, "max_items": max_items, "suites_run": suites,
              "suite_versions": {s: manifest["suites"][s]["sha256"][:16] for s in suites if s in manifest.get("suites", {})},
              "tool_schemas_sha256": manifest.get("tool_schemas_sha256"), "protocol_version": manifest.get("protocol_version"),
              "decoding": m.get("decoding", {"do_sample": False, "num_beams": 1, "enable_thinking": False, "note": "stub - no decoding"}),
              "hardware": {"platform": platform.platform(), "python": platform.python_version(), "note": "GPU/VRAM are recorded by the Phase 5 notebook, not here"},
              "libraries": library_versions()})
    if extra:
        m.update(extra)
    return m


def default_dataset_version():
    for cand in (ROOT / "dataset" / "v2_final").glob("*.json"):
        try:
            d = json.loads(cand.read_text(encoding="utf-8"))
            if isinstance(d, dict) and d.get("version"):
                return str(d["version"])
        except Exception:  # noqa: BLE001
            pass
    return "qwen_jarvis_dataset_v2_final (version file not found)"


def tool_defs(path=None):
    snap = DATA / "tool_schemas.snapshot.json"
    _, defs = load_tool_defs(path or snap)
    return defs
