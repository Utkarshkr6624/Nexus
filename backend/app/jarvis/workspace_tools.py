"""Phase 7 tools added after training: workspace, code, data and calculation helpers.

Unlike the 21 ``toolproto-1`` tools in ``tool_schemas.json``, checkpoint_v31_139 never saw these
names, so how well it picks them is unmeasured. They follow the same contract: strict argument
schemas, the ``{"ok": ...}`` envelope, the workspace sandbox of ``tools._inside`` (relative paths,
no ``..``, no credential-like names, no symlink escapes) and the approval tiers of the registry:
read tools run directly, anything that writes or executes waits for the user's Approve.
"""

from __future__ import annotations

import ast
import csv
import difflib
import hashlib
import io
import json
import math
import operator
import os
import re
import shutil
import statistics
import subprocess
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from app.core.config import Settings
from app.jarvis.tools import (
    _SAFE_ENV,
    _SECRET_NAME,
    MAX_LIST,
    ToolContext,
    ToolError,
    ToolSpec,
    _fs_reason,
    _inside,
    _rel,
    _root,
    _threaded,
    _walk,
    ok,
)

MAX_TEXT = 200_000
_S = {"type": "string"}


def _spec(
    name: str, desc: str, side_effect: str, props: dict[str, Any], required: list[str]
) -> ToolSpec:
    params = {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }
    return ToolSpec(name, desc, params, side_effect)


SPECS = [
    _spec(
        "workspace_list_files",
        "List files and folders in the NEXO workspace (optionally under a sub-folder, filtered by a glob such as *.py).",
        "read",
        {"path": _S, "pattern": _S},
        [],
    ),
    _spec(
        "workspace_file_metadata",
        "Size, extension, modified/created times and SHA-256 of one workspace file.",
        "read",
        {"path": _S},
        ["path"],
    ),
    _spec(
        "workspace_compare_files",
        "Compare two workspace text files and return a unified diff.",
        "read",
        {"path_a": _S, "path_b": _S},
        ["path_a", "path_b"],
    ),
    _spec(
        "workspace_create_file",
        "Create a NEW text file in the workspace; refuses if the file already exists.",
        "write",
        {"path": _S, "content": _S},
        ["path", "content"],
    ),
    _spec(
        "workspace_backup_file",
        "Copy a workspace file to .nexo_backups/ and verify the copy's SHA-256.",
        "write",
        {"path": _S},
        ["path"],
    ),
    _spec(
        "code_search_symbols",
        "Find definitions (functions, classes, constants) and references of a symbol in workspace source files.",
        "read",
        {"symbol": _S, "path": _S},
        ["symbol"],
    ),
    _spec(
        "code_apply_patch",
        "Apply a unified diff (git format) to files in the workspace after checking it applies cleanly.",
        "write",
        {"patch": _S},
        ["patch"],
    ),
    _spec(
        "run_project_tests",
        "Run the approved project test command in the workspace and report its exit code and output.",
        "execute",
        {},
        [],
    ),
    _spec(
        "run_project_lint",
        "Run the approved project lint/format/type-check command in the workspace and report its exit code and output.",
        "execute",
        {},
        [],
    ),
    _spec(
        "git_inspect",
        "Read-only git information for the project: action is status, log, diff (optionally for one path) or branch.",
        "read",
        {"action": _S, "path": _S, "limit": {"type": "integer", "minimum": 1}},
        ["action"],
    ),
    _spec(
        "json_yaml_validate",
        "Check that a workspace .json/.yaml/.yml file (or given text) parses; reports the error position.",
        "read",
        {"path": _S, "content": _S, "format": _S},
        [],
    ),
    _spec(
        "csv_analyze",
        "Summarise a workspace CSV file: rows, columns, missing values, duplicate rows, numeric statistics and top values.",
        "read",
        {"path": _S},
        ["path"],
    ),
    _spec(
        "calculate",
        "Exact arithmetic (e.g. '(3+4)*2**10', 'sqrt(2)'), unit conversion ('12 km to mi', '30 c to f') and date maths "
        "('2026-10-13 + 30 days', 'days between 2026-01-01 and 2026-10-13').",
        "read",
        {"expression": _S},
        ["expression"],
    ),
]


# -- workspace files ---------------------------------------------------------------


def _text(p: Path, label: str) -> str:
    if not p.is_file():
        raise ToolError("not_found", f"{label} does not exist")
    raw = p.read_bytes()[: MAX_TEXT + 1]
    if b"\x00" in raw[:4096]:
        raise ToolError("unsupported", f"{label} is a binary file")
    return raw[:MAX_TEXT].decode("utf-8", errors="replace")


def _list_files(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    base = _inside(ctx, args.get("path") or ".")
    if not base.is_dir():
        raise ToolError("not_found", f"{args.get('path')} is not a directory")
    pattern = args.get("pattern") or "*"
    entries, truncated = [], False
    for p in sorted(base.iterdir()):
        if _SECRET_NAME.search(p.name) or not p.resolve().is_relative_to(_root(ctx)):
            continue
        if not Path(p.name).match(pattern) and not p.is_dir():
            continue
        if len(entries) >= MAX_LIST:
            truncated = True
            break
        entries.append(
            {
                "path": _rel(ctx, p),
                "type": "dir" if p.is_dir() else "file",
                **({"bytes": p.stat().st_size} if p.is_file() else {}),
            }
        )
    return ok({"path": _rel(ctx, base) or ".", "entries": entries, "truncated": truncated})


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _metadata(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    p = _inside(ctx, args["path"])
    if not p.is_file():
        raise ToolError("not_found", f"{args['path']} does not exist")
    st = p.stat()
    return ok(
        {
            "path": _rel(ctx, p),
            "bytes": st.st_size,
            "extension": p.suffix,
            "modified": datetime.fromtimestamp(st.st_mtime, ctx.tz).isoformat(timespec="seconds"),
            "created": datetime.fromtimestamp(st.st_ctime, ctx.tz).isoformat(timespec="seconds"),
            "sha256": _sha256(p),
        }
    )


def _compare(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    a, b = _inside(ctx, args["path_a"]), _inside(ctx, args["path_b"])
    ta, tb = _text(a, args["path_a"]), _text(b, args["path_b"])
    diff = list(
        difflib.unified_diff(
            ta.splitlines(), tb.splitlines(), _rel(ctx, a), _rel(ctx, b), lineterm="", n=2
        )
    )
    body = "\n".join(diff)
    return ok(
        {
            "identical": ta == tb,
            "diff": body[:20_000],
            "truncated": len(body) > 20_000,
            "added": sum(1 for d in diff if d.startswith("+") and not d.startswith("+++")),
            "removed": sum(1 for d in diff if d.startswith("-") and not d.startswith("---")),
        }
    )


def _create(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    p = _inside(ctx, args["path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with p.open("x", encoding="utf-8", newline="") as fh:  # "x": fails if it exists, no race
            fh.write(args["content"])
    except FileExistsError:
        raise ToolError("conflict", f"{args['path']} already exists; nothing was written") from None
    return ok({"path": _rel(ctx, p), "bytes": p.stat().st_size})


def _backup(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    p = _inside(ctx, args["path"])
    if not p.is_file():
        raise ToolError("not_found", f"{args['path']} does not exist")
    dest_dir = _root(ctx) / ".nexo_backups"
    dest_dir.mkdir(exist_ok=True)
    stamp = datetime.now(ctx.tz).strftime("%Y%m%dT%H%M%S%f")
    dest = dest_dir / f"{p.name}.{stamp}.bak"
    shutil.copy2(p, dest)
    src_sha, dst_sha = _sha256(p), _sha256(dest)
    if src_sha != dst_sha:
        dest.unlink(missing_ok=True)
        raise ToolError("error", "the backup copy did not match the original; it was removed")
    return ok(
        {"source": _rel(ctx, p), "backup": _rel(ctx, dest), "sha256": src_sha, "verified": True}
    )


# -- code ---------------------------------------------------------------------------

_SOURCE = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".go",
    ".rs",
    ".java",
    ".cs",
    ".rb",
    ".php",
    ".sql",
}
_IDENT = re.compile(r"^[A-Za-z_$][\w$]{0,120}$")


def _symbols(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    sym = args["symbol"].strip()
    if not _IDENT.match(sym):
        raise ToolError(
            "invalid_arguments", "symbol must be one identifier, e.g. parse_day or JarvisPage"
        )
    s = re.escape(sym)
    definition = re.compile(
        rf"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:def|class|function|interface|type|enum|struct|fn|func|const|let|var)\s+{s}\b"
        rf"|^\s*{s}\s*(?::[^=]*)?=(?!=)"
    )
    ref = re.compile(rf"(?<![\w$]){s}(?![\w$])")
    base = _inside(ctx, args.get("path") or ".")
    if not base.is_dir():
        raise ToolError("not_found", f"{args.get('path')} is not a directory")
    defs, refs, scanned = [], [], 0
    for p in _walk(base, _root(ctx)):
        if p.suffix not in _SOURCE or p.stat().st_size > 1_000_000:
            continue
        scanned += 1
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if definition.search(line):
                defs.append({"path": _rel(ctx, p), "line": n, "text": line.strip()[:200]})
            elif ref.search(line) and len(refs) < MAX_LIST:
                refs.append({"path": _rel(ctx, p), "line": n, "text": line.strip()[:200]})
        if scanned >= 2000:
            break
    return ok(
        {
            "symbol": sym,
            "definitions": defs[:MAX_LIST],
            "references": refs,
            "files_scanned": scanned,
        }
    )


_PATCH_PATH = re.compile(r"^(?:---|\+\+\+) (?:[ab]/)?(\S+)", re.M)


def _git() -> str:
    exe = shutil.which("git")
    if not exe:
        raise ToolError("unavailable", "git is not installed, so patches cannot be applied")
    return exe


def _apply_patch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    patch = args["patch"]
    if "GIT binary patch" in patch or re.search(
        r"^(?:new|old|deleted) file mode 120000", patch, re.M
    ):
        raise ToolError("permission_denied", "binary and symlink patches are not accepted")
    paths = {m for m in _PATCH_PATH.findall(patch) if m != "/dev/null"}
    if not paths:
        raise ToolError(
            "invalid_arguments", "no file paths found; send a unified diff with ---/+++ headers"
        )
    for raw in paths:
        _inside(ctx, raw)  # every touched path must be a normal workspace path
    root = _root(ctx)
    # The workspace may sit inside another git repository; stop git from finding it.
    env = {k: os.environ[k] for k in _SAFE_ENV if k in os.environ} | {
        "GIT_CEILING_DIRECTORIES": str(root.parent)
    }
    cmd = [_git(), "apply", "--whitespace=nowarn", "-"]
    check = subprocess.run(  # noqa: S603 - resolved git, fixed argv
        [*cmd[:2], "--check", *cmd[2:]],
        input=patch.encode(),
        cwd=root,
        env=env,
        capture_output=True,
        timeout=30,
    )
    if check.returncode:
        raise ToolError(
            "conflict",
            "the patch does not apply cleanly; nothing was changed: "
            + check.stderr.decode(errors="replace")[:400],
        )
    done = subprocess.run(  # noqa: S603 - resolved git, fixed argv
        cmd, input=patch.encode(), cwd=root, env=env, capture_output=True, timeout=30
    )
    if done.returncode:
        raise ToolError("error", "git apply failed: " + done.stderr.decode(errors="replace")[:400])
    return ok({"applied": True, "files": sorted(paths)})


def _project_commands(s: Settings) -> dict[str, list[str]]:
    raw = s.jarvis_project_commands.strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return (
        {
            k: v
            for k, v in data.items()
            if isinstance(v, list) and v and all(isinstance(x, str) for x in v)
        }
        if isinstance(data, dict)
        else {}
    )


def _command_reason(key: str):
    def reason(s: Settings) -> str | None:
        return _fs_reason(s) or (
            None
            if key in _project_commands(s)
            else f"no approved '{key}' command is configured (JARVIS_PROJECT_COMMANDS)"
        )

    return reason


#: Project test suites take longer than an ordinary tool call.
COMMAND_TIMEOUT_SECONDS = 600.0


def _run_command(key: str):
    def run(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        argv = _project_commands(ctx.settings)[key]
        exe = shutil.which(argv[0])
        if not exe:
            raise ToolError("unavailable", f"{argv[0]} is not installed")
        env = {k: os.environ[k] for k in _SAFE_ENV if k in os.environ}
        try:
            p = subprocess.run(  # noqa: S603 - approved command from server config, no shell
                [exe, *argv[1:]],
                cwd=_root(ctx),
                env=env,
                capture_output=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            raise ToolError(
                "timeout", f"the {key} command did not finish within {COMMAND_TIMEOUT_SECONDS:g}s"
            ) from None
        out = (p.stdout + p.stderr).decode("utf-8", errors="replace")
        return ok(
            {
                "command": argv,
                "exit_code": p.returncode,
                "passed": p.returncode == 0,
                "output_tail": out[-6000:],
                "truncated": len(out) > 6000,
            }
        )

    return run


def _git_reason(s: Settings) -> str | None:
    d = s.jarvis_git_repo_dir.strip()
    if not d:
        return "no repository is configured (JARVIS_GIT_REPO_DIR)"
    if not (Path(d) / ".git").exists():
        return "the configured JARVIS_GIT_REPO_DIR is not a git working tree"
    return None if shutil.which("git") else "git is not installed"


def _git_inspect(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    action = args["action"].strip().lower()
    limit = min(args.get("limit") or 10, 50)
    path = (args.get("path") or "").strip().replace("\\", "/")
    if path and (path.startswith(("-", "/")) or ".." in path.split("/") or ":" in path):
        raise ToolError(
            "permission_denied", "path must be a plain relative path inside the repository"
        )
    if path and _SECRET_NAME.search(Path(path).name):
        raise ToolError("permission_denied", "credential-like files are not accessible to NEXO")
    tail = ["--", path] if path else []
    commands = {
        "status": ["status", "--short", "--branch"],
        "log": ["log", f"-n{limit}", "--date=iso", "--pretty=format:%h %ad %an %s", *tail],
        "diff": [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            *(["--stat"] if not path else []),
            *tail,
        ],
        "branch": ["branch", "--list", "--no-color"],
    }
    if action not in commands:
        raise ToolError("invalid_arguments", "action must be one of: status, log, diff, branch")
    repo = Path(ctx.settings.jarvis_git_repo_dir)
    env = {k: os.environ[k] for k in _SAFE_ENV if k in os.environ} | {
        "GIT_PAGER": "cat",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    if path and action == "diff" and (repo / path).is_dir():
        # A folder diff could print tracked credential files the name check above never sees.
        raise ToolError(
            "permission_denied", "diff takes one file; use diff without a path for a summary"
        )
    p = subprocess.run(  # noqa: S603 - resolved git, fixed subcommands
        [_git(), "-c", "core.fsmonitor=false", *commands[action]],
        cwd=repo,
        env=env,
        capture_output=True,
        timeout=30,
    )
    if p.returncode:
        raise ToolError("error", p.stderr.decode(errors="replace")[:400])
    out = p.stdout.decode("utf-8", errors="replace")
    return ok({"action": action, "output": out[:20_000], "truncated": len(out) > 20_000})


# -- data ---------------------------------------------------------------------------


def _validate(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    if bool(args.get("path")) == bool(args.get("content")):
        raise ToolError("invalid_arguments", "give exactly one of path or content")
    if args.get("path"):
        p = _inside(ctx, args["path"])
        text, fmt = _text(p, args["path"]), (args.get("format") or p.suffix.lstrip(".")).lower()
    else:
        text, fmt = args["content"], (args.get("format") or "json").lower()
    if fmt == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return ok(
                {
                    "format": "json",
                    "valid": False,
                    "error": exc.msg,
                    "line": exc.lineno,
                    "column": exc.colno,
                }
            )
    elif fmt in ("yaml", "yml"):
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            return ok(
                {
                    "format": "yaml",
                    "valid": False,
                    "error": str(getattr(exc, "problem", exc)),
                    "line": mark and mark.line + 1,
                    "column": mark and mark.column + 1,
                }
            )
    else:
        raise ToolError(
            "invalid_arguments", "format must be json or yaml (syntax only; no schema validation)"
        )
    return ok(
        {"format": "yaml" if fmt == "yml" else fmt, "valid": True, "top_level": type(data).__name__}
    )


def _csv(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    p = _inside(ctx, args["path"])
    text = _text(p, args["path"])
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as exc:
        raise ToolError("invalid_arguments", f"not valid CSV: {exc}") from None
    if not rows:
        return ok({"rows": 0, "columns": []})
    header, body = rows[0], rows[1:]
    columns = []
    for i, name in enumerate(header):
        values = [r[i].strip() if i < len(r) else "" for r in body]
        present = [v for v in values if v]
        nums = []
        for v in present:
            try:
                nums.append(float(v))
            except ValueError:
                break
        col: dict[str, Any] = {"name": name, "missing": len(values) - len(present)}
        if present and len(nums) == len(present):
            col |= {
                "type": "number",
                "min": min(nums),
                "max": max(nums),
                "mean": round(statistics.fmean(nums), 6),
            }
        else:
            col |= {
                "type": "text",
                "distinct": len(set(present)),
                "top": Counter(present).most_common(3),
            }
        columns.append(col)
    seen = Counter(tuple(r) for r in body)
    return ok(
        {
            "path": _rel(ctx, p),
            "rows": len(body),
            "columns": columns,
            "duplicate_rows": sum(c - 1 for c in seen.values() if c > 1),
            "ragged_rows": sum(1 for r in body if len(r) != len(header)),
            "truncated": len(text) >= MAX_TEXT,
        }
    )


# -- calculate ------------------------------------------------------------------------

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
_FUNCS = {
    n: getattr(math, n)
    for n in (
        "sqrt",
        "log",
        "log10",
        "log2",
        "exp",
        "sin",
        "cos",
        "tan",
        "asin",
        "acos",
        "atan",
        "floor",
        "ceil",
        "factorial",
        "radians",
        "degrees",
    )
}
_FUNCS |= {"abs": abs, "round": round, "min": min, "max": max}
_CONSTS = {"pi": math.pi, "e": math.e}

#: factor to the base unit of its dimension
_UNITS = {
    "mm": ("length", 0.001),
    "cm": ("length", 0.01),
    "m": ("length", 1.0),
    "km": ("length", 1000.0),
    "in": ("length", 0.0254),
    "ft": ("length", 0.3048),
    "yd": ("length", 0.9144),
    "mi": ("length", 1609.344),
    "mg": ("mass", 1e-6),
    "g": ("mass", 0.001),
    "kg": ("mass", 1.0),
    "lb": ("mass", 0.45359237),
    "oz": ("mass", 0.028349523125),
    "ml": ("volume", 0.001),
    "l": ("volume", 1.0),
    "gal": ("volume", 3.785411784),
    "s": ("time", 1.0),
    "min": ("time", 60.0),
    "h": ("time", 3600.0),
    "day": ("time", 86400.0),
    "week": ("time", 604800.0),
    "b": ("data", 1.0),
    "kb": ("data", 1e3),
    "mb": ("data", 1e6),
    "gb": ("data", 1e9),
    "kib": ("data", 1024.0),
    "mib": ("data", 1024.0**2),
    "gib": ("data", 1024.0**3),
}
_TEMP = {"c", "f", "k"}


def _eval(node: ast.AST) -> float | int:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 1000 or abs(left) > 1e6):
            raise ToolError("invalid_arguments", "exponent too large")
        return _OPS[type(node.op)](left, right)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCS
        and not node.keywords
    ):
        a = [_eval(x) for x in node.args]
        if node.func.id == "factorial" and (not a or a[0] > 1000):
            raise ToolError("invalid_arguments", "factorial argument too large")
        return _FUNCS[node.func.id](*a)
    raise ToolError(
        "invalid_arguments",
        "only numbers, + - * / // % **, parentheses, pi, e and math functions are allowed",
    )


def _temp(v: float, a: str, b: str) -> float:
    c = {"c": v, "f": (v - 32) * 5 / 9, "k": v - 273.15}[a]
    return {"c": c, "f": c * 9 / 5 + 32, "k": c + 273.15}[b]


def _calculate(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    expr = args["expression"].strip()
    if len(expr) > 500:
        raise ToolError("invalid_arguments", "expression too long")
    low = expr.lower()
    if m := re.fullmatch(r"days between (\d{4}-\d{2}-\d{2}) and (\d{4}-\d{2}-\d{2})", low):
        a, b = (date.fromisoformat(x) for x in m.groups())
        return ok({"expression": expr, "kind": "date", "result": (b - a).days, "unit": "days"})
    if m := re.fullmatch(r"(\d{4}-\d{2}-\d{2})\s*([+-])\s*(\d+)\s*(day|days|week|weeks)", low):
        d0, sign, n, unit = m.groups()
        delta = timedelta(days=int(n) * (7 if unit.startswith("week") else 1))
        out = date.fromisoformat(d0) + (delta if sign == "+" else -delta)
        return ok(
            {
                "expression": expr,
                "kind": "date",
                "result": out.isoformat(),
                "weekday": out.strftime("%A"),
            }
        )
    if m := re.fullmatch(r"(-?[\d.]+(?:e-?\d+)?)\s*°?([a-z]+)\s+(?:to|in)\s+°?([a-z]+)", low):
        v, a, b = float(m.group(1)), m.group(2), m.group(3)
        a, b = (
            {"days": "day", "hr": "h", "hours": "h", "weeks": "week"}.get(a, a),
            {"days": "day", "hr": "h", "hours": "h", "weeks": "week"}.get(b, b),
        )
        if a in _TEMP and b in _TEMP:
            return ok(
                {
                    "expression": expr,
                    "kind": "conversion",
                    "result": round(_temp(v, a, b), 10),
                    "unit": b.upper(),
                }
            )
        if a not in _UNITS or b not in _UNITS or _UNITS[a][0] != _UNITS[b][0]:
            raise ToolError(
                "invalid_arguments",
                f"cannot convert {a} to {b} (supported: {', '.join(sorted(_UNITS))}, c/f/k)",
            )
        return ok(
            {
                "expression": expr,
                "kind": "conversion",
                "result": round(v * _UNITS[a][1] / _UNITS[b][1], 10),
                "unit": b,
            }
        )
    try:
        tree = ast.parse(expr.replace("^", "**"), mode="eval")
        value = _eval(tree)
    except ZeroDivisionError:
        raise ToolError("invalid_arguments", "division by zero") from None
    except (SyntaxError, ValueError, OverflowError, TypeError) as exc:
        raise ToolError("invalid_arguments", f"cannot evaluate: {type(exc).__name__}") from None
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)
    return ok({"expression": expr, "kind": "arithmetic", "result": value})


# -- table --------------------------------------------------------------------------


def _always(_s: Settings) -> str | None:
    return None


IMPLEMENTATIONS = {
    "workspace_list_files": (_fs_reason, _threaded(_list_files)),
    "workspace_file_metadata": (_fs_reason, _threaded(_metadata)),
    "workspace_compare_files": (_fs_reason, _threaded(_compare)),
    "workspace_create_file": (_fs_reason, _threaded(_create)),
    "workspace_backup_file": (_fs_reason, _threaded(_backup)),
    "code_search_symbols": (_fs_reason, _threaded(_symbols)),
    "code_apply_patch": (
        lambda s: _fs_reason(s) or (None if shutil.which("git") else "git is not installed"),
        _threaded(_apply_patch),
    ),
    "run_project_tests": (_command_reason("test"), _threaded(_run_command("test"))),
    "run_project_lint": (_command_reason("lint"), _threaded(_run_command("lint"))),
    "git_inspect": (_git_reason, _threaded(_git_inspect)),
    "json_yaml_validate": (_fs_reason, _threaded(_validate)),
    "csv_analyze": (_fs_reason, _threaded(_csv)),
    "calculate": (_always, _threaded(_calculate)),
}

#: Read tools whose results are the user's private data (gate later web egress like the fs_* reads).
PRIVATE_READS = {
    "workspace_list_files",
    "workspace_file_metadata",
    "workspace_compare_files",
    "code_search_symbols",
    "git_inspect",
    "json_yaml_validate",
    "csv_analyze",
}
#: Tools that may run longer than JARVIS_TOOL_TIMEOUT_SECONDS.
TIMEOUTS = {
    "run_project_tests": COMMAND_TIMEOUT_SECONDS,
    "run_project_lint": COMMAND_TIMEOUT_SECONDS,
}
