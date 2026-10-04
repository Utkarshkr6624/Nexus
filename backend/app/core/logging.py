"""Structured logging for the NEXUS backend.

Two sinks are possible, both fed by the same stdlib logger tree:

* stdout — JSON when ``LOG_JSON=true``, otherwise a colourised human format.
* a file — always JSON lines, when ``LOG_FILE`` is set.

The stdlib is used rather than structlog deliberately: Phase 1 needs exactly
one thing structlog would have provided (per-request context attached to every
record), and ``contextvars`` plus a custom ``Formatter`` does that in a few
dozen lines without adding a dependency to the runtime image.

Every record carries the ``request_id`` bound by
:func:`app.core.middleware.request_context_middleware`, and payload keys listed
in :data:`REDACTED_KEYS` are replaced before they can reach a sink.
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import sys
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from app.core.config import Settings, get_settings

__all__ = [
    "REDACTED_KEYS",
    "configure_logging",
    "get_logger",
    "new_request_id",
    "redact",
    "request_id_var",
    "set_request_id",
]

#: Request-scoped correlation id. Bound per request, reset when the request ends.
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

#: Attribute names that must never appear in a log line, in any casing.
#: Spelled with underscores because :func:`redact` folds hyphens to underscores
#: before looking a key up.
REDACTED_KEYS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "hashed_password",
        "new_password",
        "old_password",
        # The password-change payload names the field ``current_password``
        # (``schemas/security.py``). It was missing from this set, so enabling
        # ``log_request_body`` wrote the account's current password to the
        # access log in plaintext. Same secret as ``password``; different key.
        "current_password",
        "confirm_password",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "authorization",
        "auth",
        "secret",
        "secret_key",
        "api_key",
        "apikey",
        "x_api_key",
        "cookie",
        "set_cookie",
        "session",
        "csrf_token",
    }
)
_REDACTED = "***redacted***"

_LEVEL_COLOURS: Final[dict[str, str]] = {
    "DEBUG": "\033[36m",  # cyan
    "INFO": "\033[32m",  # green
    "WARNING": "\033[33m",  # yellow
    "ERROR": "\033[31m",  # red
    "CRITICAL": "\033[1;41m",  # white on red
}
_RESET = "\033[0m"
_DIM = "\033[2m"

_RESERVED_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def new_request_id() -> str:
    """Return a fresh request correlation id."""
    return str(uuid.uuid4())


def set_request_id(request_id: str) -> contextvars.Token[str | None]:
    """Bind ``request_id`` to the current context, returning a reset token."""
    return request_id_var.set(request_id)


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively replace sensitive values in mappings and sequences.

    Keys are matched case-insensitively against :data:`REDACTED_KEYS`. Depth is
    bounded so a pathological or cyclic payload cannot stall a log call.
    """
    if _depth > 8:
        return "..."
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if name.strip().lower().replace("-", "_") in REDACTED_KEYS:
                result[name] = _REDACTED
            else:
                result[name] = redact(item, _depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        return [redact(item, _depth + 1) for item in value]
    return value


class ContextFilter(logging.Filter):
    """Attach the context-local ``request_id`` to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with redaction applied to ``extra`` values."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self.service,
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_ATTRS or key == "request_id":
                continue
            # Redact the key/value pair, not the value alone: a scalar secret
            # such as password="hunter2" would otherwise pass straight through.
            payload[key] = redact({key: value})[key]
        if "extra" in payload:
            payload["extra"] = redact(payload["extra"])

        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Compact, colourised single-line output for interactive development."""

    def __init__(self, service: str, colour: bool) -> None:
        super().__init__()
        self.service = service
        self.colour = colour

    def _paint(self, text: str, colour: str) -> str:
        return f"{colour}{text}{_RESET}" if self.colour else text

    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        level = self._paint(f"{record.levelname:<8}", _LEVEL_COLOURS.get(record.levelname, ""))
        parts = [self._paint(stamp, _DIM), level, self.service, record.name, record.getMessage()]

        request_id = getattr(record, "request_id", None)
        if request_id:
            parts.append(self._paint(f"req={request_id[:8]}", _DIM))

        fields = {
            # Redact the key/value pair, not the value alone: a scalar secret
            # such as password="hunter2" has nothing to walk, and this is the
            # formatter a developer reads by eye.
            key: redact({key: value})[key]
            for key, value in record.__dict__.items()
            if key not in _RESERVED_RECORD_ATTRS and key != "request_id"
        }
        if fields:
            parts.append(
                self._paint(
                    " ".join(f"{k}={_scalar(v)}" for k, v in fields.items()),
                    _DIM,
                )
            )

        line = " ".join(parts)
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


def _scalar(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return str(value)


def _resolve_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    resolved = logging.getLevelNamesMapping().get(level.strip().upper())
    if resolved is None:
        raise ValueError(f"Unknown log level: {level!r}")
    return resolved


def configure_logging(settings: Settings | None = None, *, force: bool = False) -> logging.Logger:
    """Install handlers on the root logger. Idempotent unless ``force``.

    Must run before any request is served so that early import-time records are
    already formatted correctly.
    """
    settings = settings or get_settings()
    root = logging.getLogger()
    level = _resolve_level(settings.log_level)

    if root.handlers and not force:
        root.setLevel(level)
        return root

    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    formatter_context = ContextFilter()
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.addFilter(formatter_context)
    stdout_handler.setFormatter(
        JsonFormatter(settings.app_name)
        if settings.log_json
        else ConsoleFormatter(
            settings.app_name, colour=not settings.is_production and sys.stdout.isatty()
        )
    )
    root.addHandler(stdout_handler)

    if settings.log_file:
        destination = Path(settings.log_file)
        destination.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.WatchedFileHandler(
            destination, encoding="utf-8", delay=True
        )
        file_handler.addFilter(formatter_context)
        # The file sink is always machine-readable, even in console-human mode.
        file_handler.setFormatter(JsonFormatter(settings.app_name))
        root.addHandler(file_handler)

    root.setLevel(level)
    for noisy in ("uvicorn.access", "uvicorn.error", "uvicorn", "sqlalchemy.engine"):
        logging.getLogger(noisy).handlers.clear()
        logging.getLogger(noisy).propagate = True

    # uvicorn installs its own colour access log; ours is the single source.
    logging.getLogger("uvicorn.access").disabled = True
    return root


def get_logger(name: str) -> logging.Logger:
    """Return a module logger, ensuring handlers are installed.

    Safe to call at import time and before :func:`configure_logging` — a
    provisional handler is installed so nothing is lost if startup fails.
    """
    if not logging.getLogger().handlers:
        configure_logging()
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    exc_info: bool = False,
    **fields: Any,
) -> None:
    """Emit a record whose keyword fields are redacted before reaching a sink.

    Field names that would collide with ``LogRecord`` attributes are renamed
    rather than dropped, so a caller typo can never raise from inside logging.
    """
    extra = {
        (name if name not in _RESERVED_RECORD_ATTRS else f"field_{name}"): value
        for name, value in redact(fields).items()
    }
    logger.log(level, message, exc_info=exc_info, extra=extra)
