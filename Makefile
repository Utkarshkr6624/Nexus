# =============================================================================
# NEXUS — task runner.
# =============================================================================
# Every target is a thin wrapper around a real command or a real script; no
# logic is duplicated here. Requires GNU make 4.x (`make` is not shipped with
# Windows — use Git Bash with a make package, or run the scripts directly).
#
#   make            list the targets
#   make install    bootstrap the checkout (venv, .env, npm install)
# =============================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

BACKEND  := backend
FRONTEND := frontend
SCRIPTS  := scripts

# Interpreter used for the backend. A Windows virtualenv keeps it in Scripts/,
# a POSIX one in bin/, and neither layout exists on the other platform, so the
# first path that is actually present wins.
#
# Overridable. The value is always read relative to the repository root: the
# targets that `cd backend` prepend a `../` themselves, so the same value works
# from either directory.
#   make migrate PY=backend/.venv/Scripts/python.exe
PY_SCRIPTS := $(wildcard $(BACKEND)/.venv/Scripts/python.exe)
PY_BIN     := $(wildcard $(BACKEND)/.venv/bin/python)
PY ?= $(if $(PY_SCRIPTS),$(PY_SCRIPTS),$(PY_BIN))
NPM ?= npm

# Interpreter used for the Phase 10 training targets. `torch`, `transformers`
# and `datasets` live in their own virtualenv at backend/ml/.venv so that a
# contributor never has to pull a multi-gigabyte torch wheel into the backend
# environment the test suite runs against. Same Windows/POSIX layout split as
# $(PY), same relative-to-repository-root convention.
#
# Falls back to $(PY) when the ML environment has not been created. A missing
# torch then surfaces as an ImportError naming the package, which tells the
# operator what to build; "No such file or directory" on a path they were never
# told about does not.
#   make ml-train-small ML_PY=backend/ml/.venv/Scripts/python.exe
ML_SCRIPTS := $(wildcard $(BACKEND)/ml/.venv/Scripts/python.exe)
ML_BIN     := $(wildcard $(BACKEND)/ml/.venv/bin/python)
ML_PY ?= $(if $(ML_SCRIPTS),$(ML_SCRIPTS),$(if $(ML_BIN),$(ML_BIN),$(PY)))

# `make install` has to work before the virtualenv exists, so it probes for any
# Python 3.13+ on PATH instead of using $(PY).
PYTHON_BOOTSTRAP ?= $(shell command -v python3 2>/dev/null || command -v python 2>/dev/null || echo python3)

# Seconds `db-wait` keeps retrying before it gives up.
TIMEOUT ?= 60
# Extra flags for `test-db`. Empty on purpose: the target only ever creates the
# test database, so `make test-db TEST_DB_FLAGS=--drop` is how you ask for a
# rebuild rather than having destruction be the default.
TEST_DB_FLAGS ?=

# `alembic.ini` uses paths relative to backend/, so Alembic always runs there.
ALEMBIC := cd $(BACKEND) && ../$(PY) -m alembic

# Likewise, the ml package is imported as `ml` from the backend working
# directory (pytest.ini sets pythonpath = . from backend/), so every ml target
# runs there and prepends `../` to an interpreter path rooted at the repo.
ML_RUN := cd $(BACKEND) && ../$(ML_PY) -m ml.train

.PHONY: help install bootstrap up down logs migrate migrate-down revision \
        db-wait test-db test test-backend test-frontend lint backend frontend clean \
        ml-help ml-prepare ml-datasets ml-validate ml-train-small \
        ml-train-small-resume ml-eval ml-train-qwen ml-probe-remote ml-eval-qwen \
        ml-qwen-status ml-all ml-test

help: ## Show this help
	@echo "NEXUS — available targets:"
	@grep -E '^[a-z][a-zA-Z0-9_-]*:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "PY defaults to the interpreter inside backend/.venv/ (Scripts/ on Windows,"
	@echo "bin/ on POSIX). To use another one, give a path relative to this directory:"
	@echo "  make test-backend PY=backend/.venv/Scripts/python.exe"

install: ## Create .env, the backend venv and all dependencies
	$(PYTHON_BOOTSTRAP) $(SCRIPTS)/bootstrap.py

bootstrap: install ## Alias for `make install`

up: ## Build and start the Docker stack in the background
	docker compose up -d --build
	@echo "frontend  http://localhost:5173"
	@echo "api docs  http://localhost:8000/docs"

down: ## Stop the Docker stack (keeps the database volume)
	docker compose down

logs: ## Follow the Docker stack logs
	docker compose logs -f

migrate: ## Apply all pending Alembic migrations
	$(ALEMBIC) upgrade head

migrate-down: ## Roll back the most recent Alembic migration
	$(ALEMBIC) downgrade -1

revision: ## Create a migration:  make revision m="add widgets table"
	@test -n "$(m)" || { echo "usage: make revision m=\"short message\""; exit 1; }
	$(ALEMBIC) revision -m "$(m)"

db-wait: ## Block until PostgreSQL accepts connections (make db-wait TIMEOUT=120)
	$(PY) $(SCRIPTS)/wait_for_db.py --timeout $(TIMEOUT)

test: test-backend test-frontend ## Run every test suite

test-db: ## Create the pytest database; `make test-db TEST_DB_FLAGS=--drop` rebuilds it
	$(PY) $(SCRIPTS)/create_test_database.py $(TEST_DB_FLAGS)

test-backend: ## Run the pytest suite
	cd $(BACKEND) && ../$(PY) -m pytest

test-frontend: ## Run the vitest suite
	cd $(FRONTEND) && $(NPM) run test

lint: ## Lint and typecheck backend and frontend
	cd $(BACKEND) && ../$(PY) -m ruff check .
	cd $(BACKEND) && ../$(PY) -m ruff format --check .
	cd $(FRONTEND) && $(NPM) run lint
	cd $(FRONTEND) && $(NPM) run typecheck

backend: ## Run the FastAPI server in the foreground
	./$(SCRIPTS)/dev.sh backend

frontend: ## Run the Vite dev server in the foreground
	./$(SCRIPTS)/dev.sh frontend

clean: ## Remove build artefacts and tooling caches (never touches .env or data)
	cd $(BACKEND) && ../$(PY) -c "import shutil,pathlib;[shutil.rmtree(p,ignore_errors=True) for p in ['.pytest_cache','.ruff_cache']]"
	cd $(BACKEND) && find app migrations tests -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(FRONTEND)/dist $(FRONTEND)/coverage
	rm -f $(FRONTEND)/*.tsbuildinfo
	@echo "cleaned build artefacts"

# =============================================================================
# Phase 10 — ML training pipeline
# =============================================================================
# Split in two by what they need, because the two halves have nothing in common
# except a package name:
#
#   * The data half (ml-prepare, ml-validate, ml-eval) is stdlib-only and runs
#     on any interpreter, because everything under ml/ is stdlib-only. That is
#     what keeps the test suite fast and CI dependency-free.
#   * The training half (ml-train-small, ml-train-qwen) imports torch. It needs
#     $(ML_PY), whose environment carries the torch wheel the backend one does
#     not.
#
# `ml-train-qwen` is deliberately not part of `ml-all`: Qwen3-8B under QLoRA
# does not fit the 4 GB of VRAM on this machine and the remote kernel has no
# working GPU, so folding it into the local pipeline would mean "all" that
# cannot actually run.

ml-help: ## Show the Phase 10 ML targets
	@grep -E '^ml-[a-z-]*:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "ML_PY defaults to the interpreter inside backend/ml/.venv/ (Scripts/ on"
	@echo "Windows, bin/ on POSIX), falling back to backend/.venv when the ML"
	@echo "environment has not been created yet. To pin it:"
	@echo "  make ml-train-small ML_PY=backend/ml/.venv/Scripts/python.exe"

ml-prepare: ## Build, validate and split the datasets (stdlib only, no GPU)
	$(ML_RUN) --prepare

ml-datasets: ml-prepare ## Alias for `make ml-prepare`

ml-validate: ml-prepare ## Prepare the datasets, then print the validation reports
	@found=0; for report in $(BACKEND)/ml/reports/*.md; do \
		[ -e "$$report" ] || continue; \
		found=1; echo "==> $$report"; cat "$$report"; echo; \
	done; \
	if [ "$$found" -eq 0 ]; then \
		echo "no reports in $(BACKEND)/ml/reports/ - run 'make ml-prepare' first"; \
	fi

ml-train-small: ## Fine-tune the routing classifier locally on CPU (needs torch)
	$(ML_RUN) --train-small

ml-train-small-resume: ## Resume the classifier from its latest local checkpoint
	$(ML_RUN) --train-small --resume

ml-eval: ## Evaluate the trained classifier and write the evaluation reports
	$(ML_RUN) --evaluate

ml-train-qwen: ## Push the QLoRA fine-tune of Qwen3-8B to a remote Kaggle kernel
	$(ML_RUN) --train-qwen

ml-probe-remote: ## Push a one-cell kernel that records whether Kaggle gave us a GPU
	$(ML_RUN) --probe-remote

ml-eval-qwen: ## Compare the base Qwen against the fine-tuned adapter (paired)
	$(ML_RUN) --eval-qwen

ml-qwen-status: ## Report the state of the remote Qwen kernel
	$(ML_RUN) --qwen-status

ml-all: ml-prepare ml-train-small ml-eval ## Run the whole local Phase 10 pipeline

ml-test: ## Run the ml test modules only
	cd $(BACKEND) && ../$(PY) -m pytest tests/test_ml_*.py
