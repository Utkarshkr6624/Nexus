"""Uses the REAL Qwen3-8B tokenizer/chat template files in _hf_meta (no weights) to verify that (a) the prompts the HF generator would build are
what Qwen expects, and (b) the tool-call text produced by Qwen's own template is parsed correctly by our strict parser."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.eval import runner  # noqa: E402
from pipeline.eval.build_suites import resolve_tools  # noqa: E402
from pipeline.eval.toolparse import parse_tool_calls  # noqa: E402

try:
    from pipeline.data.render import load_tokenizer
    TOK = load_tokenizer()
except Exception:  # noqa: BLE001
    TOK = None

DEFS = runner.tool_defs()
ITEMS = {i["id"]: i for i in runner.load_suite("tool_awareness")}


def build_prompt(item):
    tools = resolve_tools(item, DEFS) or None
    return TOK.apply_chat_template(item["messages"], tools=tools, add_generation_prompt=True, tokenize=False, enable_thinking=False)


@unittest.skipIf(TOK is None, "Qwen3 tokenizer files / transformers unavailable")
class PromptRenderTests(unittest.TestCase):
    def test_generation_prompt_ends_with_empty_think_block(self):
        for it in list(ITEMS.values())[:12]:
            p = build_prompt(it)
            self.assertTrue(p.endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n"), it["id"])

    def test_tools_are_rendered_in_the_system_block(self):
        it = next(i for i in ITEMS.values() if i["category"] == "tool_awareness/read_call")
        p = build_prompt(it)
        self.assertIn("# Tools", p)
        self.assertIn("<tools>", p)
        for n in it["tool_names"]:
            self.assertIn(f'"name": "{n}"', p)

    def test_no_tools_block_without_tools(self):
        it = next(i for i in ITEMS.values() if i["category"] == "tool_awareness/no_tools_attached")
        self.assertNotIn("<tools>", build_prompt(it))

    def test_tool_result_is_rendered_as_tool_response_in_a_user_turn(self):
        it = next(i for i in ITEMS.values() if i["category"] == "tool_awareness/after_result")
        p = build_prompt(it)
        self.assertRegex(p, r"<\|im_start\|>user\n<tool_response>\n\{\"ok\":true")
        self.assertIn("</tool_response><|im_end|>", p)
        self.assertIn("<tool_call>", p)                           # the earlier assistant call is part of the history

    def test_qwen_template_tool_call_text_round_trips_through_our_parser(self):
        for it in ITEMS.values():
            for m in it["messages"]:
                for tc in m.get("tool_calls") or []:
                    msgs = [{"role": "user", "content": "go"}, {"role": "assistant", "content": "", "tool_calls": [tc]}]
                    text = TOK.apply_chat_template(msgs, tokenize=False)
                    block = re.search(r"<tool_call>.*?</tool_call>", text, re.S).group(0)
                    calls = parse_tool_calls(block)
                    self.assertEqual(calls, [{"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}], it["id"])

    def test_every_suite_prompt_renders_without_error(self):
        for name in runner.SUITES:
            for it in runner.load_suite(name)[:25]:
                tools = resolve_tools(it, DEFS) or None
                p = TOK.apply_chat_template(it["messages"], tools=tools, add_generation_prompt=True, tokenize=False, enable_thinking=False)
                self.assertTrue(p.startswith("<|im_start|>"), it["id"])


if __name__ == "__main__":
    unittest.main()
