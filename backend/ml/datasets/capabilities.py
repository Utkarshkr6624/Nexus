"""The capability inventory: what NEXUS can actually do, read off the source.

Everything Phase 10 trains is an utterance aimed at *this* application, so the
label space cannot be invented — it has to be harvested from the code that will
have to serve the resulting intent. That harvest is this module, and it is done
with :mod:`ast` rather than by importing ``app``: importing the routers would
pull FastAPI, SQLAlchemy and the settings machinery onto a bare interpreter, and
``ml`` is stdlib-only by design. Parsing also has a property importing cannot
offer — it reads the *declaration*, so a route counted here is a route the
module declares, whether or not the application object was ever assembled.

Four vocabularies are harvested, and they are not interchangeable:

* **Routes** — the HTTP surface, from the ``APIRouter(prefix=...)`` assignment
  and the ``@router.<verb>(...)`` decorators. The path argument is read as an
  AST node because these decorators are formatted across many lines and a
  regex over source text would break on the first one someone wrapped.
* **Recommendation types** and **risk types** — the actions NEXUS can *propose*.
  Both are closed sets, and every recommendation type names something a person
  does rather than something the system performs. That constraint is load-
  bearing for training: an intent label set that contained an "auto-execute"
  verb would be teaching the model to ask for behaviour the product forbids.
* **Permissions** — the capability axis the authorisation layer actually checks.
* **Activity events** — the vocabulary the work-management trail is written in.

Parsing fails loudly. A missing or unparseable file raises
:class:`~ml.datasets.schema.DatasetError` naming the file rather than yielding a
short inventory, because an inventory that quietly lost a router would produce
a training set whose label space is a subset of the real one — and a router
model is judged precisely on whether its labels cover the space it will be
asked about.

This module reads source and writes JSON. It never reads a credential, never
imports the application, and never calls anything outside ``ast``, ``json`` and
``pathlib``.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ml.datasets.schema import DatasetError, DataValidationError, stable_json_dumps

#: Version of the capability-inventory record shape. Bump on any field rename or
#: on a change to how routes, enums or permissions are harvested, because a
#: consumer cannot tell a real reduction in the surface from a parser that
#: stopped finding routes.
CAPABILITY_INVENTORY_VERSION = "nexo_capabilities.v1"

#: The decorator verbs that actually register a route. ``include_router`` and
#: friends are deliberately absent: a sub-router inclusion is not an endpoint,
#: and counting it would inflate the total with a number no client can call.
_HTTP_VERBS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})

#: Location of each enum relative to the ``app`` package directory.
_ENUM_SOURCE = ("models", "enums.py")

#: The enum classes harvested from :data:`_ENUM_SOURCE`, by class name.
_ENUM_CLASSES = ("RecommendationType", "RiskType", "ActivityEvent")

#: Location of the permission vocabulary relative to the ``app`` package.
_PERMISSION_SOURCE = ("core", "permissions.py")

_PERMISSION_CLASS = "Permission"


@dataclass(frozen=True, slots=True)
class Route:
    """One declared HTTP endpoint.

    ``path`` is the router prefix joined to the decorator's path argument, which
    is what a client actually calls minus the ``/api/v1`` mount. ``module`` is
    the file stem the endpoint is declared in and ``handler`` the function name,
    because a training example that has to name a failure needs to say *where*,
    not just what.
    """

    method: str
    path: str
    module: str
    handler: str

    @property
    def domain(self) -> str:
        """The entity this route acts on.

        Taken as the first non-empty segment of the path, which is the router
        prefix for every router in this application — including ``health``,
        whose router declares no prefix and whose single route path supplies its
        own first segment. A route that carries no segment at all (a router
        mounted straight at the version root with a bare ``"/"``) yields the
        empty string rather than a fabricated name.
        """
        for segment in self.path.split("/"):
            if segment:
                return segment
        return ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise deterministically.

        Returns:
            A JSON-ready mapping. ``domain`` is derived, but it is written out
            because the saved inventory is read by humans and by other tools
            that should not have to re-derive the rule to group a route.
        """
        return {
            "method": self.method,
            "path": self.path,
            "module": self.module,
            "handler": self.handler,
            "domain": self.domain,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Route:
        """Rebuild a route from its serialised form.

        Args:
            raw: A decoded JSON object.

        Returns:
            The parsed route.

        Raises:
            DataValidationError: A required field is missing or malformed.
        """
        fields = {}
        for key in ("method", "path", "module", "handler"):
            value = raw.get(key)
            if not isinstance(value, str) or not value.strip():
                raise DataValidationError(
                    f"route {key!r} must be a non-empty string, got {value!r}"
                )
            fields[key] = value
        return cls(**fields)


@dataclass(frozen=True, slots=True)
class CapabilityInventory:
    """The complete label space Phase 10 may train against.

    The route tuple is sorted by ``(path, method)`` at build time so the saved
    inventory hashes to the same value on an unchanged surface, which is what
    lets a training run manifest assert that its inputs did not move.

    ``inventory_version`` travels with the record rather than being implied by
    the class, so a file written by an older harvest is refused rather than
    read as if it were current.
    """

    routes: tuple[Route, ...]
    recommendation_types: tuple[str, ...]
    risk_types: tuple[str, ...]
    permissions: tuple[str, ...]
    activity_events: tuple[str, ...]
    inventory_version: str = CAPABILITY_INVENTORY_VERSION

    def routes_by_module(self) -> dict[str, tuple[Route, ...]]:
        """Group the routes by the module that declares them.

        Returns:
            Module name to its routes. Module names are sorted so the grouping
            is stable across runs.
        """
        grouped: dict[str, list[Route]] = {}
        for route in self.routes:
            grouped.setdefault(route.module, []).append(route)
        return {name: tuple(grouped[name]) for name in sorted(grouped)}

    def module_route_count(self) -> dict[str, int]:
        """Count routes per module.

        Returns:
            Module name to route count, in the same order as
            :meth:`routes_by_module`.
        """
        return {name: len(routes) for name, routes in self.routes_by_module().items()}

    def total_routes(self) -> int:
        """The number of declared endpoints.

        Returns:
            The route count.
        """
        return len(self.routes)

    def entities(self) -> tuple[str, ...]:
        """The distinct entity names the surface acts on.

        Returns:
            Sorted domain names, one per router family.
        """
        return tuple(sorted({route.domain for route in self.routes if route.domain}))

    def verbs(self) -> tuple[str, ...]:
        """The action vocabulary: HTTP verbs plus the proposable actions.

        A request is more than a verb and a noun — *"reschedule my Tuesday
        task"*, *"break this down"*, *"block time for it"* are all actions NEXUS
        can be asked to take, and none of them appears in an HTTP method name.
        Merging the two sets is what lets a caller treat "what can this thing
        do" as one question rather than two.

        Returns:
            Sorted action names: every HTTP verb used by a declared route, plus
            every recommendation and risk type value.
        """
        harvested: set[str] = {route.method for route in self.routes}
        harvested.update(self.recommendation_types)
        harvested.update(self.risk_types)
        return tuple(sorted(harvested))

    def to_dict(self) -> dict[str, Any]:
        """Serialise deterministically.

        Returns:
            A JSON-ready mapping with sorted keys once passed through
            :func:`~ml.datasets.schema.stable_json_dumps`.
        """
        return {
            "inventory_version": self.inventory_version,
            "routes": [route.to_dict() for route in self.routes],
            "recommendation_types": list(self.recommendation_types),
            "risk_types": list(self.risk_types),
            "permissions": list(self.permissions),
            "activity_events": list(self.activity_events),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CapabilityInventory:
        """Rebuild an inventory from its serialised form.

        Args:
            raw: A decoded JSON object.

        Returns:
            The parsed inventory.

        Raises:
            DataValidationError: The version is unknown, or a field is missing or
                malformed. An unrecognised version is refused rather than
                best-effort parsed, because a field silently dropped here is a
                whole slice of the label space that quietly stops existing.
        """
        version = raw.get("inventory_version")
        if version != CAPABILITY_INVENTORY_VERSION:
            raise DataValidationError(
                f"inventory_version must be {CAPABILITY_INVENTORY_VERSION!r}, got {version!r}"
            )

        raw_routes = raw.get("routes", [])
        if not isinstance(raw_routes, list):
            raise DataValidationError(f"routes must be a list, got {raw_routes!r}")
        routes = []
        for entry in raw_routes:
            if not isinstance(entry, Mapping):
                raise DataValidationError(f"each route must be an object, got {entry!r}")
            routes.append(Route.from_dict(entry))

        vocabularies: dict[str, tuple[str, ...]] = {}
        for key in ("recommendation_types", "risk_types", "permissions", "activity_events"):
            values = raw.get(key, [])
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                raise DataValidationError(f"{key} must be a list of strings, got {values!r}")
            vocabularies[key] = tuple(values)

        return cls(
            routes=tuple(routes),
            recommendation_types=vocabularies["recommendation_types"],
            risk_types=vocabularies["risk_types"],
            permissions=vocabularies["permissions"],
            activity_events=vocabularies["activity_events"],
            inventory_version=CAPABILITY_INVENTORY_VERSION,
        )


def _parse_source(path: Path) -> ast.Module:
    """Parse a Python file into an AST.

    Args:
        path: The file to parse.

    Returns:
        The parsed module.

    Raises:
        DatasetError: The file is missing, unreadable, or does not parse.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DatasetError(f"cannot read capability source {path}: {exc}") from exc
    try:
        return ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise DatasetError(f"cannot parse capability source {path}: {exc}") from exc


def _string_constant(node: ast.expr, path: Path, description: str) -> str:
    """Read a literal string out of an AST node.

    Args:
        node: The node expected to hold a string constant.
        path: The source file being parsed, for the error message.
        description: What the node was supposed to be, for the error message.

    Returns:
        The string value.

    Raises:
        DatasetError: The node is not a string literal.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    raise DatasetError(f"{path}: expected a string literal for {description}, got {ast.dump(node)}")


def _join_paths(prefix: str, suffix: str) -> str:
    """Join a router prefix and a decorator path into one clean path.

    A router declares its prefix and each route its own suffix, and the two are
    formatted independently — so ``"/tasks"`` plus ``"/"`` has to come out as
    ``"/tasks"`` rather than ``"/tasks/"`` or ``"//"``.

    Args:
        prefix: The router's ``prefix=`` keyword.
        suffix: The route decorator's first positional argument.

    Returns:
        The joined path, leading slash, no empty or duplicate segments.
    """
    segments = [seg for seg in f"{prefix}/{suffix}".split("/") if seg]
    return "/" + "/".join(segments)


def _router_prefix(tree: ast.Module, path: Path) -> str | None:
    """Find the module-level ``router = APIRouter(...)`` assignment.

    Only the attribute names a *verb* can be called on are harvested, which is
    what separates a real router from the ``api_v1_router`` that aggregates
    them in ``router.py``.

    Args:
        tree: The parsed router module.
        path: The source file, for the error message.

    Returns:
        The declared prefix, or None when the module declares no router.

    Raises:
        DatasetError: A router exists but its prefix is not a string literal.
    """
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == "router" for t in targets):
            continue
        if not isinstance(value, ast.Call):
            continue
        for keyword in value.keywords:
            if keyword.arg == "prefix":
                return _string_constant(keyword.value, path, "the APIRouter prefix")
        return ""
    return None


def _iter_route_decorators(tree: ast.Module) -> Iterator[tuple[str, str, ast.Call]]:
    """Yield every ``@router.<verb>(...)`` decoration in a module.

    Walking function definitions rather than bare calls is deliberate: the
    decoration is the only place a handler name exists, and a call that merely
    looks like a route decorator but decorates nothing is not an endpoint.

    Args:
        tree: The parsed router module.

    Yields:
        ``(handler name, lowercased verb, the decorator call)`` per route.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not isinstance(func, ast.Attribute) or func.attr not in _HTTP_VERBS:
                continue
            if not isinstance(func.value, ast.Name) or func.value.id != "router":
                continue
            yield node.name, func.attr.lower(), decorator


def _parse_routes(api_dir: Path) -> tuple[Route, ...]:
    """Harvest every declared endpoint under ``app/api/v1``.

    Args:
        api_dir: The versioned API package directory.

    Returns:
        Every route found, sorted by ``(path, method)``.

    Raises:
        DatasetError: The directory is missing, or a file that declares routes
            is unreadable, unparseable, or hides a path behind a computed
            expression instead of a literal.
    """
    if not api_dir.is_dir():
        raise DatasetError(f"capability source directory not found: {api_dir}")

    routes: list[Route] = []
    for path in sorted(api_dir.glob("*.py")):
        tree = _parse_source(path)
        prefix = _router_prefix(tree, path)
        decorations = list(_iter_route_decorators(tree))
        if prefix is None:
            if decorations:
                raise DatasetError(f"{path}: routes are declared but no module-level router is")
            continue
        for handler, verb, call in decorations:
            if not call.args:
                raise DatasetError(f"{path}: @router.{verb} on {handler} has no path argument")
            suffix = _string_constant(call.args[0], path, f"the path of {handler}")
            routes.append(
                Route(
                    method=verb, path=_join_paths(prefix, suffix), module=path.stem, handler=handler
                )
            )
    return tuple(sorted(routes, key=lambda route: (route.path, route.method)))


def _parse_str_enum(tree: ast.Module, class_name: str, path: Path) -> tuple[str, ...]:
    """Read the string members of a :class:`~enum.StrEnum` subclass.

    Member *values* are read, not member names, because the value is the stable
    string the backend stores and compares against; the name is only the
    Python-side spelling of it.

    Args:
        tree: The parsed module.
        class_name: The enum class to read.
        path: The source file, for the error message.

    Returns:
        Member values in declaration order.

    Raises:
        DatasetError: The class is absent, empty, or has a member whose value
            is not a string literal.
    """
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        values: list[str] = []
        for statement in node.body:
            targets: list[ast.expr] = []
            value: ast.expr | None = None
            if isinstance(statement, ast.Assign):
                targets = list(statement.targets)
                value = statement.value
            elif isinstance(statement, ast.AnnAssign):
                targets = [statement.target]
                value = statement.value
            else:
                continue
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if not names or names[0].startswith("_"):
                continue
            values.append(_string_constant(value, path, f"{class_name}.{names[0]}"))
        if not values:
            raise DatasetError(f"{path}: {class_name} declares no string members")
        return tuple(values)
    raise DatasetError(f"{path}: class {class_name} not found")


def build_capability_inventory(app_dir: Path) -> CapabilityInventory:
    """Harvest the capability inventory from the backend source.

    Args:
        app_dir: Path to the ``app`` package directory, e.g.
            ``backend/app``. Nothing under it is imported.

    Returns:
        The inventory, with routes sorted deterministically.

    Raises:
        DatasetError: Any source file is missing, unreadable, unparseable, or
            declares something this harvest cannot read. Raised rather than
            swallowed, because a silently short inventory produces a training
            set whose label space is quietly incomplete.
    """
    if not app_dir.is_dir():
        raise DatasetError(f"app package directory not found: {app_dir}")

    routes = _parse_routes(app_dir / "api" / "v1")
    if not routes:
        raise DatasetError(f"no routes declared under {app_dir / 'api' / 'v1'}")

    enums_path = app_dir.joinpath(*_ENUM_SOURCE)
    enums_tree = _parse_source(enums_path)
    harvested = {name: _parse_str_enum(enums_tree, name, enums_path) for name in _ENUM_CLASSES}

    permissions_path = app_dir.joinpath(*_PERMISSION_SOURCE)
    permissions_tree = _parse_source(permissions_path)
    permissions = _parse_str_enum(permissions_tree, _PERMISSION_CLASS, permissions_path)

    return CapabilityInventory(
        routes=routes,
        recommendation_types=harvested["RecommendationType"],
        risk_types=harvested["RiskType"],
        activity_events=harvested["ActivityEvent"],
        permissions=permissions,
    )


def save_inventory(inv: CapabilityInventory, path: Path) -> None:
    """Write an inventory as canonical JSON.

    Args:
        inv: The inventory to write.
        path: Destination file. Parent directories are created.

    Raises:
        DatasetError: The destination could not be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(stable_json_dumps(inv.to_dict()) + "\n", encoding="utf-8", newline="\n")
    except OSError as exc:
        raise DatasetError(f"cannot write capability inventory to {path}: {exc}") from exc


def load_inventory(path: Path) -> CapabilityInventory:
    """Read an inventory back.

    Args:
        path: The file to read.

    Returns:
        The parsed inventory.

    Raises:
        DatasetError: The file is missing or is not a JSON object.
        DataValidationError: The file is not a readable inventory record.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DatasetError(f"cannot read capability inventory {path}: {exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DatasetError(f"{path}: malformed JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise DatasetError(f"{path}: expected a JSON object")
    return CapabilityInventory.from_dict(raw)


__all__ = [
    "CAPABILITY_INVENTORY_VERSION",
    "CapabilityInventory",
    "Route",
    "build_capability_inventory",
    "load_inventory",
    "save_inventory",
]
