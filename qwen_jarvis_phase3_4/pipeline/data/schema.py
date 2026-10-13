"""Strict validation of the Phase 3 "chat+tools" record format (see docs/CONTRACT.md) with a lenient Phase-2 mode.

Modes
  strict : roles system/user/assistant/tool, tool_calls + tools allowed, everything in CONTRACT enforced.
  phase2 : the old Phase 2 format (system/user/assistant only, no tools); extra keys such as "metadata" are accepted.
Both modes reject text that would corrupt the chat structure (literal control tokens inside content) and any <think> tags
(our data is non-thinking; the template would otherwise split the content on </think>).

Pure stdlib (jsonschema is used for argument checking only when importable)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

CONTROL_TOKENS = ("<|im_start|>", "<|im_end|>", "<|endoftext|>", "<|object_ref_start|>", "<|object_ref_end|>", "<|box_start|>", "<|box_end|>",
                  "<|quad_start|>", "<|quad_end|>", "<|vision_start|>", "<|vision_end|>", "<|vision_pad|>", "<|image_pad|>", "<|video_pad|>")
THINK_TAGS = ("<think>", "</think>")
ROLES_STRICT = {"system", "user", "assistant", "tool"}
ROLES_PHASE2 = {"system", "user", "assistant"}
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]{0,63}$")


class SchemaError(ValueError):
    """Raised by validate_record(..., raise_on_error=True); .problems holds every problem found."""

    def __init__(self, problems, rec_id=None):
        self.problems = list(problems)
        self.rec_id = rec_id
        super().__init__(f"record {rec_id!r}: " + "; ".join(self.problems[:5]) + (" ..." if len(self.problems) > 5 else ""))


@dataclass
class Report:
    n: int = 0
    ok: int = 0
    bad: int = 0
    problems: list = field(default_factory=list)   # (index, id, [problem,...]) for the first few failures

    def add(self, idx, rec_id, probs):
        self.n += 1
        if probs:
            self.bad += 1
            if len(self.problems) < 25:
                self.problems.append((idx, rec_id, probs[:5]))
        else:
            self.ok += 1


def _check_tools(tools, probs):
    names = set()
    if not isinstance(tools, list) or not tools:
        probs.append("tools must be a non-empty list when present")
        return names
    for i, t in enumerate(tools):
        if not isinstance(t, dict) or t.get("type") != "function" or not isinstance(t.get("function"), dict):
            probs.append(f"tools[{i}] must be {{type:'function', function:{{...}}}}")
            continue
        f = t["function"]
        name = f.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            probs.append(f"tools[{i}].function.name invalid: {name!r}")
            continue
        if name in names:
            probs.append(f"duplicate tool name {name!r}")
        names.add(name)
        if not isinstance(f.get("description"), str) or not f["description"].strip():
            probs.append(f"tools[{i}] ({name}) needs a non-empty description")
        p = f.get("parameters")
        if not isinstance(p, dict) or p.get("type") != "object" or not isinstance(p.get("properties"), dict):
            probs.append(f"tools[{i}] ({name}).parameters must be an object schema with 'properties'")
        else:
            req = p.get("required", [])
            if not isinstance(req, list) or any(r not in p["properties"] for r in req):
                probs.append(f"tools[{i}] ({name}).required must list declared properties")
    return names


_TYPE_MAP = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}


def check_arguments(args, schema):
    """Minimal JSON-schema check (required, known properties, simple types, enum). Returns list of problems."""
    probs = []
    if not isinstance(args, dict):
        return ["arguments must be an object"]
    props = schema.get("properties", {})
    for r in schema.get("required", []):
        if r not in args:
            probs.append(f"missing required argument {r!r}")
    for k, v in args.items():
        if k not in props:
            probs.append(f"unknown argument {k!r}")
            continue
        spec = props[k]
        t = spec.get("type")
        if t in _TYPE_MAP:
            ok = isinstance(v, _TYPE_MAP[t]) and not (t in ("integer", "number") and isinstance(v, bool))
            if not ok:
                probs.append(f"argument {k!r} should be {t}, got {type(v).__name__}")
        if "enum" in spec and v not in spec["enum"]:
            probs.append(f"argument {k!r} not in enum {spec['enum']}")
    return probs


def _text_problems(text, where, probs):
    for tok in CONTROL_TOKENS:
        if tok in text:
            probs.append(f"{where} contains control token {tok}")
    for tag in THINK_TAGS:
        if tag in text:
            probs.append(f"{where} contains {tag}")


def validate_record(rec, mode="strict", raise_on_error=False):
    """Return a list of problems (empty = valid)."""
    probs = []
    if mode not in ("strict", "phase2"):
        raise ValueError("mode must be 'strict' or 'phase2'")
    roles_ok = ROLES_STRICT if mode == "strict" else ROLES_PHASE2
    rec_id = rec.get("id") if isinstance(rec, dict) else None
    if not isinstance(rec, dict):
        probs.append("record must be an object")
    else:
        msgs = rec.get("messages")
        tools = rec.get("tools")
        names, tool_by_name = set(), {}
        if tools is not None:
            if mode == "phase2":
                probs.append("tools not allowed in phase2 mode")
            else:
                names = _check_tools(tools, probs)
                tool_by_name = {t["function"]["name"]: t["function"]["parameters"] for t in tools
                                if isinstance(t, dict) and isinstance(t.get("function"), dict) and isinstance(t["function"].get("parameters"), dict)
                                and isinstance(t["function"].get("name"), str)}
        if not isinstance(msgs, list) or len(msgs) < 2:
            probs.append("messages must be a list with at least 2 items")
        else:
            body_start = 0
            for i, m in enumerate(msgs):
                if not isinstance(m, dict) or m.get("role") not in roles_ok:
                    probs.append(f"messages[{i}] has invalid role {m.get('role') if isinstance(m, dict) else m!r}")
                    break
                if m["role"] == "system" and i != 0:
                    probs.append("system message must be first and unique")
            if not probs:
                if msgs[0]["role"] == "system":
                    body_start = 1
                body = msgs[body_start:]
                state = "need_user"          # state machine over the non-system messages
                pending_calls = []
                for j, m in enumerate(body):
                    i = j + body_start
                    role, content = m["role"], m.get("content")
                    if not isinstance(content, str):
                        probs.append(f"messages[{i}].content must be a string")
                        continue
                    _text_problems(content, f"messages[{i}]", probs)
                    extra = set(m) - {"role", "content", "tool_calls", "name"}
                    if extra:
                        probs.append(f"messages[{i}] has unexpected keys {sorted(extra)}")
                    if role == "system":
                        probs.append("system message must be first")
                        continue
                    if role == "user":
                        if state != "need_user":
                            probs.append(f"messages[{i}]: unexpected user message (state {state})")
                        if not content.strip():
                            probs.append(f"messages[{i}]: empty user content")
                        state = "need_assistant"
                    elif role == "assistant":
                        if state != "need_assistant":
                            probs.append(f"messages[{i}]: unexpected assistant message (state {state})")
                        calls = m.get("tool_calls")
                        if calls:
                            if mode == "phase2":
                                probs.append(f"messages[{i}]: tool_calls not allowed in phase2 mode")
                            if not tools:
                                probs.append(f"messages[{i}]: tool_calls but the record has no tools")
                            if not isinstance(calls, list):
                                probs.append(f"messages[{i}].tool_calls must be a list")
                                calls = []
                            pending_calls = []
                            for c, call in enumerate(calls):
                                f = call.get("function") if isinstance(call, dict) else None
                                if not isinstance(call, dict) or call.get("type") != "function" or not isinstance(f, dict):
                                    probs.append(f"messages[{i}].tool_calls[{c}] must be {{type:'function', function:{{name, arguments}}}}")
                                    continue
                                nm, ar = f.get("name"), f.get("arguments")
                                if nm not in names:
                                    probs.append(f"messages[{i}].tool_calls[{c}] calls undeclared tool {nm!r}")
                                if not isinstance(ar, dict):
                                    probs.append(f"messages[{i}].tool_calls[{c}].arguments must be an object (not a string)")
                                elif nm in tool_by_name:
                                    probs += [f"messages[{i}].tool_calls[{c}]: {p}" for p in check_arguments(ar, tool_by_name[nm])]
                                pending_calls.append(nm)
                            state = "need_tool"
                        else:
                            if "tool_calls" in m and m["tool_calls"] not in (None, []):
                                probs.append(f"messages[{i}].tool_calls malformed")
                            if not content.strip():
                                probs.append(f"messages[{i}]: empty assistant content without tool_calls")
                            state = "need_user"
                    elif role == "tool":
                        if state != "need_tool":
                            probs.append(f"messages[{i}]: tool message not preceded by an assistant tool_calls message")
                        else:
                            exp = pending_calls.pop(0) if pending_calls else None
                            if m.get("name") != exp:
                                probs.append(f"messages[{i}].name {m.get('name')!r} != expected tool {exp!r}")
                            if not content.strip():
                                probs.append(f"messages[{i}]: empty tool content")
                            if not pending_calls:
                                state = "need_assistant"
                # A conversation may END on an assistant tool request (the training target is the call itself; the tool result
                # arrives at runtime). It must not end after only some of the requested tool results.
                if state == "need_tool" and not (body and body[-1].get("role") == "assistant" and body[-1].get("tool_calls")):
                    probs.append("conversation ends after only some of the requested tool results")
                if body and body[-1].get("role") != "assistant":
                    probs.append("last message must be an assistant message")
                if not body or body[0].get("role") != "user":
                    probs.append("first non-system message must be user")
    if probs and raise_on_error:
        raise SchemaError(probs, rec_id)
    return probs


def validate_all(records, mode="strict"):
    rep = Report()
    seen = set()
    for i, r in enumerate(records):
        p = validate_record(r, mode)
        rid = r.get("id") if isinstance(r, dict) else None
        if rid is not None:
            if rid in seen:
                p.append(f"duplicate id {rid}")
            seen.add(rid)
        rep.add(i, rid, p)
    return rep


def has_tools(rec):
    return bool(rec.get("tools")) or any(m.get("tool_calls") or m.get("role") == "tool" for m in rec.get("messages", []))


def user_turns(rec):
    return sum(1 for m in rec["messages"] if m["role"] == "user")


def dumps_args(args):
    """The exact JSON used for arguments when rendering (matches the HF 'tojson' filter: default separators, no ASCII escaping)."""
    return json.dumps(args, ensure_ascii=False)
