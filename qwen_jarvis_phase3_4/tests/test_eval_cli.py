import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.eval import cli  # noqa: E402


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **k)
    return r, buf.getvalue()


class CliTests(unittest.TestCase):
    def test_stub_run_writes_stamped_reports(self):
        with tempfile.TemporaryDirectory() as d:
            rep, out = quiet(cli.main, ["run", "--stub", "oracle", "--suite", "tool_awareness,safety", "--out", d, "--seed", "7", "--tag", "unit"])
            data = json.loads((Path(d) / "eval_results.json").read_text(encoding="utf-8"))
            md = (Path(d) / "eval_report.md").read_text(encoding="utf-8")
        self.assertIn("NOT A MODEL RESULT", out)
        self.assertEqual(data["report"]["disclaimer"], "STUB — NOT A MODEL RESULT")
        self.assertEqual(data["report"]["meta"]["seed"], 7)
        self.assertEqual(data["report"]["meta"]["suites_run"], ["tool_awareness", "safety"])
        self.assertEqual(data["report"]["metrics"]["overall"]["rate"], 1.0)
        self.assertTrue(md.startswith("# STUB"))
        for key in ("dataset_version", "decoding", "libraries", "tool_schemas_sha256", "suite_versions"):
            self.assertIn(key, data["report"]["meta"])
        self.assertFalse(data["report"]["meta"]["decoding"].get("do_sample", True))

    def test_max_items_subsample(self):
        with tempfile.TemporaryDirectory() as d:
            quiet(cli.main, ["run", "--stub", "empty", "--suite", "general", "--out", d, "--max-items", "5"])
            data = json.loads((Path(d) / "eval_results.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["items"]), 5)

    def test_loading_a_model_requires_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit) as cm:
                quiet(cli.main, ["run", "--model", "Qwen/Qwen3-8B", "--out", d])
            self.assertIn("--allow-model-load", str(cm.exception))
            with self.assertRaises(SystemExit):
                quiet(cli.main, ["loss", "--model", "Qwen/Qwen3-8B", "--split", "x.jsonl", "--out", d])

    def test_unknown_suite_and_stub_are_clear_errors(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit) as cm:
                quiet(cli.main, ["run", "--stub", "oracle", "--suite", "nope", "--out", d])
            self.assertIn("unknown suite", str(cm.exception))
            with self.assertRaises(SystemExit) as cm:
                quiet(cli.main, ["run", "--stub", "nope", "--suite", "general", "--out", d])
            self.assertIn("unknown stub", str(cm.exception))
            with self.assertRaises(SystemExit):
                quiet(cli.main, ["run", "--out", d])                        # neither --model nor --stub

    def test_compare_two_runs(self):
        with tempfile.TemporaryDirectory() as d:
            a, b, o = Path(d) / "a", Path(d) / "b", Path(d) / "cmp"
            quiet(cli.main, ["run", "--stub", "oracle", "--suite", "general", "--out", str(a)])
            quiet(cli.main, ["run", "--stub", "refuse_all", "--suite", "general", "--out", str(b)])
            cmp, _ = quiet(cli.main, ["compare", "--a", str(a / "eval_results.json"), "--b", str(b / "eval_results.json"), "--out", str(o)])
            md = (o / "comparison.md").read_text(encoding="utf-8")
        self.assertTrue(cmp["stub"])
        self.assertLess(cmp["rows"]["overall"]["delta"], -0.5)
        self.assertIn("NOT A MODEL RESULT", md)

    def test_selftest_shape(self):
        rows, out = quiet(cli.main, ["selftest"])
        names = {r[0]: r[1] for r in rows}
        self.assertEqual(names["oracle"], 1.0)
        for n, r in names.items():
            if n != "oracle":
                self.assertLess(r, 0.06, n)

    def test_importing_the_package_does_not_import_torch_or_transformers(self):
        import subprocess
        code = ("import sys; sys.path.insert(0, r'%s'); import pipeline.eval.cli, pipeline.eval.generators, pipeline.eval.report, pipeline.eval.runner; "
                "print('torch' in sys.modules, 'transformers' in sys.modules)" % str(Path(__file__).resolve().parents[1]))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "False False", r.stderr[-300:])


if __name__ == "__main__":
    unittest.main()
