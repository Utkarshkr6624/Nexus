"""The 14-class intent taxonomy the Phase 10 routing model predicts.

**This taxonomy is derived from the router inventory, not from chatbot priors.**
A generic assistant taxonomy (``small_talk``, ``sentiment_analysis``, ``math``)
describes what a model *could* do; this one describes what NEXUS *actually
does*. Every ``DestinationKind.ROUTER`` intent names a router that exists in
``backend/app/api/v1/`` and a surface it can reach, because an intent whose
destination is unimplemented is a class the model can learn to predict and the
runtime can only refuse — which teaches the classifier to look smart and the
product to do nothing. The eleven router intents therefore track the twenty
routers and their 185 routes rather than any external benchmark's label set.

**Why fourteen and not twenty.** The routers are not the label set. ``tags``
and ``work_sessions`` have no utterance that a person would phrase as a request
on their own — you tag something as a side effect of organising a task, and you
log a session as a side effect of scheduling one — so they fold into
``TASK_MANAGE`` and ``SCHEDULE_PLAN``. ``recommendations`` folds into
``RISK_QUERY`` because a recommendation *is* a surfaced risk: both name an
action a person takes, and NEXUS auto-executes none of them. Conversely
``KNOWLEDGE_CAPTURE`` and ``KNOWLEDGE_LOOKUP`` stay split even though both land
on ``api/v1/knowledge``, because the write path (capture, ingest, tag, link)
and the read path (search, snippet, source) fail in different ways and a single
class would average their errors together.

**``CODE_ASSIST`` and ``DEEP_REASONING`` exist to protect the 8B model.** They
are the only two classes with ``DestinationKind.LARGE_MODEL``, and they are
listed in the taxonomy *before* the router classes exist precisely so that
"route this to Qwen" is a narrow, learnable decision rather than the default.
If every class could reach the model, the classifier would be decorative and
every trivial *"mark it done"* would pay generation latency to be answered by a
router that already exists. Keeping the model classes few and explicit is what
makes the deterministic engine the default path — the rule Phase 8/9 wrote
down, *"deterministic before learned… the deterministic engine stays as the
fallback"*, stated as a property of the label set rather than a hope.

**Out of scope is an explicit abstention, never a silent drop.** ``OUT_OF_SCOPE``
is a trained class with ``DestinationKind.FALLBACK`` and destination
``abstain``, so "I do not know" is a prediction the model can be *right*
about and the evaluation can score. Its fallback policy is explicit and is
recorded in the spec's own description: escalate to the large model when the
utterance is at least plausibly answerable in context, otherwise ask for
clarification naming the surface the user might have meant. Dropping the row
would delete exactly the examples that teach the model the boundary, and
guessing a router would trade a visible abstention for an invisible wrong
answer written into the user's calendar.

The version string follows the ``*_features.v1`` convention: a dataset stamped
``routing_intent.v1`` plus ``taxonomy_version`` says exactly which label set
every label belongs to, so a v2 taxonomy cannot be trained against a v1 corpus
by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ml.datasets.schema import DataValidationError

#: Version of the intent label set. Bump on any rename, addition or removal of
#: an :class:`Intent` member; a routing dataset carrying a stale version is
#: training against a label set that no longer exists.
TAXONOMY_VERSION = "nexo_intents.v1"


class DestinationKind(StrEnum):
    """What a predicted intent is allowed to reach.

    The point of a closed set here is enforcement, not description: a caller
    can branch on the kind without parsing ``destination``, and adding a fourth
    kind — say one that executes a tool — becomes a visible change to this enum
    rather than a new string appearing in a data file.
    """

    ROUTER = "router"
    """An existing NEXUS API router handles it; no generation is involved."""

    LARGE_MODEL = "large_model"
    """Qwen3-8B answers it in text; no router call follows."""

    FALLBACK = "fallback"
    """No router fits. Escalate or ask, per the spec's stated policy."""


class Intent(StrEnum):
    """The fourteen classes the routing model predicts.

    Values are the stable strings written into ``routing_intent.v1`` rows, so
    they are the model's labels, the validation set's labels and the runtime's
    lookup keys simultaneously. Order is meaningful: it is the order the
    examples are generated in and the order a report lists classes in.
    """

    TASK_MANAGE = "task_manage"
    PROJECT_MANAGE = "project_manage"
    SCHEDULE_PLAN = "schedule_plan"
    KNOWLEDGE_CAPTURE = "knowledge_capture"
    KNOWLEDGE_LOOKUP = "knowledge_lookup"
    ANALYTICS_INSIGHT = "analytics_insight"
    RISK_QUERY = "risk_query"
    DEVELOPER_INTEL = "developer_intel"
    LEARNING_TRACK = "learning_track"
    CAREER_TRACK = "career_track"
    ACCOUNT_ADMIN = "account_admin"
    CODE_ASSIST = "code_assist"
    DEEP_REASONING = "deep_reasoning"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass(frozen=True, slots=True)
class IntentSpec:
    """What one intent means, where it goes, and how it is recognised.

    Frozen because the taxonomy is data, not state: a generator, a validator
    and the runtime all read the same specs, and none of them may edit one.
    """

    intent: Intent
    description: str
    destination: str
    destination_kind: DestinationKind
    examples: tuple[str, ...]
    keywords: tuple[str, ...]


INTENT_SPECS: tuple[IntentSpec, ...] = (
    IntentSpec(
        intent=Intent.TASK_MANAGE,
        description=(
            "Creating, editing, completing, re-dating or deleting a task, and "
            "asking what tasks exist. Everything a person does to the 16 routes "
            "on the tasks router."
        ),
        destination="api/v1/tasks",
        destination_kind=DestinationKind.ROUTER,
        examples=(
            "Mark the API contract task as done",
            "Add a task to draft the migration plan for Friday",
            "What tasks are still open on the Nexo rewrite?",
            "Push the deadline on the auth refactor out by two days",
            "Delete that duplicate reminder I created",
        ),
        keywords=(
            "task",
            "tasks",
            "todo",
            "subtask",
            "due date",
            "status",
            "completed",
            "assignee",
            "estimate",
            "priority",
            "backlog",
        ),
    ),
    IntentSpec(
        intent=Intent.PROJECT_MANAGE,
        description=(
            "Creating, editing, archiving or asking about a project and its "
            "members. Distinct from tasks because a project carries the "
            "progress and milestone figures the task router cannot answer."
        ),
        destination="api/v1/projects",
        destination_kind=DestinationKind.ROUTER,
        examples=(
            "Show me the projects I'm working on",
            "Create a project for the mobile redesign",
            "Archive the old marketing site project",
            "What is the status of the payments integration?",
            "Add a milestone to the Nexo rollout",
        ),
        keywords=(
            "project",
            "projects",
            "milestone",
            "status",
            "owner",
            "members",
            "scope",
            "archive",
            "roadmap",
            "progress",
            "deliverable",
        ),
    ),
    IntentSpec(
        intent=Intent.SCHEDULE_PLAN,
        description=(
            "Planning where work happens in time: day views, availability "
            "windows, time blocks and logged work sessions. The planner router "
            "is the destination; calendar, work-sessions and availability are "
            "the sibling surfaces it plans against."
        ),
        destination="api/v1/planner",
        destination_kind=DestinationKind.ROUTER,
        examples=(
            "What does my calendar look like on Thursday?",
            "Block two hours tomorrow morning for the proposal",
            "Schedule a focus block for deep work on Friday afternoon",
            "When am I free next week?",
            "Log a work session for the schema migration",
        ),
        keywords=(
            "schedule",
            "calendar",
            "availability",
            "free",
            "time block",
            "focus",
            "work session",
            "plan",
            "day view",
            "reschedule",
            "meeting",
        ),
    ),
    IntentSpec(
        intent=Intent.KNOWLEDGE_CAPTURE,
        description=(
            "Putting something into the knowledge base: saving a note, filing a "
            "link, capturing a decision or a meeting summary. The write half of "
            "the knowledge router, kept separate from lookup because capture "
            "fails on missing metadata while lookup fails on a bad match."
        ),
        destination="api/v1/knowledge",
        destination_kind=DestinationKind.ROUTER,
        examples=(
            "Save a note about the auth token rotation policy",
            "Store this article on vector databases in my knowledge base",
            "Add a link to the ADR on deterministic scoring",
            "Capture that decision so I don't lose it",
            "File this meeting summary under the Nexo project",
        ),
        keywords=(
            "note",
            "capture",
            "save",
            "knowledge base",
            "link",
            "summary",
            "bookmark",
            "document",
            "source",
            "reference",
            "inbox",
        ),
    ),
    IntentSpec(
        intent=Intent.KNOWLEDGE_LOOKUP,
        description=(
            "Getting something back out of the knowledge base: searching saved "
            "material or recalling a decision previously recorded. Same router "
            "as capture, opposite direction, and the retrieval path whose "
            "failure modes the lookup evaluation measures."
        ),
        destination="api/v1/knowledge",
        destination_kind=DestinationKind.ROUTER,
        examples=(
            "What did we decide about the risk scoring thresholds?",
            "Find the note where I wrote down the Qwen dataset plan",
            "Search my knowledge base for anything about time estimation",
            "Which sources did I save about feature stores?",
            "Look up that Postgres partitioning doc I saved",
        ),
        keywords=(
            "search",
            "look up",
            "find",
            "recall",
            "saved note",
            "knowledge",
            "references",
            "sources",
            "snippet",
            "results",
            "what did i write",
        ),
    ),
    IntentSpec(
        intent=Intent.ANALYTICS_INSIGHT,
        description=(
            "Asking about measured work patterns: productivity, throughput, "
            "focus trends, completion rates and period comparisons. Read-only, "
            "and answered from the deterministic scorers — nothing here writes "
            "anything back."
        ),
        destination="api/v1/analytics",
        destination_kind=DestinationKind.ROUTER,
        examples=(
            "How productive was I last week?",
            "Show my focus trend over the last 30 days",
            "What is my task throughput per project?",
            "Compare my planned hours against actual hours this week",
            "Summarise my activity over the last month",
        ),
        keywords=(
            "analytics",
            "insight",
            "trend",
            "throughput",
            "productivity",
            "focus",
            "completion rate",
            "activity",
            "statistics",
            "score",
            "this week",
            "chart",
        ),
    ),
    IntentSpec(
        intent=Intent.RISK_QUERY,
        description=(
            "Asking what could go wrong: which deadlines are at risk, where the "
            "workload exceeds capacity, which conflicts block progress. Covers "
            "the recommendations surface too, because every RecommendationType "
            "names an action a person takes rather than an execution the system "
            "performs."
        ),
        destination="api/v1/risks",
        destination_kind=DestinationKind.ROUTER,
        examples=(
            "What risks am I carrying right now?",
            "Which of my deadlines are at risk?",
            "Am I overcommitted this week?",
            "Show the scheduling conflicts that are blocking me",
            "What should I review before the project slips?",
        ),
        keywords=(
            "risk",
            "risks",
            "at risk",
            "deadline risk",
            "workload",
            "conflict",
            "overdue",
            "slip",
            "blocked",
            "consistency",
            "exposure",
            "mitigation",
        ),
    ),
    IntentSpec(
        intent=Intent.DEVELOPER_INTEL,
        description=(
            "Asking about engineering activity: commits, pull requests, "
            "repositories touched, languages and streaks. Reads the developer "
            "router's repository-derived figures, which is why it is separate "
            "from general analytics."
        ),
        destination="api/v1/developer",
        destination_kind=DestinationKind.ROUTER,
        examples=(
            "How active was I on GitHub this week?",
            "Summarise my commit activity on the backend repo",
            "Which repositories have I been touching lately?",
            "Show my developer streak and my recent pull requests",
            "What languages have I been committing in?",
        ),
        keywords=(
            "repository",
            "repo",
            "commit",
            "commits",
            "pull request",
            "github",
            "streak",
            "branch",
            "language",
            "contribution",
            "developer activity",
            "commit activity",
        ),
    ),
    IntentSpec(
        intent=Intent.LEARNING_TRACK,
        description=(
            "Study goals and progress against them: hours logged toward a "
            "learning goal, what to pick up next, which skills are being "
            "neglected. The learning router's goals and sessions, never the "
            "career router's ladder."
        ),
        destination="api/v1/learning",
        destination_kind=DestinationKind.ROUTER,
        examples=(
            "How is my learning goal for distributed systems going?",
            "What should I study next to keep up with my goals?",
            "Log two hours of learning on Rust this week",
            "Am I making progress toward the certification target?",
            "Which skills am I neglecting?",
        ),
        keywords=(
            "learning",
            "goal",
            "study",
            "course",
            "certification",
            "skill",
            "practice",
            "progress",
            "curriculum",
            "syllabus",
            "level",
            "hours",
        ),
    ),
    IntentSpec(
        intent=Intent.CAREER_TRACK,
        description=(
            "Longer-horizon questions about role fit: progress toward a target "
            "role, which competencies to build, how repository activity reads "
            "against a career profile. Deliberately distinct from learning "
            "tracking — one is study, the other is standing."
        ),
        destination="api/v1/career",
        destination_kind=DestinationKind.ROUTER,
        examples=(
            "Am I on track for the senior engineer role?",
            "How does my repository activity affect my career path?",
            "Which skills should I build for the next promotion?",
            "Show my target roles and how I measure up",
            "What does my career profile say I'm strong at?",
        ),
        keywords=(
            "career",
            "role",
            "promotion",
            "seniority",
            "target role",
            "skills",
            "profile",
            "growth",
            "competency",
            "ladder",
            "position",
            "level",
        ),
    ),
    IntentSpec(
        intent=Intent.ACCOUNT_ADMIN,
        description=(
            "Changing or inspecting the account itself: profile fields, "
            "timezone, availability windows and what the signed-in user is "
            "permitted to see. Every action is gated by a Permission value, so "
            "this class exists as much for the permission check as for the route."
        ),
        destination="api/v1/users",
        destination_kind=DestinationKind.ROUTER,
        examples=(
            "Update my timezone to Europe/Lisbon",
            "Change the email address on my account",
            "Set my availability windows for next week",
            "Who is allowed to see my projects?",
            "Update my notification preferences",
        ),
        keywords=(
            "account",
            "profile",
            "settings",
            "timezone",
            "email",
            "password",
            "permission",
            "role",
            "preferences",
            "admin",
            "access",
            "sign in",
        ),
    ),
    IntentSpec(
        intent=Intent.CODE_ASSIST,
        description=(
            "Writing, reading or debugging source code: generating a function, "
            "explaining an error, refactoring a snippet. One of only two classes "
            "that may reach the 8B model, and the cheaper of the two — the "
            "answer is code-shaped and the prompt does not need to be reasoned "
            "out at length."
        ),
        destination="qwen3-8b",
        destination_kind=DestinationKind.LARGE_MODEL,
        examples=(
            "Write a Python function that parses an ISO timestamp with an offset",
            "Refactor this SQLAlchemy query so it stops doing an N+1 join",
            "Explain what this stack trace actually means",
            "Write a pytest fixture that hands out a database session",
            "Generate a FastAPI dependency that checks a permission",
        ),
        keywords=(
            "code",
            "function",
            "refactor",
            "python",
            "sql",
            "bug",
            "stack trace",
            "exception",
            "unit test",
            "snippet",
            "compile",
            "api endpoint",
        ),
    ),
    IntentSpec(
        intent=Intent.DEEP_REASONING,
        description=(
            "Multi-step analysis with no correct endpoint: comparing designs, "
            "weighing trade-offs, defending a choice. Needs long-form generation "
            "and is the most expensive class, so the router must be confident "
            "before paying for it."
        ),
        destination="qwen3-8b",
        destination_kind=DestinationKind.LARGE_MODEL,
        examples=(
            "Compare three ways to model scheduling conflicts and argue which one I'd pick",
            "Work through the trade-offs of replacing the deterministic scorer with a learned one",
            "Design the architecture for a Phase 10 training pipeline and defend the choices",
            "Reason about why my accuracy drops on the long tail and what to do about it",
            "Decide which of these two scheduling algorithms fits a bounded planner",
        ),
        keywords=(
            "trade-offs",
            "design",
            "architecture",
            "compare",
            "justify",
            "reasoning",
            "why",
            "decide",
            "evaluate",
            "pros and cons",
            "strategy",
            "approach",
        ),
    ),
    IntentSpec(
        intent=Intent.OUT_OF_SCOPE,
        description=(
            "An utterance NEXUS has no surface for — weather, news, sport, "
            "chat. Trained explicitly so abstention is a *correct* prediction "
            "the evaluation can score rather than a dropped row. Fallback "
            "policy, in order: escalate to qwen3-8b when the utterance is "
            "plausibly answerable in context and carries no false commitment to "
            "Nexo data; otherwise ask the user to clarify and name the surfaces "
            "they may have meant. Never drop the turn and never guess a router "
            "— a wrong write into the calendar is worse than an admitted gap."
        ),
        destination="abstain",
        destination_kind=DestinationKind.FALLBACK,
        examples=(
            "What's the weather in Porto tomorrow?",
            "Book me a flight to Lisbon next Tuesday",
            "Who won the derby last night?",
            "Put on some jazz while I work",
            "What's 2 + 2?",
        ),
        keywords=(
            "weather",
            "forecast",
            "news",
            "sports",
            "football",
            "music",
            "travel",
            "flight",
            "restaurant",
            "recipe",
            "joke",
            "small talk",
        ),
    ),
)

INTENTS: tuple[Intent, ...] = tuple(spec.intent for spec in INTENT_SPECS)

INTENT_NAMES: tuple[str, ...] = tuple(str(intent) for intent in INTENTS)

#: The only classes allowed to reach the 8B model. Derived from the specs rather
#: than re-declared, so the enum and the data can never disagree about which
#: classes are expensive.
LARGE_MODEL_INTENTS: frozenset[Intent] = frozenset(
    spec.intent for spec in INTENT_SPECS if spec.destination_kind is DestinationKind.LARGE_MODEL
)

#: Classes handled by an existing NEXUS router.
ROUTER_INTENTS: frozenset[Intent] = frozenset(
    spec.intent for spec in INTENT_SPECS if spec.destination_kind is DestinationKind.ROUTER
)

#: Classes with no router behind them; these take the spec's fallback policy.
FALLBACK_INTENTS: frozenset[Intent] = frozenset(
    spec.intent for spec in INTENT_SPECS if spec.destination_kind is DestinationKind.FALLBACK
)

#: What the router predicts when it is not confident enough to name a class.
#: Pointing the default at the abstention class keeps an uncertain prediction
#: visibly uncertain instead of silently writing to the wrong surface.
UNKNOWN_INTENT = Intent.OUT_OF_SCOPE

_SPECS_BY_NAME: dict[str, IntentSpec] = {str(spec.intent): spec for spec in INTENT_SPECS}


def intent_spec(name: str) -> IntentSpec:
    """Look up the spec for an intent name.

    Args:
        name: An :class:`Intent` value, e.g. ``"task_manage"``.

    Returns:
        The matching spec.

    Raises:
        DataValidationError: The name is not a member of :class:`Intent`. Training
            data carrying an unknown label is a data-integrity failure, so it
            stops the pipeline rather than being coerced to the nearest class.
    """
    try:
        return _SPECS_BY_NAME[name]
    except KeyError as exc:
        raise DataValidationError(
            f"unknown intent {name!r}; expected one of {sorted(_SPECS_BY_NAME)}"
        ) from exc


def is_valid_intent(name: str) -> bool:
    """Whether a label belongs to this taxonomy.

    Args:
        name: The candidate label.

    Returns:
        True when :func:`intent_spec` would resolve it.
    """
    return name in _SPECS_BY_NAME


def taxonomy_as_dict() -> dict:
    """Serialise the whole taxonomy deterministically.

    The grouping by destination kind is included because the runtime's first
    branch is "does this class cost a generation?", and a report that only
    listed labels would force the reader to recompute it.

    Returns:
        A JSON-ready mapping with sorted key order.
    """
    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "unknown_intent": str(UNKNOWN_INTENT),
        "destinations": {
            "large_model": sorted(str(intent) for intent in LARGE_MODEL_INTENTS),
            "router": sorted(str(intent) for intent in ROUTER_INTENTS),
            "fallback": sorted(str(intent) for intent in FALLBACK_INTENTS),
        },
        "intents": [
            {
                "intent": str(spec.intent),
                "description": spec.description,
                "destination": spec.destination,
                "destination_kind": str(spec.destination_kind),
                "examples": list(spec.examples),
                "keywords": list(spec.keywords),
            }
            for spec in INTENT_SPECS
        ],
    }


__all__ = [
    "FALLBACK_INTENTS",
    "INTENTS",
    "INTENT_NAMES",
    "INTENT_SPECS",
    "LARGE_MODEL_INTENTS",
    "ROUTER_INTENTS",
    "TAXONOMY_VERSION",
    "UNKNOWN_INTENT",
    "DestinationKind",
    "Intent",
    "IntentSpec",
    "intent_spec",
    "is_valid_intent",
    "taxonomy_as_dict",
]
