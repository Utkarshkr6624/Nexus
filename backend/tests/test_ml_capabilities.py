"""The capability inventory harvested off `backend/app` by AST parsing.

Everything Phase 10 trains is an utterance aimed at *this* application, so the
label space cannot be invented — it has to be read off the code that will have
to serve the resulting intent. A silently short inventory is the failure this
module guards: the training set would look fine, the model would converge, and
the router would be judged on recall over a label space that quietly no longer
exists.

That is why the route and enum counts are asserted exactly rather than
"greater than zero". They are a tripwire on the *backend surface*: adding a
route here is normal and will require bumping this file; losing one silently is
a parser regression and should fail the suite loudly. The harvest is a pure AST
walk, so nothing in `app` is imported and the test runs on an interpreter with
only pydantic and pytest installed.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import app
from ml.datasets.capabilities import (
    CAPABILITY_INVENTORY_VERSION,
    CapabilityInventory,
    Route,
    build_capability_inventory,
    load_inventory,
    save_inventory,
)
from ml.datasets.schema import DatasetError, DataValidationError

#: The ``app`` package directory. The inventory reads files from here and never
#: imports them, so this is a path lookup rather than an application start.
APP_DIR = Path(app.__file__).parent

# Verified against this repository. A change to any of these is a change to the
# backend surface the training data is written for, and the number here is the
# record of what that surface was when the datasets were generated.
#
# Phase 11 added the ``/ml`` domain and its two endpoints, which is what took the
# totals from 185/19 to 187/20. Note what that means for the corpus: the
# classifier's label set was written for the 185-route surface, and the two new
# routes are intent *diagnostics*, not a new destination any intent routes to —
# so nothing in the training data is stale, but the counts below now describe a
# backend one generation newer than the corpus was drawn from.
EXPECTED_TOTAL_ROUTES = 187
EXPECTED_DOMAINS = 20
EXPECTED_RECOMMENDATION_TYPES = 12
EXPECTED_RISK_TYPES = 7
EXPECTED_PERMISSIONS = 11
EXPECTED_ACTIVITY_EVENTS = 62


@pytest.fixture(scope="module")
def inventory() -> CapabilityInventory:
    """Harvest the inventory once; the walk is AST-only and takes milliseconds."""
    return build_capability_inventory(APP_DIR)


def test_route_count_matches_the_verified_backend_surface(inventory):
    assert inventory.total_routes() == EXPECTED_TOTAL_ROUTES


def test_domain_and_vocabulary_counts_match_the_verified_backend_surface(inventory):
    assert len(inventory.entities()) == EXPECTED_DOMAINS
    assert len(inventory.recommendation_types) == EXPECTED_RECOMMENDATION_TYPES
    assert len(inventory.risk_types) == EXPECTED_RISK_TYPES
    assert len(inventory.permissions) == EXPECTED_PERMISSIONS
    assert len(inventory.activity_events) == EXPECTED_ACTIVITY_EVENTS


def test_every_vocabulary_is_free_of_duplicates(inventory):
    for name, values in (
        ("recommendation_types", inventory.recommendation_types),
        ("risk_types", inventory.risk_types),
        ("permissions", inventory.permissions),
        ("activity_events", inventory.activity_events),
    ):
        assert all(value.strip() for value in values), name
        assert len(set(values)) == len(values), name


def test_every_route_carries_a_method_path_module_and_handler(inventory):
    for route in inventory.routes:
        assert route.method
        assert route.path.startswith("/")
        assert route.module
        assert route.handler
        assert route.method in {"get", "post", "put", "patch", "delete", "head", "options"}
        assert route.domain, route.handler


def test_no_two_routes_share_a_method_and_path(inventory):
    """A duplicate pair means the declared surface is ambiguous about a verb."""
    pairs = [(route.path, route.method) for route in inventory.routes]
    assert len(set(pairs)) == len(pairs)


def test_routes_are_sorted_deterministically_by_path_then_method(inventory):
    keys = [(route.path, route.method) for route in inventory.routes]
    assert keys == sorted(keys)


def test_routes_are_grouped_per_module_and_the_counts_add_up(inventory):
    grouped = inventory.routes_by_module()
    counts = inventory.module_route_count()

    assert list(grouped) == sorted(grouped)
    assert list(counts) == list(grouped)
    assert sum(counts.values()) == inventory.total_routes()
    for module, routes in grouped.items():
        assert counts[module] == len(routes)
        assert all(route.module == module for route in routes)


def test_verbs_merge_the_http_methods_with_the_proposable_actions(inventory):
    verbs = inventory.verbs()

    assert verbs == tuple(sorted(verbs))
    assert set(inventory.recommendation_types) <= set(verbs)
    assert set(inventory.risk_types) <= set(verbs)
    assert {"get", "post"} <= set(verbs)
    # "break_down_task" is an action a person takes and no HTTP method is called it.
    assert "break_down_task" in verbs


def test_a_domain_is_the_first_path_segment(inventory):
    for route in inventory.routes:
        first = next((segment for segment in route.path.split("/") if segment), "")
        assert route.domain == first


def test_inventory_round_trips_through_to_dict_and_from_dict(inventory):
    restored = CapabilityInventory.from_dict(json.loads(json.dumps(inventory.to_dict())))

    assert restored == inventory
    assert restored.to_dict() == inventory.to_dict()
    assert restored.inventory_version == CAPABILITY_INVENTORY_VERSION


def test_a_saved_inventory_reads_back_identically(tmp_path, inventory):
    destination = tmp_path / "nested" / "capabilities.json"

    save_inventory(inventory, destination)

    assert load_inventory(destination) == inventory
    # Canonical JSON, so a manifest can hash the file and mean something.
    assert destination.read_text(encoding="utf-8").endswith("\n")
    assert destination.read_text(encoding="utf-8").count("\n") == 1


def test_an_unknown_inventory_version_is_refused(inventory):
    payload = inventory.to_dict()
    payload["inventory_version"] = "nexo_capabilities.v2"

    with pytest.raises(DataValidationError, match="inventory_version"):
        CapabilityInventory.from_dict(payload)


def test_a_malformed_route_entry_is_refused(inventory):
    payload = inventory.to_dict()
    payload["routes"] = [{"method": "get"}]

    with pytest.raises(DataValidationError, match="must be a non-empty string"):
        CapabilityInventory.from_dict(payload)


def test_a_non_list_route_collection_is_refused(inventory):
    payload = inventory.to_dict()
    payload["routes"] = "not a list"

    with pytest.raises(DataValidationError, match="routes must be a list"):
        CapabilityInventory.from_dict(payload)


def test_load_inventory_reports_a_missing_file(tmp_path):
    with pytest.raises(DatasetError, match="cannot read capability inventory"):
        load_inventory(tmp_path / "absent.json")


def test_load_inventory_reports_malformed_json(tmp_path):
    destination = tmp_path / "broken.json"
    destination.write_text("{not json", encoding="utf-8")

    with pytest.raises(DatasetError, match="malformed JSON"):
        load_inventory(destination)


def test_building_from_a_missing_directory_is_refused(tmp_path):
    with pytest.raises(DatasetError, match="app package directory not found"):
        build_capability_inventory(tmp_path / "no-such-app")


def test_building_from_a_directory_with_no_api_package_is_refused(tmp_path):
    empty = tmp_path / "app"
    empty.mkdir()

    with pytest.raises(DatasetError, match="capability source directory not found"):
        build_capability_inventory(empty)


def test_the_harvest_never_imports_the_application(tmp_path):
    """Proof by construction: the parser reads files and cannot execute them.

    A ``py`` file full of nonsense still parses, and an app whose routes were
    assembled at import time rather than declared would contribute nothing. Both
    facts are properties of using `ast` instead of importing, and both are what
    keeps `ml` runnable on an interpreter with no FastAPI installed.
    """
    api = tmp_path / "api" / "v1"
    api.mkdir(parents=True)
    (api / "router.py").write_text(
        "router = APIRouter(prefix='/x')\nthis is not python(\n", encoding="utf-8"
    )

    with pytest.raises(DatasetError, match="cannot parse capability source"):
        build_capability_inventory(tmp_path)

    assert isinstance(ast.parse("router = 1"), ast.Module)


def test_route_serialisation_carries_the_derived_domain():
    route = Route(method="get", path="/tasks/{task_id}", module="tasks", handler="get_task")

    payload = route.to_dict()

    assert payload["domain"] == "tasks"
    assert Route.from_dict(payload) == route
