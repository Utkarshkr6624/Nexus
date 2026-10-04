"""The shared vocabulary of the Phase 3 work-management schema.

Projects, tasks, tags and the activity feed all describe the same handful of
states and grades, and every layer above the ORM — schemas, services, the API and
the frontend — needs to speak about them. They live here, in a module with no
model imports, so that a schema module can import an enum without importing the
tables and a repository can import both without a cycle.

Every one of these is a :class:`~enum.StrEnum`, so a member *is* its persisted
string: ``TaskStatus.TODO == "todo"`` is true, and a value read straight out of a
column can be compared against a member without unwrapping anything.

Why plain ``String`` columns rather than native PostgreSQL enums
--------------------------------------------------------------
The reasoning is :class:`app.models.user.UserRole`'s, and it is worth restating
where the whole Phase 3 vocabulary is defined, because Phase 3 multiplies the
decision from one column to five:

* **Adding a value must be an ordinary transaction.** Growing a native Postgres
  enum needs ``ALTER TYPE ... ADD VALUE``, which historically could not run
  inside a transaction block and so fails halfway through a deploy on some
  deployment paths. Shipping a new task status should be an application
  release, not a migration that can only half-apply. A string with an
  application-side enum adds the value in a plain transaction; a migration is
  only needed for a *column* change.
* **The database cannot be the only place the vocabulary is written.** Rows can
  arrive from a bulk import, a fixture, an admin script or a future service, so
  the value has to be checkable wherever the row is written — which is exactly
  what the ``validate_*`` helpers below are for, and where they can be tested.
* **Rename and delete are free.** Postgres has no ``ALTER TYPE ... RENAME
  VALUE`` before PostgreSQL 10 and no drop at all; the rows are the contract.

The trade-off is real and is not hidden: the database will accept
``status = 'in_progres'``. That is why every write path funnels through
:func:`validate_task_status` rather than trusting the column, and why a stray
value degrades to an unrenderable row rather than a corrupt one — nothing else in
the schema depends on the ordering or completeness of these values.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "ActivityEvent",
    "CalendarEventType",
    "CareerEvidenceType",
    "CareerRecordKind",
    "EvidenceStrength",
    "GitScanStatus",
    "KnowledgeEntityType",
    "KnowledgeLinkType",
    "LearningActivityType",
    "LearningGoalStatus",
    "NoteStatus",
    "ProjectPriority",
    "ProjectStatus",
    "RecommendationPriority",
    "RecommendationStatus",
    "RecommendationType",
    "ResourceType",
    "RiskSeverity",
    "RiskStatus",
    "RiskType",
    "SkillLevelSource",
    "TaskPriority",
    "TaskStatus",
    "WorkSessionStatus",
    "validate_activity_event",
    "validate_calendar_event_type",
    "validate_career_evidence_type",
    "validate_career_record_kind",
    "validate_git_scan_status",
    "validate_knowledge_entity_type",
    "validate_knowledge_link_type",
    "validate_learning_activity_type",
    "validate_learning_goal_status",
    "validate_note_status",
    "validate_project_priority",
    "validate_project_status",
    "validate_recommendation_status",
    "validate_recommendation_type",
    "validate_resource_type",
    "validate_risk_status",
    "validate_risk_type",
    "validate_skill_level_source",
    "validate_task_priority",
    "validate_task_status",
    "validate_work_session_status",
]


class ProjectStatus(StrEnum):
    """Where a project sits in its life.

    A project is never deleted in Phase 3; it is archived and then left alone, so
    the last two members — and only the last two — are terminal. A :class:`ProjectStatus`
    is persisted on ``projects.status``.
    """

    PLANNED = "planned"
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class ProjectPriority(StrEnum):
    """How much a project competes for attention.

    Deliberately the same four grades as :class:`TaskPriority`: a project and
    the work inside it are prioritised on one scale, so the UI can sort a
    project and its tasks with the same control. Persisted on
    ``projects.priority``.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TaskStatus(StrEnum):
    """Where a task sits on the board.

    ``COMPLETED`` and ``CANCELLED`` are the two states a task is *not* done in.
    Keeping "dropped" separate from "finished" is what lets the dashboard's
    overdue query exclude the real backlog without also counting work that was
    deliberately abandoned. Persisted on ``tasks.status``.
    """

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskPriority(StrEnum):
    """How much a task competes for attention.

    The same four grades as :class:`ProjectPriority`; see that class for why the
    two scales are deliberately identical. Persisted on ``tasks.priority``.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CalendarEventType(StrEnum):
    """What a calendar entry *is*, as opposed to where it sits in time.

    The calendar carries three different kinds of row — something to attend, time
    deliberately not spent working, and a date that means nothing until the work
    lands on it — and they behave differently enough to be worth naming. A
    ``meeting`` blocks a slot; a ``break`` releases one; a ``deadline`` does
    neither, it only bounds the scheduler. A calendar that had one generic
    ``event`` member would force every one of those rules to be re-derived from
    the title at read time.

    ``OTHER`` is a genuine member rather than a "no type supplied" hole: a user
    importing a calendar from elsewhere should not have to lie about what an
    appointment is to make it appear. Persisted on ``calendar_events.event_type``.
    """

    WORK = "work"
    STUDY = "study"
    MEETING = "meeting"
    PERSONAL = "personal"
    BREAK = "break"
    DEADLINE = "deadline"
    OTHER = "other"


class WorkSessionStatus(StrEnum):
    """Where a work session sits between planned and finished.

    ``ACTIVE`` is the running timer, and it is the only state with a live
    ``actual_start``. Exactly one session per user is expected to be active at a
    time — which the service layer enforces rather than the database, because
    "the most recent session still running" is a query, not an invariant.

    It lives here rather than beside the table because two layers above the ORM
    need to name it: the request schemas (to validate a status on the way in)
    and the service (to compare against ``.ACTIVE``). A member that only its own
    table used would be a local detail; one that the API surface speaks is
    vocabulary, and vocabulary belongs in this module. Persisted on
    ``work_sessions.status``.
    """

    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class NoteStatus(StrEnum):
    """Where a note sits between "I just typed this" and "this is done".

    Deliberately three members and not a general-purpose workflow: a knowledge
    base that can model fourteen note states has not been given a requirement it
    can satisfy, and every extra state is a value the list filters and the
    counts have to keep exhaustive.

    * ``DRAFT`` is the default and what an autosaving editor produces. It is not
      "unfinished" in any judgemental sense — it is simply not asserted yet.
    * ``PUBLISHED`` is the user's explicit "this is real now" and is the only
      state a link target is *meant* to point at. Nothing in the schema forbids
      linking to a draft, but the UI filters to published by default.
    * ``ARCHIVED`` is set aside, and is reachable only through the archive
      action rather than through a PATCH, so that the archived set cannot be
      left by a generic edit.

    Persisted on ``notes.status``.
    """

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class KnowledgeEntityType(StrEnum):
    """Which kind of thing an edge endpoint names.

    ``knowledge_links`` is **polymorphic**: one table holds edges between notes,
    concepts and resources rather than one table per pair (there are six pairs,
    and a seventh entity type would make seven tables). The price is that
    ``source_id``/``target_id`` carry **no foreign key** — a FK can only point at
    one table, and the table it would point at is decided by this column. So the
    database cannot stop a dangling or cross-user edge, and
    :meth:`~app.services.knowledge_service.KnowledgeService.create_link` resolves
    both endpoints through an owner-scoped query before writing one. That comment
    is the security model of this table; do not read it as decoration.

    Persisted on ``knowledge_links.source_type`` and ``.target_type``.
    """

    NOTE = "note"
    CONCEPT = "concept"
    RESOURCE = "resource"


class KnowledgeLinkType(StrEnum):
    """What an edge between two knowledge objects *means*.

    Extensible by design: the spec explicitly forbids hardcoding relationship
    types through the application, so this is one table the UI filters on rather
    than a switch statement per screen. Adding a member here is an ordinary
    transaction and needs no migration.

    The six members come straight from the Phase 5 brief::

        NOTE -> REFERENCES -> NOTE
        NOTE -> EXPLAINS   -> CONCEPT
        CONCEPT -> RELATED_TO -> CONCEPT
        RESOURCE -> SUPPORTS -> CONCEPT
        PROJECT -> USES -> CONCEPT
        TASK -> REQUIRES -> CONCEPT

    The last two name projects and tasks as endpoints, which this phase does not
    yet model: the entity-type vocabulary is deliberately the three Phase 5
    objects, and linking a project to a concept is a later phase's extension to
    both enums rather than a value that exists but can never resolve.

    ``REFERENCES`` is the one the spec cares most about: it is what produces
    backlinks, and a note is far more often the *target* of a reference than the
    source, which is exactly why the link table carries a ``(target_type,
    target_id)`` index.

    Persisted on ``knowledge_links.link_type``.
    """

    REFERENCES = "references"
    EXPLAINS = "explains"
    RELATED_TO = "related_to"
    SUPPORTS = "supports"
    USES = "uses"
    REQUIRES = "requires"


class ResourceType(StrEnum):
    """What kind of external thing a resource points at.

    ``OTHER`` is a real member rather than a nullable column with no default: a
    user saving a link to something that fits none of the eight kinds should not
    have to lie, and "other" is also the value a type filter matches to mean
    "the ones I did not classify". Persisted on ``resources.resource_type``.
    """

    ARTICLE = "article"
    VIDEO = "video"
    COURSE = "course"
    DOCUMENTATION = "documentation"
    REPOSITORY = "repository"
    PAPER = "paper"
    WEBSITE = "website"
    OTHER = "other"


class ActivityEvent(StrEnum):
    """The work-management events worth leaving a trail of.

    Separate from :class:`app.models.audit.AuditEvent`, which records
    *security* events. These record what happened to the work: the same feed
    answers "what did I do to this project last Tuesday" and "who closed this".
    They live in ``activity_events.event_type``, which — like the audit column —
    is filtered on by consumers, so the spelling is part of the contract and is
    never renamed once rows exist.

    The lifecycle members mirror the status transitions: ``TASK_STARTED`` is the
    ``todo -> in_progress`` edge, ``TASK_COMPLETED`` the edge into ``completed``,
    ``TASK_REOPENED`` the edge back out of it, and ``TASK_BLOCKED`` the edge into
    ``blocked``. ``TASK_SCHEDULED`` is the deliberate edge *not* tied to a status
    column — it is the "I put a date on this" moment, which happens far more
    often than the task actually starting.
    """

    PROJECT_CREATED = "project_created"
    PROJECT_UPDATED = "project_updated"
    PROJECT_COMPLETED = "project_completed"
    PROJECT_ARCHIVED = "project_archived"
    PROJECT_RESTORED = "project_restored"
    # The counterpart to ``TASK_DELETED``, and it exists for the same reason: a
    # delete is a moment that happened, not a mutation of a row that still
    # exists. Recording it as ``PROJECT_UPDATED`` left a consumer filtering on
    # the delete unable to see a deletion at all, and left every "what changed
    # in this project" reader counting the removal as an edit.
    PROJECT_DELETED = "project_deleted"
    TASK_CREATED = "task_created"
    TASK_UPDATED = "task_updated"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_REOPENED = "task_reopened"
    TASK_BLOCKED = "task_blocked"
    TASK_PRIORITY_CHANGED = "task_priority_changed"
    TASK_DUE_DATE_CHANGED = "task_due_date_changed"
    TASK_DELETED = "task_deleted"
    TASK_SCHEDULED = "task_scheduled"
    # -- Phase 4 (Planner) --------------------------------------------------
    # The calendar and the clock are separate systems that both move tasks, so
    # both are recorded: the *session* events are about time actually spent,
    # the *calendar* events about time deliberately reserved, and neither
    # implies the other.
    WORK_SESSION_STARTED = "work_session_started"
    WORK_SESSION_COMPLETED = "work_session_completed"
    CALENDAR_EVENT_CREATED = "calendar_event_created"
    CALENDAR_EVENT_UPDATED = "calendar_event_updated"
    CALENDAR_EVENT_DELETED = "calendar_event_deleted"
    TASK_RESCHEDULED = "task_rescheduled"
    PLANNER_SUGGESTION_ACCEPTED = "planner_suggestion_accepted"
    PLANNER_SUGGESTION_REJECTED = "planner_suggestion_rejected"
    # -- Phase 5 (Knowledge) -------------------------------------------------
    # Phase 6 analytics and the Phase 8-9 learning work read this feed to learn
    # *which* knowledge a user actually works with and revisits, so the
    # lifecycle moments are members of their own rather than a generic
    # `NOTE_UPDATED`. `NOTE_REVISION_CREATED` and `NOTE_VIEWED` from the spec are
    # deliberately absent: a revision is recorded by the edit that caused it
    # (already `NOTE_UPDATED`) and a read is not an event on a work feed.
    NOTE_CREATED = "note_created"
    NOTE_UPDATED = "note_updated"
    NOTE_ARCHIVED = "note_archived"
    NOTE_PUBLISHED = "note_published"
    NOTE_RESTORED = "note_restored"
    NOTE_REVISION_RESTORED = "note_revision_restored"
    CONCEPT_CREATED = "concept_created"
    RESOURCE_CREATED = "resource_created"
    BOOKMARK_CREATED = "bookmark_created"
    KNOWLEDGE_LINK_CREATED = "knowledge_link_created"
    KNOWLEDGE_LINK_REMOVED = "knowledge_link_removed"

    # Phase 7. The risk lifecycle and the recommendation feedback loop are
    # recorded here for the same reason the task lifecycle is: a closed set,
    # spelled once, that the columns and the API both validate against. These
    # ten are the training labels Phase 10 will want, so writing them to
    # `activity_events` rather than only to a status column is what preserves
    # *when* and *in what order* a user responded -- the two things a status
    # column cannot express and a classifier would need.
    RISK_DETECTED = "risk_detected"
    RISK_UPDATED = "risk_updated"
    RISK_RESOLVED = "risk_resolved"
    RISK_ACKNOWLEDGED = "risk_acknowledged"
    RISK_DISMISSED = "risk_dismissed"
    RECOMMENDATION_CREATED = "recommendation_created"
    RECOMMENDATION_VIEWED = "recommendation_viewed"
    RECOMMENDATION_ACCEPTED = "recommendation_accepted"
    RECOMMENDATION_REJECTED = "recommendation_rejected"
    RECOMMENDATION_COMPLETED = "recommendation_completed"

    # -- Phase 8 (Developer Intelligence) -------------------------------------
    # Repository and commit facts are recorded here for the same reason the task
    # lifecycle is: they are the trail Phase 6 analytics reads and the surface
    # Phase 10 learns from. A scan writes one REPOSITORY_SCANNED row rather than
    # one row per commit, because a re-scan of an unchanged repository is not new
    # history — COMMIT_DETECTED is reserved for a commit the scan had not seen.
    REPOSITORY_REGISTERED = "repository_registered"
    REPOSITORY_UPDATED = "repository_updated"
    REPOSITORY_SCANNED = "repository_scanned"
    REPOSITORY_REMOVED = "repository_removed"
    COMMIT_DETECTED = "commit_detected"
    BRANCH_CREATED = "branch_created"
    BRANCH_CHANGED = "branch_changed"
    FILE_ACTIVITY_DETECTED = "file_activity_detected"

    # -- Phase 9 (Learning & Career) -----------------------------------------
    # The learning and career lifecycle is recorded here for the same reason the
    # task lifecycle is: these rows are the trail Phase 6 analytics reads and
    # the surface Phase 10 learns from. What is deliberately *not* recorded is
    # anything that would be an inferred quality — there is no `SKILL_LEVEL_
    # ESTIMATED` event, because an estimate is a read-time derivation over the
    # activities below and not a moment that happened. The evidence for a
    # number is the `LEARNING_SESSION_RECORDED` and `SKILL_ACTIVITY_RECORDED`
    # rows; the claim itself is a column, and a column can say who made it.
    LEARNING_GOAL_CREATED = "learning_goal_created"
    LEARNING_GOAL_UPDATED = "learning_goal_updated"
    LEARNING_GOAL_COMPLETED = "learning_goal_completed"
    LEARNING_SESSION_RECORDED = "learning_session_recorded"
    SKILL_CREATED = "skill_created"
    SKILL_UPDATED = "skill_updated"
    SKILL_ACTIVITY_RECORDED = "skill_activity_recorded"
    CAREER_PROFILE_UPDATED = "career_profile_updated"
    CAREER_EVIDENCE_ADDED = "career_evidence_added"
    CAREER_EVIDENCE_UPDATED = "career_evidence_updated"


def validate_project_status(value: ProjectStatus | str) -> ProjectStatus:
    """Coerce a stored or user-supplied value into a :class:`ProjectStatus`.

    Raises:
        ValueError: If the value is not a known status. The column is a string,
            so an unrecognised status is storable and would leave a project
            that no list filter — none of which match an unknown value — can
            ever find again.
    """
    if isinstance(value, ProjectStatus):
        return value
    try:
        return ProjectStatus(value)
    except ValueError:
        raise ValueError(f"Unknown project status: {value!r}") from None


def validate_project_priority(value: ProjectPriority | str) -> ProjectPriority:
    """Coerce a stored or user-supplied value into a :class:`ProjectPriority`.

    Raises:
        ValueError: If the value is not a known priority.
    """
    if isinstance(value, ProjectPriority):
        return value
    try:
        return ProjectPriority(value)
    except ValueError:
        raise ValueError(f"Unknown project priority: {value!r}") from None


def validate_task_status(value: TaskStatus | str) -> TaskStatus:
    """Coerce a stored or user-supplied value into a :class:`TaskStatus`.

    Raises:
        ValueError: If the value is not a known status. This is the boundary
            that keeps the Kanban board's five columns exhaustive: a task
            carrying an unrecognised status belongs to no column and silently
            disappears from the board.
    """
    if isinstance(value, TaskStatus):
        return value
    try:
        return TaskStatus(value)
    except ValueError:
        raise ValueError(f"Unknown task status: {value!r}") from None


def validate_task_priority(value: TaskPriority | str) -> TaskPriority:
    """Coerce a stored or user-supplied value into a :class:`TaskPriority`.

    Raises:
        ValueError: If the value is not a known priority.
    """
    if isinstance(value, TaskPriority):
        return value
    try:
        return TaskPriority(value)
    except ValueError:
        raise ValueError(f"Unknown task priority: {value!r}") from None


def validate_work_session_status(value: WorkSessionStatus | str) -> WorkSessionStatus:
    """Coerce a stored or user-supplied value into a :class:`WorkSessionStatus`.

    Raises:
        ValueError: If the value is not a known status. An unrecognised status
            belongs to no filter, so the session drops out of the "what am I
            running right now" and the "what did I finish" views alike.
    """
    if isinstance(value, WorkSessionStatus):
        return value
    try:
        return WorkSessionStatus(value)
    except ValueError:
        raise ValueError(f"Unknown work session status: {value!r}") from None


def validate_calendar_event_type(value: CalendarEventType | str) -> CalendarEventType:
    """Coerce a stored or user-supplied value into a :class:`CalendarEventType`.

    Raises:
        ValueError: If the value is not a known type. Same reasoning as the
            other validators here: an unrecognised ``event_type`` belongs to no
            filter, so the event becomes invisible to the "show me my meetings"
            view rather than appearing under an unknown heading.
    """
    if isinstance(value, CalendarEventType):
        return value
    try:
        return CalendarEventType(value)
    except ValueError:
        raise ValueError(f"Unknown calendar event type: {value!r}") from None


def validate_activity_event(value: ActivityEvent | str) -> ActivityEvent:
    """Coerce a stored or user-supplied value into an :class:`ActivityEvent`.

    Raises:
        ValueError: If the value is not a known event. Same reasoning as
            :func:`app.models.audit.validate_audit_event`: a mistyped event name
            is written as a row no filter can ever match, so the history silently
            develops a hole where an event should be.
    """
    if isinstance(value, ActivityEvent):
        return value
    try:
        return ActivityEvent(value)
    except ValueError:
        raise ValueError(f"Unknown activity event: {value!r}") from None


def validate_note_status(value: NoteStatus | str) -> NoteStatus:
    """Coerce a stored or user-supplied value into a :class:`NoteStatus`.

    Raises:
        ValueError: If the value is not a known status. The column is a string,
            so an unrecognised status is storable and would leave a note that no
            list filter — none of which match an unknown value — can ever find
            again.
    """
    if isinstance(value, NoteStatus):
        return value
    try:
        return NoteStatus(value)
    except ValueError:
        raise ValueError(f"Unknown note status: {value!r}") from None


def validate_knowledge_entity_type(value: KnowledgeEntityType | str) -> KnowledgeEntityType:
    """Coerce a value into a :class:`KnowledgeEntityType`.

    Raises:
        ValueError: If the value is not a known entity type. This one is more
            than a data-quality concern: ``knowledge_links`` has no foreign key
            on its endpoints precisely because this column chooses the table, so
            an unrecognised type names a table that does not exist and the edge
            can never be resolved, joined or rendered.
    """
    if isinstance(value, KnowledgeEntityType):
        return value
    try:
        return KnowledgeEntityType(value)
    except ValueError:
        raise ValueError(f"Unknown knowledge entity type: {value!r}") from None


def validate_knowledge_link_type(value: KnowledgeLinkType | str) -> KnowledgeLinkType:
    """Coerce a value into a :class:`KnowledgeLinkType`.

    Raises:
        ValueError: If the value is not a known link type. ``link_type`` is half
            of the link table's unique constraint, so an unrecognised value does
            not merely render badly — it defeats the constraint that stops the
            same edge being recorded twice.
    """
    if isinstance(value, KnowledgeLinkType):
        return value
    try:
        return KnowledgeLinkType(value)
    except ValueError:
        raise ValueError(f"Unknown knowledge link type: {value!r}") from None


def validate_resource_type(value: ResourceType | str) -> ResourceType:
    """Coerce a stored or user-supplied value into a :class:`ResourceType`.

    Raises:
        ValueError: If the value is not a known type, for the same reason as
            :func:`validate_calendar_event_type` — a resource carrying one
            belongs to no type filter and disappears from the filtered view.
    """
    if isinstance(value, ResourceType):
        return value
    try:
        return ResourceType(value)
    except ValueError:
        raise ValueError(f"Unknown resource type: {value!r}") from None


# ---------------------------------------------------------------------------
# Phase 7 — the intelligence vocabulary
# ---------------------------------------------------------------------------


class RiskType(StrEnum):
    """What kind of condition a risk describes.

    A closed set on purpose. The brief lists these seven and the risk engine
    ships exactly these detectors, so a risk row can only ever name a condition
    some detector knows how to re-derive — which is what makes "the condition
    disappeared, so resolve the risk" a decidable question rather than a guess.
    An eighth type would have no detector behind it and so could never be
    evaluated or resolved.

    ``DEADLINE`` and ``TASK`` are distinct on purpose even though both point at
    a task: a deadline risk is about *time running out*, a task risk is about
    the task itself (blocked, repeatedly rescheduled, effort far exceeding its
    estimate). Splitting them means each carries its own formula and its own
    thresholds rather than one averaged number that explains nothing.
    """

    DEADLINE = "deadline"
    WORKLOAD = "workload"
    PROJECT = "project"
    TASK = "task"
    SCHEDULING = "scheduling"
    ESTIMATION = "estimation"
    CONSISTENCY = "consistency"


class RiskSeverity(StrEnum):
    """How loudly a risk speaks.

    Derived from the score, never set by hand. :func:`risk_severity_for` in
    ``app.services.risk.scoring`` is the only thing that decides which band a
    score falls in, so a score and its severity cannot disagree.

    Ordered most-severe first. That is what lets a UI sort by severity with a
    plain string comparison and get the right answer, and it is why the
    persisted strings are words and not numbers — the ordering would be invisible
    to a reader of a row six months later.
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RiskStatus(StrEnum):
    """Where a risk sits in its lifecycle.

    ``ACTIVE`` and ``ACKNOWLEDGED`` are the two states a risk can be
    *re-detected into* without creating a second row — that pair is what the
    partial unique index on ``(user_id, risk_type, entity_type, entity_id)``
    keys on, and it is the mechanism behind the brief's "avoid creating
    duplicate risk records every time the detection engine runs".

    ``ACKNOWLEDGED`` is not ``RESOLVED``: acknowledging says "I have seen this
    and I accept it is still true", which is the state a risk the user intends
    to live with should rest in so it stops competing for attention.
    """

    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class RecommendationType(StrEnum):
    """What kind of action a recommendation proposes.

    Every member names an action a *person* takes. None of them is an
    operation NEXUS performs: the brief is explicit that the engine must not
    reschedule anything without confirmation, and encoding that as a type list
    containing no "do it now" member makes the constraint structural rather
    than a rule somebody has to remember at each call site.

    The two Phase 9 members are the same kind of thing as the Phase 7 eight.
    Both name something the *user* does with their own learning record — book
    sessions against a goal they set, or record a practice activity — and
    neither says anything about the person: ``REVIEW_LEARNING_GOAL`` is about a
    deadline and a percentage the user themselves entered, and
    ``REVIVE_TARGET_SKILL`` is about days since the last activity NEXUS was
    told about. A level is the user's or visibly derived, and neither member
    carries a claim about ability into the vocabulary that a rule might later
    hang one on.
    """

    RESCHEDULE_TASK = "reschedule_task"
    BREAK_DOWN_TASK = "break_down_task"
    REDUCE_WORKLOAD = "reduce_workload"
    START_TASK = "start_task"
    PRIORITIZE_TASK = "prioritize_task"
    REVIEW_DEADLINE = "review_deadline"
    UPDATE_ESTIMATE = "update_estimate"
    BLOCK_TIME = "block_time"
    COMPLETE_BLOCKED_TASK = "complete_blocked_task"
    REVIEW_PROJECT = "review_project"
    REVIEW_LEARNING_GOAL = "review_learning_goal"
    REVIVE_TARGET_SKILL = "revive_target_skill"


class RecommendationStatus(StrEnum):
    """What has happened to a recommendation since it was raised.

    The terminal states (``ACCEPTED``, ``REJECTED``, ``COMPLETED``, ``EXPIRED``)
    are the training signal Phase 10 will want, and they are why every
    transition is written to ``activity_events`` as well as to this column: the
    column says what it is now, the event says when and in what order.

    ``EXPIRED`` is set by the service when the risk that produced the
    recommendation is resolved, rather than by a clock. A recommendation is not
    merely old, it is moot — which is a different and more useful distinction.
    """

    NEW = "new"
    VIEWED = "viewed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    COMPLETED = "completed"
    EXPIRED = "expired"


class RecommendationPriority(StrEnum):
    """How soon a recommendation wants an answer.

    Same ordering reasoning as :class:`RiskSeverity`, and derived the same way —
    from the score of the risk that raised it — so priority and severity are two
    views of one number rather than two opinions.
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EvidenceStrength(StrEnum):
    """How much data a risk or recommendation was derived from.

    **This is deliberately not called confidence.** The brief forbids presenting
    a deterministic rule as though it were a model's posterior, and the name is
    where that misrepresentation would start. It says only what is true and
    checkable: how many observations the rule had to work with.

    This is the cold-start mechanism. A user with two completed tasks can have an
    estimation risk, but it is reported at ``LOW`` evidence strength, which the
    UI shows, so a thin sample never masquerades as a firm conclusion.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


def validate_risk_type(value: RiskType | str) -> RiskType:
    """Coerce a value into a :class:`RiskType`.

    Raises:
        ValueError: If the value is not a known type. ``risk_type`` is a third
            of the risks table's deduplication index, so an unrecognised value
            does not merely render badly — it defeats the constraint that stops
            the same risk being inserted twice.
    """
    if isinstance(value, RiskType):
        return value
    try:
        return RiskType(value)
    except ValueError:
        raise ValueError(f"Unknown risk type: {value!r}") from None


def validate_risk_status(value: RiskStatus | str) -> RiskStatus:
    """Coerce a value into a :class:`RiskStatus`.

    Raises:
        ValueError: If not a known status. The lifecycle transitions read this
            column, so a value none of them recognise would strand the row in a
            state no detector can ever resolve or re-detect.
    """
    if isinstance(value, RiskStatus):
        return value
    try:
        return RiskStatus(value)
    except ValueError:
        raise ValueError(f"Unknown risk status: {value!r}") from None


def validate_recommendation_type(value: RecommendationType | str) -> RecommendationType:
    """Coerce a value into a :class:`RecommendationType`.

    Raises:
        ValueError: If not a known type, for the same reason as
            :func:`validate_risk_type`.
    """
    if isinstance(value, RecommendationType):
        return value
    try:
        return RecommendationType(value)
    except ValueError:
        raise ValueError(f"Unknown recommendation type: {value!r}") from None


def validate_recommendation_status(value: RecommendationStatus | str) -> RecommendationStatus:
    """Coerce a value into a :class:`RecommendationStatus`.

    Raises:
        ValueError: If not a known status. ``status`` is what the feedback
            queries filter on, so an unrecognised value would be invisible to
            every one of them — the row would exist and nothing would ever find
            it again.
    """
    if isinstance(value, RecommendationStatus):
        return value
    try:
        return RecommendationStatus(value)
    except ValueError:
        raise ValueError(f"Unknown recommendation status: {value!r}") from None


# ---------------------------------------------------------------------------
# Phase 8 — the developer-intelligence vocabulary
# ---------------------------------------------------------------------------


class GitScanStatus(StrEnum):
    """The outcome of one attempt to read a repository from disk.

    ``PENDING`` is not a member: a scan is a synchronous request, so a row is only
    ever written once the attempt has already finished.

    Two members, because two is what the scan boundary needs. Every scan is
    wrapped, so a repository git cannot read — deleted mid-request, corrupt, not
    a work tree, past the timeout — comes back as an ``ERROR`` row carrying a
    human sentence instead of an exception that takes the page down. A third
    "in flight" member would be a state the server never spends time in and would
    leave the health of a repository unanswerable for the duration of every scan.

    Persisted on ``git_scan_runs.status`` and mirrored onto
    ``git_repositories.last_scan_status``.
    """

    OK = "ok"
    ERROR = "error"


def validate_git_scan_status(value: GitScanStatus | str) -> GitScanStatus:
    """Coerce a stored or user-supplied value into a :class:`GitScanStatus`.

    Raises:
        ValueError: If not a known status. ``git_scan_runs.status`` is what the
            "when did this repository last stop being readable" query filters
            on, and ``git_repositories.last_scan_status`` is what decides whether
            a registration is presented as healthy or as failed. A value neither
            recognises leaves a scan row that reports neither outcome — the
            repository looks scanned, and nothing can say whether it succeeded.
    """
    if isinstance(value, GitScanStatus):
        return value
    try:
        return GitScanStatus(value)
    except ValueError:
        raise ValueError(f"Unknown git scan status: {value!r}") from None


# ---------------------------------------------------------------------------
# Phase 9 — the learning and career vocabulary
# ---------------------------------------------------------------------------


class LearningGoalStatus(StrEnum):
    """Where a learning goal sits in its own life.

    ``ARCHIVED`` is separate from ``COMPLETED`` because a finished goal the user
    still wants as a record and a goal they have dismissed are different facts,
    and an archived goal must not count as incomplete work anywhere.

    ``PAUSED`` is a member rather than a comment on ``NOT_STARTED`` for the same
    reason the planner has a separate "on hold" state: a goal the user intends
    to return to in March is not a goal they have abandoned, and merging the two
    would make every "still open" count lie about one of them.
    """

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class SkillLevelSource(StrEnum):
    """Who is allowed to claim a number for a skill level.

    This is the single most important honesty control in Phase 9. A level the
    user typed is a claim they are making and NEXUS merely records; a level
    NEXUS derived is an inference it must be able to show its working for.

    There is deliberately no "unknown" member and no null: a skill with no level
    is not a skill with an unknown level, and a row that could hold either would
    let an inference be presented as a self-assessment by accident. The source is
    written with the number, every time, so the UI never has to guess which of
    the two it is about to render.

    Persisted on ``skills.level_source``.
    """

    USER_DEFINED = "user_defined"
    SYSTEM_ESTIMATE = "system_estimate"


class LearningActivityType(StrEnum):
    """What kind of event counts as evidence that something was learned.

    Each member is a fact NEXUS can point at a record for. None of them implies
    understanding — ``RESOURCE_VIEWED`` in particular records that a page was
    opened, which is the weakest of these and is weighted as such.

    ``CODING_ACTIVITY`` exists so that Phase 8's repository facts can be
    referenced as evidence without being re-described as learning: the row says
    *this activity happened and here is the thing it came from*, and the
    ``source_type``/``source_id`` pair says what that thing was. NEXUS never
    converts a commit into a claim that a task was completed.

    Persisted on ``learning_activities.activity_type``.
    """

    STUDY_SESSION = "study_session"
    TASK_COMPLETED = "task_completed"
    NOTE_CREATED = "note_created"
    RESOURCE_VIEWED = "resource_viewed"
    CONCEPT_LEARNED = "concept_learned"
    PROJECT_COMPLETED = "project_completed"
    CODING_ACTIVITY = "coding_activity"


class CareerEvidenceType(StrEnum):
    """A thing worth putting in front of someone who is deciding about you.

    ``CERTIFICATION`` and ``ACHIEVEMENT`` are user-entered rows and nothing
    else. NEXUS never creates a certification, never dates one, and never
    infers one from activity — the absence of a generator is what makes the
    rest of this table trustworthy, so these two members carry no machinery
    behind them at all.

    The remaining five are types a *derived* row may have, and each names the
    subsystem it would have come from, so ``career_evidence.source`` and
    ``career_evidence.evidence_type`` can be checked against each other rather
    than trusted to agree.

    Persisted on ``career_evidence.evidence_type``.
    """

    PROJECT_COMPLETED = "project_completed"
    FEATURE_SHIPPED = "feature_shipped"
    REPOSITORY_ACTIVITY = "repository_activity"
    SKILL_ACTIVITY = "skill_activity"
    LEARNING_MILESTONE = "learning_milestone"
    CERTIFICATION = "certification"
    ACHIEVEMENT = "achievement"


class CareerRecordKind(StrEnum):
    """A line on the career profile that is a record rather than an achievement.

    Education, work experience and certifications are the dated history a
    profile is made of, and they are three different *kinds* of claim — one was
    studied, one was worked, one was passed. Collapsing them into an undifferentiated
    "entry" list would lose exactly the part a reader is scanning for.

    There is no ``OTHER`` here, unlike :class:`CalendarEventType` or
    :class:`ResourceType`: those hold a *characterisation* the user applies to
    their own life and "other" is a useful answer. This column picks which of
    three record shapes is being written, so a fourth kind would need a fourth
    shape to go with it.

    Persisted on ``career_experience.kind``.
    """

    EDUCATION = "education"
    EXPERIENCE = "experience"
    CERTIFICATION = "certification"


def validate_learning_goal_status(value: LearningGoalStatus | str) -> LearningGoalStatus:
    """Coerce a stored or user-supplied value into a :class:`LearningGoalStatus`.

    Raises:
        ValueError: If the value is not a known status. ``status`` is what the
            open-goals, overdue-goals and completion-rate queries all filter on,
            so an unrecognised value leaves a goal that counts as neither
            finished nor outstanding — the one place where being wrong cannot be
            noticed from the numbers, because the total still adds up.
    """
    if isinstance(value, LearningGoalStatus):
        return value
    try:
        return LearningGoalStatus(value)
    except ValueError:
        raise ValueError(f"Unknown learning goal status: {value!r}") from None


def validate_skill_level_source(value: SkillLevelSource | str) -> SkillLevelSource:
    """Coerce a stored or user-supplied value into a :class:`SkillLevelSource`.

    Raises:
        ValueError: If not a known source. This is the Phase 9 honesty control
            and it is the one place where a bad value is not merely
            unrenderable: a row whose source nothing recognises would have to be
            rendered by the UI, and the only safe rendering is the cautious one.
            Failing the write instead means a skill level can never reach a
            screen without a stated provenance.
    """
    if isinstance(value, SkillLevelSource):
        return value
    try:
        return SkillLevelSource(value)
    except ValueError:
        raise ValueError(f"Unknown skill level source: {value!r}") from None


def validate_learning_activity_type(
    value: LearningActivityType | str,
) -> LearningActivityType:
    """Coerce a stored or user-supplied value into a :class:`LearningActivityType`.

    Raises:
        ValueError: If not a known type. ``activity_type`` is what separates a
            weighted study session from an unweighted page view, so an
            unrecognised value would silently land in whichever bucket the
            query forgot to exclude — and the evidence count behind a skill
            level would then overstate itself.
    """
    if isinstance(value, LearningActivityType):
        return value
    try:
        return LearningActivityType(value)
    except ValueError:
        raise ValueError(f"Unknown learning activity type: {value!r}") from None


def validate_career_evidence_type(value: CareerEvidenceType | str) -> CareerEvidenceType:
    """Coerce a stored or user-supplied value into a :class:`CareerEvidenceType`.

    Raises:
        ValueError: If not a known type. ``evidence_type`` is one of the six
            columns of ``uq_career_evidence_source_identity``, so a value nothing
            recognises does not merely fail to render — it defeats the
            constraint that is the entire deduplication mechanism for derived
            career evidence.
    """
    if isinstance(value, CareerEvidenceType):
        return value
    try:
        return CareerEvidenceType(value)
    except ValueError:
        raise ValueError(f"Unknown career evidence type: {value!r}") from None


def validate_career_record_kind(value: CareerRecordKind | str) -> CareerRecordKind:
    """Coerce a stored or user-supplied value into a :class:`CareerRecordKind`.

    Raises:
        ValueError: If not a known kind. The three members are the three record
            shapes the profile renders, and an unrecognised one is a row that
            appears in no section of the timeline it was filed under.
    """
    if isinstance(value, CareerRecordKind):
        return value
    try:
        return CareerRecordKind(value)
    except ValueError:
        raise ValueError(f"Unknown career record kind: {value!r}") from None
