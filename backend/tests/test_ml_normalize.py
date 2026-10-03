"""Normalisation, near-duplicate keys and credential detection.

Three pure functions that everything else in the pipeline leans on. Two of them
are security-relevant, and the tests below treat them that way:

* **Credential detection** is the gate every dataset, manifest, report and log
  passes through. The contract it has to honour is that it returns the *kind* of
  credential and never the value — a validator that prints the secret it is
  validating has leaked it a second time, into a file, a CI log and a ticket.
* **Near-duplicate keys** define what "leakage" means for the splitter and the
  leakage audit. A key that is too strict lets a paraphrase straddle a split and
  the held-out score becomes a measurement of memorisation.

Every credential-shaped string in this module is fabricated. They are built from
repeated characters rather than pasted from anywhere, and none of them is a real
token.
"""

from __future__ import annotations

import pytest

from ml.preprocessing.normalize import (
    contains_credential,
    find_credential,
    near_duplicate_key,
    ngrams,
    normalize_text,
    redact,
    tokenize,
)

# Fabricated credential-shaped strings, one per recognised provider family. The
# `*_secret` cases exercise the generic assignment rule rather than a vendor
# pattern, which is what catches a key the table has never heard of.
FAKE_KAGGLE = "KAGGLE_" + "a" * 32
FAKE_HF = "hf_" + "Ab9" * 9
FAKE_GITHUB = "ghp_" + "B2" * 14
FAKE_GITHUB_PAT = "github_pat_" + "C3" * 14
FAKE_OPENAI = "sk-proj-" + "dE4" * 9
FAKE_NVIDIA = "nvapi-" + "fE5" * 9
FAKE_AWS = "AKIA" + "G6" * 8
FAKE_GOOGLE = "AIza" + "hF7" * 11
FAKE_SLACK = "xoxb-1234567890-abcdefghij"
FAKE_STRIPE = "sk_live_" + "iG8" * 10
FAKE_BEARER = "Bearer " + "jH9" * 10
FAKE_PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----\nZm9v\n-----END RSA PRIVATE KEY-----"
FAKE_ASSIGNED = "api_key = 9f8a7b6c5d4e3f2a1b0c"


def test_normalize_text_folds_case_accents_punctuation_and_whitespace():
    assert normalize_text("  Add   the TASK!  ") == "add the task"
    assert normalize_text("Réflexion") == "reflexion"
    assert normalize_text("a\n\tb   c") == "a b c"


def test_normalize_text_keeps_the_characters_that_carry_meaning():
    """Punctuation that carries meaning survives.

    `->`, `<>`, `=`, `/`, `+`, `-`, `#` and `.` are all kept: two messages
    differing only in one of them describe different things.
    """
    assert normalize_text("migrate -> v2") == "migrate -> v2"
    assert normalize_text("deadline <today>") == "deadline <today>"
    assert normalize_text("weight = high") == "weight = high"
    assert normalize_text("api/v1/tasks") == "api/v1/tasks"
    assert normalize_text("RFC-7231") == "rfc-7231"
    assert normalize_text("v1.2") == "v1.2"


def test_normalize_text_is_idempotent():
    once = normalize_text("  Réflexion: the API contract!  ")

    assert normalize_text(once) == once


def test_normalize_text_of_punctuation_only_is_empty():
    assert normalize_text("!!! ???") == ""


def test_tokenize_splits_the_normalised_form():
    assert tokenize("Add the task!") == ["add", "the", "task"]
    assert tokenize("") == []
    assert tokenize("   ") == []


def test_ngrams_windows_over_the_sequence():
    assert ngrams(["a", "b", "c"], 2) == [("a", "b"), ("b", "c")]
    assert ngrams(["a", "b", "c"], 1) == [("a",), ("b",), ("c",)]
    assert ngrams(["a", "b", "c", "d"], 3) == [("a", "b", "c"), ("b", "c", "d")]


def test_ngrams_of_a_sequence_shorter_than_the_window_is_the_whole_sequence():
    assert ngrams(["a", "b"], 5) == [("a", "b")]
    assert ngrams([], 3) == [()]


@pytest.mark.parametrize("size", [0, -1, -10])
def test_ngrams_refuses_a_non_positive_window(size):
    with pytest.raises(ValueError, match="n-gram size must be positive"):
        ngrams(["a", "b"], size)


def test_near_duplicate_key_is_insensitive_to_word_order():
    assert near_duplicate_key("Add the task for Friday") == near_duplicate_key(
        "for Friday add the task"
    )


def test_near_duplicate_key_is_insensitive_to_punctuation_and_case():
    assert near_duplicate_key("Add the task for Friday!") == near_duplicate_key(
        "add the task for friday"
    )


def test_near_duplicate_key_separates_different_bags_of_words():
    assert near_duplicate_key("add the task") != near_duplicate_key("delete the project")


def test_near_duplicate_key_is_stable():
    text = "Show my open tasks"

    assert near_duplicate_key(text) == near_duplicate_key(text)


def test_near_duplicate_key_sorts_its_tokens():
    assert near_duplicate_key("zebra apple mango") == "apple mango zebra"


@pytest.mark.parametrize(
    ("sample", "kind"),
    [
        (FAKE_KAGGLE, "kaggle_token"),
        (FAKE_HF, "huggingface_token"),
        (FAKE_GITHUB, "github_token"),
        (FAKE_GITHUB_PAT, "github_pat"),
        (FAKE_OPENAI, "openai_key"),
        (FAKE_NVIDIA, "nvidia_key"),
        (FAKE_AWS, "aws_access_key"),
        (FAKE_GOOGLE, "google_api_key"),
        (FAKE_SLACK, "slack_token"),
        (FAKE_STRIPE, "stripe_key"),
        (FAKE_BEARER, "bearer_token"),
        (FAKE_PRIVATE_KEY, "private_key_block"),
        (FAKE_ASSIGNED, "assigned_secret"),
    ],
)
def test_find_credential_reports_the_kind_and_never_the_secret(sample, kind):
    detected = find_credential(sample)

    assert detected == kind
    # The kind is a closed vocabulary of short words; the secret is not in it.
    assert sample not in detected
    assert find_credential(sample).islower()


@pytest.mark.parametrize(
    "sample",
    [
        "Mark the API contract task as done",
        "What is the weather in Porto tomorrow?",
        "How productive was I last week?",
        "KAGGLE is not a credential",
        "hf is not a credential either",
        "sk_short",
        "AKIA1234",
    ],
)
def test_find_credential_leaves_ordinary_text_alone(sample):
    assert find_credential(sample) is None
    assert not contains_credential(sample)


@pytest.mark.parametrize(
    "text",
    [
        "api_key = your_token_here",
        "password: changeme",
        "token = <token>",
        "api_key = ${KAGGLE_TOKEN}",
        "secret = placeholder",
    ],
)
def test_placeholders_are_not_flagged(text):
    """A validator that cries wolf gets switched off, so placeholders pass by name."""
    assert find_credential(text) is None


def test_contains_credential_agrees_with_find_credential():
    assert contains_credential(FAKE_KAGGLE)
    assert not contains_credential("nothing to see here")


def test_redact_removes_every_recognised_shape():
    for sample in (
        FAKE_KAGGLE,
        FAKE_HF,
        FAKE_GITHUB,
        FAKE_OPENAI,
        FAKE_AWS,
        FAKE_SLACK,
        FAKE_BEARER,
        FAKE_PRIVATE_KEY,
    ):
        cleaned = redact(sample)

        assert find_credential(cleaned) is None, sample[:12]
        assert "[REDACTED]" in cleaned


def test_redact_removes_the_value_of_a_secret_assignment():
    cleaned = redact(FAKE_ASSIGNED)

    assert find_credential(cleaned) is None
    assert "9f8a7b6c5d4e3f2a1b0c" not in cleaned


def test_redact_leaves_clean_text_untouched():
    text = "Mark the API contract task as done."

    assert redact(text) == text


def test_redact_accepts_a_custom_placeholder():
    cleaned = redact(FAKE_KAGGLE, placeholder="<hidden>")

    assert "<hidden>" in cleaned
    assert find_credential(cleaned) is None


def test_redact_is_idempotent():
    once = redact(FAKE_ASSIGNED)

    assert redact(once) == once


def test_a_credential_embedded_in_a_dataset_row_is_found_and_redacted():
    """The end-to-end shape of the gate: a row the builder pasted a key into."""
    row = f"my kaggle token is {FAKE_KAGGLE} please use it"

    assert find_credential(row) == "kaggle_token"
    cleaned = redact(row)
    assert FAKE_KAGGLE not in cleaned
    assert find_credential(cleaned) is None


def test_detection_is_deliberately_generic_rather_than_provider_tuned():
    """A pipeline that only knows one provider ships the key that arrived with it.

    The assignment rule keys on the *name* looking like a secret and on the value
    being long enough not to be a placeholder; it does not care which vendor
    issued the value, which is how an unrecognised key is still caught.
    """
    for name in ("api_key", "api-key", "secret_key", "password", "passwd", "token", "auth"):
        assert find_credential(f"{name} = 7a3f9c2e1b8d4056a7c3") == "assigned_secret", name

    # A name that does not read like a secret is not one, whatever it carries.
    assert find_credential("vendor = 7a3f9c2e1b8d4056a7c3") is None
