#!/usr/bin/env python
"""Prepare a NEXUS checkout for local development.

Idempotent and safe to re-run: an existing ``.env`` is left untouched, an
existing ``backend/.venv`` is reused rather than recreated, and the dependency
installs are the ordinary incremental ones.

    python scripts/bootstrap.py                # everything
    python scripts/bootstrap.py --skip-install # only the checks and .env

Runs on a bare interpreter — it must work before any dependency exists — so it
uses the standard library only. It is safe to run repeatedly.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    BACKEND_DIR,
    ENV_EXAMPLE_FILE,
    ENV_FILE,
    FRONTEND_DIR,
    REPO_ROOT,
    fail,
    info,
    ok,
    venv_python,
    warn,
)

MIN_PYTHON = (3, 13)
MIN_NODE_MAJOR = 20
VENV_DIR = BACKEND_DIR / ".venv"

#: PyTorch publishes its CPU wheels on its own index, not on PyPI. The backend
#: pins ``torch==2.14.1+cpu`` — the exact build Phase 10 trained and evaluated the
#: classifier with — and that local version identifier exists nowhere else, so
#: every install path needs this URL.
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"


def step(title: str) -> None:
    """Print a step heading."""
    print(f"\n== {title} ==", flush=True)


def check_python() -> None:
    """Verify the running interpreter is new enough for the backend."""
    if sys.version_info < MIN_PYTHON:
        required = ".".join(str(part) for part in MIN_PYTHON)
        running = ".".join(str(part) for part in sys.version_info[:3])
        fail(
            f"Python {required}+ is required, this is {running} ({sys.executable}).\n"
            "       Install a current Python 3.13 and re-run with it, e.g.\n"
            "         python3.13 scripts/bootstrap.py"
        )
    ok(f"Python {'.'.join(str(p) for p in sys.version_info[:3])} ({sys.executable})")


def check_node() -> str:
    """Verify Node and npm, returning the npm executable to use."""
    node = shutil.which("node")
    if node is None:
        fail(
            "Node.js was not found on PATH.\n"
            f"       Install Node {MIN_NODE_MAJOR}+ (https://nodejs.org) and open a new terminal."
        )
    major = int(
        subprocess.run(
            [node, "--version"], capture_output=True, text=True, check=True
        ).stdout.lstrip("v").split(".")[0]
    )
    if major < MIN_NODE_MAJOR:
        fail(
            f"Node {MIN_NODE_MAJOR}+ is required, this is v{major} ({node}).\n"
            "       Upgrade Node, then delete frontend/node_modules and re-run."
        )

    npm = shutil.which("npm")
    if npm is None:
        fail(
            "npm was not found on PATH even though Node is installed.\n"
            "       Reinstall Node with npm enabled, then open a new terminal."
        )
    npm_version = subprocess.run(
        [npm, "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    ok(f"Node v{major} and npm {npm_version}")
    return npm


def check_repository() -> None:
    """Verify the layout the scripts depend on is present."""
    required = {
        "backend/requirements.txt": BACKEND_DIR / "requirements.txt",
        "backend/alembic.ini": BACKEND_DIR / "alembic.ini",
        "frontend/package.json": FRONTEND_DIR / "package.json",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        fail(
            "incomplete checkout; missing: " + ", ".join(missing) + "\n"
            f"       Run this script from the NEXUS repository ({REPO_ROOT})."
        )
    ok("repository layout looks complete")


def ensure_env_file() -> None:
    """Create ``.env`` from ``.env.example`` when it does not exist yet."""
    if ENV_FILE.is_file():
        ok(".env already present (not modified)")
        info("edit it to change credentials or ports")
        return
    if not ENV_EXAMPLE_FILE.is_file():
        fail(
            f"neither {ENV_FILE.name} nor {ENV_EXAMPLE_FILE.name} exists.\n"
            "       Restore .env.example from version control, then re-run."
        )
    shutil.copyfile(ENV_EXAMPLE_FILE, ENV_FILE)
    ok(f"created {ENV_FILE.name} from {ENV_EXAMPLE_FILE.name}")
    warn(
        f"{ENV_FILE.name} contains the example SECRET_KEY and password. That is "
        "fine for local development, never use them anywhere else."
    )


def ensure_venv() -> Path:
    """Return the backend virtualenv interpreter, creating the venv if needed."""
    existing = venv_python(VENV_DIR)
    if existing is not None:
        ok(f"reusing virtualenv {VENV_DIR.relative_to(REPO_ROOT)}")
        return existing

    info(f"creating virtualenv {VENV_DIR.relative_to(REPO_ROOT)}")
    completed = subprocess.run(
        [sys.executable, "-m", "venv", str(VENV_DIR)], capture_output=True, text=True
    )
    if completed.returncode != 0:
        fail(
            f"could not create the virtualenv at {VENV_DIR}.\n"
            f"       {completed.stderr.strip() or completed.stdout.strip()}"
        )
    created = venv_python(VENV_DIR)
    if created is None:
        fail(
            f"the virtualenv was created at {VENV_DIR} but no interpreter was "
            f"found in it.\n       Delete {VENV_DIR} and re-run this script."
        )
    return created


def install_backend(python: Path) -> None:
    """Install the pinned backend requirements into the virtualenv."""
    info("installing backend/requirements.txt (pinned, incl. dev tooling)")
    # The torch pin carries a `+cpu` local version, which is published only on
    # PyTorch's own CPU index and not on PyPI. Without the extra index pip
    # reports "No matching distribution found" and the install fails before any
    # of the ML code can be exercised. `--extra-index-url` rather than
    # `--index-url` because every other pin still has to come from PyPI.
    completed = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--extra-index-url",
            TORCH_CPU_INDEX,
            "-r",
            str(BACKEND_DIR / "requirements.txt"),
        ],
        cwd=BACKEND_DIR,
    )
    if completed.returncode != 0:
        fail(
            "pip install failed (see the output above).\n"
            "       Common causes: no network access, or a proxy that needs\n"
            "       HTTP_PROXY/HTTPS_PROXY set in this shell."
        )
    ok("backend dependencies installed")


def install_frontend(npm: str) -> None:
    """Install the frontend dependencies declared in package-lock.json."""
    lockfile = FRONTEND_DIR / "package-lock.json"
    if not lockfile.is_file():
        warn("frontend/package-lock.json is missing; `npm install` will resolve unpinned versions")
    info("installing frontend dependencies")
    completed = subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=FRONTEND_DIR)
    if completed.returncode != 0:
        fail(
            "npm install failed (see the output above).\n"
            "       Common causes: no network access, or a proxy that needs\n"
            "       HTTP_PROXY/HTTPS_PROXY set in this shell."
        )
    ok("frontend dependencies installed")


def report_docker() -> None:
    """Report Docker availability. Never fatal: the stack also runs natively."""
    if shutil.which("docker") is None:
        info("Docker was not found on PATH, so `make up` will not work on this machine.")
        info("Everything below also runs directly against a local PostgreSQL.")
        return
    try:
        completed = subprocess.run(
            ["docker", "compose", "version"], capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.SubprocessError) as exc:
        warn(f"could not run `docker compose version`: {exc}")
        return
    if completed.returncode != 0:
        warn("Docker is installed but the Compose plugin did not answer.")
        return
    ok(completed.stdout.strip().splitlines()[0])


def print_next_steps() -> None:
    """Print the commands that finish setting NEXUS up."""
    venv_python_cmd = (
        "backend/.venv/Scripts/python.exe" if os.name == "nt" else "backend/.venv/bin/python"
    )
    print(
        "\nNext steps\n"
        "----------\n"
        "  Container stack (needs Docker):\n"
        "      docker compose up -d --build\n"
        "      open http://localhost:5173\n"
        "\n"
        "  Or, against an already-running PostgreSQL, without Docker:\n"
        f"      {venv_python_cmd} scripts/wait_for_db.py\n"
        f"      {venv_python_cmd} scripts/create_test_database.py\n"
        f"      (cd backend && ../{venv_python_cmd} -m alembic upgrade head)\n"
        "      ./scripts/dev.sh              # or: make backend / make frontend\n"
        "\n"
        "  Create an account at http://localhost:8000/docs "
        "(POST /api/v1/auth/register).",
        flush=True,
    )


def main() -> int:
    """Run every bootstrap step and report the outcome."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="only run the checks and create .env; do not install dependencies",
    )
    args = parser.parse_args()

    print("NEXUS bootstrap")
    print(f"repository: {REPO_ROOT}")

    step("Prerequisites")
    check_python()
    npm = check_node()
    check_repository()
    report_docker()

    step("Configuration")
    ensure_env_file()

    step("Python environment")
    python = ensure_venv()
    if not args.skip_install:
        install_backend(python)
    else:
        warn("--skip-install: skipping dependency installation")

    step("Node dependencies")
    if not args.skip_install:
        install_frontend(npm)
    else:
        warn("--skip-install: skipping `npm install`")

    print_next_steps()
    print("\nBootstrap complete.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
