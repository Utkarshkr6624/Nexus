"""C8 tool-protocol tests. Run: python test_protocol.py   (or pytest).  Uses stdlib unittest; the render tests need the venv (transformers).
Covers: normal answer -> no tool call; tool-required example -> structured request; tool result representation; final response uses the result;
failed tool -> honest failure handling; determinism of the converter; and negative tests proving validate_protocol rejects broken records."""
import copy
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import convert  # noqa: E402
import validate_protocol as vp  # noqa: E402

CAND = HERE.parent / "dataset" / "v2_final_candidate"
EX = [json.loads(l) for l in open(HERE / "protocol_examples.jsonl", encoding="utf-8")]


def load_all(d):
    out = []
    for s in ("train", "validation", "test"):
        p = d / f"{s}.jsonl"
        if p.exists():
            out += [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    return out


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Protocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recs = load_all(CAND) + EX

    # --- normal answer
    def test_normal_answer_has_no_tool_call(self):
        normal = [r for r in EX if r["metadata"]["subcategory"] == "protocol_no_tool_needed"]
        self.assertGreaterEqual(len(normal), 2)
        for r in normal:
            self.assertEqual(r["messages"][-1]["role"], "assistant")
            self.assertFalse(any(m.get("tool_calls") for m in r["messages"]))
        # also: no-tools records in the dataset never contain calls or tool messages
        for r in self.recs:
            if not r.get("tools"):
                self.assertFalse(any(m.get("tool_calls") or m["role"] == "tool" for m in r["messages"]), r["id"])

    # --- tool-required example -> structured request
    def test_tool_required_produces_valid_structured_call(self):
        direct = [r for r in self.recs if r.get("tools") and any(m.get("tool_calls") for m in r["messages"])]
        self.assertGreater(len(direct), 200)
        for r in direct:
            self.assertEqual(vp.validate_record(r), [], r["id"])
            for m in r["messages"]:
                for c in m.get("tool_calls") or []:
                    self.assertIsInstance(c["function"]["arguments"], dict)
                    self.assertRegex(c["function"]["name"], r"^[a-z][a-z0-9_]{0,63}$")

    # --- confirmation-first policy for side-effect tools
    def test_side_effect_tools_are_previewed_before_calling(self):
        n = 0
        for r in self.recs:
            for i, m in enumerate(r["messages"]):
                for c in m.get("tool_calls") or []:
                    if vp.BY_NAME[c["function"]["name"]]["requires_confirmation"]:
                        n += 1
                        prior = [x for x in r["messages"][:i] if x["role"] == "assistant"]
                        self.assertTrue(prior and "Proposed call:" in prior[-1]["content"], r["id"])
        self.assertGreater(n, 50)

    # --- tool result representation
    def test_tool_results_are_compact_envelopes(self):
        n = 0
        for r in self.recs:
            for m in r["messages"]:
                if m["role"] == "tool":
                    n += 1
                    self.assertIsNone(vp.check_envelope(m["content"]), r["id"])
                    self.assertEqual(m["content"], json.dumps(json.loads(m["content"]), ensure_ascii=False, separators=(",", ":")))
        self.assertGreater(n, 120)

    # --- final response uses the result
    def test_final_answer_is_grounded_in_the_result(self):
        STOP = set("the a an and or of to in on is are was were it its this that for with as at by from be has have had not no your you i we they there then than so if".split())
        checked = bad = 0
        for r in self.recs:
            ms = r["messages"]
            for i, m in enumerate(ms):
                if m["role"] == "tool" and i + 1 < len(ms) and ms[i + 1]["role"] == "assistant":
                    env = json.loads(m["content"])
                    text = env["data"] if env["ok"] else env["error"]["message"]
                    text = text if isinstance(text, str) else json.dumps(text)
                    low = re.sub(r"(?<=\d),(?=\d)", "", text.lower())          # 1,284 == 1284
                    toks = {t for t in re.findall(r"[a-z0-9]{3,}", low) if t not in STOP}
                    ans = set(re.findall(r"[a-z0-9]{3,}", re.sub(r"(?<=\d),(?=\d)", "", ms[i + 1]["content"].lower())))
                    if low.strip(" .") == "no results":                        # empty result: any honest 'nothing found' phrasing is grounded
                        toks = set()
                        self.assertRegex(ms[i + 1]["content"], r"(?i)(nothing|no results|empty|no information|came back empty|did not find|didn't find|haven't found)")
                    if toks:
                        checked += 1
                        if not (toks & ans):
                            bad += 1
        self.assertGreater(checked, 120)
        self.assertLessEqual(bad / checked, 0.03, f"{bad}/{checked} answers share no content word with their tool result")

    # --- failed tool -> honest failure handling
    def test_failed_tool_leads_to_honest_report(self):
        NEG = re.compile(r"(n't|\bnot\b|\bno\b|\bfailed\b|\berror\b|\brefus|\bdenied|\btimed out|couldn|\brejected|\bunable|\bwithout\b|\bnothing\b|\bnever\b|conflict|unavailable|\bempty\b)", re.I)
        n = 0
        for r in self.recs:
            ms = r["messages"]
            for i, m in enumerate(ms):
                if m["role"] == "tool" and json.loads(m["content"])["ok"] is False and i + 1 < len(ms) and ms[i + 1]["role"] == "assistant":
                    n += 1
                    self.assertRegex(ms[i + 1]["content"], NEG, r["id"])
                    self.assertNotRegex(ms[i + 1]["content"], r"\b(successfully|has been (?:created|saved|sent|deleted)|I(?:'ve| have) (?:created|saved|sent|deleted))\b", r["id"])
        self.assertGreater(n, 60)

    # --- determinism
    def test_converter_is_deterministic(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            for out in (a, b):
                subprocess.run([sys.executable, str(HERE / "convert.py"), "--out", out, "--strict"], check=True, capture_output=True, cwd=HERE)
            for s in ("train", "validation", "test"):
                self.assertEqual(sha(Path(a) / f"{s}.jsonl"), sha(Path(b) / f"{s}.jsonl"))
            self.assertEqual(sha(Path(a) / "train.jsonl"), sha(CAND / "train.jsonl"))

    # --- negative tests: the validator must reject broken records
    def _sample(self):
        return copy.deepcopy(next(r for r in EX if r["metadata"]["subcategory"] == "protocol_read_success"))

    def test_validator_rejects_unknown_tool(self):
        r = self._sample()
        r["messages"][2]["tool_calls"][0]["function"]["name"] = "launch_missiles"
        self.assertTrue(any("R2" in e for e in vp.validate_record(r)))

    def test_validator_rejects_bad_arguments(self):
        r = self._sample()
        r["messages"][2]["tool_calls"][0]["function"]["arguments"] = {"query": 5, "extra": 1}
        errs = vp.validate_record(r)
        self.assertTrue(any("wrong type" in e for e in errs) and any("unknown argument" in e for e in errs))

    def test_validator_rejects_orphan_tool_message(self):
        r = self._sample()
        r["messages"].insert(1, {"role": "tool", "name": "web_search", "content": '{"ok":true,"data":"x"}'})
        self.assertTrue(any("R3" in e for e in vp.validate_record(r)))

    def test_validator_rejects_bad_envelope(self):
        r = self._sample()
        r["messages"][3]["content"] = "No results."
        self.assertTrue(any("R4" in e for e in vp.validate_record(r)))

    def test_validator_rejects_unconfirmed_write(self):
        r = copy.deepcopy(next(x for x in EX if x["metadata"]["subcategory"] == "protocol_write_confirm_success"))
        del r["messages"][2:4]          # drop the preview and the approval
        self.assertTrue(any("R5" in e for e in vp.validate_record(r)))

    def test_validator_rejects_calls_without_tools(self):
        r = self._sample()
        r["tools"] = []
        self.assertTrue(any("R6" in e or "R2" in e for e in vp.validate_record(r)))

    # --- audit finding 9: stronger rules
    def _write_example(self):
        return copy.deepcopy(next(x for x in EX if x["metadata"]["subcategory"] == "protocol_write_confirm_success"))

    def test_validator_rejects_call_after_a_refusal(self):
        r = self._write_example()
        r["messages"][3]["content"] = "No, do not do that!"
        self.assertTrue(any("not an approval" in e for e in vp.validate_record(r)))

    def test_validator_rejects_call_without_affirmation(self):
        r = self._write_example()
        r["messages"][3]["content"] = "Hmm, what does that mean?"
        self.assertTrue(any("not an approval" in e for e in vp.validate_record(r)))

    def test_validator_rejects_preview_for_a_different_tool(self):
        r = self._write_example()
        c = r["messages"][4]["tool_calls"][0]["function"]
        r["tools"].append(convert.tool_def("app.update_task"))      # attached, so only the preview mismatch can be the problem
        c["name"] = "app_update_task"            # a different side-effect tool than the one previewed
        c["arguments"] = {"task": "Renew passport"}
        self.assertTrue(any("preview names" in e for e in vp.validate_record(r)))

    def test_validator_rejects_preview_with_different_arguments(self):
        r = self._write_example()
        r["messages"][4]["tool_calls"][0]["function"]["arguments"]["title"] = "Something else entirely"
        self.assertTrue(any("preview argument title differs" in e for e in vp.validate_record(r)))

    def test_validator_rejects_action_claim_without_tool_message(self):
        r = copy.deepcopy(next(x for x in EX if x["metadata"]["subcategory"] == "protocol_no_tool_needed"))
        r["messages"][-1]["content"] = "I ran git log and the output was three commits."
        self.assertTrue(any("R9" in e for e in vp.validate_record(r)))

    def test_validator_rejects_success_claim_after_error_result(self):
        r = copy.deepcopy(next(x for x in EX if x["metadata"]["subcategory"] == "protocol_read_error"))
        r["messages"][-1]["content"] = "Done: the file was read successfully."
        self.assertTrue(any("R10" in e for e in vp.validate_record(r)))

    def test_array_item_types_are_checked(self):
        spec = {"type": "array", "items": {"type": "string"}}
        self.assertTrue(vp.check_type(["a", "b"], spec))
        self.assertFalse(vp.check_type(["a", 3], spec))

    # --- rendering through the real chat template (skipped if transformers is not importable)
    def test_chat_template_rendering(self):
        try:
            from transformers import AutoTokenizer
        except Exception:
            self.skipTest("transformers not available")
        tok = AutoTokenizer.from_pretrained(str(HERE.parent / "_hf_meta"))
        for r in EX:
            self.assertEqual(vp.render_check(tok, r), [], r["id"])
        txt = tok.apply_chat_template(EX[0]["messages"], tools=EX[0]["tools"], tokenize=False, enable_thinking=False)
        self.assertIn("<tool_call>", txt)
        self.assertIn("<tool_response>", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
