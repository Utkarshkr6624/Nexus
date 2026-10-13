import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.eval.toolparse import (ToolCallParseError, format_tool_call, mentions_tool_call, parse_tool_calls, try_parse_tool_calls)  # noqa: E402

GOOD = '<tool_call>\n{"name": "web_search", "arguments": {"query": "node version"}}\n</tool_call>'


class ParserTests(unittest.TestCase):
    def test_valid_single_call(self):
        calls = parse_tool_calls(GOOD)
        self.assertEqual(calls, [{"name": "web_search", "arguments": {"query": "node version"}}])

    def test_no_call_is_empty_list(self):
        self.assertEqual(parse_tool_calls("The capital of France is Paris."), [])
        self.assertFalse(mentions_tool_call("The capital of France is Paris."))

    def test_content_before_call_allowed(self):
        r = try_parse_tool_calls("Let me look that up.\n" + GOOD)
        self.assertTrue(r.ok)
        self.assertEqual(r.content, "Let me look that up.")
        self.assertEqual(len(r.calls), 1)

    def test_multiple_calls(self):
        two = GOOD + "\n" + format_tool_call("fs_read_file", {"path": "a.txt"})
        self.assertEqual([c["name"] for c in parse_tool_calls(two)], ["web_search", "fs_read_file"])

    def test_roundtrip_with_template_format(self):
        s = format_tool_call("app_create_task", {"title": "Renew passport", "due": "30 June"})
        self.assertTrue(s.startswith("<tool_call>\n{") and s.endswith("}\n</tool_call>"))
        self.assertEqual(parse_tool_calls(s)[0]["arguments"]["due"], "30 June")

    def test_special_tail_tokens_ignored(self):
        self.assertEqual(len(parse_tool_calls(GOOD + "<|im_end|>")), 1)

    def test_malformed_json(self):
        r = try_parse_tool_calls('<tool_call>\n{"name": "web_search", "arguments": {"query": }\n</tool_call>')
        self.assertEqual(r.error, "invalid_json")
        with self.assertRaises(ToolCallParseError) as cm:
            parse_tool_calls('<tool_call>{not json}</tool_call>')
        self.assertEqual(cm.exception.code, "invalid_json")

    def test_unbalanced_tags(self):
        self.assertEqual(try_parse_tool_calls('<tool_call>\n{"name": "x", "arguments": {}}').error, "unbalanced_tags")
        self.assertEqual(try_parse_tool_calls('{"name": "x", "arguments": {}}\n</tool_call>').error, "unbalanced_tags")

    def test_nested_call(self):
        self.assertEqual(try_parse_tool_calls('<tool_call>\n<tool_call>{"name":"a","arguments":{}}</tool_call></tool_call>').error, "nested_tool_call")

    def test_missing_keys(self):
        self.assertEqual(try_parse_tool_calls('<tool_call>{"arguments": {}}</tool_call>').error, "missing_name")
        self.assertEqual(try_parse_tool_calls('<tool_call>{"name": "x"}</tool_call>').error, "missing_arguments")

    def test_unexpected_keys(self):
        self.assertEqual(try_parse_tool_calls('<tool_call>{"name": "x", "arguments": {}, "id": 1}</tool_call>').error, "unexpected_keys")

    def test_arguments_must_be_object(self):
        self.assertEqual(try_parse_tool_calls('<tool_call>{"name": "x", "arguments": "{\\"a\\": 1}"}</tool_call>').error, "arguments_not_object")
        self.assertEqual(try_parse_tool_calls('<tool_call>{"name": "x", "arguments": [1]}</tool_call>').error, "arguments_not_object")

    def test_bad_name(self):
        self.assertEqual(try_parse_tool_calls('<tool_call>{"name": "web search", "arguments": {}}</tool_call>').error, "bad_name")
        self.assertEqual(try_parse_tool_calls('<tool_call>{"name": 5, "arguments": {}}</tool_call>').error, "bad_name")

    def test_duplicate_keys_rejected(self):
        self.assertEqual(try_parse_tool_calls('<tool_call>{"name": "x", "arguments": {"a": 1, "a": 2}}</tool_call>').error, "duplicate_key")

    def test_not_an_object(self):
        self.assertEqual(try_parse_tool_calls('<tool_call>[1, 2]</tool_call>').error, "not_an_object")

    def test_trailing_text_strict_vs_lenient(self):
        t = GOOD + "\nHere are the results: 42."
        self.assertEqual(try_parse_tool_calls(t).error, "trailing_text")
        r = try_parse_tool_calls(t, strict=False)
        self.assertTrue(r.ok)
        self.assertEqual(len(r.calls), 1)

    def test_model_must_not_write_tool_response(self):
        t = GOOD + "\n<tool_response>\n{\"ok\": true}\n</tool_response>"
        self.assertEqual(try_parse_tool_calls(t).error, "model_emitted_tool_response")

    def test_text_between_calls(self):
        t = GOOD + "\nand then\n" + GOOD
        self.assertEqual(try_parse_tool_calls(t).error, "text_between_calls")

    def test_too_many_calls(self):
        t = "\n".join([GOOD] * 9)
        self.assertEqual(try_parse_tool_calls(t).error, "too_many_calls")

    def test_prose_proposed_call_counts_as_markup(self):
        self.assertTrue(mentions_tool_call("Proposed call: `fs_write_file(path=\"a\")`"))

    def test_non_ascii_arguments(self):
        s = format_tool_call("web_search", {"query": "café 東京"})
        self.assertEqual(parse_tool_calls(s)[0]["arguments"]["query"], "café 東京")


if __name__ == "__main__":
    unittest.main()
