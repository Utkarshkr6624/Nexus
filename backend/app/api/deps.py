"""API-layer dependencies.

Only wiring that is specific to the HTTP layer lives here: turning the
request-scoped session into the services the routers need, and layering the
*token is still good* checks on top of the canonical identity resolution —
that the token was not denylisted, and that the session row it names is still
live. Everything about *who is calling* — bearer scheme, current user, superuser
check — is owned by ``app.core.deps`` and is re-exported so routers have a
single import site.

The wiring rule here is that a service is built from *real* collaborators. A
provider that passes ``None`` for something the service will actually use does
not produce a simpler object graph, it produces a service that silently degrades
— ``AuthService`` without a session store issues tokens with no row behind them
and cannot revoke a single device, and does so without telling anyone. The
degraded paths exist for the token rules to be exercisable on their own; the API
layer is not that caller. That rule is why :func:`get_authenticated_user` can
consult the ``sessions`` table: the row is there to be consulted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request

from app.core.config import Settings, get_settings
from app.core.deps import (
    Credentials,
    CurrentSessionId,
    CurrentUser,
    DbSession,
    SuperUser,
    UserRepositoryDep,
    bearer_scheme,
    get_current_session_id,
    get_current_user,
    get_optional_user,
    get_user_repository,
)
from app.core.exceptions import UnauthorizedError
from app.core.security import TokenType, decode_token
from app.ml.runtime import MLRuntime
from app.ml.runtime import get_ml_runtime as get_process_ml_runtime
from app.models.user import User
from app.repositories.activity import ActivityRepository
from app.repositories.analytics import AnalyticsRepository
from app.repositories.audit import AuditRepository
from app.repositories.career import CareerRepository
from app.repositories.developer import DeveloperRepository
from app.repositories.knowledge import (
    BookmarkRepository,
    CategoryRepository,
    ConceptRepository,
    DocumentRepository,
    KnowledgeLinkRepository,
    NoteRepository,
    ResourceRepository,
)
from app.repositories.learning import LearningRepository
from app.repositories.password_reset import PasswordResetRepository
from app.repositories.planner import (
    AvailabilityRuleRepository,
    CalendarEventRepository,
    WorkSessionRepository,
)
from app.repositories.project import ProjectRepository
from app.repositories.risk import RiskRepository
from app.repositories.session import SessionRepository
from app.repositories.tag import TagRepository
from app.repositories.task import TaskRepository
from app.repositories.user import UserRepository
from app.services.activity_service import ActivityService
from app.services.analytics import AnalyticsService
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.career import CareerIntelligenceService
from app.services.developer import DeveloperIntelligenceService
from app.services.knowledge_service import KnowledgeService
from app.services.learning import LearningIntelligenceService
from app.services.planner_service import PlannerService
from app.services.project_service import ProjectService
from app.services.risk.detection import RiskDetectionService
from app.services.risk.recommendation import RecommendationService
from app.services.scheduling_service import SchedulingService
from app.services.session_service import SessionService
from app.services.tag_service import TagService
from app.services.task_service import TaskService
from app.services.user_service import UserService

#: User-Agent values are stored in a 512-character column; the cap is applied
#: where the request is read so no caller has to remember it. Matches
#: ``sessions.user_agent`` and ``audit_logs.user_agent``.
_MAX_USER_AGENT_LENGTH = 512

#: One message for every way the ``sessions`` row behind a bearer can fail to
#: authorise it. "Revoked", "expired" and "no such row" must be indistinguishable
#: to the caller, or this check becomes an oracle for which session ids are real
#: — the same rule :func:`app.api.v1.auth.revoke_session` applies in the other
#: direction.
_SESSION_NOT_LIVE = "This session is no longer valid."


def get_session_repository(session: DbSession) -> SessionRepository:
    """Provide a request-scoped session repository."""
    return SessionRepository(session)


def get_audit_repository(session: DbSession) -> AuditRepository:
    """Provide a request-scoped audit repository."""
    return AuditRepository(session)


def get_password_reset_repository(session: DbSession) -> PasswordResetRepository:
    """Provide a request-scoped password-reset repository."""
    return PasswordResetRepository(session)


def get_project_repository(session: DbSession) -> ProjectRepository:
    """Provide a request-scoped project repository."""
    return ProjectRepository(session)


def get_task_repository(session: DbSession) -> TaskRepository:
    """Provide a request-scoped task repository."""
    return TaskRepository(session)


def get_tag_repository(session: DbSession) -> TagRepository:
    """Provide a request-scoped tag repository."""
    return TagRepository(session)


def get_activity_repository(session: DbSession) -> ActivityRepository:
    """Provide a request-scoped activity repository."""
    return ActivityRepository(session)


def get_analytics_repository(session: DbSession) -> AnalyticsRepository:
    """Provide a request-scoped daily-aggregate repository.

    The one Phase 6 table, and the only reason a separate provider exists rather
    than the service taking a session: the upsert's conflict target is the
    ``UNIQUE (user_id, metric_date)`` constraint declared by the model, so the
    storage detail belongs in the storage layer.
    """
    return AnalyticsRepository(session)


def get_calendar_event_repository(session: DbSession) -> CalendarEventRepository:
    """Provide a request-scoped calendar-event repository."""
    return CalendarEventRepository(session)


def get_work_session_repository(session: DbSession) -> WorkSessionRepository:
    """Provide a request-scoped work-session repository."""
    return WorkSessionRepository(session)


def get_availability_rule_repository(session: DbSession) -> AvailabilityRuleRepository:
    """Provide a request-scoped availability-rule repository."""
    return AvailabilityRuleRepository(session)


def get_risk_repository(session: DbSession) -> RiskRepository:
    """Provide a request-scoped risk repository.

    A provider of its own rather than being reachable only through
    :func:`get_risk_service`, because the Risk Center's **reads** are not the
    detection pass: listing risks, reading one, and moving one along its
    lifecycle are all bounded queries the repository answers, and burying them
    inside the detection service would make a request that only wants to show a
    list of risks construct the whole analytics graph behind it first.
    """
    return RiskRepository(session)


def get_developer_repository(session: DbSession) -> DeveloperRepository:
    """Provide a request-scoped Phase 8 repository.

    **A provider function, not ``Depends(DeveloperRepository)``.** The Phase 8
    tables — ``git_repositories``, ``git_commits``, ``git_branches``,
    ``git_scan_runs`` — are one storage concern and one set of owner-scoped
    predicates, so they are one repository rather than four. Its constructor takes
    an ``AsyncSession``, which is not a Pydantic field type, so handing FastAPI
    the class itself raises ``FastAPIError: Invalid args for response field`` at
    import time; every ``Dep`` alias in this file therefore names a function that
    takes :data:`DbSession` and returns the collaborator, and this one follows the
    rule like the rest.

    It has a provider of its own rather than being reachable only through
    :func:`get_developer_service` for the reason :func:`get_risk_repository`
    states: a request that only wants a page of registered repositories, or one
    repository's branches, must not construct the service graph behind it first.
    """
    return DeveloperRepository(session)


def get_learning_repository(session: DbSession) -> LearningRepository:
    """Provide a request-scoped Phase 9 learning repository.

    ``learning_goals``, ``skills`` and ``learning_activities`` are one storage
    concern and one set of owner-scoped predicates, so they are one repository
    rather than three — the same reasoning :func:`get_developer_repository` gives
    for the four Phase 8 tables.

    **A provider function, not ``Depends(LearningRepository)``**, for the reason
    that docstring states in full: the constructor takes an ``AsyncSession``,
    which is not a Pydantic field type, so handing FastAPI the class itself fails
    at import time.

    It has a provider of its own rather than being reachable only through
    :func:`get_learning_service` because the career service reads two things from
    it — the recorded-activity count and the ownership check on a skill an
    evidence row points at — without wanting the learning service's write path.
    """
    return LearningRepository(session)


def get_career_repository(session: DbSession) -> CareerRepository:
    """Provide a request-scoped Phase 9 career repository.

    ``career_profiles``, ``career_experience`` and ``career_evidence`` are one
    storage concern; the profile upsert in particular is keyed on a constraint
    that belongs in the storage layer rather than in the router that calls it.

    A provider function rather than ``Depends(CareerRepository)`` — same reason
    as every other ``Dep`` alias in this file, and the same fix: take
    :data:`DbSession` and return the collaborator.
    """
    return CareerRepository(session)


def get_note_repository(session: DbSession) -> NoteRepository:
    """Provide a request-scoped note repository."""
    return NoteRepository(session)


def get_concept_repository(session: DbSession) -> ConceptRepository:
    """Provide a request-scoped concept repository."""
    return ConceptRepository(session)


def get_resource_repository(session: DbSession) -> ResourceRepository:
    """Provide a request-scoped resource repository."""
    return ResourceRepository(session)


def get_bookmark_repository(session: DbSession) -> BookmarkRepository:
    """Provide a request-scoped bookmark repository."""
    return BookmarkRepository(session)


def get_document_repository(session: DbSession) -> DocumentRepository:
    """Provide a request-scoped document repository."""
    return DocumentRepository(session)


def get_category_repository(session: DbSession) -> CategoryRepository:
    """Provide a request-scoped category repository."""
    return CategoryRepository(session)


def get_knowledge_link_repository(session: DbSession) -> KnowledgeLinkRepository:
    """Provide a request-scoped knowledge-link repository."""
    return KnowledgeLinkRepository(session)


SessionRepositoryDep = Annotated[SessionRepository, Depends(get_session_repository)]
AuditRepositoryDep = Annotated[AuditRepository, Depends(get_audit_repository)]
PasswordResetRepositoryDep = Annotated[
    PasswordResetRepository, Depends(get_password_reset_repository)
]
ActivityRepositoryDep = Annotated[ActivityRepository, Depends(get_activity_repository)]
AnalyticsRepositoryDep = Annotated[AnalyticsRepository, Depends(get_analytics_repository)]
CalendarEventRepositoryDep = Annotated[
    CalendarEventRepository, Depends(get_calendar_event_repository)
]
WorkSessionRepositoryDep = Annotated[WorkSessionRepository, Depends(get_work_session_repository)]
AvailabilityRuleRepositoryDep = Annotated[
    AvailabilityRuleRepository, Depends(get_availability_rule_repository)
]
NoteRepositoryDep = Annotated[NoteRepository, Depends(get_note_repository)]
ConceptRepositoryDep = Annotated[ConceptRepository, Depends(get_concept_repository)]
ResourceRepositoryDep = Annotated[ResourceRepository, Depends(get_resource_repository)]
BookmarkRepositoryDep = Annotated[BookmarkRepository, Depends(get_bookmark_repository)]
DocumentRepositoryDep = Annotated[DocumentRepository, Depends(get_document_repository)]
CategoryRepositoryDep = Annotated[CategoryRepository, Depends(get_category_repository)]
KnowledgeLinkRepositoryDep = Annotated[
    KnowledgeLinkRepository, Depends(get_knowledge_link_repository)
]
SettingsDep = Annotated[Settings, Depends(get_settings)]
RiskRepositoryDep = Annotated[RiskRepository, Depends(get_risk_repository)]
DeveloperRepositoryDep = Annotated[DeveloperRepository, Depends(get_developer_repository)]
LearningRepositoryDep = Annotated[LearningRepository, Depends(get_learning_repository)]
CareerRepositoryDep = Annotated[CareerRepository, Depends(get_career_repository)]


def get_session_service(
    repository: SessionRepositoryDep,
    settings: SettingsDep,
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> SessionService:
    """Provide a request-scoped session service.

    Settings are injected rather than left to the service's own
    ``get_settings()`` fallback so the lifetime and cap values a request runs
    under are the ones resolved for that request. The audit sink is threaded
    through so revoking a session leaves a trail.
    """
    return SessionService(repository, settings, audit)


def get_audit_service(repository: AuditRepositoryDep) -> AuditService:
    """Provide a request-scoped audit service."""
    return AuditService(repository)


def get_activity_service(repository: ActivityRepositoryDep) -> ActivityService:
    """Provide a request-scoped activity service.

    The single sink both work-management services write through. It is wired
    here rather than left optional on purpose: see this module's docstring for
    why a service built from a degraded collaborator is not a simpler object
    graph. ``ProjectService`` and ``TaskService`` both document ``activity=None``
    as "the lifecycle rules without a history sink", which is a real mode for
    exercising the rules in isolation — but it is not the API layer, and shipping
    it would mean every project created through the API leaves no trace.
    """
    return ActivityService(repository)


def get_auth_service(
    repository: UserRepositoryDep,
    sessions: Annotated[SessionService, Depends(get_session_service)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> AuthService:
    """Provide a request-scoped auth service wired to sessions and the trail."""
    return AuthService(repository, sessions, audit)


def get_user_service(
    repository: UserRepositoryDep,
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> UserService:
    """Provide a request-scoped user service.

    The audit sink is passed through so account changes are recorded from the
    service rather than from the router that happened to call it.
    """
    return UserService(repository, audit=audit)


SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]
AuditServiceDep = Annotated[AuditService, Depends(get_audit_service)]
ActivityServiceDep = Annotated[ActivityService, Depends(get_activity_service)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
UserServiceDep = Annotated[UserService, Depends(get_user_service)]
ProjectRepositoryDep = Annotated[ProjectRepository, Depends(get_project_repository)]
TaskRepositoryDep = Annotated[TaskRepository, Depends(get_task_repository)]
TagRepositoryDep = Annotated[TagRepository, Depends(get_tag_repository)]


def get_project_service(
    repository: ProjectRepositoryDep,
    tasks: TaskRepositoryDep,
    activity: ActivityServiceDep,
    audit: AuditServiceDep,
    settings: SettingsDep,
) -> ProjectService:
    """Provide a request-scoped project service.

    **The task repository is not optional here.** :class:`ProjectService` takes it
    keyword-only because only :meth:`~app.services.project_service.ProjectService.summary`
    needs it, and it raises ``ValueError`` rather than inventing zero counts when
    it is missing — a wiring mistake should name itself, and
    ``GET /projects/{id}/summary`` is the route that would hit it. The API layer
    is not that caller: it serves the summary, so it wires the collaborator.

    ``activity`` writes ``activity_events`` beside every mutation, and ``audit`` is
    passed for symmetry with the auth services and used by neither — a project
    completing is a fact about the work, not a fact about the account, and
    :mod:`app.services.project_service` states the rule at length.

    Settings are injected rather than left to the service's own ``get_settings()``
    fallback, so the values a request runs under are the ones resolved for it.
    """
    return ProjectService(
        repository,
        activity=activity,
        audit=audit,
        settings=settings,
        task_repository=tasks,
    )


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]


def get_task_service(
    repository: TaskRepositoryDep,
    projects: ProjectRepositoryDep,
    tags: TagRepositoryDep,
    activity: ActivityServiceDep,
    audit: AuditServiceDep,
    settings: SettingsDep,
) -> TaskService:
    """Provide a request-scoped task service.

    Three repositories, not one, because the task table cannot answer two of the
    questions its own service asks. A task's ``owner_id`` is denormalised from
    its project, so nothing in the schema stops a row being filed under another
    account's project and :meth:`app.services.task_service.TaskService.create`
    has to re-check that destination through the *scoped* project lookup — a
    check the task repository has no way to perform. Tags are per-user rows, so
    every tag the service acts on is resolved through the scoped tag lookup as
    well. Wiring the dependencies here rather than in the router is what keeps
    ``app/api/v1/tasks.py`` free of repository imports.

    ``activity`` is the feed every mutation writes beside itself — a task
    completing, a board being re-triaged, a deadline slipping. It is wired rather
    than left as the service's documented ``None`` mode because the API layer is
    not a caller exercising the rules in isolation: a card whose history nobody
    can read is a card whose history does not exist.
    """
    return TaskService(
        repository,
        projects,
        tags,
        activity=activity,
        audit=audit,
        settings=settings,
    )


TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]


def get_tag_service(
    repository: TagRepositoryDep,
    activity: ActivityServiceDep,
    audit: AuditServiceDep,
    settings: SettingsDep,
) -> TagService:
    """Provide a request-scoped tag service.

    ``activity`` is wired rather than left as the service's documented ``None``
    mode for the reason this module's docstring gives in full: a service built
    from a degraded collaborator is not a simpler object graph, it is one that
    silently does less. ``TagService`` records the two tag-application writes
    (``TASK_UPDATED`` on the task, ``PROJECT_UPDATED`` on the project) and
    records nothing for a tag's own create/rename/delete, because
    :class:`~app.models.enums.ActivityEvent` has no member for those — the gap is
    documented at length in :mod:`app.services.tag_service` rather than papered
    over with a borrowed event name.

    ``audit`` is passed through and used by neither, for the same reason
    ``ProjectService`` does not use it: coining a label is a fact about the work,
    not a fact about the account.

    Settings are injected rather than left to the service's own ``get_settings()``
    fallback, so the values a request runs under are the ones resolved for it.
    """
    return TagService(
        repository,
        activity=activity,
        audit=audit,
        settings=settings,
    )


TagServiceDep = Annotated[TagService, Depends(get_tag_service)]


def get_planner_service(
    events: CalendarEventRepositoryDep,
    sessions: WorkSessionRepositoryDep,
    availability: AvailabilityRuleRepositoryDep,
    projects: ProjectRepositoryDep,
    tasks: TaskRepositoryDep,
    activity: ActivityServiceDep,
    audit: AuditServiceDep,
    settings: SettingsDep,
) -> PlannerService:
    """Provide a request-scoped planner service.

    **Three planner repositories, not one, because the three tables answer three
    different questions.** An event is a point on a calendar, a work session is a
    block of time against a task, and an availability rule is a recurring weekly
    wall-clock window with no instant of its own. There is no join that would
    collapse them, and one repository taking all three would be a repository
    whose name had to list them.

    ``projects`` and ``tasks`` are needed because an event or session may point
    at a project or a task, and every such reference is re-checked through the
    *scoped* lookup before it is written. The task repository additionally
    supplies the scheduling engine's candidate backlog.

    ``activity`` is wired rather than left as the service's documented ``None``
    mode for the reason this module's docstring gives in full: a service built
    from a degraded collaborator is not a simpler object graph, it is one that
    silently does less. Booking a meeting, starting a timer and accepting a
    planner suggestion are all facts about the work, and the activity feed is
    where a user reads them back.

    ``audit`` is accepted for symmetry with the other services and used by
    neither, on ``ProjectService``'s reasoning: booking a meeting is a fact about
    the work, not a fact about the account, and ``audit_logs`` is the security
    trail.
    """
    return PlannerService(
        events,
        sessions,
        availability,
        projects,
        tasks,
        activity=activity,
        audit=audit,
        settings=settings,
    )


PlannerServiceDep = Annotated[PlannerService, Depends(get_planner_service)]


def get_scheduling_service(
    planner: PlannerServiceDep,
    tasks: TaskRepositoryDep,
    activity: ActivityServiceDep,
    audit: AuditServiceDep,
    settings: SettingsDep,
) -> SchedulingService:
    """Provide a request-scoped scheduling service.

    **It takes the whole :class:`PlannerService` rather than the four
    repositories underneath it.** The engine creates work sessions through
    ``PlannerService.create_session``, not through the session repository
    directly, and that is the point: a session accepted from a suggestion and one
    created by hand then pass through exactly one set of rules — the window check,
    the scoped lookups for ``task_id``/``project_id``, the status default — so
    the engine cannot produce a row the API would have refused, and a change to
    those rules cannot leave one of the two doors behind.

    ``tasks`` supplies the candidate backlog: the open tasks that have both an
    estimate and a due date, which is what makes a slot placeable rather than
    arbitrary.

    ``activity`` records ``PLANNER_SUGGESTION_ACCEPTED`` and
    ``PLANNER_SUGGESTION_REJECTED``. It is wired rather than left as the
    service's documented ``None`` mode because a schedule a user acted on and a
    schedule they refused are exactly the two facts a later "why did you book
    that?" question needs, and a rejected suggestion nobody recorded is
    indistinguishable from one that was never made.

    ``audit`` is accepted for symmetry and used by neither, on the reasoning the
    other services give: accepting a slot is a fact about the work, not about the
    account.
    """
    return SchedulingService(
        planner,
        tasks,
        activity=activity,
        audit=audit,
        settings=settings,
    )


SchedulingServiceDep = Annotated[SchedulingService, Depends(get_scheduling_service)]


def get_knowledge_service(
    notes: NoteRepositoryDep,
    concepts: ConceptRepositoryDep,
    resources: ResourceRepositoryDep,
    bookmarks: BookmarkRepositoryDep,
    documents: DocumentRepositoryDep,
    categories: CategoryRepositoryDep,
    links: KnowledgeLinkRepositoryDep,
    activity: ActivityServiceDep,
    audit: AuditServiceDep,
    settings: SettingsDep,
) -> KnowledgeService:
    """Provide a request-scoped knowledge service.

    **Seven repositories, not one, and the reason is the schema rather than
    taste.** Notes, concepts, resources, bookmarks, documents, categories and
    links are seven tables with seven different uniqueness rules — a duplicate
    edge is a conflict on a five-column key, a duplicate bookmark is a conflict
    on ``(owner_id, url)``, a category is a self-referencing tree — and there is
    no join that would collapse them. One repository taking all seven would be a
    repository whose name had to list them.

    ``links`` is the one that cannot be folded into the others. ``knowledge_links``
    is **polymorphic**: ``source_type`` chooses which table ``source_id`` points
    at, so neither endpoint column carries a foreign key. Only the link service
    can resolve an endpoint through the right owner-scoped query before writing,
    which is why the repositories above are all injected here rather than left for
    the link service to construct itself.

    ``activity`` records the Phase 5 events — a note published, an edge created —
    beside the write they describe. It is wired rather than left as the service's
    documented ``None`` mode for the reason this module's docstring gives in
    full: a service built from a degraded collaborator is not a simpler object
    graph, it is one that silently does less, and the Phase 6 analytics that read
    this feed are downstream of it being true.

    ``audit`` is accepted for symmetry with the other services and used by
    neither, on ``ProjectService``'s reasoning: writing a note is a fact about
    the work, not a fact about the account, and ``audit_logs`` is the security
    trail.

    Settings are injected rather than left to the service's own ``get_settings()``
    fallback, so the values a request runs under are the ones resolved for it.
    """
    return KnowledgeService(
        notes,
        concepts,
        resources,
        bookmarks,
        documents,
        categories,
        links,
        activity=activity,
        audit=audit,
        settings=settings,
    )


KnowledgeServiceDep = Annotated[KnowledgeService, Depends(get_knowledge_service)]


def get_analytics_service(
    metrics: AnalyticsRepositoryDep,
    tasks: TaskRepositoryDep,
    projects: ProjectRepositoryDep,
    sessions: WorkSessionRepositoryDep,
    events: CalendarEventRepositoryDep,
    notes: NoteRepositoryDep,
    availability: AvailabilityRuleRepositoryDep,
    activity: ActivityServiceDep,
    audit: AuditServiceDep,
    settings: SettingsDep,
) -> AnalyticsService:
    """Provide a request-scoped analytics service.

    **Six repositories, and the reason is that six tables answer six different
    questions.** ``daily_metrics`` is the aggregate tier, ``tasks`` and
    ``work_sessions`` are where time and estimation are actually recorded,
    ``calendar_events`` is reserved rather than spent time, ``projects`` and
    ``notes`` carry the per-project and knowledge rollups, and
    ``availability_rules`` is the schedule the workload figure is compared
    against. There is no join that would collapse them.

    ``activity`` and ``audit`` are accepted for wiring symmetry and used by
    neither, on the reasoning every other service in this file gives: a rebuild
    is a maintenance action over rows the user already owns, so it is neither a
    fact about the work nor a security event.

    Settings are injected rather than left to the service's own
    ``get_settings()`` fallback, so the weights and the range ceiling a request
    runs under are the ones resolved for that request.
    """
    return AnalyticsService(
        metrics,
        tasks,
        projects,
        sessions,
        events,
        notes,
        activity=activity,
        audit=audit,
        settings=settings,
        availability=availability,
    )


AnalyticsServiceDep = Annotated[AnalyticsService, Depends(get_analytics_service)]


def get_recommendation_service(
    risks: RiskRepositoryDep,
    tasks: TaskRepositoryDep,
    projects: ProjectRepositoryDep,
    activity: ActivityServiceDep,
    learning: LearningRepositoryDep,
    settings: SettingsDep,
) -> RecommendationService:
    """Provide a request-scoped recommendation service.

    **Three repositories, and the reason is what the rules have to look at
    before they can raise anything.** A suggestion is only worth raising if it
    names something the user can act on, so every rule reads the row it is about:
    ``tasks`` for the deadline, estimate and reschedule-history rules, and
    ``projects`` for the blocked-work and project-signal rules. ``risks`` is the
    write side, and it carries the deduplicating upsert — the thing that decides
    whether this suggestion is new or one the user has already been shown, which
    is what stops a rule re-raising the same advice on every evaluation.

    Reconstructing any of those from inside the service would make the rules
    depend on repositories the API layer never wires, and would leave the
    ownership predicate the repository exists to own duplicated in the service.

    ``activity`` is wired rather than left as the service's ``None`` mode, on the
    reasoning this module's docstring gives in full. A suggestion that was
    accepted, rejected, completed or opened is a fact about the user's work, and
    the accepted/rejected pair is the training label Phase 10 is described as
    wanting — an unrecorded answer is an answer nobody can learn from.
    """
    return RecommendationService(
        risks,
        tasks,
        projects,
        activity=activity,
        # Phase 9 adds the two learning rules, which read goals and skills rather
        # than risks, so ``learning`` is wired for the same reason ``tasks`` and
        # ``projects`` are: without it ``generate_learning`` is a silent no-op and
        # the rules exist only for the test suite. ``stale_inactive_days`` is a
        # setting rather than a constant because "dormant" is a judgement the
        # deployment may reasonably make differently, not a constant the rule
        # should be quietly hard-coding.
        learning=learning,
        stale_inactive_days=settings.career_stale_inactive_days,
    )


RecommendationServiceDep = Annotated[RecommendationService, Depends(get_recommendation_service)]


def get_risk_service(
    metrics: AnalyticsRepositoryDep,
    analytics: AnalyticsServiceDep,
    tasks: TaskRepositoryDep,
    projects: ProjectRepositoryDep,
    risks: RiskRepositoryDep,
    activity: ActivityServiceDep,
    recommendations: RecommendationServiceDep,
    settings: SettingsDep,
) -> RiskDetectionService:
    """Provide a request-scoped risk detection service.

    **``analytics`` is injected as a service, not as its repositories, and that is
    the load-bearing decision here.** Every figure the six detectors consume was
    already computed and published by Phase 6, and
    :class:`~app.services.analytics.service.AnalyticsService` already carries the
    ``available`` flag and the stated reason for the figures it cannot compute.
    Holding the repositories instead would let the pass re-derive anything it
    liked, and the two answers would eventually disagree by a rounding step — so
    the detector is given the only door onto those numbers.

    ``metrics`` is the one repository that *does* have to come in alongside it,
    and only for the two things Phase 6 computes but does not publish on a read:
    the raw ``(estimated, actual)`` pairs behind the estimation mean, and the
    flat session rows behind the planning signals. A published aggregate is
    never read around the service; an unpublished one has to be read somewhere.

    ``tasks`` and ``projects`` supply the per-row detail an aggregate cannot
    answer: an aggregate describes a group, and the deadline rule needs to name
    *which* task is exposed.

    ``risks`` carries the deduplicating upsert and the stale sweep. Both are
    storage invariants rather than detection rules — the partial unique index and
    the owner-scoped update — so they belong in the repository the service calls.

    ``activity`` records ``RISK_DETECTED``/``RISK_UPDATED``/``RISK_RESOLVED``
    beside every reconciliation step, wired rather than left optional for the
    reason the rest of this file gives.

    ``recommendations`` is passed so one pass ends with both halves of the same
    answer: risks that were written, and the actions raised on the back of them.
    Wired as the real generator rather than left ``None`` because a Risk Center
    whose risks have no suggestions is a risk engine that found the problem and
    then said nothing about what to do about it.

    Settings are injected rather than left to the service's own ``get_settings()``
    fallback, so the analytics range ceiling a window is checked against is the
    one resolved for this request.
    """
    return RiskDetectionService(
        metrics,
        analytics,
        tasks,
        projects,
        risks,
        activity=activity,
        recommendations=recommendations,
        settings=settings,
    )


RiskServiceDep = Annotated[RiskDetectionService, Depends(get_risk_service)]


def get_developer_service(
    repositories: DeveloperRepositoryDep,
    projects: ProjectRepositoryDep,
    activity: ActivityServiceDep,
    settings: SettingsDep,
) -> DeveloperIntelligenceService:
    """Provide a request-scoped Phase 8 developer-intelligence service.

    **Four collaborators, and the list is short because Phase 8 reads one
    account's own work rather than correlating it with anybody else's.**
    :mod:`app.services.risk.detection` needs seven, most of them so a rule that
    spans two tables has somewhere to read both; a scan reads a local directory
    and writes rows against its owner, so the whole graph is Phase 8 storage, the
    project table, the history sink and the settings.

    ``repositories`` is the whole of :mod:`app.repositories.developer` — one
    repository over ``git_repositories``, ``git_commits``, ``git_branches`` and
    ``git_scan_runs``. It is passed positionally because it is the service's first
    parameter, and it is the collaborator that carries every ``user_id`` predicate:
    the API layer names no owner and no id it was given by the client, and a row
    belonging to another account is never loaded in the first place, which is what
    makes the answer a 404 rather than a 403.

    ``projects`` is needed for two things only, both ownership checks the Phase 8
    tables cannot make: proving a ``project_id`` belongs to the caller before it
    is written onto a repository row, and reading the name
    ``GET /developer/projects/{project_id}`` returns. A project belonging to
    somebody else is *not found*, never a permission error.

    ``activity`` is the history sink, and it is wired rather than left as the
    service's documented ``None`` mode for the reason this module's docstring
    gives in full: a scan or a registration that records nothing is a fact about
    the account that nobody can later read back, and the feed is also the trail a
    later phase learns from. ``None`` remains a real mode for exercising the
    service against a hand-built snapshot — it is not the API layer.

    Settings are injected rather than left to the service's own ``get_settings()``
    fallback, so the repository cap, the scan's commit ceiling, the path allowlist
    and the window bounds a request runs under are the ones resolved for that
    request rather than whatever a module-level singleton happened to read first.
    """
    return DeveloperIntelligenceService(
        repositories,
        projects,
        activity=activity,
        settings=settings,
    )


DeveloperIntelligenceServiceDep = Annotated[
    DeveloperIntelligenceService, Depends(get_developer_service)
]


def get_learning_service(
    repositories: LearningRepositoryDep,
    projects: ProjectRepositoryDep,
    notes: NoteRepositoryDep,
    activity: ActivityServiceDep,
    settings: SettingsDep,
) -> LearningIntelligenceService:
    """Provide a request-scoped Phase 9 learning-intelligence service.

    **Four collaborators, and the list is short because Phase 9 reads one
    account's own record of what they meant to learn.** The goals, skills and
    activities are one repository, and the two other repositories exist for one
    job each: ``projects`` and ``notes`` prove a pointer a client supplied belongs
    to the caller *before* it is written onto a goal. Both refusals are not-founds
    rather than permission errors, because a 403 would confirm the id exists and
    turn the learning page into a directory of other people's notes.

    **The note repository is required rather than optional** for that reason. A
    goal that silently accepted a foreign ``note_id`` would be the one write in
    this phase that trusted an identifier from the request body, and it is exactly
    the write nobody would notice failing.

    ``activity`` is the history sink, and it is wired rather than left as the
    service's documented ``None`` mode for the reason this module's docstring
    gives in full: a goal created through the API that records nothing is a fact
    about the account nobody can later read back. ``None`` stays a real mode for
    exercising the service against a hand-built snapshot; it is not the API
    layer.

    Settings are injected rather than left to the service's own ``get_settings()``
    fallback, so the goal and skill caps, the window bounds and the minimum
    evidence before NEXUS will present its own estimate are the ones resolved for
    this request.
    """
    return LearningIntelligenceService(
        repositories,
        projects,
        notes,
        activity=activity,
        settings=settings,
    )


LearningIntelligenceServiceDep = Annotated[
    LearningIntelligenceService, Depends(get_learning_service)
]


def get_career_service(
    repositories: CareerRepositoryDep,
    learning: LearningRepositoryDep,
    projects: ProjectRepositoryDep,
    developer: DeveloperRepositoryDep,
    activity: ActivityServiceDep,
    settings: SettingsDep,
) -> CareerIntelligenceService:
    """Provide a request-scoped Phase 9 career-intelligence service.

    **Four collaborators, one per question the page asks.** The career tables are
    one repository; ``learning`` supplies the recorded-activity count and the
    ownership check on a ``skill_id`` an evidence row points at; ``projects``
    supplies the project counts and the same ownership check for
    ``project_id``; and ``developer`` supplies the repository count plus the one
    join behind the ``project_activity`` feature. Reading Phase 8's repository
    here rather than a second career-side copy of it is what stops the career page
    and the developer page disagreeing about how many repositories an account has
    registered.

    **Nothing here writes a qualification.** Every collaborator exists to count
    rows the user typed or a subsystem recorded, and to prove a pointer belongs to
    the caller before it is stored. There is no generator in this graph, which is
    what makes the rule a property of the wiring rather than an intention.

    ``activity`` is wired for the same reason as every other service in this file:
    a profile edit or a piece of evidence entered and then corrected is a fact
    about the account, and an unrecorded one cannot be read back at all.

    Settings are injected rather than left to the service's own ``get_settings()``
    fallback, so the evidence cap and the window bounds a request runs under are
    the ones resolved for this request.
    """
    return CareerIntelligenceService(
        repositories,
        learning=learning,
        projects=projects,
        developer=developer,
        activity=activity,
        settings=settings,
    )


CareerIntelligenceServiceDep = Annotated[CareerIntelligenceService, Depends(get_career_service)]


def get_ml_runtime(request: Request) -> MLRuntime:
    """Provide the process-wide Phase 11 intent classifier runtime.

    **A provider function, not ``Depends(MLRuntime)``,** for the same reason as
    every other ``Dep`` alias in this file: the return type is a plain object
    that is not a Pydantic model, and handing FastAPI a class is what produces
    ``Invalid args for response field`` at import time.

    The runtime is read from ``request.app.state`` because the lifespan is what
    loads the model, and a router asking for the classifier must be asking the
    instance the server actually loaded rather than constructing a second one —
    two would mean two copies of 703 MiB of weights.

    **The fallback exists because the test suite has no lifespan.** Clients built
    on ``ASGITransport`` never execute one, so ``app.state.ml_runtime`` is simply
    absent there; falling back to the module singleton keeps those routes
    importable and callable, and leaves the singleton *unloaded* — which is
    honest, because nothing in that process loaded a model either. The endpoint
    then reports the runtime's reason instead of pretending ML is up.

    Importing this module therefore must not import ``torch``:
    :mod:`app.ml.runtime` reaches for the classifier inside a function, so a
    server with ML switched off never pays for the dependency at startup.

    Args:
        request: The in-flight request, carrying the application the lifespan
            configured.

    Returns:
        The loaded runtime, or a degraded one whose ``is_available`` is False.
    """
    runtime: MLRuntime | None = getattr(request.app.state, "ml_runtime", None)
    if runtime is None:
        runtime = get_process_ml_runtime()
    return runtime


MLRuntimeDep = Annotated[MLRuntime, Depends(get_ml_runtime)]


def get_client_context(request: Request, settings: SettingsDep) -> tuple[str | None, str | None]:
    """Return ``(ip_address, user_agent)`` for the calling request.

    The address rule is the one :func:`app.core.middleware._client_ip` applies
    to the rate limiter — **the left-most ``X-Forwarded-For`` entry only when
    ``settings.rate_limit_trust_forwarded_for`` is on, and the socket peer
    otherwise**. The two must move together: one module trusting the header
    while the other did not would leave the audit row and the limiter describing
    the same request differently, and turning the setting on would have to be
    remembered in two places. The access log is the deliberate exception — it
    believes the header unconditionally, because a misattributed log line costs
    nothing; ``ip_address`` is the field an investigation reads, and it is
    written by whatever the caller claimed.

    Defaulting that setting to False is what keeps ``ip_address`` an
    *attribution* rather than a free-text field: without a proxy we control,
    anything that can reach the process can put any string in the header, and
    an audit row whose address the caller wrote is worse than no address at
    all.

    **Neither value is an identity claim.** Both headers are supplied by the
    client: anything that can reach the process can put any string in them. They
    are recorded because "this sign-in came from that address, with that browser"
    is what makes an audit row worth reading, not because they are trustworthy.
    No authorisation decision may ever rest on them — see
    :attr:`app.models.session.Session.ip_address`.
    """
    ip_address = ""
    if settings.rate_limit_trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        ip_address = forwarded.split(",")[0].strip() if forwarded else ""
    if not ip_address:
        ip_address = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent") or ""
    return ip_address or None, user_agent[:_MAX_USER_AGENT_LENGTH] or None


ClientContext = Annotated[tuple[str | None, str | None], Depends(get_client_context)]


async def get_authenticated_user(
    current_user: Annotated[User, Depends(get_current_user)],
    credentials: Credentials,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    sessions: SessionRepositoryDep,
    session_id: CurrentSessionId,
) -> User:
    """Resolve the caller and reject a bearer the session table no longer backs.

    Two layers, in this order, and the second is the one that makes revocation
    mean what the UI tells the user it means.

    **The denylist first.** :meth:`AuthService.is_revoked` catches the single
    ``jti`` that logout presented, or that a rotation just retired. It is
    process-local by construction, so it is belt-and-braces rather than the
    control.

    **The session row second, and this is the control.** A JWT stays
    cryptographically valid until it expires, so checking only the denylist left
    revoking a device ending nothing but that device's *refresh* credential:
    every access token already minted from it went on authorising requests for
    the whole ``ACCESS_TOKEN_EXPIRE_MINUTES`` window, and ``DELETE
    /auth/sessions/{id}``, ``POST /auth/logout-all`` and ``PATCH
    /auth/password`` all produced that same gap. The row lives in the database,
    so requiring it also makes revocation survive a restart — a documented
    limitation of the denylist that no longer applies to this path.

    **Cost: one primary-key lookup per authenticated request.** For a local-first,
    single-user deployment that is the right trade — the alternative is up to an
    hour during which "that device is signed out" is not true. If the lookup ever
    becomes hot, the follow-up is a per-session cache with a short TTL, never one
    that can outlive a revocation.

    **No new trust boundary.** The ``sid`` claim is inside a signed JWT and
    ``get_current_user`` has already bound the subject to this user, so the row
    cannot name somebody else's device. It is looked up with
    ``get_by_id_for_user(sid, user.id)`` anyway — scoping a lookup by the
    subject it belongs to is free, and it means a future change to how the claim
    is built cannot turn into a cross-tenant read.

    Args:
        current_user: The caller, already resolved from the same token.
        credentials: The raw bearer, when the request carried one.
        auth: Supplies the revocation denylist.
        sessions: Supplies the session row the token claims to belong to.
        session_id: The token's ``sid`` claim, or ``None`` when it has no usable
            one. ``None`` is fatal here rather than skipped: a token with no
            session behind it cannot be revoked individually, which is exactly
            the gap sessions exist to close.

    Returns:
        The caller, unchanged.

    Raises:
        UnauthorizedError: If the token was denylisted, names no usable session,
            or names one that is missing, not the caller's, revoked or expired.
    """
    if credentials is not None and credentials.credentials:
        token_data = decode_token(credentials.credentials, expected_type=TokenType.ACCESS)
        if await auth.is_revoked(token_data):
            raise UnauthorizedError("This token has been revoked.")
    if session_id is None:
        raise UnauthorizedError(_SESSION_NOT_LIVE)
    row = await sessions.get_by_id_for_user(session_id, current_user.id)
    now = datetime.now(UTC)
    if row is None or row.revoked_at is not None or _session_expired(row.expires_at, now):
        raise UnauthorizedError(_SESSION_NOT_LIVE)
    return current_user


AuthenticatedUser = Annotated[User, Depends(get_authenticated_user)]


def _session_expired(expires_at: datetime | None, now: datetime) -> bool:
    """Whether a session row's expiry has already passed.

    Mirrors :func:`app.services.session_service._is_expired` rather than sharing
    it: that helper is private to the service layer and importing across module
    boundaries would make an internal become part of this module's contract. A
    naive value is read as UTC, because the column is timezone-aware but a value
    assembled in memory is not guaranteed to be, and treating it as local time
    expires sessions early for anyone east of UTC.

    The comparison is against this process's clock rather than the database's
    ``func.now()``, which is the one place this path departs from the rule
    :mod:`app.repositories.session` follows everywhere else. The instant itself
    comes from the row, so the only exposure is the drift between the host's
    clock and the server's — and a session's expiry is days away, not seconds.

    Args:
        expires_at: The row's ``expires_at``, or ``None`` for a value that was
            never set. A missing expiry counts as expired: a session that cannot
            say when it dies is not a session that outlives.
        now: The instant to compare against, read once by the caller so the
            comparison is against a single clock reading.
    """
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= now


__all__ = [
    "ActivityRepository",
    "ActivityRepositoryDep",
    "ActivityService",
    "ActivityServiceDep",
    "AnalyticsRepository",
    "AnalyticsRepositoryDep",
    "AnalyticsService",
    "AnalyticsServiceDep",
    "AuditRepository",
    "AuditRepositoryDep",
    "AuditService",
    "AuditServiceDep",
    "AuthService",
    "AuthServiceDep",
    "AuthenticatedUser",
    "AvailabilityRuleRepository",
    "AvailabilityRuleRepositoryDep",
    "BookmarkRepository",
    "BookmarkRepositoryDep",
    "CalendarEventRepository",
    "CalendarEventRepositoryDep",
    "CareerIntelligenceService",
    "CareerIntelligenceServiceDep",
    "CareerRepository",
    "CareerRepositoryDep",
    "CategoryRepository",
    "CategoryRepositoryDep",
    "ClientContext",
    "ConceptRepository",
    "ConceptRepositoryDep",
    "Credentials",
    "CurrentSessionId",
    "CurrentUser",
    "DbSession",
    "DeveloperIntelligenceService",
    "DeveloperIntelligenceServiceDep",
    "DeveloperRepository",
    "DeveloperRepositoryDep",
    "DocumentRepository",
    "DocumentRepositoryDep",
    "KnowledgeLinkRepository",
    "KnowledgeLinkRepositoryDep",
    "KnowledgeService",
    "KnowledgeServiceDep",
    "LearningIntelligenceService",
    "LearningIntelligenceServiceDep",
    "LearningRepository",
    "LearningRepositoryDep",
    "MLRuntime",
    "MLRuntimeDep",
    "NoteRepository",
    "NoteRepositoryDep",
    "PasswordResetRepository",
    "PasswordResetRepositoryDep",
    "PlannerService",
    "PlannerServiceDep",
    "ProjectRepository",
    "ProjectRepositoryDep",
    "ProjectService",
    "ProjectServiceDep",
    "RecommendationService",
    "RecommendationServiceDep",
    "ResourceRepository",
    "ResourceRepositoryDep",
    "RiskDetectionService",
    "RiskRepository",
    "RiskRepositoryDep",
    "RiskServiceDep",
    "SchedulingService",
    "SchedulingServiceDep",
    "SessionRepository",
    "SessionRepositoryDep",
    "SessionService",
    "SessionServiceDep",
    "Settings",
    "SettingsDep",
    "SuperUser",
    "TagRepository",
    "TagRepositoryDep",
    "TagService",
    "TagServiceDep",
    "TaskRepository",
    "TaskRepositoryDep",
    "TaskService",
    "TaskServiceDep",
    "UserRepository",
    "UserRepositoryDep",
    "UserService",
    "UserServiceDep",
    "WorkSessionRepository",
    "WorkSessionRepositoryDep",
    "bearer_scheme",
    "get_activity_repository",
    "get_activity_service",
    "get_analytics_repository",
    "get_analytics_service",
    "get_audit_repository",
    "get_audit_service",
    "get_auth_service",
    "get_authenticated_user",
    "get_availability_rule_repository",
    "get_bookmark_repository",
    "get_calendar_event_repository",
    "get_career_repository",
    "get_career_service",
    "get_category_repository",
    "get_client_context",
    "get_concept_repository",
    "get_current_session_id",
    "get_current_user",
    "get_developer_repository",
    "get_developer_service",
    "get_document_repository",
    "get_knowledge_link_repository",
    "get_knowledge_service",
    "get_learning_repository",
    "get_learning_service",
    "get_ml_runtime",
    "get_note_repository",
    "get_optional_user",
    "get_password_reset_repository",
    "get_planner_service",
    "get_project_repository",
    "get_project_service",
    "get_recommendation_service",
    "get_resource_repository",
    "get_risk_repository",
    "get_risk_service",
    "get_scheduling_service",
    "get_session_repository",
    "get_session_service",
    "get_settings",
    "get_tag_repository",
    "get_tag_service",
    "get_task_repository",
    "get_task_service",
    "get_user_repository",
    "get_user_service",
    "get_work_session_repository",
]
