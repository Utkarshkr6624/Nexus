"""Suite-level checks: every oracle passes, every adversarial stub scores near zero where it should, the generator never sees answers."""
import json
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.eval import build_suites, generators, report, runner  # noqa: E402

DEFS = runner.tool_defs()
ITEMS = []
for _s in runner.SUITES:
    ITEMS += runner.load_suite(_s)
MANIFEST = json.loads((runner.DATA / "manifest.json").read_text(encoding="utf-8"))


def rate_of(res, prefix=None, suite=None):
    sel = [r for r in res if r["scored"] and (prefix is None or r["category"].startswith(prefix)) and (suite is None or r["suite"] == suite)]
    return (sum(r["pass"] for r in sel) / len(sel)) if sel else None, len(sel)


class SuiteShapeTests(unittest.TestCase):
    def test_sizes(self):
        sz = {k: v["n"] for k, v in MANIFEST["suites"].items()}
        self.assertGreaterEqual(sz["nexus_facts"], 80)
        for name in runner.SUITES:
            self.assertGreater(sz[name], 20, name)

    def test_unique_ids(self):
        ids = [i["id"] for i in ITEMS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_prompts_end_on_user_or_tool_turn(self):
        for it in ITEMS:
            self.assertIn(it["messages"][-1]["role"], ("user", "tool"), it["id"])

    def test_tools_referenced_exist(self):
        for it in ITEMS:
            for n in it.get("tool_names", []):
                self.assertIn(n, DEFS, it["id"])

    def test_tool_messages_use_the_envelope(self):
        n = 0
        for it in ITEMS:
            for m in it["messages"]:
                if m["role"] == "tool":
                    n += 1
                    env = json.loads(m["content"])
                    self.assertEqual(m["content"], json.dumps(env, separators=(",", ":"), ensure_ascii=False))
                    if env["ok"]:
                        self.assertEqual(set(env), {"ok", "data"})
                    else:
                        self.assertEqual(set(env), {"ok", "error"})
                        self.assertEqual(set(env["error"]), {"code", "message"})
                for tc in m.get("tool_calls") or []:
                    self.assertIsInstance(tc["function"]["arguments"], dict)
        self.assertGreater(n, 15)

    def test_nexus_probes_cover_all_three_statuses(self):
        cats = Counter(i["category"] for i in ITEMS if i["suite"] == "nexus_facts")
        for c in ("nexus_facts/current", "nexus_facts/planned", "nexus_facts/absent", "nexus_facts/false_premise"):
            self.assertGreater(cats[c], 5, c)

    def test_every_item_has_a_check_and_oracle(self):
        for it in ITEMS:
            self.assertTrue(it["check"])
            self.assertTrue(it["oracle"].strip())


class OracleAndAdversarialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.oracle = runner.run_items(generators.OracleGenerator(ITEMS), ITEMS, DEFS)
        cls.res = {n: runner.run_items(generators.STUBS[n](), ITEMS, DEFS) for n in generators.STUBS}

    def test_oracle_passes_everything_scored(self):
        bad = [r["id"] for r in self.oracle if r["scored"] and not r["pass"]]
        self.assertEqual(bad, [])

    def test_all_adversarial_stubs_score_low_overall(self):
        for name, res in self.res.items():
            r, n = rate_of(res)
            self.assertLess(r, 0.06, f"{name} passes {r:.3f} of {n}")

    def test_empty_output_scores_zero(self):
        self.assertEqual(rate_of(self.res["empty"])[0], 0.0)

    def test_hallucinating_nexus_fails_every_nexus_fact(self):
        r, n = rate_of(self.res["hallucinate_nexus"], suite="nexus_facts")
        self.assertEqual(r, 0.0)
        self.assertGreater(n, 80)

    def test_refuse_all_is_caught_on_both_sides_of_safety(self):
        harmful, nh = rate_of(self.res["refuse_all"], "safety/harmful")
        benign, nb = rate_of(self.res["refuse_all"], "safety/benign_lookalike")
        self.assertEqual(harmful, 0.0)            # refuses, but never offers an alternative
        self.assertEqual(benign, 0.0)             # over-refusal
        agg = report.aggregate(self.res["refuse_all"])["derived"]
        self.assertEqual(agg["harmful_refusal_rate"]["rate"], 1.0)
        self.assertEqual(agg["over_refusal_rate"]["rate"], 1.0)

    def test_refuse_all_does_not_pass_boundaries_or_failure_reports(self):
        self.assertEqual(rate_of(self.res["refuse_all"], "capability_boundaries")[0], 0.0)
        self.assertEqual(rate_of(self.res["refuse_all"], "tool_awareness/after_error")[0], 0.0)
        self.assertEqual(rate_of(self.res["refuse_all"], "tool_awareness/no_tools_attached")[0], 0.0)

    def test_never_calling_tools_fails_tool_needed_items(self):
        for cat in ("tool_awareness/read_call", "tool_awareness/write_preview", "tool_awareness/write_after_approval", "tool_awareness/after_result", "tool_awareness/after_error"):
            self.assertEqual(rate_of(self.res["never_call_tool"], cat)[0], 0.0, cat)

    def test_always_calling_tools_fails_where_no_call_is_right(self):
        for cat in ("tool_awareness/not_needed", "tool_awareness/no_tools_attached", "tool_awareness/tool_missing", "tool_awareness/ambiguous", "tool_awareness/write_preview",
                    "tool_awareness/after_result", "tool_awareness/after_error", "capability_boundaries/no_access"):
            self.assertEqual(rate_of(self.res["always_call_tool"], cat)[0], 0.0, cat)
        self.assertEqual(rate_of(self.res["always_call_tool"], suite="nexus_facts")[0], 0.0)

    def test_echoing_the_prompt_does_not_satisfy_knowledge_checks(self):
        self.assertEqual(rate_of(self.res["echo_user"], suite="nexus_facts")[0], 0.0)
        self.assertEqual(rate_of(self.res["echo_user"], suite="capability_boundaries")[0], 0.0)

    def test_scoring_is_deterministic(self):
        again = runner.run_items(generators.OracleGenerator(ITEMS), ITEMS, DEFS)
        strip = lambda rs: json.dumps([{k: v for k, v in r.items()} for r in rs], sort_keys=True)  # noqa: E731
        self.assertEqual(strip(self.oracle), strip(again))


class IsolationTests(unittest.TestCase):
    def test_generator_never_sees_oracle_or_checks_and_cannot_mutate_items(self):
        seen = []

        def spy(messages, tools):
            seen.append(json.dumps({"m": messages, "t": [t["function"]["name"] for t in (tools or [])]}, ensure_ascii=False))
            messages.append({"role": "user", "content": "MUTATED"})
            return "x"

        sample = ITEMS[:60]
        before = json.dumps(sample, sort_keys=True)
        runner.run_items(spy, sample, DEFS)
        self.assertEqual(before, json.dumps(sample, sort_keys=True))
        for it, s in zip(sample, seen):
            if len(it["oracle"]) > 40:
                self.assertNotIn(json.dumps(it["oracle"], ensure_ascii=False)[1:-1], s, it["id"])

    def test_generator_exception_is_a_failure_not_a_crash(self):
        def boom(messages, tools):
            raise RuntimeError("cuda out of memory")

        res = runner.run_items(boom, ITEMS[:3], DEFS)
        self.assertTrue(all((not r["pass"]) and r["gen_error"] for r in res))

    def test_non_string_output_is_rejected(self):
        res = runner.run_items(lambda m, t: None, ITEMS[:2], DEFS)
        self.assertTrue(all(not r["pass"] and "TypeError" in r["gen_error"] for r in res))


class BuildTests(unittest.TestCase):
    def test_subsample_is_deterministic_and_seeded(self):
        a = runner.subsample(ITEMS, 25, 0)
        b = runner.subsample(ITEMS, 25, 0)
        c = runner.subsample(ITEMS, 25, 1)
        self.assertEqual([i["id"] for i in a], [i["id"] for i in b])
        self.assertNotEqual([i["id"] for i in a], [i["id"] for i in c])
        self.assertEqual(len(runner.subsample(ITEMS, 10 ** 6, 0)), len(ITEMS))

    def test_rebuild_is_byte_identical(self):
        before = {k: v["sha256"] for k, v in MANIFEST["suites"].items()}
        build_suites.main([])
        after = json.loads((runner.DATA / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(before, {k: v["sha256"] for k, v in after["suites"].items()})


if __name__ == "__main__":
    unittest.main()
