"""The migration chain and the absence of model/schema drift."""

from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

from app.models import Base
from tests.conftest import _alembic_config

pytestmark = pytest.mark.integration

EXCLUDED_TABLES = frozenset({"alembic_version", "spatial_ref_sys"})
MANAGED_SCHEMA = "public"


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Mirror of ``migrations/env.py``: only NEXUS-owned objects are compared."""
    if type_ == "table":
        if name in EXCLUDED_TABLES:
            return False
        schema = getattr(obj, "schema", None)
        return schema is None or schema == MANAGED_SCHEMA
    return True


def head_revision() -> str:
    return ScriptDirectory.from_config(
        _alembic_config("postgresql+psycopg://unused")
    ).get_current_head()


def test_the_migration_chain_is_linear_and_has_a_single_head():
    script = ScriptDirectory.from_config(_alembic_config("postgresql+psycopg://unused"))
    heads = script.get_heads()

    assert len(heads) == 1, f"multiple heads, the chain has diverged: {heads}"
    assert heads[0] == script.get_current_head()
    # ``walk_revisions`` yields head first, so this is the reverse of the applied
    # order. Phase 2 added ``0002_phase2_identity_sessions`` and Phase 3 added
    # ``0004_phase4_planner`` and Phase 5 added ``0005_phase5_knowledge``;
    # a revision added later must extend this list rather than replace it.
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


def test_every_revision_is_reachable_from_the_single_head():
    """No orphan file in ``versions/`` and no unapplied fork.

    ``walk_revisions`` above already proves the order; this proves the graph is
    exactly a chain — each revision names a ``down_revision`` that exists, and
    the tip of it is the single head. A revision left behind pointing at a
    ``down_revision`` that has since been renamed cannot slip in unnoticed.
    """
    script = ScriptDirectory.from_config(_alembic_config("postgresql+psycopg://unused"))

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
    assert head_revision() == "0011"


async def test_the_test_database_is_migrated_to_head(engine):
    async with engine.connect() as connection:
        rows = await connection.execute(text("SELECT version_num FROM alembic_version"))

    assert [row[0] for row in rows] == [head_revision()]


async def test_the_migration_built_every_table_in_the_metadata(engine):
    """The schema must come from the migration, not from ``create_all``."""
    async with engine.connect() as connection:
        live = await connection.run_sync(
            lambda sync_connection: set(
                inspect(sync_connection).get_table_names(schema=MANAGED_SCHEMA)
            )
        )

    assert set(Base.metadata.tables) <= live


async def test_autogenerate_reports_no_drift(engine):
    """``Base.metadata`` and the migrated schema must describe the same tables.

    Compared through the sync facade with the options from ``migrations/env.py``,
    so the result is what ``alembic revision --autogenerate`` would act on.
    """
    connectable = create_engine(
        engine.url.render_as_string(hide_password=False), poolclass=NullPool
    )
    try:
        with connectable.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "compare_type": True,
                    "compare_server_default": True,
                    "include_object": include_object,
                },
            )
            diffs = compare_metadata(context, Base.metadata)
    finally:
        connectable.dispose()

    assert diffs == [], f"schema drift between Base.metadata and the migration: {diffs}"
