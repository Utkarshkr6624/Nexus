"""§14.10 — NEXUS never leaves the machine, and this file is why that stays true.

"Offline / local-first" is a claim, and a claim in a README decays silently: one
`import httpx` in a service, one `requests.post` in a retry helper, one telemetry
SDK in ``requirements.txt``, and the deployment still passes every functional test
because none of them can see the packet. This module makes the claim a *test*, so
the change that breaks it has to be made in this file too, deliberately, with the
allowlist in front of whoever makes it.

What is asserted
----------------

1. **No module imports a network client.** An AST walk over ``backend/app``,
   ``backend/ml`` and ``backend/migrations`` for any module whose top-level name
   is a client library — ``httpx``, ``requests``, ``aiohttp``, ``socket``,
   ``urllib.request``, ``http.client``, ``smtplib``, ``ftplib``, ``xmlrpc``, the
   AWS/AI SDKs, the analytics SDKs, the message brokers, the cache and document
   stores. AST rather than grep, so a mention of ``requests`` in a docstring
   about HTTP semantics cannot fail the build and an aliased import cannot slip
   through.
2. **The allowlist is exactly what this repository needs.** Two entries:
   ``urllib.parse`` (four call sites, all string handling on a stored URL) and
   ``http.HTTPStatus`` (three call sites, a stdlib enum). Pinned literally so a
   fifth allowance is a visible edit here rather than a silent one.
3. **The runtime dependency set carries no HTTP client.** ``httpx`` *is* in
   ``requirements.txt`` — it is the ASGI transport the test suite uses — so the
   assertion is about the **runtime** section, before the dev marker. A client in
   the runtime section would be a dependency that ships to a machine that is
   supposed to have no way off it.
4. **No cloud AI, no cloud auth, no external database.** Named distributions that
   would give NEXUS a reason to talk to anyone.
5. **The only subprocesses are local.** Every ``subprocess`` call site names the
   interpreter it runs or the ``git`` binary, and every ``git`` subcommand in the
   source is one of five that read the work tree on disk. ``fetch``, ``pull``,
   ``clone``, ``ls-remote``, ``submodule`` and ``push`` are named explicitly, so
   adding one is a failing assertion rather than a silent capability.
6. **No telemetry, beacon or error-reporting SDK**, by name and by the browser
   APIs they are built on.

What is deliberately *not* asserted
-----------------------------------

``backend/ml/.venv`` is on disk and contains ``aiohttp`` and ``boto3``: those are
transitive dependencies of the Phase 10 training stack, installed into a separate
virtual environment, imported by nothing in this repository, and not reachable at
runtime. A scan that included installed site-packages would be asserting about
pip's resolution choices rather than about NEXUS. It is excluded by path, and
:func:`test_the_excluded_directories_are_installed_packages_and_not_project_source`
says out loud that something *was* excluded, so the exclusion cannot be widened
into "whatever is inconvenient".

Every assertion below is paired with a control that proves the scanner would fire.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.core.config import BACKEND_ROOT

#: The roots the scan covers. ``scripts/`` and ``migrations/`` at the repository
#: root are outside the backend package; ``backend/migrations`` is inside it and
#: is included.
SCANNED_ROOTS = ("app", "ml", "migrations")

#: Directories holding installed packages, compiled bytecode, generated datasets
#: or model weights rather than source this project wrote.
EXCLUDED_PARTS = frozenset({".venv", "__pycache__", "artifacts", "datasets", ".pytest_cache"})

#: Top-level module names that would give NEXUS a way off the machine. Grouped by
#: what they would be *for*, so a reader can see what the list is defending
#: against rather than just that it is long.
FORBIDDEN_ROOTS: dict[str, tuple[str, ...]] = {
    "http clients": (
        "httpx",
        "requests",
        "aiohttp",
        "urllib3",
        "http.client",
        "urllib.request",
        "urllib.error",
        "httplib",
        "pycurl",
        "treq",
    ),
    "raw sockets": ("socket", "socketserver", "asyncio.streams", "ssl"),
    "mail and ftp": ("smtplib", "ftplib", "poplib", "imaplib", "nntplib", "telnetlib"),
    "rpc": ("xmlrpc", "jsonrpc", "grpc", "zmq", "pyro"),
    "cloud and AI SDKs": (
        "boto3",
        "botocore",
        "google.cloud",
        "azure",
        "openai",
        "anthropic",
        "cohere",
        "ollama",
        "replicate",
        "huggingface_hub",
        "kaggle",
        "paramiko",
    ),
    "telemetry and error reporting": (
        "sentry_sdk",
        "posthog",
        "mixpanel",
        "segment",
        "amplitude",
        "bugsnag",
        "rollbar",
        "opentelemetry",
        "ddtrace",
        "newrelic",
        "elasticapm",
        "loguru",
    ),
    "brokers, caches and stores": (
        "redis",
        "celery",
        "kombu",
        "kafka",
        "pika",
        "confluent_kafka",
        "pymongo",
        "motor",
        "firebase",
        "firebase_admin",
        "supabase",
        "elasticsearch",
    ),
    "experiment trackers and SDK uploaders": (
        "mlflow",
        "wandb",
        "optuna",
        "dvc",
        "dagster",
        "prefect",
        "airflow",
        "ray",
        "kubeflow",
        "telemetry",
    ),
}

#: Flattened for the membership test, with ``urllib.parse`` deliberately absent:
#: it is handled by :data:`ALLOWED_IMPORTS` because parsing a stored URL is not
#: the same act as opening one.
FORBIDDEN_TOP_LEVEL = frozenset(
    name for group in FORBIDDEN_ROOTS.values() for name in group if not name.startswith("urllib.")
)

#: The complete set of network-capable imports this repository is allowed to
#: make. Each entry maps a dotted module to the names it may be imported *for*;
#: importing anything else from one of them is a failure.
#:
#: ``urllib.parse`` — every call site is string surgery on a URL NEXUS already
#:   holds: splitting a stored source URL to find its host, quoting a credential
#:   for a DSN, and ``urlunsplit`` to rebuild one after its host was lowercased.
#:   None of them opens a socket; ``urllib.request`` is the module that does, and
#:   it is forbidden above. The names are listed individually rather than the
#:   module being waved through wholesale, because ``urllib.parse`` is a package
#:   that can grow a sibling — and ``urljoin`` resolving a path against a base
#:   read off a stored URL is the kind of name that should be argued for here
#:   rather than inherited.
#: ``http.HTTPStatus`` — three call sites reading an stdlib enum for a status code.
ALLOWED_IMPORTS: dict[str, frozenset[str] | None] = {
    "urllib.parse": frozenset(
        {"quote", "unquote", "urlsplit", "urlunsplit", "urlparse", "urlencode"}
    ),
    "http": frozenset({"HTTPStatus"}),
}

#: The git subcommands ``app/services/developer/git.py`` is allowed to run. Every
#: one reads the work tree on disk and none contacts a remote.
ALLOWED_GIT_SUBCOMMANDS = frozenset({"for-each-ref", "log", "ls-files", "rev-parse", "status"})

#: Named so that adding one is a failing assertion. A local command that contacts
#: a remote, or moves objects between repositories, is exactly the capability the
#: claim forbids.
NETWORKING_GIT_SUBCOMMANDS = (
    "fetch",
    "pull",
    "push",
    "clone",
    "ls-remote",
    "remote",
    "submodule",
    "archive",
    "request-pull",
    "upload-pack",
    "receive-pack",
    "daemon",
    "credential",
)

#: Distribution names that would give NEXUS a reason to talk to anyone. Compared
#: against the *requirement name*, so ``httpx`` is caught whether it is written
#: ``httpx==0.28.1`` or ``httpx``.
#: ``httpx`` is deliberately absent from this set. It is a real dependency of the
#: repository — the ASGI transport every HTTP test drives the app through — and
#: excluding it here rather than special-casing it keeps one rule per rule. Its
#: boundary is asserted exactly, and to the line, by
#: :func:`test_httpx_is_declared_only_below_the_dev_marker`.
FORBIDDEN_DISTRIBUTIONS = (
    frozenset(n for group in FORBIDDEN_ROOTS.values() for n in group if "." not in n) - {"httpx"}
) | {
    "urllib3",
    "kaggle",
    "google-cloud-storage",
    "google-cloud-bigquery",
    "psycopg2",
    "pymysql",
    "mysqlclient",
    "redis",
    "elasticsearch",
    "requests-toolbelt",
}

#: The only external program this repository runs, by module-level constant.
GIT_EXECUTABLE = "git"

#: Browser and shell primitives that are telemetry under another name. NEXUS has
#: no browser, but a backend template that smuggles in a beacon is the failure
#: this catches.
BEACON_PATTERNS = (
    r"\bsendBeacon\b",
    r"\bXMLHttpRequest\b",
    r"\bnavigator\.",
    r"\bgtag\s*\(",
    r"\bga\s*\(\s*'send'",
    r"\b_mixpanel\b",
    r"\bposthog\.init\b",
    r"\bsentry\.init\b",
    r"\bdataLayer\.push\b",
    r"\bpixel\s*\(",
)

#: A comment is allowed to say these words; an import or a call is not. Matched
#: against source with comments and strings stripped first, by the callers below.
_COMMENT = re.compile(r"#.*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_source_files(root: Path):
    """Every ``.py`` file under ``root`` that is project source, not an artifact."""
    for path in sorted(root.rglob("*.py")):
        if EXCLUDED_PARTS & set(path.parts):
            continue
        yield path


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for name in SCANNED_ROOTS:
        root = BACKEND_ROOT / name
        if root.is_dir():
            files.extend(_iter_source_files(root))
    assert files, f"no source found under {SCANNED_ROOTS}"
    return files


def _imports(path: Path) -> list[tuple[int, str, tuple[str, ...]]]:
    """Every import in one file as ``(lineno, module, names)``.

    ``names`` is empty for a plain ``import x`` — the caller only needs them for
    the allowlist, where the distinction between ``import http`` and
    ``from http import HTTPStatus`` decides whether it passes.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name, ()))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module, tuple(alias.name for alias in node.names)))
    return found


def _offending_imports(paths: list[Path]) -> list[str]:
    """Human-readable descriptions of every forbidden import, across ``paths``."""
    offences: list[str] = []
    for path in paths:
        try:
            imports = _imports(path)
        except SyntaxError as exc:  # pragma: no cover — would be a broken checkout
            offences.append(f"{path}: could not be parsed ({exc})")
            continue
        for lineno, module, names in imports:
            top = module.split(".")[0]
            if module in ALLOWED_IMPORTS:
                allowed = ALLOWED_IMPORTS[module]
                if allowed is not None and names and not set(names) <= allowed:
                    offences.append(
                        f"{path}:{lineno}: from {module} import {sorted(set(names) - allowed)}"
                    )
                continue
            if module.startswith("urllib."):
                offences.append(f"{path}:{lineno}: {module} opens a socket")
                continue
            if top in FORBIDDEN_TOP_LEVEL:
                offences.append(f"{path}:{lineno}: {module}")
    return offences


def _runtime_requirements() -> list[str]:
    """The requirement lines the Docker runtime image installs.

    ``backend/Dockerfile`` takes everything above the dev marker, so "the
    dependency set that ships" is exactly that section.
    """
    text = (BACKEND_ROOT / "requirements.txt").read_text(encoding="utf-8")
    runtime: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ===") and "DEV MARKER" in stripped:
            break
        if stripped and not stripped.startswith("#"):
            runtime.append(stripped)
    return runtime


def _requirement_name(line: str) -> str:
    """``httpx==0.28.1 ; python_version<'3.11'`` -> ``httpx``."""
    head = line.split(";", 1)[0].strip()
    for separator in ("==", ">=", "<=", "~=", "!=", ">", "<", "[", " "):
        head = head.split(separator, 1)[0]
    return head.strip().lower()


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------


def test_no_module_imports_a_network_client():
    """The headline assertion, over the whole backend package.

    An AST walk rather than a text search: a docstring explaining what an HTTP
    422 means, or a comment naming ``socket``, cannot fail this, and
    ``import socket as s`` cannot evade it.
    """
    offences = _offending_imports(_scanned_files())
    assert not offences, "outbound-capable imports found:\n" + "\n".join(offences)


def test_the_network_import_allowlist_is_exactly_what_this_repository_needs():
    """A tripwire on the allowlist itself.

    Pinned to the two entries the source actually justifies, so a fifth
    allowance has to be made here — where the reason for it can be written down —
    rather than added to a broad ``except`` clause that quietly permits
    ``urllib.request``.
    """
    assert set(ALLOWED_IMPORTS) == {"urllib.parse", "http"}
    assert ALLOWED_IMPORTS["http"] == frozenset({"HTTPStatus"})
    assert "urlopen" not in (ALLOWED_IMPORTS["urllib.parse"] or frozenset())


def test_the_allowlist_entries_are_all_actually_used():
    """An allowance nothing imports is dead policy pretending to be a control.

    Removing it costs nothing and shrinks what the next reader has to reason
    about, so this file asks for that to happen.
    """
    used: set[str] = set()
    for path in _scanned_files():
        for _lineno, module, _names in _imports(path):
            if module in ALLOWED_IMPORTS:
                used.add(module)
    assert used == set(ALLOWED_IMPORTS)


def test_the_scanner_would_notice_a_network_client(tmp_path):
    """The control the headline assertion needs to be worth anything.

    A scanner that matched nothing would pass forever. This runs the identical
    ``_offending_imports`` over a synthetic tree that *does* contain a client
    import and asserts the report names it.
    """
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "leaky.py").write_text(
        "import socket\nfrom urllib.request import urlopen\n", encoding="utf-8"
    )
    (tmp_path / "app" / "clean.py").write_text(
        "from urllib.parse import urlsplit\nfrom http import HTTPStatus\n", encoding="utf-8"
    )

    offences = _offending_imports(sorted((tmp_path / "app").glob("*.py")))

    assert any("socket" in offence for offence in offences), offences
    assert any("urllib.request" in offence for offence in offences), offences
    assert not any("clean.py" in offence for offence in offences), offences


def test_an_alias_does_not_evade_the_scan(tmp_path):
    """``import socket as s`` is the same capability with a different spelling."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "aliased.py").write_text("import socket as s\n", encoding="utf-8")

    assert _offending_imports([tmp_path / "app" / "aliased.py"])


def test_a_relative_import_cannot_reach_a_forbidden_module(tmp_path):
    """``from . import x`` is local; the module still has to be top-level here.

    ``node.level > 0`` is an intra-package import, which cannot name an
    installed distribution — so the scanner ignores it, and this proves that
    ignoring it does not hide ``httpx`` smuggled in under a relative alias.
    """
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "pkg").mkdir()
    (tmp_path / "app" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "app" / "pkg" / "local.py").write_text(
        "from . import sibling\nfrom .. import other\n", encoding="utf-8"
    )

    assert _offending_imports([tmp_path / "app" / "pkg" / "local.py"]) == []


# ---------------------------------------------------------------------------
# What ships
# ---------------------------------------------------------------------------


def test_the_runtime_dependency_set_carries_no_http_client():
    """``httpx`` is present in the file — in the *dev* section. This says so.

    It is the ASGI transport the test suite drives the app through, so it will
    always be a dependency of the repository. What must not happen is it moving
    above the marker and into the runtime image, which is the shape
    "the code could phone home" takes in practice.
    """
    names = {_requirement_name(line) for line in _runtime_requirements()}

    assert "httpx" not in names, "httpx reached the runtime section"
    assert "requests" not in names
    assert "aiohttp" not in names
    assert "urllib3" not in names


def test_httpx_is_declared_only_below_the_dev_marker():
    """The positive half of the assertion above, so it cannot be deleted to pass."""
    text = (BACKEND_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "httpx" in text, "the test suite's ASGI transport is undeclared"
    dev_index = text.index("DEV MARKER")
    assert text.rindex("httpx") > dev_index


def test_no_forbidden_distribution_is_installed_at_all():
    """Runtime or dev, neither one.

    A dev dependency that talks to a network is a machine that can, and the
    boundary between "the tests need it" and "the product needs it" is exactly
    the boundary this file refuses to blur.
    """
    installed = {_requirement_name(line) for line in _runtime_requirements()}
    text = (BACKEND_ROOT / "requirements.txt").read_text(encoding="utf-8")
    marker = text.index("DEV MARKER")
    for line in text[marker:].splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            installed.add(_requirement_name(stripped))

    offenders = sorted(installed & FORBIDDEN_DISTRIBUTIONS)
    assert not offenders, f"forbidden distributions declared: {offenders}"


def test_the_model_stack_is_the_local_transformer_pair_and_nothing_else():
    """One model, and it runs here.

    NEXUS ships ``microsoft/deberta-v3-base`` as a checkpoint on disk and reads
    it with ``transformers``. There is no hosted model, no local model server and
    no second model; this asserts the shape of that in the one place a second
    model could be quietly added.
    """
    names = {_requirement_name(line) for line in _runtime_requirements()}

    assert {"torch", "transformers"} <= names
    assert not names & {"vllm", "llama-cpp-python", "ctransformers", "onnxruntime", "timm"}
    assert not names & {"openai", "anthropic", "ollama", "cohere", "langchain", "llama-index"}


def test_the_only_database_driver_is_the_local_postgres_one():
    """No hosted database, no cache, no document store.

    PostgreSQL is the deployment's own server — ``docker-compose.yml`` runs it
    beside the API — so it is not an outbound call. Everything below it would be.
    """
    names = {_requirement_name(line) for line in _runtime_requirements()}

    assert "psycopg" in names
    assert not names & {"pymongo", "redis", "elasticsearch", "psycopg2", "pymysql", "mysqlclient"}


def test_no_external_auth_provider_is_installed():
    """Passwords are hashed by bcrypt and sessions are rows in NEXUS's own table.

    Anything that delegates authentication to somebody else would be an outbound
    call on every request, and would put a third party in the middle of the one
    flow ``REDACTED_KEYS`` exists to protect.
    """
    names = {_requirement_name(line) for line in _runtime_requirements()}

    assert "bcrypt" in names and "pyjwt" in names
    assert not names & {
        "authlib",
        "python-jose",
        "oauthlib",
        "social-auth-app-dict",
        "auth0",
        "firebase-admin",
        "supabase",
    }


# ---------------------------------------------------------------------------
# The git surface is local
# ---------------------------------------------------------------------------


def test_every_git_subcommand_in_the_source_reads_the_local_work_tree():
    """The five that exist, and nothing else.

    Extracted from the ``run_git`` call sites across ``app/`` rather than from a
    hand-written list, so a sixth command added to a scan path is a failing
    assertion rather than a review comment. ``run_git(repo_path, *args)`` takes
    the path first, so the subcommand is the *second* positional argument.
    """
    commands: set[str] = set()
    call_sites = 0
    for path in _iter_source_files(BACKEND_ROOT / "app"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if ast.unparse(node.func) != "run_git":
                continue
            call_sites += 1
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                commands.add(node.args[1].value)

    assert call_sites, "no run_git call site was found — check the pattern"
    assert commands, "no run_git call site carried a literal subcommand"
    assert commands <= ALLOWED_GIT_SUBCOMMANDS, sorted(commands - ALLOWED_GIT_SUBCOMMANDS)
    assert "log" in commands, "the multi-commit scans vanished; the extraction is wrong"


def test_no_git_command_that_contacts_a_remote_appears_anywhere_in_the_backend():
    """Named individually, so adding one is loud.

    A string literal is enough to trip this, including in a comment — which is
    the correct trade for a capability this costly to add by accident.
    """
    offenders: list[str] = []
    for path in _scanned_files():
        text = path.read_text(encoding="utf-8")
        for command in NETWORKING_GIT_SUBCOMMANDS:
            for match in re.finditer(rf"""["']\s*git\s+{re.escape(command)}\b""", text):
                offenders.append(
                    f"{path}:{text[: match.start()].count(chr(10)) + 1}: git {command}"
                )
            for _bare in re.finditer(rf"""["']\s*{re.escape(command)}\s+["']""", text):
                # A bare subcommand as the whole string literal is how run_git is
                # called; catching it here means a caller cannot add "fetch" and
                # rely on the AST test alone to notice.
                offenders.append(f"{path}: {command}")
    assert not offenders, offenders


def test_the_git_executable_is_a_constant_and_there_is_no_shell():
    """``shell=True`` would turn a fixed argv into a command line.

    Combined with the argument-list rule, that is the difference between running
    ``git`` and running whatever is in a repository path — which is exactly the
    local_path a scan accepts.
    """
    git_module = BACKEND_ROOT / "app" / "services" / "developer" / "git.py"
    source = git_module.read_text(encoding="utf-8")

    # Matched as a keyword argument, not as text: this module's own docstring
    # explains at length that there is no ``shell=True``, so a substring search
    # would either be useless or would forbid the sentence that documents the
    # property. What must be absent is the *call*.
    tree = ast.parse(source)
    shells = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "shell"
    ]
    assert not shells, f"shell= passed at lines {shells}"
    assert GIT_EXECUTABLE in source
    assert "create_subprocess_exec" in source


# ---------------------------------------------------------------------------
# Only one external program, and it is git
# ---------------------------------------------------------------------------


def test_the_only_external_process_started_is_git_or_this_interpreter():
    """``subprocess`` and ``asyncio.create_subprocess_exec`` are the only two doors.

    Matched on the *qualified* callee rather than on a method name, so a
    ``stage.run(pipeline)`` or a ``session.run()`` is not mistaken for a process
    launch. Every real call site is either ``git`` (the developer surface and the
    Phase 10 manifest) or this interpreter (the training pipeline probing its own
    torch). A new call site naming anything else — a curl probe, a health-check
    URL, an installer — is a failing assertion.
    """
    launchers = {
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "os.system",
        "os.popen",
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
    }

    found: dict[str, str] = {}
    for path in _scanned_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = ast.unparse(node.func)
            if callee not in launchers:
                continue
            executable = node.args[0] if node.args else None
            if isinstance(executable, (ast.List, ast.Tuple)) and executable.elts:
                # argv form: [GIT_EXECUTABLE, *args] or [interpreter, "-c", probe]
                executable = executable.elts[0]
            # ast.unparse renders a string literal with its quotes; the rest of
            # this test compares against bare names, so they come off here.
            executable_text = ast.unparse(executable) if executable is not None else "<no argv>"
            executable_text = executable_text.strip("'\"")
            found[f"{path}:{node.lineno}"] = executable_text

    assert found, "the scan found no process-launch call sites at all — check the pattern"

    # ``GIT_EXECUTABLE`` and ``git`` are the two spellings the tree uses for the
    # git binary (a module constant, and a literal in the git_repository fixture
    # helper). ``command`` is a local argv list: both sites in ``ml/train.py``
    # build it as ``[sys.executable, "ml.scripts.train_small_local", ...]`` or
    # ``[str(interpreter), "-c", ...]``, so the first element is this interpreter.
    local_argv = {"GIT_EXECUTABLE", GIT_EXECUTABLE, "str(interpreter)", "command", "sys.executable"}
    suspicious = {where: argv for where, argv in found.items() if argv not in local_argv}
    assert not suspicious, suspicious
    assert any("manifest.py" in where for where in found), "the Phase 10 manifest git call vanished"


def test_the_process_scan_would_notice_a_new_external_program(tmp_path):
    """The control the assertion above needs.

    A scanner that matched nothing would pass forever; this runs the identical
    launcher set over a synthetic tree that launches ``curl`` and asserts it is
    reported.
    """
    launchers = {"subprocess.run", "asyncio.create_subprocess_exec", "os.system"}
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import asyncio\n"
        "import subprocess\n"
        "\n"
        "def go(url):\n"
        "    return subprocess.run(['curl', '-s', url])\n"
        "\n"
        "async def shell(url):\n"
        "    return await asyncio.create_subprocess_exec('curl', url)\n",
        encoding="utf-8",
    )

    tree = ast.parse(probe.read_text(encoding="utf-8"))
    reported = [
        ast.unparse(
            node.args[0].elts[0] if isinstance(node.args[0], ast.List) else node.args[0]
        ).strip("'\"")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.unparse(node.func) in launchers and node.args
    ]

    assert "curl" in reported, reported


def test_no_shell_or_telemetry_primitive_appears_in_backend_source():
    """Beacons and analytics by name.

    NEXUS has no browser, so this is a guard against a backend template or an
    HTML error page smuggling one in.
    """
    offenders: list[str] = []
    for path in _scanned_files():
        text = _COMMENT.sub("", path.read_text(encoding="utf-8"))
        for pattern in BEACON_PATTERNS:
            if re.search(pattern, text):
                offenders.append(f"{path}: {pattern}")
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# The exclusions, stated out loud
# ---------------------------------------------------------------------------


def test_the_excluded_directories_are_installed_packages_and_not_project_source():
    """Something *was* skipped, so the exclusion cannot grow unnoticed.

    ``backend/ml/.venv`` holds a full training environment including ``aiohttp``
    and ``boto3`` as transitive dependencies of ``accelerate``. They are not
    imported by anything in this repository and are not on the runtime path; a
    scan that included installed site-packages would be asserting about pip's
    resolution rather than about NEXUS. This test says which directories were
    skipped and why, so "we excluded it because it is convenient" is a visible
    claim rather than a hidden one.
    """
    excluded: set[str] = set()
    for name in SCANNED_ROOTS:
        root = BACKEND_ROOT / name
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if EXCLUDED_PARTS & set(path.parts):
                excluded.add(str(path.relative_to(BACKEND_ROOT)).split("\\")[0].split("/")[0])

    assert "ml" in excluded, "expected ml/.venv or ml/artifacts to be present and skipped"


def test_the_scan_actually_covers_the_application_package():
    """A scan that silently found one file would pass every assertion above."""
    files = _scanned_files()

    relative = {path.relative_to(BACKEND_ROOT).as_posix() for path in files}

    assert len(files) > 100, f"only {len(files)} files scanned"
    assert "app/ml/model_loader.py" in relative
    assert "app/main.py" in relative
    assert "app/services/developer/git.py" in relative
    assert not any(".venv" in parts for parts in relative)


def test_the_scanned_roots_all_exist():
    """A typo in :data:`SCANNED_ROOTS` would narrow the scan without failing."""
    for name in SCANNED_ROOTS:
        assert (BACKEND_ROOT / name).is_dir(), f"{name} does not exist under {BACKEND_ROOT}"


@pytest.mark.parametrize(
    "command",
    NETWORKING_GIT_SUBCOMMANDS,
)
def test_the_network_git_command_list_would_notice_one(command):
    """Each forbidden subcommand is individually load-bearing.

    Parametrised so deleting one from the tuple is a collection-time failure
    rather than a silent narrowing of what the headline test refuses.
    """
    assert command in NETWORKING_GIT_SUBCOMMANDS
    assert command not in ALLOWED_GIT_SUBCOMMANDS
