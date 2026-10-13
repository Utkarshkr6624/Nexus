import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.eval import runner  # noqa: E402
from pipeline.eval.schema import validate, validate_tool_call  # noqa: E402

DEFS = list(runner.tool_defs().values())


class SchemaTests(unittest.TestCase):
    def test_types(self):
        self.assertEqual(validate("a", {"type": "string"}), [])
        self.assertTrue(validate(1, {"type": "string"}))
        self.assertTrue(validate(True, {"type": "integer"}))          # bool is not an integer
        self.assertEqual(validate(3, {"type": "number"}), [])
        self.assertEqual(validate(3.5, {"type": ["number", "null"]}), [])
        self.assertEqual(validate(None, {"type": ["number", "null"]}), [])

    def test_required_and_additional(self):
        sch = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"], "additionalProperties": False}
        self.assertEqual(validate({"a": "x"}, sch), [])
        self.assertTrue(any("missing required" in e for e in validate({}, sch)))
        self.assertTrue(any("unexpected property" in e for e in validate({"a": "x", "b": 1}, sch)))

    def test_enum_range_length_pattern(self):
        self.assertTrue(validate("x", {"enum": ["a", "b"]}))
        self.assertTrue(validate(0, {"type": "integer", "minimum": 1}))
        self.assertTrue(validate(11, {"type": "integer", "maximum": 10}))
        self.assertTrue(validate("", {"type": "string", "minLength": 1}))
        self.assertTrue(validate("abc", {"type": "string", "pattern": "^[0-9]+$"}))

    def test_arrays(self):
        sch = {"type": "array", "items": {"type": "integer"}, "minItems": 1}
        self.assertEqual(validate([1, 2], sch), [])
        self.assertTrue(validate([], sch))
        self.assertTrue(validate([1, "x"], sch))

    def test_tool_call_valid(self):
        self.assertEqual(validate_tool_call({"name": "fs_read_file", "arguments": {"path": "a.txt"}}, DEFS), [])

    def test_tool_call_unknown_tool(self):
        errs = validate_tool_call({"name": "nope", "arguments": {}}, DEFS)
        self.assertTrue(errs and "unknown tool" in errs[0])

    def test_tool_call_missing_required(self):
        self.assertTrue(any("missing required" in e for e in validate_tool_call({"name": "app_create_event", "arguments": {"title": "x"}}, DEFS)))

    def test_tool_call_wrong_type(self):
        errs = validate_tool_call({"name": "app_create_event", "arguments": {"title": "x", "start": "now", "minutes": "60"}}, DEFS)
        self.assertTrue(any("expected integer" in e for e in errs))

    def test_tool_call_invented_argument_rejected(self):
        errs = validate_tool_call({"name": "fs_read_file", "arguments": {"path": "a", "encoding": "utf8"}}, DEFS)
        self.assertTrue(any("unexpected property" in e for e in errs))

    def test_tool_call_enum(self):
        errs = validate_tool_call({"name": "http_request", "arguments": {"method": "FETCH", "url": "https://x"}}, DEFS)
        self.assertTrue(any("not in enum" in e for e in errs))

    def test_arguments_not_object(self):
        self.assertTrue(validate_tool_call({"name": "fs_read_file", "arguments": "a.txt"}, DEFS))


if __name__ == "__main__":
    unittest.main()
