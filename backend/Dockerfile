# syntax=docker/dockerfile:1
#
# NEXUS backend — multi-stage image.
#
#   docker build -t nexus-backend ./backend
#   docker run --rm -p 8000:8000 --env-file ../.env nexus-backend
#
# Build context must be ./backend: the image copies app/, migrations/, ml/ and
# requirements.txt from that directory.

# --- Stage 1: build the virtualenv ------------------------------------------
FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# libpq-dev/postgresql-client: psycopg's binary wheel needs neither, but libpq5
# is required at runtime, so it is installed in the runtime stage too.
WORKDIR /build

COPY requirements.txt ./

# Everything above the DEV marker in requirements.txt is runtime; awk extracts
# exactly that, so pytest/ruff can never leak into the runtime image.
# The extra index is what makes the torch pin resolvable: `torch==2.14.1+cpu`
# carries a local version identifier published only on PyTorch's CPU index, and
# without it pip fails with "No matching distribution found". It is an *extra*
# index rather than a replacement because every other pin still comes from PyPI.
# The image therefore grows by roughly a gigabyte and ML stays a runtime
# requirement; ML itself still degrades to a 503 if the checkpoint is absent,
# because the app never imports torch until it decides to load a model.
RUN awk '/^# =+ DEV MARKER/{exit} {print}' requirements.txt > requirements.runtime.txt \
    && python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
    && /opt/venv/bin/pip install \
         --extra-index-url https://download.pytorch.org/whl/cpu \
         -r requirements.runtime.txt

# --- Stage 2: runtime --------------------------------------------------------
FROM python:3.13-slim AS runtime

LABEL org.opencontainers.image.title="NEXUS Backend" \
      org.opencontainers.image.description="NEXUS — Personal Intelligence & Decision Platform API" \
      org.opencontainers.image.version="0.1.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app \
    NEXUS_HOST=0.0.0.0 \
    NEXUS_PORT=8000

# libpq5 backs psycopg; curl backs the healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

# Unprivileged runtime user. Fixed uid/gid so bind-mounted volumes have a
# predictable owner on the host.
RUN groupadd --system --gid 10001 nexus \
    && useradd --system --uid 10001 --gid nexus --no-create-home --shell /usr/sbin/nologin nexus

WORKDIR /app
COPY --chown=nexus:nexus app ./app
COPY --chown=nexus:nexus migrations ./migrations
COPY --chown=nexus:nexus alembic.ini ./alembic.ini
COPY --chown=nexus:nexus run.py ./run.py

# The ML taxonomy package, which the app imports at boot: app.ml.router and
# app.api.v1.ml read ml.datasets, and app.ml.classifier reads ml.validation for
# its credential-shaped-text screen. Without these the process cannot even
# import app.main, and the ML routes report "checkpoint missing" for a reason
# that has nothing to do with ML_MODEL_PATH.
# Copied path by path rather than as `COPY ml ./ml` because backend/.dockerignore
# does not exclude ml/artifacts/, so the whole-package copy would bake the
# multi-gigabyte Phase 10 checkpoint into the image.
COPY --chown=nexus:nexus ml/__init__.py ml/validation.py ./ml/
COPY --chown=nexus:nexus ml/datasets ./ml/datasets
COPY --chown=nexus:nexus ml/preprocessing ./ml/preprocessing

USER nexus
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${NEXUS_PORT}/health" || exit 1

# One worker: the engine and the in-process token revocation store are
# per-process, so horizontal scaling must come from running more containers
# behind a load balancer, not from more workers here.
CMD ["python", "run.py"]
