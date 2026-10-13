"""The strict Jarvis tool-call parser: only well-formed <tool_call> blocks are calls."""

from __future__ import annotations

import pytest

from app.jarvis.toolcalls import parse_turn


def call(body: str, pre: str = "") -> str:
    return f"{pre}<tool_call>\n{body}\n</tool_call>"


def test_plain_text_is_content_with_no_calls():
    p = parse_turn("Hello there.")
    assert p.error is None and p.calls == [] and p.content == "Hello there."


def test_one_call_with_preface():
    p = parse_turn(call('{"name": "web_search", "arguments": {"query": "x"}}', "Let me look. "))
    assert p.error is None and p.content == "Let me look."
    assert p.calls == [{"name": "web_search", "arguments": {"query": "x"}}]


def test_parallel_calls():
    t = (
        call('{"name": "fs_read_file", "arguments": {"path": "a"}}')
        + "\n"
        + call('{"name": "fs_read_file", "arguments": {"path": "b"}}')
    )
    assert len(parse_turn(t).calls) == 2


def test_empty_think_block_and_end_token_are_ignored():
    p = parse_turn("<think>\n\n</think>\n\nHi.<|im_end|>")
    assert p.error is None and p.content == "Hi."


@pytest.mark.parametrize(
    "text,code",
    [
        (call('{"name": "web_search", "arguments": {"query": "x"'), "invalid_json"),
        (call('["web_search"]'), "not_an_object"),
        (call('{"name": "web_search", "arguments": {}, "id": 1}'), "unexpected_keys"),
        (call('{"name": "Web Search", "arguments": {}}'), "bad_name"),
        (call('{"name": "web_search", "arguments": "{\\"query\\": 1}"}'), "arguments_not_object"),
        (call('{"name": "web_search", "name": "fs_read_file", "arguments": {}}'), "duplicate_key"),
        ('<tool_call>{"name": "x", "arguments": {}}', "unbalanced_tags"),
        ("</tool_call> hi <tool_call>", "unbalanced_tags"),
        (
            call('{"name": "web_search", "arguments": {}}') + " and then I saved it.",
            "trailing_text",
        ),
        ('<tool_response>{"ok":true}</tool_response>', "model_emitted_tool_response"),
        (
            "".join(
                call(f'{{"name": "web_fetch", "arguments": {{"url": "u{i}"}}}}') for i in range(5)
            ),
            "too_many_calls",
        ),
    ],
)
def test_malformed_calls_are_errors_not_calls(text, code):
    p = parse_turn(text)
    assert p.error == code and p.calls == []


def test_a_described_call_in_prose_is_not_a_call():
    p = parse_turn(
        'Proposed call: `fs_write_file(path="notes.md", content="hi")`. Okay to proceed?'
    )
    assert p.error is None and p.calls == []


def test_deeply_nested_call_body_is_an_error_not_a_crash():
    deep = "<tool_call>" + "[" * 5000 + "]" * 5000 + "</tool_call>"
    assert parse_turn(deep).error == "invalid_json"


@pytest.mark.parametrize("bad", [None, 1, b"<tool_call>"])
def test_non_text_generation_is_malformed(bad):
    assert parse_turn(bad).error == "malformed_output"
