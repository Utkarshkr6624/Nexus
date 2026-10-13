"""Jarvis endpoints: the Phase 7 development integration of the Qwen3-8B model.

Three routes:

* ``GET /jarvis/status`` - is the model reachable, which adapter, and the full
  tool table with each tool's availability and blocker. Answers 200 even when
  Jarvis is down, because "down" is the information.
* ``POST /jarvis/chat`` - one conversational turn. History is held by the client
  (user/assistant messages only); NEXUS stores no transcript, matching the
  voice design's privacy stance.
* ``POST /jarvis/confirm`` - approve or decline a pending side-effect proposal.
  Single use: a second call with the same id is a 404, so an action cannot be
  executed twice by a retry.

Separation from NEXUS's own model: the DeBERTa classifier keeps serving
``/ml/route`` exactly as before. Chat only *reads* its prediction, when one is
already loaded, as diagnostic context; a Jarvis failure never touches it and a
classifier failure never blocks Jarvis.

Message text is never logged.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated, Any, Literal

try:
    import httpx
except (
    ImportError
):  # dev-only dependency (below the requirements DEV marker): NEXUS must still boot
    httpx = None  # type: ignore[assignment]
from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.deps import AuthenticatedUser, MLRuntimeDep
from app.core.config import Settings, get_settings
from app.core.deps import require_permission
from app.core.exceptions import ErrorCode, NexusError, NotFoundError, ValidationError
from app.core.permissions import Permission
from app.jarvis.model_client import JarvisModelClient, JarvisUnavailableError
from app.jarvis.model_manager import ModelBusyError, manager
from app.jarvis.orchestrator import WITHHELD, Outcome, TurnResult, resume_turn, run_turn
from app.jarvis.tools import ToolContext, availability, registry
from app.services.planner_service import resolve_timezone

router = APIRouter(prefix="/jarvis", tags=["jarvis"])

_USE = [Depends(require_permission(Permission.JARVIS_USE))]
QUALIFICATION = (
    "development candidate - NOT acceptance-qualified (Phase 6 verdict FAIL under scorer v1 and v2.2); "
    "runs only behind the runtime guards"
)


class JarvisServiceUnavailableError(NexusError):
    code = ErrorCode.JARVIS_UNAVAILABLE
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    default_message = (
        "NEXO is not available. NEXUS itself, including intent classification, is unaffected."
    )


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)
    timezone: str | None = Field(
        default=None, max_length=64, description="IANA zone for relative dates."
    )

    @model_validator(mode="after")
    def _ends_with_user(self) -> ChatRequest:
        if self.messages[-1].role != "user":
            raise ValueError("the last message must be the user's")
        return self


class ConfirmRequest(BaseModel):
    # Only the id and the decision: the action that runs is the stored proposal, never one the client describes.
    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(min_length=32, max_length=32)
    approve: bool
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("proposal_id")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{32}", v):
            raise ValueError("not a proposal id")
        return v


class TurnRead(BaseModel):
    outcome: str
    text: str
    trace: list[dict[str, Any]]
    proposal_id: str | None
    proposal: list[dict[str, Any]] | None
    guards: dict[str, Any]
    steps: int
    model: dict[str, Any]
    nexus_classification: dict[str, Any] | None


def get_jarvis_client(settings: Annotated[Settings, Depends(get_settings)]) -> JarvisModelClient:
    return JarvisModelClient(settings)


JarvisClientDep = Annotated[JarvisModelClient, Depends(get_jarvis_client)]


def _context(request: Request, settings: Settings, tz_name: str | None) -> ToolContext:
    tz = resolve_timezone(tz_name, settings)
    nexus = (
        None
        if httpx is None
        else httpx.AsyncClient(
            transport=httpx.ASGITransport(app=request.app),
            base_url="http://nexus.internal",
            headers={"Authorization": request.headers.get("Authorization", "")},
            timeout=settings.jarvis_tool_timeout_seconds,
        )
    )
    return ToolContext(settings=settings, tz=tz, now=datetime.now(UTC), nexus=nexus)


def _read(
    result: TurnResult, settings: Settings, classification: dict[str, Any] | None
) -> TurnRead:
    ran = any(t.status in ("executed_ok", "partial", "executed_failed") for t in result.trace)
    if result.outcome == Outcome.MODEL_UNAVAILABLE and not ran:
        raise JarvisServiceUnavailableError(details={"reason": result.text})
    # A model failure after a tool ran is still answered with the trace: the action happened, the
    # proposal is spent, and the user must be able to see what ran.
    withhold = bool(result.guards.get("tool_data_withheld"))
    proposal = result.proposal
    if withhold and proposal:
        proposal = [{"name": c["name"], "arguments": {"withheld": WITHHELD}} for c in proposal]
    return TurnRead(
        outcome=result.outcome,
        text=result.text,
        trace=[t.public(withhold) for t in result.trace],
        proposal_id=result.proposal_id,
        proposal=proposal,
        guards=result.guards,
        steps=result.steps,
        model={
            "adapter_sha256": settings.jarvis_expected_adapter_sha256,
            "qualification": QUALIFICATION,
        },
        nexus_classification=classification,
    )


async def _classify(runtime: Any, text: str) -> dict[str, Any] | None:
    classifier = getattr(runtime, "classifier", None)
    if classifier is None:
        return {
            "available": False,
            "reason": getattr(getattr(runtime, "status", None), "reason", "not loaded"),
        }
    try:
        p = await run_in_threadpool(classifier.predict, text[:2000])
    except Exception as exc:  # diagnostics only; never blocks the turn
        return {"available": False, "reason": type(exc).__name__}
    return {
        "available": True,
        "model": "microsoft/deberta-v3-base",
        "intent": p.intent,
        "confidence": round(float(p.confidence), 4),
    }


@router.get("/status", summary="NEXO model availability and the tool table", dependencies=_USE)
async def jarvis_status(
    current_user: AuthenticatedUser,
    client: JarvisClientDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    try:
        health = await client.health()
        model = {
            "available": True,
            "model_id": health.get("model_id"),
            "adapter_sha256": health.get("adapter_sha256"),
        }
    except JarvisUnavailableError as exc:
        model = {"available": False, "reason": exc.reason}
    tools = [
        {
            "name": s.name,
            "side_effect": s.side_effect,
            "requires_confirmation": s.needs_confirmation,
            "available": availability(s.name, settings) is None,
            "reason": availability(s.name, settings),
        }
        for s in registry().values()
    ]
    if model["available"] and manager.state in ("off", "error"):
        manager.state, manager.error = "external", None  # answered, but NEXUS did not start it
    elif not model["available"] and manager.state in ("ready", "external"):
        manager.state = "off" if manager.proc is None else manager.state
    return {
        "enabled": settings.jarvis_enabled,
        "model": model,
        "lifecycle": manager.snapshot(settings),
        "expected_adapter_sha256": settings.jarvis_expected_adapter_sha256,
        "qualification": QUALIFICATION,
        "tools": tools,
    }


@router.post(
    "/chat", response_model=TurnRead, summary="One NEXO conversational turn", dependencies=_USE
)
async def jarvis_chat(
    payload: ChatRequest,
    request: Request,
    current_user: AuthenticatedUser,
    client: JarvisClientDep,
    runtime: MLRuntimeDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> TurnRead:
    if sum(len(m.content) for m in payload.messages) > settings.jarvis_max_input_chars:
        raise ValidationError(
            f"the conversation exceeds {settings.jarvis_max_input_chars} characters"
        )
    ctx = _context(request, settings, payload.timezone)
    try:
        async with _active():
            result = await run_turn(
                client, ctx, str(current_user.id), [m.model_dump() for m in payload.messages]
            )
    finally:
        if ctx.nexus is not None:
            await ctx.nexus.aclose()
    return _read(result, settings, await _classify(runtime, payload.messages[-1].content))


@router.post(
    "/confirm",
    response_model=TurnRead,
    summary="Approve or decline a pending NEXO action",
    dependencies=_USE,
)
async def jarvis_confirm(
    payload: ConfirmRequest,
    request: Request,
    current_user: AuthenticatedUser,
    client: JarvisClientDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> TurnRead:
    ctx = _context(request, settings, payload.timezone)
    try:
        async with _active():
            result = await resume_turn(
                client, ctx, str(current_user.id), payload.proposal_id, payload.approve
            )
    finally:
        if ctx.nexus is not None:
            await ctx.nexus.aclose()
    if result is None:
        raise NotFoundError(
            "No pending approval with that id: it expired, was already used, or the server restarted. "
            "Nothing was run."
        )
    return _read(result, settings, None)


@contextlib.asynccontextmanager
async def _active() -> AsyncIterator[None]:
    try:
        async with manager.activity():
            yield
    except ModelBusyError as exc:
        raise JarvisServiceUnavailableError(details={"reason": str(exc)}) from exc


@router.post(
    "/model/load",
    summary="Switch NEXO on: start the model server (if NEXUS manages it) and report its state",
    dependencies=_USE,
)
async def jarvis_model_load(
    current_user: AuthenticatedUser,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    return await manager.load(settings)


@router.post(
    "/model/unload",
    summary="Switch NEXO off: stop the model server NEXUS started and free its memory",
    dependencies=_USE,
)
async def jarvis_model_unload(
    current_user: AuthenticatedUser,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    return await manager.unload(settings)


__all__ = ["router"]
