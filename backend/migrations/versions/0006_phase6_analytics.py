"""phase6_analytics

Revision ID: 0006
Revises: 0005
Create Date: 2026-02-22 00:00:00

Explicit DDL for the Phase 6 data engine: ``daily_metrics``, the daily
aggregate tier every analytics read is served from.

Models are deliberately NOT imported here — as in ``0001`` through ``0005`` — so
that a later change to ``app/models/`` cannot silently rewrite history.

Why this migration creates ONE table
-------------------------------------
The Phase 6 brief lists ``daily_metrics``, ``weekly_metrics``, ``monthly_metrics``,
``project_metrics``, ``task_metrics`` and ``productivity_snapshots`` as
*possible* entities and then says, explicitly: **do not create redundant tables
for every possible metric**. This migration is where that decision is recorded,
because a reader of the schema a year from now would otherwise have no way to
tell a deliberate omission from an unfinished phase. So, to restate it in the
place it belongs:

* **No ``weekly_metrics`` / ``monthly_metrics``.** A week's totals are the sum of
  the seven ``daily_metrics`` rows it contains. Storing the sum as well would
  create a second answer to "how much did I finish last week" that can disagree
  with the first the moment one of the days is rebuilt — and rebuilding is a
  normal operation here, not an exceptional one. The service buckets the daily
  rows in Python, which for a 366-day maximum range is a single pass over at
  most 366 rows and still answers "per week" in one query.

* **No ``project_metrics`` / ``task_metrics``.** Neither has a daily shape to
  pre-aggregate: the interesting axis *is* the project or the task, and this
  table has no column for it. A per-project row per day would be
  (projects x days) rows holding numbers derivable by one ``GROUP BY`` over
  ``tasks`` and ``work_sessions``, which is the query the service actually runs.

* **No ``productivity_snapshots``.** One row per task per day is precisely the
  "thousands of redundant snapshots" the brief warns against, and Phase 10 does
  not need one: ``AnalyticsService.feature_snapshot`` extracts the training
  features on demand from the live tables, so the dataset can be built
  deterministically and re-built whenever the schema changes, with no stored
  copy to keep in step.

What the one table buys is that a dashboard read is one indexed probe into a
table bounded by (days x users) instead of a re-scan of ``activity_events``,
``work_sessions``, ``tasks`` and ``calendar_events`` on every render.

``metric_date`` is a calendar day
--------------------------------
A bare ``Date``. This migration is the one that *created* it, cut at UTC midnight,
because every stored instant above it was timezone-aware UTC (see
``app/models/planner.py``) and a fixed cut needs no tzdata lookup in the
database.

It is **no longer cut at UTC midnight**. The column still carries no zone, but the
bucket is now the database server's local day —
``date(col AT TIME ZONE current_setting('TimeZone'))``, with window bounds at the
matching ``local_midnight``. Nothing about the schema changed: a bare ``Date`` was
always able to hold it, and the historical rows are re-read under the current rule
by the next rebuild rather than reinterpreted in place. ``app/models/analytics.py``
and ``app/repositories/analytics.py`` document the rule as it now stands.

The planner's local-day views stay local because they take an explicit ``tz``, and
an analytics day deliberately does not — a day boundary that moved with a query
parameter could not be compared against the previous period it is being compared
to.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_date", sa.Date(), nullable=False),
        # Every counter is NOT NULL with a zero server default, so a day with no
        # activity is a row of measured zeroes rather than a missing row. The
        # distinction matters: "nothing happened Tuesday" and "Tuesday has not
        # been aggregated" are different states, and a chart that renders them
        # identically claims a completeness it does not have.
        sa.Column("tasks_created", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tasks_completed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tasks_overdue", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tasks_cancelled", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tasks_blocked", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tasks_rescheduled", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("planned_minutes", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("actual_minutes", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("work_sessions", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("calendar_events", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("knowledge_events", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("projects_touched", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Load-bearing rather than incidental: this is how the API answers "is
        # this number stale?", which the brief requires ("do not silently show
        # stale numbers without indication").
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        # THE IDEMPOTENCY ANCHOR. Recomputing a day replaces its row; it never
        # adds a second one. That is what lets `rebuild_range` be re-run from a
        # worker that may retry, or by hand over the same range, without
        # double-counting the month.
        sa.UniqueConstraint("user_id", "metric_date", name="uq_daily_metrics_owner_date"),
        sa.PrimaryKeyConstraint("id"),
    )
    # The unique constraint above already builds a btree led by `user_id` and
    # would serve every read this table has on its own. This narrower index is
    # what PostgreSQL picks for the plain "all my metrics" probe — the same
    # reasoning as `ix_availability_rules_owner_id`. It costs one index entry per
    # row on a table bounded by (days x users), not by the amount of work the
    # user has done.
    op.create_index(
        op.f("ix_daily_metrics_user_id"),
        "daily_metrics",
        ["user_id"],
        unique=False,
    )
    # No index on `metric_date` alone, and no composite other than the unique
    # one: every read is `user_id = ? AND metric_date BETWEEN ...`, which the
    # unique constraint's leading `user_id` plus its range on `metric_date`
    # already serves, with rows arriving in date order — the order the chart
    # renders in anyway.


def downgrade() -> None:
    # One table, no dependents: nothing points at `daily_metrics`, and its own
    # indexes and constraints go with it.
    op.drop_table("daily_metrics")
