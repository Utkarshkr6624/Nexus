"""HTTP client for the separate Jarvis generation server.

Qwen3-8B cannot share the backend process (CPU-only torch pins, 4 GB GPU), so it
runs on its own (``Jarvis/jarvis_phase7/serve_jarvis.py``) and NEXUS calls:

* ``GET  /health``   -> ``{"status": "ok", "model_id": ..., "adapter_sha256": ...}``
* ``POST /generate`` -> ``{"text": <raw generation>, "adapter_sha256": ...}`` for
  ``{"messages": [...], "tools": [...]}`` (greedy, ``enable_thinking=False``).

Every answer must name the expected adapter. A server running some other
checkpoint is treated exactly like a server that is down: unavailable.
"""

from __future__ import annotations

from typing import Any

try:
    import httpx
except (
    ImportError
):  # dev-only dependency (below the requirements DEV marker): NEXUS must still boot
    httpx = None  # type: ignore[assignment]

from app.core.config import Settings

__all__ = ["JarvisModelClient", "JarvisUnavailableError"]


class JarvisUnavailableError(Exception):
    """The model cannot answer: off, unconfigured, unreachable, failing or serving another adapter."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class JarvisModelClient:
    """One request per call; no connection is held between turns."""

    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self._transport = transport

    def _check_configured(self) -> None:
        if httpx is None:
            raise JarvisUnavailableError(
                "httpx is not installed in this environment (it is a dev-only dependency), so NEXO cannot make HTTP calls"
            )
        if not self.settings.jarvis_enabled:
            raise JarvisUnavailableError("NEXO chat is disabled (JARVIS_ENABLED=false)")
        if not self.settings.jarvis_model_url.strip():
            raise JarvisUnavailableError("no NEXO model server is configured (JARVIS_MODEL_URL)")

    def _check_adapter(self, body: Any) -> None:
        reported = body.get("adapter_sha256") if isinstance(body, dict) else None
        if reported != self.settings.jarvis_expected_adapter_sha256:
            raise JarvisUnavailableError(
                f"the model server reports adapter {str(reported)[:12]!r}, not the configured "
                f"{self.settings.jarvis_expected_adapter_sha256[:12]!r}"
            )

    async def _call(self, method: str, path: str, seconds: float, **kw: Any) -> Any:
        self._check_configured()
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.jarvis_model_url, timeout=seconds, transport=self._transport
            ) as client:
                r = await client.request(method, path, **kw)
        except httpx.TimeoutException as exc:
            raise JarvisUnavailableError("the NEXO model server timed out") from exc
        except httpx.HTTPError as exc:
            raise JarvisUnavailableError(
                f"the NEXO model server is unreachable ({type(exc).__name__})"
            ) from exc
        if r.status_code != 200:
            raise JarvisUnavailableError(f"the NEXO model server answered HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError as exc:
            raise JarvisUnavailableError("the NEXO model server returned invalid JSON") from exc
        self._check_adapter(body)
        return body

    async def health(self) -> dict[str, Any]:
        """The server's identity, verified against the expected adapter."""
        return await self._call("GET", "/health", 5.0)

    async def generate(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> str:
        """One raw generation (text and any ``<tool_call>`` blocks) for the conversation so far."""
        body = await self._call(
            "POST",
            "/generate",
            self.settings.jarvis_generation_timeout_seconds,
            json={"messages": messages, "tools": tools},
        )
        text = body.get("text")
        if not isinstance(text, str):
            raise JarvisUnavailableError("the NEXO model server returned no text")
        return text
