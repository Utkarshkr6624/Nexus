"""Strict parser for the tool calls Jarvis writes.

The rules are the ones the Phase 6 evaluator scored the model against
(``qwen_jarvis_phase3_4/pipeline/eval/toolparse.py``): a call is only a call if
it is a well-formed ``<tool_call>{"name": ..., "arguments": {...}}</tool_call>``
block. Prose that *describes* a call ("Proposed call: fs_write_file(...)") is
text, never an instruction to the executor, and a model that writes its own
``<tool_response>`` is producing an invalid turn, not a result.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

__all__ = ["MAX_CALLS", "ParsedTurn", "parse_turn"]

OPEN, CLOSE = "<tool_call>", "</tool_call>"
MAX_CALLS = 4
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SPECIAL_TAIL = re.compile(r"(?:<\|im_end\|>|<\|endoftext\|>)\s*$")
_THINK = re.compile(r"^\s*<think>.*?</think>\s*", re.S)


@dataclass
class ParsedTurn:
    """One model turn: the visible text and the structured calls, or an error code."""

    content: str
    calls: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    detail: str = ""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate_key")
    return dict(pairs)


def parse_turn(text: object) -> ParsedTurn:
    """Split a raw generation into content and validated-shape tool calls."""
    if not isinstance(text, str):
        return ParsedTurn("", error="malformed_output", detail="generation is not text")
    t = _THINK.sub("", _SPECIAL_TAIL.sub("", text))
    if "<tool_response>" in t or "</tool_response>" in t:
        return ParsedTurn(
            t, error="model_emitted_tool_response", detail="the model wrote a tool result itself"
        )
    if OPEN not in t and CLOSE not in t:
        return ParsedTurn(t.strip())
    if t.count(OPEN) != t.count(CLOSE):
        return ParsedTurn(t, error="unbalanced_tags")
    content = t[: t.index(OPEN)].strip()
    calls: list[dict[str, Any]] = []
    rest = t[t.index(OPEN) :]
    while rest.strip():
        if not rest.lstrip().startswith(OPEN):
            return ParsedTurn(content, error="trailing_text", detail="text after a tool call")
        rest = rest.lstrip()[len(OPEN) :]
        end = rest.find(CLOSE)
        if end < 0 or CLOSE in content:
            return ParsedTurn(content, error="unbalanced_tags")
        body = rest[:end]
        if OPEN in body:
            return ParsedTurn(content, error="nested_tool_call")
        rest = rest[end + len(CLOSE) :]
        try:
            obj = json.loads(body, object_pairs_hook=_no_duplicate_keys)
        except (ValueError, RecursionError) as exc:  # a deeply nested body must not crash the turn
            return ParsedTurn(
                content, error="duplicate_key" if str(exc) == "duplicate_key" else "invalid_json"
            )
        if not isinstance(obj, dict):
            return ParsedTurn(content, error="not_an_object")
        if set(obj) != {"name", "arguments"}:
            return ParsedTurn(content, error="unexpected_keys", detail=",".join(sorted(obj)))
        if not isinstance(obj["name"], str) or not _NAME.match(obj["name"]):
            return ParsedTurn(content, error="bad_name")
        if not isinstance(obj["arguments"], dict):
            return ParsedTurn(content, error="arguments_not_object")
        calls.append({"name": obj["name"], "arguments": obj["arguments"]})
        if len(calls) > MAX_CALLS:
            return ParsedTurn(content, error="too_many_calls")
    return ParsedTurn(content, calls)
