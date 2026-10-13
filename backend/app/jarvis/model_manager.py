"""On-demand loading of the NEXO model server.

NEXUS starts without Qwen3-8B. The user switches NEXO on from the chat page; this module then
starts the configured model server (``JARVIS_MODEL_LAUNCH_ARGV``, a JSON argv list set by the
launcher), waits for a real ``/health`` that names the expected adapter, and switches it off
again on request or after ``JARVIS_IDLE_UNLOAD_MINUTES`` without use.

Only a process this module started is ever stopped: a server someone started by hand is
reported as ``external`` and left alone. Stopping kills that one process tree (serve_jarvis.py
and its llama-server child) and then checks the process is gone and the port no longer answers.

State is per backend process, like the pending approvals: a single ``run.py`` process.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.jarvis.model_client import JarvisModelClient, JarvisUnavailableError

logger = logging.getLogger("app.jarvis.model_manager")

#: Measured with the default runtime (16 GPU layers, 8k q8_0 KV): about 2.5 GB VRAM and 3.6 GB RAM
#: (much of it the memory-mapped GGUF). Below these the load would fail or push NEXUS into swap.
MIN_FREE_RAM_MB = 3000
MIN_FREE_VRAM_MB = 2600
#: How long an unload waits for a running generation before stopping the server anyway.
UNLOAD_GRACE_SECONDS = 30.0


class ModelBusyError(Exception):
    """The model is loading or unloading, so a chat turn cannot start now."""


def free_ram_mb() -> int | None:
    """Available physical memory in MB, or None when it cannot be read."""
    if sys.platform != "win32":
        with contextlib.suppress(OSError, ValueError):
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
        return None

    class _Status(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
            (n, ctypes.c_ulonglong)
            for n in (
                "ullTotalPhys",
                "ullAvailPhys",
                "ullTotalPageFile",
                "ullAvailPageFile",
                "ullTotalVirtual",
                "ullAvailVirtual",
                "ullAvailExtendedVirtual",
            )
        ]

    st = _Status()
    st.dwLength = ctypes.sizeof(_Status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):  # type: ignore[attr-defined]
        return None
    return int(st.ullAvailPhys // (1024 * 1024))


def gpu_memory_mb() -> dict[str, int] | None:
    """Used and free VRAM of the first GPU from nvidia-smi, or None when there is none."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, resolved executable
            [exe, "--query-gpu=memory.used,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        used, free = (int(x) for x in out.splitlines()[0].split(","))
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
    return {"used": used, "free": free}


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":  # serve_jarvis.py starts llama-server as a child; /T takes both
        subprocess.run(  # noqa: S603 - fixed argv; the pid is our own child
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],  # noqa: S607 - Windows system tool
            capture_output=True,
            check=False,
        )
    else:
        proc.terminate()


def _spawn(argv: list[str]) -> subprocess.Popen[bytes]:
    # argv[1] is serve_jarvis.py; its folder holds logs/ next to the launcher's logs.
    workdir = Path(argv[1]).parent if len(argv) > 1 else Path.cwd()
    (workdir / "logs").mkdir(parents=True, exist_ok=True)
    with (workdir / "logs" / "jarvis-model.log").open("wb") as log:
        return subprocess.Popen(  # noqa: S603 - argv comes from server config, never from a request
            argv,
            cwd=workdir,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )


class ModelManager:
    """Owns at most one model-server process and its off/loading/ready/unloading state."""

    def __init__(self) -> None:
        self.state = "off"  # off | loading | ready | unloading | error | external
        self.error: str | None = None
        self.proc: subprocess.Popen[bytes] | None = None
        self.active = 0
        self.last_used = time.monotonic()
        self.last_unload: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self._watch: asyncio.Task[None] | None = None

    # -- status ---------------------------------------------------------------

    @staticmethod
    def launch_argv(settings: Settings) -> list[str] | None:
        """The configured server command, or None when on-demand loading is not set up."""
        raw = settings.jarvis_model_launch_argv.strip()
        if not raw:
            return None
        argv = json.loads(raw)
        if not (isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv)):
            raise ValueError("JARVIS_MODEL_LAUNCH_ARGV must be a JSON list of strings")
        return argv

    def snapshot(self, settings: Settings) -> dict[str, Any]:
        """The public lifecycle view; ``busy`` is ``ready`` with a turn in flight."""
        state = self.state
        if state == "ready" and self.active:
            state = "busy"
        try:
            on_demand = self.launch_argv(settings) is not None
        except (ValueError, json.JSONDecodeError):
            on_demand = False
        return {
            "state": state,
            "error": self.error,
            "on_demand": on_demand,
            "owned": self.proc is not None,
            "idle_unload_minutes": settings.jarvis_idle_unload_minutes,
            "idle_seconds": round(time.monotonic() - self.last_used),
            "last_unload": self.last_unload,
        }

    # -- activity -------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def activity(self) -> AsyncIterator[None]:
        """Wrap one chat or confirm turn: blocks unloading while it runs and resets the idle clock."""
        if self.state in ("loading", "unloading"):
            raise ModelBusyError(f"the NEXO model is {self.state}; try again when it is ready")
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1
            self.last_used = time.monotonic()

    # -- load -----------------------------------------------------------------

    async def load(self, settings: Settings) -> dict[str, Any]:
        """Start the server if it is not already up; returns at once, readiness is supervised."""
        async with self._lock:
            if self.state in ("loading", "ready"):
                return self.snapshot(settings)
            client = JarvisModelClient(settings)
            with contextlib.suppress(JarvisUnavailableError):
                await client.health()
                self.state, self.error = "external", None  # already running, not ours to stop
                return self.snapshot(settings)
            try:
                argv = self.launch_argv(settings)
            except (ValueError, json.JSONDecodeError) as exc:
                return self._fail(settings, str(exc))
            if argv is None:
                return self._fail(
                    settings,
                    "on-demand loading is not configured (JARVIS_MODEL_LAUNCH_ARGV); start NEXUS with start.cmd",
                )
            if not settings.jarvis_enabled or not settings.jarvis_model_url.strip():
                return self._fail(
                    settings, "NEXO is disabled on this server (JARVIS_ENABLED / JARVIS_MODEL_URL)"
                )
            missing = [
                a
                for a in argv
                if a.endswith((".py", ".exe", ".gguf")) and not os.path.exists(a)  # noqa: ASYNC240 - a few stat calls
            ]
            if missing:
                return self._fail(settings, f"model files are missing: {', '.join(missing)}")
            ram = free_ram_mb()
            if ram is not None and ram < MIN_FREE_RAM_MB:
                return self._fail(
                    settings,
                    f"only {ram} MB RAM free; NEXO needs about {MIN_FREE_RAM_MB} MB. Close other applications and retry.",
                )
            gpu = gpu_memory_mb()
            if gpu is not None and gpu["free"] < MIN_FREE_VRAM_MB:
                return self._fail(
                    settings,
                    f"only {gpu['free']} MB GPU memory free; NEXO needs about {MIN_FREE_VRAM_MB} MB.",
                )
            self.proc = await asyncio.to_thread(_spawn, argv)
            self.state, self.error = "loading", None
            self.last_used = time.monotonic()
            logger.info("jarvis_model_loading", extra={"pid": self.proc.pid})
            self._watch = asyncio.create_task(self._supervise(settings))
            return self.snapshot(settings)

    def _fail(self, settings: Settings, reason: str) -> dict[str, Any]:
        self.state, self.error = "error", reason
        return self.snapshot(settings)

    async def _supervise(self, settings: Settings) -> None:
        """Wait for readiness, then unload after the idle interval. Ends when the model is off."""
        client = JarvisModelClient(settings)
        deadline = time.monotonic() + settings.jarvis_model_load_timeout_seconds
        while self.state == "loading":
            proc = self.proc
            if proc is None:
                return
            if proc.poll() is not None:
                self.proc, self.state = None, "error"
                self.error = f"the model server exited during loading (code {proc.returncode}); see Jarvis/jarvis_phase7/logs/jarvis-model.log"
                return
            try:
                await client.health()  # also checks the adapter hash
                self.state = "ready"
                self.last_used = time.monotonic()
                logger.info("jarvis_model_ready")
                break
            except JarvisUnavailableError:
                pass
            if time.monotonic() > deadline:
                await self.unload(settings, reason="load timed out")
                self.state, self.error = "error", "the model did not become ready in time"
                return
            await asyncio.sleep(2)
        idle = settings.jarvis_idle_unload_minutes * 60
        while self.state == "ready" and self.proc is not None:
            await asyncio.sleep(min(30.0, idle or 30.0))
            proc = self.proc
            if proc is not None and proc.poll() is not None:
                self.proc, self.state = None, "error"
                self.error = f"the model server stopped unexpectedly (code {proc.returncode})"
                return
            if idle and self.active == 0 and time.monotonic() - self.last_used >= idle:
                await self.unload(settings, reason="idle")

    # -- unload ---------------------------------------------------------------

    async def unload(self, settings: Settings, reason: str = "user") -> dict[str, Any]:
        """Stop the server this manager started; waits up to the grace period for running turns."""
        if self.state == "external":
            self.error = "this model server was not started by NEXUS, so NEXUS will not stop it"
            return self.snapshot(settings)
        proc = self.proc
        if proc is None:
            if self.state != "loading":
                self.state = "off"
            return self.snapshot(settings)
        self.state = "unloading"  # new turns are refused from here on
        deadline = time.monotonic() + UNLOAD_GRACE_SECONDS
        while self.active and time.monotonic() < deadline:  # noqa: ASYNC110 - bounded poll of a counter
            await asyncio.sleep(0.5)
        cancelled_active = self.active
        before = gpu_memory_mb()
        await asyncio.to_thread(_kill_tree, proc)
        try:
            await asyncio.to_thread(proc.wait, 30)
        except subprocess.TimeoutExpired:
            proc.kill()
            await asyncio.to_thread(proc.wait, 10)
        try:
            await JarvisModelClient(settings).health()
            still_answers = True
        except JarvisUnavailableError:
            still_answers = False
        await asyncio.sleep(1)  # the driver releases VRAM shortly after the process exits
        after = gpu_memory_mb()
        self.proc = None
        self.state = "off"
        self.error = None
        self.last_unload = {
            "reason": reason,
            "process_exited": proc.poll() is not None,
            "server_still_answers": still_answers,
            "generations_interrupted": cancelled_active,
            "gpu_used_mb_before": before and before["used"],
            "gpu_used_mb_after": after and after["used"],
        }
        logger.info("jarvis_model_unloaded", extra=self.last_unload)
        return self.snapshot(settings)


manager = ModelManager()

__all__ = ["ModelBusyError", "ModelManager", "free_ram_mb", "gpu_memory_mb", "manager"]
