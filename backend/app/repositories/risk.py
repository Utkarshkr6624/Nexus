"""Data access for derived risks, recommendations, and evaluation summaries.

The repository owns SQL only. It never raises a *domain* error: a foreign id is
answered with an empty result or ``None``, never an exception. The only guards
against a *programming* error are the enum coercions in the two upserts and the
two transitions, and those raise :class:`ValueError` — a bad risk type, severity or
status is a fact about the calling code, not about the caller's data, and it
should fail at the call site rather than write a row nothing can order or find
again. Every method that takes an owner id puts it in the ``WHERE`` clause rather
than filtering a loaded page afterwards: a row the caller may not see is never
loaded at all, and another user's id is answered with an empty result or ``None``
— identically to an id that does not exist, so this cannot be used to probe which
ids are real.

Four decisions carry the file, and three of them are decisions about what
PostgreSQL will *not* do for us.

**The dedup insert is a real ``INSERT ... ON CONFLICT`` against a partial
index, and it has to name the index predicate.** ``uq_risks_live_identity`` is
*partial* (``WHERE status IN ('active', 'acknowledged')``), and PostgreSQL will
not infer a partial index from a bare column list: an ``ON CONFLICT
(user_id, risk_type, entity_type, entity_id)`` that omits the predicate fails
with "there is no unique or exclusion constraint matching the ON CONFLICT
specification". So both upserts pass ``index_elements`` *and* ``index_where``,
carrying the same predicate text migration ``0007`` used. The alternative — a
select-then-write, which the contract permits — was rejected for the row-level
case because it reads a row and then writes it in two statements, and two
concurrent detection runs can both read "nothing there" before either inserts.
The partial index is the arbiter, and it is already there precisely so that
storage owns the invariant.

**A null does not collide, and account-level risks depend on that.** In a btree
unique index two nulls are distinct, so a risk with ``entity_id IS NULL`` — the
workload and consistency detectors both produce those, because they are about
the account rather than about a row — is *not* deduplicated by the index at all.
An ``ON CONFLICT`` upsert over such an identity would therefore insert a fresh
row on every run, which is precisely the "hundreds of identical records" the
brief forbids. So the upsert is a hybrid: row-level identities (both
``entity_type`` and ``entity_id`` present) take the index-arbitrated path, and
null-bearing identities take a select-then-write guarded by a transaction-scoped
advisory lock keyed on the identity, because otherwise the *absence* of an index
becomes the only thing standing between two runs and a duplicate. The lock is
taken in the null branch only, so the common case still costs exactly one
statement. Both branches return the same ``(row, created)`` pair, so no caller
has to know which one it got.

That same null-sensitivity runs through the reads. Every identity comparison
uses ``IS NOT DISTINCT FROM`` rather than ``=`` — ``entity_id = NULL`` is never
true, so a plain equality would answer "no such risk" for a workload risk that
exists, and the dedup lookup, the stale sweep and the recommendation lookup
would each fail to find what they are looking for.

**Severity is ordered by rank, not by string.** The words do not sort
alphabetically into their own severity order — ``medium`` > ``low`` > ``high`` >
``critical`` under a plain ``ORDER BY severity DESC``, which would put a medium
risk above a critical one in the Risk Center's own list. Ordering therefore goes
through a ``CASE`` rank built from :class:`~app.models.enums.RiskSeverity`
itself, so the ordering cannot drift from the vocabulary if the words are ever
re-spelled. The cost is that the list ordering can no longer be served straight
off ``ix_risks_owner_status_severity``; that is accepted, because the alternative
is a list that ranks the user's problems wrongly. The index keeps its value for a
different job: ``severity`` is its *third* column, so the Risk Center's band
filter is an equality probe on ``(user_id, status, severity)`` rather than a scan
— which is why adding a server-side severity filter needed no migration.

**Counts are one grouped query, seeded, and carry no derived total.**
:meth:`RiskRepository.count_by_severity` returns every severity key with a zero
rather than only the buckets that happen to be populated, because the Risk Center
header renders four fixed buckets and would raise on a missing key the first time
a user had no low-severity risk at all. It deliberately adds no ``"total"``
key: the response schema carries ``total`` beside ``by_severity``, and a second
total inside the tally would be a number the client could sum twice or compare
against itself.

Two smaller rules, both inherited from Phase 6 rather than invented here.
``updated_at`` is written explicitly into every ``ON CONFLICT`` ``set_``
mapping, because a Core ``insert().on_conflict_do_update()`` bypasses the
``onupdate`` that :class:`~app.db.base.TimestampMixin` declares and would
otherwise leave every re-detected risk stamped with the time it was first seen.
And a naive ``datetime`` handed to this module is read as UTC rather than passed
through, because PostgreSQL would otherwise interpret it in whatever ``TimeZone``
the connection was configured with — the same silent drift
:mod:`app.repositories.analytics` refuses when it buckets days.

The repository holds no detection *rules*. Which risk exists, in what words, and
whether it is worth storing at all is the service layer's judgement; what lives
here is the bounded reading, the lifecycle write, and the upsert, because the
conflict target is a constraint storage has to own.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    and_,
    case,
    cast,
    func,
    literal,
    literal_column,
    or_,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import Case

from app.models.enums import (
    EvidenceStrength,
    RecommendationPriority,
    RecommendationStatus,
    RecommendationType,
    RiskSeverity,
    RiskStatus,
    RiskType,
    validate_recommendation_status,
    validate_recommendation_type,
    validate_risk_status,
    validate_risk_type,
)
from app.models.risk import (
    LIVE_RISK_STATUSES,
    OPEN_RECOMMENDATION_STATUSES,
    Recommendation,
    Risk,
    RiskEvaluation,
)

__all__ = ["RiskRepository"]

#: The two statuses a risk is *live* in, and therefore the states a detection
#: run can re-detect into without creating a second row. Imported from the model
#: rather than restated: the index, this predicate and the service's resolution
#: sweep all key on the same set, and a second copy of it is a second opinion.
_LIVE_STATUSES = LIVE_RISK_STATUSES
_OPEN_STATUSES = OPEN_RECOMMENDATION_STATUSES

#: Spellings of the partial-index predicates, byte for byte as migration ``0007``
#: generated them. ``ON CONFLICT`` infers a *partial* unique index only when the
#: inference specification includes the index's own predicate; a paraphrase of
#: the same condition parses to a different expression and PostgreSQL reports
#: "no unique or exclusion constraint matching the ON CONFLICT specification".
#: Duplicated rather than imported because the model keeps these private (they
#: are DDL, not a contract other modules may rely on), and a repository that
#: cannot infer its conflict target should say so at the call site.
_RISKS_LIVE_PREDICATE = "status IN ('active', 'acknowledged')"
_RECOMMENDATIONS_OPEN_PREDICATE = "status IN ('new', 'viewed')"

#: A risk is terminal once the condition it describes has stopped being news.
#: ``resolved_at`` is stamped for these and for nothing else, and the check
#: constraint ``ck_risks_terminal_has_timestamp`` rejects a terminal row without
#: one — so the two agree by construction rather than by review.
_TERMINAL_RISK_STATUSES = (RiskStatus.RESOLVED.value, RiskStatus.DISMISSED.value)

#: The lifecycle, as a source-to-targets map.
#:
#: A terminal risk is terminal. Re-opening one would be a different product
#: decision from the one the schema already made: ``uq_risks_live_identity`` is
#: *partial*, so a condition that goes away and comes back is representable as a
#: new row, and reusing the old one would overwrite the ``detected_at`` that
#: ``resolved_at`` is measured against — destroying the only record of how long
#: the previous episode was open. ``acknowledged`` may not fall back to
#: ``active`` either, because re-detection deliberately does not undo an
#: acknowledgement (see :meth:`RiskRepository.upsert_risk`) and a transition is
#: the only other thing that could.
_RISK_TRANSITIONS: dict[str, frozenset[str]] = {
    RiskStatus.ACTIVE.value: frozenset(
        {
            RiskStatus.ACTIVE.value,
            RiskStatus.ACKNOWLEDGED.value,
            RiskStatus.RESOLVED.value,
            RiskStatus.DISMISSED.value,
        }
    ),
    RiskStatus.ACKNOWLEDGED.value: frozenset(
        {
            RiskStatus.ACKNOWLEDGED.value,
            RiskStatus.RESOLVED.value,
            RiskStatus.DISMISSED.value,
        }
    ),
    RiskStatus.RESOLVED.value: frozenset({RiskStatus.RESOLVED.value}),
    RiskStatus.DISMISSED.value: frozenset({RiskStatus.DISMISSED.value}),
}

#: The same shape for recommendations, with one addition: a recommendation the
#: user accepted can still be completed, because "I will do this" and "I did
#: this" are two events and Phase 10 wants the pair. A rejected recommendation is
#: terminal, which is what makes an identical suggestion re-raisable — see
#: :data:`app.models.risk.OPEN_RECOMMENDATION_STATUSES`.
_RECOMMENDATION_TRANSITIONS: dict[str, frozenset[str]] = {
    RecommendationStatus.NEW.value: frozenset(status.value for status in RecommendationStatus),
    RecommendationStatus.VIEWED.value: frozenset(
        {
            RecommendationStatus.VIEWED.value,
            RecommendationStatus.ACCEPTED.value,
            RecommendationStatus.REJECTED.value,
            RecommendationStatus.COMPLETED.value,
            RecommendationStatus.EXPIRED.value,
        }
    ),
    RecommendationStatus.ACCEPTED.value: frozenset(
        {RecommendationStatus.ACCEPTED.value, RecommendationStatus.COMPLETED.value}
    ),
    RecommendationStatus.REJECTED.value: frozenset({RecommendationStatus.REJECTED.value}),
    RecommendationStatus.COMPLETED.value: frozenset({RecommendationStatus.COMPLETED.value}),
    RecommendationStatus.EXPIRED.value: frozenset({RecommendationStatus.EXPIRED.value}),
}

#: The recommendation statuses that mean the user answered. ``viewed`` is not one
#: of them — a suggestion being opened is not a decision about it, and the model
#: makes the same distinction by keeping ``viewed`` inside the open set. This is
#: what decides whether ``transition_recommendation`` stamps ``responded_at``.
_ANSWERED_RECOMMENDATION_STATUSES = (
    RecommendationStatus.ACCEPTED.value,
    RecommendationStatus.REJECTED.value,
    RecommendationStatus.COMPLETED.value,
)


def _severity_rank(column: Any) -> Case[Any]:
    """Turn a severity or priority column into an orderable rank.

    ``ORDER BY severity DESC`` on the stored words is wrong, because
    ``'medium'`` sorts above ``'low'`` and both sort above ``'high'``. Building
    the rank from the enum — reversed, so the most severe member gets the
    highest number — means the ordering is a property of the vocabulary rather
    than of the alphabet, and stays correct if a word is ever re-spelled.
    """
    ranks = {member.value: index for index, member in enumerate(reversed(tuple(RiskSeverity)))}
    return case(ranks, value=column, else_=0)


def _priority_rank(column: Any) -> Case[Any]:
    """:func:`_severity_rank` for recommendations.

    A separate function rather than a shared one only because the two scales are
    separate enums; the reasoning about alphabetical order applies identically,
    and a recommendation list ordered by the wrong rank would put a ``medium``
    above a ``critical`` suggestion just as the risk list would.
    """
    ranks = {
        member.value: index for index, member in enumerate(reversed(tuple(RecommendationPriority)))
    }
    return case(ranks, value=column, else_=0)


def _as_utc(instant: datetime | None) -> datetime | None:
    """Read a caller-supplied instant as UTC when it carries no offset.

    A naive ``datetime`` handed to a ``timestamptz`` column is interpreted in the
    *session's* ``TimeZone`` by PostgreSQL, so the same ``resolved_at`` would
    land at a different instant depending on which connection ran the statement.
    Attaching UTC here makes the value a property of the caller rather than of
    the connection — the same rule :mod:`app.repositories.analytics` follows when
    it refuses to bucket a ``timestamptz`` without naming the zone. An
    already-aware value is returned untouched, offset and all.
    """
    if instant is None or instant.tzinfo is not None:
        return instant
    return instant.replace(tzinfo=UTC)


def _evidence_rows(evidence: Iterable[Mapping[str, Any] | Any]) -> list[dict[str, Any]]:
    """Normalise a detector's evidence into JSONB-shaped rows.

    The detection service holds :class:`~app.services.risk.scoring.RiskEvidence`
    dataclasses and the schema serves dictionaries, so the conversion has to
    happen exactly once and it happens here — on the way into storage — rather
    than in the service and again in the serializer.

    Raises:
        TypeError: If a line is neither a mapping nor a dataclass. There is no
            sensible third rendering of "one input that moved a score", and
            guessing would store evidence the UI cannot show.
    """
    rows: list[dict[str, Any]] = []
    for line in evidence:
        if isinstance(line, Mapping):
            rows.append(dict(line))
        elif is_dataclass(line) and not isinstance(line, type):
            rows.append(asdict(line))
        else:
            raise TypeError(
                f"Cannot store evidence line of type {type(line).__name__!r}; "
                "risk evidence must be a mapping or a dataclass instance."
            )
    return rows


#: Column name -> ORM attribute name, for the columns where the two differ.
#:
#: ``metadata`` is reserved on a declarative class (``Base.metadata`` is the
#: registry), so both risk models declare the column ``metadata`` under the
#: attribute ``metadata_``. The Core insert path can use the column name
#: directly; the ORM path — ``Risk(**values)`` and ``setattr`` — cannot, and
#: using the column name there sets a stray instance attribute that SQLAlchemy
#: never writes. The symptom was silent and total: every account-level risk
#: (workload, consistency and estimation — three of the six detectors, and every
#: risk whose ``entity_id`` is null) stored ``{}`` while its sibling going
#: through the indexed path stored correctly.
#:
#: Translating once at the boundary is the fix. Renaming either side then needs
#: changing in one place rather than in four scattered ORM writes.
_ORM_ATTRIBUTE_OVERRIDES: dict[str, str] = {"metadata": "metadata_"}


def _to_orm_attributes(values: Mapping[str, Any]) -> dict[str, Any]:
    """Rewrite column names to ORM attribute names for an ORM write."""
    return {_ORM_ATTRIBUTE_OVERRIDES.get(key, key): value for key, value in values.items()}


def _risk_filters(
    owner_id: uuid.UUID,
    *,
    statuses: Sequence[str] | None,
    risk_types: Sequence[str] | None,
    severities: Sequence[str] | None,
) -> list[Any]:
    """Build the one ``WHERE`` clause the risk list, its count and its tally share.

    Three statements answer three questions about the same question — "which rows
    do these filters match" — and a filter written out three times is three
    chances for the header to disagree with the rows under it. Building it once
    is what makes ``by_severity`` a description of the filtered set rather than a
    near neighbour of it, and it is why this takes all three filter kinds as
    required keywords: a caller cannot add a filter to one statement and forget
    the others.

    Each is a sequence rather than a single value because that is what the
    filters really are — "one of these statuses" is the live set, and a caller
    that wants one word passes one word. An empty or absent sequence means *no
    restriction on this column*, which is the same convention as the SQL: the
    predicate is simply not added.

    Args:
        owner_id: Whose risks, always asserted rather than filtered afterwards.
        statuses: Restrict to these :class:`~app.models.enums.RiskStatus` values.
        risk_types: Restrict to these :class:`~app.models.enums.RiskType` values.
        severities: Restrict to these
            :class:`~app.models.enums.RiskSeverity` values.

    Returns:
        The predicates, owner first. ``ix_risks_owner_status_severity`` is keyed
        on ``(user_id, status, severity)``, so a query carrying both a status and
        a band is an equality probe on three indexed columns and needs no new
        index to stay cheap.
    """
    filters: list[Any] = [Risk.user_id == owner_id]
    if statuses:
        filters.append(Risk.status.in_(list(statuses)))
    if risk_types:
        filters.append(Risk.risk_type.in_(list(risk_types)))
    if severities:
        filters.append(Risk.severity.in_(list(severities)))
    return filters


class RiskRepository:
    """Risk, recommendation, and evaluation persistence for one session.

    Every read is owner-scoped and every write carries the owner id explicitly,
    including the upserts — where the id is also part of the conflict target, so
    a row can never be adopted by a different user even if a caller passed a
    mismatched pair.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Risks
    # ------------------------------------------------------------------

    async def list_risks(
        self,
        owner_id: uuid.UUID,
        *,
        statuses: Sequence[str] | None = None,
        risk_types: Sequence[str] | None = None,
        severities: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Risk], int]:
        """The Risk Center list: this owner's risks, worst first, and the total.

        Ordered by severity rank then ``detected_at`` descending, with ``id`` as
        a final tiebreaker. The tiebreaker is not decoration: ``detected_at`` is
        second-resolution, so without a total order two rows written in the same
        run could swap places between page requests and a caller paging through
        would see one twice and miss another.

        The unpaginated total comes from a window function over the same rows
        rather than a second ``COUNT``, so the page and the total are one
        statement and cannot describe two different snapshots.

        Args:
            owner_id: Whose risks to list.
            statuses: Restrict to these statuses. ``None`` means every status —
                the Risk Center's own filter is the usual caller, and it passes
                the live set explicitly.
            risk_types: Restrict to these :class:`~app.models.enums.RiskType`
                values. Same convention as ``statuses``.
            severities: Restrict to these
                :class:`~app.models.enums.RiskSeverity` values. This is the band
                filter the Risk Center's tiles drive, and it is answered here
                rather than in the client so a band count can reach beyond the
                current page. It costs nothing to serve: ``severity`` is the third
                column of ``ix_risks_owner_status_severity``, so a caller holding
                both a status and a band is probing all three indexed columns for
                equality.
            limit: Page size.
            offset: Rows to skip.

        Returns:
            The page of rows and the total number of rows the filters match.
        """
        filters = _risk_filters(
            owner_id, statuses=statuses, risk_types=risk_types, severities=severities
        )

        statement = (
            select(Risk, func.count().over().label("total"))
            .where(*filters)
            .order_by(
                _severity_rank(Risk.severity).desc(),
                Risk.detected_at.desc(),
                Risk.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        rows = list((await self.session.execute(statement)).all())
        total = int(rows[0].total) if rows else await self._count_risks(owner_id, filters)
        return [row[0] for row in rows], total

    async def _count_risks(self, owner_id: uuid.UUID, filters: Sequence[Any]) -> int:
        """Count the rows a filtered list would return, for the empty-page case.

        A window count only exists on a returned row, so a filter matching
        nothing has to be counted separately. The owner predicate is re-asserted
        rather than trusted from the caller: this statement is the one place a
        page and its total could otherwise disagree, and it is the empty page
        that a dashboard renders as "nothing to see".
        """
        return int(
            await self.session.scalar(
                select(func.count()).select_from(Risk).where(Risk.user_id == owner_id, *filters)
            )
        )

    async def get_risk(self, owner_id: uuid.UUID, risk_id: uuid.UUID) -> Risk | None:
        """One risk, or ``None`` if it is not this owner's.

        The route turns ``None`` into a 404. That is deliberate and is the same
        rule Phase 6 settled: a foreign id is *not* a 403, because a 403 would
        confirm the id exists and turn the endpoint into a probe for which risk
        ids are real. Nothing is raised here and nothing about another user's
        row is loaded.
        """
        result = await self.session.execute(
            select(Risk).where(Risk.id == risk_id, Risk.user_id == owner_id)
        )
        return result.scalar_one_or_none()

    async def find_live_risk(
        self,
        owner_id: uuid.UUID,
        *,
        risk_type: str | RiskType,
        entity_type: str | None,
        entity_id: uuid.UUID | None,
    ) -> Risk | None:
        """The live risk for one condition identity, if there is one.

        The dedup lookup, and the read that the null-identity path of
        :meth:`upsert_risk` is built on. Both entity columns are compared with
        ``IS NOT DISTINCT FROM`` rather than ``=`` because the account-level
        detectors produce ``entity_id IS NULL``, and ``entity_id = NULL`` is
        never true — a plain equality would answer "no such risk" for a workload
        risk that exists and then insert a second one beside it.

        Returns the ``active`` **or** ``acknowledged`` row, because both are live
        and re-detecting into either must update rather than duplicate. When both
        existed the older ``detected_at`` wins, so the answer is deterministic
        regardless of the plan.
        """
        result = await self.session.execute(
            select(Risk)
            .where(
                Risk.user_id == owner_id,
                Risk.risk_type == validate_risk_type(risk_type).value,
                Risk.entity_type.is_not_distinct_from(entity_type),
                Risk.entity_id.is_not_distinct_from(entity_id),
                Risk.status.in_(_LIVE_STATUSES),
            )
            .order_by(Risk.detected_at.asc(), Risk.id.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def upsert_risk(
        self,
        owner_id: uuid.UUID,
        *,
        risk_type: str | RiskType,
        severity: str | RiskSeverity,
        score: int,
        title: str,
        description: str,
        evidence: Iterable[Mapping[str, Any] | Any] = (),
        evidence_strength: str | EvidenceStrength = EvidenceStrength.LOW,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> tuple[Risk, bool]:
        """Store one detection, creating the risk or refreshing the live one.

        **This is the idempotency anchor of the whole engine.** A condition that
        is detected on every run is one row with a moving score, not a row per
        run; that is what makes the brief's "the same underlying risk should not
        generate hundreds of identical records" true, and the partial index
        ``uq_risks_live_identity`` is what enforces it.

        On a refresh, ``severity``, ``score``, ``title``, ``description``,
        ``evidence``, ``evidence_strength`` and ``metadata`` are replaced
        wholesale rather than merged. The detection that produced the row is the
        only thing that should describe it, and a merge would accumulate stale
        evidence lines from a detection that no longer holds.

        Two columns are deliberately **not** written on refresh:

        * ``detected_at`` keeps the instant the *current episode* began. It is
          the left-hand side of ``resolved_at - detected_at``, which the model
          documents as how "how long was this open" is answered without reading
          the event log; refreshing it every run would make every live risk look
          brand new and every resolved one look instantaneous.
        * ``status`` is left alone, so re-detecting an ``acknowledged`` risk does
          not drag it back to ``active``. Acknowledging says "I have seen this
          and accept it is still true", and nothing about re-measuring it should
          revoke that.

        The two paths differ in how the duplicate is prevented, and only because
        PostgreSQL makes them differ: a row-level identity (both entity columns
        present) is arbitrated by the partial index through a single
        ``INSERT ... ON CONFLICT`` that names the index and its predicate, while
        an identity with a null entity — the account-level workload and
        consistency risks — cannot be arbitrated at all, because nulls do not
        collide in a btree unique index. That path takes a transaction-scoped
        advisory lock on the identity, then reads and writes inside the same
        transaction, so two concurrent runs cannot both conclude "nothing there"
        and both insert.

        Args:
            owner_id: Whose risk this is; also the leading conflict-target column.
            risk_type: What kind of condition this is.
            severity: The band derived from ``score`` by
                :func:`~app.services.risk.scoring.risk_severity_for`. Validated
                here rather than trusted, because an unrecognised word would
                order wrongly in every list it appeared in.
            score: 0-100, already rounded and clamped by the scoring module.
            title: One-line, neutral statement of the condition.
            description: The plain-language account of what is true.
            evidence: Ordered evidence lines; mappings or
                :class:`~app.services.risk.scoring.RiskEvidence` dataclasses.
            evidence_strength: How much data the score came from.
            entity_type: ``task`` / ``project`` / ``account``, or ``None``.
            entity_id: The row the risk is about, or ``None`` for an account-level
                risk.
            metadata: The raw inputs the score was computed from.

        Returns:
            ``(row, created)``. ``created`` is ``True`` only when this call
            inserted the risk. The detection service emits ``RISK_DETECTED`` on
            ``created`` and ``RISK_UPDATED`` otherwise, and the evaluation
            summary counts the two separately, so collapsing them would make the
            event feed and the trend history disagree with each other.

        Raises:
            ValueError: If ``risk_type``, ``severity`` or ``evidence_strength`` is
                not a known value, or ``score`` falls outside 0-100. Both are
                programming errors — the words come from the scoring module and
                the number from its formula — and failing at this call site beats
                writing a row nothing can order or explain.
        """
        risk_type_value = validate_risk_type(risk_type).value
        severity_value = RiskSeverity(severity).value
        strength_value = EvidenceStrength(evidence_strength).value
        if not 0 <= int(score) <= 100:
            raise ValueError(f"Cannot store a risk scored {score}; scores run 0-100.")
        rows = _evidence_rows(evidence)
        values: dict[str, Any] = {
            "severity": severity_value,
            "score": int(score),
            "title": title,
            "description": description,
            "evidence": rows,
            "evidence_strength": strength_value,
            "metadata": dict(metadata or {}),
        }

        if entity_type is not None and entity_id is not None:
            return await self._upsert_indexed_risk(
                owner_id,
                risk_type=risk_type_value,
                entity_type=entity_type,
                entity_id=entity_id,
                values=values,
            )
        return await self._upsert_null_identity_risk(
            owner_id,
            risk_type=risk_type_value,
            entity_type=entity_type,
            entity_id=entity_id,
            values=values,
        )

    async def _upsert_indexed_risk(
        self,
        owner_id: uuid.UUID,
        *,
        risk_type: str,
        entity_type: str,
        entity_id: uuid.UUID,
        values: Mapping[str, Any],
    ) -> tuple[Risk, bool]:
        """The index-arbitrated path: one ``INSERT ... ON CONFLICT`` statement.

        ``xmax = 0`` is how the two outcomes are told apart: a tuple inserted
        into a fresh slot carries the zero system version, while one rewritten by
        ``DO UPDATE`` carries the updater's. It is the same discriminator
        :meth:`app.repositories.analytics.AnalyticsRepository.upsert_daily` uses,
        for the same reason — an ``ON CONFLICT`` that does not say which branch
        it took leaves the caller unable to distinguish "found this" from
        "created this".

        ``updated_at`` is written by the statement rather than left to the model.
        ``TimestampMixin.updated_at`` carries an ``onupdate`` of ``now()``, but
        that is a Core construct SQLAlchemy applies to UPDATE statements it
        generates itself; a Core ``insert().on_conflict_do_update()`` bypasses
        it entirely, so without this every re-detected risk would keep the
        timestamp of its first detection forever.

        ``populate_existing`` is not optional. A ``RETURNING`` ORM insert
        populates the identity-mapped instance only if it is not already there —
        so a risk this session had loaded earlier (by the dedup lookup, by the
        list, or by an earlier run in the same request) would keep its *old*
        attribute values and the caller would serialise a score the database had
        already moved past. The option overwrites the cached attributes with the
        row the statement just wrote, at no extra round trip.
        """
        statement = (
            pg_insert(Risk)
            .values(
                id=uuid.uuid4(),
                user_id=owner_id,
                risk_type=risk_type,
                entity_type=entity_type,
                entity_id=entity_id,
                **values,
            )
            .on_conflict_do_update(
                index_elements=[Risk.user_id, Risk.risk_type, Risk.entity_type, Risk.entity_id],
                index_where=literal_column(_RISKS_LIVE_PREDICATE),
                set_={**values, "updated_at": func.now()},
            )
            .returning(Risk, literal_column("xmax = 0").label("was_inserted"))
        )
        row = (
            await self.session.execute(statement.execution_options(populate_existing=True))
        ).one()
        await self.session.commit()
        return row[0], bool(row[1])

    async def _upsert_null_identity_risk(
        self,
        owner_id: uuid.UUID,
        *,
        risk_type: str,
        entity_type: str | None,
        entity_id: uuid.UUID | None,
        values: Mapping[str, Any],
    ) -> tuple[Risk, bool]:
        """The application-arbitrated path, for an identity with a null in it.

        PostgreSQL will not arbitrate this case — a btree unique index treats
        nulls as distinct, so there is no conflict to detect — so the
        repository arbitrates it instead, and must do so under a lock: the
        advisory lock is scoped to this transaction and keyed on the identity, so
        a second run computing the same account-level risk waits here, finds the
        row this one wrote, and updates it.

        Without the lock this method would be a correct-then-racy select-then-
        write, and the race is not exotic: an evaluation the user triggered by
        hand while the scheduled one is running produces exactly two sessions
        doing exactly this. The lock costs one statement and applies only to
        identities the index cannot protect.
        """
        await self.session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(
                        _identity_lock_key(owner_id, risk_type, entity_type, entity_id), 0
                    )
                )
            )
        )
        existing = await self.find_live_risk(
            owner_id,
            risk_type=risk_type,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        if existing is not None:
            for attribute, value in _to_orm_attributes(values).items():
                setattr(existing, attribute, value)
            # `updated_at` is a `TimestampMixin` column and this is an ORM write,
            # so the model's `onupdate` applies here; it is set explicitly nowhere
            # because the ORM already owns it on this path.
            self.session.add(existing)
            await self.session.commit()
            await self.session.refresh(existing)
            return existing, False

        row = Risk(
            id=uuid.uuid4(),
            user_id=owner_id,
            risk_type=risk_type,
            entity_type=entity_type,
            entity_id=entity_id,
            **_to_orm_attributes(values),
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row, True

    async def transition_risk(
        self,
        owner_id: uuid.UUID,
        risk_id: uuid.UUID,
        *,
        status: str | RiskStatus,
        resolved_at: datetime | None = None,
        responded: bool | None = None,
    ) -> Risk | None:
        """Move a risk along its lifecycle, or return ``None`` if it is not theirs.

        The write is a single owner-scoped ``UPDATE ... RETURNING`` rather than
        a read followed by a write, so a risk that belongs to someone else is
        never loaded, only *not* updated — and the returned ``None`` is what the
        route turns into a 404.

        ``resolved_at`` is stamped only for a terminal status, and only when the
        caller did not supply one. The check constraint on the table rejects a
        terminal row without the timestamp, and stamping it on a non-terminal
        transition would make "how long was this open" wrong for a risk the user
        merely acknowledged.

        ``responded`` records *who* moved it. The risk table has no
        ``responded_at`` column — unlike ``recommendations``, where "the user
        acted" is a fact the lifecycle genuinely tracks — so the answer is kept
        in ``metadata`` under ``resolution``, as ``"user"`` or ``"engine"``. It
        is what separates "they fixed it" from "the condition went away", which
        is the difference a future model would be trained on and a human reading
        a history would want told. ``None`` — the default — writes nothing, so a
        caller that does not care pays nothing and leaves the column alone.

        Args:
            owner_id: Whose risk this is.
            risk_id: The risk to move.
            status: The target status. Validated against
                :class:`~app.models.enums.RiskStatus`.
            resolved_at: The instant the condition ended. Defaults to the
                database clock for a terminal status, and is ignored otherwise.
            responded: ``True`` for a user action, ``False`` for the detection
                engine resolving a condition that is gone, ``None`` to record
                nothing.

        Returns:
            The updated row, or ``None`` when no row matched — a foreign or
            unknown id, and equally a row whose current status cannot reach
            ``status``. The second case is reported as a no-op rather than an
            error on purpose: the row's *current* status is data, and a
            detection run that tries to resolve a risk another run already
            resolved should find it settled, not raise. Only an unreachable
            *target* — one no status can ever transition into — is a
            programming error, because that is a statement about the code
            rather than about a row.

        Raises:
            ValueError: If ``status`` is not a known risk status, or is a known
                status that no risk status transitions into. The statuses arrive
                from the enum and from the three lifecycle routes, so either
                would otherwise be a silent no-op that looks like a successful
                write until someone reads the row.
        """
        target = validate_risk_status(status).value
        allowed_from = frozenset(
            source for source, targets in _RISK_TRANSITIONS.items() if target in targets
        )
        if not allowed_from:
            raise ValueError(
                f"Cannot move a risk to {target!r}; no risk status transitions into it."
            )

        writes: dict[str, Any] = {"status": target}
        if target in _TERMINAL_RISK_STATUSES:
            writes["resolved_at"] = _as_utc(resolved_at) or func.now()
        if responded is not None:
            writes["metadata_"] = _merge_metadata(
                Risk.metadata_, {"resolution": "user" if responded else "engine"}
            )

        statement = (
            update(Risk)
            .where(
                Risk.id == risk_id,
                Risk.user_id == owner_id,
                Risk.status.in_(sorted(allowed_from)),
            )
            .values(**writes)
            .returning(Risk)
        )
        # `populate_existing` for the reason given in `_upsert_indexed_risk`: a
        # route that read the risk before transitioning it would otherwise get
        # back the pre-transition instance from the identity map and answer
        # `GET /risks/{id}` with a status the row no longer has.
        result = await self.session.execute(statement.execution_options(populate_existing=True))
        row = result.scalar_one_or_none()
        await self.session.commit()
        return row

    async def list_stale_live_risks(
        self,
        owner_id: uuid.UUID,
        *,
        seen: Iterable[tuple[str, str | None, uuid.UUID | None]],
        limit: int = 200,
    ) -> list[Risk]:
        """Live risks whose condition was *not* re-detected on this run.

        The candidates for auto-resolution, and the reason the Risk Center does
        not only grow: a condition that has disappeared should stop being a live
        risk, and the only evidence of that is its absence from this run's
        output.

        The ``seen`` set is built into the statement rather than filtered in
        Python. A loop of per-identity lookups would be a round trip per
        detector, and the alternative of loading every live risk and comparing in
        Python would defeat the owner-scoped query this module is built on.

        ``seen`` holds ``(risk_type, entity_type, entity_id)`` triples, and the
        identity has to be negated with null-aware comparison. ``NOT IN`` over a
        list of row values silently mishandles nulls — a tuple containing a null
        never compares equal to anything, so account-level risks would be
        reported stale on every run even when they were just re-detected. So the
        identities are split: those with a concrete ``entity_id`` go into one
        ``(risk_type, entity_type, entity_id) NOT IN (...)`` predicate, which is
        compact and index-friendly, and each null-bearing identity becomes its
        own ``IS NOT DISTINCT FROM`` clause negated and OR-ed together. There
        are at most one such identity per risk type per run, so the predicate
        stays short.

        Args:
            owner_id: Whose risks to consider.
            seen: Identities detected on this run.
            limit: Cap on the number of rows returned, oldest first. A cap
                because the sweep is a repair pass — a run that somehow
                accumulated thousands of live risks should reconcile in batches
                rather than rewriting the whole table at once.

        Returns:
            Live risks whose identity is absent from ``seen``, oldest
            ``detected_at`` first so that a truncated sweep resolves the longest
            open conditions first.
        """
        concrete: list[tuple[str, str, uuid.UUID]] = []
        null_bearing: list[tuple[str, str | None, uuid.UUID | None]] = []
        for risk_type, entity_type, entity_id in seen:
            if entity_type is None or entity_id is None:
                null_bearing.append((risk_type, entity_type, entity_id))
            else:
                concrete.append((risk_type, entity_type, entity_id))

        filters: list[Any] = [Risk.user_id == owner_id, Risk.status.in_(_LIVE_STATUSES)]
        if concrete:
            filters.append(
                tuple_(Risk.risk_type, Risk.entity_type, Risk.entity_id).notin_(concrete)
            )
        if null_bearing:
            filters.append(
                ~or_(
                    *(
                        and_(
                            Risk.risk_type == risk_type,
                            Risk.entity_type.is_not_distinct_from(entity_type),
                            Risk.entity_id.is_not_distinct_from(entity_id),
                        )
                        for risk_type, entity_type, entity_id in null_bearing
                    )
                )
            )

        result = await self.session.execute(
            select(Risk)
            .where(*filters)
            .order_by(Risk.detected_at.asc(), Risk.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_by_severity(
        self,
        owner_id: uuid.UUID,
        *,
        statuses: Sequence[str] | None = None,
        risk_types: Sequence[str] | None = None,
        severities: Sequence[str] | None = None,
    ) -> dict[str, int]:
        """``{severity: count}`` for the Risk Center header, in one grouped query.

        Every member of :class:`~app.models.enums.RiskSeverity` is present even
        when it has no rows, so a caller can read ``counts["critical"]`` without a
        ``.get()`` default. This is not cosmetic: the header renders four fixed
        buckets, and a dictionary that omitted a bucket would raise there the
        first time a user had no low-severity risk at all — the response shape
        would depend on the user's data.

        There is deliberately no ``"total"`` key, unlike the task repository's
        status tally. The response schema carries ``total`` beside
        ``by_severity``, so a total inside the tally would be a second number
        that a client could sum alongside the first.

        The three filters are the ones :meth:`list_risks` takes, built by the same
        :func:`_risk_filters`, and they are all honoured here. A tally that
        described a *different* set from the rows on screen would be worse than
        no tally: the header would say there are four critical findings while the
        filtered list showed none, and nothing on the page would let a reader tell
        which of the two numbers to believe. Passing ``severities`` collapses the
        tally to one populated band, which is the honest answer to "what is behind
        this band" — and it is the answer the Risk Center tiles need in order to
        count a band across every page rather than the one it happens to hold.
        """
        filters = _risk_filters(
            owner_id, statuses=statuses, risk_types=risk_types, severities=severities
        )
        result = await self.session.execute(
            select(Risk.severity, func.count()).where(*filters).group_by(Risk.severity)
        )
        counts: dict[str, int] = {severity.value: 0 for severity in RiskSeverity}
        for severity_value, bucket in result.all():
            counts[str(severity_value)] = int(bucket)
        return counts

    # ------------------------------------------------------------------
    # Recommendations
    # ------------------------------------------------------------------

    async def list_recommendations(
        self,
        owner_id: uuid.UUID,
        *,
        statuses: Sequence[str] | None = None,
        types: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Recommendation], int]:
        """This owner's recommendations, most urgent first, and the total.

        Ordered by priority rank then ``created_at`` descending, with ``id`` as a
        tiebreaker for the same pagination reason as :meth:`list_risks`. The page
        and its total come from one statement, as there.

        Args:
            owner_id: Whose recommendations to list.
            statuses: Restrict to these :class:`~app.models.enums.RecommendationStatus`
                values; ``None`` means every status.
            types: Restrict to these
                :class:`~app.models.enums.RecommendationType` values.
            limit: Page size.
            offset: Rows to skip.

        Returns:
            The page of rows and the total number of rows the filters match.
        """
        filters: list[Any] = [Recommendation.user_id == owner_id]
        if statuses:
            filters.append(Recommendation.status.in_(list(statuses)))
        if types:
            filters.append(Recommendation.recommendation_type.in_(list(types)))

        statement = (
            select(Recommendation, func.count().over().label("total"))
            .where(*filters)
            .order_by(
                _priority_rank(Recommendation.priority).desc(),
                Recommendation.created_at.desc(),
                Recommendation.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        rows = list((await self.session.execute(statement)).all())
        total = int(rows[0].total) if rows else await self._count_recommendations(owner_id, filters)
        return [row[0] for row in rows], total

    async def _count_recommendations(self, owner_id: uuid.UUID, filters: Sequence[Any]) -> int:
        """Count the rows a filtered recommendation list would return.

        The empty-page counterpart of the window count, mirroring
        :meth:`_count_risks`: a window function only exists on a row that was
        returned, so a filter matching nothing has to be counted separately.
        """
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(Recommendation)
                .where(Recommendation.user_id == owner_id, *filters)
            )
        )

    async def get_recommendation(
        self, owner_id: uuid.UUID, recommendation_id: uuid.UUID
    ) -> Recommendation | None:
        """One recommendation, or ``None`` if it is not this owner's.

        The same rule as :meth:`get_risk`, for the same reason: a foreign id is
        a 404, not a 403, so the endpoint cannot be used to discover which
        recommendation ids exist.
        """
        result = await self.session.execute(
            select(Recommendation).where(
                Recommendation.id == recommendation_id,
                Recommendation.user_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def find_open_recommendation(
        self,
        owner_id: uuid.UUID,
        *,
        recommendation_type: str | RecommendationType,
        entity_type: str | None,
        entity_id: uuid.UUID | None,
    ) -> Recommendation | None:
        """The open recommendation for one identity, if there is one.

        The open set is ``new``/``viewed``. A rejected or completed suggestion
        is deliberately *not* found, so an identical suggestion can be raised
        again after the user has genuinely changed the situation — while a
        suggestion they have merely ignored or not yet opened is not re-raised
        on the next run, which is the nagging the partial index exists to
        prevent.

        Null-aware entity comparison, for the same reason as
        :meth:`find_live_risk`: a suggestion that is about the account rather
        than a row must still be found again by the rule that raised it.
        """
        result = await self.session.execute(
            select(Recommendation)
            .where(
                Recommendation.user_id == owner_id,
                Recommendation.recommendation_type
                == validate_recommendation_type(recommendation_type).value,
                Recommendation.entity_type.is_not_distinct_from(entity_type),
                Recommendation.entity_id.is_not_distinct_from(entity_id),
                Recommendation.status.in_(_OPEN_STATUSES),
            )
            .order_by(Recommendation.created_at.asc(), Recommendation.id.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def upsert_recommendation(
        self,
        owner_id: uuid.UUID,
        *,
        recommendation_type: str | RecommendationType,
        priority: str | RecommendationPriority,
        title: str,
        description: str,
        reason: str,
        risk_id: uuid.UUID | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> tuple[Recommendation, bool]:
        """Store one suggestion, creating it or refreshing the open one.

        The same contract as :meth:`upsert_risk`, against
        ``uq_recommendations_open_identity`` and with the same two paths: a
        row-level identity is arbitrated by a single ``INSERT ... ON CONFLICT``
        naming the partial index and its predicate, and a null-bearing identity —
        "you have more planned than fits in this week" — is arbitrated by the
        repository, because a btree unique index does not treat two nulls as
        equal and would let every run add another identical suggestion.

        On refresh, the wording and the reason are replaced rather than merged.
        The reason is the *numbers* behind the suggestion, and those change every
        run; keeping the first run's figures would leave a recommendation
        arguing for something the data no longer says.

        Unlike a risk, a refreshed recommendation keeps its ``created_at``: it
        has no ``resolved_at`` to be measured against, and "since when has this
        been suggested" is worth more to the UI than the ordering benefit of
        moving it.

        Args:
            owner_id: Whose recommendation this is.
            recommendation_type: What action is proposed.
            priority: How soon it wants an answer, derived from the raising
                risk's severity rather than chosen independently.
            title: WHAT the user is being asked to do.
            description: The suggested action, in the imperative.
            reason: WHY, in words, with the numbers. Not optional: a suggestion
                with no stated reason is not one this engine will store.
            risk_id: The risk that raised it, if any.
            entity_type: ``task`` / ``project`` / ``account``, or ``None``.
            entity_id: The row the action is about, or ``None``.
            metadata: Anything the rule chose to record about the derivation.

        Returns:
            ``(row, created)``, where ``created`` distinguishes a new suggestion
            from one refreshed by a later run.

        Raises:
            ValueError: If ``recommendation_type`` is unknown, or ``priority`` is
                outside the four bands the table's check constraint allows. Both
                are programming errors, and both would otherwise surface as an
                ``IntegrityError`` from storage with no mention of which value
                was wrong.
        """
        type_value = validate_recommendation_type(recommendation_type).value
        priority_value = RecommendationPriority(priority).value
        values: dict[str, Any] = {
            "priority": priority_value,
            "title": title,
            "description": description,
            "reason": reason,
            "risk_id": risk_id,
            "metadata": dict(metadata or {}),
        }

        if entity_type is not None and entity_id is not None:
            return await self._upsert_indexed_recommendation(
                owner_id,
                recommendation_type=type_value,
                entity_type=entity_type,
                entity_id=entity_id,
                values=values,
            )
        return await self._upsert_null_identity_recommendation(
            owner_id,
            recommendation_type=type_value,
            entity_type=entity_type,
            entity_id=entity_id,
            values=values,
        )

    async def _upsert_indexed_recommendation(
        self,
        owner_id: uuid.UUID,
        *,
        recommendation_type: str,
        entity_type: str,
        entity_id: uuid.UUID,
        values: Mapping[str, Any],
    ) -> tuple[Recommendation, bool]:
        """The index-arbitrated path for recommendations.

        Identical in shape to :meth:`_upsert_indexed_risk` and for identical
        reasons: one statement, the partial index's predicate supplied as
        ``index_where`` because PostgreSQL will not infer a partial index without
        it, ``xmax = 0`` to report which branch ran, ``updated_at`` written
        explicitly because a Core ``ON CONFLICT`` bypasses the model's
        ``onupdate``, and ``populate_existing`` so the returned row carries the
        wording this run just wrote rather than whatever the session last
        cached for that id.
        """
        statement = (
            pg_insert(Recommendation)
            .values(
                id=uuid.uuid4(),
                user_id=owner_id,
                recommendation_type=recommendation_type,
                entity_type=entity_type,
                entity_id=entity_id,
                **values,
            )
            .on_conflict_do_update(
                index_elements=[
                    Recommendation.user_id,
                    Recommendation.recommendation_type,
                    Recommendation.entity_type,
                    Recommendation.entity_id,
                ],
                index_where=literal_column(_RECOMMENDATIONS_OPEN_PREDICATE),
                set_={**values, "updated_at": func.now()},
            )
            .returning(Recommendation, literal_column("xmax = 0").label("was_inserted"))
        )
        row = (
            await self.session.execute(statement.execution_options(populate_existing=True))
        ).one()
        await self.session.commit()
        return row[0], bool(row[1])

    async def _upsert_null_identity_recommendation(
        self,
        owner_id: uuid.UUID,
        *,
        recommendation_type: str,
        entity_type: str | None,
        entity_id: uuid.UUID | None,
        values: Mapping[str, Any],
    ) -> tuple[Recommendation, bool]:
        """The application-arbitrated path for a null-bearing recommendation identity.

        The lock key is namespaced separately from the risk one so an account
        risk and an account recommendation cannot block each other, and
        otherwise the argument is :meth:`_upsert_null_identity_risk`'s: the index
        cannot arbitrate a null, so the repository must.
        """
        await self.session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(
                        _identity_lock_key(
                            owner_id,
                            recommendation_type,
                            entity_type,
                            entity_id,
                            namespace="recommendation",
                        ),
                        0,
                    )
                )
            )
        )
        existing = await self.find_open_recommendation(
            owner_id,
            recommendation_type=recommendation_type,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        if existing is not None:
            for attribute, value in _to_orm_attributes(values).items():
                setattr(existing, attribute, value)
            self.session.add(existing)
            await self.session.commit()
            await self.session.refresh(existing)
            return existing, False

        row = Recommendation(
            id=uuid.uuid4(),
            user_id=owner_id,
            recommendation_type=recommendation_type,
            entity_type=entity_type,
            entity_id=entity_id,
            **_to_orm_attributes(values),
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row, True

    async def transition_recommendation(
        self,
        owner_id: uuid.UUID,
        recommendation_id: uuid.UUID,
        *,
        status: str | RecommendationStatus,
        responded_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> Recommendation | None:
        """Move a recommendation along its lifecycle, or return ``None``.

        Owner-scoped single statement, as :meth:`transition_risk` is, and with
        the same 404-not-403 consequence for a foreign id.

        ``responded_at`` is stamped for any status that means the user acted —
        accepted, rejected or completed — and only defaults to the database clock
        when the caller does not supply an instant. A suggestion that was merely
        viewed or that the engine expired has not been answered, and the column
        is what makes "how many suggestions were never answered" answerable
        without a second query. ``expires_at`` is written only when the caller
        passes one: expiry means the raising risk went away, and that is a fact
        about a risk, not a fact about the recommendation.

        Args:
            owner_id: Whose recommendation this is.
            recommendation_id: The recommendation to move.
            status: The target status, validated against
                :class:`~app.models.enums.RecommendationStatus`.
            responded_at: When the user acted. Defaults to now for an answering
                status; ignored otherwise.
            expires_at: When the suggestion became moot.

        Returns:
            The updated row, or ``None`` when nothing matched — an unknown or
            foreign id, and equally a row whose current status cannot reach
            ``status``. See :meth:`transition_risk` for why the second case is a
            no-op rather than an error.

        Raises:
            ValueError: If ``status`` is not a known recommendation status, or is
                a known status that no other status transitions into.
        """
        target = validate_recommendation_status(status).value
        allowed_from = frozenset(
            source for source, targets in _RECOMMENDATION_TRANSITIONS.items() if target in targets
        )
        if not allowed_from:
            raise ValueError(
                f"Cannot move a recommendation to {target!r}; "
                "no recommendation status transitions into it."
            )

        writes: dict[str, Any] = {"status": target}
        if target in _ANSWERED_RECOMMENDATION_STATUSES:
            writes["responded_at"] = _as_utc(responded_at) or func.now()
        if expires_at is not None:
            writes["expires_at"] = _as_utc(expires_at)

        statement = (
            update(Recommendation)
            .where(
                Recommendation.id == recommendation_id,
                Recommendation.user_id == owner_id,
                Recommendation.status.in_(sorted(allowed_from)),
            )
            .values(**writes)
            .returning(Recommendation)
        )
        # See `transition_risk`: without `populate_existing` a session that
        # already holds this recommendation would hand back the pre-transition
        # instance.
        result = await self.session.execute(statement.execution_options(populate_existing=True))
        row = result.scalar_one_or_none()
        await self.session.commit()
        return row

    async def expire_recommendations_for_risks(
        self, owner_id: uuid.UUID, risk_ids: Sequence[uuid.UUID]
    ) -> int:
        """Expire the still-open suggestions raised by risks that are gone.

        A suggestion whose risk has been resolved or dismissed is not stale, it
        is *moot* — and recording that is more useful than deleting the row,
        because "declined because the problem went away" and "declined because
        they said no" are different training labels.

        The condition is checked in the statement rather than trusted from the
        caller's list: ``NOT EXISTS`` a live risk for the row's ``risk_id`` is
        the rule, and it also covers a risk that was deleted outright. The id list
        is still supplied so the statement is bounded to the risks the caller
        actually resolved, rather than scanning the owner's open suggestions.

        Returns:
            How many recommendations were made moot — zero when the list is empty
            or none of them were still open.
        """
        if not risk_ids:
            return 0
        live_risk = (
            select(Risk.id)
            .where(Risk.id == Recommendation.risk_id, Risk.status.in_(_LIVE_STATUSES))
            .exists()
        )
        statement = (
            update(Recommendation)
            .where(
                Recommendation.user_id == owner_id,
                Recommendation.risk_id.in_(list(risk_ids)),
                Recommendation.status.in_(_OPEN_STATUSES),
                ~live_risk,
            )
            .values(
                status=RecommendationStatus.EXPIRED.value,
                # An already-stamped expiry is kept: it says when the suggestion
                # first became moot, and a second sweep must not move it.
                expires_at=func.coalesce(Recommendation.expires_at, func.now()),
            )
            .returning(Recommendation.id)
        )
        result = await self.session.execute(statement)
        expired = result.scalars().all()
        await self.session.commit()
        return len(expired)

    async def count_by_priority(
        self,
        owner_id: uuid.UUID,
        *,
        statuses: Sequence[str] | None = None,
        types: Sequence[str] | None = None,
    ) -> dict[str, int]:
        """``{priority: count}`` for the recommendations header, one grouped query.

        The recommendation counterpart of :meth:`count_by_severity`, seeded with
        all four bands for the same reason — the header renders four fixed
        buckets — and for the same no-``total`` reason.

        ``types`` is honoured for the same reason ``risk_types`` is honoured
        there, and it was missing here long enough to be a bug rather than a
        choice: ``RecommendationListRead`` puts ``by_priority`` beside ``total``
        and the caller filters the list by ``recommendation_type``, so a tally
        that ignored the filter would no longer sum to the ``total`` next to it —
        the header would claim recommendations the filtered list had excluded.

        This method is not in the contract's method table. It is here because
        ``RecommendationListRead`` carries a ``by_priority`` field with no other
        source, and a repository that can answer the question in one statement
        should be the place the answer comes from.
        """
        filters: list[Any] = [Recommendation.user_id == owner_id]
        if statuses:
            filters.append(Recommendation.status.in_(list(statuses)))
        if types:
            filters.append(Recommendation.recommendation_type.in_(list(types)))
        result = await self.session.execute(
            select(Recommendation.priority, func.count())
            .where(*filters)
            .group_by(Recommendation.priority)
        )
        counts: dict[str, int] = {priority.value: 0 for priority in RecommendationPriority}
        for priority_value, bucket in result.all():
            counts[str(priority_value)] = int(bucket)
        return counts

    # ------------------------------------------------------------------
    # Evaluation runs
    # ------------------------------------------------------------------

    async def record_evaluation(
        self,
        owner_id: uuid.UUID,
        *,
        window_start: datetime,
        window_end: datetime,
        risks_found: int = 0,
        risks_created: int = 0,
        risks_updated: int = 0,
        risks_resolved: int = 0,
        by_severity: Mapping[str, int] | None = None,
        by_type: Mapping[str, int] | None = None,
        recommendations_created: int = 0,
        duration_ms: int = 0,
    ) -> RiskEvaluation:
        """Write the one summary row for a detection run.

        Exactly one row per run, and that is the whole design: a per-risk-per-run
        record would grow this table by the number of risks the user has, which
        is the multiplication the brief warns against. The counts are a snapshot
        of a moment; the risks themselves are the durable record.

        ``run_token`` is generated here rather than left to the model default so
        that a caller retrying this call produces a *new* run rather than
        colliding with the unique constraint ``uq_risk_evaluations_run`` — the
        constraint exists to stop one run becoming two summaries, and a retried
        insert of the same run is exactly the thing it should catch.

        Args:
            owner_id: Whose run this is.
            window_start: Start of the window the pass reasoned over.
            window_end: End of that window. Stored with the row so a snapshot is
                interpretable without reconstructing which range produced it.
            risks_found: Surviving detections, created plus updated.
            risks_created: How many of them were new rows.
            risks_updated: How many refreshed an existing live risk.
            risks_resolved: How many live risks this run closed.
            by_severity: ``{severity: count}`` for the run.
            by_type: ``{risk_type: count}`` for the run.
            recommendations_created: How many suggestions the pass produced.
            duration_ms: How long the pass took.

        Returns:
            The stored row, refreshed so ``evaluated_at`` carries the database's
            answer rather than being unset locally.
        """
        evaluation = RiskEvaluation(
            id=uuid.uuid4(),
            user_id=owner_id,
            run_token=uuid.uuid4(),
            window_start=_as_utc(window_start),
            window_end=_as_utc(window_end),
            risks_found=int(risks_found),
            risks_created=int(risks_created),
            risks_updated=int(risks_updated),
            risks_resolved=int(risks_resolved),
            by_severity=dict(by_severity or {}),
            by_type=dict(by_type or {}),
            recommendations_created=int(recommendations_created),
            duration_ms=int(duration_ms),
        )
        self.session.add(evaluation)
        await self.session.commit()
        # `evaluated_at` is a server default, so re-read rather than hand back a
        # row whose timestamp is still unset.
        await self.session.refresh(evaluation)
        return evaluation

    async def list_evaluations(
        self, owner_id: uuid.UUID, *, limit: int = 20
    ) -> list[RiskEvaluation]:
        """This owner's recent evaluation runs, newest first.

        The one read ``risk_evaluations`` has, and the one the intelligence
        screen renders. ``id`` breaks ties between runs stamped in the same
        instant, so paging through a busy day is stable.
        """
        result = await self.session.execute(
            select(RiskEvaluation)
            .where(RiskEvaluation.user_id == owner_id)
            .order_by(RiskEvaluation.evaluated_at.desc(), RiskEvaluation.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


def _identity_lock_key(
    owner_id: uuid.UUID,
    kind: str,
    entity_type: str | None,
    entity_id: uuid.UUID | None,
    *,
    namespace: str = "risk",
) -> str:
    """Build the advisory-lock key for one dedup identity.

    A string rather than a hash so the key is inspectable in ``pg_locks`` when a
    detection run is holding something up, and the nulls are spelled rather than
    omitted so that an account-level identity and a row-level one can never
    produce the same key.
    """
    return f"{namespace}:{owner_id}:{kind}:{entity_type}:{entity_id}"


def _merge_metadata(column: Any, patch: Mapping[str, Any]) -> Any:
    """A ``jsonb`` merge expression for a partial column write.

    ``UPDATE ... SET metadata = '{...}'`` would discard whatever the detection
    run recorded, so the patch is concatenated onto the existing value in SQL
    rather than replaced from a value read earlier — the write stays a single
    statement, and a key the patch sets wins over the stored one.
    """
    encoded = cast(literal(json.dumps(dict(patch), sort_keys=True)), JSONB)
    return func.coalesce(column, cast(literal("{}"), JSONB)).op("||")(encoded)
