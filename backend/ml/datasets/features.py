"""The four ``*_features.v1`` contracts, restated as training rows.

Nexus extracts feature vectors in four places and stamps each with a closed
contract version: ``developer_features.v1`` (11 columns),
``learning_features.v1`` (8), ``career_features.v1`` (6) and
``analytics_features.v1`` (15). Phase 10 trains on all four, so this module is
the one place that knows what those column names mean and — the part that
actually matters — *which of them are allowed to be null*.

Why the parallel ``available`` mask is mandatory, not tidiness
-------------------------------------------------------------
The backend's rule, stated in Phase 8 and Phase 9 and repeated verbatim in
``app/schemas/developer.py``, ``app/schemas/learning.py`` and
``app/schemas/career.py``:

    **A figure that could not be computed is ``None``, never ``0``.**

The worked examples are all one shape. ``developer_features.v1`` types
``repository_age_days`` and ``inactivity_days`` as ``int | None`` because for a
repository with no commits, ``0`` would assert *committed today*. ``career_
features.v1`` types ``project_activity`` as ``float | None`` because ``0``
there would claim a repository exists and carries no commits when the truth is
that nobody has looked. ``analytics_features.v1`` is explicit about the training
consequence in :meth:`app.services.analytics.service.AnalyticsService.feature_
snapshot`: *a fabricated zero is indistinguishable from a real observation once
it is in a feature matrix*, so the key is present and its value is ``None``,
"which an imputer can handle deliberately rather than by accident".

That sentence is why a bare ``dict[str, float]`` cannot carry these rows. On an
API a ``None`` renders as "not enough data yet", and a human reads it. Inside a
training matrix there is nobody reading it: a column of numbers has no room for
a ``None`` that says *why*, and the moment a loader coerces the column to a
float it has invented a measurement for every absent figure at once. A
**never-scanned repository must not become a row of zeros**, because after that
coercion the model is being taught that an absence of observation and an
observation of absence are the same event — and it will reproduce the
fabrication, confidently, in a prediction.

So the mask is not a convenience next to the values. It is the only place the
distinction survives, and :func:`build_feature_row` derives it rather than
trusting the caller to have thought about it:

* a **nullable** column whose value is ``None`` is *unavailable* — we looked and
  there was nothing to look at;
* the **same column** carrying ``0`` is *available* — the arithmetic came out
  at zero, which is a real and interesting observation;
* a **non-nullable** column is never ``None`` at all. Those are the counts of
  things that exist or do not: ``commits_last_7d``, ``repositories``,
  ``projects_completed``. Zero there means "none were found", which is always a
  computable answer, so a null would be a claim that the counter never ran.

The row is rectangular either way — every contract column appears in both
``values`` and ``available`` — because a matrix with ragged rows is a bug the
loader has to guess its way out of, and a guess about nulls is the exact guess
that hides the defect.

Three more decisions worth naming
---------------------------------
**Column order is contract order, never a dict's.** :data:`FEATURE_COLUMNS`
holds an explicit tuple per version, because a positional feature matrix is
meaningless without it: the eleventh column of ``developer_features.v1`` is
``project_association`` forever, and a schema version bump is how that gets to
change.

**An unknown schema version is a hard failure.** Guessing at a layout is how a
v2 gets fitted as a v1, and a model that is silently wrong about its own
columns produces accuracy numbers nobody can act on.

**Nullability is declared, not inferred.** :data:`NULLABLE_COLUMNS` is a
literal per contract rather than something worked out from types at runtime,
because this package is stdlib-only and cannot import the Pydantic models it
would otherwise introspect. The tables are checked against each other at import
so the two can never drift apart silently — a nullable name that is not a
column is a typo, and a typo here would quietly downgrade a guarded column to
an unguarded one.

This module builds rows. It does not generate them from live data, fetch
anything, or touch a credential: :func:`build_nexus_dataset` belongs to a
sibling that owns the inventory walk, and ``~/.kaggle/access_token`` is not an
input to anything here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ml.datasets.schema import DataValidationError, FeatureRow, Provenance

__all__ = [
    "FEATURE_COLUMNS",
    "NULLABLE_COLUMNS",
    "build_feature_row",
    "describe_feature_contracts",
]

#: Columns of each ``*_features.v1`` contract, in the order the extractor emits
#: them. These tuples *are* the positional layout of the feature matrix, so they
#: are append-only within a version: reordering is a new contract with a new
#: version string, exactly as the backend schemas require.
FEATURE_COLUMNS: dict[str, tuple[str, ...]] = {
    "developer_features.v1": (
        "commits_last_7d",
        "commits_last_30d",
        "active_days_7d",
        "active_days_30d",
        "files_changed_7d",
        "additions_7d",
        "deletions_7d",
        "repository_age_days",
        "inactivity_days",
        "commit_frequency",
        "project_association",
    ),
    "learning_features.v1": (
        "sessions_last_7d",
        "sessions_last_30d",
        "learning_minutes",
        "goal_progress",
        "goal_deadline_distance_days",
        "completion_rate",
        "learning_consistency",
        "skill_activity_frequency",
    ),
    "career_features.v1": (
        "projects_completed",
        "project_activity",
        "repositories",
        "relevant_skill_evidence",
        "learning_activity",
        "portfolio_evidence_count",
    ),
    "analytics_features.v1": (
        "priority",
        "task_age_days",
        "estimated_minutes",
        "actual_minutes",
        "deadline_distance_days",
        "reschedule_count",
        "project_open_task_count",
        "historical_completion_rate",
        "recent_work_minutes",
        "work_session_count",
        "time_of_day",
        "day_of_week",
        "project_velocity",
        "overdue_count",
        "project_overdue_task_count",
    ),
}

#: Which columns each contract may legitimately leave ``None``.
#:
#: Everything absent from these sets is a count of something that exists or does
#: not, so zero is always a computable answer for it and a null would be a
#: claim that the counter never ran.
_NULLABLE_COLUMNS: dict[str, frozenset[str]] = {
    "developer_features.v1": frozenset({"repository_age_days", "inactivity_days"}),
    "learning_features.v1": frozenset(
        {
            "learning_minutes",
            "goal_progress",
            "goal_deadline_distance_days",
            "completion_rate",
            "learning_consistency",
            "skill_activity_frequency",
        }
    ),
    "career_features.v1": frozenset({"project_activity"}),
    "analytics_features.v1": frozenset(
        {
            "actual_minutes",
            "deadline_distance_days",
            "recent_work_minutes",
            "work_session_count",
            "time_of_day",
            "day_of_week",
            "project_velocity",
        }
    ),
}

#: One line per column, taken from the backend schema that declares it, so the
#: meaning travels with the training set instead of living only in the file
#: nobody reopens. Kept private because :func:`describe_feature_contracts` is
#: how a caller is meant to read it.
_COLUMN_MEANINGS: dict[str, dict[str, str]] = {
    "developer_features.v1": {
        "commits_last_7d": "Commits recorded in the last 7 days.",
        "commits_last_30d": "Commits recorded in the last 30 days.",
        "active_days_7d": "Distinct UTC dates in the last 7 days carrying at least one commit.",
        "active_days_30d": "Distinct UTC dates in the last 30 days carrying at least one commit.",
        "files_changed_7d": "Files touched by the commits in the last 7 days.",
        "additions_7d": "Lines added by the commits in the last 7 days.",
        "deletions_7d": "Lines deleted by the commits in the last 7 days.",
        "repository_age_days": (
            "Days from the earliest recorded commit to now. Null for a subject with "
            "no commits: 0 would claim it was created today."
        ),
        "inactivity_days": (
            "Days since the most recent recorded commit. Null for a subject with no "
            "commits, for the same reason."
        ),
        "commit_frequency": (
            "Recorded commits per day across the window. A rate of events, never a "
            "rate of work, and never a statement about a person."
        ),
        "project_association": (
            "True when the repository is linked to a project, or for an account-level "
            "row when at least one of its repositories is."
        ),
    },
    "learning_features.v1": {
        "sessions_last_7d": "Activities recorded in the last 7 days.",
        "sessions_last_30d": "Activities recorded in the last 30 days.",
        "learning_minutes": (
            "Minutes summed from activities in the window that carried a duration, or "
            "null when none did."
        ),
        "goal_progress": (
            "Mean progress across the account's live goals, 0-100, or null when there "
            "are none. The user's own asserted progress, never a derived competence "
            "score."
        ),
        "goal_deadline_distance_days": (
            "Signed mean days from now to the deadlines the account's open, dated "
            "goals carry, negative when the average is already past. Null when no goal "
            "carries a date, which is not the same as a deadline today."
        ),
        "completion_rate": (
            "Completed goals as a fraction of goals that reached a terminal state, or "
            "null over an empty denominator rather than 0.0, which would read as 'you "
            "complete nothing'."
        ),
        "learning_consistency": (
            "Distinct days carrying a recorded activity as a fraction of the window, or "
            "null when nothing was recorded. A rate of events per day, never a measure "
            "of a habit."
        ),
        "skill_activity_frequency": (
            "Activities per tracked skill per week across the window, or null when no "
            "skills are tracked. A rate over an empty set, not a statement about how "
            "fast anyone learns."
        ),
    },
    "career_features.v1": {
        "projects_completed": (
            "Projects that reached `completed`, read from the project status column "
            "rather than inferred from the evidence table."
        ),
        "project_activity": (
            "Recorded commits per completed project across the window, or null when no "
            "repository has ever been scanned. Null rather than 0: 0 would claim a "
            "repository exists and carries no commits when nobody has looked."
        ),
        "repositories": (
            "Repositories registered for this account. Zero is a real count: nobody "
            "registered one, which differs from 'registered but never read'."
        ),
        "relevant_skill_evidence": (
            "Career evidence rows pointing at a tracked skill, across every match. A "
            "count of claims the user made about their own skills."
        ),
        "learning_activity": (
            "Learning activities recorded across the account, read from the same rows "
            "the learning page counts so the two cannot disagree."
        ),
        "portfolio_evidence_count": (
            "Evidence rows the user entered by hand plus the profile's own links: the "
            "provenance figure, how much of the career page was written by the person "
            "rather than derived by the system."
        ),
    },
    "analytics_features.v1": {
        "priority": "Ordinal rank the extractor resolved the task's stored priority to.",
        "task_age_days": "Days from creation to today, floored at zero.",
        "estimated_minutes": "The task's own estimate in minutes, as recorded.",
        "actual_minutes": (
            "Recorded actual minutes, or null when no work session ever ran against "
            "the task. The stored column is NOT NULL with a zero default, so without a "
            "session behind it the figure was never observed."
        ),
        "deadline_distance_days": (
            "Days from today to the due date, negative when past. Null when the task "
            "has no deadline, which is not the same as one due today."
        ),
        "reschedule_count": (
            "Recorded `task_rescheduled` activity events for this task, scoped by owner "
            "as well as by task."
        ),
        "project_open_task_count": "Open tasks in the project this task belongs to.",
        "historical_completion_rate": (
            "Fraction of the project's tasks that are completed, or null over an empty denominator."
        ),
        "recent_work_minutes": (
            "Minutes summed from this task's work sessions, or null when there are none."
        ),
        "work_session_count": "Work sessions recorded against this task.",
        "time_of_day": (
            "UTC hour of the first recorded work session on this task, or null when there is none."
        ),
        "day_of_week": (
            "Weekday of the deadline, or null when the task has no deadline. Never the "
            "creation weekday: beside a null deadline distance, a fabricated deadline "
            "weekday claims a deadline the task does not have."
        ),
        "project_velocity": (
            "Tasks completed in this project in the last 30 days, or null when the task "
            "belongs to no project."
        ),
        "overdue_count": (
            "Days past the due date while the task is still open. A genuine 0 when the "
            "task is not overdue or has already been completed."
        ),
        "project_overdue_task_count": (
            "Open tasks already past their due date in this task's project: the "
            "project-level backlog pressure the per-task figures alone do not carry."
        ),
    },
}


def _validated_nullables() -> dict[str, frozenset[str]]:
    """Check the two tables agree, then hand back the nullable column sets.

    A nullable name that is not a column is a typo, and the direction it fails
    in matters: it silently un-guards a column that the contract says may be
    null, turning a legal null into a validation failure nobody would expect.
    The reverse — a column nothing has declared nullable — is the safe
    direction, so it is not enforced here, only reported through the contract
    description a caller can read.

    Returns:
        The nullable columns per contract version.

    Raises:
        RuntimeError: A declared nullable column is not in ``FEATURE_COLUMNS``.
    """
    for version, declared in _NULLABLE_COLUMNS.items():
        unknown = sorted(declared - set(FEATURE_COLUMNS[version]))
        if unknown:
            raise RuntimeError(
                f"{version}: nullable columns that are not contract columns: {unknown}"
            )
    return dict(_NULLABLE_COLUMNS)


#: The validated form of ``_NULLABLE_COLUMNS``. Every name here is a column of
#: the contract it is filed under, so a null in these positions is an answer and
#: a null anywhere else is a bug in the caller.
NULLABLE_COLUMNS: dict[str, frozenset[str]] = _validated_nullables()


def _resolve(
    version: str,
    values: Mapping[str, Any],
    declared: Mapping[str, bool],
) -> tuple[dict[str, Any], dict[str, bool]]:
    """Settle one column's value and its availability flag.

    Three outcomes per column, and they are the whole contract:

    * the caller declared the column unavailable — no figure may accompany it;
    * the caller declared the column available — a figure must;
    * nothing was declared — derive it, which is the common path.

    Args:
        version: The contract version, used in every error message.
        values: The figures the caller supplied.
        declared: The availability flags the caller overrode with.

    Returns:
        The value map and the availability map, both covering every column of
        the contract in contract order.

    Raises:
        DataValidationError: A column marked unavailable carries a figure, a column
            marked available carries none, or a non-nullable column is null.
    """
    columns = FEATURE_COLUMNS[version]
    nullable = NULLABLE_COLUMNS[version]
    resolved_values: dict[str, Any] = {}
    resolved_flags: dict[str, bool] = {}

    for column in columns:
        flag = declared.get(column)
        supplied = column in values
        raw = values.get(column)

        if flag is False:
            if supplied and raw is not None:
                raise DataValidationError(
                    f"{version}: column {column!r} is marked unavailable but carries "
                    f"{raw!r}. An unmeasured column cannot carry a figure; drop the "
                    "figure or drop the flag, not both."
                )
            resolved_values[column] = None
            resolved_flags[column] = False
            continue

        if flag is True:
            if not supplied or raw is None:
                raise DataValidationError(
                    f"{version}: column {column!r} is marked available but carries no "
                    "figure. Asserting availability without a measurement is the "
                    "fabrication this mask exists to prevent."
                )
            resolved_values[column] = raw
            resolved_flags[column] = True
            continue

        if raw is None:
            if column not in nullable:
                raise DataValidationError(
                    f"{version}: column {column!r} is not nullable, so null is not an "
                    "answer for it. A count of things that exist is always computable: "
                    "use 0 for 'none were found', or add the column to "
                    "NULLABLE_COLUMNS if the contract really can leave it unmeasured."
                )
            resolved_values[column] = None
            resolved_flags[column] = False
            continue

        resolved_values[column] = raw
        resolved_flags[column] = True

    return resolved_values, resolved_flags


def build_feature_row(
    source_schema_version: str,
    subject: str,
    values: Mapping[str, Any],
    *,
    available: Mapping[str, bool] | None = None,
    provenance: Provenance = Provenance.DERIVED,
    source: str = "",
) -> FeatureRow:
    """Wrap one extracted feature vector as a training row.

    The availability mask is **derived**, not asked for: every contract column
    appears in both maps, and a nullable column carrying ``None`` is unavailable
    while the same column carrying ``0`` is available.

    Omission is read differently depending on the column, and the difference is
    the contract rather than a convenience. A **nullable** column the caller
    omitted is unmeasured, exactly as an explicit ``None`` would be — no figure
    is invented to fill the hole. A **count** column the caller omitted is an
    error: those are always computable, so their absence means the extractor
    never ran, and a row that guesses at them would be the fabricated-zero defect
    wearing a different hat.

    Args:
        source_schema_version: One of :data:`FEATURE_COLUMNS`, naming the
            contract the figures came from.
        subject: What the row describes: a repository, an account, a task.
        values: The extracted figures, keyed by contract column. Nullable columns
            may be omitted; every count column must be supplied.
        available: Optional explicit availability overrides. Normally left as
            None so the mask is derived; supplied only by a caller that already
            holds a mask and wants the disagreement checked here rather than
            downstream.
        provenance: Where the figures came from. Defaults to ``DERIVED``, which
            is what every real extraction is; ``SYNTHETIC`` is for a row a
            template produced.
        source: Which recorded facts the extraction read, so a row can be traced
            back to them.

    Returns:
        The row, carrying every contract column in both maps, in contract order.

    Raises:
        DataValidationError: The contract version or subject is empty, a figure was
            supplied for a column the contract does not have, a non-nullable
            column is null, an overridden flag is not a bool, or an availability
            flag and its figure disagree about whether the column was measured.
    """
    if source_schema_version not in FEATURE_COLUMNS:
        raise DataValidationError(
            f"unknown feature contract {source_schema_version!r}; "
            f"known contracts: {sorted(FEATURE_COLUMNS)}"
        )
    if not isinstance(subject, str) or not subject.strip():
        raise DataValidationError(f"subject must be a non-empty string, got {subject!r}")

    columns = FEATURE_COLUMNS[source_schema_version]
    unknown = sorted(set(values) - set(columns))
    if unknown:
        raise DataValidationError(
            f"{source_schema_version}: values for columns the contract does not define: {unknown}"
        )

    declared: Mapping[str, bool] = {}
    if available is not None:
        not_bool = sorted(name for name, flag in available.items() if not isinstance(flag, bool))
        if not_bool:
            raise DataValidationError(
                f"{source_schema_version}: availability flags must be bool, got {not_bool}"
            )
        unknown_flags = sorted(set(available) - set(columns))
        if unknown_flags:
            raise DataValidationError(
                f"{source_schema_version}: availability flags for columns the "
                f"contract does not define: {unknown_flags}"
            )
        declared = available

    resolved_values, resolved_flags = _resolve(source_schema_version, values, declared)
    return FeatureRow(
        source_schema_version=source_schema_version,
        subject=subject,
        values=resolved_values,
        available=resolved_flags,
        provenance=provenance,
        source=source,
    )


def describe_feature_contracts() -> dict[str, Any]:
    """Describe all four contracts as plain JSON-ready data.

    A manifest, an eval report and a human reading a diff all need the same
    three things per contract — which columns exist, which may be null, and what
    each one means — and keeping those in one function is what stops them from
    drifting apart. Everything returned is a list, a string or an integer, so the
    result can be written straight into a manifest with
    :func:`ml.datasets.schema.stable_json_dumps`.

    Column order inside a contract is the contract's own order rather than
    alphabetical, because that order is the positional layout of the feature
    matrix. Sets inside the description are sorted, because a manifest hashed
    tomorrow must not depend on a frozenset's iteration.

    Returns:
        A mapping keyed by contract version. Each value carries ``columns``,
        ``column_count``, ``nullable_columns``, ``required_columns`` and
        ``column_meanings``, the last keyed by column in contract order.
    """
    description: dict[str, Any] = {}
    for version, columns in FEATURE_COLUMNS.items():
        nullable = NULLABLE_COLUMNS[version]
        description[version] = {
            "columns": list(columns),
            "column_count": len(columns),
            "nullable_columns": sorted(nullable),
            "required_columns": sorted(set(columns) - nullable),
            "column_meanings": {column: _COLUMN_MEANINGS[version][column] for column in columns},
        }
    return description
