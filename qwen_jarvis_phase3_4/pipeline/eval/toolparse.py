"""Strict, deterministic parser for Qwen3-style tool calls.

Format (mirrors the Qwen3 chat template output):

    <tool_call>
    {"name": "<function-name>", "arguments": {<json object>}}
    </tool_call>

Rules (anything else is an error with a stable machine-readable code):
  * every <tool_call> has a matching </tool_call>; no nesting                          -> unbalanced_tags / nested_tool_call
  * the body is a single JSON object with exactly the keys "name" and "arguments"      -> invalid_json / not_an_object / unexpected_keys / missing_name / missing_arguments
  * duplicate keys anywhere in the JSON are rejected                                    -> duplicate_key
  * "name" is a non-empty identifier ([A-Za-z_][A-Za-z0-9_.-]*)                         -> bad_name
  * "arguments" is a JSON *object* (not a string)                                       -> arguments_not_object
  * at most MAX_CALLS calls per message                                                 -> too_many_calls
  * the model must not write a tool result itself (<tool_response> in its output)      -> model_emitted_tool_response
  * strict mode: no prose after the last </tool_call> (the model should stop there)     -> trailing_text
Text before the first tag is allowed and returned as `content`.
"""
import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

OPEN, CLOSE = "<tool_call>", "</tool_call>"
MAX_CALLS = 8
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")
_SPECIAL_TAIL = re.compile(r"(?:<\|im_end\|>|<\|endoftext\|>)\s*$")


class ToolCallParseError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


@dataclass
class ParseResult:
    calls: List[dict] = field(default_factory=list)
    error: Optional[str] = None          # stable error code, None when ok
    detail: str = ""
    content: str = ""                    # text before the first tool call (or the whole text when there is no call)
    trailing: str = ""                   # text after the last </tool_call>

    @property
    def ok(self) -> bool:
        return self.error is None


def _no_dupes(pairs):
    keys = [k for k, _ in pairs]
    dup = {k for k in keys if keys.count(k) > 1}
    if dup:
        raise ToolCallParseError("duplicate_key", ", ".join(sorted(dup)))
    return dict(pairs)


def try_parse_tool_calls(text: str, strict: bool = True) -> ParseResult:
    """Non-raising parse. `result.calls` is empty and `result.error is None` when the text contains no tool call."""
    t = _SPECIAL_TAIL.sub("", text or "")
    if "<tool_response>" in t or "</tool_response>" in t:
        return ParseResult(error="model_emitted_tool_response", detail="model wrote a tool result itself", content=t)
    if OPEN not in t and CLOSE not in t:
        return ParseResult(content=t.strip())
    n_open, n_close = t.count(OPEN), t.count(CLOSE)
    if n_open != n_close:
        return ParseResult(error="unbalanced_tags", detail=f"{n_open} open vs {n_close} close", content=t)
    if n_open > MAX_CALLS:
        return ParseResult(error="too_many_calls", detail=str(n_open), content=t)
    calls, pos = [], 0
    first = t.index(OPEN)
    content = t[:first].strip()
    while True:
        i = t.find(OPEN, pos)
        if i < 0:
            break
        j = t.find(CLOSE, i + len(OPEN))
        if j < 0:
            return ParseResult(error="unbalanced_tags", detail="open without close", content=content)
        body = t[i + len(OPEN):j]
        if OPEN in body:
            return ParseResult(error="nested_tool_call", content=content)
        if pos > 0 and t[pos:i].strip():
            return ParseResult(error="text_between_calls", detail=t[pos:i].strip()[:60], content=content)
        try:
            obj = json.loads(body.strip(), object_pairs_hook=_no_dupes)
        except ToolCallParseError as e:
            return ParseResult(error=e.code, detail=e.detail, content=content)
        except json.JSONDecodeError as e:
            return ParseResult(error="invalid_json", detail=str(e)[:80], content=content)
        if not isinstance(obj, dict):
            return ParseResult(error="not_an_object", content=content)
        extra = set(obj) - {"name", "arguments"}
        if extra:
            return ParseResult(error="unexpected_keys", detail=", ".join(sorted(extra)), content=content)
        if "name" not in obj:
            return ParseResult(error="missing_name", content=content)
        if "arguments" not in obj:
            return ParseResult(error="missing_arguments", content=content)
        if not isinstance(obj["name"], str) or not _NAME.match(obj["name"]):
            return ParseResult(error="bad_name", detail=repr(obj["name"])[:40], content=content)
        if not isinstance(obj["arguments"], dict):
            return ParseResult(error="arguments_not_object", detail=type(obj["arguments"]).__name__, content=content)
        calls.append({"name": obj["name"], "arguments": obj["arguments"]})
        pos = j + len(CLOSE)
    trailing = t[pos:].strip()
    if strict and trailing:
        return ParseResult(calls=calls, error="trailing_text", detail=trailing[:60], content=content, trailing=trailing)
    return ParseResult(calls=calls, content=content, trailing=trailing)


def parse_tool_calls(text: str, strict: bool = True) -> List[dict]:
    """Raising parse: returns the list of {'name','arguments'} dicts ([] if the text has no tool call) or raises ToolCallParseError."""
    r = try_parse_tool_calls(text, strict=strict)
    if r.error:
        raise ToolCallParseError(r.error, r.detail)
    return r.calls


def mentions_tool_call(text: str) -> bool:
    """True if the output contains any tool-call markup at all, valid or not (used for 'must not call a tool' checks)."""
    return OPEN in text or CLOSE in text or bool(re.search(r"(?i)\bProposed call\b", text))


def format_tool_call(name: str, arguments: dict) -> str:
    """Canonical rendering used by the oracle stubs and tests (identical to what Qwen3's template produces)."""
    return f'{OPEN}\n{json.dumps({"name": name, "arguments": arguments}, ensure_ascii=False)}\n{CLOSE}'
