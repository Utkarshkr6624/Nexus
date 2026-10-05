"""Data access for :class:`~app.models.activity.ActivityLog`.

The repository owns SQL only. It never raises domain errors.

Activity rows are append-only, so there is no ``update_fields`` here by design:
the only way a row's contents can change is if the repository offers a way to
change them, and it should not.

Two shapes of read, and the difference matters. :meth:`ActivityRepository.list_for_user`
is the user's own timeline and is scoped by ``user_id`` in the query, so knowing
an event id buys nothing. :meth:`ActivityRepository.recent_for_project` and
:meth:`ActivityRepository.recent_for_task` are reached from inside a project or a
task that the caller has *already* been authorised for — the service resolved and
checked ownership of the parent before calling — so they take no ``user_id`` and
adding one would mean the same authorisation twice. That asymmetry is the
reason to keep both: the first is a privacy boundary, the second is not.

.. warning::
   ``metadata`` must never receive a password, a token, or a hash of either. The
   value is written verbatim into JSONB and is retained long after the session it
   describes. Nothing is filtered on the way in, because a redaction list would
   eventually miss the one field that matters; sanitising is the caller's job,
   at the point where the value is still known to be safe.

.. note::
   :meth:`record` commits on the caller's request-scoped session, which every
   other repository in the request also holds. A failed commit therefore leaves
   that session needing a ``rollback()`` before anyone else can use it.
   Restoring it is not this layer's job — a history entry must never dictate what
   happens to shared state — so it belongs to the service, which owns the
   best-effort contract.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import ActivityLog

__all__ = ["ActivityRepository"]


class ActivityRepository:
    """Activity history persistence bound to a single request-scoped session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        user_id: uuid.UUID | None,
        event_type: str,
        project_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ActivityLog:
        """Append one event to the history.

        ``user_id`` is nullable, and for a different reason than in
        :meth:`app.repositories.audit.AuditRepository.record`: an event recorded
        by a background job or a sweep has no signed-in principal behind it.
        Leaving the column null says exactly that, where inventing one would put
        someone else's name on work they did not do.

        ``metadata`` is copied with ``dict()`` so a caller mutating their mapping
        afterwards cannot change a row that is already committed, and defaults to
        ``{}`` — which is also the column's server default — so an event with
        nothing to say does not write NULL into a NOT NULL column.
        """
        entry = ActivityLog(
            user_id=user_id,
            event_type=event_type,
            project_id=project_id,
            task_id=task_id,
            metadata_=dict(metadata) if metadata else {},
        )
        self.session.add(entry)
        await self.session.commit()
        # created_at is a server default; re-read so the caller can sort on it
        # without a second query.
        await self.session.refresh(entry)
        return entry

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        project_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        event_type: str | None = None,
    ) -> tuple[list[ActivityLog], int]:
        """List one user's history, newest first, with the unpaginated total.

        ``user_id`` is in the ``WHERE`` clause, not applied afterwards: a row the
        caller may not see is never loaded. An event whose ``user_id`` was blanked
        by ``ON DELETE SET NULL`` is deliberately absent from here — the account
        it belonged to is gone, and a timeline that outlived its owner belongs to
        the project's history, not to a user page.

        ``total`` comes from a ``COUNT`` over the same filters as the page, so it
        is the size of the result set rather than the size of the slice.

        **A ``task_id`` filter also matches the rows whose task no longer
        exists.** ``task_deleted`` cannot carry the column — its foreign key is
        ``ON DELETE SET NULL`` and the row it would point at has just been
        deleted — so the id travels in ``metadata`` and the predicate reads both
        places. Without the second half, the documented ``?task_id=`` filter
        answered ``total: 0`` for the one event that is about that task.

        Newest first is the product decision: an activity feed is read from the
        top, and a viewer has no field that could tell them which end is newer.
        """
        filters = [ActivityLog.user_id == user_id]
        if project_id is not None:
            filters.append(ActivityLog.project_id == project_id)
        if task_id is not None:
            # ``OR`` with the ``metadata`` copy, not just the column. A
            # ``task_deleted`` row carries a null ``task_id`` on purpose — the
            # foreign key is ``ON DELETE SET NULL`` and the task it named is
            # gone — and keeps the id in ``metadata`` instead, so the documented
            # ``?task_id=`` filter found nothing at all for the one event type
            # whose whole purpose is to answer "what happened to that card?".
            filters.append(
                or_(
                    ActivityLog.task_id == task_id,
                    ActivityLog.metadata_["task_id"].astext == str(task_id),
                )
            )
        if event_type is not None:
            filters.append(ActivityLog.event_type == event_type)

        page = (
            select(ActivityLog)
            .where(*filters)
            .order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(page)
        rows = list(result.scalars().all())

        total = int(
            await self.session.scalar(select(func.count()).select_from(ActivityLog).where(*filters))
        )
        return rows, total

    async def count_for_user(self, user_id: uuid.UUID) -> int:
        """Count the user's history rows.

        Counted in SQL so a badge showing "N events" costs the same whether the
        user has ten rows or ten thousand.
        """
        result = await self.session.execute(
            select(func.count()).select_from(ActivityLog).where(ActivityLog.user_id == user_id)
        )
        return int(result.scalar_one())

    async def recent_for_project(
        self, project_id: uuid.UUID, *, limit: int = 20
    ) -> list[ActivityLog]:
        """Return the newest events inside one project.

        Not scoped by owner, and deliberately so: the caller reached this from a
        project whose ownership it has already checked, and this history is
        exactly what that project is showing. Re-asserting the user's id here
        would mean the same authorisation twice, and would break the legitimate
        case where a project's events outlived the account that recorded them.
        """
        result = await self.session.execute(
            select(ActivityLog)
            .where(ActivityLog.project_id == project_id)
            .order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def recent_for_task(self, task_id: uuid.UUID, *, limit: int = 20) -> list[ActivityLog]:
        """Return the newest events for one task. See :meth:`recent_for_project`."""
        result = await self.session.execute(
            select(ActivityLog)
            .where(ActivityLog.task_id == task_id)
            .order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
