"""The labelled dataset the small routing classifier trains on.

**These rows are SYNTHETIC.** Every ``text`` here was produced by a template in
this file, deterministically, from ``random.Random(seed)``. None of it is a
transcript, because there is nothing to transcribe: NEXUS is a single-user
personal system, so no corpus of real user utterances exists to sample from.
What the module produces instead is *controlled* linguistic coverage over the real
capability vocabulary — the fourteen intents, the twelve recommendation types,
the seven risk types, the eleven permissions and the activity events, filtered
against a harvested :class:`~ml.datasets.capabilities.CapabilityInventory` when
the caller supplies one. Every row carries
``provenance=Provenance.SYNTHETIC`` because that is exactly what it is, and a
reader who cannot tell template output from observed speech will read a
confidence the data does not contain.

**It teaches routing, not capability.** A row answers one question: *which of the
fourteen classes is this utterance?* It says nothing about whether the answering
surface exists (that is :mod:`ml.datasets.capabilities`), how the answer is
computed (Phase 8/9's deterministic scorers), or whether the answer will be
right. Generated text proxies for the distribution of phrasing; it is not
evidence about the user, and a router fitted on it belongs in its run manifest
described as a template-trained baseline rather than as a model of real demand.

**The two large-model classes exist so trivial requests never reach the 8B
model.** ``code_assist`` and ``deep_reasoning`` are the only two intents whose
destination would be a generation model, and they are correspondingly
*heavy* here: multi-step,
technical, design-argument utterances. Widening them to include trivia — "what
is 2 + 2", "fix my typo" — would pay generation latency for an answer a router
could already give, which is exactly the failure the standing rule exists to
prevent: *"deterministic before learned… the deterministic engine stays as the
fallback."* ``out_of_scope`` is the mirror of that: weather, sport, travel, small
talk, politics and near-miss non-NEXUS asks, so abstention is a class the model
can be *right* about rather than a row that was quietly dropped.

**Adjacent intents are kept genuinely separable, because that is where a template
corpus usually cheats.** The generator never reuses a text across intents —
every candidate is checked globally against everything emitted so far via
:func:`~ml.preprocessing.normalize.normalize_text` — and each intent gets its own
template families:

* ``task_manage`` changes the task; ``schedule_plan`` places work in time;
  ``project_manage`` changes the container both live in.
* ``knowledge_capture`` writes into the knowledge base; ``knowledge_lookup``
  reads back out of it.
* ``analytics_insight`` reports measured history; ``risk_query`` reports what is
  about to go wrong.
* ``learning_track`` is study against a goal; ``career_track`` is standing.

**Per-intent RNG, so edits do not cascade.** Each intent draws from
``random.Random(seed * 1000003 + index_of_intent)``, so adding a template to one
intent cannot reshuffle another intent's rows and a dataset hash changes only in
the segment that actually changed. Balance is exact by construction:
:class:`BuildStats` reports the same count for all fourteen intents or the builder
has failed.

Stdlib only. It never reads ``~/.kaggle/access_token`` or any other credential,
and it never imports the application — the capability vocabulary arrives as a
:class:`~ml.datasets.capabilities.CapabilityInventory` or falls back to the
literals declared here.
"""

from __future__ import annotations

import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ml.datasets.capabilities import CapabilityInventory
from ml.datasets.schema import DataValidationError, Provenance, RoutingExample
from ml.datasets.taxonomy import INTENT_NAMES, Intent, intent_spec
from ml.preprocessing.normalize import normalize_text

__all__ = [
    "ROUTING_DATASET_VERSION",
    "BuildStats",
    "build_routing_dataset",
    "build_routing_records",
    "label_map",
    "routing_dataset_version",
]

#: Version of this generator, not of a record. ``routing_intent.v1`` versions the
#: *record*; this versions the corpus that produces it, so a v2 corpus cannot be
#: mistaken for a re-run of v1 that happened to land differently.
ROUTING_DATASET_VERSION = "routing_dataset.v1"

#: Written into every row's ``source``. Not the inventory version: a corpus
#: generated against an older inventory is still this corpus, and a reader who
#: wants to know which vocabulary filled the slots finds it in the run manifest.
_SOURCE = f"ml.datasets.routing.build_routing_dataset::{ROUTING_DATASET_VERSION}"

#: Intents with a template family synthesised from the inventory's closed
#: vocabularies rather than from a hand-written pool. Building these from
#: literals instead would let the corpus drift away from the enums silently: a
#: renamed ``RecommendationType`` would keep producing rows describing a type the
#: product no longer proposes. Deriving them means a rename stops generation
#: instead of teaching a stale label, and the class count drops visibly.
_VOCABULARY_INTENTS: dict[Intent, str] = {
    Intent.RISK_QUERY: "recommendations",
    Intent.ACCOUNT_ADMIN: "permissions",
    Intent.TASK_MANAGE: "activity_events",
    Intent.SCHEDULE_PLAN: "activity_events",
    Intent.ANALYTICS_INSIGHT: "activity_events",
}

#: Punctuation that survives :func:`~ml.preprocessing.normalize.normalize_text`
#: and is therefore safe to decorate an utterance with without perturbing its
#: meaning. Anything else — an apostrophe, a comma between two clauses, a full
#: stop mid-sentence — is rejected as a frame so that surface variation can
#: never turn one intent's row into another's.
_FRAME_PUNCTUATION = frozenset("?.!")

#: Frames prefixed to a rendered template. Empty string is in the pool so that a
#: bare imperative stays a live possibility; a corpus that only ever spoke in
#: "please…" would be a corpus the router overfits to.
_PREFIXES: tuple[str, ...] = (
    "",
    "",
    "please ",
    "can you ",
    "could you ",
    "hey, ",
    "quick one: ",
    "when you get a chance, ",
    "i need to ",
    "i'd like to ",
)

#: Frames appended to a rendered template. Deliberately short: every added clause
#: is token weight the classifier cannot use to pick a class, and a corpus that
#: pads to length teaches the padding. Both frame pools are checked against
#: :data:`_FRAME_PUNCTUATION` before use, so a frame can never strip a character
#: that carries a template's meaning.
_SUFFIXES: tuple[str, ...] = (
    "",
    "",
    " please",
    " thanks",
    " if you can",
    " for this week",
    " before I forget",
    " — I want to get ahead of it",
    " and tell me if that looks wrong",
)


#: Openers that make a statement into a question when a question mark is
#: attached to it. Restricted to verbs and question words on purpose: attaching
#: one to *"mark the invoice as done"* would produce a sentence nobody would say,
#: and rows like that are the padding this corpus is supposed to avoid.
_QUESTION_STARTERS = frozenset(
    {
        "am",
        "anyone",
        "anything",
        "are",
        "can",
        "could",
        "did",
        "do",
        "does",
        "how",
        "is",
        "list",
        "show",
        "should",
        "tell",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "would",
    }
)

#: Openers no prefix may precede, because the template already starts with a noun
#: phrase: *"can you the Tuesday event moved to Friday afternoon"* is not a
#: phrasing anyone uses, and a corpus that contains them teaches the model to
#: expect broken syntax rather than requests.
_NO_PREFIX_WORDS = frozenset({"a", "an", "its", "my", "the", "there", "this", "that"})

#: Shapes for the rows grounded in :attr:`IntentSpec.keywords`. Every shape has to
#: survive a two-word keyword such as "at risk" or "commit activity", which is why
#: multi-word keywords are filtered out of the pool rather than trusted to fit.
_KEYWORD_SHAPES: tuple[str, ...] = (
    "i have a question about my {keyword}",
    "can you tell me about my {keyword}",
    "my {keyword} is a mess right now",
    "what should I do about my {keyword}?",
)

#: Keywords excluded from the keyword-grounded rows because the shapes are noun
#: phrases ("my <keyword> is a mess right now"). A verb lands in one as "can you
#: tell me about my capture", which is a grammar error rather than a phrasing
#: variant.
_VERB_KEYWORDS = frozenset(
    {
        "add",
        "capture",
        "compare",
        "decide",
        "design",
        "evaluate",
        "find",
        "focus",
        "free",
        "justify",
        "log",
        "plan",
        "recall",
        "review",
        "save",
        "schedule",
        "search",
        "study",
        "why",
    }
)

#: Intents that get the keyword-grounded rows. The three excluded ones are
#: excluded on purpose: a one-line keyword utterance would be a light, generic
#: request, and putting one into ``code_assist``, ``deep_reasoning`` or
#: ``out_of_scope`` either blurs the boundary that justifies routing to the 8B
#: model or adds a row that says nothing. Those three classes are carried
#: entirely by their hand-written families.
_KEYWORD_INTENTS: frozenset[Intent] = frozenset(Intent(name) for name in INTENT_NAMES) - {
    Intent.CODE_ASSIST,
    Intent.DEEP_REASONING,
    Intent.OUT_OF_SCOPE,
}

# ---------------------------------------------------------------------------
#
# User data NEXUS has no seed copy of: task titles, project names, the artefact
# someone saved, the skill they are studying. These are plausible, not derived,
# because inventing them is the only honest option — a "real" title harvested from
# a repository fixture would be a fiction with extra steps. They are only ever
# wrapped by intent templates, which is what gives them their label.
# ---------------------------------------------------------------------------

_TASK_TITLES: tuple[str, ...] = (
    "write the quarterly report",
    "finish the migration plan",
    "review the API contract",
    "reconcile last month's invoices",
    "reply to the landlord",
    "update the onboarding docs",
    "profile the slow query",
    "fix the flaky checkout test",
    "book the venue for the offsite",
    "ship the release notes",
    "clean up the open pull requests",
    "draft the design review doc",
    "rebook the dentist",
    "reconcile my timesheet",
    "rename the feature flags",
    "confirm the invoice with the client",
    "read the vendor contract",
    "back up the laptop before the trip",
    "call the accountant",
    "renew the domain",
    "sort out the parking permit",
    "chase the missing invoice",
    "outline the sprint retrospective",
    "collect the signed contract",
    "replace the broken headset",
    "print the boarding passes",
    "water the plants",
)

_TASK_NAMES: tuple[str, ...] = (
    "the quarterly report",
    "the migration plan",
    "the API contract",
    "last month's invoices",
    "the onboarding docs",
    "the slow query",
    "the flaky checkout test",
    "the venue for the offsite",
    "the release notes",
    "the open pull requests",
    "the design review doc",
    "the timesheet",
    "the feature flags",
    "the client invoice",
    "the vendor contract",
    "the laptop backup",
    "the accountant",
    "the domain renewal",
    "the parking permit",
    "the missing invoice",
    "the sprint retrospective",
    "the signed contract",
    "the broken headset",
    "the boarding passes",
    "the plants",
)

_PROJECT_NAMES: tuple[str, ...] = (
    "Nexo rewrite",
    "mobile redesign",
    "payments integration",
    "auth refactor",
    "the migration plan",
    "API contract cleanup",
    "onboarding funnel",
    "observability rollout",
    "billing migration",
    "search relevance",
    "the mobile app",
    "data retention work",
    "the Phase 9 remediation",
    "the planner rewrite",
    "the analytics rewrite",
    "the knowledge base cleanup",
    "the release train",
    "the incident follow up",
    "the documentation sprint",
    "the hiring loop",
    "the router retraining",
    "the feature contract work",
    "the permission audit",
    "the recommendation engine",
    "the voice pipeline scoping",
    "the command centre design",
    "the ollama evaluation",
    "the career track pilot",
    "the learning path refresh",
    "the GitHub intelligence scan",
    "the risk rules rewrite",
    "the contract test suite",
    "the session storage work",
    "the background job runner",
    "the notification digest",
    "the calendar sync",
    "the onboarding email sequence",
    "the data export",
    "the usage report",
    "the backup drill",
    "the dependency bump",
    "the accessibility pass",
    "the dark mode work",
)

_TOPICS: tuple[str, ...] = (
    "vector databases",
    "deterministic scoring",
    "Postgres partitioning",
    "token rotation policy",
    "feature stores",
    "attention budgets",
    "capacity planning",
    "prompt caching",
    "embedding drift",
    "calendar recurrence rules",
    "keyword extraction",
    "schema migrations",
    "read replica lag",
    "idempotent writes",
    "the null versus zero rule",
    "leakage in a validation split",
    "evaluation rubrics",
    "checkpointing",
    "router abstention",
    "label imbalance",
    "backpressure on the job queue",
    "idempotency keys in the API",
    "row level security",
    "connection pooling",
    "blue green deploys",
    "feature flag rollout",
    "cold start latency",
    "cache invalidation",
    "retry storms",
    "circuit breakers",
    "distributed tracing",
    "log volume controls",
    "index bloat",
    "vacuum scheduling",
    "lock contention",
    "queue fairness",
    "abstention thresholds",
    "calibration of confidence scores",
    "prompt injection defences",
    "retrieval chunk boundaries",
    "evaluation set drift",
    "annotation guidelines",
    "provenance tracking",
)

_ARTIFACTS: tuple[str, ...] = (
    "the ADR on deterministic scoring",
    "the Postgres partitioning guide",
    "the notes from the architecture review",
    "the fine-tuning plan",
    "the risk scoring thresholds doc",
    "the meeting summary from Tuesday",
    "the article on query planning",
    "the API reference page",
    "the onboarding checklist",
    "the incident timeline",
    "the sprint retro notes",
    "the schema diagram",
    "the conference talk write up",
    "the interview rubric",
    "the load test report",
    "the on call handover note",
    "the postmortem for the checkout outage",
    "the query plan I saved last week",
    "the reading list on caching",
    "the glossary of project jargon",
    "the runbook for the nightly job",
    "the sketch of the new settings screen",
    "the transcript of the design review",
    "the summary of the pricing thread",
    "the cheatsheet for the CLI",
    "the comparison of the two libraries",
    "the checklist for the launch",
    "the note about index selection",
    "the questions I owe the platform team",
    "the link to the RFC",
)

_SKILLS: tuple[str, ...] = (
    "Rust",
    "SQL tuning",
    "technical writing",
    "Kubernetes",
    "graph algorithms",
    "prompt engineering",
    "system design",
    "Postgres internals",
    "async Python",
    "observability",
    "threat modelling",
    "type theory",
    "index design",
    "incident response",
    "distributed systems",
    "API design",
    "test engineering",
    "data modelling",
    "query optimisation",
    "release engineering",
    "accessibility",
    "frontend performance",
    "machine learning ops",
    "cost management",
    "code review",
    "debugging",
)

_GOALS: tuple[str, ...] = (
    "my distributed systems goal",
    "the Rust certification",
    "the two-hours-a-week study goal",
    "my system design goal",
    "the technical writing goal",
    "the Postgres internals goal",
    "the security basics goal",
    "the architecture reading goal",
    "my Kubernetes operations goal",
    "the accessibility audit goal",
    "my data modelling goal",
    "the incident response goal",
    "my query optimisation goal",
    "the release engineering goal",
    "my machine learning ops goal",
    "the API design goal",
)

_ROLES: tuple[str, ...] = (
    "senior backend engineer",
    "staff engineer",
    "engineering lead",
    "principal engineer",
    "platform engineer",
    "senior frontend engineer",
    "data engineer",
    "site reliability engineer",
    "security engineer",
    "solutions architect",
    "engineering manager",
    "technical program manager",
    "senior mobile engineer",
    "machine learning engineer",
    "developer productivity engineer",
    "database administrator",
    "quality engineer",
    "product engineer",
    "infrastructure engineer",
    "security architect",
    "senior data analyst",
    "technical writer",
    "release manager",
    "site reliability architect",
)

_COMPANIES: tuple[str, ...] = (
    "Nexo",
    "the payments integration",
    "the mobile app",
    "the observability rollout",
    "the hiring team",
    "the platform team",
    "the data team",
    "the security review board",
    "the partner integration",
    "the internal tooling group",
    "the customer success team",
    "the design systems team",
)

_DAYS: tuple[str, ...] = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
_WEEKENDS: tuple[str, ...] = ("Saturday", "Sunday")
_DATES: tuple[str, ...] = (
    "Friday",
    "next Monday",
    "tomorrow",
    "the 14th",
    "end of the month",
    "this sprint",
    "next Tuesday",
    "the end of the quarter",
    "Monday",
    "the 30th",
    "the start of next month",
    "the last Friday of the month",
    "the first of next month",
    "the 21st",
    "the middle of next week",
    "the week after next",
    "the day before the review",
    "the Friday after that",
    "the end of the sprint",
    "the start of the quarter",
    "the 7th",
    "two weeks from now",
)
_WINDOWS: tuple[str, ...] = (
    "tomorrow morning",
    "this afternoon",
    "first thing tomorrow",
    "Friday afternoon",
    "after lunch",
    "Monday morning",
    "late afternoon",
    "Wednesday morning",
    "the end of the day",
    "the middle of the morning",
    "Thursday afternoon",
    "Tuesday morning",
    "the start of the day",
    "the hour before lunch",
    "the last hour of the day",
    "Friday morning",
    "the middle of the afternoon",
    "this evening",
)
_DURATIONS: tuple[str, ...] = (
    "45 minutes",
    "an hour",
    "two hours",
    "90 minutes",
    "an hour and a half",
    "three hours",
    "half a day",
    "30 minutes",
    "four hours",
    "a quarter of a day",
    "20 minutes",
    "two and a half hours",
    "five hours",
    "a full day",
    "three quarters of a day",
    "an hour and a quarter",
)
_COUNTS: tuple[str, ...] = (
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "twelve",
    "fifteen",
    "twenty",
)

_REPOS: tuple[str, ...] = (
    "the backend repo",
    "the frontend repo",
    "nexo-api",
    "the ml pipeline repo",
    "my work repo",
    "the docs repo",
    "the infra repo",
    "the notebook repo",
    "the schema repo",
    "the migration repo",
    "the worker repo",
    "the cli repo",
    "the fine tune repo",
    "the validator repo",
    "the frontend design system repo",
    "the alerting repo",
    "the integration test repo",
    "the docker repo",
    "the analytics repo",
    "the calendar sync repo",
    "the auth service repo",
    "the notification repo",
    "the export job repo",
    "the backup tooling repo",
    "the load test repo",
)
_BRANCHES: tuple[str, ...] = (
    "feature/scheduler",
    "fix/nullable-features",
    "chore/line-length",
    "feat/routing-dataset",
    "fix/duplicate-keys",
    "feat/activity-templates",
    "chore/ruff-config",
    "fix/permission-check",
    "feat/split-leakage",
    "hotfix/token-rotation",
    "feat/router-abstention",
    "fix/checkpoint-resume",
    "feat/manifest-schema",
    "chore/ci-cache",
    "fix/timezone-drift",
    "feat/export-job",
    "fix/calendar-sync",
    "docs/phase-nine-report",
)
_LANGUAGES: tuple[str, ...] = (
    "Python",
    "TypeScript",
    "SQL",
    "Rust",
    "Bash",
    "Go",
    "Java",
    "CSS",
    "Ruby",
    "Kotlin",
    "Swift",
    "C#",
    "PHP",
    "Elixir",
    "Lua",
    "Haskell",
)
_TIMEZONES: tuple[str, ...] = (
    "Europe/Lisbon",
    "Europe/Berlin",
    "America/New_York",
    "UTC",
    "Europe/Madrid",
    "Asia/Tokyo",
    "Europe/Dublin",
    "Europe/London",
    "Europe/Amsterdam",
    "Europe/Oslo",
    "America/Los_Angeles",
    "America/Chicago",
    "America/Sao_Paulo",
    "Asia/Singapore",
    "Australia/Sydney",
)
_EMAILS: tuple[str, ...] = (
    "work.example@nexo.dev",
    "me.example@nexo.dev",
    "admin.example@nexo.dev",
    "billing.example@nexo.dev",
    "alerts.example@nexo.dev",
    "personal.example@nexo.dev",
    "inbox.example@nexo.dev",
    "team.example@nexo.dev",
)

#: Pools for the two model-facing classes and the abstention class. The city,
#: sport, dish and artist names are here so those classes can fill slots without
#: inventing a corpus of unique strings: the point of ``out_of_scope`` is the
#: *category* of thing being asked about, not the specific city.
_LIBRARIES: tuple[str, ...] = (
    "SQLAlchemy",
    "FastAPI",
    "pytest",
    "Pydantic",
    "httpx",
    "asyncio",
    "Postgres",
    "Docker",
    "Playwright",
    "Celery",
    "Redis",
    "Kubernetes",
    "Alembic",
    "SQLModel",
    "attrs",
    "structlog",
    "orjson",
    "httptools",
    "uvloop",
    "typer",
    "rich",
    "jinja2",
    "dateutil",
)
_DEV_TOPICS: tuple[str, ...] = (
    "retry logic",
    "null handling",
    "index selection",
    "pagination",
    "authentication",
    "migrations",
    "structured logging",
    "caching",
    "concurrency",
    "file uploads",
    "rate limiting",
    "idempotency keys",
    "timezone handling",
    "batch inserts",
    "connection pooling",
    "schema validation",
    "error mapping",
    "background jobs",
    "websocket reconnects",
    "permission checks",
    "input sanitisation",
    "dependency pinning",
    "cold starts",
    "memory profiling",
    "query planning",
    "test fixtures",
)
_REASONING_TOPICS: tuple[str, ...] = (
    "scheduling conflicts",
    "the risk engine",
    "the learned scorer",
    "the deterministic fallback",
    "near duplicate leakage",
    "class imbalance",
    "abstention",
    "the feature contract layout",
    "recommendations that stay advisory",
    "the router confidence floor",
    "the split strategy",
    "the evaluation rubric",
    "the manifest format",
    "credential scanning",
    "the provenance rule",
    "permission checks",
    "the schema version chain",
    "checkpoint resume semantics",
    "quantised weight loading",
    "the adapter boundary",
    "distribution shift",
    "label noise",
    "calibration",
    "the abstention fallback path",
    "feature availability semantics",
    "the missing versus zero rule",
    "held out evaluation",
    "reproducibility of a seed",
    "the cost of a wrong route",
    "the deterministic before learned rule",
)
_CITIES: tuple[str, ...] = (
    "Porto",
    "Lisbon",
    "Berlin",
    "London",
    "Tokyo",
    "New York",
    "Madrid",
    "Dublin",
    "Oslo",
    "Vienna",
    "Milan",
    "Athens",
    "Dubai",
    "Toronto",
    "Amsterdam",
    "Copenhagen",
    "Prague",
    "Warsaw",
    "Porto",
    "Stockholm",
    "Helsinki",
    "Edinburgh",
    "Manchester",
    "Munich",
    "Zurich",
    "Geneva",
    "Seattle",
    "Austin",
    "Boston",
    "Chicago",
)
_SPORTS: tuple[str, ...] = (
    "football",
    "basketball",
    "tennis",
    "Formula 1",
    "cycling",
    "rowing",
    "handball",
    "motogp",
    "rugby",
    "swimming",
    "volleyball",
    "cricket",
)
_DISHES: tuple[str, ...] = (
    "chicken",
    "pasta",
    "risotto",
    "curry",
    "salad",
    "soup",
    "lasagne",
    "ramen",
    "tacos",
    "risotto",
    "dumplings",
    "gnocchi",
    "biryani",
    "paella",
    "falafel",
    "miso soup",
)
_ARTISTS: tuple[str, ...] = (
    "Bach",
    "Mozart",
    "Miles Davis",
    "Radiohead",
    "Satie",
    "Coltrane",
    "Nina Simone",
    "Debussy",
    "Aphex Twin",
    "Ella Fitzgerald",
    "Bill Evans",
    "Fela Kuti",
    "Björk",
    "Stevie Wonder",
)
_GENRES: tuple[str, ...] = ("jazz", "classical", "ambient", "lo-fi", "soul", "drum and bass")
_SEASONS: tuple[str, ...] = ("autumn", "winter", "spring", "summer", "the rainy season")

# ---------------------------------------------------------------------------
# Closed vocabularies mirrored from the application, keyed by enum value.
#
# The values themselves are asserted against a real inventory when one is passed
# in (:func:`_vocabulary`); the phrase beside each one is the part ``ml`` cannot
# import, because ``app`` is not importable from a stdlib-only package.
# ---------------------------------------------------------------------------

_RECOMMENDATION_PHRASES: dict[str, str] = {
    "reschedule_task": "moving a task whose estimate keeps sliding",
    "break_down_task": "splitting one oversized task into steps I can start",
    "reduce_workload": "cutting the week back to what actually fits",
    "start_task": "picking the next thing to actually start",
    "prioritize_task": "deciding which task wins when two land the same day",
    "review_deadline": "re-reading a deadline that has drifted",
    "update_estimate": "correcting an estimate that turned out wrong",
    "block_time": "finding a slot for deep work",
    "complete_blocked_task": "clearing whatever is blocking a task",
    "review_project": "reviewing a project before it slips",
    "review_learning_goal": "reviewing a learning goal against its deadline",
    "revive_target_skill": "reviving a target skill that has gone quiet",
}

_RISK_PHRASES: dict[str, str] = {
    "deadline": "a deadline that is going to land badly",
    "workload": "a week that is booked well past capacity",
    "project": "a project drifting away from its milestone",
    "task": "a task that is blocked or has been pushed over and over",
    "scheduling": "two calendar entries that collide",
    "estimation": "an estimate that no longer matches the work",
    "consistency": "a pattern that has been going on long enough to matter",
}

_PERMISSION_PHRASES: dict[str, str] = {
    "users.read": "read my user profile",
    "users.write": "change my user profile",
    "projects.read": "see my projects",
    "projects.write": "create or edit my projects",
    "tasks.read": "see my tasks",
    "tasks.write": "create or edit my tasks",
    "analytics.read": "see my analytics",
    "calendar.read": "see my calendar",
    "calendar.write": "change my calendar",
    "knowledge.read": "read my knowledge base",
    "knowledge.write": "write to my knowledge base",
}

#: The activity-feed members each intent may draw a template from.
#:
#: Split per intent rather than pooled globally because the activity feed is the
#: widest vocabulary in the application. A shared pool lets a ``task_manage`` row
#: say *"log a note being saved and move it to Monday"* — knowledge vocabulary
#: inside the task class — which is exactly how a template corpus quietly
#: destroys the boundary it was written to teach. Only the three intents that
#: genuinely read the feed appear here.
#:
#: These are real ``ActivityEvent`` members. The families below render them as
#: ordinary English rather than substituting the enum value, so a family produces
#: nothing at all when the application drops one of its events.
_ACTIVITY_INTENTS: dict[Intent, frozenset[str]] = {
    Intent.TASK_MANAGE: frozenset(
        {
            "task_created",
            "task_started",
            "task_completed",
            "task_blocked",
            "task_priority_changed",
            "task_due_date_changed",
        }
    ),
    Intent.SCHEDULE_PLAN: frozenset(
        {
            "calendar_event_created",
            "calendar_event_updated",
            "calendar_event_deleted",
            "task_scheduled",
        }
    ),
    Intent.ANALYTICS_INSIGHT: frozenset(
        {
            "task_completed",
            "work_session_completed",
            "commit_detected",
            "learning_session_recorded",
            "risk_detected",
        }
    ),
}

_SLOT = re.compile(r"\{(\w+)\}")

#: Any brace pair, captured so a build-time check can ask whether the inner text
#: names a slot the template declared.
_BRACE_PAIR = re.compile(r"\{([^{}]*)\}")


@dataclass(frozen=True, slots=True)
class _Vocabulary:
    """Closed vocabularies harvested from the capability inventory.

    The four tuples are the real enum members — recommendations, risks,
    permissions, activity events — filtered to the members this module has a
    phrase for. Every field is therefore a subset of what the application
    actually declares, and an empty field is a signal (a renamed enum) rather
    than a silent fallback to an invented vocabulary.
    """

    recommendation_types: tuple[str, ...]
    risk_types: tuple[str, ...]
    permissions: tuple[str, ...]
    activity_events: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Template:
    """One generator row: a text pattern and the slots it consumes.

    ``framed`` marks a template that is already a complete first-person
    utterance, so no prefix or suffix may be attached to it. Those rows exist to
    add phrasing variety; bolting "please" onto one produces *"can you i have a
    question about my growth"*, which teaches nothing and reads like noise.
    """

    template_id: str
    text: str
    slots: tuple[str, ...]
    framed: bool = False


def _vocabulary(inventory: CapabilityInventory | None) -> _Vocabulary:
    """Read the closed vocabularies the templates draw on.

    Args:
        inventory: The harvested capability inventory, or None when the caller
            has no application source to walk. Without one the vocabularies are
            empty and the families that need them contribute nothing — the module
            stays correct and simply produces fewer rows per intent.

    Returns:
        The filtered vocabularies.
    """
    if inventory is None:
        return _Vocabulary((), (), (), ())
    activity_keys = frozenset(value for events in _ACTIVITY_INTENTS.values() for value in events)
    return _Vocabulary(
        recommendation_types=tuple(
            value for value in inventory.recommendation_types if value in _RECOMMENDATION_PHRASES
        ),
        risk_types=tuple(value for value in inventory.risk_types if value in _RISK_PHRASES),
        permissions=tuple(value for value in inventory.permissions if value in _PERMISSION_PHRASES),
        activity_events=tuple(
            value for value in inventory.activity_events if value in activity_keys
        ),
    )


def _render(template: str, slots: Mapping[str, Any]) -> str:
    """Fill a template's slots.

    ``str.format`` is deliberately not used: the code-assist templates contain
    braces of their own, and a language where "{" is a placeholder makes every
    code example a syntax error.

    Args:
        template: The pattern, with ``{name}`` placeholders.
        slots: Values by slot name.

    Returns:
        The filled text.

    Raises:
        DataValidationError: The template references a slot the caller did not
            supply, which is a generator bug rather than user data.
    """

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in slots:
            raise DataValidationError(f"template slot {name!r} has no value")
        return str(slots[name])

    return _SLOT.sub(substitute, template)


def _typo(rng: random.Random, text: str) -> str:
    """Introduce one realistic keystroke error.

    Only interior letters of words of five characters or more are touched: a
    typo in a short word ("taks") destroys the very signal the row exists to
    carry, and a typo in the first word destroys the verb the intent is named
    after.

    Args:
        rng: The per-intent generator.
        text: The rendered utterance.

    Returns:
        The text with at most one character-level error, or unchanged.
    """
    words = [word for word in text.split() if len(word) >= 5]
    if not words:
        return text
    index = rng.randrange(len(words))
    parts = text.split()
    position = next(i for i, part in enumerate(parts) if part == words[index])
    word = parts[position]
    spot = rng.randrange(1, len(word) - 1)
    choice = rng.randrange(3)
    if choice == 0:
        word = word[:spot] + word[spot + 1] + word[spot] + word[spot + 2 :]
    elif choice == 1:
        word = word[:spot] + word[spot + 1 :]
    else:
        word = word[:spot] + word[spot] + word[spot:]
    parts[position] = word
    return " ".join(parts)


def _decoration_ok(text: str) -> bool:
    """Whether a frame may be attached to a rendered template.

    A frame that contains punctuation the normaliser strips would let a frame
    rewrite the meaning of a template — and cross-intent meaning is precisely
    what this dataset exists to keep clean.

    Args:
        text: The candidate decorated utterance.

    Returns:
        True when every non-alphanumeric character survived normalisation.
    """
    for char in text:
        if not char.isalnum() and not char.isspace() and char not in _FRAME_PUNCTUATION:
            return False
    return True


def _decorate(rng: random.Random, template: _Template, text: str) -> str:
    """Add a frame, a trailing question mark, or a keystroke error.

    The shape of the template decides what is allowed on it. A template that is
    already a question gets no frame at all: *"please which tasks are still open
    before I forget"* is not a phrasing anyone uses, and a corpus that contains
    them teaches the model to expect broken syntax rather than requests.

    Args:
        rng: The per-intent generator.
        template: The template the text came from.
        text: The rendered template.

    Returns:
        The decorated utterance, which is the value checked for uniqueness.
    """
    if template.framed or text.endswith("?") or text[:1].isupper():
        return _typo(rng, text) if rng.random() < 0.10 else text
    if text.split(" ", 1)[0].casefold() in _QUESTION_STARTERS:
        if rng.random() >= 0.35:
            return text
        text = f"{text}?"
        return _typo(rng, text) if rng.random() < 0.10 else text
    prefix = "" if text.split(" ", 1)[0].casefold() in _NO_PREFIX_WORDS else rng.choice(_PREFIXES)
    suffix = rng.choice(_SUFFIXES)
    # "please ... please" reads as a stutter rather than as politeness, and it is
    # the single most obvious tell of a generated corpus.
    if "please" in prefix and suffix in (" please", " if you can"):
        suffix = ""
    decorated = f"{prefix}{text}{suffix}".strip()
    if not _decoration_ok(decorated):
        decorated = text
    return _typo(rng, decorated) if rng.random() < 0.10 else decorated


def _curated(intent: Intent) -> tuple[_Template, ...]:
    """Take the hand-written exemplars the taxonomy already carries.

    The examples in :class:`~ml.datasets.taxonomy.IntentSpec` were written
    against the real routers rather than generated, so they are the highest
    quality rows available and every intent's corpus should contain them.

    Args:
        intent: The intent whose spec supplies the examples.

    Returns:
        One template per exemplar, plus two keyword-grounded phrasings so the
        corpus is not built from five strings per class.
    """
    spec = intent_spec(str(intent))
    templates = [
        _Template(template_id=f"{intent}.ex{i:02d}", text=example, slots=())
        for i, example in enumerate(spec.examples)
    ]
    if intent in _KEYWORD_INTENTS:
        single_word = tuple(
            dict.fromkeys(
                keyword
                for keyword in spec.keywords
                if " " not in keyword and keyword not in _VERB_KEYWORDS
            )
        )
        for position, shape in enumerate(_KEYWORD_SHAPES):
            keyword = single_word[position % len(single_word)]
            # The keyword is substituted here rather than drawn from a slot: the
            # pool must be this intent's own keywords, or a task row comes out
            # carrying analytics vocabulary and the class boundary blurs.
            templates.append(
                _Template(
                    template_id=f"{intent}.kw{position:02d}_{keyword}",
                    text=shape.format(keyword=keyword),
                    slots=(),
                    framed=True,
                )
            )
    return tuple(templates)


def _build_templates(intent: Intent, vocabulary: _Vocabulary) -> tuple[_Template, ...]:
    """Assemble every template family an intent draws from.

    Args:
        intent: The intent being generated.
        vocabulary: The harvested closed vocabularies.

    Returns:
        The templates for this intent: its curated exemplars, its hand-written
        families, and — where applicable — the families synthesised from the
        inventory.
    """
    templates: tuple[_Template, ...] = _curated(intent)
    families = _INTENT_TEMPLATES.get(intent, ())
    templates += tuple(
        _Template(template_id=f"{intent}.{i:03d}", text=text, slots=tuple(sorted(_slots(text))))
        for i, text in enumerate(families, start=1)
    )
    if intent in _VOCABULARY_INTENTS:
        templates += _vocabulary_templates(intent, vocabulary)
    _check_templates(intent, templates)
    return templates


def _check_templates(intent: Intent, templates: Sequence[_Template]) -> None:
    """Refuse a template whose braces disagree with its declared slots.

    The parser is tolerant of a brace pair it cannot resolve — a code-assist
    template quoting ``{x: int}`` is legitimate — so what is checked is the only
    thing genuinely ambiguous: a pair that is not a declared slot. That is the
    state where the template author expected a substitution and the renderer will
    hand them a literal, which produces a fluent sentence with a placeholder in
    the middle of it.

    Args:
        intent: The intent whose templates are being checked.
        templates: The assembled set.

    Raises:
        DataValidationError: Two templates share an id, a template contains an
            unresolved brace pair, or it declares a slot it never uses.
    """
    seen: set[str] = set()
    for template in templates:
        if template.template_id in seen:
            raise DataValidationError(f"{intent}: duplicate template id {template.template_id!r}")
        seen.add(template.template_id)
        declared = set(template.slots)
        for match in _BRACE_PAIR.finditer(template.text):
            if match.group(1) not in declared:
                raise DataValidationError(
                    f"{intent}: template {template.template_id!r} contains braces the slot "
                    f"parser would read as a placeholder: {match.group(0)!r}"
                )
        used = _slots(template.text)
        if used != declared:
            raise DataValidationError(
                f"{intent}: template {template.template_id!r} declares {sorted(declared)} "
                f"but uses {sorted(used)}"
            )


def _slots(template: str) -> set[str]:
    """The slot names a template references."""
    return {match.group(1) for match in _SLOT.finditer(template)}


def _vocabulary_templates(intent: Intent, vocabulary: _Vocabulary) -> tuple[_Template, ...]:
    """Synthesise template families from the inventory's closed vocabularies.

    Args:
        intent: The intent being generated.
        vocabulary: The harvested closed vocabularies.

    Returns:
        One template per vocabularily distinct phrasing shape, or nothing when
        the inventory carries none of the members this module has a phrase for.
    """
    if _VOCABULARY_INTENTS[intent] == "recommendations":
        shapes = (
            ("rec", "what do you recommend about {rec}?"),
            ("rec", "which suggestions do you have for {rec}?"),
            ("rec", "anything to do about {rec}?"),
            ("rec", "I keep ignoring the suggestion about {rec}"),
            ("risk", "tell me if you think there is {risk}"),
            ("risk", "is there {risk} anywhere in my week?"),
            ("risk", "flag {risk} for me if you see it"),
        )
    elif _VOCABULARY_INTENTS[intent] == "permissions":
        shapes = (
            ("permission", "who can {permission}?"),
            ("permission", "who is allowed to {permission}?"),
            ("permission", "can anyone else {permission}?"),
            ("permission", "check whether I am allowed to {permission}"),
            ("permission", "I need to {permission} — is that permitted?"),
        )
    else:
        if intent is Intent.TASK_MANAGE:
            activity = (
                ("task_created", "log a new task under {project}"),
                ("task_started", "that one is in progress now, log it"),
                ("task_completed", "I closed one off — log it as completed"),
                ("task_blocked", "that one is blocked and I cannot move it"),
                ("task_priority_changed", "its priority changed today, log that"),
                ("task_due_date_changed", "its due date just moved, log it as {date}"),
            )
        elif intent is Intent.SCHEDULE_PLAN:
            activity = (
                (
                    "calendar_event_created",
                    "put something on the calendar for {task_name} on {date}",
                ),
                ("calendar_event_created", "reserve {duration} on {date}"),
                ("calendar_event_updated", "the {day} event moved to {window}"),
                ("calendar_event_deleted", "the {day} entry is gone, drop it from the calendar"),
                ("task_scheduled", "I put {task_name} on the calendar for {date}"),
            )
        else:
            activity = (
                ("task_completed", "how many tasks did I close this week"),
                ("work_session_completed", "how many work sessions do I have this month"),
                ("commit_detected", "how many commits came in over the last 30 days"),
                ("learning_session_recorded", "how many study sessions am I logging"),
                ("risk_detected", "how many risks showed up last week"),
            )
        return tuple(
            _Template(
                template_id=f"{intent}.act_{position:02d}_{value}",
                text=template,
                slots=tuple(sorted(_slots(template))),
            )
            for position, (value, template) in enumerate(activity)
            if value in _ACTIVITY_INTENTS[intent] and value in vocabulary.activity_events
        )
    return tuple(
        _Template(
            template_id=f"{intent}.voc_{slot}{index:02d}",
            text=template,
            slots=(slot,),
        )
        for index, (slot, template) in enumerate(shapes)
        if _vocabulary_has(vocabulary, slot)
    )


def _vocabulary_has(vocabulary: _Vocabulary, slot: str) -> bool:
    """Whether a template family can be filled from the harvested vocabulary.

    Args:
        vocabulary: The harvested closed vocabularies.
        slot: The pool the shape reads.

    Returns:
        False when the inventory carried none of the members this module has a
        phrase for, which means the family would have to invent them.
    """
    return {
        "rec": bool(vocabulary.recommendation_types),
        "risk": bool(vocabulary.risk_types),
        "permission": bool(vocabulary.permissions),
        "event": bool(vocabulary.activity_events),
    }[slot]


#: Every slot a template may draw on. A template naming a slot that is not here
#: fails at build time with a missing key rather than silently rendering an
#: empty string, because a template with a hole in it produces a fluent-sounding
#: utterance with no entity in it and nobody notices until the model is trained.
_SLOT_VALUES: dict[str, tuple[str, ...]] = {
    "task": _TASK_TITLES,
    "task_name": _TASK_NAMES,
    "project": _PROJECT_NAMES,
    "topic": _TOPICS,
    "artifact": _ARTIFACTS,
    "skill": _SKILLS,
    "goal": _GOALS,
    "role": _ROLES,
    "company": _COMPANIES,
    "day": _DAYS,
    "weekend": _WEEKENDS,
    "date": _DATES,
    "window": _WINDOWS,
    "duration": _DURATIONS,
    "count": _COUNTS,
    "repo": _REPOS,
    "branch": _BRANCHES,
    "language": _LANGUAGES,
    "library": _LIBRARIES,
    "dev_topic": _DEV_TOPICS,
    "reasoning_topic": _REASONING_TOPICS,
    "city": _CITIES,
    "sport": _SPORTS,
    "dish": tuple(dict.fromkeys(_DISHES)),
    "artist": _ARTISTS,
    "genre": _GENRES,
    "season": _SEASONS,
    "timezone": _TIMEZONES,
    "email": _EMAILS,
    "rec": tuple(_RECOMMENDATION_PHRASES.values()),
    "risk": tuple(_RISK_PHRASES.values()),
    "permission": tuple(_PERMISSION_PHRASES.values()),
}

#: ``concept`` is an alias rather than a second pool: a concept in the knowledge
#: base is the same kind of named subject a note is about, and a second list
#: would only invite the two to drift apart.
_SLOT_VALUES["concept"] = _TOPICS


#: Hand-written families per intent. Slot names come from :data:`_SLOT_VALUES`;
#: an unknown name is a build-time error rather than an empty substitution.
#:
#: The wording rule these were written under: state the *action on the entity*,
#: and leave out any word that would move the utterance into a neighbouring
#: class. No "calender", no "priority", no "deadline" inside ``task_manage``; no
#: "how am I doing" inside ``schedule_plan``. Adjacent classes are separated by
#: what changes — the task row changes the task, the planner row changes where
#: the work sits in time — and a template that mixes the two teaches the model
#: that the boundary is a coin toss.
_INTENT_TEMPLATES: dict[Intent, tuple[str, ...]] = {
    Intent.TASK_MANAGE: (
        "add a task to {task}",
        "create a task called {task}",
        "make me a task for {task}",
        "new task: {task}",
        "put {task} on my list",
        "add {task} to my backlog",
        "show me my open tasks",
        "what tasks do I still have open",
        "which tasks are on my plate right now",
        "list everything I have not finished",
        "mark {task} as done",
        "tick {task} off my list",
        "{task} is finished — close it",
        "close {task} out",
        "I finished {task}",
        "set {task} back to open",
        "reopen {task}",
        "delete the task about {task}",
        "remove {task} from my list",
        "get rid of that duplicate task about {task}",
        "how long did I estimate for {task}",
        "change the estimate on {task} to {duration}",
        "is {task} still in the backlog or has it moved on",
        "which tasks have no owner at all",
        "add a subtask to {task} for {count} days",
        "I have a task called {task} that I cannot find",
    ),
    Intent.PROJECT_MANAGE: (
        "create a project for {project}",
        "start a new project called {project}",
        "open a project for {project}",
        "add a project called {project}",
        "what projects am I working on",
        "list my active projects",
        "which projects are still open",
        "show me everything under {project}",
        "what is the progress on {project}",
        "how is {project} going",
        "how far along is {project}",
        "what is the scope of {project}",
        "who is on {project}",
        "add a milestone to {project} for {date}",
        "put {date} on the {project} roadmap",
        "archive {project}",
        "close out {project}",
        "restore {project}",
        "rename {project}",
        "change the description of {project}",
        "which projects have slipped past their milestones",
        "give me a one line status for each of my projects",
        "what deliverables are still open on {project}",
    ),
    Intent.SCHEDULE_PLAN: (
        "what does my calendar look like on {day}",
        "show me {day} in the day view",
        "what is on my calendar for {date}",
        "how busy is {day}",
        "am I free {window}",
        "when am I free on {day}",
        "find me a free slot {window}",
        "book {duration} for {task_name} on {date}",
        "block {duration} {window} for {task_name}",
        "put {task_name} on my calendar for {date}",
        "schedule {task_name} on {day} {window}",
        "create a calendar event for {task_name} on {date}",
        "move my {day} meeting to {window}",
        "reschedule the {day} event to {date}",
        "cancel the meeting on {day}",
        "remove the {day} block from my calendar",
        "log a work session of {duration} on {task_name}",
        "I worked on {task_name} for {duration}",
        "log {duration} of study time for {skill}",
        "give me my availability windows for {day}",
        "block out my {weekend} for deep work",
        "shift everything on {day} into the afternoon",
        "when can I fit {duration} of {task_name} in this week",
        "plan my {day} around {project}",
    ),
    Intent.KNOWLEDGE_CAPTURE: (
        "save a note about {topic}",
        "write down what I decided about {topic}",
        "jot down something about {topic}",
        "capture {artifact}",
        "file {artifact} in my knowledge base",
        "keep {artifact} for later",
        "add {artifact} to my references",
        "bookmark {artifact}",
        "store this link about {topic}",
        "save the link to {artifact}",
        "add {artifact} to my reading list",
        "log a reading note on {topic}",
        "add a concept called {concept}",
        "link {artifact} to {project}",
        "tag {artifact} with {topic}",
        "save the summary of the call about {topic}",
        "remember that {topic} matters for {project}",
        "make a note so I do not forget the {topic} decision",
        "turn this thread into a note about {topic}",
        "archive the note about {topic}",
        "publish the note about {topic} so I can find it",
    ),
    Intent.KNOWLEDGE_LOOKUP: (
        "what did I decide about {topic}",
        "what was the conclusion on {topic}",
        "find the note where I wrote about {topic}",
        "search my notes for {topic}",
        "look up {topic} in my knowledge base",
        "which notes mention {topic}",
        "what have I saved about {topic}",
        "where did I read something about {topic}",
        "remind me what I wrote about {topic}",
        "recall my note on {topic}",
        "find {artifact}",
        "which link did I save for {topic}",
        "list everything I bookmarked about {topic}",
        "what sources did I keep on {topic}",
        "find my notes on {topic} from last month",
        "search for {topic} and show me the best match",
        "which concepts are linked to {topic}",
        "does {artifact} say anything about {topic}",
        "pull up the note where I described {topic}",
        "what did I save about {project} last week",
    ),
    Intent.ANALYTICS_INSIGHT: (
        "how productive was I last week",
        "how am I doing this week",
        "how did the last 30 days go",
        "show my focus trend",
        "what does my productivity look like",
        "plot my focus over the last 30 days",
        "what is my completion rate",
        "how many things did I finish this week",
        "compare my planned hours with the hours I actually spent",
        "where did my time go this week",
        "how much of my week was deep work",
        "which project did I spend the most time on",
        "summarise my activity for the last month",
        "give me the numbers for last week",
        "how consistent have I been",
        "show me my throughput per project",
        "am I finishing more or less than last month",
        "what does my activity feed say about this week",
        "how many notes did I save last month",
        "how many notes did I write this week",
        "break my week down by project",
        "chart my planned against actual hours",
        "is my throughput going up or down",
        "how many hours go into {project} a week",
        "tell me what my activity data says",
        "how many tasks did I complete on {day}",
        "what is my average task age",
        "show my completion rate for {project}",
        "how much time did I spend on {project} this week",
        "what share of my planned work landed this week",
        "how many tasks were rescheduled this week",
        "what is my throughput on {project}",
        "compare my focus time this week with last week",
        "how many hours of deep work did I log",
        "how many projects moved this week",
        "how long does an open task usually sit before it is done",
    ),
    Intent.RISK_QUERY: (
        "what risks am I carrying right now",
        "which of my deadlines are at risk",
        "am I overcommitted this week",
        "what should I worry about this week",
        "what is going to slip",
        "show me the scheduling conflicts blocking me",
        "which of my tasks is blocked",
        "what is blocking {project}",
        "which estimates look wrong",
        "am I overestimating anywhere",
        "where is my workload too heavy",
        "which tasks are stuck behind something else",
        "what is overdue",
        "what should I review before {project} slips",
        "how much risk is on {project}",
        "which projects are drifting from their milestones",
        "is there anything I am about to miss",
        "which risks have I already acknowledged",
        "what do you recommend about {rec}",
        "which suggestions do you have for {rec}",
        "is there {risk} anywhere in my week",
        "what are my top three risks",
        "give me the risk report for {project}",
        "anything I should be careful about before {date}",
        "is {project} at risk of slipping",
        "what should I do about the risk on {project}",
        "which tasks have effort far above their estimate",
        "show me the risks that are still unacknowledged",
        "which tasks have been rescheduled twice already",
        "what is the riskiest project I have",
        "how many risks are open right now",
        "which of my deadlines have the least slack",
        "which risks did I acknowledge and never resolved",
    ),
    Intent.DEVELOPER_INTEL: (
        "how active was I on github this week",
        "how many commits did I make this week",
        "summarise my commit activity on {repo}",
        "what did I commit on {branch} this week",
        "which repositories have I been touching",
        "what is going on in {repo}",
        "show my developer streak",
        "how is my streak looking",
        "which branches am I working on",
        "what have I been committing in {repo} lately",
        "which languages have I been writing in",
        "list my pull requests",
        "what open pull requests do I have",
        "show the commits on {branch}",
        "how often do I commit",
        "which repo am I most active in",
        "when was my last commit",
        "how many files did I touch in {repo} this week",
        "summarise my contributions to {repo}",
        "compare my commit activity with last month",
        "which repos have not been scanned yet",
        "register {repo} so it shows up in my activity",
        "what did I work on in {repo} over the last 30 days",
        "how many commits have I made this month",
        "which branches have I opened in {repo}",
        "how many commits landed on {branch} this week",
        "what languages appear in {repo}",
        "compare {repo} activity with last week",
        "how many pull requests do I have open on {branch}",
        "when did I last push to {branch}",
        "which commits did I make on {day}",
        "how much of my week went into {repo}",
        "which repo changed the most this week",
    ),
    Intent.LEARNING_TRACK: (
        "how is my learning goal going",
        "how is {goal} going",
        "how far through {goal} am I",
        "what should I study next",
        "what should I learn next for {goal}",
        "log {duration} of learning on {skill}",
        "I studied {skill} for {duration}",
        "record a learning session on {skill}",
        "am I making progress toward {goal}",
        "which skills am I neglecting",
        "how consistent have my study sessions been",
        "what is my completion rate on {goal}",
        "set up a learning goal for {skill}",
        "add a study goal about {skill}",
        "how many hours have I logged on {skill}",
        "which skills have I practised recently",
        "my learning goal is behind — what now",
        "book study time for {skill} {window}",
        "which courses are part of {goal}",
        "how many practice sessions did I do on {skill} this month",
        "where am I on {goal}",
        "I want to learn {skill} — set a goal for it",
        "which skills do I need to practise more",
        "how much of {goal} is left",
    ),
    Intent.CAREER_TRACK: (
        "am I on track for {role}",
        "how am I tracking toward a {role} role",
        "what would it take to become a {role}",
        "which skills should I build for the next promotion",
        "show my career profile",
        "what does my career profile say I am strong at",
        "how does my repository activity affect my career path",
        "which skills matter for {role}",
        "where am I against my target role",
        "what is the ladder to {role}",
        "add evidence to my career profile",
        "add a piece of evidence for {project} on my profile",
        "which competencies am I missing for {role}",
        "how far along am I for {role}",
        "should I be aiming at {role} or something else",
        "what does my profile say about my growth",
        "record that {project} counts as evidence",
        "which of my projects support my case for {role}",
        "what is my standing against the {role} bar",
        "update my career profile for {role}",
        "compare myself against the {role} expectations",
        "what gaps are in my profile",
        "what would I need to learn to be a {role}",
        "compare my profile against the {role} expectations",
        "what should I do to be ready for a {role} interview",
        "which evidence supports my move toward a {role}",
        "set my target role to {role}",
        "how does {company} factor into my path to a {role}",
        "what is missing from my profile for a {role}",
        "how strong is my evidence for {project} these days",
        "what is the fastest way to build standing for {role}",
    ),
    Intent.ACCOUNT_ADMIN: (
        "update my timezone to {timezone}",
        "change my timezone to {timezone}",
        "my timezone is wrong — it should be {timezone}",
        "change the email on my account to {email}",
        "update my email address",
        "change my password",
        "reset my password",
        "set my availability windows for next week",
        "update my notification preferences",
        "show me my profile settings",
        "what is my account set to",
        "check what my account permissions allow",
        "who is allowed to see my projects",
        "who is allowed to see my analytics",
        "am I allowed to change my calendar",
        "am I allowed to write to my knowledge base",
        "who can see my tasks",
        "who can create or edit my tasks",
        "update my name on the account",
        "sign out of my account",
        "sign me in",
        "how long is my session good for",
        "what can this account actually do",
        "change the region my account is set to",
        "change my availability on {day} to {window}",
        "mark {day} as unavailable in my availability settings",
        "block out {date} on my availability",
        "update my profile timezone to {timezone} for good",
        "use {email} as my account email",
        "make my sessions expire after {duration}",
        "extend my session window to {count} hours",
        "turn off {day} summaries in my preferences",
        "which of my account settings can I not change",
        "reset my account settings to the defaults",
        "how long are my sessions good for",
    ),
    Intent.CODE_ASSIST: (
        "write a {language} function that parses an ISO timestamp with an offset",
        "write a {language} function that validates an email address",
        "write a {language} helper for {dev_topic} that I can unit test",
        "generate a SQL query that counts rows per group over the last 30 days",
        "write a pytest fixture that hands out a {library} database session",
        "write a unit test for a {language} function that retries three times with backoff",
        "refactor this {language} function so it stops mutating its argument",
        "refactor my {library} query so it stops doing an N plus 1 join",
        "explain what this {library} stack trace actually means",
        "I got a KeyError in this {language} function and I do not understand why",
        "why does this {library} query do an N plus 1 join — rewrite it",
        "generate a {library} dependency that checks a permission",
        "write a typed model for the {dev_topic} record in our schema",
        "write a migration that backfills a nullable column for {dev_topic}",
        "write a {language} decorator that retries a flaky network call",
        "help me debug this regex, it matches more of my {dev_topic} than it should",
        "explain what this traceback from the {library} worker means",
        "write a function to diff two sorted lists in linear time",
        "convert this callback style {library} code into async",
        "write a type annotated dataclass for a scheduled time block",
        "fix this {language} code so it handles the empty list case",
        "what is wrong with this {library} query",
        "write a script that walks the repo and counts commits per {language}",
        "show me how this {library} decorator works line by line",
        "write a {language} context manager for a database transaction",
        "explain the difference between these two {library} error classes in my code",
        "write a {language} function that batches {dev_topic} without loading it all",
        "explain why my {library} test suite is slow this week",
        "write a {language} script to backfill {topic} in batches",
    ),
    Intent.DEEP_REASONING: (
        "compare three ways to model scheduling conflicts and argue which one I would pick",
        "compare three ways to handle {reasoning_topic} and argue which one I would pick",
        "work through the trade offs of replacing the deterministic scorer with a learned one",
        "work through the trade offs of caching {reasoning_topic} against recomputing it",
        "design the architecture for a {reasoning_topic} pipeline and defend the choices",
        "design the architecture for {reasoning_topic} and defend the choices",
        "reason about why my accuracy drops on the long tail and what to do about it",
        "reason about why {reasoning_topic} keeps drifting in my own data",
        "decide which of these two scheduling algorithms fits a bounded planner",
        "decide whether {reasoning_topic} belongs in the router or in the scorer",
        "weigh whether the router should abstain below a confidence floor",
        "weigh three options for handling {reasoning_topic} without inventing behaviour",
        "think through the consequences of making the engine learn every rule",
        "think through what would break if {reasoning_topic} stopped being deterministic",
        "argue whether a learned model should ever write to the calendar",
        "argue for keeping {reasoning_topic} a rule rather than a prediction",
        "compare batch and streaming scoring for the risk engine",
        "compare batch and streaming approaches to {reasoning_topic}",
        "evaluate the trade offs of keeping the deterministic engine as the fallback",
        "evaluate the trade offs of dropping {reasoning_topic} from the feature set",
        "design a schema that survives adding a fourth large model class",
        "design how {reasoning_topic} should be represented across four feature contracts",
        "reason about which failure is worse: a wrong task write or a missed deadline",
        "reason about which error the user should see when {reasoning_topic} fails",
        "compare storing intents as a classifier versus a rule cascade",
        "compare a rules first router against a pure classifier",
        "work out how to split a dataset without leaking near duplicates",
        "think about where a template corpus will overfit the router",
        "defend the choice of leaving two classes for the large model",
        "defend the choice of abstaining instead of guessing on {reasoning_topic}",
        "compare embedding approaches for retrieval over my own notes",
        "reason about the cost of an extra generation call in the routing path",
        "think through what evidence would justify a career inference",
        "compare a rule based router against a classifier on my own data",
        "reason about the failure modes of a balanced synthetic corpus",
        "argue for or against feature engineering here",
    ),
    Intent.OUT_OF_SCOPE: (
        "what is the weather in {city} tomorrow",
        "will it rain in {city} this weekend",
        "give me the forecast for {city} next week",
        "what is the temperature in {city}",
        "who won the {sport} match last night",
        "how did my {sport} team do last night",
        "what is the final score of the {sport} game",
        "book me a flight to {city} next Tuesday",
        "find me a hotel in {city} for two nights",
        "what is a good restaurant in {city}",
        "give me a recipe for {dish}",
        "put on some {genre} while I work",
        "tell me a joke",
        "what is 2 plus 2",
        "who won the election",
        "give me the latest headlines",
        "what happened in the news today",
        "what time is it in {city}",
        "translate this into Portuguese",
        "write me a poem about {season}",
        "how do I boil an egg",
        "tell me a story about a dragon",
        "what is the capital of Peru",
        "play something by {artist}",
        "how tall is the Eiffel Tower",
        "what is your name",
        "order me a {dish} to be delivered",
        "call me a taxi",
        "look up the price of gold",
        "what is the population of {city}",
        "when does the {sport} season start",
        "give me a cocktail recipe",
        "what is on television tonight",
        "remind me to water the plants",  # a habit, not a NEXUS entity
    ),
}

#: Vocabulary grounding for slots whose value must come from the closed
#: vocabularies the taxonomy already exposes.
_SLOT_VALUES["concept"] = _TOPICS


def _generate(
    intent: Intent,
    templates: Sequence[_Template],
    *,
    seed: int,
    target: int,
    seen: set[str],
) -> list[RoutingExample]:
    """Produce exactly ``target`` unique examples for one intent.

    Args:
        intent: The intent being generated.
        templates: The intent's template families.
        seed: The per-intent seed, derived so one intent cannot reshuffle another.
        target: How many examples the intent must contribute.
        seen: Normalised texts already emitted, across all intents.

    Returns:
        Exactly ``target`` examples in generation order.

    Raises:
        DataValidationError: The families cannot produce that many distinct
            utterances. Balance is a property of the dataset, so the builder
            refuses to return a lopsided corpus and lets the caller raise
            ``per_intent`` instead.
    """
    rng = random.Random(seed)  # noqa: S311 — a reproducible corpus, never a secret
    examples: list[RoutingExample] = []
    drawn: set[tuple[str, tuple[str, ...]]] = set()
    # `seen` is the caller's cross-intent set and is deliberately NOT re-bound
    # here. Shadowing it with a fresh set would make the de-duplication
    # per-intent, silently defeating the invariant this module's whole design
    # rests on: no utterance may carry two labels. It happens to be invisible at
    # the shipped seed, where no two template families collide, which is exactly
    # why it has to be fixed rather than noted — a latent label collision at a
    # higher --per-intent is the failure mode nobody would debug from the symptom.
    attempts = 0
    budget = max(target * 400, 10_000)
    while len(examples) < target:
        attempts += 1
        if attempts > budget:
            raise DataValidationError(
                f"{intent}: produced {len(examples)} of {target} unique examples from "
                f"{len(templates)} templates in {budget} attempts; add template families "
                "or lower per_intent"
            )
        template = templates[rng.randrange(len(templates))]
        slots = _sample_slots(rng, template.slots)
        # A slot combination may be drawn once only. Without this the corpus fills
        # its quota with the same sentence wearing different decorations, which is
        # precisely the padding this dataset is meant not to contain — and which
        # the validator would report as near-duplicates.
        combo = (template.template_id, tuple(sorted(slots.items())))
        if combo in drawn:
            continue
        drawn.add(combo)
        text = _decorate(rng, template, _render(template.text, slots))
        key = normalize_text(text)
        if not key or key in seen:
            continue
        seen.add(key)
        examples.append(
            RoutingExample(
                text=text,
                intent=str(intent),
                provenance=Provenance.SYNTHETIC,
                template_id=template.template_id,
                source=_SOURCE,
            )
        )
    return examples


def _sample_slots(rng: random.Random, names: Sequence[str]) -> dict[str, Any]:
    """Draw a value for every slot a template declares.

    Args:
        rng: The per-intent generator.
        names: The slot names the template references.

    Returns:
        A mapping covering exactly those names.
    """
    return {name: rng.choice(_SLOT_VALUES[name]) for name in names}


@dataclass(frozen=True, slots=True)
class BuildStats:
    """What the build produced, for the run manifest and the builder report."""

    per_intent: Mapping[str, int]
    total: int
    seed: int

    def to_dict(self) -> dict[str, Any]:
        """Serialise deterministically.

        Returns:
            A JSON-ready mapping with sorted keys, including the dataset version
            so a stats block read on its own still says which generator produced
            the corpus.
        """
        return {
            "dataset_version": ROUTING_DATASET_VERSION,
            "per_intent": dict(sorted(self.per_intent.items())),
            "total": self.total,
            "seed": self.seed,
            "balanced": len(set(self.per_intent.values())) == 1,
        }


def label_map() -> dict[str, int]:
    """The stable intent-name to class-index mapping the training head uses.

    Indices follow the taxonomy's own order rather than alphabetical order. That
    is a deliberate choice with a cost worth naming: sorted order would be
    equally stable and one glance more obvious, but the taxonomy order is the
    order the examples are generated in and the order the routing classes were
    *chosen* in — routers first, then the two model classes, then abstention. A
    class index that preserves that is readable in a confusion matrix, where a
    head sitting next to its escalation classes tells you something at a glance.

    Returns:
        Mapping of every intent name to its index.
    """
    return {name: index for index, name in enumerate(INTENT_NAMES)}


def routing_dataset_version() -> str:
    """The version of this generator.

    Returns:
        The dataset version string.
    """
    return ROUTING_DATASET_VERSION


def build_routing_dataset(
    *,
    seed: int = 20260101,
    per_intent: int = 200,
    capability_inventory: CapabilityInventory | None = None,
) -> tuple[list[RoutingExample], BuildStats]:
    """Build the labelled routing corpus.

    Args:
        seed: Master seed. Each intent derives its own generator from it as
            ``seed * 1000003 + index_of_intent``, so a template added to one
            intent leaves every other intent's rows byte-identical.
        per_intent: Examples per intent. Applied to all fourteen equally, because
            a template corpus is already a distortion of the real request
            distribution and letting one class dwarf another would add a second
            one on top.
        capability_inventory: Harvested capability vocabulary. When None the
            inventory-derived families are skipped, which yields fewer examples
            per intent rather than invented ones.

    Returns:
        The examples and the statistics describing them.

    Raises:
        DataValidationError: ``per_intent`` is not positive, or the template
            families cannot fill the requested balance.
    """
    if per_intent < 1:
        raise DataValidationError(f"per_intent must be positive, got {per_intent}")
    vocabulary = _vocabulary(capability_inventory)
    examples: list[RoutingExample] = []
    counts: dict[str, int] = {}
    seen: set[str] = set()
    for index, name in enumerate(INTENT_NAMES):
        intent = Intent(name)
        templates = _build_templates(intent, vocabulary)
        built = _generate(
            intent,
            templates,
            seed=seed * 1000003 + index,
            target=per_intent,
            seen=seen,
        )
        examples.extend(built)
        counts[name] = len(built)
    if len(set(counts.values())) != 1:
        raise DataValidationError(f"intents are not balanced: {sorted(counts.items())}")
    return examples, BuildStats(per_intent=counts, total=len(examples), seed=seed)


def build_routing_records(
    *,
    seed: int = 20260101,
    per_intent: int = 200,
    capability_inventory: CapabilityInventory | None = None,
) -> list[dict[str, Any]]:
    """Build the corpus in its serialised ``routing_intent.v1`` form.

    Args:
        seed: Master seed, as for :func:`build_routing_dataset`.
        per_intent: Examples per intent.
        capability_inventory: Harvested capability vocabulary.

    Returns:
        One mapping per example, ready for :func:`~ml.datasets.schema.write_jsonl`.
    """
    examples, _ = build_routing_dataset(
        seed=seed,
        per_intent=per_intent,
        capability_inventory=capability_inventory,
    )
    return [example.to_dict() for example in examples]
