"""The Jarvis tool registry: one auditable table of every tool the model knows.

The tool names, descriptions and argument schemas are exactly the 21 of
``toolproto-1`` (``tool_schemas.json``, copied byte-for-byte from
``qwen_jarvis_phase3_4/tool_protocol/tool_schemas.json``, sha256 ``0d6a2498…``),
because those are the only tools the model was trained and evaluated on.
Phase 7 adds a separate, clearly marked set in ``workspace_tools.py`` at the
user's request; the model never saw those names, so its use of them is unmeasured.

Every tool has an implementation entry, and each one is in exactly one state:

* **available** - executes for real when its configuration is present;
* **unavailable** - a documented blocker (no backing service in NEXUS, a
  security policy, or missing configuration). The executor returns
  ``{"ok": false, "error": {"code": "unavailable", ...}}`` and never pretends.

Results always use the training envelope ``{"ok": true, "data": ...}`` /
``{"ok": false, "error": {"code", "message"}}``. Only the executor writes them;
the model never can.
"""

from __future__ import annotations

import asyncio
import fnmatch
import html
import ipaddress
import json
import logging
import os
import re
import shlex
import shutil
import socket
import subprocess
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

try:
    import httpx
except (
    ImportError
):  # dev-only dependency (below the requirements DEV marker): NEXUS must still boot
    httpx = None  # type: ignore[assignment]

from app.core.config import Settings
from app.jarvis.when import WhenError, parse_day, parse_instant, parse_window

__all__ = [
    "SCHEMAS_PATH",
    "ToolContext",
    "ToolSpec",
    "available_tools",
    "registry",
    "run_tool",
    "tool_definitions",
    "validate_arguments",
]

logger = logging.getLogger("app.jarvis.tools")

SCHEMAS_PATH = Path(__file__).with_name("tool_schemas.json")
_TIER_TEXT = {
    "read": "Read-only; may be called directly.",
    "write": "Changes state or has side effects; show the user the exact call and get their confirmation before calling.",
}
MAX_STRING = 200_000
MAX_LIST = 50


@dataclass(frozen=True)
class ToolSpec:
    """One trained tool: name, description, argument schema and side-effect tier."""

    name: str
    description: str
    parameters: dict[str, Any]
    side_effect: str

    @property
    def needs_confirmation(self) -> bool:
        """Anything that is not read-only waits for the user's explicit approval."""
        return self.side_effect != "read"


@dataclass
class ToolContext:
    """What a tool may use.

    ``nexus`` is an HTTP client into this same app that carries the caller's own
    token, so NEXUS tools are owner-scoped and permission-checked by the ordinary
    routes, never by the model.
    """

    settings: Settings
    tz: ZoneInfo
    now: datetime
    nexus: httpx.AsyncClient | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class ToolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def err(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message[:500]}}


# -- Registry ------------------------------------------------------------------


@lru_cache(maxsize=1)
def registry() -> dict[str, ToolSpec]:
    """The 21 trained tools plus the Phase 7 additions by name; refuses to load if any lacks an implementation entry."""
    raw = json.loads(SCHEMAS_PATH.read_text(encoding="utf-8"))
    specs = {}
    for t in raw["tools"]:
        specs[t["name"]] = ToolSpec(t["name"], t["description"], t["parameters"], t["side_effect"])
    for s in _added.SPECS:
        if s.name in specs:
            raise RuntimeError(f"added tool {s.name} collides with a trained tool")
        specs[s.name] = s
    missing = set(specs) ^ set(_IMPLEMENTATIONS)
    if missing:  # every trained tool has an implementation entry, and nothing else does
        raise RuntimeError(f"tool registry and implementations disagree: {sorted(missing)}")
    return specs


def availability(name: str, settings: Settings) -> str | None:
    """None when the tool can run; otherwise the reason it cannot."""
    return _IMPLEMENTATIONS[name][0](settings)


def available_tools(settings: Settings) -> list[ToolSpec]:
    """The tools that can run under these settings; only these are shown to the model."""
    return [s for n, s in registry().items() if availability(n, settings) is None]


def tool_definitions(specs: list[ToolSpec]) -> list[dict[str, Any]]:
    """The `tools` list sent to the model, in the exact training format."""
    return [
        {
            "type": "function",
            "function": {
                "name": s.name,
                "description": f"{s.description} {_TIER_TEXT['read' if s.side_effect == 'read' else 'write']}",
                "parameters": s.parameters,
            },
        }
        for s in specs
    ]


def validate_arguments(spec: ToolSpec, args: object) -> list[str]:
    """Strict validation against the tool's JSON schema subset (object of scalars)."""
    if not isinstance(args, dict):
        return ["arguments must be an object"]
    p = spec.parameters
    props = p.get("properties", {})
    errors = [f"missing required argument {k!r}" for k in p.get("required", []) if k not in args]
    for key, value in args.items():
        if key not in props:
            errors.append(f"unknown argument {key!r}")
            continue
        want = props[key].get("type")
        if want == "string":
            if not isinstance(value, str):
                errors.append(f"{key!r} must be a string")
            elif len(value) > MAX_STRING:
                errors.append(f"{key!r} is too long")
            elif key in p.get("required", []) and not value.strip():
                errors.append(f"{key!r} must not be empty")
        elif want == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                errors.append(f"{key!r} must be an integer")
            elif "minimum" in props[key] and value < props[key]["minimum"]:
                errors.append(f"{key!r} must be >= {props[key]['minimum']}")
        else:
            errors.append(f"{key!r} has an unsupported schema type {want!r}")
    return errors


async def run_tool(name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Execute one already-validated, already-authorised call. Never raises."""
    reason = availability(name, ctx.settings)
    if reason:
        return err("unavailable", reason)
    run = _IMPLEMENTATIONS[name][1]
    started = asyncio.get_running_loop().time()
    limit = max(ctx.settings.jarvis_tool_timeout_seconds, _added.TIMEOUTS.get(name, 0.0))
    try:
        async with asyncio.timeout(limit):
            result = await run(args, ctx)
    except ToolError as exc:
        result = err(exc.code, exc.message)
    except TimeoutError:
        result = err("timeout", f"{name} did not finish within {limit:g}s")
    except Exception as exc:  # a broken tool must report, not take the conversation down
        logger.exception("jarvis_tool_crashed", extra={"tool": name})
        result = err("error", f"{name} failed internally ({type(exc).__name__})")
    if not (
        isinstance(result, dict)
        and isinstance(result.get("ok"), bool)
        and (("data" in result) == result["ok"])
    ):
        result = err("invalid_result", f"{name} returned a malformed result")
    logger.info(
        "jarvis_tool_executed",
        extra={
            "tool": name,
            "ok": result["ok"],
            "code": None if result["ok"] else result["error"]["code"],
            "ms": round((asyncio.get_running_loop().time() - started) * 1000),
        },
    )
    return result


# -- Unavailable-by-design tools ----------------------------------------------


def _never(reason: str) -> Callable[[Settings], str]:
    return lambda _s: reason


async def _not_run(
    args: dict[str, Any], ctx: ToolContext
) -> dict[str, Any]:  # pragma: no cover - guarded by availability
    raise ToolError("unavailable", "this tool is not implemented")


_NO_MESSAGING = (
    "NEXUS has no messaging service to send through; app_send_message is not implemented"
)
_NO_SANDBOX = (
    "code_run_python would execute arbitrary model-written code; NEXUS has no isolated sandbox on this host "
    "(no Docker/WSL), so it is disabled by policy"
)
_NO_RAW_SQL = (
    "raw SQL against the NEXUS database would bypass per-user ownership scoping; db_query/db_execute are disabled "
    "by policy - NEXUS data is reachable only through the app_* tools"
)


# -- Filesystem (sandboxed to JARVIS_WORKSPACE_DIR) ------------------------------

_SECRET_NAME = re.compile(
    r"(^\.env(\..*)?$|\.pem$|\.key$|^id_(rsa|ed25519|ecdsa)|credential|secret|\.p12$|\.pfx$|"
    r"^\.?_?netrc$|\.pgpass$|\.tfvars$|^\.npmrc$|^\.pypirc$|^\.git-credentials$|\.kdbx$|^\.ssh$|^\.aws$|"
    r"^\.gnupg$|^token|password|passwd|api[_-]?key|^private)",
    re.I,
)
#: Windows device names: opening one reads a device, not a file in the workspace.
_DEVICE = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.I)


def _fs_reason(s: Settings) -> str | None:
    if not s.jarvis_workspace_dir.strip():
        return "no workspace is configured (JARVIS_WORKSPACE_DIR), so file tools are off"
    if not Path(s.jarvis_workspace_dir).is_dir():
        return "the configured workspace directory does not exist"
    return None


def _root(ctx: ToolContext) -> Path:
    return Path(ctx.settings.jarvis_workspace_dir).resolve()


def _inside(ctx: ToolContext, raw: str) -> Path:
    """Resolve a model-supplied path inside the workspace or refuse."""
    rel = raw.strip().replace("\\", "/")
    if not rel or rel.startswith("/") or re.match(r"^[a-zA-Z]:", rel) or rel.startswith("~"):
        raise ToolError("permission_denied", "paths must be relative to the workspace")
    if any(part == ".." for part in rel.split("/")):
        raise ToolError("permission_denied", "'..' is not allowed in paths")
    if ":" in rel or any(_DEVICE.match(part) for part in rel.split("/")):
        raise ToolError(
            "permission_denied", "streams, drive-relative and device paths are not allowed"
        )
    root = _root(ctx)
    full = (root / rel).resolve()
    if full != root and not full.is_relative_to(root):
        raise ToolError("permission_denied", "path leaves the workspace")
    if any(_SECRET_NAME.search(part) for part in full.relative_to(root).parts):
        raise ToolError("permission_denied", "credential-like files are not accessible to NEXO")
    return full


def _rel(ctx: ToolContext, p: Path) -> str:
    return p.relative_to(_root(ctx)).as_posix()


def _fs_read(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    p = _inside(ctx, args["path"])
    if not p.is_file():
        raise ToolError("not_found", f"{args['path']} does not exist")
    limit = ctx.settings.jarvis_web_max_bytes
    with p.open("rb") as fh:
        raw = fh.read(limit + 1)
    if b"\x00" in raw[:4096]:
        raise ToolError("unsupported", f"{args['path']} is a binary file")
    return ok(
        {
            "path": _rel(ctx, p),
            "content": raw[:limit].decode("utf-8", errors="replace"),
            "truncated": len(raw) > limit,
        }
    )


def _fs_search(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    base = _inside(ctx, args.get("path") or ".")
    if not base.is_dir():
        raise ToolError("not_found", f"{args.get('path')} is not a directory")
    q = args["query"].lower()
    root = _root(ctx)
    hits, truncated = [], False
    for scanned, p in enumerate(_walk(base, root), 1):
        if scanned > 2000:
            truncated = True
            break
        if q in p.name.lower():
            hits.append({"path": _rel(ctx, p), "match": "name"})
        elif p.stat().st_size <= 1_000_000:
            for n, line in enumerate(
                p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
            ):
                if q in line.lower():
                    hits.append({"path": _rel(ctx, p), "line": n, "text": line.strip()[:200]})
                    break
        if len(hits) >= MAX_LIST:
            truncated = True
            break
    return ok({"query": args["query"], "results": hits, "truncated": truncated})


def _walk(base: Path, root: Path):
    """Files under base, lazily and in a stable order; secret names and symlinks out of the workspace are skipped."""
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if not _SECRET_NAME.search(d))
        for f in sorted(filenames):
            p = Path(dirpath) / f
            if _SECRET_NAME.search(f) or not p.resolve().is_relative_to(root):
                continue
            yield p


def _fs_write(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    p = _inside(ctx, args["path"])
    if p.is_dir():
        raise ToolError("conflict", f"{args['path']} is a directory")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(args["content"], encoding="utf-8", newline="")
    os.replace(tmp, p)  # atomic: a crash never leaves half a file
    return ok({"path": _rel(ctx, p), "bytes": p.stat().st_size})


def _fs_move(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    src, dst = _inside(ctx, args["source"]), _inside(ctx, args["destination"])
    if not src.exists():
        raise ToolError("not_found", f"{args['source']} does not exist")
    if dst.exists():
        raise ToolError("conflict", f"{args['destination']} already exists; nothing was moved")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return ok({"source": args["source"], "destination": _rel(ctx, dst)})


def _fs_delete(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    p = _inside(ctx, args["path"])
    if p == _root(ctx):
        raise ToolError("permission_denied", "the workspace root cannot be deleted")
    if args.get("match"):
        if not p.is_dir():
            raise ToolError("not_found", f"{args['path']} is not a directory")
        targets = [
            x
            for x in sorted(p.iterdir())
            if x.is_file()
            and fnmatch.fnmatch(x.name, args["match"])
            and not _SECRET_NAME.search(
                x.name
            )  # credential files are never touched, even by a glob
        ]
        if len(targets) > MAX_LIST:
            raise ToolError(
                "conflict",
                f"{len(targets)} files match; refusing to delete more than {MAX_LIST} at once",
            )
    else:
        if p.is_dir():
            raise ToolError(
                "conflict", f"{args['path']} is a directory; fs_delete_file deletes files only"
            )
        if not p.is_file():
            raise ToolError("not_found", f"{args['path']} does not exist")
        targets = [p]
    for t in targets:
        t.unlink()
    return ok({"deleted": [_rel(ctx, t) for t in targets]})


# -- Shell (argv only, allowlisted programs, workspace cwd) ---------------------

_SHELL_META = re.compile(r"[|&;<>`$\n\r]|\$\(")
_SAFE_ENV = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "LANG",
    "PATHEXT",
    "COMSPEC",
)


#: Programs that execute code given as arguments. An allowlist entry for one of these would
#: turn shell_run into arbitrary code execution, so they are refused even when listed.
_INTERPRETERS = {
    "python",
    "python3",
    "py",
    "pythonw",
    "node",
    "deno",
    "bun",
    "ruby",
    "perl",
    "php",
    "lua",
    "java",
    "pwsh",
    "powershell",
    "cmd",
    "bash",
    "sh",
    "zsh",
    "fish",
    "wsl",
    "wscript",
    "cscript",
    "mshta",
    "rundll32",
    "regsvr32",
    "npx",
    "npm",
    "pnpm",
    "yarn",
    "pip",
    "uv",
    "env",
    "xargs",
    "find",
}
#: git options that run arbitrary commands or leave the working directory.
_GIT_DENY = re.compile(
    r"^(-c|--config-env.*|--exec-path.*|-C|--git-dir.*|--work-tree.*|--upload-pack.*|"
    r"--receive-pack.*|--ext-diff|--textconv|--output.*)$"
)


def _shell_allowlist(s: Settings) -> set[str]:
    return {x.strip().lower() for x in s.jarvis_shell_allowlist.split(",") if x.strip()}


def _shell_reason(s: Settings) -> str | None:
    if not _shell_allowlist(s):
        return "no programs are allowlisted (JARVIS_SHELL_ALLOWLIST), so shell_run is off"
    return (
        _fs_reason(s)
        and "shell_run needs a workspace directory (JARVIS_WORKSPACE_DIR) as its working directory"
    )


async def _shell_run(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    cmd = args["command"]
    if _SHELL_META.search(cmd):
        raise ToolError(
            "invalid_arguments",
            "pipes, redirection, chaining and substitution are not supported; run one program",
        )
    try:
        argv = shlex.split(cmd, posix=True)
    except ValueError as exc:
        raise ToolError("invalid_arguments", f"could not parse the command: {exc}") from exc
    if not argv:
        raise ToolError("invalid_arguments", "empty command")
    prog = Path(argv[0]).name.lower().removesuffix(".exe")
    if prog not in _shell_allowlist(ctx.settings) or Path(argv[0]).name != argv[0]:
        raise ToolError("permission_denied", f"{argv[0]!r} is not an allowlisted program")
    if prog in _INTERPRETERS:
        raise ToolError("permission_denied", f"{prog} runs arbitrary code and is never allowed")
    if prog == "git" and any(_GIT_DENY.match(a) or a.startswith("--config") for a in argv[1:]):
        raise ToolError(
            "permission_denied", "git options that run commands or change directory are not allowed"
        )
    exe = shutil.which(argv[0])
    if not exe:
        raise ToolError("not_found", f"{argv[0]!r} is not installed")
    cwd = _inside(ctx, args.get("cwd") or ".")
    if not cwd.is_dir():
        raise ToolError("not_found", "the working directory does not exist")
    return await _run_argv([exe, *argv[1:]], cwd, ctx, prog)


async def _run_argv(argv: list[str], cwd: Path, ctx: ToolContext, prog: str) -> dict[str, Any]:
    """Run one program (no shell, minimal environment, timeout) and return its capped output."""
    env = {
        k: os.environ[k] for k in _SAFE_ENV if k in os.environ
    }  # no secrets from the backend's environment
    timeout = max(1.0, ctx.settings.jarvis_tool_timeout_seconds - 1)
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            timeout=timeout,
            shell=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError("timeout", f"{prog} was stopped after {timeout:g}s") from exc
    out = proc.stdout.decode("utf-8", errors="replace")
    errt = proc.stderr.decode("utf-8", errors="replace")
    data = {
        "exit_code": proc.returncode,
        "stdout": out[-20_000:],
        "stderr": errt[-5_000:],
        "truncated": len(out) > 20_000 or len(errt) > 5_000,
    }
    if proc.returncode != 0:
        raise ToolError(
            "nonzero_exit",
            f"exit code {proc.returncode}: {errt.strip()[-400:] or out.strip()[-400:]}",
        )
    return ok(data)


# -- Web (public hosts only, size-capped) ---------------------------------------


def _web_reason(_s: Settings) -> str | None:
    return (
        None
        if httpx is not None
        else "httpx is not installed in this environment (it is a dev-only dependency), so NEXO cannot make HTTP calls"
    )


WIKIPEDIA_SEARCH_URL = "https://en.wikipedia.org/w/api.php"


def _web_search_reason(s: Settings) -> str | None:
    if httpx is None:
        return _web_reason(s)
    provider = s.jarvis_web_search_provider.strip().lower()
    if provider == "wikipedia":
        return None
    if provider != "searxng":
        return f"unknown search provider {provider!r} (JARVIS_WEB_SEARCH_PROVIDER: searxng or wikipedia)"
    return (
        None
        if s.jarvis_web_search_url.strip()
        else "no search provider is configured (JARVIS_WEB_SEARCH_PROVIDER=wikipedia, or a SearXNG JARVIS_WEB_SEARCH_URL)"
    )


async def _check_host(url: str, ctx: ToolContext) -> str | None:
    """Validate ``url`` and return the public IP to connect to (``None`` when private hosts are allowed).

    The caller connects to exactly this IP, so a DNS answer that changes after the check
    (DNS rebinding) cannot redirect the request to a private address.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ToolError("invalid_arguments", "only absolute http(s) URLs are supported")
    if parts.username or parts.password:
        raise ToolError("invalid_arguments", "URLs with credentials are not allowed")
    if ctx.settings.jarvis_web_allow_private_hosts:
        return None
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo, parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP
        )
    except socket.gaierror as exc:
        raise ToolError("connection_failed", f"could not resolve {parts.hostname}") from exc
    if not infos:
        raise ToolError("connection_failed", f"could not resolve {parts.hostname}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise ToolError(
                "permission_denied", f"{parts.hostname} resolves to a non-public address"
            )
    return str(ipaddress.ip_address(infos[0][4][0]))


def _pinned(url: str, ip: str | None) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Request target, headers and extensions that connect to ``ip`` while keeping Host and TLS name checks."""
    headers = {"User-Agent": "NEXUS-NEXO/0.7"}
    if ip is None:
        return url, headers, {}
    parts = urlsplit(url)
    host = f"[{ip}]" if ":" in ip else ip
    headers["Host"] = parts.netloc
    # httpcore uses sni_hostname for both SNI and certificate hostname verification.
    ext = {"sni_hostname": parts.hostname} if parts.scheme == "https" else {}
    return (
        urlunsplit(parts._replace(netloc=f"{host}:{parts.port}" if parts.port else host)),
        headers,
        ext,
    )


async def _http(method: str, url: str, ctx: ToolContext, body: str | None = None) -> dict[str, Any]:
    limit = ctx.settings.jarvis_web_max_bytes
    async with httpx.AsyncClient(
        timeout=ctx.settings.jarvis_tool_timeout_seconds, follow_redirects=False
    ) as client:
        for _ in range(4):
            target, headers, ext = _pinned(url, await _check_host(url, ctx))
            try:
                async with client.stream(
                    method, target, content=body, headers=headers, extensions=ext
                ) as r:
                    if r.is_redirect and method == "GET":
                        url = urljoin(url, r.headers.get("location", ""))
                        continue
                    buf = bytearray()
                    async for chunk in r.aiter_bytes():
                        buf += chunk
                        if len(buf) > limit:
                            break
                    text = bytes(buf[:limit]).decode(r.encoding or "utf-8", errors="replace")
                    if "html" in r.headers.get("content-type", ""):
                        text = re.sub(
                            r"\s+", " ", re.sub(r"(?s)<(script|style).*?</\1>|<[^>]+>", " ", text)
                        ).strip()
                    data = {
                        "url": url,
                        "status": r.status_code,
                        "content_type": r.headers.get("content-type", ""),
                        "text": text,
                        "truncated": len(buf) > limit,
                    }
                    if r.status_code >= 400:
                        raise ToolError(f"http_{r.status_code}", f"{url} answered {r.status_code}")
                    return ok(data)
            except httpx.TimeoutException as exc:
                raise ToolError("timeout", f"{url} did not answer in time") from exc
            except httpx.HTTPError as exc:
                raise ToolError(
                    "connection_failed", f"could not reach {url}: {type(exc).__name__}"
                ) from exc
    raise ToolError("error", "too many redirects")


async def _web_fetch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return await _http("GET", args["url"], ctx)


async def _http_request(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    method = args["method"].upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
        raise ToolError("invalid_arguments", f"unsupported method {args['method']!r}")
    return await _http(method, args["url"], ctx, args.get("body"))


async def _web_search(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    wikipedia = ctx.settings.jarvis_web_search_provider.strip().lower() == "wikipedia"
    url = ctx.settings.jarvis_web_search_url.strip() or (WIKIPEDIA_SEARCH_URL if wikipedia else "")
    params = (
        {
            "action": "query",
            "list": "search",
            "srsearch": args["query"],
            "srlimit": 10,
            "format": "json",
            "utf8": 1,
        }
        if wikipedia
        else {"q": args["query"], "format": "json"}
    )
    # Wikimedia's API policy asks every client to identify itself.
    headers = {"User-Agent": f"NEXUS-NEXO/0.7 ({ctx.settings.jarvis_web_search_contact})"}
    async with httpx.AsyncClient(timeout=ctx.settings.jarvis_tool_timeout_seconds) as client:
        try:
            r = await client.get(url, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise ToolError("timeout", "the search provider did not answer in time") from exc
        except httpx.HTTPError as exc:
            raise ToolError(
                "connection_failed", f"search provider unreachable: {type(exc).__name__}"
            ) from exc
    if r.status_code != 200:
        raise ToolError(f"http_{r.status_code}", "search provider error")
    try:
        body = r.json()
        results = (body.get("query") or {}).get("search") if wikipedia else body.get("results")
    except (ValueError, AttributeError) as exc:
        raise ToolError("invalid_result", "search provider returned non-JSON") from exc
    if not isinstance(results, list):
        raise ToolError("invalid_result", "search provider returned no result list")
    if wikipedia:
        rows = [
            {
                "title": str(x["title"])[:200],
                "url": "https://en.wikipedia.org/wiki/" + quote(str(x["title"]).replace(" ", "_")),
                "snippet": html.unescape(re.sub(r"<[^>]+>", "", str(x.get("snippet", ""))))[:400],
            }
            for x in results[:10]
            if isinstance(x, dict) and isinstance(x.get("title"), str) and x["title"].strip()
        ]
    else:
        rows = [
            {
                "title": str(x.get("title", ""))[:200],
                "url": str(x.get("url", "")),
                "snippet": str(x.get("content", ""))[:400],
            }
            for x in results[:10]
            if isinstance(x, dict) and str(x.get("url", "")).startswith(("http://", "https://"))
        ]
    return ok(
        {
            "query": args["query"],
            "provider": "wikipedia" if wikipedia else "searxng",
            # Told to the model and shown to the user, so a Wikipedia hit list is never presented as "the web".
            "coverage": "Wikipedia articles only, not the general web"
            if wikipedia
            else "the configured SearXNG instance",
            "results": rows,
            "truncated": len(results) > 10,
        }
    )


# -- Database metadata -----------------------------------------------------------


#: Account and security tables: their layout is not something a chat needs.
_PRIVATE_TABLES = {"users", "sessions", "password_reset_tokens", "audit_logs"}


async def _db_describe(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from sqlalchemy import text

    from app.db.session import get_engine

    table = args["table"].strip()
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", table):
        raise ToolError("invalid_arguments", "table must be a plain lower-case identifier")
    if table in _PRIVATE_TABLES or table.startswith(("alembic", "pg_")):
        raise ToolError("permission_denied", f"the structure of {table!r} is not exposed")
    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :t ORDER BY ordinal_position"
                ),
                {"t": table},
            )
        ).all()
    if not rows:
        raise ToolError("not_found", f"no table named {table!r}")
    return ok(
        {
            "table": table,
            "columns": [{"name": r[0], "type": r[1], "nullable": r[2] == "YES"} for r in rows],
        }
    )


# -- NEXUS adapters (through the app's own authenticated routes) ----------------


def _nexus_reason(_s: Settings) -> str | None:
    return (
        None
        if httpx is not None
        else "httpx is not installed in this environment (it is a dev-only dependency), so NEXO cannot make HTTP calls"
    )


async def _api(ctx: ToolContext, method: str, path: str, **kw: Any) -> Any:
    if ctx.nexus is None:
        raise ToolError("unavailable", "NEXUS is not reachable from this request")
    try:
        r = await ctx.nexus.request(method, f"/api/v1{path}", **kw)
    except httpx.HTTPError as exc:
        raise ToolError("unavailable", f"NEXUS request failed: {type(exc).__name__}") from exc
    if r.status_code < 300:
        return r.json() if r.content else None
    try:
        message = r.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        message = r.reason_phrase
    code = {
        401: "permission_denied",
        403: "permission_denied",
        404: "not_found",
        409: "conflict",
        422: "invalid_arguments",
        400: "invalid_arguments",
        429: "rate_limited",
        503: "unavailable",
    }.get(r.status_code, f"http_{r.status_code}")
    raise ToolError(code, message)


def _when(fn: Callable[..., Any], value: str, ctx: ToolContext) -> Any:
    try:
        return fn(value, ctx.tz, ctx.now)
    except WhenError as exc:
        raise ToolError("invalid_arguments", str(exc)) from exc


_OPEN = ("todo", "in_progress", "blocked")


def _task_view(t: dict[str, Any]) -> dict[str, Any]:
    return {k: t.get(k) for k in ("id", "title", "status", "priority", "due_date", "project_id")}


async def _list_tasks(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    f = (args.get("filter") or "").strip().lower()
    today = ctx.now.astimezone(ctx.tz).date()
    queries: list[dict[str, Any]]
    status = next(
        (
            s
            for s in ("todo", "in_progress", "blocked", "completed", "cancelled")
            if f.replace(" ", "_") == s
        ),
        None,
    )
    if f in ("", "all"):
        queries = [{}]
    elif f in ("open", "pending", "outstanding", "active", "remaining"):
        queries = [{"status": s} for s in _OPEN]
    elif f == "overdue":
        queries = [
            {"status": s, "due_before": (today - timedelta(days=1)).isoformat()} for s in _OPEN
        ]
    elif f in ("today", "due today"):
        queries = [
            {"status": s, "due_before": today.isoformat(), "due_after": today.isoformat()}
            for s in _OPEN
        ]
    elif f in ("done", "finished") or status:
        queries = [{"status": status or "completed"}]
    elif m := re.fullmatch(r"(low|medium|high|critical|urgent)( priority)?", f):
        queries = [{"priority": "critical" if m.group(1) == "urgent" else m.group(1)}]
    else:
        queries = [{"search": args["filter"].strip()}]
    items, total = [], 0
    for q in queries:
        page = await _api(ctx, "GET", "/tasks", params={**q, "limit": MAX_LIST})
        items += page["items"]
        total += page["meta"]["total"]
    return ok(
        {
            "filter": args.get("filter", ""),
            "tasks": [_task_view(t) for t in items[:MAX_LIST]],
            "total": total,
            "truncated": total > min(len(items), MAX_LIST),
        }
    )


async def _resolve(
    ctx: ToolContext, kind: str, ref: str, rows: list[dict[str, Any]], label: str = "title"
) -> dict[str, Any]:
    ref = ref.strip()
    try:
        uid = str(uuid.UUID(ref))
        found = [r for r in rows if str(r.get("id")) == uid]
    except ValueError:
        exact = [r for r in rows if str(r.get(label, "")).strip().lower() == ref.lower()]
        found = exact or [r for r in rows if ref.lower() in str(r.get(label, "")).lower()]
    if not found:
        raise ToolError("not_found", f"no {kind} matches {ref!r}")
    if len(found) > 1:
        names = ", ".join(repr(r.get(label)) for r in found[:5])
        raise ToolError(
            "ambiguous", f"{len(found)} {kind}s match {ref!r} ({names}); ask the user which one"
        )
    return found[0]


async def _create_task(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    projects = (await _api(ctx, "GET", "/projects", params={"limit": 100}))["items"]
    live = [p for p in projects if p.get("status") in ("planned", "active", "on_hold")]
    if len(live) != 1:
        raise ToolError(
            "conflict",
            "a NEXUS task must belong to a project and the tool has no project argument; "
            f"the user has {len(live)} open projects, so the task was not created - create it in the Projects view",
        )
    body: dict[str, Any] = {"title": args["title"], "project_id": live[0]["id"]}
    if args.get("due"):
        body["due_date"] = _when(parse_day, args["due"], ctx).isoformat()
    task = await _api(ctx, "POST", "/tasks", json=body)
    return ok({**_task_view(task), "project": live[0].get("name")})


_ACTIONS = {
    "complete": "complete",
    "completed": "complete",
    "done": "complete",
    "finish": "complete",
    "start": "start",
    "in_progress": "start",
    "started": "start",
    "reopen": "reopen",
    "todo": "reopen",
    "cancel": "cancel",
    "cancelled": "cancel",
    "block": "block",
    "blocked": "block",
}


async def _update_task(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    wanted = {
        k: (args.get(k) or "").strip().lower().replace(" ", "_") for k in ("status", "action")
    }
    actions = {_ACTIONS.get(v) for v in wanted.values() if v} - {None}
    unknown = [v for v in wanted.values() if v and v not in _ACTIONS]
    if unknown or len(actions) > 1:
        raise ToolError(
            "invalid_arguments",
            f"status/action must be one of complete, start, reopen, cancel, block; got {unknown or sorted(actions)}",
        )
    patch: dict[str, Any] = {}
    if args.get("title"):
        patch["title"] = args["title"]
    if args.get("due"):
        patch["due_date"] = _when(parse_day, args["due"], ctx).isoformat()
    if not patch and not actions:
        raise ToolError("invalid_arguments", "nothing to change: give title, due, status or action")
    rows = (await _api(ctx, "GET", "/tasks", params={"limit": MAX_LIST, "search": args["task"]}))[
        "items"
    ]
    try:
        uuid.UUID(args["task"].strip())
        rows = [await _api(ctx, "GET", f"/tasks/{args['task'].strip()}")]
    except ValueError:
        pass
    task = await _resolve(ctx, "task", args["task"], rows)
    if patch:
        task = await _api(ctx, "PATCH", f"/tasks/{task['id']}", json=patch)
    if actions:
        task = await _api(ctx, "POST", f"/tasks/{task['id']}/{actions.pop()}")
    return ok(_task_view(task))


def _event_view(e: dict[str, Any]) -> dict[str, Any]:
    return {k: e.get(k) for k in ("id", "title", "starts_at", "ends_at", "all_day", "location")}


async def _read_calendar(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    start, end = _when(parse_window, args["range"], ctx)
    page = await _api(
        ctx,
        "GET",
        "/calendar",
        params={"from": start.isoformat(), "to": end.isoformat(), "limit": 100},
    )
    return ok(
        {
            "range": args["range"],
            "from": start.isoformat(),
            "to": end.isoformat(),
            "events": [_event_view(e) for e in page["items"]],
            "truncated": page["meta"]["total"] > len(page["items"]),
        }
    )


async def _create_event(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    start = _when(parse_instant, args["start"], ctx)
    end = start + timedelta(minutes=args.get("minutes", 60))
    ev = await _api(
        ctx,
        "POST",
        "/calendar",
        json={"title": args["title"], "starts_at": start.isoformat(), "ends_at": end.isoformat()},
    )
    return ok(_event_view(ev))


async def _update_event(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    if not any(args.get(k) for k in ("title", "start", "minutes")):
        raise ToolError("invalid_arguments", "nothing to change: give title, start or minutes")
    window = {
        "from": (ctx.now - timedelta(days=30)).isoformat(),
        "to": (ctx.now + timedelta(days=365)).isoformat(),
        "limit": 100,
    }
    ev = await _resolve(
        ctx, "event", args["event"], (await _api(ctx, "GET", "/calendar", params=window))["items"]
    )
    patch: dict[str, Any] = {}
    if args.get("title"):
        patch["title"] = args["title"]
    old_start, old_end = (
        datetime.fromisoformat(ev["starts_at"]),
        datetime.fromisoformat(ev["ends_at"]),
    )
    if args.get("start") or args.get("minutes"):
        start = _when(parse_instant, args["start"], ctx) if args.get("start") else old_start
        minutes = args.get("minutes") or int((old_end - old_start).total_seconds() // 60)
        patch.update(
            starts_at=start.isoformat(), ends_at=(start + timedelta(minutes=minutes)).isoformat()
        )
    return ok(_event_view(await _api(ctx, "PATCH", f"/calendar/{ev['id']}", json=patch)))


async def _update_goal(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    m = re.fullmatch(r"\s*(\d{1,3})\s*%?\s*", args["progress"])
    if not m or int(m.group(1)) > 100:
        raise ToolError("invalid_arguments", "progress must be a percentage from 0 to 100")
    goals = (await _api(ctx, "GET", "/learning/goals", params={"limit": 100}))["items"]
    goal = await _resolve(ctx, "goal", args["goal"], goals)
    g = await _api(
        ctx, "PATCH", f"/learning/goals/{goal['id']}", json={"progress": int(m.group(1))}
    )
    return ok({k: g.get(k) for k in ("id", "title", "status", "progress", "target_date")})


def _threaded(fn: Callable[[dict[str, Any], ToolContext], dict[str, Any]]) -> Runner:
    """Run a blocking tool on a worker thread so a large workspace cannot stall the event loop."""

    async def run(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return await asyncio.to_thread(fn, args, ctx)

    return run


# -- The table -------------------------------------------------------------------

Availability = Callable[[Settings], str | None]
Runner = Callable[[dict[str, Any], ToolContext], Awaitable[dict[str, Any]]]

_IMPLEMENTATIONS: dict[str, tuple[Availability, Runner]] = {
    "app_create_event": (_nexus_reason, _create_event),
    "app_create_task": (_nexus_reason, _create_task),
    "app_list_tasks": (_nexus_reason, _list_tasks),
    "app_read_calendar": (_nexus_reason, _read_calendar),
    "app_send_message": (_never(_NO_MESSAGING), _not_run),
    "app_update_event": (_nexus_reason, _update_event),
    "app_update_goal": (_nexus_reason, _update_goal),
    "app_update_task": (_nexus_reason, _update_task),
    "code_run_python": (_never(_NO_SANDBOX), _not_run),
    "db_describe": (_nexus_reason, _db_describe),
    "db_execute": (_never(_NO_RAW_SQL), _not_run),
    "db_query": (_never(_NO_RAW_SQL), _not_run),
    "fs_delete_file": (_fs_reason, _threaded(_fs_delete)),
    "fs_move_file": (_fs_reason, _threaded(_fs_move)),
    "fs_read_file": (_fs_reason, _threaded(_fs_read)),
    "fs_search": (_fs_reason, _threaded(_fs_search)),
    "fs_write_file": (_fs_reason, _threaded(_fs_write)),
    "http_request": (_web_reason, _http_request),
    "shell_run": (_shell_reason, _shell_run),
    "web_fetch": (_web_reason, _web_fetch),
    "web_search": (_web_search_reason, _web_search),
}

# Phase 7 additions the model was not trained on; imported last because they reuse the helpers above.
from app.jarvis import workspace_tools as _added  # noqa: E402

_IMPLEMENTATIONS.update(_added.IMPLEMENTATIONS)
