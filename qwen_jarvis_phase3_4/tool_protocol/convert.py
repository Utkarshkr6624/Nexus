"""Phase-2 prose tool conventions -> Qwen-native structured tool protocol ("toolproto-1").  Deterministic; stdlib only.

usage: python convert.py [--src DIR] [--out DIR] [--strict]
  src  default ../dataset/v2_clean      (train/validation/test.jsonl in Phase-2 message format)
  out  default ../dataset/v2_final_candidate

What it converts (all of it inside records whose system message lists tools; 679 records in v2_clean):
  * system message: the tool-name enumeration is removed from the text and replaced by a `tools` array of JSON-schema function definitions
    (tool_schemas.json) which Qwen3's chat template renders in its own "# Tools" block.  Dotted names (web.search) become canonical names (web_search).
  * "Proposed call: `tool(args)`" prose:
        read-only tools      -> a structured assistant `tool_calls` message right away (the runtime executes read-only tools automatically);
        other tools          -> kept as a plain-text CONFIRMATION PREVIEW (canonical names); after the user's approval the structured call follows.
  * "[tool result from X]\n<text>" user messages -> role:"tool" messages whose content is a compact JSON result envelope
        {"ok":true,"data":...} / {"ok":false,"error":{"code":...,"message":...}} (the original text is preserved inside, so the final answer stays grounded).
  * direct flows whose assistant turn never stated arguments get them from arg_overrides.json (hand annotations, consistent with the recorded result);
    write/execute tools in such flows get a confirmation exchange inserted so every non-read tool call is preceded by a confirmation preview + approval.
Anything not matching a known shape is reported (conversion_report.json) and fails --strict."""
import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMAS = json.load(open(HERE / "tool_schemas.json", encoding="utf-8"))
TOOLS = {t["dotted_name"]: t for t in SCHEMAS["tools"]}
CANON = {d: t["name"] for d, t in TOOLS.items()}
OVERRIDES = json.load(open(HERE / "arg_overrides.json", encoding="utf-8"))["entries"] if (HERE / "arg_overrides.json").exists() else []
DOTTED = re.compile(r"\b(" + "|".join(re.escape(d) for d in sorted(CANON, key=len, reverse=True)) + r")\b")

SYS_TEMPLATES = [
    (re.compile(r"^(You are the assistant inside NEXUS\.) Available tools: [a-z._, ]+\. (State which tool .*)$"), r"\1 \2"),
    (re.compile(r"^(You are a personal assistant\.) The runtime has attached these tools: [a-z._, ]+\. You may propose using them; (you must not claim .*)$"),
     r"\1 You may propose using the attached tools; \2"),
    (re.compile(r"^(You are the NEXUS assistant\.) Tools available in this session: [a-z._, ]+\. (Use a tool only .*)$"), r"\1 \2"),
]
SYS_TOOL_LIST = re.compile(r"(?:Available tools|The runtime has attached these tools|Tools available in this session): ([a-z._, ]+)\.")
CONSENT = ["Yes, go ahead.", "Sure, proceed.", "Do it.", "Yes please.", "Go for it.", "Okay, run it."]
PROP_CALL = re.compile(r"Proposed call: `([a-z]+\.[a-z_]+)\((.*)\)`")
RESULT_HEAD = re.compile(r"^\[tool result from ([a-z]+\.[a-z_]+)\]\n?(.*)$", re.S)
KW = re.compile(r'\s*([a-z_]+)=("(?:[^"\\]|\\.)*"|<[^>]*>|-?\d+(?:\.\d+)?)\s*(?:,|$)')


def md5(t):
    return hashlib.md5(t.encode("utf-8")).hexdigest()


def canon_text(t):
    return DOTTED.sub(lambda m: CANON[m.group(1)], t)


def parse_args(argstr):
    """-> dict or None when the preview contains placeholders / free text."""
    s = argstr.strip()
    out, pos = {}, 0
    while pos < len(s):
        m = KW.match(s, pos)
        if not m:
            return None
        k, v = m.group(1), m.group(2)
        if v.startswith("<"):
            return None
        if v.startswith('"'):
            v = json.loads(v)
        else:
            v = float(v) if "." in v else int(v)
        out[k] = v
        pos = m.end()
    return out


def ordered_args(dotted, args):
    props = list(TOOLS[dotted]["parameters"]["properties"])
    return {k: args[k] for k in props if k in args} | {k: v for k, v in args.items() if k not in props}


def tool_def(dotted):
    t = TOOLS[dotted]
    desc = t["description"].rstrip(".") + "."
    desc += " Read-only; may be called directly." if not t["requires_confirmation"] else " Changes state or has side effects; show the user the exact call and get their confirmation before calling."
    return {"type": "function", "function": {"name": t["name"], "description": desc, "parameters": t["parameters"]}}


def preview(dotted, args):
    parts = []
    for k, v in ordered_args(dotted, args).items():
        if isinstance(v, str):
            parts.append(f"{k}={json.dumps(v, ensure_ascii=False)}" if len(v) <= 60 and "\n" not in v else f"{k}=<{len(v)} characters, shown above>")
        else:
            parts.append(f"{k}={v}")
    return f"{CANON[dotted]}({', '.join(parts)})"


def envelope(text):
    """tool-result prose -> compact JSON envelope string (original text kept verbatim inside)."""
    t = text.strip()
    code = None
    if re.match(r"ERROR 403", t) or re.search(r"permission denied|PermissionError", t, re.I):
        code = "permission_denied"
    elif t.startswith("ERROR") and re.search(r"FileNotFoundError|not found|no such file", t, re.I):
        code = "not_found"
    elif t.startswith("ERROR") and re.search(r"timed out|timeout", t, re.I):
        code = "timeout"
    elif t.startswith("ERROR") and re.search(r"connection refused|connection reset|unreachable", t, re.I):
        code = "connection_failed"
    elif t.startswith("ERROR") and "conflict" in t:
        code = "conflict"
    elif t.startswith("ERROR") and re.search(r"disk quota|quota", t, re.I):
        code = "quota_exceeded"
    elif t.startswith("ERROR") and re.search(r"relation|syntax error|no such table|column", t, re.I):
        code = "sql_error"
    elif t.startswith("ERROR") and re.search(r"remote rejected|rejected", t, re.I):
        code = "remote_rejected"
    elif t.startswith("ERROR"):
        code = "error"
    elif re.match(r"HTTP (\d{3})", t):
        n = re.match(r"HTTP (\d{3})", t).group(1)
        code = f"http_{n}" if int(n) >= 400 else None
    elif re.match(r"exit code [1-9]", t):
        code = "nonzero_exit"
    elif t.startswith("Traceback"):
        code = "python_error"
    if code:
        msg = re.sub(r"^ERROR:?\s*", "", t) if t.startswith("ERROR") else t
        return json.dumps({"ok": False, "error": {"code": code, "message": msg}}, ensure_ascii=False, separators=(",", ":"))
    data = re.sub(r"^OK:\s*", "", t) if t.startswith("OK:") else t
    return json.dumps({"ok": True, "data": data}, ensure_ascii=False, separators=(",", ":"))


def find_override(user, tool, result_text):
    for e in OVERRIDES:
        m = e["match"]
        if m["user"] == user and m["tool"] == tool and result_text.strip().startswith(m["result_prefix"]):
            return e
    return None


class Unconvertible(Exception):
    pass


def convert_record(rec):
    """returns (new_record, info) ; raises Unconvertible for unknown shapes"""
    msgs = [dict(m) for m in rec["messages"]]
    sysm = next((m for m in msgs if m["role"] == "system"), None)
    listed = SYS_TOOL_LIST.search(sysm["content"]) if sysm else None
    info = {"kind": None}
    if not listed:
        # no tools attached: only canonicalise stray dotted names in assistant text
        changed = False
        for m in msgs:
            if m["role"] == "assistant":
                new = canon_text(m["content"])
                if new != m["content"]:
                    m["content"] = new
                    changed = True
        if any(m["role"] == "user" and m["content"].startswith("[tool result") for m in msgs):
            raise Unconvertible("tool result without tool list")
        out = dict(rec, messages=msgs)
        info["kind"] = "no_tools_renamed" if changed else "unchanged"
        return out, info
    names = list(dict.fromkeys(n.strip() for n in listed.group(1).split(",") if n.strip()))     # unique, order-preserving (5 Phase-2 system messages listed a tool twice)
    for n in names:
        if n not in TOOLS:
            raise Unconvertible(f"unknown tool {n}")
    # system rewrite
    new_sys = None
    for rx, rep in SYS_TEMPLATES:
        if rx.match(sysm["content"]):
            new_sys = rx.sub(rep, sysm["content"])
            break
    if new_sys is None:
        raise Unconvertible("unknown system template")
    sysm["content"] = new_sys
    tools = [tool_def(n) for n in names]
    body = [m for m in msgs if m["role"] != "system"]
    users = [m for m in body if m["role"] == "user"]
    has_result = any(m["content"].startswith("[tool result") for m in users)
    out_msgs = [sysm]
    seed = md5(json.dumps(rec["messages"], sort_keys=True))
    consent = CONSENT[int(seed[:4], 16) % len(CONSENT)]

    def call_msg(dotted, args, content):
        return {"role": "assistant", "content": content, "tool_calls": [{"type": "function", "function": {"name": CANON[dotted], "arguments": ordered_args(dotted, args)}}]}

    if not has_result:
        # ---- shapes without a recorded result: single/multi-turn text; convert read-only proposals at the END of the conversation
        i = 0
        while i < len(body):
            m = body[i]
            if m["role"] == "assistant":
                pc = PROP_CALL.search(m["content"])
                last = i == len(body) - 1
                if pc and last:
                    dotted, argstr = pc.group(1), pc.group(2)
                    if dotted not in names:
                        raise Unconvertible(f"proposal for unattached tool {dotted}")
                    args = parse_args(argstr)
                    if not TOOLS[dotted]["requires_confirmation"] and args is not None and all(k in args for k in TOOLS[dotted]["parameters"].get("required", [])):
                        pre = m["content"][:pc.start()].strip()
                        pre = canon_text(pre) if pre else f"I'll use `{CANON[dotted]}` for this."
                        out_msgs.append(call_msg(dotted, args, pre))
                        info["kind"] = "read_proposal_to_call"
                        i += 1
                        continue
                    info["kind"] = info["kind"] or "confirm_preview_text"
                out_msgs.append({"role": "assistant", "content": canon_text(m["content"])})
            else:
                out_msgs.append(m)
            i += 1
        info["kind"] = info["kind"] or "text_only"
        return dict(rec, messages=out_msgs, tools=tools), info

    # ---- shapes with a recorded tool result
    nuser = len(users)
    if nuser == 3:
        # u1 request, a1 proposal, u2 consent, a2 'Running', u3 result, a3 final
        u1, a1, u2, a2, u3, a3 = body
        if [m["role"] for m in body] != ["user", "assistant", "user", "assistant", "user", "assistant"]:
            raise Unconvertible("flow3 role pattern")
        pc = PROP_CALL.search(a1["content"])
        rh = RESULT_HEAD.match(u3["content"])
        if not pc or not rh or pc.group(1) != rh.group(1) or pc.group(1) not in names:
            raise Unconvertible("flow3 proposal/result mismatch")
        dotted = pc.group(1)
        args = parse_args(pc.group(2))
        a1_text = a1["content"]
        if args is None:
            ov = find_override(u1["content"], dotted, rh.group(2))
            if not ov:
                raise Unconvertible("flow3 unparsable args")
            args = ov["arguments"]
            if "new_user" in ov:
                u1 = dict(u1, content=ov["new_user"])
            a1_text = a1_text[:pc.start()] + f"Proposed call: `{preview(dotted, args)}`" + a1_text[pc.end():]
            info["edited"] = True
        if TOOLS[dotted]["requires_confirmation"]:
            out_msgs += [u1, {"role": "assistant", "content": canon_text(a1_text)}, u2,
                         call_msg(dotted, args, canon_text(a2["content"]))]
            info["kind"] = "flow_confirm_then_call"
        else:
            # read-only tool: no confirmation round-trip; the call follows the request directly
            out_msgs += [u1, call_msg(dotted, args, f"I'll use `{CANON[dotted]}` for that.")]
            info["kind"] = "flow_read_direct"
        out_msgs += [{"role": "tool", "name": CANON[dotted], "content": envelope(rh.group(2))}, {"role": "assistant", "content": canon_text(a3["content"])}]
        return dict(rec, messages=out_msgs, tools=tools), info
    if nuser == 2:
        u1, a1, u2, a2 = body
        if [m["role"] for m in body] != ["user", "assistant", "user", "assistant"]:
            raise Unconvertible("flow2 role pattern")
        rh = RESULT_HEAD.match(u2["content"])
        mt = re.search(r"`([a-z]+\.[a-z_]+)`", a1["content"])
        if not rh or not mt or mt.group(1) != rh.group(1) or rh.group(1) not in names or PROP_CALL.search(a1["content"]):
            raise Unconvertible("flow2 shape")
        dotted = rh.group(1)
        ov = find_override(u1["content"], dotted, rh.group(2))
        if not ov:
            raise Unconvertible(f"flow2 without argument annotation: {u1['content'][:50]}")
        args = ov["arguments"]
        if "new_user" in ov:
            u1 = dict(u1, content=ov["new_user"])
            info["edited"] = True
        if TOOLS[dotted]["requires_confirmation"]:
            prev = (f"I'd use `{CANON[dotted]}` for this.\n\nProposed call: `{preview(dotted, args)}`\n\n"
                    "Since this changes things, I'll wait for your confirmation. Okay to proceed?")
            out_msgs += [u1, {"role": "assistant", "content": prev}, {"role": "user", "content": consent},
                         call_msg(dotted, args, f"Running `{CANON[dotted]}` now. I'll tell you exactly what it returns.")]
            info["kind"] = "direct_write_with_inserted_confirmation"
        else:
            out_msgs += [u1, call_msg(dotted, args, canon_text(a1["content"]))]
            info["kind"] = "direct_read"
        out_msgs += [{"role": "tool", "name": CANON[dotted], "content": envelope(rh.group(2))}, {"role": "assistant", "content": canon_text(a2["content"])}]
        return dict(rec, messages=out_msgs, tools=tools), info
    raise Unconvertible("unexpected number of user turns with a tool result")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(HERE.parent / "dataset" / "v2_clean"))
    ap.add_argument("--out", default=str(HERE.parent / "dataset" / "v2_final_candidate"))
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    src, out = Path(a.src), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    kinds = collections.Counter()
    unconv = []
    total = 0
    for split in ("train", "validation", "test"):
        with open(out / f"{split}.jsonl", "w", encoding="utf-8") as fo:
            for l in open(src / f"{split}.jsonl", encoding="utf-8"):
                rec = json.loads(l)
                total += 1
                try:
                    new, info = convert_record(rec)
                except Unconvertible as e:
                    unconv.append({"id": rec["id"], "why": str(e)})
                    new, info = rec, {"kind": "UNCONVERTED"}
                kinds[info["kind"]] += 1
                meta = dict(new["metadata"])
                if new.get("tools"):
                    meta["tool_protocol"] = SCHEMAS["protocol_version"]
                if info.get("edited"):
                    meta["edited"] = True
                new["metadata"] = meta
                new = {"id": new["id"], "messages": new["messages"], **({"tools": new["tools"]} if new.get("tools") else {}), "metadata": meta}
                fo.write(json.dumps(new, ensure_ascii=False) + "\n")
    rep = {"protocol_version": SCHEMAS["protocol_version"], "records": total, "by_kind": dict(kinds), "unconverted": unconv}
    json.dump(rep, open(out / "conversion_report.json", "w", encoding="utf-8"), indent=1)
    print(json.dumps({"records": total, "by_kind": dict(kinds), "unconverted": len(unconv)}, indent=1))
    for u in unconv[:15]:
        print("  UNCONVERTED", u)
    if a.strict and unconv:
        sys.exit(1)


if __name__ == "__main__":
    main()
