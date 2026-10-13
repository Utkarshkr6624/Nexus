"""NEXUS factual-accuracy rules (deterministic).

Source of truth: Phase 1 report -> qwen_jarvis_phase2/scripts/nexus_facts.py (status C = implemented today, P = planned, N = absent).
The sentence-level negation / planned-marker logic mirrors qwen_jarvis_phase2/validation/checks.py::nexus_claims (copied, not imported, so this
package is self-contained) and is slightly stricter: it also flags 'it uses Ollama'-style sentences that do not repeat the word NEXUS.
"""
import re

from .text_utils import contains_phrase, remove_echo

NEG = re.compile(r"\b(not|no|never|none|n't|cannot|can't|couldn't|won't|wouldn't|didn't|haven't|hasn't|isn't|doesn't|don't|unable|without|nothing|neither|nor|unless|if|whether|would|could|might|may|exclud\w+|ruled out|lacks?|lacking|absent|forbid\w*|banned|removed)\b|n't", re.I)
PLAN = re.compile(r"\b(plan|planned|planning|future|roadmap|later|eventually|will|would|intend|proposed|propose|yet|goal|target|aim|aims|rejected|refused|deliberately|absent|myth|false|claim|claims|stated|says|said|readme|mentions|mentioned|aspir|hypothetical|undecided)\b", re.I)
ENTITIES = re.compile(r"\b(ollama|whisper|qwen|llm|language model|web search|internet access|browse the web|chat endpoint|sqlite)\b", re.I)
AFFIRM = re.compile(r"\b(uses?|using|runs?|running|has|have|supports?|integrat\w+|powered by|built (?:on|with)|connected to|includes?|ships? with)\b", re.I)
ABSENCE = re.compile(r"\b(no|not|none|never|nothing|without|absent|neither|nor|isn't|doesn't|cannot|can't|hasn't|haven't|aren't|don't|didn't|unavailable)\b|n't", re.I)
PLANNED = re.compile(r"\b(plan|planned|planning|future|roadmap|will|would|intend|intended|not yet|undecided|eventually|later|proposed|probably|aim|goal|target|upcoming|next phase)\b", re.I)
SENT = re.compile(r"(?<=[.!?])\s+|\n+")

FORBIDDEN_CLAIMS = [
    "nexus uses ollama", "nexus has ollama", "nexus uses whisper", "nexus runs qwen", "qwen is integrated", "qwen is running in nexus",
    "nexus can browse the web", "nexus can search the web", "nexus has a chat endpoint", "nexus uses sqlite", "nexus can read your files", "nexus can run terminal",
    # eval-only extras
    "nexus currently has a chat", "nexus has a chat", "nexus supports chat", "nexus has an llm", "nexus has a language model", "nexus has a tool registry",
    "nexus has a tool system", "nexus has web search", "nexus uses qwen", "nexus is powered by qwen", "nexus runs ollama", "qwen has been trained",
    "qwen is already", "nexus has already", "nexus already uses", "nexus is already powered", "qwen is now integrated", "qwen is currently integrated",
]


def _sentences(text):
    t = re.sub(r"```.*?```", " ", text, flags=re.S)
    return [s for s in SENT.split(t) if s.strip()]


def forbidden_claims(output: str):
    """Sentences that assert a thing the Phase 1 audit says does not exist (or is only planned). Empty list = clean."""
    bad = []
    for s in _sentences(output):
        sl = s.lower()
        for claim in FORBIDDEN_CLAIMS:
            if claim in sl and not NEG.search(s):
                bad.append(f"forbidden claim: {claim}")
        if ENTITIES.search(s) and not (NEG.search(s) or PLAN.search(s)):
            if "nexus" in sl or AFFIRM.search(s):
                bad.append("unqualified claim about an entity that is absent/planned: " + s.strip()[:90])
    return bad


def check_fact(output: str, spec: dict, question: str = ""):
    """spec = {groups:[(min_hits,[alternatives])...], neg:bool, plan:bool}. Returns (passed, detail)."""
    problems = forbidden_claims(output)
    cleaned = remove_echo(output, question) if question else output      # copying the question back must not satisfy a keyword
    for min_hits, alts in spec.get("groups", []):
        hits = sum(1 for a in alts if contains_phrase(cleaned, a))
        if hits < min_hits:
            problems.append(f"needs >= {min_hits} of {alts[:4]}..., found {hits}")
    if spec.get("neg") and not ABSENCE.search(cleaned):
        problems.append("expected an absence/negation statement")
    if spec.get("plan") and not PLANNED.search(cleaned):
        problems.append("expected 'planned / not yet' wording")
    return (not problems, "; ".join(problems[:3]) or "ok")


def G(*groups, neg=False, plan=False):
    return {"groups": list(groups), "neg": neg, "plan": plan}


# per-fact required content (id -> spec). Every spec is validated against the fact's own authored answer when the suite is built.
FACT_SPECS = {
    "what_is": G((1, ["personal intelligence", "decision platform", "full-stack", "full stack"])),
    "phases": G((1, ["14", "fourteen"])),
    "modules": G((3, ["task", "project", "planner", "knowledge", "analytics", "learning", "career", "risk"])),
    "frontend": G((1, ["react"]), (1, ["typescript", "vite", "tailwind"])),
    "backend": G((1, ["fastapi"]), (1, ["python", "sqlalchemy", "postgresql", "postgres", "alembic", "pydantic"])),
    "api_versioning": G((1, ["/api/v1", "v1", "versioned"])),
    "frontend_backend": G((1, ["rest", "/api/v1", "bearer", "proxy", "http"])),
    "database": G((1, ["postgres", "postgresql"])),
    "auth": G((2, ["jwt", "refresh", "role", "rate limit", "rate-limit", "audit", "password", "session"])),
    "ml_model": G((1, ["deberta"]), (1, ["classifier", "classification", "intent"])),
    "intent_list": G((3, ["task_manage", "project_manage", "schedule_plan", "knowledge_capture", "knowledge_lookup", "analytics_insight", "risk_query", "developer_intel", "learning_track", "career_track", "account_admin", "code_assist", "deep_reasoning"])),
    "classifier_accuracy": G((1, ["0.974", "97.4", "97%"]), (1, ["42", "75%", "56", "hand-written", "handwritten", "natural"])),
    "confidence_threshold": G((1, ["0.90", "0.9", "90%"])),
    "ml_endpoints": G((1, ["ml/route", "ml/status", "ml/action"])),
    "generation_unavailable": G((1, ["generation_unavailable", "code_assist", "unavailable", "no generative", "no language model", "not available"])),
    "propose_confirm": G((1, ["propos*"]), (1, ["confirm*"])),
    "slot_extraction": G((1, ["parser", "hand-written", "handwritten", "rule", "regex", "not a model", "not a language model"])),
    "voice_stt": G((1, ["web speech", "browser"])),
    "voice_privacy": G((1, ["not necessarily", "google", "microsoft", "browser", "transcribed"])),
    "voice_tts": G((1, ["speechsynthesis", "speech synthesis", "browser", "operating system", "os voice"])),
    "assistant_page": G((1, ["classif*", "rout*", "intent"])),
    "voice_scope": G((1, ["rout*", "classif*", "area", "intent", "interface"]), neg=True),
    "search": G((2, ["project", "task", "note", "resource", "concept", "repositor*", "learning", "skill", "calendar", "risk"])),
    "palette": G((1, ["ctrl+k", "cmd+k", "ctrl + k", "launcher", "keyboard"])),
    "command_center": G((1, ["priorit*", "recommend*", "attention", "signal", "next", "work on"])),
    "developer_module": G((1, ["git", "repositor*", "commit"])),
    "learning_career": G((1, ["goal", "skill", "portfolio", "profile", "gap"])),
    "risks": G((1, ["detect*", "score", "evidence", "recommendation", "signal", "deadline"])),
    "planner": G((1, ["weekly", "availability", "time block", "focus", "schedul*"])),
    "design_principles": G((2, ["local", "deterministic", "modul*", "separate", "knows", "estimat*", "confirm*", "security", "principles", "six", "evidence", "purpose"])),
    "one_model_rule": G((1, ["one model", "single model", "local", "explainable", "measurable"])),
    "tests": G((1, ["pytest", "vitest"])),
    "ml_runtime": G((1, ["lazy", "lazily", "503", "ml_unavailable", "once", "missing", "disabled"])),
    "history_not_sent": G((1, ["only the current", "current utterance", "browser", "20", "no dialogue memory", "no memory"]), neg=True),
    "no_llm": G((1, ["language model", "llm", "chat", "generative", "deberta", "classifier"]), neg=True),
    "no_ollama": G((1, ["ollama"]), neg=True),
    "no_web": G((1, ["web", "internet", "browse", "search"]), neg=True),
    "no_tools": G((1, ["tool", "registry", "file", "terminal", "browser"]), neg=True),
    "no_chat_ui": G((1, ["chat", "/assistant", "routing", "rout*"]), neg=True),
    "no_server_voice": G((1, ["browser", "text", "client", "server", "backend"]), neg=True),
    "qwen_not_trained": G((1, ["qwen", "planned", "trained", "integrated"]), neg=True),
    "qwen_plan": G((1, ["qwen*"]), (1, ["fine-tun*", "generative", "brain", "single", "tools", "conversation"]), plan=True),
    "brain_body": G((1, ["brain", "reason", "decid*"]), (1, ["tool", "service", "data", "body"])),
    "training_not_access": G((1, ["tool", "runtime", "access"]), neg=True),
    "kaggle_plan": G((1, ["kaggle", "qlora", "gpu", "3050", "4 gb", "4gb"]), plan=True),
    "isolation": G((1, ["isolat*", "separate", "not modified", "workspace", "untouched", "not touched", "own director*"])),
    "roadmap": G((2, ["specification", "dataset", "tool", "training", "evaluation", "integration", "voice", "jarvis", "phase"])),
    "evaluation_gate": G((1, ["compare", "base", "evaluation", "integration does not", "fail"])),
    "tool_layer_plan": G((1, ["registry", "executor", "schema", "permission", "confirm"]), plan=True),
    "serving_plan": G((1, ["separate", "process", "endpoint", "cpu", "in-process"]), plan=True),
    "deberta_future": G((1, ["undecided", "kept", "router", "decid*", "not decided", "after"])),
    "voice_future": G((1, ["transcript", "chat endpoint", "speak", "browser"]), plan=True),
    "local_inference": G((1, ["slow", "4 gb", "4gb", "offload", "5 gb", "cpu", "measure"])),
    "dataset_phase": G((1, ["fine-tun*", "supervised", "dataset", "training data", "sft"])),
    "kaggle_not_used": G((1, ["training", "kaggle", "gpu"]), neg=True),
}

# false-premise probes only need a correction + no endorsement
FALSE_PREMISE_SPEC = G((1, ["not", "no", "n't", "there is no", "planned", "readme"]), neg=True)
