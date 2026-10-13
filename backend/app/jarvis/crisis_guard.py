"""Runtime crisis-response safeguard for Jarvis output.

This runs on every piece of text Jarvis is about to deliver, after generation and
after any tool use, and before the user sees it. It does not make the model any
better: Phase 6 showed that checkpoint_v31_139 still lists country-specific
hotline numbers (one of them wrong, one outdated) when the user never said where
they are. The guard stops those answers from reaching the user; it is reported
separately from model quality and never counts towards a model acceptance score.

Rules (``crisis_rules.json``, the project's audited allowlist):

* The guard is active when the conversation is a crisis conversation (the user's
  own words), or when the response itself offers crisis resources.
* In an active conversation every phone-like number must be on the allowlist
  *and* belong to a country the user stated. Country is never inferred from a
  name, the language, the timezone or anything else - only from what the user
  said.
* Every URL or e-mail address must be on the URL allowlist.
* Any claim that Jarvis called, contacted, notified or connected someone is
  rejected: Jarvis has no tool that can do that.
* A violating response is replaced as a whole by a truthful, supportive fallback.
  Numbers are never cut out of the middle of a sentence, which could leave an
  incoherent or misleading answer.

There is deliberately no ``enabled`` flag. The rules are data and can be edited,
but the normal execution path cannot skip the check.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import pairwise
from pathlib import Path
from typing import Any

__all__ = ["CrisisRules", "GuardResult", "check_response", "is_crisis_text", "load_rules"]

RULES_PATH = Path(__file__).with_name("crisis_rules.json")

#: The user's own words that make a conversation a crisis conversation. Kept to
#: unambiguous self-harm and suicide phrasing so ordinary frustration ("this bug
#: is hopeless") does not trip it; the response-side trigger below covers the rest.
_USER_CRISIS = re.compile(
    r"suicid|kill(ing)? (myself|me)\b|end(ing)? (it all|my life|things)|want(ed)? to die|wanna die|"
    r"(want|wanna|going) to end it\b|"
    r"don'?t want to (be alive|live|exist|wake up)|(hurt|harm|cut)(ing|ting)? myself|self[- ]?harm|"
    r"take all (of )?my (pills|meds|medication)|\b(take|taking|took) an overdose|\boverdos(e|ing) on (my|all)|"
    r"no reason to live|better off dead|"
    r"want (it|everything) (all )?to (end|stop)|can'?t go on",
    re.I,
)
#: A response that offers crisis resources is checked even if the user's wording
#: was not caught above. "Emergency" alone is not enough: a benign answer about
#: the emergency number in Germany must not be rewritten.
_RESPONSE_CRISIS = re.compile(
    r"crisis (line|hotline|text|lifeline)|suicide|self[- ]harm|lifeline|samaritans|"
    r"\bhotline\b|helpline|findahelpline|\b988\b|116 ?123|1[- ]?800[- ]?273|741741",
    re.I,
)
#: Separators a model (or a tool page) may put inside a phone number. Applied to
#: the normalized text, so non-breaking spaces and full-width digits are covered.
_PHONE = re.compile(r"(?<!\d)\+?\d[\d \-().]{1,20}\d(?![\d%])")  # no "/": "24/7" is not a number
_DESCENDING = re.compile(r"^\d(?:\s*[-,]\s*\d)+$")  # "5-4-3-2-1" grounding steps, see _is_grounding
_URL = re.compile(r"\b(?:https?://)?(?:www\.)?((?:[a-z0-9-]+\.)+[a-z]{2,})(?![a-z0-9-])", re.I)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_CLAIM = re.compile(
    r"\bI(?:'ve| have| just| already)? (?:called|phoned|dialled|dialed|texted|contacted|alerted|notified|"
    r"informed|messaged|reached out to|connected you|sent (?:a|an|your))\b"
    r"|\bI(?: can|'ll| will|'m going to| am going to)? (?:help you )?(?:call|phone|dial|text|contact|reach|"
    r"alert|notify|message|connect you|get you connected|send (?:help|someone|an alert))\b"
    r"|\bI(?:'m| am) (?:calling|phoning|contacting|connecting you|notifying|alerting|sending help|reaching out|"
    r"getting (?:you )?help)\b"
    r"|\bI(?:'ve| have| just)? (?:let|told) (?:them|someone|somebody|your [a-z]+|the [a-z]+) know\b"
    r"|\b(?:help|emergency services|an ambulance|the police|someone|they) (?:is|are|has been|have been) "
    r"(?:on (?:the|their) way|notified|alerted|called|contacted|dispatched)\b",
    re.I,
)
_ZERO_WIDTH = re.compile("[​-‍⁠﻿­]")
_DIGIT_WORDS = {
    "zero": "0",
    "oh": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}
_DIGIT_ALT = "|".join(_DIGIT_WORDS)
_WORD_RUN = re.compile(rf"\b(?:(?:{_DIGIT_ALT})[\s-]+){{2,}}(?:{_DIGIT_ALT})\b", re.I)
#: The user must say where *they* are ("I'm in", "I live in", "based in"), not
#: where someone else is ("my sister lives in the US").
_SELF_PLACE = re.compile(
    r"(?:\bI(?:['\u2019]m|\s+am)\b|\bI live\b|\bI'?ve moved\b|\bliving\b|\bbased\b|\blocated\b|\bhere\b)"
    r"[^.!?;]{0,20}$",
    re.I,
)


def _normalize(text: str) -> str:
    """Undo the spellings a number can hide behind: unicode forms, invisible characters, digit words."""
    t = _ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", text))
    return _WORD_RUN.sub(
        lambda m: (
            " " + "".join(_DIGIT_WORDS[w.lower()] for w in re.split(r"[\s-]+", m.group(0))) + " "
        ),
        t,
    )


def _is_grounding(raw: str, digits: str) -> bool:
    """A strictly descending run such as 5-4-3-2-1 is an exercise, not a number."""
    return (
        bool(_DESCENDING.match(raw))
        and len(digits) >= 3
        and all(int(a) - int(b) == 1 for a, b in pairwise(digits))
    )


@dataclass(frozen=True)
class CrisisRules:
    """The audited allowlist, compiled once."""

    version: str
    numbers: Mapping[str, frozenset[str]]
    urls: frozenset[str]
    countries: Mapping[str, re.Pattern[str]]
    country_resources: Mapping[str, str]


@dataclass(frozen=True)
class GuardResult:
    """What the guard decided about one response.

    ``action`` is ``"pass"`` (delivered unchanged), ``"inactive"`` (not a crisis
    conversation, delivered unchanged) or ``"replaced"`` (the fallback was
    delivered instead; ``violations`` says why).
    """

    text: str
    action: str
    crisis_context: bool
    violations: tuple[str, ...] = field(default_factory=tuple)
    countries: tuple[str, ...] = field(default_factory=tuple)
    rules_version: str = ""


@lru_cache(maxsize=4)
def load_rules(path: str | None = None) -> CrisisRules:
    """Load and validate the rules file; a malformed file raises at start-up."""
    raw: dict[str, Any] = json.loads(Path(path or RULES_PATH).read_text(encoding="utf-8"))
    countries = {code: re.compile(rx) for code, rx in raw["countries"].items()}
    numbers = {str(n): frozenset(c) for n, c in raw["numbers"].items()}
    for n, cs in numbers.items():
        if not n.isdigit() or not cs <= countries.keys():
            raise ValueError(f"crisis rules: bad number entry {n!r} -> {sorted(cs)}")
    resources = dict(raw.get("country_resources", {}))
    if not resources.keys() <= countries.keys():
        raise ValueError("crisis rules: country_resources names an unknown country")
    return CrisisRules(
        version=raw["version"],
        numbers=numbers,
        urls=frozenset(u.lower() for u in raw["urls"]),
        countries=countries,
        country_resources=resources,
    )


def is_crisis_text(text: str) -> bool:
    """True when the user's own words are unambiguous self-harm or suicide phrasing."""
    return bool(text) and _USER_CRISIS.search(text) is not None


def stated_countries(user_texts: Iterable[str], rules: CrisisRules) -> set[str]:
    """Countries the user named in their own messages - nothing else counts."""
    said = set()
    for t in (_normalize(x) for x in user_texts if x):
        short = len(t.split()) <= 4  # a bare answer such as "UK" to "where are you?"
        for code, rx in rules.countries.items():
            if any(short or _SELF_PLACE.search(t[: m.start()]) for m in rx.finditer(t)):
                said.add(code)
    return said


def _violations(text: str, said: set[str], rules: CrisisRules) -> list[str]:
    bad: list[str] = []
    text = _normalize(text)
    for match in _PHONE.finditer(text):
        raw = match.group(0).strip()
        digits = re.sub(r"\D", "", raw)
        if (
            len(digits) < 3
            or _is_grounding(raw, digits)
            or (len(digits) == 4 and re.fullmatch(r"(19|20)\d\d", digits))
        ):
            continue  # counts, years and grounding steps are not contact numbers
        allowed_in = rules.numbers.get(digits)
        if allowed_in is None:
            bad.append(f"unapproved number {raw}")
        elif not (said & allowed_in):
            bad.append(
                f"number {raw} given but the user's country ({'/'.join(sorted(allowed_in))}) is not established"
            )
    for domain in _URL.findall(text):
        if domain.lower() not in rules.urls:
            bad.append(f"unapproved url {domain}")
    bad += [f"unapproved email {e}" for e in _EMAIL.findall(text)]
    claim = _CLAIM.search(text)
    if claim:
        bad.append(f"unsupported contact claim {claim.group(0)!r}")
    return bad


def fallback_text(said: set[str], rules: CrisisRules) -> str:
    """The supportive, truthful answer delivered instead of a violating one."""
    parts = [
        "I'm really sorry you're going through this, and I'm glad you told me.",
        "If you might act on these thoughts or you are in immediate danger, please contact your local "
        "emergency number now or go to the nearest emergency department.",
    ]
    parts += [rules.country_resources[c] for c in sorted(said) if c in rules.country_resources]
    parts += [
        "findahelpline.com lists free, confidential crisis lines for your country.",
        "If you can, reach out to someone you trust and stay near them.",
        "I can't call or contact anyone for you, but I'm here to listen. Would you like to tell me what's been happening?",
    ]
    return " ".join(parts)


def check_response(
    response: object,
    user_texts: Iterable[str],
    *,
    rules: CrisisRules | None = None,
    countries: Iterable[str] | None = None,
    known_crisis: bool = False,
) -> GuardResult:
    """Validate one generated response against the crisis rules.

    ``user_texts`` are the user's own messages in this conversation (never tool
    output, never the system prompt): they decide whether this is a crisis
    conversation and, unless ``countries`` is given, which country has been established.

    ``countries`` overrides the country evidence. The integrated flow passes only
    countries the user stated in requests the server itself received, so a forged
    client-held history can make the guard stricter but never unlock a number.
    ``known_crisis`` is server-side memory of a recent disclosure: it keeps every
    number and URL check active after the disclosure has been trimmed from history.
    """
    rules = rules or load_rules()
    users = [t for t in user_texts if isinstance(t, str)]
    text = response if isinstance(response, str) else ""
    user_crisis = any(is_crisis_text(t) for t in users)
    active = user_crisis or known_crisis or bool(_RESPONSE_CRISIS.search(_normalize(text)))
    said = stated_countries(users, rules) if countries is None else set(countries)
    countries = tuple(sorted(said))
    if not active:
        return GuardResult(text, "inactive", False, (), countries, rules.version)
    bad = [] if text.strip() else ["empty or malformed response"]
    bad += _violations(text, said, rules)
    if user_crisis and text.strip() and not bad and not _SUPPORT.search(text):
        bad.append("crisis conversation without supportive guidance")  # e.g. a tool error message
    if bad:
        return GuardResult(
            fallback_text(said, rules), "replaced", True, tuple(bad), countries, rules.version
        )
    return GuardResult(text, "pass", True, (), countries, rules.version)


#: A reply to someone in crisis must at least point to people or urgent help.
_SUPPORT = re.compile(
    r"emergency|crisis (line|hotline|service)|helpline|lifeline|samaritans|findahelpline|counsell?or|doctor|"
    r"therapist|someone you trust|trusted person|\b(988|911|999|112)\b|116 123",
    re.I,
)

_CATEGORIES = (
    ("unapproved number", "unapproved_number"),
    ("number ", "country_not_established"),
    ("unapproved url", "unapproved_url"),
    ("unapproved email", "unapproved_email"),
    ("unsupported contact claim", "contact_claim"),
    ("empty", "empty_response"),
    ("crisis conversation without", "unsupportive_response"),
)


def violation_categories(violations: Iterable[str]) -> list[str]:
    """What may be shown to a client: the kind of violation, never the rejected number or URL."""
    return sorted({next(c for p, c in _CATEGORIES if v.startswith(p)) for v in violations})
