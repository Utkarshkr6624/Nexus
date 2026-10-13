"""The Jarvis tool registry and the non-NEXUS tools, executed for real.

Filesystem tools run against a real temporary workspace, ``shell_run`` starts
real processes, and ``web_fetch`` / ``http_request`` make real HTTP requests to
a real server on 127.0.0.1 (``JARVIS_WEB_ALLOW_PRIVATE_HOSTS`` is switched on
only for those). ``web_search`` is exercised against a local SearXNG-shaped
stub: that proves the client contract only, **not** any real search provider,
because none is configured on this machine.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

import pytest

from app.core.config import get_settings
from app.jarvis import tools as T  # noqa: N812 - short alias reads better in a table of cases
from app.jarvis.when import WhenError, parse_day, parse_instant, parse_window

TOOLPROTO = {
    "app_create_event",
    "app_create_task",
    "app_list_tasks",
    "app_read_calendar",
    "app_send_message",
    "app_update_event",
    "app_update_goal",
    "app_update_task",
    "code_run_python",
    "db_describe",
    "db_execute",
    "db_query",
    "fs_delete_file",
    "fs_move_file",
    "fs_read_file",
    "fs_search",
    "fs_write_file",
    "http_request",
    "shell_run",
    "web_fetch",
    "web_search",
}
NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)  # a Monday
TZ = ZoneInfo("Asia/Kolkata")


def ctx(**overrides) -> T.ToolContext:
    return T.ToolContext(settings=get_settings().model_copy(update=overrides), tz=TZ, now=NOW)


def run(name, args, c):
    return asyncio.run(T.run_tool(name, args, c))


# -- Registry -------------------------------------------------------------------


ADDED = {
    "workspace_list_files",
    "workspace_file_metadata",
    "workspace_compare_files",
    "workspace_create_file",
    "workspace_backup_file",
    "code_search_symbols",
    "code_apply_patch",
    "run_project_tests",
    "run_project_lint",
    "git_inspect",
    "json_yaml_validate",
    "csv_analyze",
    "calculate",
}


def test_registry_is_the_21_trained_tools_plus_the_marked_additions():
    assert set(T.registry()) == TOOLPROTO | ADDED
    assert not TOOLPROTO & ADDED


def test_schema_file_is_the_trained_protocol_copy():
    import hashlib

    assert (  # line endings normalized: git core.autocrlf rewrites the checkout to CRLF on Windows
        hashlib.sha256(T.SCHEMAS_PATH.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        == "0d6a24986f45b4f2964303cdf2d7bea50a39ba8a93851e04a5d85186fc80ec35"
    )


@pytest.mark.parametrize("name", sorted(TOOLPROTO | ADDED))
def test_every_tool_schema_is_a_closed_object_of_scalars(name):
    p = T.registry()[name].parameters
    assert p["type"] == "object" and p["additionalProperties"] is False
    assert set(p["required"]) <= set(p["properties"])
    assert all(v["type"] in ("string", "integer") for v in p["properties"].values())


def test_tool_definitions_carry_the_trained_tier_sentence():
    d = {
        x["function"]["name"]: x["function"]["description"]
        for x in T.tool_definitions(list(T.registry().values()))
    }
    assert d["web_fetch"].endswith("Read-only; may be called directly.")
    assert d["fs_write_file"].endswith("get their confirmation before calling.")


@pytest.mark.parametrize(
    "args,expect",
    [
        ({"path": "a", "content": "b"}, []),
        ({"path": "a"}, ["missing required argument 'content'"]),
        ({"path": "a", "content": "b", "mode": "w"}, ["unknown argument 'mode'"]),
        ({"path": 1, "content": "b"}, ["'path' must be a string"]),
        ({"path": " ", "content": "b"}, ["'path' must not be empty"]),
        ("path=a", ["arguments must be an object"]),
    ],
)
def test_argument_validation(args, expect):
    assert T.validate_arguments(T.registry()["fs_write_file"], args) == expect


def test_integer_arguments_reject_bools_and_minimum():
    spec = T.registry()["app_create_event"]
    assert T.validate_arguments(spec, {"title": "x", "start": "today at 9", "minutes": True}) == [
        "'minutes' must be an integer"
    ]
    assert T.validate_arguments(spec, {"title": "x", "start": "today at 9", "minutes": 0}) == [
        "'minutes' must be >= 1"
    ]


@pytest.mark.parametrize("name", ["app_send_message", "code_run_python", "db_query", "db_execute"])
def test_blocked_tools_are_unavailable_with_a_reason_and_never_run(name):
    c = ctx()
    assert T.availability(name, c.settings)
    args = dict.fromkeys(T.registry()[name].parameters["required"], "x")
    r = run(name, args, c)
    assert r == {
        "ok": False,
        "error": {"code": "unavailable", "message": T.availability(name, c.settings)},
    }


def test_config_gated_tools_are_off_by_default():
    s = get_settings().model_copy(
        update={
            "jarvis_workspace_dir": "",
            "jarvis_shell_allowlist": "",
            "jarvis_web_search_url": "",
        }
    )
    off = {n for n in TOOLPROTO if T.availability(n, s)}
    assert {
        "fs_read_file",
        "fs_write_file",
        "fs_search",
        "fs_move_file",
        "fs_delete_file",
        "shell_run",
        "web_search",
    } <= off


# -- Executor failure handling ----------------------------------------------------


def test_timeout_crash_and_malformed_results_are_reported(monkeypatch):
    async def slow(args, c):
        await asyncio.sleep(5)

    async def boom(args, c):
        raise RuntimeError("secret detail")

    async def junk(args, c):
        return {"ok": True}

    for fn, code in ((slow, "timeout"), (boom, "error"), (junk, "invalid_result")):
        monkeypatch.setitem(T._IMPLEMENTATIONS, "web_fetch", (lambda s: None, fn))
        r = run("web_fetch", {"url": "http://x"}, ctx(jarvis_tool_timeout_seconds=0.2))
        assert r["ok"] is False and r["error"]["code"] == code
        assert "secret detail" not in r["error"]["message"]


# -- Filesystem (real files) -----------------------------------------------------


@pytest.fixture
def ws(tmp_path) -> T.ToolContext:
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "todo.md").write_text("buy milk\ncall Ada\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    return ctx(jarvis_workspace_dir=str(tmp_path))


def test_fs_write_read_search_move_delete_round_trip(ws, tmp_path):
    assert run("fs_write_file", {"path": "out/report.md", "content": "Findings: ok"}, ws)["ok"]
    assert (tmp_path / "out" / "report.md").read_text(encoding="utf-8") == "Findings: ok"
    r = run("fs_read_file", {"path": "out/report.md"}, ws)
    assert r == {
        "ok": True,
        "data": {"path": "out/report.md", "content": "Findings: ok", "truncated": False},
    }
    hits = run("fs_search", {"query": "ada"}, ws)["data"]["results"]
    assert hits == [{"path": "notes/todo.md", "line": 2, "text": "call Ada"}]
    assert run("fs_move_file", {"source": "out/report.md", "destination": "archive/report.md"}, ws)[
        "ok"
    ]
    assert run("fs_delete_file", {"path": "archive/report.md"}, ws)["data"]["deleted"] == [
        "archive/report.md"
    ]
    assert not (tmp_path / "archive" / "report.md").exists()


@pytest.mark.parametrize(
    "path",
    [
        "../escape.txt",
        "notes/../../x",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "~/x",
        ".env",
        "notes/id_rsa",
    ],
)
def test_fs_refuses_traversal_absolute_and_secret_paths(ws, path, tmp_path):
    for name, args in (
        ("fs_read_file", {"path": path}),
        ("fs_write_file", {"path": path, "content": "x"}),
    ):
        r = run(name, args, ws)
        assert r["ok"] is False and r["error"]["code"] == "permission_denied", (name, path, r)
    assert not (tmp_path.parent / "escape.txt").exists()


def test_fs_symlink_out_of_the_workspace_is_refused(ws, tmp_path):
    outside = tmp_path.parent / f"outside-{tmp_path.name}.txt"
    outside.write_text("private", encoding="utf-8")
    try:
        (tmp_path / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks need developer mode on Windows")
    assert run("fs_read_file", {"path": "link.txt"}, ws)["error"]["code"] == "permission_denied"


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are Windows-only")
def test_fs_junction_out_of_the_workspace_is_refused(ws, tmp_path):
    """Junctions need no developer mode, so this runs where the symlink tests skip."""
    import subprocess

    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir()
    (outside / "x.txt").write_text("private", encoding="utf-8")
    subprocess.run(  # noqa: S603 - fixed argv
        ["cmd", "/c", "mklink", "/J", str(tmp_path / "j"), str(outside)],  # noqa: S607
        check=True,
        capture_output=True,
    )
    try:
        assert run("fs_read_file", {"path": "j/x.txt"}, ws)["error"]["code"] == "permission_denied"
        assert (
            run("fs_write_file", {"path": "j/new.txt", "content": "x"}, ws)["error"]["code"]
            == "permission_denied"
        )
        assert not (outside / "new.txt").exists()
    finally:
        os.rmdir(tmp_path / "j")  # removes the junction only, never its target


def test_fs_errors_are_specific(ws, tmp_path):
    assert run("fs_read_file", {"path": "missing.txt"}, ws)["error"]["code"] == "not_found"
    (tmp_path / "bin.dat").write_bytes(b"\x00\x01\x02")
    assert run("fs_read_file", {"path": "bin.dat"}, ws)["error"]["code"] == "unsupported"
    assert (
        run("fs_move_file", {"source": "notes/todo.md", "destination": "notes/todo.md"}, ws)[
            "error"
        ]["code"]
        == "conflict"
    )
    assert run("fs_delete_file", {"path": "notes"}, ws)["error"]["code"] == "conflict"
    assert run("fs_delete_file", {"path": "."}, ws)["error"]["code"] == "permission_denied"
    assert (tmp_path / "notes" / "todo.md").exists()


def test_fs_read_truncates_and_says_so(ws, tmp_path):
    (tmp_path / "big.txt").write_text("a" * 50, encoding="utf-8")
    c = ctx(jarvis_workspace_dir=str(tmp_path), jarvis_web_max_bytes=10)
    assert run("fs_read_file", {"path": "big.txt"}, c)["data"] == {
        "path": "big.txt",
        "content": "a" * 10,
        "truncated": True,
    }


@pytest.mark.parametrize(
    "path",
    [
        "notes/todo.md:hidden",
        ".env::$DATA",
        "nul",
        "notes/con.txt",
        ".netrc",
        "keys/api_key.txt",
        "x/.ssh/config",
        "creds/password.txt",
    ],
)
def test_fs_refuses_streams_devices_and_more_credential_names(ws, path):
    r = run("fs_read_file", {"path": path}, ws)
    assert r["ok"] is False and r["error"]["code"] == "permission_denied", (path, r)


def test_fs_glob_delete_never_touches_credential_files(ws, tmp_path):
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / ".env").write_text("SECRET=1", encoding="utf-8")
    (tmp_path / "cfg" / "a.env").write_text("x", encoding="utf-8")
    r = run("fs_delete_file", {"path": "cfg", "match": "*env"}, ws)
    assert r["data"]["deleted"] == ["cfg/a.env"] and (tmp_path / "cfg" / ".env").exists()


def test_fs_search_skips_symlinks_that_leave_the_workspace(ws, tmp_path):
    outside = tmp_path.parent / f"outside-{tmp_path.name}.txt"
    outside.write_text("call Ada secretly", encoding="utf-8")
    try:
        (tmp_path / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks need developer mode on Windows")
    assert run("fs_search", {"query": "secretly"}, ws)["data"]["results"] == []


def test_fs_glob_delete_is_bounded(ws, tmp_path):
    (tmp_path / "tmp").mkdir()
    for i in range(3):
        (tmp_path / "tmp" / f"{i}.log").write_text("x", encoding="utf-8")
    (tmp_path / "tmp" / "keep.txt").write_text("x", encoding="utf-8")
    r = run("fs_delete_file", {"path": "tmp", "match": "*.log"}, ws)
    assert (
        sorted(r["data"]["deleted"]) == ["tmp/0.log", "tmp/1.log", "tmp/2.log"]
        and (tmp_path / "tmp" / "keep.txt").exists()
    )


# -- Shell (real processes) -----------------------------------------------------


@pytest.fixture
def sh(tmp_path) -> T.ToolContext:
    return ctx(
        jarvis_workspace_dir=str(tmp_path),
        jarvis_shell_allowlist="python,git,ping",
        jarvis_tool_timeout_seconds=6,
    )


def test_shell_runs_an_allowlisted_program_for_real(sh):
    r = run("shell_run", {"command": "git --version"}, sh)
    assert r["ok"] and r["data"]["exit_code"] == 0 and "git version" in r["data"]["stdout"]


@pytest.mark.parametrize("cmd", ["python --version", "python -c print(1)"])
def test_shell_never_runs_an_interpreter_even_if_allowlisted(sh, cmd):
    r = run("shell_run", {"command": cmd}, sh)
    assert r["error"]["code"] == "permission_denied" and "never allowed" in r["error"]["message"]


@pytest.mark.parametrize(
    "cmd",
    [
        "git -c core.pager=whoami log",
        "git -C .. status",
        "git --exec-path=. status",
        "git --config-env=x=y status",
        "git log --ext-diff",
    ],
)
def test_shell_refuses_git_options_that_run_commands_or_leave_the_workspace(sh, cmd):
    assert run("shell_run", {"command": cmd}, sh)["error"]["code"] == "permission_denied"


def test_shell_nonzero_exit_is_a_failure(sh):
    r = run("shell_run", {"command": "git rev-parse --verify definitely-not-a-ref"}, sh)
    assert r["ok"] is False and r["error"]["code"] == "nonzero_exit"


@pytest.mark.parametrize(
    "cmd",
    [
        "python -c 'import os'; rm -rf /",
        "git log | more",
        "git status && whoami",
        "echo $(id)",
        "git status > out.txt",
        "python\nwhoami",
    ],
)
def test_shell_refuses_metacharacters(sh, cmd):
    assert run("shell_run", {"command": cmd}, sh)["error"]["code"] == "invalid_arguments"


@pytest.mark.parametrize(
    "cmd",
    [
        "powershell -c dir",
        "cmd /c dir",
        "C:/Windows/System32/cmd.exe /c dir",
        "../python --version",
    ],
)
def test_shell_refuses_programs_not_on_the_allowlist(sh, cmd):
    assert run("shell_run", {"command": cmd}, sh)["error"]["code"] == "permission_denied"


def test_shell_cwd_cannot_leave_the_workspace(sh):
    assert (
        run("shell_run", {"command": "git status", "cwd": ".."}, sh)["error"]["code"]
        == "permission_denied"
    )


def test_shell_timeout_kills_the_process(sh):
    c = ctx(
        jarvis_workspace_dir=sh.settings.jarvis_workspace_dir,
        jarvis_shell_allowlist="ping",
        jarvis_tool_timeout_seconds=2.5,
    )
    r = run("shell_run", {"command": "ping -n 30 127.0.0.1"}, c)
    assert r["ok"] is False and r["error"]["code"] == "timeout"


def test_shell_does_not_leak_backend_secrets(sh, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "super-secret-value")
    r = run("shell_run", {"command": "git var -l"}, sh)
    assert "super-secret-value" not in json.dumps(r)


# -- Web (real local HTTP server) ------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, code, body, ctype="text/html", headers=None):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/page"):
            return self._reply(
                200, "<html><script>x()</script><h1>Title</h1><p>Body text</p></html>"
            )
        if self.path.startswith("/big"):
            return self._reply(200, "z" * 5000, "text/plain")
        if self.path.startswith("/redirect"):
            return self._reply(302, "", headers={"Location": "/page"})
        if self.path.startswith("/search"):
            return self._reply(
                200,
                json.dumps({"results": [{"title": "T", "url": "https://e.org", "content": "c"}]}),
                "application/json",
            )
        if self.path.startswith("/badsearch"):
            return self._reply(200, "not json", "text/plain")
        if self.path.startswith("/w/api.php"):  # MediaWiki list=search shape
            return self._reply(
                200,
                json.dumps(
                    {
                        "query": {
                            "search": [
                                {
                                    "title": "Citizen Kane",
                                    "snippet": 'often <span class="m">ranked</span> &amp; praised',
                                },
                                {"title": ""},
                                "junk",
                            ]
                        }
                    }
                ),
                "application/json",
            )
        return self._reply(404, "nope")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self._reply(201, json.dumps({"got": self.rfile.read(n).decode()}), "application/json")


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def local(**kw) -> T.ToolContext:
    return ctx(jarvis_web_allow_private_hosts=True, **kw)


def test_web_fetch_real_page_is_text_without_markup(server):
    r = run("web_fetch", {"url": f"{server}/page"}, local())
    assert r["ok"] and r["data"]["status"] == 200 and r["data"]["text"] == "Title Body text"


def test_web_fetch_follows_redirects_and_truncates(server):
    assert run("web_fetch", {"url": f"{server}/redirect"}, local())["data"]["url"].endswith("/page")
    r = run("web_fetch", {"url": f"{server}/big"}, local(jarvis_web_max_bytes=100))
    assert r["data"]["truncated"] is True and len(r["data"]["text"]) == 100


def test_web_fetch_http_errors_and_unreachable_hosts(server):
    assert run("web_fetch", {"url": f"{server}/missing"}, local())["error"]["code"] == "http_404"
    assert (
        run("web_fetch", {"url": "http://127.0.0.1:1/"}, local())["error"]["code"]
        == "connection_failed"
    )
    assert (
        run("web_fetch", {"url": "ftp://example.com/x"}, local())["error"]["code"]
        == "invalid_arguments"
    )
    assert (
        run("web_fetch", {"url": "http://user:pw@example.com/"}, local())["error"]["code"]
        == "invalid_arguments"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost:8000/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5/",
        "http://[::1]/",
    ],
)
def test_web_fetch_refuses_private_hosts_by_default(url):
    r = run("web_fetch", {"url": url}, ctx(jarvis_web_allow_private_hosts=False))
    assert r["ok"] is False and r["error"]["code"] == "permission_denied"


def _capture_requests(monkeypatch, answers):
    """Fake DNS (one answer per lookup, in order) and capture what the HTTP transport is asked to send."""
    import socket

    import httpx

    lookups = iter(answers)
    sent = []

    def fake_getaddrinfo(host, port, *a, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(lookups), port))]

    async def fake_send(self, request):
        sent.append(request)
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="ok")

    monkeypatch.setattr(T.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fake_send)
    return sent


def test_web_fetch_connects_to_the_checked_ip_and_keeps_host_and_tls_name(monkeypatch):
    sent = _capture_requests(monkeypatch, ["93.184.215.14", "127.0.0.1"])
    r = run(
        "web_fetch",
        {"url": "https://rebind.example:8443/x"},
        ctx(jarvis_web_allow_private_hosts=False),
    )
    assert r["ok"] and r["data"]["url"] == "https://rebind.example:8443/x"
    (req,) = sent
    assert req.url.host == "93.184.215.14" and req.url.port == 8443
    assert req.headers["host"] == "rebind.example:8443"
    assert req.extensions["sni_hostname"] == "rebind.example"


def test_web_fetch_dns_rebinding_cannot_reach_a_private_address(monkeypatch):
    """The second DNS answer (127.0.0.1) is never used: the request goes to the IP that was checked."""
    sent = _capture_requests(monkeypatch, ["93.184.215.14", "127.0.0.1", "127.0.0.1"])
    run("web_fetch", {"url": "http://rebind.example/"}, ctx(jarvis_web_allow_private_hosts=False))
    assert [str(r.url.host) for r in sent] == ["93.184.215.14"]


def test_web_fetch_rechecks_a_redirect_target_that_resolves_private(monkeypatch):
    import httpx

    sent = []
    answers = iter(["93.184.215.14", "10.0.0.5"])
    monkeypatch.setattr(
        T.socket,
        "getaddrinfo",
        lambda host, port, *a, **kw: [(2, 1, 6, "", (next(answers), port))],
    )

    async def redirect(self, request):
        sent.append(request)
        return httpx.Response(302, headers={"location": "http://inside.example/"})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", redirect)
    r = run(
        "web_fetch", {"url": "http://outside.example/"}, ctx(jarvis_web_allow_private_hosts=False)
    )
    assert r["error"]["code"] == "permission_denied" and len(sent) == 1


def test_http_request_sends_a_body_and_validates_the_method(server):
    r = run("http_request", {"method": "post", "url": f"{server}/echo", "body": "hi"}, local())
    assert r["ok"] and json.loads(r["data"]["text"]) == {"got": "hi"}
    assert (
        run("http_request", {"method": "TRACE", "url": server}, local())["error"]["code"]
        == "invalid_arguments"
    )


def test_web_search_contract_against_a_local_stub(server):
    """Contract test only: the stub stands in for a SearXNG instance. No real provider is tested."""
    r = run("web_search", {"query": "q"}, local(jarvis_web_search_url=f"{server}/search"))
    assert r == {
        "ok": True,
        "data": {
            "query": "q",
            "provider": "searxng",
            "coverage": "the configured SearXNG instance",
            "results": [{"title": "T", "url": "https://e.org", "snippet": "c"}],
            "truncated": False,
        },
    }
    assert (
        run("web_search", {"query": "q"}, local(jarvis_web_search_url=f"{server}/badsearch"))[
            "error"
        ]["code"]
        == "invalid_result"
    )
    assert (
        run("web_search", {"query": "q"}, local(jarvis_web_search_url="http://127.0.0.1:1/"))[
            "error"
        ]["code"]
        == "connection_failed"
    )
    assert (
        run("web_search", {"query": "q"}, ctx(jarvis_web_search_url=""))["error"]["code"]
        == "unavailable"
    )


def test_wikipedia_provider_parses_mediawiki_search_and_drops_malformed_rows(server):
    """Contract test against a MediaWiki-shaped stub; the live API was exercised separately (PHASE7_REPORT)."""
    c = local(jarvis_web_search_provider="wikipedia", jarvis_web_search_url=f"{server}/w/api.php")
    r = run("web_search", {"query": "good movies"}, c)
    assert r["ok"] and r["data"]["provider"] == "wikipedia"
    assert r["data"]["coverage"] == "Wikipedia articles only, not the general web"
    assert r["data"]["results"] == [
        {
            "title": "Citizen Kane",
            "url": "https://en.wikipedia.org/wiki/Citizen_Kane",
            "snippet": "often ranked & praised",
        }
    ]
    assert T.availability("web_search", c.settings) is None


def test_unknown_search_provider_is_unavailable():
    c = ctx(jarvis_web_search_provider="bing")
    assert "unknown search provider" in T.availability("web_search", c.settings)


# -- Time phrases used in the training data -------------------------------------


@pytest.mark.parametrize(
    "phrase,expect",
    [
        ("tomorrow at 10", "2026-10-13T10:00:00+05:30"),
        ("tomorrow at 9", "2026-10-13T09:00:00+05:30"),
        ("Wednesday at 11", "2026-10-14T11:00:00+05:30"),
        ("first thing tomorrow", "2026-10-13T09:00:00+05:30"),
        ("Friday afternoon", "2026-10-16T12:00:00+05:30"),
        ("today at 3pm", "2026-10-12T15:00:00+05:30"),
        ("2026-11-01T08:30:00+00:00", "2026-11-01T08:30:00+00:00"),
    ],
)
def test_parse_instant(phrase, expect):
    assert parse_instant(phrase, TZ, NOW).isoformat() == expect


@pytest.mark.parametrize(
    "phrase", ["tomorrow", "sometime", "next week at 10", "at 25:00 tomorrow", "2026-02-30T10:00"]
)
def test_parse_instant_refuses_what_it_cannot_resolve(phrase):
    with pytest.raises(WhenError):
        parse_instant(phrase, TZ, NOW)


def test_parse_window_and_day():
    s, e = parse_window("this weekend", TZ, NOW)
    assert (s.date().isoformat(), e.date().isoformat()) == ("2026-10-17", "2026-10-19")
    s, e = parse_window("tonight", TZ, NOW)
    assert (s.hour, e.isoformat()) == (17, "2026-10-13T00:00:00+05:30")
    assert parse_window("tomorrow at 9", TZ, NOW)[0].isoformat() == "2026-10-13T09:00:00+05:30"
    assert parse_day("next Monday", TZ, NOW).isoformat() == "2026-10-19"
    assert parse_day("in two days", TZ, NOW).isoformat() == "2026-10-14"
    assert parse_day("next week", TZ, NOW).isoformat() == "2026-10-25"
