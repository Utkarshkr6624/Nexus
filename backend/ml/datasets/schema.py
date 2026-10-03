"""Record schemas for every dataset Phase 10 builds.

Three record types, one per dataset, each carrying a **closed schema version**
in the same spirit as ``developer_features.v1`` and its siblings: a trainer that
reads ``routing_intent.v1`` knows what every field meant without having to trust
the process that wrote it, and a v2 cannot be mistaken for a v1.

Three rules run through the whole module.

**Provenance is a field, not a comment.** Every record says whether it is
``SYNTHETIC`` (generated from templates), ``DERIVED`` (computed from real
repository output) or ``REAL`` (observed). A dataset that cannot say where its
rows came from is a dataset nobody can rebuild.

**A missing value is ``None``, never ``0``.** This is the contract Phases 8 and 9
established for feature vectors — *"a figure that could not be computed is
``null``, never ``0``"* — and it binds here. :class:`FeatureRow` therefore pairs
every value with a parallel ``available`` mask, so a value that was never
measured can be told from a value that was measured and happened to be zero.
Inside a training matrix those two are otherwise indistinguishable, and a
fabricated zero is exactly the defect the Phases 8/9 remediation pass removed.

**Serialisation is deterministic.** Records serialise with sorted keys, so a
dataset hash in a manifest means the same thing tomorrow.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

#: Version of the routing/intent record shape. Bump on any field rename.
SCHEMA_VERSION_ROUTING = "routing_intent.v1"


#: Version of the feature-row record shape. Distinct from the four
#: ``*_features.v1`` contracts it carries, because this is the *training* row
#: wrapping one of them rather than the vector itself.
SCHEMA_VERSION_FEATURES = "nexo_feature_rows.v1"

#: Every schema version this module knows how to read. A record stamped with
#: anything else is a hard validation failure rather than a best-effort guess:
#: silently coercing an unknown layout is how a v2 gets fitted as a v1.
KNOWN_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION_ROUTING, SCHEMA_VERSION_FEATURES})


class Provenance(StrEnum):
    """Where a record came from.

    ``SYNTHETIC`` is not a lesser kind of record — it is a *declared* one. A
    dataset built entirely from templates is legitimate and useful, provided
    every row says so and the generator is deterministic.
    """

    SYNTHETIC = "synthetic"
    DERIVED = "derived"
    REAL = "real"


class DatasetError(Exception):
    """A dataset could not be built, read or written."""


class DataValidationError(DatasetError):
    """A data-integrity check failed.

    Raised by :mod:`ml.validation` and deliberately *not* caught by the
    pipeline: the brief requires training to stop rather than continue on data
    whose integrity is unknown.
    """


def _require_str(raw: Mapping[str, Any], key: str) -> str:
    """Read a required string field, or raise.

    Args:
        raw: The decoded record.
        key: The field name.

    Returns:
        The string value.

    Raises:
        DataValidationError: The field is absent, null, or not a string.
    """
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DataValidationError(f"{key!r} must be a non-empty string, got {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class RoutingExample:
    """One labelled utterance for the small routing model.

    The model is an encoder classifier: it reads ``text`` and predicts
    ``intent``. Nothing else in the record reaches the forward pass — ``intent``
    is the label, and the remaining fields exist so a reader can tell where the
    row came from and reproduce it.
    """

    text: str
    intent: str
    provenance: Provenance = Provenance.SYNTHETIC
    template_id: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise deterministically.

        Returns:
            A JSON-ready mapping with sorted keys.
        """
        return {
            "schema_version": SCHEMA_VERSION_ROUTING,
            "text": self.text,
            "intent": self.intent,
            "provenance": str(self.provenance),
            "template_id": self.template_id,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RoutingExample:
        """Rebuild a record from its serialised form.

        Args:
            raw: A decoded JSON object.

        Returns:
            The parsed example.

        Raises:
            DataValidationError: A required field is missing or malformed.
        """
        provenance = raw.get("provenance", Provenance.SYNTHETIC)
        try:
            provenance = Provenance(str(provenance))
        except ValueError as exc:
            raise DataValidationError(f"unknown provenance {provenance!r}") from exc
        return cls(
            text=_require_str(raw, "text"),
            intent=_require_str(raw, "intent"),
            provenance=provenance,
            template_id=str(raw.get("template_id", "")),
            source=str(raw.get("source", "")),
        )


@dataclass(frozen=True, slots=True)
class FeatureRow:
    """One feature vector wrapped for training.

    ``values`` and ``available`` are parallel maps over the *same* column names.
    A column present in ``available`` with ``False`` means the figure could not
    be computed and ``values[name]`` is ``None`` — it is emphatically not zero.
    A column present in ``available`` with ``True`` and a value of ``0`` is a
    real measurement that happened to be zero.

    This pairing is the direct consequence of ``career_features.v1``'s
    ``project_activity``: null when no repository has ever been scanned,
    because ``0`` would assert that a repository exists and carries no commits
    when the truth is that nobody has looked. Dropping the mask and filling with
    zeros would reintroduce, at training time, precisely the defect the Phase 9
    remediation removed at the API.

    ``source_schema_version`` names the ``*_features.v1`` contract the columns
    came from, so a row can be attributed to the extraction that produced it.
    """

    source_schema_version: str
    subject: str
    values: Mapping[str, Any]
    available: Mapping[str, bool]
    provenance: Provenance = Provenance.DERIVED
    source: str = ""

    def unavailable_columns(self) -> tuple[str, ...]:
        """Columns whose value could not be computed.

        Returns:
            Sorted column names marked unavailable.
        """
        return tuple(sorted(k for k, ok in self.available.items() if not ok))

    def is_complete(self) -> bool:
        """Whether every column carries a real measurement.

        Returns:
            True when no column is marked unavailable.
        """
        return not self.unavailable_columns()

    def to_dict(self) -> dict[str, Any]:
        """Serialise deterministically.

        Returns:
            A JSON-ready mapping with sorted keys.
        """
        return {
            "schema_version": SCHEMA_VERSION_FEATURES,
            "source_schema_version": self.source_schema_version,
            "subject": self.subject,
            "values": dict(self.values),
            "available": dict(self.available),
            "provenance": str(self.provenance),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> FeatureRow:
        """Rebuild a record from its serialised form.

        Args:
            raw: A decoded JSON object.

        Returns:
            The parsed row.

        Raises:
            DataValidationError: A required field is missing, or the value and
                availability maps disagree about which columns exist.
        """
        provenance = raw.get("provenance", Provenance.DERIVED)
        try:
            provenance = Provenance(str(provenance))
        except ValueError as exc:
            raise DataValidationError(f"unknown provenance {provenance!r}") from exc
        values = raw.get("values", {})
        available = raw.get("available", {})
        if not isinstance(values, Mapping) or not isinstance(available, Mapping):
            raise DataValidationError("values and available must both be objects")

        missing_mask = sorted(set(values) - set(available))
        if missing_mask:
            raise DataValidationError(
                f"every value column needs an availability flag; missing for {missing_mask}"
            )

        flags: dict[str, bool] = {}
        for name, flag in available.items():
            if not isinstance(flag, bool):
                raise DataValidationError(f"available[{name!r}] must be a bool, got {flag!r}")
            flags[name] = flag

        # An unavailable column must not carry a value. Keeping both would let a
        # trainer read the value and never consult the mask.
        for name, ok in flags.items():
            if not ok and values.get(name) is not None:
                raise DataValidationError(
                    f"column {name!r} is marked unavailable but carries {values[name]!r}"
                )

        return cls(
            source_schema_version=_require_str(raw, "source_schema_version"),
            subject=_require_str(raw, "subject"),
            values=dict(values),
            available=flags,
            provenance=provenance,
            source=str(raw.get("source", "")),
        )


def stable_json_dumps(payload: Any) -> str:
    """Serialise with sorted keys so a hash means something stable.

    Args:
        payload: Any JSON-serialisable object.

    Returns:
        Canonical JSON text.
    """
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_text(text: str) -> str:
    """Hash a string.

    Args:
        text: The content to hash.

    Returns:
        The lowercase hex digest.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    """Hash a file's bytes.

    Args:
        path: The file to hash.

    Returns:
        The lowercase hex digest.

    Raises:
        DatasetError: The file does not exist or cannot be read.
    """
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise DatasetError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> int:
    """Write records as JSON Lines.

    Args:
        path: Destination file. Parent directories are created.
        records: Mappings to serialise, one per line.

    Returns:
        The number of records written.

    Raises:
        DatasetError: The destination could not be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    try:
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(stable_json_dumps(record) + "\n")
                count += 1
    except OSError as exc:
        raise DatasetError(f"cannot write {path}: {exc}") from exc
    return count


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSON Lines file.

    Blank lines are skipped; a malformed line is an error rather than a silent
    skip, because a dataset that quietly loses rows is worse than one that
    refuses to load.

    Args:
        path: The file to read.

    Returns:
        The decoded records, in file order.

    Raises:
        DatasetError: The file is missing or a line is not a JSON object.
    """
    if not path.exists():
        raise DatasetError(f"dataset not found: {path}")
    records: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    decoded = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise DatasetError(f"{path}:{number}: malformed JSON: {exc}") from exc
                if not isinstance(decoded, dict):
                    raise DatasetError(f"{path}:{number}: expected a JSON object")
                records.append(decoded)
    except OSError as exc:
        raise DatasetError(f"cannot read {path}: {exc}") from exc
    return records


__all__ = [
    "KNOWN_SCHEMA_VERSIONS",
    "SCHEMA_VERSION_FEATURES",
    "SCHEMA_VERSION_ROUTING",
    "DataValidationError",
    "DatasetError",
    "FeatureRow",
    "Provenance",
    "RoutingExample",
    "read_jsonl",
    "sha256_file",
    "sha256_text",
    "stable_json_dumps",
    "write_jsonl",
]
