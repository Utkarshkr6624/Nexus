"""One Jarvis chat turn: generate, validate, (confirm), execute, observe, answer.

Order of authority, from the model outward:

1. The model proposes text or ``<tool_call>`` blocks. Nothing it writes is
   executed unless it parses strictly (:mod:`app.jarvis.toolcalls`), names a
   registered *and available* tool, and passes that tool's argument schema.
2. A tool with any side effect is never executed in the turn that proposed it:
   the turn stops with ``confirmation_required`` and a single-use proposal id.
   Only ``POST /jarvis/confirm`` with that id runs it, once.
3. Only the executor writes ``tool`` messages; the client cannot send them and
   the model cannot write them.
4. The final text goes through two guards: an honesty guard (no claim of a
   completed action unless a tool actually succeeded) and the crisis-contact
   guard (:mod:`app.jarvis.crisis_guard`), which runs last on every response.

The loop is bounded by ``JARVIS_MAX_STEPS``; a repeated identical call is
refused rather than re-executed, so a looping model cannot repeat an action.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.jarvis import crisis_guard
from app.jarvis.model_client import JarvisModelClient, JarvisUnavailableError
from app.jarvis.toolcalls import ParsedTurn, parse_turn
from app.jarvis.tools import (
    ToolContext,
    availability,
    available_tools,
    err,
    registry,
    run_tool,
    tool_definitions,
    validate_arguments,
)
from app.jarvis.workspace_tools import PRIVATE_READS

__all__ = [
    "SYSTEM_PROMPT",
    "ConfirmationStore",
    "Outcome",
    "TurnResult",
    "confirmations",
    "resume_turn",
    "run_turn",
]

logger = logging.getLogger("app.jarvis")

#: The system prompt every tool-bearing training record used (V2-V3.1), verbatim.
TRAINED_SYSTEM_PROMPT = (
    "You are the assistant inside NEXUS. State which tool you would use and with what arguments; "
    "report outcomes only from actual tool results."
)
#: The runtime prompt: the trained sentence unchanged plus one identity line, the smallest drift from training.
SYSTEM_PROMPT = TRAINED_SYSTEM_PROMPT + " Your name is NEXO."


class Outcome:
    """The routing outcome reported for a turn; exactly one per response."""

    RESPONSE = "response"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    TOOL_PARTIAL = "tool_partial"
    TOOL_UNSUPPORTED = "tool_unsupported"
    INVALID_TOOL_ARGUMENTS = "invalid_tool_arguments"
    INVALID_TOOL_CALL = "invalid_tool_call"
    CLARIFICATION_REQUIRED = "clarification_required"
    CONFIRMATION_REQUIRED = "confirmation_required"
    DENIED_BY_USER = "denied_by_user"
    MODEL_UNAVAILABLE = "model_unavailable"
    SERVICE_UNAVAILABLE = "service_unavailable"
    STEP_LIMIT = "step_limit"


#: Most decisive first: the reported outcome is the first one that happened.
_PRIORITY = [
    Outcome.MODEL_UNAVAILABLE,
    Outcome.CONFIRMATION_REQUIRED,
    Outcome.STEP_LIMIT,
    Outcome.INVALID_TOOL_CALL,
    Outcome.DENIED_BY_USER,
    Outcome.TOOL_UNSUPPORTED,
    Outcome.INVALID_TOOL_ARGUMENTS,
    Outcome.CLARIFICATION_REQUIRED,
    Outcome.SERVICE_UNAVAILABLE,
    Outcome.TOOL_FAILED,
    Outcome.TOOL_PARTIAL,
    Outcome.TOOL_COMPLETED,
    Outcome.RESPONSE,
]

WITHHELD = "tool details are not shown in a crisis conversation"

_ACTION_CLAIM = re.compile(
    r"\bI(?:'ve| have| just)? (?:created|saved|written|wrote|deleted|removed|moved|renamed|sent|scheduled|booked|added|"
    r"updated|changed|ran|run|executed|searched|fetched|downloaded|completed|marked|set up|installed|committed|pushed|"
    r"called|invoked|used|triggered)\b"
    r"|\b(?:has|have) been (?:created|saved|deleted|moved|sent|scheduled|added|updated|executed|completed)\b"
    r"|\b(?:done|all set)[.!]",
    re.I,
)


#: Which tools can back a claimed action. A claim is honest only if one of its tools succeeded in this turn.
_CLAIM_TOOLS = {
    "created": {"app_create_task", "app_create_event", "fs_write_file"},
    "added": {"app_create_task", "app_create_event", "fs_write_file"},
    "saved": {"fs_write_file"},
    "written": {"fs_write_file"},
    "wrote": {"fs_write_file"},
    "deleted": {"fs_delete_file"},
    "removed": {"fs_delete_file"},
    "moved": {"fs_move_file", "app_update_event", "app_update_task"},
    "renamed": {"fs_move_file", "app_update_task", "app_update_event"},
    "sent": {"app_send_message", "http_request"},
    "scheduled": {"app_create_event", "app_update_event"},
    "booked": {"app_create_event", "app_update_event"},
    "updated": {"app_update_task", "app_update_event", "app_update_goal", "fs_write_file"},
    "changed": {"app_update_task", "app_update_event", "app_update_goal", "fs_write_file"},
    "marked": {"app_update_task"},
    "completed": {"app_update_task"},
    "ran": {"shell_run"},
    "run": {"shell_run"},
    "executed": {"shell_run"},
    "installed": {"shell_run"},
    "committed": {"shell_run"},
    "pushed": {"shell_run"},
    "searched": {"web_search", "fs_search", "app_list_tasks"},
    "fetched": {"web_fetch", "http_request"},
    "downloaded": {"web_fetch", "http_request"},
}
#: Read-only tools whose results are the user's private data. Once one has run in a turn, an outbound
#: web request could carry that data off-host (prompt injection), so it waits for approval too.
_PRIVATE_SOURCES = {
    "fs_read_file",
    "fs_search",
    "app_list_tasks",
    "app_read_calendar",
    "db_describe",
    "shell_run",
    *PRIVATE_READS,
}
_EGRESS = {"web_fetch", "web_search"}
_OK = ("executed_ok", "partial")


#: Passive outcome claims. Only judged when no tool succeeded: after a read, "this task was created on Monday"
#: describes data. checkpoint_v31_139 said "It returned that the task was created" with no call made.
#: The subject must be the user's thing ("the task", "your file", "it"), so "Python was created by Guido" is not a claim.
_PASSIVE_CLAIM = re.compile(
    r"\b(?:it|they|this|that|your \w+|the (?:task|file|event|meeting|message|email|request|goal|reminder|"
    r"folder|command|program|script|item|entry|note)s?) (?:was|were|has been|have been) (?:successfully )?"
    r"(?:created|saved|written|deleted|removed|moved|renamed|sent|scheduled|booked|added|updated|changed|"
    r"executed|run)\b",
    re.I,
)


def _unsupported_claim(text: str, trace: list[TraceEntry]) -> bool:
    """True when the text claims an action that no successful tool call in this turn performed."""
    done = {t.tool for t in trace if t.status in _OK}
    if not done and _PASSIVE_CLAIM.search(text or ""):
        return True
    for m in _ACTION_CLAIM.finditer(text or ""):
        verb = next(
            (w for w in re.findall(r"[a-z]+", m.group(0).lower()) if w in _CLAIM_TOOLS), None
        )
        if not (done & _CLAIM_TOOLS[verb] if verb else done):
            return True
    return False


PROSE_PROPOSAL_TEXT = (
    "I described this action in words instead of calling the tool, so nothing has run. "
    "Here is the exact call for you to check."
)
_TOOL_NAME = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")
_KWARG = re.compile(r'(\w+)\s*=\s*("(?:[^"\\]|\\.)*"|-?\d+)')
_PAIR = re.compile(r'`?(\w+)`?\s*:\s*("(?:[^"\\]|\\.)*"|-?\d+)')


def _proposal_from_prose(text: str, available: set[str]) -> dict[str, Any] | None:
    """A side-effect call the model spelled out in prose, if it is unambiguous and schema-valid.

    checkpoint_v31_139 was trained to show a side-effect call and ask before calling, and in the runtime it
    writes ``app_create_task(title="Buy milk")`` or ``{"title": "Buy milk"}`` as text rather than a
    <tool_call>. Only one named tool that needs approval, with arguments that pass its schema, qualifies;
    the result is still only a proposal.
    """
    names = {
        n
        for n in _TOOL_NAME.findall(text or "")
        if n in available and registry()[n].needs_confirmation
    }
    if len(names) != 1:
        return None
    name = names.pop()
    args: Any = None
    if m := re.search(r"\{[^{}]*\}", text):
        try:
            args = json.loads(m.group(0))
        except ValueError:
            args = None
    if args is None and (m := re.search(rf"{name}\s*\(([^)]*)\)", text)):
        try:
            args = {k: json.loads(v) for k, v in _KWARG.findall(m.group(1))} or None
        except ValueError:
            args = None
    if args is None:
        # The runtime also writes `title`: "Buy milk" lines. Only keys of this tool's own schema count, and a key
        # given two different values is ambiguous, so it is not a proposal.
        props = registry()[name].parameters.get("properties", {})
        found: dict[str, Any] = {}
        try:
            for k, v in _PAIR.findall(text):
                if k in props and found.setdefault(k, json.loads(v)) != json.loads(v):
                    return None
        except ValueError:
            return None
        args = found or None
    if not isinstance(args, dict) or validate_arguments(registry()[name], args):
        return None
    return {"name": name, "arguments": args}


#: Tool names the runtime model has invented, mapped to the real tool with the same arguments.
_ALIASES = {
    "fs_create_file": "workspace_create_file",
    "create_file": "workspace_create_file",
    "write_file": "fs_write_file",
    "read_file": "fs_read_file",
    "search_web": "web_search",
    "search": "web_search",
    "fetch_url": "web_fetch",
    "browse": "web_fetch",
}
#: "I can't search / don't have internet access / can't write a file" - each paired with the tool that disproves it.
_INABILITY = [
    (
        re.compile(
            r"\b(?:can't|cannot|can not|unable to|don't|do not)\b[^.?!]{0,30}\b(?:search|look (?:it |this )?up)",
            re.I,
        ),
        "web_search",
    ),
    (
        re.compile(
            r"\b(?:access to the internet|internet access|browse the (?:web|internet)|fetch \w+ data|access the (?:web|internet)|real-time (?:data|information|access)|up-to-date (?:data|information)|current (?:data|information|listings))\b",
            re.I,
        ),
        "web_fetch",
    ),
    (
        re.compile(
            r"\b(?:can't|cannot|unable to)\b[^.?!]{0,30}\b(?:write|save|create)\b[^.?!]{0,20}\bfiles?\b",
            re.I,
        ),
        "workspace_create_file",
    ),
]
_TOOLS_REMINDER = (
    "Correction: in this session you DO have tools for this. web_search searches Wikipedia, web_fetch reads a public "
    "web page by URL, and workspace_create_file / fs_write_file save text files (the user approves writes). If the "
    "request needs one, call it now with a <tool_call>; otherwise answer normally."
)


#: The user explicitly asked for a search ("hey can u search good movies for me"); group 1 is what follows the verb.
_SEARCH_ASKED = re.compile(
    r"\b(?:search(?:\s+(?:wikipedia|the web|the internet|online))?(?:\s+(?:for|about|on))?|look (?:it |this |that )?up"
    r"|lookup|google)\b(.*)",
    re.I | re.S,
)
#: "search my files / tasks / calendar" is a private search, never a web one.
_PRIVATE_SEARCH = re.compile(
    r"\bmy\s+(?:files?|folders?|tasks?|calendar|notes?|workspace|events?|goals?)\b", re.I
)
_FILLER = re.compile(r"(?:\s+(?:for me|please|pls|plz|thanks|thank you))*[\s.?!]*$", re.I)
#: "Create a file named notes.txt containing the text: hello" - name and content taken verbatim from the user.
_CREATE_ASKED = re.compile(
    r"\b(?:create|make|write|save)\s+(?:an?\s+)?(?:new\s+)?(?:text\s+)?file\s+(?:named|called)\s+[\"'`]?"
    r"([\w\-./]+\.\w{1,8})[\"'`]?\s*,?\s*(?:containing|with)(?:\s+the)?(?:\s+(?:text|content|contents|words?))?"
    r"\s*:?\s*(.+)$",
    re.I | re.S,
)


def _false_inability(text: str, available: set[str]) -> bool:
    """The reply claims it cannot do something that an attached tool does."""
    return any(tool in available and pat.search(text or "") for pat, tool in _INABILITY)


def _routed_call(convo: list[dict[str, Any]], available: set[str]) -> dict[str, Any] | None:
    """The one call the user's own words ask for, when the model failed to make it.

    Live, checkpoint_v31_139 refused "hey can u search good movies for me" even after the tools reminder,
    malformed the call for "...Look it up.", and wrote a file request as prose with an invented tool name and
    garbled content (Phase 6: over-refusal regressed on every checkpoint). Only two explicit asks qualify, and
    their arguments come verbatim from the user's message, never from the model. The call then takes the normal
    path: schema validation, and an approval card for the write.
    """
    said = next((m["content"] for m in reversed(convo) if m["role"] == "user"), "").strip()
    if "workspace_create_file" in available and (m := _CREATE_ASKED.search(said)):
        content = m.group(2).strip().strip("\"'`")
        if content:
            return {
                "name": "workspace_create_file",
                "arguments": {"path": m.group(1), "content": content},
            }
    if (
        "web_search" in available
        and not _PRIVATE_SEARCH.search(said)
        and (m := _SEARCH_ASKED.search(said))
    ):
        query = _FILLER.sub("", m.group(1)).strip(" :,") or _FILLER.sub(
            "", said[: m.start()]
        ).strip(" :,")
        if query:
            return {"name": "web_search", "arguments": {"query": query[:200]}}
    return None


def _unverified(call: dict[str, Any], convo: list[dict[str, Any]]) -> list[str]:
    """Text arguments that do not appear word for word in the user's latest message.

    The runtime model sometimes alters a value it copies (a title suffix became ``<PASSWORD>``, a path became
    ``movedoved.txt``). This only flags such values on the approval card; it never blocks or changes them.
    """
    said = _norm(next((m["content"] for m in reversed(convo) if m["role"] == "user"), ""))
    return [
        k
        for k, v in call["arguments"].items()
        if isinstance(v, str) and v.strip() and _norm(v) not in said
    ]


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _needs_confirmation(call: dict[str, Any], trace: list[TraceEntry]) -> bool:
    if registry()[call["name"]].needs_confirmation:
        return True
    return call["name"] in _EGRESS and any(
        t.tool in _PRIVATE_SOURCES and t.status in _OK for t in trace
    )


@dataclass
class TraceEntry:
    tool: str
    arguments: dict[str, Any]
    status: str  # executed_ok | executed_failed | partial | invalid_arguments | unsupported | pending_confirmation | denied | duplicate
    result: dict[str, Any] | None = None

    def public(self, withhold: bool) -> dict[str, Any]:
        """The client view; in a crisis conversation tool arguments, data and error text are withheld."""
        result, arguments = self.result, self.arguments
        if withhold:
            arguments = {"withheld": WITHHELD}
            if result and result.get("ok"):
                result = {"ok": True, "data": {"withheld": WITHHELD}}
            elif result:
                result = {
                    "ok": False,
                    "error": {"code": result["error"]["code"], "message": WITHHELD},
                }
        return {
            "tool": self.tool,
            "arguments": arguments,
            "status": self.status,
            "result": result,
        }


@dataclass
class TurnResult:
    """Everything one turn produced, before HTTP rendering."""

    outcome: str
    text: str
    trace: list[TraceEntry] = field(default_factory=list)
    proposal_id: str | None = None
    proposal: list[dict[str, Any]] | None = None
    guards: dict[str, Any] = field(default_factory=dict)
    steps: int = 0


@dataclass
class GuardFacts:
    """What the crisis guard may rely on for one turn."""

    texts: list[str]  # every user turn in the client-held history: may only make the guard stricter
    countries: set[str]  # stated in requests this server received, never taken from client history
    known_crisis: bool  # a disclosure this server saw recently, even if trimmed from history


class _UserFacts:
    """Server-side memory of the user's own statements, keyed by user id.

    Only the newest message of a request is recorded: earlier turns are client-held
    and could be forged. shortcut: process memory, like the confirmation store.
    """

    COUNTRY_SECONDS = 24 * 3600
    CRISIS_SECONDS = 3600

    def __init__(self) -> None:
        self._items: dict[str, tuple[set[str], float, float]] = {}

    def observe(self, user_id: str, history: list[str]) -> GuardFacts:
        now = time.monotonic()
        countries, c_until, k_until = self._items.get(user_id, (set(), 0.0, 0.0))
        if c_until < now:
            countries = set()
        current = history[-1:] if history else []
        said = crisis_guard.stated_countries(current, crisis_guard.load_rules())
        if said:
            countries, c_until = countries | said, now + self.COUNTRY_SECONDS
        if any(crisis_guard.is_crisis_text(t) for t in current):
            k_until = now + self.CRISIS_SECONDS
        self._items[user_id] = (countries, c_until, k_until)
        return GuardFacts(history, set(countries), k_until >= now)


user_facts = _UserFacts()


@dataclass
class _Pending:
    user_id: str
    expires: float
    convo: list[dict[str, Any]]
    calls: list[dict[str, Any]]
    trace: list[TraceEntry]
    users: GuardFacts
    executed: set[str]
    steps: int


class ConfirmationStore:
    """Single-use proposals. ``take`` removes the entry, so a proposal executes at most once.

    shortcut: process memory, so pending confirmations die with the process and are not shared
    between workers; move to the database before running more than one backend worker.
    """

    MAX = 1000
    PER_USER = 20

    def __init__(self) -> None:
        self._items: dict[str, _Pending] = {}
        self._lock = asyncio.Lock()

    async def put(self, item: _Pending) -> str | None:
        """Store a proposal and return its id, or None when the store is full.

        Expired entries go first; a user at their own cap loses their own oldest proposal, never anyone else's.
        """
        async with self._lock:
            now = time.monotonic()
            for k in [k for k, v in self._items.items() if v.expires < now]:
                del self._items[k]
            mine = [k for k, v in self._items.items() if v.user_id == item.user_id]
            if len(mine) >= self.PER_USER:
                del self._items[mine[0]]
            if len(self._items) >= self.MAX:
                return None
            pid = uuid.uuid4().hex
            self._items[pid] = item
            return pid

    async def take(self, pid: str, user_id: str) -> _Pending | None:
        """Remove and return the caller's live proposal, or None."""
        async with self._lock:
            item = self._items.get(pid)
            if item is None or item.user_id != user_id:
                return None  # another user's id is indistinguishable from an unknown one
            del self._items[pid]
            return item if item.expires >= time.monotonic() else None


confirmations = ConfirmationStore()


def _key(call: dict[str, Any]) -> str:
    return call["name"] + json.dumps(call["arguments"], sort_keys=True, ensure_ascii=False)


def _status_of(result: dict[str, Any]) -> str:
    if not result["ok"]:
        return "executed_failed"
    data = result.get("data")
    return "partial" if isinstance(data, dict) and data.get("truncated") else "executed_ok"


def _outcome(trace: list[TraceEntry], extra: set[str]) -> str:
    seen = set(extra)
    for t in trace:
        code = (
            (t.result or {}).get("error", {}).get("code")
            if t.result and not t.result.get("ok")
            else None
        )
        seen.add(
            {
                "unsupported": Outcome.TOOL_UNSUPPORTED,
                "invalid_arguments": Outcome.INVALID_TOOL_ARGUMENTS,
                "pending_confirmation": Outcome.CONFIRMATION_REQUIRED,
                "denied": Outcome.DENIED_BY_USER,
                "partial": Outcome.TOOL_PARTIAL,
                "executed_ok": Outcome.TOOL_COMPLETED,
                "duplicate": Outcome.TOOL_FAILED,
            }.get(t.status)
            or (
                Outcome.CLARIFICATION_REQUIRED
                if code == "ambiguous"
                else Outcome.INVALID_TOOL_ARGUMENTS
                if code == "invalid_arguments"
                else Outcome.SERVICE_UNAVAILABLE
                if code in ("unavailable", "connection_failed", "timeout")
                else Outcome.TOOL_FAILED
            )
        )
    return next(o for o in _PRIORITY if o in seen or o == Outcome.RESPONSE)


def _honest_summary(trace: list[TraceEntry]) -> str:
    if not trace:
        return "I haven't run any tool for this, so I can't confirm that anything was done."
    lines = []
    for t in trace:
        if t.status in ("executed_ok", "partial"):
            lines.append(
                f"{t.tool} ran successfully"
                + (" (partial result)" if t.status == "partial" else "")
            )
        else:
            msg = (t.result or {}).get("error", {}).get("message", t.status)
            lines.append(f"{t.tool} did not complete: {msg}")
    return "Here is what actually happened: " + "; ".join(lines) + "."


async def _execute_calls(
    calls: list[dict[str, Any]],
    ctx: ToolContext,
    trace: list[TraceEntry],
    executed: set[str],
    convo: list[dict[str, Any]],
) -> None:
    for call in calls:
        key = _key(call)
        if key in executed:
            result = err(
                "duplicate_call",
                "this exact call already ran in this conversation turn; it was not repeated",
            )
            trace.append(TraceEntry(call["name"], call["arguments"], "duplicate", result))
        else:
            executed.add(key)
            result = await run_tool(call["name"], call["arguments"], ctx)
            trace.append(TraceEntry(call["name"], call["arguments"], _status_of(result), result))
        convo.append(
            {
                "role": "tool",
                "name": call["name"],
                "content": json.dumps(result, separators=(",", ":"), ensure_ascii=False),
            }
        )


async def _loop(
    model: JarvisModelClient,
    ctx: ToolContext,
    user_id: str,
    convo: list[dict[str, Any]],
    users: GuardFacts,
    trace: list[TraceEntry],
    executed: set[str],
    steps: int,
) -> TurnResult:
    settings: Settings = ctx.settings
    specs = available_tools(settings)
    available = {s.name for s in specs}
    defs = tool_definitions(specs)
    extra: set[str] = set()
    text = ""
    nudged = routed = False
    while True:
        if steps >= settings.jarvis_max_steps:
            extra.add(Outcome.STEP_LIMIT)
            text = (
                _honest_summary(trace)
                + " I stopped before finishing because this request needed too many steps."
            )
            break
        steps += 1
        try:
            raw = await model.generate(convo, defs)
        except JarvisUnavailableError as exc:
            return _finish(
                TurnResult(
                    Outcome.MODEL_UNAVAILABLE,
                    f"NEXO is unavailable: {exc.reason}."
                    + (f" {_honest_summary(trace)}" if trace else ""),
                    trace,
                    steps=steps,
                ),
                users,
            )
        parsed = parse_turn(raw)
        if parsed.error and not trace and not routed and (call := _routed_call(convo, available)):
            routed, parsed = (
                True,
                ParsedTurn("", [call]),
            )  # malformed attempt at an explicit ask: make the ask
        if parsed.error:
            extra.add(Outcome.INVALID_TOOL_CALL)
            text = (
                f"I produced a malformed tool call ({parsed.error}), so nothing was executed. "
                + (_honest_summary(trace) if trace else "")
            )
            break
        if not parsed.calls and (prose := _proposal_from_prose(parsed.content, available)):
            # The model wrote a side-effect call out in prose instead of a <tool_call>. It is never run
            # from prose: it becomes a normal proposal the user must approve with these exact arguments.
            parsed = ParsedTurn(PROSE_PROPOSAL_TEXT, [prose])
        if (
            not parsed.calls
            and not trace
            and not routed
            and (call := _routed_call(convo, available))
        ):
            routed, parsed = True, ParsedTurn("", [call])
        if (
            not parsed.calls
            and not nudged
            and not trace
            and _false_inability(parsed.content, available)
        ):
            # The runtime model often says "I can't search / have no internet / can't write files" while those
            # tools are attached. Ask once more with the facts; whatever it answers then is final.
            nudged = True
            convo.append({"role": "system", "content": _TOOLS_REMINDER})
            continue
        if not parsed.calls:
            text = parsed.content
            break
        for c in parsed.calls:  # names the model invents for real tools, e.g. fs_create_file
            if c["name"] not in registry() and _ALIASES.get(c["name"]) in available:
                c["name"] = _ALIASES[c["name"]]
        convo.append(
            {
                "role": "assistant",
                "content": parsed.content,
                "tool_calls": [{"type": "function", "function": c} for c in parsed.calls],
            }
        )
        rejected: list[tuple[dict[str, Any], dict[str, Any], str]] = []
        for c in parsed.calls:
            if c["name"] not in registry():
                rejected.append(
                    (c, err("unknown_tool", f"there is no tool named {c['name']!r}"), "unsupported")
                )
            elif c["name"] not in available:
                rejected.append(
                    (
                        c,
                        err("unavailable", availability(c["name"], settings) or "not attached"),
                        "unsupported",
                    )
                )
            elif problems := validate_arguments(registry()[c["name"]], c["arguments"]):
                rejected.append(
                    (c, err("invalid_arguments", "; ".join(problems)), "invalid_arguments")
                )
        if rejected:  # nothing in a turn with a bad call runs; the model gets every error and may correct itself
            for c in parsed.calls:
                bad = next((r for r in rejected if r[0] is c), None)
                result, status = (
                    (bad[1], bad[2])
                    if bad
                    else (
                        err(
                            "not_executed",
                            "skipped because another call in the same turn was rejected",
                        ),
                        "invalid_arguments",
                    )
                )
                trace.append(TraceEntry(c["name"], c["arguments"], status, result))
                convo.append(
                    {
                        "role": "tool",
                        "name": c["name"],
                        "content": json.dumps(result, separators=(",", ":")),
                    }
                )
            continue
        if any(_needs_confirmation(c, trace) for c in parsed.calls):
            for c in parsed.calls:
                trace.append(TraceEntry(c["name"], c["arguments"], "pending_confirmation"))
            pid = await confirmations.put(
                _Pending(
                    user_id,
                    time.monotonic() + settings.jarvis_confirmation_ttl_seconds,
                    convo,
                    parsed.calls,
                    trace,
                    users,
                    executed,
                    steps,
                )
            )
            if pid is None:
                trace[-len(parsed.calls) :] = [
                    TraceEntry(
                        c["name"],
                        c["arguments"],
                        "executed_failed",
                        err("unavailable", "too many pending approvals; nothing was run"),
                    )
                    for c in parsed.calls
                ]
                return _finish(
                    TurnResult(
                        Outcome.SERVICE_UNAVAILABLE,
                        "I can't queue that action right now, so nothing "
                        "was done. Please try again shortly.",
                        trace,
                        steps=steps,
                    ),
                    users,
                )
            preface = parsed.content or "I'd like to run the following."
            return _finish(
                TurnResult(
                    Outcome.CONFIRMATION_REQUIRED,
                    preface + " Nothing has been done yet; please approve or decline.",
                    trace,
                    pid,
                    [{**c, "unverified": _unverified(c, convo)} for c in parsed.calls],
                    steps=steps,
                ),
                users,
            )
        await _execute_calls(parsed.calls, ctx, trace, executed, convo)
    return _finish(TurnResult(_outcome(trace, extra), text, trace, steps=steps), users)


def _failure_note(trace: list[TraceEntry]) -> str:
    """A fixed server sentence for every tool that ran and failed.

    The reply (and its spoken form) can never hide a failure behind a confident answer the model wrote
    around it. The reason stays in the trace only, where a crisis conversation withholds it.
    """
    return " ".join(f"[{t.tool} did not complete.]" for t in trace if t.status == "executed_failed")


def _finish(result: TurnResult, users: GuardFacts) -> TurnResult:
    honesty = "pass"
    if result.outcome != Outcome.CONFIRMATION_REQUIRED and _unsupported_claim(
        result.text, result.trace
    ):
        result.text = _honest_summary(result.trace)
        honesty = "replaced"
    elif (note := _failure_note(result.trace)) and "did not complete" not in result.text:
        result.text = f"{result.text}\n\n{note}".strip()
        honesty = "failure_noted"
    g = crisis_guard.check_response(
        result.text, users.texts, countries=users.countries, known_crisis=users.known_crisis
    )
    result.text = g.text
    result.guards = {
        "honesty": honesty,
        "crisis": g.action,
        "crisis_violations": crisis_guard.violation_categories(g.violations),
        "crisis_rules": g.rules_version,
    }
    if g.crisis_context:
        result.guards["tool_data_withheld"] = True
    logger.info(
        "jarvis_turn",
        extra={
            "outcome": result.outcome,
            "steps": result.steps,
            "crisis": g.action,
            "honesty": honesty,
            "tools": [t.tool for t in result.trace],
        },
    )
    return result


async def run_turn(
    model: JarvisModelClient, ctx: ToolContext, user_id: str, messages: list[dict[str, str]]
) -> TurnResult:
    """Run one turn for a client-held conversation of user/assistant messages."""
    users = user_facts.observe(user_id, [m["content"] for m in messages if m["role"] == "user"])
    convo = [{"role": "system", "content": SYSTEM_PROMPT}, *messages]
    return await _loop(model, ctx, user_id, convo, users, [], set(), 0)


async def resume_turn(
    model: JarvisModelClient, ctx: ToolContext, user_id: str, proposal_id: str, approve: bool
) -> TurnResult | None:
    """Run (or decline) a pending proposal exactly once, then let the model answer from the results."""
    item = await confirmations.take(proposal_id, user_id)
    if item is None:
        return None
    item.trace = [t for t in item.trace if t.status != "pending_confirmation"]
    if approve:
        await _execute_calls(item.calls, ctx, item.trace, item.executed, item.convo)
    else:
        for c in item.calls:
            result = err("denied_by_user", "the user declined this call; it was not executed")
            item.trace.append(TraceEntry(c["name"], c["arguments"], "denied", result))
            item.convo.append(
                {
                    "role": "tool",
                    "name": c["name"],
                    "content": json.dumps(result, separators=(",", ":")),
                }
            )
    return await _loop(
        model, ctx, user_id, item.convo, item.users, item.trace, item.executed, item.steps
    )
