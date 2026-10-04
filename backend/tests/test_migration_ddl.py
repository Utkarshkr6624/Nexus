"""Model/migration agreement, checked by rendering the migrations offline.

``test_migrations.test_autogenerate_reports_no_drift`` asks Alembic to compare
``Base.metadata`` against a *migrated* database. That is the authoritative
check, but it needs PostgreSQL, so it cannot run on every machine — and in
practice it did not run when Phase 2 landed, which is how a stale expectation
survived.

This module asks the same question without a database. Alembic can render a
migration to SQL without connecting anywhere (``as_sql=True`` plus an output
buffer), so ``0001`` and ``0002`` are executed here into a string, that string
is parsed, and every ``CREATE TABLE`` column is compared with the one the model
declares — same name, same type, same nullability, same server default — along
with every foreign key and index. Nothing about the *live* schema is
asserted; the claim is narrower and honest: **the DDL the migrations emit and
the DDL the models describe are the same schema.**

Two casts are normalised away before comparing (``::jsonb``,
``::character varying``). Alembic's own PostgreSQL default comparison sends
both sides to the server as literals and lets the server decide, so a cast the
database would add itself is not drift; it is what ``pg_get_expr`` prints.
"""

from __future__ import annotations

import importlib
import io
import re

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models import Base
from tests.conftest import _alembic_config

#: Import paths, in the order the chain applies them.
MIGRATION_MODULES = (
    "migrations.versions.0001_initial_create_users",
    "migrations.versions.0002_phase2_identity_sessions",
    "migrations.versions.0003_phase3_projects_tasks",
    "migrations.versions.0004_phase4_planner",
    "migrations.versions.0005_phase5_knowledge",
    "migrations.versions.0006_phase6_analytics",
    "migrations.versions.0007_phase7_intelligence",
    "migrations.versions.0008_phase8_developer_intelligence",
    "migrations.versions.0009_phase9_learning_career",
    "migrations.versions.0010_learning_career_integrity",
    "migrations.versions.0011_tasks_analytics_window_indexes",
)

PG = postgresql.dialect()

_CONSTRAINT_LINE = re.compile(r"^(PRIMARY KEY|FOREIGN KEY|UNIQUE|CHECK|CONSTRAINT|EXCLUDE)\b")
_CREATE_TABLE = re.compile(r"CREATE TABLE (\w+) \((.*?)\n\)[;]?", re.S)
# The optional ``WHERE`` tail is what makes this match *partial* indexes.
# Phase 7 introduced them — they are the whole deduplication mechanism — and
# a regex that stopped at the closing paren would silently exclude every one
# of them from the comparison, which is the failure mode this module exists
# to prevent: an index that is never checked is an index that can drift.
# ``0010`` added the second thing that can sit between the column list and the
# predicate: ``NULLS NOT DISTINCT`` (PostgreSQL 15+), which is the difference
# between a uniqueness rule that fires and one that looks like it fires.
_CREATE_INDEX = re.compile(
    r"CREATE (UNIQUE )?INDEX (\w+) ON (\w+) \(([^)]*)\)"
    r"( NULLS NOT DISTINCT)?(?: WHERE (.+?))?;"
)
_ADD_COLUMN = re.compile(r"ALTER TABLE (\w+) ADD COLUMN (\w+) ([^;]+);")
_RENAME_COLUMN = re.compile(r"ALTER TABLE (\w+) RENAME (\w+) TO (\w+);")
_SET_NOT_NULL = re.compile(r"ALTER TABLE (\w+) ALTER COLUMN (\w+) SET NOT NULL;")
_FOREIGN_KEY = re.compile(r"FOREIGN KEY\((\w+)\) REFERENCES (\w+) \((\w+)\)([^,\n]*)")
_ON_DELETE = re.compile(r"ON DELETE ([A-Z ]+?)\s*$")

#: The three statement shapes a ``learning_activities.skill_id`` delete rule
#: arrives in. PostgreSQL cannot alter a foreign key's ``ON DELETE``, so the
#: only way to change it is to drop the constraint and add it again — which means
#: the chain contains an ``ALTER TABLE`` this module had no reason to understand
#: until ``0010`` wrote one. Matched as one alternation rather than three passes
#: so the statements are replayed in the order the migration emits them; reading
#: the ``CREATE TABLE`` blocks in isolation would report the superseded
#: ``CASCADE`` as the current rule and every foreign-key assertion below would be
#: checking a schema that no longer exists.
_ALTER_FOREIGN_KEY = re.compile(
    r"ALTER TABLE (?P<alter_table>\w+) ADD CONSTRAINT (?P<alter_name>\w+)"
    r" FOREIGN KEY\((?P<alter_child>\w+)\) REFERENCES (?P<alter_parent>\w+)"
    r" \(\w+\)(?P<alter_rule>[^;]*);"
)
_DROP_FOREIGN_KEY = re.compile(
    r"ALTER TABLE (?P<drop_table>\w+) DROP CONSTRAINT (?P<drop_name>\w+);"
)
_STATEMENTS_WITH_FOREIGN_KEYS = re.compile(
    r"CREATE TABLE (\w+) \((.*?)\n\)[;]?"
    rf"|{_ALTER_FOREIGN_KEY.pattern}"
    rf"|{_DROP_FOREIGN_KEY.pattern}",
    re.S,
)

#: ``0002`` renames one of ``0001``'s columns. The replayed shape keys on the
#: name the model uses, and this map records the name that came before it.
PREVIOUSLY_NAMED = {"users": {"full_name": "display_name"}}

#: Tables ``0001`` creates and ``0002`` alters rather than recreates. Their
#: final shape is the ``CREATE TABLE`` body plus every later statement, so the
#: parser below replays the statements in order instead of reading one block.
ALTERED_BY_0002 = frozenset({"users"})


def _normalise(ddl: str) -> str:
    """Collapse whitespace and drop casts PostgreSQL adds on reflection.

    ``DEFAULT 'true'`` and ``DEFAULT true`` are the same default: the first is
    an untyped string literal the database casts to ``boolean`` on assignment,
    and PostgreSQL normalises the stored expression back to ``true`` when
    reflecting it. Alembic's own comparison treats them as equal — it hands
    both sides to the server as literals and asks. Quoting is likewise dropped
    from ``::jsonb`` / ``::character varying`` casts.
    """
    collapsed = re.sub(r"\s+", " ", ddl).strip()
    collapsed = re.sub(r"::\w+(\[\])?", "", collapsed)
    return re.sub(r"DEFAULT '(true|false)'", r"DEFAULT \1", collapsed)


def render_migrations() -> str:
    """Run every migration in ``as_sql`` mode and return the emitted DDL.

    ``Operations.context`` installs the module-level ``alembic.op`` proxy the
    migrations call, and the ``output_buffer`` is where the offline ``impl``
    writes instead of a connection.
    """
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": buffer},
    )
    with Operations.context(context):
        for module in MIGRATION_MODULES:
            importlib.import_module(module).upgrade()
    return buffer.getvalue()


def _columns_from_create_table(body: str) -> dict[str, str]:
    columns: dict[str, str] = {}
    for raw in body.split("\n"):
        line = raw.strip().rstrip(",").strip()
        if not line or _CONSTRAINT_LINE.match(line):
            continue
        columns[line.split()[0]] = _normalise(line)
    return columns


def replay(ddl: str) -> dict[str, dict[str, str]]:
    """Apply the rendered statements to a bare ``{table: {column: ddl}}`` map.

    Only the statement forms the chain uses are interpreted — ``CREATE TABLE``,
    ``ADD COLUMN``, ``RENAME COLUMN`` and ``ALTER COLUMN ... SET NOT NULL``.
    Anything else is ignored, so a future revision using a form this helper does
    not understand shows up as a column the model declares and the replay does
    not, rather than passing unnoticed.
    """
    tables: dict[str, dict[str, str]] = {}
    for match in _CREATE_TABLE.finditer(ddl):
        tables[match.group(1)] = _columns_from_create_table(match.group(2))
    for match in _ADD_COLUMN.finditer(ddl):
        tables[match.group(1)][match.group(2)] = _normalise(f"{match.group(2)} {match.group(3)}")
    for match in _RENAME_COLUMN.finditer(ddl):
        table, old, new = match.groups()
        renamed = {}
        for name, text in tables[table].items():
            if name == old:
                new_name = new or PREVIOUSLY_NAMED.get(table, {}).get(old, old)
                renamed[new_name] = re.sub(rf"^{old}\b", new_name, text)
            else:
                renamed[name] = text
        tables[table] = renamed
    for match in _SET_NOT_NULL.finditer(ddl):
        table, column = match.groups()
        tables[table][column] = f"{tables[table][column]} NOT NULL"
    return tables


@pytest.fixture(scope="module")
def ddl() -> str:
    return render_migrations()


@pytest.fixture(scope="module")
def created_tables(ddl: str) -> dict[str, dict[str, str]]:
    """The final shape of every table the chain ends up with."""
    return replay(ddl)


def _foreign_keys(ddl: str) -> set[tuple[str, str, str, str | None]]:
    """``{(child_table, child_column, parent_table, ondelete)}`` for the DDL.

    The child table is not named in a ``FOREIGN KEY`` clause, so it has to be
    carried over from the enclosing ``CREATE TABLE`` block.

    Statements are replayed in emission order so that an ``ALTER TABLE ... ADD
    CONSTRAINT ... FOREIGN KEY`` *replaces* the rule recorded for the same
    ``(table, column)`` when the table was created. ``0010`` is the first
    revision to need this: changing a foreign key's delete rule is a drop
    followed by an add, because PostgreSQL has no ``ALTER CONSTRAINT`` for it.

    A ``DROP CONSTRAINT`` on its own deliberately removes nothing, since the
    constraint name carries no column and nothing in this module may guess at a
    naming convention. That is the safe direction: an unbalanced drop leaves the
    superseded rule standing, so ``expected == declared`` below disagrees with
    ``Base.metadata`` and fails, rather than the two sides quietly agreeing on a
    constraint that no longer exists.
    """
    found: set[tuple[str, str, str, str | None]] = set()
    for match in _STATEMENTS_WITH_FOREIGN_KEYS.finditer(ddl):
        if match.group(1) is not None:
            table = match.group(1)
            for child, parent, _referenced, rule in _FOREIGN_KEY.findall(match.group(2)):
                ondelete = _ON_DELETE.findall(rule)
                found.add((table, child, parent, ondelete[0] if ondelete else None))
        elif match.group("alter_table") is not None:
            table, child, parent = (
                match.group("alter_table"),
                match.group("alter_child"),
                match.group("alter_parent"),
            )
            ondelete = _ON_DELETE.findall(match.group("alter_rule"))
            found = {entry for entry in found if entry[:2] != (table, child)}
            found.add((table, child, parent, ondelete[0] if ondelete else None))
    return found


@pytest.fixture(scope="module")
def created_indexes(ddl: str) -> dict[str, tuple[str, tuple[str, ...], bool]]:
    """``{index_name: (table, columns, unique)}``.

    The predicate of a partial index is deliberately *not* part of the value:
    the comparison below is about which columns an index covers, and holding
    the two sides to the exact text of a ``WHERE`` clause would make this
    module fail on quoting differences rather than on schema drift. What
    matters for a partial index is that it exists, covers the deduplication
    key, and is unique — all three of which are asserted here.
    """
    return {
        match.group(2): (
            match.group(3),
            tuple(column.strip() for column in match.group(4).split(",")),
            match.group(1) is not None,
        )
        for match in _CREATE_INDEX.finditer(ddl)
    }


def _model_columns() -> dict[str, dict[str, str]]:
    """The metadata rendered to ``CREATE TABLE`` DDL, parsed the same way.

    Going through :class:`CreateTable` rather than :class:`CreateColumn` for
    each column matters: a boolean column's ``server_default="true"`` renders as
    a quoted literal when SQLAlchemy is not given the column's type, so a
    per-column render would report a difference against a migration that is
    byte-for-byte right.
    """
    return {
        name: _columns_from_create_table(
            _CREATE_TABLE.search(str(CreateTable(table).compile(dialect=PG))).group(2)
        )
        for name, table in Base.metadata.tables.items()
    }


@pytest.fixture(scope="module")
def model_columns() -> dict[str, dict[str, str]]:
    return _model_columns()


# -- The chain ---------------------------------------------------------------


def test_the_chain_renders_without_an_error(ddl):
    """A syntax or arity error in a migration raises here rather than on deploy."""
    assert "CREATE TABLE users" in ddl
    assert "CREATE TABLE sessions" in ddl


def test_the_migration_chain_is_linear_with_a_single_head():
    """The head and the order, read off the script directory rather than the DDL.

    ``test_migrations`` asserts the same thing but is marked ``integration``;
    this copy runs everywhere, which is the point — the assertion that went stale
    when Phase 2 landed was never executed, and the one that went stale when
    Phase 3 landed failed the same way.
    """
    script = ScriptDirectory.from_config(_alembic_config("postgresql+psycopg://unused"))

    assert script.get_heads() == ["0011"]
    assert [revision.revision for revision in script.walk_revisions()] == [
        "0011",
        "0010",
        "0009",
        "0008",
        "0007",
        "0006",
        "0005",
        "0004",
        "0003",
        "0002",
        "0001",
    ]
    assert {revision.revision: revision.down_revision for revision in script.walk_revisions()} == {
        "0011": "0010",
        "0010": "0009",
        "0009": "0008",
        "0008": "0007",
        "0007": "0006",
        "0006": "0005",
        "0005": "0004",
        "0004": "0003",
        "0003": "0002",
        "0002": "0001",
        "0001": None,
    }


def test_every_migrated_table_exists_in_the_metadata(ddl):
    created = {match.group(1) for match in _CREATE_TABLE.finditer(ddl)}
    assert created == set(Base.metadata.tables)
    assert ALTERED_BY_0002 & created == {"users"}, (
        "a table outside ALTERED_BY_0002 changed shape without being updated there"
    )


# -- Columns -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("table_name", "column_name"),
    [
        (table, column)
        for table, column in (
            ("users", "id"),
            ("users", "email"),
            ("users", "username"),
            ("users", "display_name"),
            ("users", "avatar_url"),
            ("users", "role"),
            ("users", "hashed_password"),
            ("users", "is_active"),
            ("users", "is_verified"),
            ("users", "is_superuser"),
            ("users", "last_login_at"),
            ("users", "password_changed_at"),
            ("users", "created_at"),
            ("users", "updated_at"),
            ("sessions", "id"),
            ("sessions", "user_id"),
            ("sessions", "token_hash"),
            ("sessions", "user_agent"),
            ("sessions", "ip_address"),
            ("sessions", "expires_at"),
            ("sessions", "last_used_at"),
            ("sessions", "revoked_at"),
            ("sessions", "created_at"),
            ("sessions", "updated_at"),
            ("password_reset_tokens", "id"),
            ("password_reset_tokens", "user_id"),
            ("password_reset_tokens", "token_hash"),
            ("password_reset_tokens", "expires_at"),
            ("password_reset_tokens", "used_at"),
            ("password_reset_tokens", "created_at"),
            ("password_reset_tokens", "updated_at"),
            ("audit_logs", "id"),
            ("audit_logs", "user_id"),
            ("audit_logs", "event_type"),
            ("audit_logs", "ip_address"),
            ("audit_logs", "user_agent"),
            ("audit_logs", "metadata"),
            ("audit_logs", "created_at"),
            ("projects", "id"),
            ("projects", "owner_id"),
            ("projects", "name"),
            ("projects", "description"),
            ("projects", "status"),
            ("projects", "priority"),
            ("projects", "start_date"),
            ("projects", "target_date"),
            ("projects", "completed_at"),
            ("projects", "archived_at"),
            ("projects", "created_at"),
            ("projects", "updated_at"),
            ("tasks", "id"),
            ("tasks", "project_id"),
            ("tasks", "owner_id"),
            ("tasks", "parent_id"),
            ("tasks", "title"),
            ("tasks", "description"),
            ("tasks", "status"),
            ("tasks", "priority"),
            ("tasks", "start_date"),
            ("tasks", "due_date"),
            ("tasks", "estimated_minutes"),
            ("tasks", "actual_minutes"),
            ("tasks", "completed_at"),
            ("tasks", "position"),
            ("tasks", "created_at"),
            ("tasks", "updated_at"),
            ("tags", "id"),
            ("tags", "user_id"),
            ("tags", "name"),
            ("tags", "created_at"),
            ("tags", "updated_at"),
            ("task_dependencies", "id"),
            ("task_dependencies", "task_id"),
            ("task_dependencies", "depends_on_id"),
            ("activity_events", "id"),
            ("activity_events", "user_id"),
            ("activity_events", "project_id"),
            ("activity_events", "task_id"),
            ("activity_events", "event_type"),
            ("activity_events", "metadata"),
            ("activity_events", "created_at"),
        )
    ],
)
def test_a_migrated_column_matches_the_model(
    column_name, table_name, created_tables, model_columns
):
    """Type, nullability and server default agree, column by column."""
    assert created_tables[table_name][column_name] == model_columns[table_name][column_name]


def test_no_column_is_migrated_that_the_model_does_not_declare(created_tables):
    extra = {
        table: set(columns) - set(Base.metadata.tables[table].c.keys())
        for table, columns in created_tables.items()
        if set(columns) - set(Base.metadata.tables[table].c.keys())
    }
    assert extra == {}


def test_the_renamed_column_never_appears_after_phase_1(ddl, created_tables):
    """``full_name`` was renamed, not duplicated.

    Both halves matter: the emitted DDL has to say ``RENAME`` rather than
    ``ADD COLUMN``, and ``full_name`` must be gone from the final shape — a
    leftover copy would leave the model and the database describing different
    tables while every other assertion here still passed.
    """
    assert "ALTER TABLE users RENAME full_name TO display_name" in ddl
    assert "full_name" not in created_tables["users"]
    assert "display_name" in created_tables["users"]


# -- The Phase 2 username backfill -------------------------------------------


def test_username_is_backfilled_per_row_and_then_made_not_null(ddl):
    """The three statements that make ``username`` NOT NULL are all present.

    Adding the column NOT NULL in one step would fail against a populated
    ``users`` table, and adding it with a constant ``server_default`` would
    hand every existing row the same handle and trip the unique index. The
    chosen strategy — add nullable, backfill per row, then ``SET NOT NULL`` —
    is the only one of the three that applies to any database.
    """
    assert "ALTER TABLE users ADD COLUMN username VARCHAR(32);" in ddl
    assert "SET NOT NULL" in ddl
    assert "UPDATE users SET username" in ddl
    # A shared default would defeat the unique index the next statement creates.
    assert not re.search(r"ADD COLUMN username VARCHAR\(32\) DEFAULT", ddl)


def test_the_backfilled_username_fits_the_column_width_and_stays_unique(ddl):
    """The SQL that builds a handle cannot emit a value longer than VARCHAR(32).

    15 characters of local part, an underscore, then 16 hex digits of the row's
    own UUID: 15 + 1 + 16 = 32 exactly. A length change to either term without
    a change to the other would either truncate (silent collisions once two
    rows truncate to the same handle) or be rejected by the column.
    """
    backfill = next(
        line for line in ddl.split("\n") if line.startswith("UPDATE users SET username")
    )
    assert "left(split_part(email, '@', 1), 15)" in backfill
    assert "|| '_' ||" in backfill
    assert "left(replace(id::text, '-', ''), 16)" in backfill
    assert len("x" * 15 + "_" + "f" * 16) == 32


def test_the_username_unique_index_exists_in_the_migration(created_indexes):
    """Uniqueness is folded into the index, as the model's ``unique=True`` asks.

    The model writes ``unique=True, index=True`` on the column, which makes
    SQLAlchemy emit one unique index rather than a column plus a separate
    ``UniqueConstraint``; a migration that created a plain index would leave the
    database free to accept a duplicate handle.
    """
    assert created_indexes["ix_users_username"] == ("users", ("username",), True)


# -- Foreign keys ------------------------------------------------------------


def test_the_audit_user_id_foreign_key_is_set_null_not_cascade(ddl, created_tables):
    """An audit row must outlive the account it describes.

    ``app/models/audit.py`` declares ``ForeignKey("users.id",
    ondelete="SET NULL")``. The migration has to say the same thing: ``CASCADE``
    would delete the security history along with the account, which is exactly
    the trail an investigation into that deletion needs.
    """
    rules = {
        (child, parent, ondelete)
        for child_table, child, parent, ondelete in _foreign_keys(ddl)
        if child_table == "audit_logs"
    }
    assert rules == {("user_id", "users", "SET NULL")}
    # And the column the constraint hangs off has to be nullable for SET NULL to
    # be meaningful at all.
    assert created_tables["audit_logs"]["user_id"] == "user_id UUID"


def test_the_session_and_reset_token_foreign_keys_cascade(ddl):
    """A session row is part of the account; it goes with it."""
    rules = {
        (child_table, child, parent, ondelete)
        for child_table, child, parent, ondelete in _foreign_keys(ddl)
    }
    assert ("sessions", "user_id", "users", "CASCADE") in rules
    assert ("password_reset_tokens", "user_id", "users", "CASCADE") in rules


def test_every_model_foreign_key_has_a_matching_migrated_constraint(ddl):
    """The other direction: nothing the model declares is left undeclared.

    ``ondelete`` is compared too, because a constraint that exists with the
    wrong rule is worse than one that does not exist: it looks migrated, and
    every test above that reads a ``CREATE TABLE`` block would still pass.
    """
    declared = _foreign_keys(ddl)
    expected = {
        (column.table.name, column.name, element.column.table.name, element.ondelete)
        for table in Base.metadata.tables.values()
        for column in table.columns
        for element in column.foreign_keys
    }
    assert expected == declared


# -- Phase 3: work management -------------------------------------------------


def test_the_dependency_and_subtask_checks_reject_self_reference(ddl):
    """Self-reference is refused by the DATABASE, not only by the service.

    Two independent guards are asserted here, because they guard different
    tables for different reasons. ``task_dependencies`` is a genuine graph, so
    a row claiming a task depends on itself is a cycle of length one and would
    make the cycle walk in the service recurse forever if it ever got in.
    ``tasks.parent_id`` is a hierarchy, and a task that is its own parent is
    nonsense that a single careless update can write. Neither is something a
    Python check should be the last line of defence for — a check in the service
    is a check some other writer can forget.
    """
    assert "CHECK (task_id <> depends_on_id)" in ddl
    assert "CHECK (parent_id IS NULL OR parent_id <> id)" in ddl


def test_the_dependency_pair_is_unique(ddl):
    """The same dependency cannot be recorded twice.

    Without this, adding a dependency twice is a silent no-op the second time
    rather than a conflict, and the duplicate row would be returned to a
    relationship listing that then renders the same edge twice.
    """
    assert "UNIQUE (task_id, depends_on_id)" in ddl


def test_activity_history_outlives_the_project_and_task_it_describes(ddl):
    """``activity_events`` is ``SET NULL`` on every reference, never ``CASCADE``.

    Phase 3 makes deletion cheap and common, and the activity feed is the thing
    that makes a deleted project explainable afterwards. If the rows went with
    the row, the record of what happened would vanish with the thing it
    describes — the same reasoning that puts ``SET NULL`` on ``audit_logs``,
    and worth asserting separately because the two tables are written by
    different code for different readers.
    """
    rules = {
        (child, parent, ondelete)
        for child_table, child, parent, ondelete in _foreign_keys(ddl)
        if child_table == "activity_events"
    }
    assert rules == {
        ("user_id", "users", "SET NULL"),
        ("project_id", "projects", "SET NULL"),
        ("task_id", "tasks", "SET NULL"),
    }


def test_projects_and_tasks_cascade_from_their_owner(ddl):
    """Deleting an account takes its work with it.

    The opposite choice to ``activity_events``, and deliberate: a project row
    with no owner is an orphan no query can reach, so keeping it would only
    leak rows into a table nothing reads. The history is kept precisely because
    it stays reachable without an owner.
    """
    rules = {
        (child_table, child, parent, ondelete)
        for child_table, child, parent, ondelete in _foreign_keys(ddl)
    }
    assert ("projects", "owner_id", "users", "CASCADE") in rules
    assert ("tasks", "owner_id", "users", "CASCADE") in rules
    assert ("tasks", "project_id", "projects", "CASCADE") in rules
    assert ("tags", "user_id", "users", "CASCADE") in rules


def test_tags_are_unique_per_user_and_not_globally(ddl):
    """Two accounts may each have a tag called ``python``.

    A global unique index on the name would make the second account's tag a
    conflict it cannot resolve without reading someone else's vocabulary, and
    the tag list is filtered by owner on every query, so a global name is never
    resolved across accounts anyway.
    """
    assert "UNIQUE (user_id, name)" in ddl


def test_the_migration_declares_exactly_one_foreign_key_per_table(ddl):
    per_table: dict[str, int] = {}
    for child_table, *_ in _foreign_keys(ddl):
        per_table[child_table] = per_table.get(child_table, 0) + 1
    assert per_table == {
        "sessions": 1,
        "password_reset_tokens": 1,
        "audit_logs": 1,
        "projects": 1,
        "tags": 1,
        "tasks": 3,
        "task_dependencies": 2,
        "task_tags": 2,
        "project_tags": 2,
        "activity_events": 3,
        "calendar_events": 3,
        "work_sessions": 3,
        "availability_rules": 1,
        "notes": 2,
        "note_revisions": 2,
        "note_tags": 2,
        "concept_tags": 2,
        "concepts": 1,
        "resources": 1,
        "bookmarks": 1,
        "documents": 1,
        "categories": 2,
        "knowledge_links": 1,
        # Phase 6: the daily aggregate tier belongs outright to the account
        # whose rows it summarises, so it cascades like every other per-user
        # table in the schema.
        "daily_metrics": 1,
        # Phase 7. `recommendations` is the only table in the schema with two:
        # the owning user, plus the risk it came from. That second one is SET
        # NULL rather than CASCADE on purpose, so deleting a risk keeps the
        # record of the user having acted on it — which is the training label,
        # and the only reason to keep the link at all.
        "risks": 1,
        "recommendations": 2,
        "risk_evaluations": 1,
        # Phase 8. The three tables that hang off a repository each carry two:
        # the owning user, plus the repository they belong to. `git_repositories`
        # is the exception — its second reference is the *project* it is
        # associated with, which is SET NULL so the repository trail outlives the
        # project rather than the other way round.
        "git_repositories": 2,
        "git_commits": 2,
        "git_branches": 2,
        "git_scan_runs": 2,
        # Phase 9. The three new user-owned tables that also point at something
        # else carry two references each, and the counts say so deliberately:
        # `learning_goals` reaches a skill, a project *and* a note, and
        # `career_evidence` reaches a project, a skill *and* a repository. Those
        # links are SET NULL so the trail outlives the thing it points at — the
        # reasoning `git_repositories.project_id` already sets out — while the
        # owning `user_id` cascades, because a row with no owner is an orphan no
        # query can reach.
        "learning_goals": 4,
        "skills": 1,
        "learning_activities": 3,
        "career_profiles": 1,
        "career_experience": 1,
        "career_evidence": 4,
    }
    # `learning_activities` still has three after 0010: that revision changed what
    # one of the three does on delete rather than adding or removing a reference,
    # which is the point the rule-level assertion below makes.


# -- Phase 10: repairs --------------------------------------------------------


def test_a_recorded_activity_outlives_the_skill_it_was_recorded_against(ddl, created_tables):
    """``learning_activities.skill_id`` is ``SET NULL``, not ``CASCADE``.

    ``0009`` shipped this foreign key as ``CASCADE`` while its own prose said
    that deleting a skill leaves the recorded trail standing; the constraint won.
    The consequence was invisible by construction, because ``learning_activities``
    carries no ``updated_at`` — nothing recorded that rows had been destroyed, and
    ``skills.evidence_count`` went on counting a set that no longer existed.

    Both references on the table are asserted, not just the one that changed: the
    point of the repair is that ``skill_id`` and ``goal_id`` now follow the same
    rule, and an assertion that only ever named ``skill_id`` would still pass if
    someone reverted ``goal_id``.
    """
    rules = {
        (child, parent, ondelete)
        for child_table, child, parent, ondelete in _foreign_keys(ddl)
        if child_table == "learning_activities"
    }
    assert rules == {
        ("user_id", "users", "CASCADE"),
        ("skill_id", "skills", "SET NULL"),
        ("goal_id", "learning_goals", "SET NULL"),
    }
    # SET NULL is only meaningful against a nullable column, and so is the second
    # half of the argument: the surviving row must still be able to say "no skill
    # was named" rather than being forced to carry a dangling id.
    assert created_tables["learning_activities"]["skill_id"] == "skill_id UUID"


def test_the_evidence_deduplication_index_treats_nulls_as_equal_to_each_other(ddl):
    """The rendered index says ``NULLS NOT DISTINCT``, and is partial besides.

    Two separate defects hide behind one index name, and the DDL is the only
    place either is visible:

    * **Partial.** A row with all three foreign keys null is a manual
      achievement, and the user may record several of them. Leaving those rows in
      the index would make the rule refuse the second, which is the opposite of
      what the phase wants.
    * **``NULLS NOT DISTINCT``.** A plain btree unique index treats nulls as equal
      to nothing, so a project-derived row — ``project_id`` set, the other two
      null — was never a duplicate of itself. This flag is the only reason the
      index refuses the second derivation.

    The phrase is asserted rather than inferred from the word ``UNIQUE`` because
    a unique index without it is exactly the bug: it looks like the rule and
    behaves as though it were not there.
    """
    assert (
        "CREATE UNIQUE INDEX uq_career_evidence_source_identity "
        "ON career_evidence (user_id, evidence_type, source, project_id, "
        "skill_id, repository_id) NULLS NOT DISTINCT "
        "WHERE project_id IS NOT NULL OR skill_id IS NOT NULL "
        "OR repository_id IS NOT NULL;" in ddl
    )
    # And the table-level constraint that could not enforce either half is
    # released before the index takes its name, rather than sitting alongside it
    # and still pretending to. The ``CREATE TABLE`` above is `0009`'s and still
    # contains it, which is exactly why the order has to be asserted: the
    # constraint stops existing part way through the chain, not in the table
    # definition.
    dropped = ddl.index(
        "ALTER TABLE career_evidence DROP CONSTRAINT uq_career_evidence_source_identity;"
    )
    created = ddl.index("CREATE UNIQUE INDEX uq_career_evidence_source_identity")
    assert dropped < created


# -- Indexes -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("index_name", "table", "columns", "unique"),
    [
        ("ix_users_email", "users", ("email",), True),
        ("ix_users_username", "users", ("username",), True),
        ("ix_sessions_user_id", "sessions", ("user_id",), False),
        ("ix_sessions_token_hash", "sessions", ("token_hash",), False),
        ("ix_password_reset_tokens_user_id", "password_reset_tokens", ("user_id",), False),
        (
            "ix_password_reset_tokens_token_hash",
            "password_reset_tokens",
            ("token_hash",),
            False,
        ),
        ("ix_audit_logs_user_id", "audit_logs", ("user_id",), False),
        ("ix_audit_logs_event_type", "audit_logs", ("event_type",), False),
        ("ix_projects_owner_id", "projects", ("owner_id",), False),
        ("ix_tasks_project_id", "tasks", ("project_id",), False),
        ("ix_tasks_owner_id", "tasks", ("owner_id",), False),
        ("ix_tasks_parent_id", "tasks", ("parent_id",), False),
        (
            "ix_tasks_owner_status_due",
            "tasks",
            ("owner_id", "status", "due_date"),
            False,
        ),
        ("ix_tags_user_id", "tags", ("user_id",), False),
        ("ix_task_dependencies_task_id", "task_dependencies", ("task_id",), False),
        (
            "ix_task_dependencies_depends_on_id",
            "task_dependencies",
            ("depends_on_id",),
            False,
        ),
        ("ix_activity_events_user_id", "activity_events", ("user_id",), False),
        ("ix_activity_events_project_id", "activity_events", ("project_id",), False),
        ("ix_activity_events_task_id", "activity_events", ("task_id",), False),
        ("ix_activity_events_event_type", "activity_events", ("event_type",), False),
        # 0010. The feed's own read: an equality probe on `user_id` and then a
        # sort on `created_at`. `id` is deliberately absent — it only breaks ties
        # within a microsecond, and it would widen every insert into an
        # append-only table for that. The bare `(user_id)` index above stays
        # exactly as narrow as it was, because it is still the cheapest thing to
        # reach for when nothing is being ordered.
        (
            "ix_activity_events_owner_created",
            "activity_events",
            ("user_id", "created_at"),
            False,
        ),
        # Phase 7. The two partial unique indexes are the deduplication
        # mechanism, so they are listed here like any other index — which is
        # only possible because `_CREATE_INDEX` was taught to match a WHERE
        # tail. Before that they were invisible to this comparison entirely.
        (
            "uq_risks_live_identity",
            "risks",
            ("user_id", "risk_type", "entity_type", "entity_id"),
            True,
        ),
        ("ix_risks_user_id", "risks", ("user_id",), False),
        ("ix_risks_detected_at", "risks", ("detected_at",), False),
        (
            "ix_risks_owner_status_severity",
            "risks",
            ("user_id", "status", "severity"),
            False,
        ),
        (
            "uq_recommendations_open_identity",
            "recommendations",
            ("user_id", "recommendation_type", "entity_type", "entity_id"),
            True,
        ),
        ("ix_recommendations_user_id", "recommendations", ("user_id",), False),
        (
            "ix_recommendations_owner_status_priority",
            "recommendations",
            ("user_id", "status", "priority"),
            False,
        ),
        (
            "ix_risk_evaluations_user_id",
            "risk_evaluations",
            ("user_id",),
            False,
        ),
        (
            "ix_risk_evaluations_owner_evaluated",
            "risk_evaluations",
            ("user_id", "evaluated_at"),
            False,
        ),
        # Phase 8. Each of these is a windowed read keyed on the owner and then
        # ordered or filtered by time, which is the shape a bare `user_id` index
        # cannot serve — the two composite indexes per fact table are the ones
        # that make the analytics windows cheap, and are listed here so their
        # column order is checked rather than merely their existence.
        ("ix_git_repositories_user_id", "git_repositories", ("user_id",), False),
        (
            "ix_git_repositories_owner_active",
            "git_repositories",
            ("user_id", "is_active"),
            False,
        ),
        ("ix_git_repositories_project_id", "git_repositories", ("project_id",), False),
        ("ix_git_commits_user_id", "git_commits", ("user_id",), False),
        (
            "ix_git_commits_repo_committed",
            "git_commits",
            ("repository_id", "committed_at"),
            False,
        ),
        (
            "ix_git_commits_user_committed",
            "git_commits",
            ("user_id", "committed_at"),
            False,
        ),
        ("ix_git_branches_user_id", "git_branches", ("user_id",), False),
        ("ix_git_branches_repo_id", "git_branches", ("repository_id",), False),
        ("ix_git_scan_runs_user_id", "git_scan_runs", ("user_id",), False),
        (
            "ix_git_scan_runs_repo_scanned",
            "git_scan_runs",
            ("repository_id", "scanned_at"),
            False,
        ),
        # Phase 9. Same two shapes as Phase 8's: a bare `user_id` index for the
        # list read, and a composite `(user_id, <time or status>)` for the
        # windowed reads every learning and career screen is made of. The
        # `(user_id, target_date)` and `(user_id, occurred_on)` indexes in
        # particular are what make "goals due soon" and "evidence, newest first"
        # cheap; their column order is the point of listing them here.
        ("ix_learning_goals_user_id", "learning_goals", ("user_id",), False),
        (
            "ix_learning_goals_owner_status",
            "learning_goals",
            ("user_id", "status"),
            False,
        ),
        (
            "ix_learning_goals_owner_target_date",
            "learning_goals",
            ("user_id", "target_date"),
            False,
        ),
        ("ix_skills_user_id", "skills", ("user_id",), False),
        ("ix_learning_activities_user_id", "learning_activities", ("user_id",), False),
        (
            "ix_learning_activities_user_occurred",
            "learning_activities",
            ("user_id", "occurred_at"),
            False,
        ),
        ("ix_learning_activities_skill_id", "learning_activities", ("skill_id",), False),
        ("ix_learning_activities_goal_id", "learning_activities", ("goal_id",), False),
        ("ix_career_profiles_user_id", "career_profiles", ("user_id",), False),
        ("ix_career_experience_user_id", "career_experience", ("user_id",), False),
        ("ix_career_evidence_user_id", "career_evidence", ("user_id",), False),
        (
            "ix_career_evidence_user_occurred",
            "career_evidence",
            ("user_id", "occurred_on"),
            False,
        ),
        # 0010. This one was a table-level `UNIQUE` constraint until 0010 and is
        # listed here now only because it became an *index*: a partial unique
        # index is the only form of "at most one of these, when one of these" that
        # PostgreSQL will apply the predicate to. `unique=True` is the assertion
        # that matters and it is asserted; the predicate itself is checked in
        # `tests/test_migration_0010.py`, where the behaviour it produces is
        # actually attempted against a live server.
        (
            "uq_career_evidence_source_identity",
            "career_evidence",
            (
                "user_id",
                "evidence_type",
                "source",
                "project_id",
                "skill_id",
                "repository_id",
            ),
            True,
        ),
        # 0011. The analytics window reads on `tasks`. Both are listed here
        # because `tests/test_migration_ddl.py::test_every_indexed_model_column_has_a_migrated_index`
        # asserts the two directions of that agreement — a model index with no
        # migration is a schema that never gets built, and a migrated index with
        # no model is one that a later `create_all` or autogenerate would
        # silently drop. What the index is *for* — a measured sequential scan
        # narrowed to the window — is argued in the migration's own docstring.
        ("ix_tasks_owner_created", "tasks", ("owner_id", "created_at"), False),
        ("ix_tasks_owner_completed", "tasks", ("owner_id", "completed_at"), False),
    ],
)
def test_a_migrated_index_matches_the_model(index_name, table, columns, unique, created_indexes):
    assert created_indexes[index_name] == (table, columns, unique)


def test_every_indexed_model_column_has_a_migrated_index(created_indexes):
    """A missing index is silent drift: nothing errors, the table just gets slow.

    An index can be declared two ways and both have to be covered. ``index=True``
    on a column is the common one; Phase 3 introduced the second, a composite
    ``Index(...)`` in ``__table_args__`` for the dashboard's hot queries. Reading
    only the column flag made this test report those columns as "migrated
    without a model index" — the assertion was narrower than the model, not the
    migration wrong.
    """
    from_index_flag = {
        (column.table.name, column.name)
        for table in Base.metadata.tables.values()
        for column in table.columns
        if column.index
    }
    # Explicit composite indexes: every column they cover is declared indexed,
    # even though no single column carries the flag.
    for table in Base.metadata.tables.values():
        for index in table.indexes:
            for expression in index.expressions:
                name = getattr(expression, "name", None)
                if name is not None:
                    from_index_flag.add((table.name, name))
    from_migration = {
        (table, name) for table, columns, _ in created_indexes.values() for name in columns
    }
    assert from_index_flag == from_migration
