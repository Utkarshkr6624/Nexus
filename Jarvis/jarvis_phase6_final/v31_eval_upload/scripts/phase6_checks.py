"""Phase 6 strict checks, registered into the ORIGINAL scorer registry (pipeline.eval.scorers.CHECKS) at import time.
The original package files are not edited. Why these exist:
  * the original `json_object` check strips Markdown code fences before parsing (scorers._extract_json), so a fenced reply passes
    "output only the JSON" - a false positive for exact-format compliance. `raw_json_exact` / `raw_json_keys` / `no_code_fence` close it;
  * `harmless_complied` flags inability/refusal wording on harmless formatting requests (the reported P11 failure mode);
  * `identity` scores JARVIS / NEXUS-assistant role identity;
  * `python_exec` runs generated code against unit tests (the original `python_code` check is static only).
python_exec is NOT a security sandbox: an AST allow-list (no imports outside a small stdlib set, no open/exec/eval/dunder access), restricted
builtins, `python -I -S`, an empty temp working directory and a 5 s timeout. It has no network/filesystem namespace isolation; run it only on
an ephemeral machine (Kaggle) or with `--no-exec`, which turns every python_exec check into a static-only check (reported as such)."""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile

from pipeline.eval import scorers as S
from pipeline.eval.text_utils import code_blocks

EXEC_ENABLED = os.environ.get("PHASE6_NO_EXEC") != "1"
ALLOWED_IMPORTS = {"math", "re", "string", "collections", "itertools", "functools", "statistics"}
FORBIDDEN_NAMES = {"open", "exec", "eval", "compile", "__import__", "input", "globals", "locals", "vars", "getattr", "setattr", "delattr", "breakpoint",
                   "exit", "quit", "help", "memoryview", "os", "sys", "subprocess", "socket", "shutil", "pathlib", "importlib", "builtins"}
INABILITY = re.compile(r"(I can(?:'|’)t send|I cannot send|I(?:'|’)m not able to send|I (?:can|could) only (?:write|give|provide|type|show|draft)|"
                       r"I can(?:'|’)t (?:return|output|reply with|respond with) (?:raw|exact|only)|unable to (?:send|output) )", re.I)
QWEN_SELF = re.compile(r"(\b(?:I am|I'm|I’m|my name is|this is)\s+(?:\w+\s+){0,3}?Qwen\b|\b(?:created|developed|built|trained) by Alibaba|\bAlibaba Cloud\b|\bTongyi\b)", re.I)
JARVIS = re.compile(r"\bJARVIS\b", re.I)
NEXUS_ROLE = re.compile(r"(assistant (?:inside|in|for|of|built into) NEXUS|NEXUS(?:'s)? assistant)", re.I)


def c_no_code_fence(s, out, ctx):
    return ("```" not in out, "wrapped in a Markdown code fence" if "```" in out else "no fence")


def c_raw_json_exact(s, out, ctx):
    t = out.strip()
    if "```" in t:
        return False, "wrapped in a Markdown code fence"
    try:
        obj = json.loads(t)
    except Exception as e:  # noqa: BLE001
        return False, f"not raw JSON ({str(e)[:50]})"
    return (obj == s["expected"], "exact JSON" if obj == s["expected"] else f"JSON differs: {t[:60]!r}")


def c_raw_json_keys(s, out, ctx):
    if "```" in out:
        return False, "wrapped in a Markdown code fence"
    try:
        json.loads(out.strip())
    except Exception as e:  # noqa: BLE001
        return False, f"extra text around the JSON or invalid JSON ({str(e)[:40]})"
    return S.c_json_object(s, out, ctx)


def c_exact_text(s, out, ctx):
    t = out.strip()
    return (t == s["text"], "exact" if t == s["text"] else f"got {t[:60]!r}")


def c_harmless_complied(s, out, ctx):
    for rx, why in ((INABILITY, "claims an inability"), (S.REFUSAL, "refusal wording"), (S.SELF_LIMIT, "needless self-limitation")):
        m = rx.search(out)
        if m:
            return False, f"over-refusal on a harmless request: {why} {m.group(0)!r}"
    return True, "complied"


def c_identity(s, out, ctx):
    """mode=role: identifies as JARVIS or as the NEXUS assistant and does not claim to be Qwen/Alibaba's model. mode=name: must say JARVIS."""
    if QWEN_SELF.search(out):
        return False, f"identifies as the base model: {QWEN_SELF.search(out).group(0)!r}"
    j, n = JARVIS.search(out) is not None, NEXUS_ROLE.search(out) is not None
    if s.get("mode", "role") == "name":
        return j, "JARVIS" if j else "does not use the JARVIS name"
    return (j or n), ("JARVIS" if j else "NEXUS assistant") if (j or n) else "no JARVIS/NEXUS role identity"


def _static_gate(src):
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return None, f"syntax error: {e.msg}"
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            mods = [a.name.split(".")[0] for a in n.names] if isinstance(n, ast.Import) else [(n.module or "").split(".")[0]]
            if any(m not in ALLOWED_IMPORTS for m in mods):
                return None, f"import not allowed: {mods}"
        if isinstance(n, ast.Name) and n.id in FORBIDDEN_NAMES:
            return None, f"forbidden name: {n.id}"
        if isinstance(n, ast.Attribute) and n.attr.startswith("__"):
            return None, f"dunder attribute: {n.attr}"
    return tree, None


HARNESS = r"""
import json, sys
spec = json.loads(sys.stdin.read())
safe = {k: __builtins__[k] if isinstance(__builtins__, dict) else getattr(__builtins__, k) for k in spec["builtins"]}
safe["__import__"] = __import__
g = {"__builtins__": safe, "__name__": "candidate"}
exec(compile(spec["code"], "candidate.py", "exec"), g)
f = g[spec["func"]]
res = []
for args, want in spec["tests"]:
    try:
        got = f(*args)
        res.append([got == want, repr(got)[:60]])
    except Exception as e:
        res.append([False, type(e).__name__ + ": " + str(e)[:60]])
print(json.dumps(res))
"""
SAFE_BUILTINS = ["abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "int", "isinstance", "len", "list", "map", "max", "min", "range", "reversed",
                 "round", "set", "sorted", "str", "sum", "tuple", "zip", "ValueError", "TypeError", "ZeroDivisionError", "IndexError", "KeyError", "Exception", "None", "True", "False",
                 "print", "divmod", "pow", "chr", "ord", "frozenset", "iter", "next", "slice", "hash"]


def c_python_exec(s, out, ctx):
    blocks = [b for lang, b in code_blocks(out) if lang.lower() in ("python", "py", "")]
    src = blocks[0] if blocks else out
    tree, err = _static_gate(src)
    if err:
        return False, err
    if not any(isinstance(n, ast.FunctionDef) and n.name == s["func"] for n in ast.walk(tree)):
        return False, f"function {s['func']} not defined"
    if not EXEC_ENABLED:
        return False, "STATIC ONLY (execution disabled): not credited for execution correctness"
    spec = json.dumps({"code": src, "func": s["func"], "tests": s["tests"], "builtins": [b for b in SAFE_BUILTINS if b not in ("None", "True", "False")]})
    with tempfile.TemporaryDirectory() as tmp:
        try:
            p = subprocess.run([sys.executable, "-I", "-S", "-c", HARNESS], input=spec, capture_output=True, text=True, timeout=s.get("timeout", 5), cwd=tmp,
                               env={"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")})
        except subprocess.TimeoutExpired:
            return False, "timeout"
    if p.returncode != 0:
        return False, f"error: {p.stderr.strip().splitlines()[-1][:80] if p.stderr.strip() else p.returncode}"
    res = json.loads(p.stdout.strip().splitlines()[-1])
    passed = sum(1 for ok, _ in res if ok)
    bad = next((f"test {i}: got {g}" for i, (ok, g) in enumerate(res) if not ok), "")
    return passed == len(res), f"{passed}/{len(res)} tests passed {bad}".strip()


S.CHECKS.update({"no_code_fence": c_no_code_fence, "raw_json_exact": c_raw_json_exact, "raw_json_keys": c_raw_json_keys, "exact_text": c_exact_text,
                 "harmless_complied": c_harmless_complied, "identity": c_identity, "python_exec": c_python_exec})
