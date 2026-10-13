import json
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.eval import report  # noqa: E402


def mk(i, cat, passed, scored=True, refused=False, markup=False, f1=None):
    info = {"hygiene": {"empty": False, "template_token_leak": False, "think_block": False}}
    if f1 is not None:
        info["lexical_f1"] = f1
    return {"id": f"i{i}", "suite": cat.split("/")[0], "category": cat, "pass": passed, "scored": scored, "checks": [], "info": info,
            "flags": {"refused": refused, "tool_markup": markup}, "gen_error": None, "output": ""}


class AggregateTests(unittest.TestCase):
    def test_counts_rates_and_intervals(self):
        items = [mk(i, "safety/harmful", i < 8, refused=i < 8) for i in range(10)] + [mk(100 + i, "safety/benign_lookalike", i < 3, refused=i >= 3) for i in range(10)]
        a = report.aggregate(items)
        self.assertEqual(a["suites"]["safety"]["n"], 20)
        self.assertEqual(a["categories"]["safety/harmful"]["pass"], 8)
        lo, hi = a["categories"]["safety/harmful"]["ci95"]
        self.assertLess(lo, 0.8)
        self.assertGreater(hi, 0.8)
        self.assertEqual(a["derived"]["harmful_refusal_rate"]["rate"], 0.8)
        self.assertEqual(a["derived"]["over_refusal_rate"]["rate"], 0.7)

    def test_unscored_items_are_excluded_from_pass_rates(self):
        items = [mk(1, "testsplit/x", True), mk(2, "testsplit/x", False), mk(3, "testsplit/info", True, scored=False, f1=0.4)]
        a = report.aggregate(items)
        self.assertEqual(a["suites"]["testsplit"]["n"], 2)
        self.assertEqual(a["n_items_total"], 3)
        self.assertEqual(a["informational"]["mean_lexical_f1"], 0.4)

    def test_tool_derived_rates(self):
        items = [mk(1, "tool_awareness/not_needed", True), mk(2, "tool_awareness/not_needed", False, markup=True),
                 mk(3, "tool_awareness/read_call", True, markup=True), mk(4, "tool_awareness/read_call", False, markup=False)]
        d = report.aggregate(items)["derived"]
        self.assertEqual(d["false_tool_call_rate"]["pass"], 1)
        self.assertEqual(d["valid_tool_call_rate_when_needed"]["rate"], 0.5)

    def test_empty(self):
        a = report.aggregate([])
        self.assertEqual(a["overall"]["n"], 0)
        self.assertIsNone(a["overall"]["rate"])


class StubStampTests(unittest.TestCase):
    def test_stub_reports_are_stamped_in_json_and_markdown(self):
        rep = report.make_report({"model_kind": "stub", "model_id": "stub:oracle"}, [mk(1, "general/qa", True)])
        self.assertEqual(rep["disclaimer"], report.STUB_STAMP)
        self.assertIn("NOT A MODEL RESULT", json.dumps(rep, ensure_ascii=False))
        self.assertTrue(report.to_markdown(rep).startswith("# STUB"))

    def test_real_model_reports_have_no_stamp(self):
        rep = report.make_report({"model_kind": "model", "model_id": "Qwen/Qwen3-8B"}, [mk(1, "general/qa", True)])
        self.assertNotIn("disclaimer", rep)
        self.assertNotIn("STUB", report.to_markdown(rep))

    def test_markdown_lists_n_for_every_row_and_caveats(self):
        rep = report.make_report({"model_kind": "stub", "model_id": "s", "seed": 3, "dataset_version": "v"}, [mk(1, "general/qa", True), mk(2, "general/math", False)])
        md = report.to_markdown(rep)
        self.assertIn("| general/qa |", md)
        self.assertIn("Caveats", md)
        self.assertIn("**seed**", md)


class CompareTests(unittest.TestCase):
    def test_mcnemar_exact(self):
        self.assertAlmostEqual(report._binom_two_sided(0, 5), 0.0625)
        self.assertAlmostEqual(report._binom_two_sided(1, 5), 0.21875)
        self.assertEqual(report._binom_two_sided(0, 0), 1.0)
        self.assertEqual(report._binom_two_sided(3, 3), 1.0)

    def test_paired_comparison(self):
        A = [mk(i, "general/qa", i < 5) for i in range(20)]
        B = [mk(i, "general/qa", i < 15) for i in range(20)]
        rep_a = report.make_report({"model_kind": "model", "model_id": "base"}, A)
        rep_b = report.make_report({"model_kind": "model", "model_id": "ft", "adapter": "ckpt"}, B)
        cmp = report.compare(rep_a, rep_b, A, B)
        row = cmp["rows"]["overall"]
        self.assertEqual(row["n"], 20)
        self.assertEqual((row["a_only_pass"], row["b_only_pass"]), (0, 10))
        self.assertAlmostEqual(row["delta"], 0.5)
        self.assertLess(row["mcnemar_p"], 0.01)
        self.assertFalse(cmp["stub"])
        md = report.compare_markdown(cmp, rep_a, rep_b)
        self.assertIn("McNemar", md)
        self.assertNotIn("NOT A MODEL RESULT", md)

    def test_stub_side_is_flagged(self):
        A = [mk(i, "general/qa", True) for i in range(4)]
        rep_a = report.make_report({"model_kind": "stub", "model_id": "stub:x"}, A)
        rep_b = report.make_report({"model_kind": "model", "model_id": "m"}, A)
        cmp = report.compare(rep_a, rep_b, A, A)
        self.assertTrue(cmp["stub"])
        self.assertIn("NOT A MODEL RESULT", report.compare_markdown(cmp, rep_a, rep_b))

    def test_only_common_items_are_compared_and_mismatch_reported(self):
        A = [mk(i, "general/qa", True) for i in range(6)]
        B = [mk(i, "general/qa", True) for i in range(3, 9)]
        cmp = report.compare(report.make_report({"model_kind": "model"}, A), report.make_report({"model_kind": "model"}, B), A, B)
        self.assertEqual(cmp["n_common"], 3)
        self.assertEqual((cmp["only_in_a"], cmp["only_in_b"]), (3, 3))

    def test_ci_overlap_flag_on_small_n(self):
        A = [mk(i, "general/qa", i < 3) for i in range(5)]
        B = [mk(i, "general/qa", i < 4) for i in range(5)]
        cmp = report.compare(report.make_report({"model_kind": "model"}, A), report.make_report({"model_kind": "model"}, B), A, B)
        self.assertTrue(cmp["rows"]["overall"]["ci_overlap"])


if __name__ == "__main__":
    unittest.main()
