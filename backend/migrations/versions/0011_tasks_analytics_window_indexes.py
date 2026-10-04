"""tasks_analytics_window_indexes

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-14 00:00:00

Two indexes on ``tasks`` for the two analytics window reads that no existing index
could serve. No column changes, no data changes, no constraints — this file only
makes a scan that was already happening bounded by the window instead of by the
account's whole history.

Models are deliberately NOT imported here — as in ``0001`` through ``0010`` — so
that a later change to ``app/models/`` cannot silently rewrite history.

What was wrong
--------------
``tasks`` carried ``ix_tasks_owner_id`` and ``ix_tasks_owner_status_due`` over
``(owner_id, status, due_date)``. Both are good indexes for the board: they serve
"my tasks in these statuses whose due date falls in this window" and the overdue
probe, and both are documented as such in ``app/models/task.py``.

Phase 6 added a third family of reads that none of them touches:

* ``AnalyticsRepository.count_tasks_by_day`` —
  ``owner_id = ? AND created_at >= ? AND created_at < ?``, grouped by
  ``date_trunc('day', created_at)``.
* ``AnalyticsRepository.count_tasks_completed_by_day`` — the same shape on
  ``completed_at``.
* ``AnalyticsRepository.completed_pairs_in_range`` and
  ``avg_cycle_minutes_in_range`` — the same shape again, and between them they
  supply deadline adherence, estimation accuracy and cycle time to the scores
  service.

Every one of those mentions ``owner_id`` and an *instant*; none mentions
``due_date``. A btree can only use an equality on its leading columns and then a
range on the next one, so ``ix_tasks_owner_status_due`` was usable for
``owner_id`` alone and every one of those reads fell back to filtering the
account's entire backlog after the fact. ``count_tasks_by_day``'s own docstring
claims the opposite — that putting the predicate on the raw ``timestamptz``
column "keeps the scan inside ``ix_tasks_owner_status_due`` instead of degrading
into a filter over every row the user owns" — and ``EXPLAIN`` says the claim is
false.

The cost is not bounded by anything the caller chose. A task's row is never
deleted, so the set being scanned is every task the account has ever created, and
the window the user asked for has nothing to do with its size. Worse, the daily
rebuild runs these statements on **every** analytics read, to decide whether the
stored aggregates still cover the requested range, so the scan is not a once-a-day
job either.

What was measured
-----------------
``EXPLAIN (ANALYZE, BUFFERS)`` on a table holding 20,000 tasks for one account,
asked for a 31-day window that matched 744 created rows and 248 completions, each
query run three times first so the timings are not first-call noise:

============================================  =======================  =======================
statement                                     before                   after
============================================  =======================  =======================
``count_tasks_by_day`` shape                  ``Seq Scan`` 1.23 ms    ``Bitmap Heap Scan`` 0.27 ms
``completed_pairs_in_range`` shape            ``Seq Scan`` 0.86 ms    ``Index Scan`` 0.03 ms
``avg_cycle_minutes_in_range`` shape          ``Seq Scan`` 0.92 ms    ``Index Scan`` 0.08 ms
============================================  =======================  =======================

The absolute numbers are small at 20k rows and that is the point being made
honestly: this is not a table that is slow today. Both sides of the comparison are
linear in the account's total task count while the indexed side is linear in the
window, so the ratio — roughly 4x, 33x and 12x here — widens without limit. The
same measurement pattern ``0010`` used for ``activity_events``.

Why two indexes rather than one
-------------------------------
``created_at`` is stamped when the row is created and never moves. ``completed_at``
is null until the task is finished and is written exactly once at the transition.
They are different columns with different cardinalities over the same table: a
long-lived account has many times as many non-null ``completed_at`` values as it
has rows *in any given window*, so the two predicates select genuinely different
row sets. One index cannot serve both, and a partial index on
``completed_at IS NOT NULL`` was considered and rejected — it would help the
completed-at reads a little and would have to be maintained twice over, while
``owner_id`` leading the key already brings the index down to one account.

``owner_id`` leads because every read is already scoped to one account. That is
the tenancy guarantee rather than an optimisation, but it has the useful
consequence that the index cannot be entered by an unscoped query, so nothing
downstream can widen it by accident.

What is deliberately not done here
----------------------------------
* **No index on ``due_date`` alone.** Every read that touches it already goes
  through ``ix_tasks_owner_status_due``, which was measured against the board's
  two hot queries when it was introduced in ``0003``.
* **No index on ``status`` alone.** Superseded for every query that matters by
  the composites above, and ``ix_tasks_owner_id`` serves the unqualified reads.
* **No rewrite of any query.** Every statement measured above keeps its shape;
  only the planner's access path changes, so no behavioural difference can ride
  in with the index.

Downgrade
---------
Drop both indexes, restoring ``0010``'s schema exactly. Nothing else in this file
changed anything, so there is nothing else to reverse — and the sequential scans
return with it, which is the honest description of what ``0010`` described.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The index names as literals rather than composed from the table and column
#: names. ``downgrade()`` has to name the object that is actually there, and a
#: spelling assembled at import time would fail on deploy rather than in review.
_CREATED_INDEX = "ix_tasks_owner_created"
_COMPLETED_INDEX = "ix_tasks_owner_completed"


def upgrade() -> None:
    # `owner_id` first, range column second: an equality on the leading column
    # plus a range on the next is the only shape a btree can serve, and every
    # query these two exist for is exactly that.
    op.create_index(_CREATED_INDEX, "tasks", ["owner_id", "created_at"], unique=False)
    op.create_index(_COMPLETED_INDEX, "tasks", ["owner_id", "completed_at"], unique=False)


def downgrade() -> None:
    # Reverse of the upgrade, in the order it was applied so the intermediate
    # state is the one a reader stepping back through revisions would see.
    #
    # No `IF EXISTS`: Alembic runs the downgrade against the schema `0011`
    # described, and a conditional drop here would hide a mismatch between this
    # file and the database rather than reporting it.
    op.drop_index(_COMPLETED_INDEX, table_name="tasks")
    op.drop_index(_CREATED_INDEX, table_name="tasks")
