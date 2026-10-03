"""Text normalisation, near-duplicate keys and credential detection.

Three jobs, all of them pure and all of them stdlib.

**Normalisation** gives the validator and the splitter one canonical form of a
string, so *"Add the task"* and *"add  the task!"* are recognised as the same
example rather than as two.

**Near-duplicate keys** go further than equality. Two examples that share the
same *bag* of words in a different order describe the same thing, and letting
them straddle a split boundary is leakage the accuracy number will quietly
flatter. The key sorts the normalised tokens, which catches reordering and
duplication without needing an embedding model.

**Credential detection** is the security gate. Every dataset, manifest, report
and log this pipeline writes is scanned for credential-shaped text before it is
allowed to leave the process. The detector never echoes what it found — it
returns the *kind* only, because a validator that prints the secret it is
validating has leaked it a second time. It is deliberately generic rather than
tuned to one provider: the brief singles out the Kaggle access token, but a
pipeline that only knows about that one token is a pipeline that ships the
Hugging Face key that arrived with it.

Nothing in this module reads, opens or imports ``~/.kaggle/access_token``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

#: Characters stripped during normalisation. Deliberately *not* stripped: the
#: characters that carry meaning in a task title, an error message or a commit
#: subject, so that two examples differing only by ``->`` stay distinct.
_STRIP_PUNCTUATION = re.compile(r"[^\w\s<>=/+\-#.]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")

#: Credential shapes, checked in order. Each entry is ``(kind, pattern)``. The
#: patterns match the *value*; the surrounding assignment syntax is handled by
#: the generic assignment rule further down.
_CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("kaggle_token", re.compile(r"\bKAGG(?:LE)?_[A-Za-z0-9_]{16,}\b")),
    ("huggingface_token", re.compile(r"\bhf_[A-Za-z0-9]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b")),
    ("nvidia_key", re.compile(r"\bnvapi-[A-Za-z0-9_\-]{20,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}")),
    ("slack_token", re.compile(r"\bxox[abprs]-[0-9A-Za-z\-]{10,}")),
    ("stripe_key", re.compile(r"\b[rs]k_(?:live|test)_[0-9A-Za-z]{16,}")),
    ("bearer_token", re.compile(r"\b[Bb]earer\s+[A-Za-z0-9_\-\.=]{20,}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.")),
)

#: ``name = "value"`` where the name reads like a secret and the value is long
#: enough not to be a placeholder. Checked after the vendor shapes above so a
#: recognisable key is reported as itself rather than as a generic assignment.
_SECRET_NAME = r"(?:api[_-]?key|secret[_-]?key|secret|password|passwd|token|access[_-]?token|auth)"
_ASSIGNMENT = re.compile(
    rf"\b{_SECRET_NAME}\b\s*[:=]\s*[\"']?([^\s\"',;}}]{{12,}})[\"']?",
    re.IGNORECASE,
)

#: Values that look assigned to a secret name but are obviously not secrets.
#: A validator that cries wolf gets switched off, so the obvious placeholders are
#: allowed through by name rather than by length.
_PLACEHOLDER_VALUES = frozenset(
    {
        "changeme",
        "change-me",
        "placeholder",
        "redacted",
        "example",
        "dummy",
        "notarealtoken",
        "your_token_here",
        "<token>",
        "xxx",
    }
)


def normalize_text(text: str) -> str:
    """Reduce a string to its canonical comparable form.

    Lowercases, strips accents, removes punctuation and collapses whitespace.
    Accent folding matters because ``réflexion`` and ``reflexion`` are the same
    word to a tokenizer's detriment; punctuation folding matters because a task
    title picked up from a UI arrives with or without a trailing full stop.

    Args:
        text: The string to normalise.

    Returns:
        The normalised string, possibly empty.
    """
    folded = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in folded if not unicodedata.combining(ch))
    without_punct = _STRIP_PUNCTUATION.sub(" ", stripped.casefold())
    return _WHITESPACE.sub(" ", without_punct).strip()


def tokenize(text: str) -> list[str]:
    """Split a string into normalised word tokens.

    Args:
        text: The string to tokenise.

    Returns:
        The tokens, in order, with empties removed.
    """
    return [token for token in normalize_text(text).split(" ") if token]


def ngrams(tokens: Sequence[str], size: int) -> list[tuple[str, ...]]:
    """Build character n-grams over a token sequence.

    Args:
        tokens: The tokens to window over.
        size: The window length. Must be positive.

    Returns:
        One tuple per window; the input itself when it is shorter than ``size``.

    Raises:
        ValueError: ``size`` is not positive.
    """
    if size < 1:
        raise ValueError("n-gram size must be positive")
    if len(tokens) < size:
        return [tuple(tokens)]
    return [tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1)]


def near_duplicate_key(text: str) -> str:
    """Build an order-insensitive signature for near-duplicate detection.

    Sorting the normalised tokens means two phrasings built from the same words
    collide, so a near-duplicate cannot be split across train and test even when
    the surface wording differs. Two distinct examples sharing a rare bag of
    words are a false positive; that is the safe direction to err in, because it
    costs a row rather than inflating a score.

    Args:
        text: The string to key.

    Returns:
        A stable key. Distinct from the plain normalised form unless the text is
        already in canonical word order.
    """
    return " ".join(sorted(tokenize(text)))


def find_credential(text: str) -> str | None:
    """Report whether a string contains credential-shaped text.

    The return value is the **kind**, never the matched secret. A caller that
    wants to log why a record was rejected gets ``"kaggle_token"``; a caller
    that wants to log what it rejected gets nothing, because there is nothing
    safe for it to print.

    Args:
        text: The string to scan.

    Returns:
        The kind of credential found, or None when the string is clean.
    """
    for kind, pattern in _CREDENTIAL_PATTERNS:
        if pattern.search(text):
            return kind
    for match in _ASSIGNMENT.finditer(text):
        value = match.group(1).strip().casefold()
        if value in _PLACEHOLDER_VALUES:
            continue
        if not value.startswith(("<", "{", "$", "%")):
            return "assigned_secret"
    return None


def contains_credential(text: str) -> bool:
    """Whether a string contains credential-shaped text.

    Args:
        text: The string to scan.

    Returns:
        True when something credential-shaped was found.
    """
    return find_credential(text) is not None


def redact(text: str, placeholder: str = "[REDACTED]") -> str:
    """Replace credential-shaped runs with a placeholder.

    Used when a finding genuinely has to be surfaced in a report: the report
    states that a credential was present and where, without reproducing it.

    Args:
        text: The string to redact.
        placeholder: What to put in place of a match.

    Returns:
        The redacted string.
    """
    for _, pattern in _CREDENTIAL_PATTERNS:
        text = pattern.sub(placeholder, text)
    return _ASSIGNMENT.sub(lambda m: m.group(0).replace(m.group(1), placeholder), text)


__all__ = [
    "contains_credential",
    "find_credential",
    "near_duplicate_key",
    "ngrams",
    "normalize_text",
    "redact",
    "tokenize",
]
