"""Phase 2.1 code runner: executes snippets in a scratch directory and returns (ok, combined_output).
Used so that program output / compiler errors quoted in the dataset are the real ones.  Stdlib only."""
import os
import re
import subprocess
import tempfile
from pathlib import Path

SCRATCH = Path(os.environ.get("P2_SCRATCH", tempfile.gettempdir())) / "p2_verify21"
SCRATCH.mkdir(parents=True, exist_ok=True)
_n = [0]


def _dir():
    _n[0] += 1
    d = SCRATCH / f"t{_n[0]}"
    d.mkdir(exist_ok=True)
    return d


def _clean(text, d):
    t = text.replace(str(d), "").replace(str(d).replace("\\", "/"), "")
    t = re.sub(r"[A-Za-z]:[\\/][^\s:]*?[\\/](?=[\w.]+\.(?:c|cpp|java|js|ts|py):)", "", t)
    return t.replace("\r", "").strip()


def _run(cmd, d, timeout=60):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=d, encoding="utf-8", errors="replace")
    return r.returncode, _clean(r.stdout + r.stderr, d)


def run_py(code):
    d = _dir()
    f = d / "main.py"
    f.write_text(code, encoding="utf-8")
    rc, out = _run(["python", "main.py"], d)
    return rc == 0, out


def run_js(code):
    d = _dir()
    (d / "main.js").write_text(code, encoding="utf-8")
    rc, out = _run(["node", "main.js"], d)
    return rc == 0, out


def run_ts(code):
    d = _dir()
    (d / "main.ts").write_text(code, encoding="utf-8")
    rc, out = _run(["node", "--experimental-strip-types", "main.ts"], d)
    out = "\n".join(l for l in out.splitlines() if "ExperimentalWarning" not in l and "node --trace-warnings" not in l)
    return rc == 0, out.strip()


def compile_c(code, cpp=False):
    d = _dir()
    f = d / ("main.cpp" if cpp else "main.c")
    f.write_text(code, encoding="utf-8")
    exe = d / "prog.exe"
    flags = ["-pedantic-errors"] if cpp else ["-Werror=implicit-function-declaration", "-Werror=incompatible-pointer-types"]   # match the strictness of current gcc/g++ defaults
    rc, out = _run(["g++" if cpp else "gcc", "-std=c++17" if cpp else "-std=c11"] + flags + [f.name, "-o", exe.name], d, 90)
    return rc == 0, out, d


def run_c(code, cpp=False):
    ok, out, d = compile_c(code, cpp)
    if not ok:
        return False, out
    rc, o = _run([str(d / "prog.exe")], d, 30)
    return rc == 0, o


def run_java(code, cls="Main"):
    d = _dir()
    (d / f"{cls}.java").write_text(code, encoding="utf-8")
    rc, out = _run(["javac", f"{cls}.java"], d, 90)
    if rc:
        return False, out
    rc, o = _run(["java", "-cp", str(d), cls], d, 30)
    return rc == 0, o


def run_sql(schema, query):
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.executescript(schema)
    cur = con.execute(query)
    cols = [c[0] for c in cur.description]
    rows = cur.fetchall()
    return cols, rows


def table(cols, rows):
    """plain-text table"""
    cells = [[str(c) for c in cols]] + [["NULL" if v is None else str(v) for v in r] for r in rows]
    w = [max(len(r[i]) for r in cells) for i in range(len(cols))]
    lines = [" | ".join(c.ljust(w[i]) for i, c in enumerate(cells[0])), "-+-".join("-" * x for x in w)]
    lines += [" | ".join(c.ljust(w[i]) for i, c in enumerate(r)) for r in cells[1:]]
    return "\n".join(lines)
