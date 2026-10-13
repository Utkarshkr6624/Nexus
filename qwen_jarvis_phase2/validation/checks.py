"""Validation checks for the Phase 2 dataset. Importable; also used by validate_final.py.
Each check returns a list of problem strings (empty list = pass)."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from nexus_facts import FORBIDDEN_CLAIMS  # noqa: E402

ROLES = {"system", "user", "assistant"}
NEG = re.compile(r"\b(not|no|never|none|n't|cannot|can't|couldn't|won't|wouldn't|didn't|haven't|hasn't|isn't|doesn't|don't|unable|without|nothing|neither|nor|unless|if|whether|would|could|might|may)\b|n't", re.I)
PLAN = re.compile(r"\b(plan|planned|future|roadmap|later|eventually|will|would|intend|proposed|propose|yet|goal|target|aim|aims|rejected|refused|refuse|deliberately|absent|myth|false|claim|claims|stated|says|said|readme|mentions|mentioned|aspir|hypothetical)\b", re.I)
PLACEHOLDER = re.compile(r"\{[a-z_]{1,20}\}|lorem ipsum", re.I)
PLACEHOLDER_CS = re.compile(r"\b(TODO|FIXME)\b")
LEAK = re.compile(r"\b(subcategory|source_type|validation_status|\"category\")\b", re.I)
# first-person claims of having performed an action
ACTION_CLAIM = re.compile(r"\bI(?:'ve| have)?\s+(?:just\s+)?(saved|sent|created|deleted|removed|ran|executed|booked|opened|searched|downloaded|installed|updated|fixed|checked|looked up|scanned|emailed|posted|scheduled|backed up|restarted)\b", re.I)
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

NEXUS_ENTITIES = re.compile(r"\b(ollama|whisper|qwen|llm|language model|web search|internet access|browse the web|chat endpoint|sqlite)\b", re.I)
ALLOWED_PATHS = {"/api/v1", "/ml", "/ml/status", "/ml/route", "/ml/action/propose", "/ml/action/confirm", "/assistant", "/api/v1/ml/route", "/api/v1/ml/status",
                 "/api/v1/ml/action/propose", "/api/v1/ml/action/confirm"}


def structural(rec):
    p = []
    m = rec.get("messages")
    if not isinstance(m, list) or len(m) < 2:
        return ["messages missing or too short"]
    roles = [x.get("role") for x in m]
    if any(r not in ROLES for r in roles):
        p.append(f"bad role: {roles}")
        return p
    body = [x for x in m if x["role"] != "system"]
    if roles.count("system") > 1 or (roles.count("system") == 1 and roles[0] != "system"):
        p.append("system message must be single and first")
    if not body or body[0]["role"] != "user":
        p.append("first non-system turn must be user")
    if body and body[-1]["role"] != "assistant":
        p.append("last turn must be assistant")
    for i, x in enumerate(body):
        exp = "user" if i % 2 == 0 else "assistant"
        if x["role"] != exp:
            p.append("roles do not alternate")
            break
    for x in m:
        if not isinstance(x.get("content"), str) or not x["content"].strip():
            p.append("empty content")
            break
    return p


def quality(rec):
    p = []
    cat = rec["category"]
    for x in rec["messages"]:
        if x["role"] == "assistant":
            c = x["content"]
            if len(c.strip()) < 1:
                p.append("empty assistant")
            elif len(c.strip()) < 2 and cat not in ("instruction_following", "mathematics"):
                p.append("assistant answer too short")
            body = re.sub(r"`[^`\n]*`", "", re.sub(r"```.*?```", "", c, flags=re.S))
            if PLACEHOLDER.search(body) or PLACEHOLDER_CS.search(body):
                p.append("unresolved placeholder")
            if LEAK.search(c):
                p.append("metadata leakage in assistant text")
            if c.count("```") % 2:
                p.append("unbalanced code fence")
    last = rec["messages"][-1]["content"]
    if len(last) > 6000:
        p.append("assistant answer too long")
    return p


def _sentences(text):
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    return [s for s in SENT_SPLIT.split(text) if s.strip()]


def fabricated_actions(rec):
    """Assistant must not claim to have performed an action unless the conversation contains an actual tool result."""
    msgs = rec["messages"]
    has_result = any(x["role"] == "user" and x["content"].startswith("[tool result") for x in msgs)
    if has_result:
        return []
    p = []
    for x in msgs:
        if x["role"] != "assistant":
            continue
        for s in _sentences(x["content"]):
            if ACTION_CLAIM.search(s) and not NEG.search(s):
                p.append("possible fabricated action claim: " + s[:90])
    return p


def nexus_claims(rec):
    """Facts about NEXUS must not be asserted when the Phase 1 report says they are absent or planned."""
    p = []
    for x in rec["messages"]:
        if x["role"] != "assistant":
            continue
        low = x["content"].lower()
        for s in _sentences(x["content"]):
            sl = s.lower()
            for bad in FORBIDDEN_CLAIMS:
                if bad in sl and not NEG.search(s):
                    p.append("forbidden NEXUS claim: " + bad)
            if "nexus" in sl and NEXUS_ENTITIES.search(s) and not (NEG.search(s) or PLAN.search(s)):
                p.append("NEXUS entity mentioned without negation/planned marker: " + s[:100])
        for m in re.findall(r"(?<![\w/.\-])(/(?:api/v1/)?(?:ml|assistant)(?:/[a-z_]+)*)(?![\w\-])", x["content"]):
            if m not in ALLOWED_PATHS:
                p.append("unknown NEXUS path: " + m)
    return p


def tool_awareness(rec):
    p = []
    sysm = next((x["content"] for x in rec["messages"] if x["role"] == "system"), "")
    none_ctx = bool(re.search(r"(Tools connected in this session: none|Runtime capabilities this session: none|No tools are attached|no tool access)", sysm))
    asst = " ".join(x["content"] for x in rec["messages"] if x["role"] == "assistant")
    if none_ctx:
        if "Proposed call" in asst or re.search(r"I'll use `[a-z_]+\.[a-z_]+`", asst):
            p.append("proposes a tool call although no tools are attached")
    return p


def boundaries(rec):
    if rec["category"] != "capability_boundaries":
        return []
    asst = rec["messages"][-1]["content"]
    if not re.search(r"(n't|\bnot\b|\bcannot\b|\bunable\b|\bno\b|\bnone\b|\bnever\b|failed|error|timed out)", asst, re.I):
        return ["capability-boundary answer lacks an explicit limitation/failure statement"]
    return []


def turn_counts(rec):
    return sum(1 for x in rec["messages"] if x["role"] == "user")


ALL = [structural, quality, fabricated_actions, nexus_claims, tool_awareness, boundaries]
