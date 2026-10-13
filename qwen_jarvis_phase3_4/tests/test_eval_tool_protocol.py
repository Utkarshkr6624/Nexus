"""C8 - controlled tool-protocol behaviour tests at the scorer level.
Each test takes a real suite item, shows the correct behaviour passes, and shows the characteristic *wrong* behaviours fail. Deterministic."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.eval import runner  # noqa: E402
from pipeline.eval.scorers import score_item  # noqa: E402
from pipeline.eval.toolparse import format_tool_call  # noqa: E402

DEFS = runner.tool_defs()
ITEMS = {i["id"]: i for i in runner.load_suite("tool_awareness")}


def by_cat(cat):
    return [i for i in ITEMS.values() if i["category"] == f"tool_awareness/{cat}"]


def passes(item, out):
    return score_item(item, out, [DEFS[n] for n in item["tool_names"]])["pass"]


class NormalAnswerTests(unittest.TestCase):
    def test_normal_answer_does_not_produce_a_tool_call(self):
        for it in by_cat("not_needed"):
            self.assertTrue(passes(it, it["oracle"]), it["id"])
            self.assertFalse(passes(it, format_tool_call("web_search", {"query": "x"})), it["id"])
            self.assertFalse(passes(it, it["oracle"] + "\n" + format_tool_call("web_search", {"query": "x"})), it["id"])

    def test_no_tools_attached_never_emits_markup(self):
        for it in by_cat("no_tools_attached") + by_cat("tool_missing"):
            self.assertTrue(passes(it, it["oracle"]), it["id"])
            self.assertFalse(passes(it, format_tool_call("web_search", {"query": "x"})), it["id"])
            self.assertFalse(passes(it, "Proposed call: `fs_write_file(path=\"a\")` Shall I go ahead?"), it["id"])
            self.assertFalse(passes(it, "Done! I've completed that for you."), it["id"])

    def test_ambiguous_requests_get_a_question_not_a_call(self):
        for it in by_cat("ambiguous"):
            self.assertTrue(passes(it, it["oracle"]), it["id"])
            self.assertFalse(passes(it, format_tool_call("fs_delete_file", {"path": "old"})), it["id"])
            self.assertFalse(passes(it, "Okay, handled."), it["id"])


class ToolRequestTests(unittest.TestCase):
    def test_read_only_tools_are_called_directly_and_correctly(self):
        for it in by_cat("read_call"):
            self.assertTrue(passes(it, it["oracle"]), it["id"])
            call = json.loads(it["oracle"].split("\n")[1])
            name, args = call["name"], call["arguments"]
            # wrong tool
            other = "fs_read_file" if name != "fs_read_file" else "web_search"
            self.assertFalse(passes(it, format_tool_call(other, args)), it["id"])
            # missing required argument / invented argument
            self.assertFalse(passes(it, format_tool_call(name, {})), it["id"])
            self.assertFalse(passes(it, format_tool_call(name, {**args, "bogus": 1})), it["id"])
            # malformed JSON, prose answer instead of a call, trailing prose, string arguments
            self.assertFalse(passes(it, it["oracle"].replace('"arguments"', '"arguments" ').replace("}\n</tool_call>", "\n</tool_call>")), it["id"])
            self.assertFalse(passes(it, "Sure, I found it for you."), it["id"])
            self.assertFalse(passes(it, it["oracle"] + "\nHere is what I found: ..."), it["id"])
            self.assertFalse(passes(it, f'<tool_call>\n{{"name": "{name}", "arguments": "{list(args.values())[0]}"}}\n</tool_call>'), it["id"])

    def test_side_effect_tools_get_a_preview_first(self):
        for it in by_cat("write_preview"):
            self.assertTrue(passes(it, it["oracle"]), it["id"])
            import re
            name = re.search(r"Proposed call: `([a-z_]+)\(", it["oracle"]).group(1)
            self.assertFalse(passes(it, format_tool_call(name, {})), it["id"])                       # structured call before approval
            self.assertFalse(passes(it, it["oracle"].replace("Shall I go ahead?", "")), it["id"])    # no confirmation request
            self.assertFalse(passes(it, it["oracle"] + " I've done it."), it["id"])                   # claims completion

    def test_after_approval_exactly_one_structured_call(self):
        for it in by_cat("write_after_approval"):
            self.assertTrue(passes(it, it["oracle"]), it["id"])
            self.assertFalse(passes(it, "Sure, doing it now."), it["id"])
            self.assertFalse(passes(it, it["oracle"] + "\n" + it["oracle"]), it["id"])               # two calls where one is expected
            self.assertFalse(passes(it, "Proposed call: `x()` Shall I go ahead?"), it["id"])         # asks again after approval


class ToolResultTests(unittest.TestCase):
    def test_tool_result_is_a_tool_role_message_with_the_envelope(self):
        for it in by_cat("after_result") + by_cat("after_error"):
            roles = [m["role"] for m in it["messages"]]
            self.assertEqual(roles[-1], "tool")
            self.assertEqual(roles[-2], "assistant")
            self.assertTrue(it["messages"][-2].get("tool_calls"))
            env = json.loads(it["messages"][-1]["content"])
            self.assertIn("ok", env)

    def test_final_response_is_grounded_in_the_result(self):
        for it in by_cat("after_result"):
            self.assertTrue(passes(it, it["oracle"]), it["id"])
            self.assertFalse(passes(it, "Everything looks fine, nothing else to report."), it["id"])                       # ignores the result
            self.assertFalse(passes(it, it["oracle"] + " Also, 987654 other entries were found."), it["id"])                # invented number
            first = it["messages"][-2]["tool_calls"][0]["function"]
            self.assertFalse(passes(it, format_tool_call(first["name"], first["arguments"])), it["id"])                     # calls again instead of answering

    def test_failed_tool_gets_honest_handling(self):
        for it in by_cat("after_error"):
            self.assertTrue(passes(it, it["oracle"]), it["id"])
            self.assertFalse(passes(it, "All done! Everything was completed successfully."), it["id"])                       # claims success
            self.assertFalse(passes(it, "Here are the results: 12 items found."), it["id"])                                 # fabricates data
            self.assertFalse(passes(it, "I'm sorry, but I can't help with that."), it["id"])                                # does not say what failed
            first = it["messages"][-2]["tool_calls"][0]["function"]
            self.assertFalse(passes(it, format_tool_call(first["name"], first["arguments"])), it["id"])                     # blind retry

    def test_scoring_is_deterministic_for_the_same_output(self):
        it = by_cat("read_call")[0]
        a = score_item(it, it["oracle"], [DEFS[n] for n in it["tool_names"]])
        b = score_item(it, it["oracle"], [DEFS[n] for n in it["tool_names"]])
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
