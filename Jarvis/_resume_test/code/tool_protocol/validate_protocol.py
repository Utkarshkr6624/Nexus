"""Validates a dataset directory (train/validation/test.jsonl) against the toolproto-1 rules.  usage: python validate_protocol.py [dir] [--render]
Rules:
  R1  `tools` present  <=> the record has tool-related content; every tool def is one of tool_schemas.json (name, parameters identical).
  R2  assistant `tool_calls`: type=function, name in the record's `tools`, `arguments` is an object that validates against the tool's JSON schema
      (required keys present, types, enums, no unknown keys when additionalProperties is false).
  R3  sequencing: an assistant message with tool_calls is followed by exactly one `tool` message per call, in order, with matching `name`, then an assistant
      message that does not itself contain a call -- except that a conversation MAY end on a tool_call message (the runtime executes it next).
  R4  `tool` message content is a compact JSON envelope: {"ok":true,"data":...} or {"ok":false,"error":{"code","message"}} (exact key sets, no extras).
  R5  confirmation-first: a tool_call to a tool with requires_confirmation is preceded (in the previous assistant turn) by a text preview that contains
      "Proposed call:" and then a user turn; read-only tools may be called directly.
  R6  no `tool_calls`/`tool` messages and no `tools` key in records whose system message says no tools are attached.
  R7  no leftover Phase-2 prose convention: no user message starting "[tool result", no dotted tool names, no "Proposed call:" text in a record without tools.
  R9  no first-person action claim ("I ran/created/searched ...") in a tools-attached record that contains no tool message.
  R10 after an {"ok":false} result the next assistant message does not claim success.
  R5 additionally: the preview names the same tool, its simple arguments equal the call's, and the user turn before the call is an affirmative (no negation).
  R8  (--render) apply_chat_template(messages, tools=tools, enable_thinking=False) succeeds, contains one <tool_call> block per call and one
      <tool_response> block per tool message, and the tool-call JSON inside round-trips to the same name/arguments.
Exit code 1 on any violation."""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMAS = json.load(open(HERE / "tool_schemas.json", encoding="utf-8"))
BY_NAME = {t["name"]: t for t in SCHEMAS["tools"]}
DOTTED = re.compile(r"\b(" + "|".join(re.escape(t["dotted_name"]) for t in SCHEMAS["tools"]) + r")\b")
NOTOOLS = re.compile(r"(Tools connected in this session: none|Runtime capabilities this session: none|No tools are attached|no tool access)")
ENV_ERR = re.compile(r"^[a-z][a-z0-9_]*$")


def check_type(v, spec):
    t = spec.get("type")
    if t == "string":
        ok = isinstance(v, str)
    elif t == "integer":
        ok = isinstance(v, int) and not isinstance(v, bool)
    elif t == "number":
        ok = isinstance(v, (int, float)) and not isinstance(v, bool)
    elif t == "boolean":
        ok = isinstance(v, bool)
    elif t == "array":
        ok = isinstance(v, list) and all(check_type(x, spec["items"]) for x in v) if isinstance(spec.get("items"), dict) else isinstance(v, list)
    elif t == "object":
        ok = isinstance(v, dict)
    else:
        ok = True
    if ok and "enum" in spec and v not in spec["enum"]:
        return False
    if ok and "minimum" in spec and isinstance(v, (int, float)) and v < spec["minimum"]:
        return False
    return ok


def validate_args(name, args):
    sch = BY_NAME[name]["parameters"]
    errs = []
    if not isinstance(args, dict):
        return ["arguments is not an object"]
    for k in sch.get("required", []):
        if k not in args:
            errs.append(f"missing required argument {k}")
    for k, v in args.items():
        if k not in sch["properties"]:
            if sch.get("additionalProperties") is False:
                errs.append(f"unknown argument {k}")
        elif not check_type(v, sch["properties"][k]):
            errs.append(f"argument {k} has wrong type/value")
    return errs


def check_envelope(text):
    try:
        o = json.loads(text)
    except Exception:
        return "tool content is not JSON"
    if not isinstance(o, dict):
        return "envelope is not an object"
    if o.get("ok") is True:
        return None if set(o) == {"ok", "data"} else "ok envelope keys"
    if o.get("ok") is False:
        e = o.get("error")
        if set(o) != {"ok", "error"} or not isinstance(e, dict) or set(e) != {"code", "message"}:
            return "error envelope keys"
        if not ENV_ERR.match(e["code"]) or not isinstance(e["message"], str):
            return "error code/message"
        return None
    return "envelope ok flag"


AFFIRM = re.compile(r"\b(yes|yep|yeah|sure|ok|okay|proceed|go ahead|go for it|do it|please do|run it|sounds good|confirm)\b", re.I)
NEGATE = re.compile(r"\b(no|nope|don't|do not|dont|cancel|stop|wait|not now|never mind|nevermind|abort|decline|instead)\b", re.I)
PREVIEW = re.compile(r"Proposed call: `([a-z][a-z0-9_]*)\((.*)\)`", re.S)
ACTION_CLAIM = re.compile(r"\bI(?:'ve| have)?\s+(?:just\s+)?(ran|executed|searched|created|saved|deleted|sent|booked|updated|checked|looked up|read|fetched|moved|wrote|queried)\b", re.I)
NEG_CTX = re.compile(r"(n't|\bnot\b|\bno\b|\bnever\b|\bhaven't\b|\bwithout\b|\bif\b|\bwhen\b|\bonce\b|\bwould\b|\bcould\b|\bcan\b|\bwill\b|\bto\b)", re.I)
SUCCESS_CLAIM = re.compile(r"(\bsuccessfully\b|\bhas been (?:created|saved|sent|deleted|booked|updated)\b|\bI(?:'ve| have) (?:created|saved|sent|deleted|booked|updated)\b|^Done\b)", re.I | re.M)


def _preview_args(argstr):
    """key -> value for simple quoted/number args in a preview; placeholders (<...>) are skipped"""
    out = {}
    for m in re.finditer(r'([a-z_]+)=("(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?)', argstr):
        v = m.group(2)
        out[m.group(1)] = json.loads(v) if v.startswith('"') else (float(v) if "." in v else int(v))
    return out


def validate_record(r):
    errs = []
    msgs = r["messages"]
    tools = r.get("tools") or []
    tnames = {t["function"]["name"] for t in tools}
    sysm = next((m["content"] for m in msgs if m["role"] == "system"), "")
    has_calls = any(m.get("tool_calls") for m in msgs)
    has_tool_msgs = any(m["role"] == "tool" for m in msgs)
    # R1
    if len(tnames) != len(tools):
        errs.append("R1 duplicate tool names in `tools`")
    for t in tools:
        f = t.get("function", {})
        ref = BY_NAME.get(f.get("name"))
        if t.get("type") != "function" or not ref or f.get("parameters") != ref["parameters"]:
            errs.append(f"R1 bad tool definition {f.get('name')}")
    # R6
    if NOTOOLS.search(sysm) and (tools or has_calls or has_tool_msgs):
        errs.append("R6 tool content in a no-tools record")
    if (has_calls or has_tool_msgs) and not tools:
        errs.append("R6 tool calls without tools")
    # R7
    for m in msgs:
        if m["role"] == "user" and m["content"].startswith("[tool result"):
            errs.append("R7 legacy [tool result] message")
        if tools and m["role"] in ("assistant", "user") and DOTTED.search(re.sub(r"```.*?```", "", m["content"], flags=re.S)):
            errs.append("R7 dotted tool name left in text")
        if m["role"] == "assistant" and "Proposed call:" in m["content"] and not tools:
            errs.append("R7 proposal text without tools")
    # R2-R5 sequencing
    i = 0
    prev_assistant_text = None
    while i < len(msgs):
        m = msgs[i]
        if m["role"] == "assistant" and m.get("tool_calls"):
            calls = m["tool_calls"]
            for c in calls:
                f = c.get("function", {})
                n = f.get("name")
                if c.get("type") != "function" or n not in tnames:
                    errs.append(f"R2 call to unattached/unknown tool {n}")
                    continue
                for e in validate_args(n, f.get("arguments")):
                    errs.append(f"R2 {n}: {e}")
                if BY_NAME[n]["requires_confirmation"]:
                    # preview must be in the nearest previous assistant message, followed by an AFFIRMATIVE user turn before the call
                    j = i - 1
                    last_user = None
                    while j >= 0 and msgs[j]["role"] != "assistant":
                        if msgs[j]["role"] == "user" and last_user is None:
                            last_user = msgs[j]["content"]
                        j -= 1
                    pm = PREVIEW.search(msgs[j]["content"]) if j >= 0 else None
                    if pm is None or last_user is None:
                        errs.append(f"R5 {n} called without a confirmation preview and approval")
                    else:
                        if pm.group(1) != n:
                            errs.append(f"R5 preview names {pm.group(1)} but the call is {n}")
                        else:
                            for k, v in _preview_args(pm.group(2)).items():
                                if f.get("arguments", {}).get(k) != v:
                                    errs.append(f"R5 preview argument {k} differs from the call")
                        if NEGATE.search(last_user) or not AFFIRM.search(last_user):
                            errs.append(f"R5 {n} called after a user turn that is not an approval: {last_user[:40]!r}")
            # R3
            k = i + 1
            got = []
            while k < len(msgs) and msgs[k]["role"] == "tool":
                got.append(msgs[k])
                k += 1
            if k < len(msgs) or got:
                if [g.get("name") for g in got] != [c["function"]["name"] for c in calls]:
                    errs.append("R3 tool messages do not match calls")
                for g in got:
                    e = check_envelope(g["content"])
                    if e:
                        errs.append("R4 " + e)
                if k < len(msgs) and not (msgs[k]["role"] == "assistant" and not msgs[k].get("tool_calls")):
                    if not (msgs[k]["role"] == "assistant" and msgs[k].get("tool_calls")):
                        errs.append("R3 call/result not followed by an assistant message")
                if not got and k < len(msgs):
                    errs.append("R3 tool_call followed by non-tool message")
            i = k
            continue
        if m["role"] == "tool":
            errs.append("R3 orphan tool message")
        i += 1
    # R9 no action claim without a tool message (records with tools only; text-only records are covered by the Phase-2 checks)
    if tools and not has_tool_msgs:
        for m in msgs:
            if m["role"] == "assistant":
                for sent in re.split(r"(?<=[.!?])\s+|\n+", re.sub(r"```.*?```", " ", m["content"], flags=re.S)):
                    if ACTION_CLAIM.search(sent) and not NEG_CTX.search(sent):
                        errs.append("R9 action claimed without any tool result: " + sent[:60])
    # R10 after an error envelope the next assistant message must not claim success
    for k, m in enumerate(msgs):
        if m["role"] == "tool" and k + 1 < len(msgs) and msgs[k + 1]["role"] == "assistant":
            try:
                ok = json.loads(m["content"]).get("ok")
            except Exception:
                ok = None
            if ok is False and SUCCESS_CLAIM.search(msgs[k + 1]["content"]):
                errs.append("R10 success claimed after an error result")
    return errs


def render_check(tok, r):
    errs = []
    txt = tok.apply_chat_template(r["messages"], tools=r.get("tools"), tokenize=False, enable_thinking=False)
    calls = [c for m in r["messages"] for c in (m.get("tool_calls") or [])]
    tool_msgs = [m for m in r["messages"] if m["role"] == "tool"]
    blocks = [b for b in re.findall(r"<tool_call>\n(.*?)\n</tool_call>", txt, flags=re.S) if "<function-name>" not in b]   # skip the template's own instruction example
    txt_tr = txt.split("</tools>", 1)[-1] if "</tools>" in txt else txt
    if len(blocks) != len(calls):
        errs.append(f"R8 {len(blocks)} <tool_call> blocks for {len(calls)} calls")
    else:
        for b, c in zip(blocks, calls):
            try:
                o = json.loads(b)
            except Exception:
                errs.append("R8 tool_call block is not JSON")
                continue
            if o.get("name") != c["function"]["name"] or o.get("arguments") != c["function"]["arguments"]:
                errs.append("R8 tool_call block does not round-trip")
    if txt_tr.count("<tool_response>") != len(tool_msgs):
        errs.append("R8 tool_response count")
    if r.get("tools") and "# Tools" not in txt:
        errs.append("R8 tools block missing")
    return errs


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    d = Path(args[0]) if args else HERE.parent / "dataset" / "v2_final_candidate"
    render = "--render" in sys.argv
    tok = None
    if render:
        from transformers import AutoTokenizer
        tok = AutoTokenizer.from_pretrained(str(HERE.parent / "_hf_meta"))
    bad, n, ncalls, nrender = [], 0, 0, 0
    for s in ("train", "validation", "test"):
        for l in open(d / f"{s}.jsonl", encoding="utf-8"):
            r = json.loads(l)
            n += 1
            errs = validate_record(r)
            if tok and (r.get("tools") or any(m["role"] == "tool" for m in r["messages"])):
                nrender += 1
                errs += render_check(tok, r)
            ncalls += sum(len(m.get("tool_calls") or []) for m in r["messages"])
            if errs:
                bad.append((r["id"], errs[:3]))
    out = {"dir": str(d), "records": n, "tool_calls": ncalls, "rendered_with_chat_template": nrender, "violations": len(bad), "examples": bad[:10]}
    print(json.dumps(out, indent=1))
    json.dump(out, open(HERE / "protocol_validation.json", "w", encoding="utf-8"), indent=1)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
