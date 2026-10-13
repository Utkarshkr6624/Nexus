"""Shared helpers for the data-pipeline tests. Plain Python: every test file can be run with `python tests/<file>.py` or pytest."""
from __future__ import annotations

import copy
import json
import random
import sys
import traceback
from pathlib import Path

WS = Path(__file__).resolve().parents[1]
if str(WS) not in sys.path:
    sys.path.insert(0, str(WS))

PHASE2_SPLITS = WS.parent / "qwen_jarvis_phase2" / "dataset" / "splits"
_tok = {}


def tokenizer():
    if "t" not in _tok:
        from pipeline.data.render import load_tokenizer
        _tok["t"] = load_tokenizer()
    return _tok["t"]


def real_records(split="train", n=500, seed=0):
    """Deterministic random sample of real Phase-2 v2 records (old format)."""
    recs = [json.loads(l) for l in open(PHASE2_SPLITS / f"{split}.jsonl", encoding="utf-8") if l.strip()]
    random.Random(seed).shuffle(recs)
    return recs[:n]


TOOLS = [
    {"type": "function", "function": {"name": "web_search", "description": "Search the web.",
                                      "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a text file.",
                                      "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
]


def tool_record(final="The search returned no results, so I can't confirm what is new."):
    return {"id": "fx-tool", "tools": copy.deepcopy(TOOLS), "messages": [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "What's new in Python 3.13? Search for it."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "web_search", "arguments": {"query": "Python 3.13 release notes é"}}}]},
        {"role": "tool", "name": "web_search", "content": "{\"results\": []}"},
        {"role": "assistant", "content": final}]}


def multi_record():
    return {"id": "fx-multi", "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello! How can I help?"},
        {"role": "user", "content": "What is 2+2?"}, {"role": "assistant", "content": "4."},
        {"role": "user", "content": "Thanks"}, {"role": "assistant", "content": "You're welcome."}]}


def run_module(ns):
    """Mini runner so test files work without pytest."""
    fails = 0
    tests = [(k, v) for k, v in ns.items() if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:
            fails += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - fails}/{len(tests)} passed")
    return 1 if fails else 0
