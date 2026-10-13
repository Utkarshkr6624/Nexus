"""Shared helper for gen21_algo_*.py: X() runs a snippet and records a verified example; D() records a verified multi-turn dialog."""
import re
from gen21_common import *
from gen21_run import *

recs = []
dropped = []
_i = [0]
RUN = {"python": run_py, "javascript": run_js, "typescript": run_ts, "c": run_c, "cpp": lambda c: run_c(c, True), "java": run_java}
FENCE = {"python": "python", "javascript": "javascript", "typescript": "typescript", "c": "c", "cpp": "cpp", "java": "java"}


def _run(lang, code):
    ok, out = RUN[lang](code)
    out = "\n".join(l.rstrip() for l in out.strip().splitlines())
    out = re.sub(r"\bin \d+\.\d+s\b", "in 0.001s", out)
    return ok, out


def X(lang, sub, level, ask, code, answer, expect=None):
    """ask may contain {code}; answer may contain {out}. Code must run OK; if expect given, output must equal it."""
    _i[0] += 1
    ok, out = _run(lang, code)
    if not ok or (expect is not None and out != expect):
        dropped.append((sub, ask[:50], out[:160]))
        return
    block = f"```{FENCE[lang]}\n{code.strip()}\n```"
    user = ask.replace("{code}", block) if "{code}" in ask else f"{ask}\n\n{block}"
    recs.append(mk("programming", sub, [("user", user), ("assistant", answer.replace("{out}", out))], f"p21alg-{_i[0]}", diff=level, src="verified"))


def D(sub, level, turns, codes):
    """turns: [(user, assistant), ...]; codes: [(lang, code), ...] each executed; text may use {out0},{out1}...; {code0}.. insert fenced code."""
    _i[0] += 1
    outs, blocks = [], []
    for lang, code in codes:
        ok, out = _run(lang, code)
        if not ok:
            dropped.append((sub, turns[0][0][:50], out[:160]))
            return
        outs.append(out)
        blocks.append(f"```{FENCE[lang]}\n{code.strip()}\n```")
    pairs = []
    for u, a in turns:
        for k in range(len(codes)):
            u = u.replace("{out%d}" % k, outs[k]).replace("{code%d}" % k, blocks[k])
            a = a.replace("{out%d}" % k, outs[k]).replace("{code%d}" % k, blocks[k])
        pairs += [("user", u), ("assistant", a)]
    recs.append(mk("programming", sub, pairs, f"p21alg-{_i[0]}", diff=level, src="verified"))
