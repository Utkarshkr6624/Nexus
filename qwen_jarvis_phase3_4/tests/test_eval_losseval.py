"""Loss / perplexity evaluator tests. The torch tests use a tiny RANDOM-WEIGHT model purely to exercise the code path:
PIPELINE SMOKE TEST - NOT QWEN, and the numbers mean nothing about any trained model."""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np  # noqa: E402

from pipeline.eval import losseval  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
try:
    import torch  # noqa: E402
    HAVE_TORCH = True
except Exception:  # noqa: BLE001
    HAVE_TORCH = False


class NumpyLossTests(unittest.TestCase):
    def test_uniform_logits_give_ln_vocab(self):
        logits = np.zeros((5, 4))
        labels = [-100, 1, 2, -100, 3]
        s, n = losseval.token_nll(logits, labels)
        self.assertEqual(n, 3)
        self.assertAlmostEqual(s / n, math.log(4), places=9)

    def test_confident_correct_prediction_is_near_zero(self):
        logits = np.full((3, 5), -20.0)
        logits[0, 2] = 20.0          # position 0 predicts label[1] = 2
        logits[1, 4] = 20.0          # position 1 predicts label[2] = 4
        s, n = losseval.token_nll(logits, [-100, 2, 4])
        self.assertEqual(n, 2)
        self.assertLess(s / n, 1e-6)

    def test_confident_wrong_prediction_is_large(self):
        logits = np.full((2, 5), -20.0)
        logits[0, 1] = 20.0
        s, n = losseval.token_nll(logits, [-100, 3])
        self.assertGreater(s / n, 30)

    def test_shift_semantics_last_position_unused(self):
        logits = np.zeros((3, 4))
        logits[2, 0] = 100.0          # last position's logits must not matter
        s1, _ = losseval.token_nll(logits, [-100, 1, 1])
        s2, _ = losseval.token_nll(np.zeros((3, 4)), [-100, 1, 1])
        self.assertAlmostEqual(s1, s2)

    def test_all_masked(self):
        self.assertEqual(losseval.token_nll(np.zeros((3, 4)), [-100, -100, -100]), (0.0, 0))

    def test_accumulator_categories_and_perplexity(self):
        acc = losseval.LossAccumulator()
        acc.add("a", math.log(2) * 10, 10)
        acc.add("a", math.log(2) * 10, 10)
        acc.add("b", math.log(8) * 5, 5)
        s = acc.summary()
        self.assertAlmostEqual(s["by_category"]["a"]["perplexity"], 2.0)
        self.assertAlmostEqual(s["by_category"]["b"]["perplexity"], 8.0)
        self.assertEqual(s["overall"]["tokens"], 25)
        self.assertAlmostEqual(s["overall"]["mean_loss"], (math.log(2) * 20 + math.log(8) * 5) / 25)
        self.assertIn("in-distribution", s["note"])

    def test_bootstrap_interval_is_deterministic_and_brackets_mean(self):
        acc = losseval.LossAccumulator()
        for i in range(40):
            acc.add("a", (1.0 + (i % 5) * 0.1) * 10, 10)
        a = acc.summary(bootstrap=200, seed=1)["overall"]
        b = acc.summary(bootstrap=200, seed=1)["overall"]
        self.assertEqual(a["mean_loss_ci95"], b["mean_loss_ci95"])
        lo, hi = a["mean_loss_ci95"]
        self.assertLessEqual(lo, a["mean_loss"])
        self.assertGreaterEqual(hi, a["mean_loss"])


@unittest.skipUnless(HAVE_TORCH, "torch not installed")
class TorchLossTests(unittest.TestCase):
    class TinyLM(torch.nn.Module if HAVE_TORCH else object):
        """Random-weight bigram-ish LM. PIPELINE SMOKE ONLY."""

        def __init__(self, vocab=151936, dim=8):
            super().__init__()
            torch.manual_seed(0)
            self.emb = torch.nn.Embedding(vocab, dim)
            self.out = torch.nn.Linear(dim, vocab, bias=False)

        def forward(self, input_ids, attention_mask=None):
            class O:
                pass
            o = O()
            o.logits = self.out(self.emb(input_ids))
            return o

    def test_torch_matches_numpy_reference(self):
        model = self.TinyLM(vocab=50, dim=6)
        samples = [{"input_ids": [3, 4, 5, 6], "attention_mask": [1] * 4, "labels": [-100, -100, 5, 6], "category": "x"},
                   {"input_ids": [7, 8, 9], "attention_mask": [1] * 3, "labels": [-100, 8, 9], "category": "y"}]
        acc = losseval.evaluate_samples(model, samples, pad_token_id=0, batch_size=2)
        s = acc.summary()
        for i, (cat, smp) in enumerate(zip(("x", "y"), samples)):
            with torch.no_grad():
                lg = model(torch.tensor([smp["input_ids"]])).logits[0].numpy()
            ref_s, ref_n = losseval.token_nll(lg, smp["labels"])
            self.assertEqual(s["by_category"][cat]["tokens"], ref_n)
            self.assertAlmostEqual(s["by_category"][cat]["mean_loss"], ref_s / ref_n, places=4)

    def test_batch_size_does_not_change_results(self):
        model = self.TinyLM(vocab=50, dim=6)
        samples = [{"input_ids": list(range(2, 2 + n)), "attention_mask": [1] * n, "labels": [-100] + list(range(3, 2 + n)), "category": "c"} for n in (3, 5, 4, 6)]
        a = losseval.evaluate_samples(model, samples, pad_token_id=0, batch_size=1).summary()["overall"]["mean_loss"]
        b = losseval.evaluate_samples(model, samples, pad_token_id=0, batch_size=4).summary()["overall"]["mean_loss"]
        self.assertAlmostEqual(a, b, places=5)

    def test_evaluate_split_with_pipeline_data_and_real_tokenizer(self):
        try:
            from pipeline.data.render import load_tokenizer
            tok = load_tokenizer()
        except Exception as e:  # noqa: BLE001
            self.skipTest(f"tokenizer/pipeline.data unavailable: {e}")
        src = ROOT / "dataset" / "v2_final" / "validation.jsonl"
        if not src.exists():
            self.skipTest("dataset/v2_final/validation.jsonl missing")
        with open(src, encoding="utf-8") as fh:
            lines = [next(fh) for _ in range(12)]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "mini.jsonl"
            p.write_text("".join(lines), encoding="utf-8")
            model = self.TinyLM()            # random weights over the real Qwen3 vocabulary: PIPELINE SMOKE TEST - NOT QWEN
            out = losseval.evaluate_split(model, tok, p, max_len=1024, batch_size=4, bootstrap=20)
        self.assertGreater(out["overall"]["tokens"], 50)
        self.assertTrue(math.isfinite(out["overall"]["mean_loss"]))
        # a random model over a 151936-token vocabulary has loss close to ln(V) ~ 11.9; a sanity bound, not a quality claim
        self.assertGreater(out["overall"]["mean_loss"], 8.0)
        self.assertGreaterEqual(len(out["by_category"]), 1)
        self.assertEqual(out["split"], str(p))


if __name__ == "__main__":
    unittest.main()
