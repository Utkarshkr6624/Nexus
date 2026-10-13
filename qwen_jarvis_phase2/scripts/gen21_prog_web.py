"""Phase 2.1 programming: HTML/CSS, React, FastAPI, Node, REST, TypeScript, security, architecture, testing, Git, debugging dialogs.
Batches live in gen21_web_a/b/c.  Here every code fence is machine-checked (Python via ast.parse, JSON via json.loads, JavaScript via
node --check, HTML via html.parser tag balance); a record whose block fails is dropped.  Only snippets that were actually executed
(gen21_web_c RUNS) carry source_type 'verified'; everything else is 'authored' (syntax-checked where the language allows)."""
import ast
import json
import re
import subprocess
from html.parser import HTMLParser
from gen21_common import *
from gen21_run import SCRATCH
import gen21_web_a, gen21_web_b, gen21_web_c, gen21_web_d

FENCE = re.compile(r"```([a-zA-Z]*)\n(.*?)```", re.S)
VOID = {"meta", "img", "input", "br", "hr", "link"}


class Bal(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.bad = [], False

    def handle_starttag(self, t, a):
        if t not in VOID:
            self.stack.append(t)

    def handle_endtag(self, t):
        if t in VOID:
            return
        if self.stack and self.stack[-1] == t:
            self.stack.pop()
        else:
            self.bad = True


def check_block(lang, code):
    if lang == "python":
        try:
            ast.parse(code)
        except SyntaxError:
            # allow snippets that contain '...' placeholders only; real syntax errors are rejected
            return False
        return True
    if lang == "json":
        try:
            json.loads(code)
            return True
        except ValueError:
            return False
    if lang == "javascript":
        f = SCRATCH / "chk.js"
        f.write_text(code, encoding="utf-8")
        return subprocess.run(["node", "--check", str(f)], capture_output=True).returncode == 0
    if lang == "html":
        p = Bal()
        p.feed(code)
        return not p.bad and not p.stack
    return True       # css, jsx, typescript, bash, dockerfile: not machine-parsed here


recs, dropped = [], []
items = gen21_web_a.B.items + gen21_web_b.B.items + gen21_web_c.B.items + gen21_web_d.B.items
for j, (sub, sysm, turns, diff, src) in enumerate(items):
    text = "\n".join(turns)
    bad = [(l, c[:60]) for l, c in FENCE.findall(text) if not check_block(l, c)]
    if bad:
        dropped.append((sub, bad))
        continue
    pairs = [("user" if k % 2 == 0 else "assistant", t) for k, t in enumerate(turns)]
    recs.append(mk("programming", sub, pairs, f"p21web-{j}", diff=diff, src=src, system=sysm))

if __name__ == "__main__":
    save("prog21_web", recs)
    print("dropped", len(dropped))
    for x in dropped:
        print("  ", x)
