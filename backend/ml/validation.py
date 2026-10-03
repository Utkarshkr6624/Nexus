"""The data-integrity gate a dataset must pass before training starts.

Every builder in :mod:`ml.datasets` can produce records; none of them can
produce *trustworthy* records. A template generator can emit the same sentence
twice, a scraper can attach two different intents to one utterance, and a
developer pasting a real task list into a prompt can carry an API key along
with it. None of those failures announce themselves at training time — the run
happily converges and reports a number that is quietly wrong. So the pipeline
puts this module between building and training and treats a passing report as a
precondition rather than a formality.

**The asymmetry between ERROR and WARNING is the design.** A duplicate text, a
contradictory label, a leaked split or a credential means the data is corrupt:
the run is measuring the wrong thing, so it stops. An imbalanced class
distribution or a near-duplicate means the data is *imperfect*: a real dataset
of real requests is lopsided and repetitive, and refusing to train on lopsided
data would refuse to train on reality. Those are recorded and the run
continues, with the caveat attached to the artifact.

Three consequences worth stating, because they shape the code:

**A report never reproduces what it rejected.** Credential findings name the
*kind* — ``kaggle_token``, ``assigned_secret`` — and carry a redacted excerpt,
never the match. A report is written to disk, printed into a CI log and
attached to a run manifest; a validator that echoed the secret it found would
have leaked it to every one of those places. The same care applies to every
other sample, which is passed through :func:`~ml.preprocessing.normalize.redact`
regardless of whether a credential was found there.

**Contradiction is not duplication.** Two identical texts with the same label
are merely redundant — a warning. The same normalised text carrying two
different labels is a hard error, because there is no training procedure that
recovers the intended boundary from a contradictory pair, and the resulting
model will be confidently wrong on exactly the phrasing that was ambiguous. The
same rule applies to the SFT dataset, where the contradiction is one prompt
paired with two different responses.

**The gate is deterministic and stdlib-only.** The same dataset produces the
same report on a contributor's laptop and in CI, with no dependency that could
drift between them — the same discipline the split logic needs, since a
non-deterministic split makes leakage undetectable.

Nothing here reaches for a model, a network or a third-party library, and
nothing here reads a credential store. It is the last thing standing between a
malformed JSONL file and a trained adapter, so it is also the cheapest place in
the pipeline to be paranoid.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import islice
from types import MappingProxyType
from typing import Any

from ml.datasets.schema import (
    KNOWN_SCHEMA_VERSIONS,
    SCHEMA_VERSION_FEATURES,
    SCHEMA_VERSION_QWEN,
    SCHEMA_VERSION_ROUTING,
    DataValidationError,
    FeatureRow,
    QwenExample,
    RoutingExample,
)
from ml.preprocessing.normalize import (
    find_credential,
    near_duplicate_key,
    normalize_text,
    redact,
)

#: Version of the report shape itself, so a report stored in a run manifest can
#: be read back without guessing which fields it had.
VALIDATION_REPORT_VERSION = "nexo_validation.v1"

#: Two reports that describe no particular record shape: the split audit reads
#: keys rather than records, and the credential scan reads raw text.
_SPLIT_SCHEMA_VERSION = "splits.v1"
_SCAN_SCHEMA_VERSION = "credential_scan.v1"

#: A finding quotes at most this many examples. A dataset with ten thousand
#: leaked rows still deserves a report a human will read to the end.
_MAX_SAMPLES = 5

#: Sample text is truncated to this many characters after redaction. Enough to
#: recognise the offending line, short enough to keep a table legible.
_EXCERPT_CHARS = 120


class Severity(StrEnum):
    """How much a finding should worry the caller.

    ``ERROR`` stops the run; ``WARNING`` annotates it. The distinction is not
    about how likely the finding is to be real — a credential in a dataset is
    near-certainly real — it is about whether the dataset is *false* or merely
    lopsided.
    """

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Finding:
    """One observation about a dataset.

    ``code`` is the stable handle a test or a pipeline step asserts on;
    ``message`` is the human sentence; ``sample`` carries redacted excerpts so
    a report can point at evidence without republishing it.
    """

    code: str
    severity: Severity
    message: str
    sample: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialise deterministically.

        Returns:
            A JSON-ready mapping with the sample as a list.
        """
        return {
            "code": self.code,
            "severity": str(self.severity),
            "message": self.message,
            "sample": list(self.sample),
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """The verdict on one dataset, split assignment or text collection.

    ``counts`` is what makes a report useful even when it passes: the class
    histogram is the evidence that a run trained on what its author thought it
    trained on. ``passed`` is derived from ``findings`` rather than supplied, so
    a report can never claim success while holding an error.
    """

    name: str
    schema_version: str
    findings: tuple[Finding, ...] = ()
    counts: Mapping[str, int] = field(default_factory=dict)
    passed: bool = field(init=False)

    def __post_init__(self) -> None:
        """Normalise the collections and derive the verdict."""
        object.__setattr__(self, "findings", tuple(self.findings))
        object.__setattr__(self, "counts", MappingProxyType(dict(self.counts)))
        object.__setattr__(self, "passed", not self._has_errors())

    def _has_errors(self) -> bool:
        """Whether any finding blocks the run.

        Returns:
            True when at least one finding has ``ERROR`` severity.
        """
        return any(finding.severity is Severity.ERROR for finding in self.findings)

    def errors(self) -> tuple[Finding, ...]:
        """The findings that block a training run.

        Returns:
            Every ``ERROR`` finding, in report order.
        """
        return tuple(f for f in self.findings if f.severity is Severity.ERROR)

    def warnings(self) -> tuple[Finding, ...]:
        """The findings that annotate a training run.

        Returns:
            Every ``WARNING`` finding, in report order.
        """
        return tuple(f for f in self.findings if f.severity is Severity.WARNING)

    def codes(self) -> tuple[str, ...]:
        """The finding codes present, for assertions and log scraping.

        Returns:
            One code per finding, in report order.
        """
        return tuple(f.code for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        """Serialise deterministically for a run manifest.

        Returns:
            A JSON-ready mapping carrying the report version, the counts and
            every finding.
        """
        return {
            "report_version": VALIDATION_REPORT_VERSION,
            "name": self.name,
            "schema_version": self.schema_version,
            "passed": self.passed,
            "counts": dict(sorted(self.counts.items())),
            "findings": [f.to_dict() for f in self.findings],
        }

    def to_markdown(self) -> str:
        """Render the report as a markdown summary for a run log.

        Returns:
            A heading, the counts as a table, the findings as a table and a
            single unambiguous ``PASS`` or ``FAIL`` verdict line.
        """
        verdict = "PASS" if self.passed else "FAIL"
        lines = [
            f"## Validation report: {self.name}",
            "",
            f"- Report version: `{VALIDATION_REPORT_VERSION}`",
            f"- Schema version: `{self.schema_version}`",
            (
                f"- Result: **{verdict}** "
                f"({len(self.errors())} error(s), {len(self.warnings())} warning(s))"
            ),
            "",
            "### Counts",
            "",
        ]
        if self.counts:
            lines.append("| Metric | Value |")
            lines.append("| --- | ---: |")
            lines.extend(
                f"| {_escape_cell(name)} | {value} |" for name, value in sorted(self.counts.items())
            )
        else:
            lines.append("_No counts recorded._")
        lines += ["", "### Findings", ""]
        if self.findings:
            lines.append("| Severity | Code | Message | Sample |")
            lines.append("| --- | --- | --- | --- |")
            for finding in self.findings:
                sample = "<br>".join(_escape_cell(s) for s in finding.sample) or "-"
                lines.append(
                    f"| {finding.severity.value} | `{finding.code}` "
                    f"| {_escape_cell(finding.message)} | {sample} |"
                )
        else:
            lines.append("_No findings._")
        lines += [
            "",
            (
                f"**{verdict}** - training may proceed."
                if self.passed
                else f"**{verdict}** - training must stop; see the errors above."
            ),
        ]
        return "\n".join(lines)


def _escape_cell(text: str) -> str:
    """Make a string safe to drop into a markdown table cell.

    Args:
        text: The raw cell content.

    Returns:
        The text with pipes and newlines neutralised.
    """
    return " ".join(text.split()).replace("|", "\\|")


def _excerpt(text: str) -> str:
    """Reduce a string to a short, redacted, single-line excerpt.

    Every sample in this module goes through here, whatever the finding is. The
    credential scan is why redaction is mandatory, but it is applied
    unconditionally: a report is written to a manifest and a CI log, and there
    is no reason for any excerpt to be the one place a secret survives.

    Args:
        text: The text to excerpt.

    Returns:
        A redacted, whitespace-collapsed string of at most ``_EXCERPT_CHARS``.
    """
    collapsed = " ".join(redact(text).split())
    if len(collapsed) <= _EXCERPT_CHARS:
        return collapsed
    return collapsed[: _EXCERPT_CHARS - 3] + "..."


def _cap(values: Iterable[str]) -> tuple[str, ...]:
    """Truncate a sample list.

    Args:
        values: The candidate samples.

    Returns:
        At most ``_MAX_SAMPLES`` of them.
    """
    return tuple(islice(values, _MAX_SAMPLES))


def _escape_and_cap(values: Iterable[str]) -> tuple[str, ...]:
    """Redact, excerpt and truncate a sample list in one step.

    Args:
        values: Raw strings to quote in a finding.

    Returns:
        At most ``_MAX_SAMPLES`` redacted excerpts.
    """
    return _cap(_excerpt(value) for value in values)


def _check_schema_versions(
    rows: Sequence[Mapping[str, Any]],
    *,
    schema_version: str,
) -> tuple[list[Mapping[str, Any]], list[Finding]]:
    """Verify every record declares a schema this pipeline can read.

    A record with no version, or with a version from a future revision, is not
    coerced and not guessed at: a v2 layout read as a v1 is precisely the
    silent corruption this gate exists to catch.

    Args:
        rows: The decoded records.
        schema_version: The version this dataset is supposed to carry.

    Returns:
        The records that may be parsed, and the findings raised on the rest.
    """
    findings: list[Finding] = []
    usable: list[Mapping[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            findings.append(
                Finding(
                    "malformed_record",
                    Severity.ERROR,
                    f"record {index} is not a JSON object (got {type(row).__name__})",
                )
            )
            continue
        declared = row.get("schema_version")
        if not isinstance(declared, str) or not declared.strip():
            findings.append(
                Finding(
                    "missing_schema_version",
                    Severity.ERROR,
                    f"record {index} declares no schema_version; expected {schema_version!r}",
                )
            )
            continue
        if declared not in KNOWN_SCHEMA_VERSIONS:
            findings.append(
                Finding(
                    "unknown_schema_version",
                    Severity.ERROR,
                    (
                        f"record {index} declares schema_version {declared!r}, which this "
                        f"pipeline cannot read; expected {schema_version!r}"
                    ),
                )
            )
            continue
        if declared != schema_version:
            findings.append(
                Finding(
                    "mismatched_schema_version",
                    Severity.WARNING,
                    f"record {index} declares {declared!r} but this dataset is {schema_version!r}",
                )
            )
        usable.append(row)
    return usable, findings


def _check_required_fields(
    rows: Sequence[Mapping[str, Any]],
    required: Sequence[str],
) -> tuple[list[Mapping[str, Any]], list[Finding]]:
    """Verify the fields a record cannot be trained without are non-empty.

    These are reported under their own code rather than as a generic parse
    failure because the remedy is different: a missing ``text`` is a builder bug
    with an obvious fix, whereas a record that will not parse is opaque.

    Args:
        rows: The decoded records.
        required: Field names that must be present and non-blank strings.

    Returns:
        The records that satisfy the requirement, and the findings on the rest.
    """
    findings: list[Finding] = []
    usable: list[Mapping[str, Any]] = []
    for index, row in enumerate(rows):
        missing = [
            name
            for name in required
            if not isinstance(row.get(name), str) or not str(row[name]).strip()
        ]
        if missing:
            findings.append(
                Finding(
                    "missing_field",
                    Severity.ERROR,
                    f"record {index} has an empty or non-string {', '.join(repr(m) for m in missing)}",
                )
            )
            continue
        usable.append(row)
    return usable, findings


def _check_credentials(
    rows: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
) -> list[Finding]:
    """Scan every text field of every record for credential-shaped content.

    The finding names the *kind* of credential and quotes a redacted excerpt.
    The matched text itself is never returned to the caller, for the reason the
    detector exists: it is about to be written to disk.

    Args:
        rows: The decoded records.
        fields: The fields to scan.

    Returns:
        One aggregated finding per (field, kind) pair.
    """
    hits: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows:
        for name in fields:
            value = row.get(name)
            if not isinstance(value, str):
                continue
            kind = find_credential(value)
            if kind is not None:
                hits[(name, kind)].append(value)
    return [
        Finding(
            "credential_detected",
            Severity.ERROR,
            (
                f"{len(values)} record(s) contain credential-shaped text of kind "
                f"{kind!r} in field {name!r}; the value is withheld"
            ),
            _escape_and_cap(values),
        )
        for (name, kind), values in sorted(hits.items())
    ]


def _parse_records(
    rows: Sequence[Mapping[str, Any]],
    *,
    parser: Callable[[Mapping[str, Any]], Any],
    schema_version: str,
) -> tuple[list[Any], list[Finding]]:
    """Run each record through its schema's ``from_dict``.

    Records are grouped by failure message rather than reported one by one, so
    a systematic builder bug produces one finding with a count instead of ten
    thousand that bury everything else in the report.

    Args:
        rows: The decoded records.
        parser: The record class's ``from_dict``.
        schema_version: The version named in failure messages.

    Returns:
        The parsed records, and one finding per distinct parse failure.
    """
    parsed: list[Any] = []
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        try:
            parsed.append(parser(row))
        except DataValidationError as exc:
            grouped[str(exc)].append(index)
    findings = [
        Finding(
            "malformed_record",
            Severity.ERROR,
            f"{len(indices)} record(s) do not parse as {schema_version}: {message}",
            _cap(f"record {index}" for index in indices),
        )
        for message, indices in sorted(grouped.items())
    ]
    return parsed, findings


def _near_duplicate_findings(
    texts: Sequence[str],
) -> list[Finding]:
    """Group texts by :func:`near_duplicate_key` and report the collisions.

    A collision is a warning rather than an error because the key is an
    order-insensitive bag of words and two genuinely different requests can
    share one — *"log the bug"* and *"the bug is logged"* describe the same
    intent anyway, but *"review deadline"* and *"deadline review"* may be
    paraphrases a human chose deliberately. The cost of the false positive is a
    dropped example; the cost of the false negative is a leaky split.

    Args:
        texts: The primary text of each usable record.

    Returns:
        At most one warning, naming the number of colliding groups.
    """
    groups: dict[str, list[str]] = defaultdict(list)
    for text in texts:
        groups[near_duplicate_key(text)].append(text)
    colliding = [values for values in groups.values() if len(set(values)) > 1]
    if not colliding:
        return []
    return [
        Finding(
            "near_duplicate_text",
            Severity.WARNING,
            (
                f"{len(colliding)} group(s) share a bag of words with another record; "
                "check that none of them straddle a split boundary"
            ),
            _escape_and_cap(values[0] for values in colliding),
        )
    ]


def _parse_or_flag(
    rows: Sequence[Mapping[str, Any]],
    *,
    parser: Callable[[Mapping[str, Any]], Any],
    required: Sequence[str],
    text_fields: Sequence[str],
    schema_version: str,
) -> tuple[list[Any], list[Finding], list[Mapping[str, Any]]]:
    """Run the shared record-level gate: version, required fields, parse, secrets.

    Args:
        rows: The decoded records.
        parser: The record class's ``from_dict``.
        required: Fields that must be non-empty strings.
        text_fields: Fields to scan for credential-shaped text.
        schema_version: The version this dataset is supposed to carry.

    Returns:
        The parsed records, the findings in a fixed order, and the raw rows
        that survived, for checks that need the undecoded mapping.
    """
    usable, findings = _check_schema_versions(rows, schema_version=schema_version)
    credential_findings = _check_credentials(usable, text_fields)
    usable, field_findings = _check_required_fields(usable, required)
    parsed, parse_findings = _parse_records(usable, parser=parser, schema_version=schema_version)
    findings += field_findings + parse_findings + credential_findings
    return parsed, findings, usable


def validate_routing_dataset(
    records: Iterable[Mapping[str, Any]],
    *,
    known_intents: Iterable[str],
    max_class_ratio: float,
) -> ValidationReport:
    """Validate the labelled intent dataset the router classifier trains on.

    The checks that can stop the run are the ones that make the learned model
    wrong rather than merely mediocre: an unparseable record, a field the model
    would read as empty, a text that carries two different labels, and a
    credential pasted in by hand. Class imbalance is reported but never blocks
    — the intent histogram of real user requests is lopsided because real
    requests are, and a training run that refuses to see that distribution is
    training for a world that does not exist.

    Args:
        records: Decoded ``routing_intent.v1`` records.
        known_intents: The closed intent taxonomy. A label outside it is an
            error, not a new class: the router can only ever act on an intent
            the application knows how to execute.
        max_class_ratio: The largest tolerated ratio of the biggest class to the
            smallest non-empty one, above which imbalance is reported.

    Returns:
        A report carrying the intent histogram and every finding.

    Raises:
        ValueError: ``max_class_ratio`` is not positive.
    """
    if max_class_ratio <= 0:
        raise ValueError("max_class_ratio must be positive")

    rows = list(records)
    taxonomy = frozenset(known_intents)
    parsed, findings, _ = _parse_or_flag(
        rows,
        parser=RoutingExample.from_dict,
        required=("text", "intent"),
        text_fields=("text",),
        schema_version=SCHEMA_VERSION_ROUTING,
    )

    examples: list[RoutingExample] = parsed
    class_counts = Counter(example.intent for example in examples)

    invalid: dict[str, list[int]] = defaultdict(list)
    for index, example in enumerate(examples):
        if example.intent not in taxonomy:
            invalid[example.intent].append(index)
    findings.extend(
        Finding(
            "invalid_label",
            Severity.ERROR,
            f"{len(indices)} record(s) carry intent {intent!r}, which is not in the taxonomy",
            _cap(f"record {index}" for index in indices),
        )
        for intent, indices in sorted(invalid.items())
    )

    verbatim = [text for text, count in Counter(e.text for e in examples).items() if count > 1]
    if verbatim:
        findings.append(
            Finding(
                "duplicate_text",
                Severity.ERROR,
                f"{len(verbatim)} text(s) appear verbatim more than once",
                _escape_and_cap(verbatim),
            )
        )

    labels_by_text: dict[str, set[str]] = defaultdict(set)
    for example in examples:
        labels_by_text[normalize_text(example.text)].add(example.intent)
    contradictions = {
        text: sorted(labels) for text, labels in labels_by_text.items() if len(labels) > 1
    }
    if contradictions:
        findings.append(
            Finding(
                "contradictory_label",
                Severity.ERROR,
                (
                    f"{len(contradictions)} text(s) carry more than one intent after "
                    "normalisation; no training procedure recovers the intended boundary"
                ),
                _escape_and_cap(
                    f"{text!r} -> {', '.join(labels)}"
                    for text, labels in sorted(contradictions.items())
                ),
            )
        )

    # A contradictory group is already reported at ERROR, and by construction it
    # also collides on the near-duplicate key, so it is left out here to keep the
    # warning list about groups a reader can still act on.
    contradicted_keys = {near_duplicate_key(text) for text in contradictions}
    findings.extend(
        _near_duplicate_findings(
            [e.text for e in examples if near_duplicate_key(e.text) not in contradicted_keys]
        )
    )

    non_empty = [count for count in class_counts.values() if count > 0]
    if len(non_empty) > 1:
        ratio = max(non_empty) / min(non_empty)
        if ratio > max_class_ratio:
            findings.append(
                Finding(
                    "class_imbalance",
                    Severity.WARNING,
                    (
                        f"the largest class holds {max(non_empty)} examples against "
                        f"{min(non_empty)} in the smallest (ratio {ratio:.1f}), above the "
                        f"tolerated {max_class_ratio:.1f}"
                    ),
                )
            )

    counts: dict[str, int] = {
        "records": len(rows),
        "usable": len(examples),
        "classes": len(class_counts),
        "known_intents": len(taxonomy),
        "invalid_labels": sum(len(indices) for indices in invalid.values()),
        "contradictions": len(contradictions),
    }
    counts.update({f"class:{intent}": count for intent, count in sorted(class_counts.items())})
    return ValidationReport(
        name="routing_intent.v1",
        schema_version=SCHEMA_VERSION_ROUTING,
        findings=tuple(findings),
        counts=counts,
    )


def validate_qwen_dataset(records: Iterable[Mapping[str, Any]]) -> ValidationReport:
    """Validate the supervised fine-tuning dataset for Qwen3-8B.

    The failure this dataset is most exposed to is the contradiction, and it
    looks different here: not one text with two labels, but one instruction with
    two different responses. A QLoRA run fitted on that pair will reproduce
    whichever target it saw last and the comparison against the base model will
    look like a result. Near-duplicate instructions get the same treatment they
    get in the routing set, and responses are checked for credential-shaped text
    because a system preamble is exactly the kind of thing someone edits by hand.

    Args:
        records: Decoded ``qwen_sft.v1`` records.

    Returns:
        A report carrying the provenance and template histograms.
    """
    rows = list(records)
    parsed, findings, _ = _parse_or_flag(
        rows,
        parser=QwenExample.from_dict,
        required=("instruction", "response", "system"),
        text_fields=("instruction", "response", "system"),
        schema_version=SCHEMA_VERSION_QWEN,
    )
    examples: list[QwenExample] = parsed

    duplicated = [
        pair
        for pair, count in Counter((e.instruction, e.response) for e in examples).items()
        if count > 1
    ]
    if duplicated:
        findings.append(
            Finding(
                "duplicate_example",
                Severity.ERROR,
                f"{len(duplicated)} instruction/response pair(s) appear more than once",
                _escape_and_cap(
                    f"{instruction}\n-> {response}" for instruction, response in duplicated
                ),
            )
        )

    responses_by_instruction: dict[str, set[str]] = defaultdict(set)
    for example in examples:
        responses_by_instruction[normalize_text(example.instruction)].add(example.response)
    contradictions = {
        instruction: sorted(responses)
        for instruction, responses in responses_by_instruction.items()
        if len(responses) > 1
    }
    if contradictions:
        findings.append(
            Finding(
                "contradictory_response",
                Severity.ERROR,
                (
                    f"{len(contradictions)} instruction(s) pair with more than one response "
                    "after normalisation"
                ),
                _escape_and_cap(
                    f"{instruction!r} -> {len(responses)} distinct responses"
                    for instruction, responses in sorted(contradictions.items())
                ),
            )
        )

    contradicted_keys = {near_duplicate_key(i) for i in contradictions}
    findings.extend(
        _near_duplicate_findings(
            [
                e.instruction
                for e in examples
                if near_duplicate_key(e.instruction) not in contradicted_keys
            ]
        )
    )

    provenance = Counter(str(example.provenance) for example in examples)
    templates = Counter(example.template_id for example in examples if example.template_id)
    counts: dict[str, int] = {
        "records": len(rows),
        "usable": len(examples),
        "distinct_instructions": len(responses_by_instruction),
        "contradictions": len(contradictions),
        "templates": len(templates),
    }
    counts.update({f"provenance:{key}": value for key, value in sorted(provenance.items())})
    return ValidationReport(
        name="qwen_sft.v1",
        schema_version=SCHEMA_VERSION_QWEN,
        findings=tuple(findings),
        counts=counts,
    )


def validate_feature_dataset(records: Iterable[Mapping[str, Any]]) -> ValidationReport:
    """Validate the wrapped feature rows the tabular models train on.

    There is no label here, so the label checks do not apply; what does apply
    is the null contract. A row whose columns could not be computed is
    *legitimate* — that is what ``available`` is for — but a dataset where most
    rows are hollow will produce a model that learned the imputation rather than
    the signal, so the share of complete rows is reported. The schema's
    ``from_dict`` already refuses the failure that matters most, a column
    marked unavailable that still carries a value, because that is the one that
    silently reintroduces the fabricated zero the Phases 8/9 remediation removed.

    Args:
        records: Decoded ``nexo_feature_rows.v1`` records.

    Returns:
        A report carrying the source-schema and completeness histograms.
    """
    rows = list(records)
    parsed, findings, _ = _parse_or_flag(
        rows,
        parser=FeatureRow.from_dict,
        required=("subject", "source_schema_version"),
        text_fields=("subject",),
        schema_version=SCHEMA_VERSION_FEATURES,
    )
    feature_rows: list[FeatureRow] = parsed

    duplicated = [
        identity
        for identity, count in Counter(
            (row.source_schema_version, row.subject) for row in feature_rows
        ).items()
        if count > 1
    ]
    if duplicated:
        findings.append(
            Finding(
                "duplicate_row",
                Severity.ERROR,
                f"{len(duplicated)} subject(s) appear more than once within one schema version",
                _escape_and_cap(f"{version}::{subject}" for version, subject in duplicated),
            )
        )

    incomplete = [row for row in feature_rows if not row.is_complete()]
    if incomplete:
        findings.append(
            Finding(
                "incomplete_row",
                Severity.WARNING,
                (
                    f"{len(incomplete)} row(s) have at least one column that could not be "
                    "computed; the availability mask is doing the work of a null"
                ),
                _escape_and_cap(
                    f"{row.source_schema_version}::{row.subject} missing "
                    f"{', '.join(row.unavailable_columns())}"
                    for row in incomplete
                ),
            )
        )

    source_versions = Counter(row.source_schema_version for row in feature_rows)
    provenance = Counter(str(row.provenance) for row in feature_rows)
    counts: dict[str, int] = {
        "records": len(rows),
        "usable": len(feature_rows),
        "subjects": len({row.subject for row in feature_rows}),
        "complete_rows": len(feature_rows) - len(incomplete),
        "incomplete_rows": len(incomplete),
        "schema_versions": len(source_versions),
    }
    counts.update({f"source_schema:{key}": value for key, value in sorted(source_versions.items())})
    counts.update({f"provenance:{key}": value for key, value in sorted(provenance.items())})
    return ValidationReport(
        name="nexo_feature_rows.v1",
        schema_version=SCHEMA_VERSION_FEATURES,
        findings=tuple(findings),
        counts=counts,
    )


def validate_splits(
    assignments: Mapping[str, Iterable[str]],
    *,
    duplicate_key_fn: Callable[[str], str],
) -> ValidationReport:
    """Verify no near-duplicate straddles a split boundary.

    Leakage is the one defect that makes an accuracy number a lie rather than
    merely optimistic: an example that appears in training and in test means the
    model was scored on something it had already read, and the gap is invisible
    in the log. ``duplicate_key_fn`` is supplied rather than hardcoded so the
    caller can pass the same key the splitter grouped by — if the two ever
    disagree, the audit checks a different partition from the one actually used,
    which is worse than not checking at all.

    Args:
        assignments: Split name mapped to the items placed in it.
        duplicate_key_fn: Maps an item to the key used to group near-duplicates.

    Returns:
        A report carrying per-split sizes and the number of leaked keys.
    """
    splits_by_key: dict[str, dict[str, str]] = defaultdict(dict)
    counts: dict[str, int] = {}
    total = 0
    for name, items in sorted(assignments.items()):
        values = list(items)
        counts[f"split:{name}"] = len(values)
        total += len(values)
        for value in values:
            key = duplicate_key_fn(value)
            splits_by_key[key][name] = value

    findings: list[Finding] = [
        Finding(
            "empty_split",
            Severity.WARNING,
            f"split {name!r} received no rows; an evaluation split with nothing in it "
            "reports nothing",
        )
        for name, items in sorted(assignments.items())
        if not list(items)
    ]

    leaked = {key: splits for key, splits in splits_by_key.items() if len(splits) > 1}
    if leaked:
        findings.append(
            Finding(
                "split_leakage",
                Severity.ERROR,
                (
                    f"{len(leaked)} duplicate group(s) appear in more than one split; "
                    "the held-out score is measuring memorisation"
                ),
                _escape_and_cap(
                    f"{key} -> {', '.join(sorted(splits))}"
                    for key, splits in sorted(leaked.items())
                ),
            )
        )

    counts["total_items"] = total
    counts["unique_keys"] = len(splits_by_key)
    counts["leaked_keys"] = len(leaked)
    return ValidationReport(
        name="splits",
        schema_version=_SPLIT_SCHEMA_VERSION,
        findings=tuple(findings),
        counts=counts,
    )


def validate_no_credentials(texts: Iterable[str]) -> ValidationReport:
    """Scan free text — a manifest, a log, a prompt — for credentials.

    This is the gate for the places a dataset is not: the run manifest, the
    evaluation transcript, the notebook that assembles the prompt. Each is
    assembled by hand or from environment values, which is exactly the code path
    that has put a key into a log line somewhere before.

    Args:
        texts: The strings to scan.

    Returns:
        A report whose findings name the credential kind and quote nothing.
    """
    values = list(texts)
    hits: dict[str, list[str]] = defaultdict(list)
    for text in values:
        if not isinstance(text, str):
            continue
        kind = find_credential(text)
        if kind is not None:
            hits[kind].append(text)
    findings = [
        Finding(
            "credential_detected",
            Severity.ERROR,
            f"{len(samples)} text(s) contain credential-shaped content of kind {kind!r}",
            _escape_and_cap(samples),
        )
        for kind, samples in sorted(hits.items())
    ]
    return ValidationReport(
        name="credential_scan",
        schema_version=_SCAN_SCHEMA_VERSION,
        findings=tuple(findings),
        counts={"texts": len(values), "flagged": sum(len(samples) for samples in hits.values())},
    )


def assert_clean(*reports: ValidationReport) -> None:
    """Stop the pipeline unless every report passed.

    Deliberately raises rather than returning a verdict: a caller that ignores
    a returned boolean is the reason a corrupt dataset reaches a training run in
    the first place. The exception is :class:`~ml.datasets.schema.DataValidationError`,
    which the pipeline does not catch — failing loudly is the entire purpose.

    Args:
        reports: The reports to gate on.

    Raises:
        DataValidationError: At least one report carries an error finding.
    """
    failures = [report for report in reports if not report.passed]
    if not failures:
        return
    summary = "; ".join(
        f"{report.name}: "
        + ", ".join(f"{finding.code} ({finding.message})" for finding in report.errors())
        for report in failures
    )
    raise DataValidationError(f"dataset validation failed: {summary}")


__all__ = [
    "VALIDATION_REPORT_VERSION",
    "Finding",
    "Severity",
    "ValidationReport",
    "assert_clean",
    "validate_feature_dataset",
    "validate_no_credentials",
    "validate_qwen_dataset",
    "validate_routing_dataset",
    "validate_splits",
]
