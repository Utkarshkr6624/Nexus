# ruff: noqa: S603 - tests start fixed local processes (git, python)
"""The Phase 7 added tools and the on-demand model manager, executed for real.

Tools run against a temporary workspace and a real git repo; the model manager drives a real
child process (a stub HTTP server, not the model).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.core.config import get_settings
from app.jarvis import tools as T  # noqa: N812
from app.jarvis.model_manager import ModelBusyError, ModelManager
from app.jarvis.orchestrator import _PRIVATE_SOURCES

TZ = ZoneInfo("UTC")


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return root


def ctx(root, **over):
    s = get_settings().model_copy(update={"jarvis_workspace_dir": str(root), **over})
    return T.ToolContext(settings=s, tz=TZ, now=datetime(2026, 10, 13, tzinfo=UTC))


def run(name, args, c):
    return asyncio.run(T.run_tool(name, args, c))


def test_approval_tiers_of_added_tools():
    reg = T.registry()
    direct = {
        "workspace_list_files",
        "workspace_file_metadata",
        "workspace_compare_files",
        "code_search_symbols",
        "git_inspect",
        "json_yaml_validate",
        "csv_analyze",
        "calculate",
    }
    for name in direct:
        assert not reg[name].needs_confirmation, name
    for name in (
        "workspace_create_file",
        "workspace_backup_file",
        "code_apply_patch",
        "run_project_tests",
        "run_project_lint",
    ):
        assert reg[name].needs_confirmation, name
    assert direct - {"calculate"} <= _PRIVATE_SOURCES  # their output gates later web egress


def test_list_and_metadata(ws):
    (ws / "a.py").write_text("x = 1\n")
    (ws / "notes.txt").write_text("hi")
    (ws / ".env").write_text("SECRET=1")
    (ws / "sub").mkdir()
    r = run("workspace_list_files", {"pattern": "*.py"}, ctx(ws))
    assert r["ok"] and [e["path"] for e in r["data"]["entries"]] == ["a.py", "sub"]
    assert ".env" not in json.dumps(run("workspace_list_files", {}, ctx(ws)))
    m = run("workspace_file_metadata", {"path": "notes.txt"}, ctx(ws))["data"]
    assert m["bytes"] == 2 and m["extension"] == ".txt"
    assert m["sha256"] == "8f434346648f6b96df89dda901c5176b10a6d83961dd3c1ac88b59b2dc327aa4"


@pytest.mark.parametrize(
    "bad", ["../x", "C:/Windows/win.ini", "/etc/passwd", ".env", "sub/../../x"]
)
def test_paths_stay_in_the_workspace(ws, bad):
    r = run("workspace_file_metadata", {"path": bad}, ctx(ws))
    assert not r["ok"] and r["error"]["code"] == "permission_denied"


def test_compare_create_backup(ws):
    (ws / "a.txt").write_text("one\ntwo\n")
    (ws / "b.txt").write_text("one\nthree\n")
    d = run("workspace_compare_files", {"path_a": "a.txt", "path_b": "b.txt"}, ctx(ws))["data"]
    assert not d["identical"] and d["added"] == 1 and d["removed"] == 1 and "+three" in d["diff"]

    assert run("workspace_create_file", {"path": "new/c.txt", "content": "c"}, ctx(ws))["ok"]
    again = run("workspace_create_file", {"path": "new/c.txt", "content": "OVERWRITE"}, ctx(ws))
    assert again["error"]["code"] == "conflict" and (ws / "new/c.txt").read_text() == "c"

    b = run("workspace_backup_file", {"path": "a.txt"}, ctx(ws))["data"]
    assert b["verified"] and (ws / b["backup"]).read_text() == "one\ntwo\n"


def test_code_search_symbols(ws):
    (ws / "m.py").write_text("def parse_day(x):\n    return x\n\nparse_day(1)\n")
    (ws / "u.ts").write_text("export function parseDay() {}\nconst y = parseDay()\n")
    d = run("code_search_symbols", {"symbol": "parse_day"}, ctx(ws))["data"]
    assert [x["line"] for x in d["definitions"]] == [1] and [
        x["line"] for x in d["references"]
    ] == [4]
    d2 = run("code_search_symbols", {"symbol": "parseDay"}, ctx(ws))["data"]
    assert d2["definitions"][0]["path"] == "u.ts"
    assert (
        run("code_search_symbols", {"symbol": "a b; rm"}, ctx(ws))["error"]["code"]
        == "invalid_arguments"
    )


@pytest.mark.skipif(not shutil.which("git"), reason="git not installed")
def test_apply_patch_checks_first_and_stays_in_workspace(ws):
    (ws / "f.txt").write_text("alpha\nbeta\n")
    good = "--- a/f.txt\n+++ b/f.txt\n@@ -1,2 +1,2 @@\n alpha\n-beta\n+gamma\n"
    r = run("code_apply_patch", {"patch": good}, ctx(ws))
    assert r["ok"], r
    assert (ws / "f.txt").read_text() == "alpha\ngamma\n"
    stale = run("code_apply_patch", {"patch": good}, ctx(ws))  # no longer applies
    assert stale["error"]["code"] == "conflict" and (ws / "f.txt").read_text() == "alpha\ngamma\n"
    escape = good.replace("a/f.txt", "a/../evil.txt").replace("b/f.txt", "b/../evil.txt")
    assert (
        run("code_apply_patch", {"patch": escape}, ctx(ws))["error"]["code"] == "permission_denied"
    )


def test_project_commands_are_fixed_and_report_real_exit_codes(ws):
    assert run("run_project_tests", {}, ctx(ws))["error"]["code"] == "unavailable"
    cmds = json.dumps(
        {
            "test": [sys.executable, "-c", "print('ok')"],
            "lint": [sys.executable, "-c", "import sys; sys.exit(3)"],
        }
    )
    t = run("run_project_tests", {}, ctx(ws, jarvis_project_commands=cmds))["data"]
    assert t["passed"] and t["exit_code"] == 0 and "ok" in t["output_tail"]
    lint = run("run_project_lint", {}, ctx(ws, jarvis_project_commands=cmds))["data"]
    assert not lint["passed"] and lint["exit_code"] == 3
    assert T.validate_arguments(T.registry()["run_project_tests"], {"command": "rm -rf /"})


@pytest.mark.skipif(not shutil.which("git"), reason="git not installed")
def test_git_inspect_is_read_only(tmp_path, ws):
    repo = tmp_path / "repo"
    repo.mkdir()
    g = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*g, "init", "-q"], cwd=repo, check=True)
    (repo / "a.txt").write_text("1\n")
    subprocess.run([*g, "add", "."], cwd=repo, check=True)
    subprocess.run([*g, "commit", "-qm", "first commit"], cwd=repo, check=True)
    (repo / "a.txt").write_text("2\n")
    c = ctx(ws, jarvis_git_repo_dir=str(repo))
    assert "a.txt" in run("git_inspect", {"action": "status"}, c)["data"]["output"]
    assert "first commit" in run("git_inspect", {"action": "log"}, c)["data"]["output"]
    assert "+2" in run("git_inspect", {"action": "diff", "path": "a.txt"}, c)["data"]["output"]
    assert run("git_inspect", {"action": "reset"}, c)["error"]["code"] == "invalid_arguments"
    assert (
        run("git_inspect", {"action": "diff", "path": "--output=x"}, c)["error"]["code"]
        == "permission_denied"
    )
    assert (repo / "a.txt").read_text() == "2\n"  # nothing discarded
    assert run("git_inspect", {"action": "status"}, ctx(ws))["error"]["code"] == "unavailable"


def test_json_yaml_and_csv(ws):
    (ws / "ok.json").write_text('{"a": 1}')
    (ws / "bad.yaml").write_text("a: [1, 2\n")
    assert run("json_yaml_validate", {"path": "ok.json"}, ctx(ws))["data"]["valid"]
    bad = run("json_yaml_validate", {"path": "bad.yaml"}, ctx(ws))["data"]
    assert not bad["valid"] and bad["line"]
    j = run("json_yaml_validate", {"content": '{"a": }'}, ctx(ws))["data"]
    assert not j["valid"] and j["line"] == 1
    (ws / "d.csv").write_text("name,score\nann,3\nbob,\nann,3\ncid,5\n")
    d = run("csv_analyze", {"path": "d.csv"}, ctx(ws))["data"]
    assert d["rows"] == 4 and d["duplicate_rows"] == 1
    score = d["columns"][1]
    assert score["type"] == "number" and score["missing"] == 1 and score["max"] == 5.0


@pytest.mark.parametrize(
    ("expr", "want"),
    [
        ("(3+4)*2**10", 7168),
        ("0.1+0.2", 0.30000000000000004),
        ("sqrt(16)", 4),
        ("12 km to mi", 7.4564543068),  # 12000 m / 1609.344 m
        ("100 c to f", 212.0),
        ("2026-10-13 + 30 days", "2026-11-12"),
        ("days between 2026-01-01 and 2026-10-13", 285),
    ],
)
def test_calculate(ws, expr, want):
    assert run("calculate", {"expression": expr}, ctx(ws))["data"]["result"] == want


@pytest.mark.parametrize(
    "expr", ["__import__('os')", "9**9**9", "open('x')", "1/0", "3 kg to km", "().__class__"]
)
def test_calculate_refuses_unsafe_or_invalid(ws, expr):
    r = run("calculate", {"expression": expr}, ctx(ws))
    assert not r["ok"] and r["error"]["code"] == "invalid_arguments"


# -- model manager ---------------------------------------------------------------------

STUB_SERVER = r"""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
SHA = sys.argv[2]
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok", "model_id": "stub", "adapter_sha256": SHA}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
"""


def _free_port():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_model_manager_loads_only_on_request_and_stops_its_own_process(tmp_path):
    script = tmp_path / "stub_server.py"
    script.write_text(STUB_SERVER)
    port = _free_port()
    base = get_settings()
    s = base.model_copy(
        update={
            "jarvis_enabled": True,
            "jarvis_model_url": f"http://127.0.0.1:{port}",
            "jarvis_model_launch_argv": json.dumps(
                [sys.executable, str(script), str(port), base.jarvis_expected_adapter_sha256]
            ),
            "jarvis_idle_unload_minutes": 0.0,
        }
    )
    m = ModelManager()

    async def scenario():
        assert m.snapshot(s)["state"] == "off"
        assert (await m.load(s))["state"] == "loading"
        proc = m.proc
        for _ in range(240):
            if m.state == "ready":
                break
            await asyncio.sleep(0.25)
        assert m.state == "ready" and m.snapshot(s)["owned"]
        assert (await m.load(s))["state"] == "ready" and m.proc is proc  # no duplicate server
        async with m.activity():
            assert m.snapshot(s)["state"] == "busy"
        out = await m.unload(s)
        assert out["state"] == "off" and proc.poll() is not None
        assert (
            out["last_unload"]["process_exited"] and not out["last_unload"]["server_still_answers"]
        )

    asyncio.run(scenario())


def test_model_manager_never_stops_a_server_it_did_not_start(tmp_path):
    script = tmp_path / "stub_server.py"
    script.write_text(STUB_SERVER)
    port = _free_port()
    base = get_settings()
    outside = subprocess.Popen(
        [sys.executable, str(script), str(port), base.jarvis_expected_adapter_sha256]
    )
    try:
        s = base.model_copy(
            update={
                "jarvis_enabled": True,
                "jarvis_model_url": f"http://127.0.0.1:{port}",
                "jarvis_model_launch_argv": json.dumps([sys.executable, "-c", "pass"]),
            }
        )
        m = ModelManager()

        async def scenario():
            for _ in range(40):
                if (await m.load(s))["state"] == "external":
                    break
                m.state = "off"
                await asyncio.sleep(0.25)
            assert m.state == "external" and m.proc is None
            out = await m.unload(s)
            assert out["state"] == "external" and "not started by NEXUS" in out["error"]

        asyncio.run(scenario())
        assert outside.poll() is None  # still running
    finally:
        outside.kill()


def test_model_manager_refuses_without_launch_config_and_blocks_turns_while_switching():
    s = get_settings().model_copy(
        update={
            "jarvis_enabled": True,
            "jarvis_model_url": "http://127.0.0.1:9",
            "jarvis_model_launch_argv": "",
        }
    )
    m = ModelManager()
    out = asyncio.run(m.load(s))
    assert out["state"] == "error" and "not configured" in out["error"]
    m.state = "unloading"

    async def turn():
        async with m.activity():
            pass

    with pytest.raises(ModelBusyError):
        asyncio.run(turn())


def test_model_manager_unloads_after_the_idle_interval_but_not_during_a_turn(tmp_path):
    script = tmp_path / "stub_server.py"
    script.write_text(STUB_SERVER)
    port = _free_port()
    base = get_settings()
    s = base.model_copy(
        update={
            "jarvis_enabled": True,
            "jarvis_model_url": f"http://127.0.0.1:{port}",
            "jarvis_model_launch_argv": json.dumps(
                [sys.executable, str(script), str(port), base.jarvis_expected_adapter_sha256]
            ),
            "jarvis_idle_unload_minutes": 0.02,  # 1.2 s
        }
    )
    m = ModelManager()

    async def scenario():
        await m.load(s)
        for _ in range(240):
            if m.state == "ready":
                break
            await asyncio.sleep(0.25)
        assert m.state == "ready"
        proc = m.proc
        async with m.activity():  # a long turn: idle time never counts while it runs
            await asyncio.sleep(3)
            assert m.state == "ready"
        for _ in range(40):
            if m.state == "off":
                break
            await asyncio.sleep(0.25)
        assert m.state == "off" and m.last_unload["reason"] == "idle" and proc.poll() is not None

    asyncio.run(scenario())
