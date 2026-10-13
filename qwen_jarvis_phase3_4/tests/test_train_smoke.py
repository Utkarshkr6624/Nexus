"""PIPELINE SMOKE TEST - NOT QWEN PRODUCTION TRAINING.
Tiny random Qwen3-architecture model + real Qwen3 tokenizer on CPU: forward, loss, backward, optimizer, eval, checkpoint, resume."""
import math

import pytest
import torch

from test_train_common import C, T, adapter_tensors, make_trainer, smoke_cfg, tokenizer_and_data

K = T.K


@pytest.fixture(scope="module")
def cfg():
    return smoke_cfg(**{"data.max_train_examples": 40, "data.max_eval_examples": 10})


def test_lora_attaches_to_expected_modules(cfg):
    model = T.attach_lora(T.build_tiny_model(cfg), cfg)
    names = [n for n, _ in model.named_modules() if n.endswith("lora_A.default")]
    layers = cfg.model.tiny.num_hidden_layers
    assert len(names) == 7 * layers
    for target in cfg.lora.target_modules:
        assert sum(1 for n in names if f".{target}." in n) == layers
    trainable = [n for n, p in model.named_parameters() if p.requires_grad]
    assert trainable and all("lora_" in n for n in trainable)               # only adapters train; base weights frozen


def test_tiny_model_is_qwen3_architecture_and_random(cfg):
    m = T.build_tiny_model(cfg)
    assert m.config.model_type == "qwen3" and m.config.num_hidden_layers == 2 and m.config.vocab_size == 151936
    assert type(m).__name__ == "Qwen3ForCausalLM"


def test_loss_matches_manual_token_weighted_computation(cfg, tmp_path):
    tr = make_trainer(cfg, tmp_path / "t_loss")
    idxs = [0, 1, 2]
    items = T.n_items([tr.tr[i] for i in idxs])
    tr.model.eval()
    with torch.no_grad():
        loss = float(tr._forward_loss(tr.tr, idxs, items))
        batch = tr._batch(tr.tr, idxs)
        logits = tr.model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]).logits.float()
    lab = batch["labels"][:, 1:]
    manual = torch.nn.functional.cross_entropy(logits[:, :-1].reshape(-1, logits.size(-1)), lab.reshape(-1), ignore_index=-100, reduction="sum") / items
    assert loss == pytest.approx(float(manual), rel=1e-4)


def test_only_assistant_tokens_are_supervised(cfg):
    tok, tr, va, info = tokenizer_and_data(cfg)
    s = tr[0]
    sup = [t for t, l in zip(s["input_ids"], s["labels"]) if l != -100]
    assert 0 < len(sup) < len(s["input_ids"])
    assert sup == [l for l in s["labels"] if l != -100]
    text = tok.decode(sup)
    assert "<|im_start|>" not in text and "<|im_end|>" in text           # answer ends with the end-of-turn token, no role headers


def test_end_to_end_train_checkpoint_resume(cfg, tmp_path):
    cfg = smoke_cfg(**{"data.max_train_examples": 40, "data.max_eval_examples": 10, "train.max_steps": 6, "train.save_every": 2, "train.eval_every": 3})
    res = T.run_smoke(cfg, run_dir=tmp_path / "smoke", echo=False)
    assert res["ok"] and res["resumed_from_step"] == res["stopped_at"] and res["final_step"] == 6
    assert math.isfinite(res["final_val_loss"])
    run = tmp_path / "smoke"
    assert (run / "latest.json").exists() and (run / "best.json").exists() and (run / "metrics.jsonl").exists()


def test_training_reduces_loss_on_a_tiny_overfit_set():
    cfg = smoke_cfg(**{"data.max_train_examples": 8, "data.max_eval_examples": 8, "train.max_steps": 30, "train.epochs": 30, "train.eval_every": 30,
                       "train.save_every": 30, "train.learning_rate": 0.02, "train.per_device_batch": 4, "train.grad_accum": 1})
    import tempfile

    tr = make_trainer(cfg, tempfile.mkdtemp())
    before = tr.evaluate()["val_loss"]
    tr.fit()
    after = tr.evaluate()["val_loss"]
    assert after < before - 0.05, (before, after)                         # optimiser + backward really update the adapter


def test_resume_is_bit_identical_to_uninterrupted_run(tmp_path):
    over = {"data.max_train_examples": 40, "data.max_eval_examples": 10, "train.max_steps": 6, "train.save_every": 2, "train.eval_every": 2}
    cfg = smoke_cfg(**over)
    ref = make_trainer(cfg, tmp_path / "ref")
    ref.fit()
    w_ref = adapter_tensors(ref)

    a = make_trainer(cfg, tmp_path / "int")
    a.fit(stop_at_step=4)                                                  # simulated interruption after step 4
    b = make_trainer(cfg, tmp_path / "int")
    meta = b.resume(K.resolve_resume(tmp_path / "int", "latest"))
    assert meta["step"] == 4 and b.step == 4
    b.fit()
    assert b.step == 6
    w_b = adapter_tensors(b)
    assert set(w_ref) == set(w_b)
    for n in w_ref:
        assert torch.equal(w_ref[n], w_b[n]), f"{n} differs after resume"
    assert ref.evaluate()["val_loss"] == pytest.approx(b.evaluate()["val_loss"], abs=0)


def test_resume_restores_optimizer_and_scheduler_state(cfg, tmp_path):
    over = smoke_cfg(**{"data.max_train_examples": 40, "data.max_eval_examples": 10, "train.max_steps": 6, "train.save_every": 2, "train.eval_every": 2})
    a = make_trainer(over, tmp_path / "r")
    a.fit(stop_at_step=4)
    b = make_trainer(over, tmp_path / "r")
    assert not b.opt.state_dict()["state"]                                  # fresh optimizer is empty
    b.resume(K.resolve_resume(tmp_path / "r", "latest"))
    sa, sb = a.opt.state_dict()["state"], b.opt.state_dict()["state"]
    assert sa.keys() == sb.keys() and sa
    for k in sa:
        assert torch.equal(sa[k]["exp_avg"], sb[k]["exp_avg"]) and torch.equal(sa[k]["exp_avg_sq"], sb[k]["exp_avg_sq"])
    assert a.sched.get_last_lr() == b.sched.get_last_lr()


def test_deterministic_batch_plan():
    lengths = [5, 90, 12, 40, 7, 33, 61, 8, 19, 25] * 13
    p1 = T.plan_epoch(lengths, 4, True, 42, 0)
    assert p1 == T.plan_epoch(lengths, 4, True, 42, 0)
    assert p1 != T.plan_epoch(lengths, 4, True, 42, 1)
    assert sorted(i for b in p1 for i in b) == list(range(len(lengths)))


def test_smoke_cli_end_to_end():
    import subprocess
    import sys

    r = subprocess.run([sys.executable, str(T.WORKSPACE / "pipeline" / "train" / "train.py"), "--smoke-test",
                        "--set", "data.max_train_examples=24", "--set", "data.max_eval_examples=8", "--set", "output.run_name=smoke_cli_test"],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-800:]
    assert "SMOKE TEST PASSED" in r.stdout and "NOT QWEN PRODUCTION TRAINING" in r.stdout


def test_label_only_loss_equals_hf_full_logits_loss(cfg, tmp_path):
    """The OOM-safe loss (lm_head only at supervised positions, chunked + recomputed) must equal HF's own shifted CE."""
    tr = make_trainer(cfg, tmp_path / "t_hf")
    idxs = [0, 1, 2, 3]
    items = T.n_items([tr.tr[i] for i in idxs])
    batch = tr._batch(tr.tr, idxs)
    tr.model.eval()
    with torch.no_grad():
        mine = float(T.masked_lm_loss(tr.model, batch, items))
        hf = float(tr.model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"], labels=batch["labels"],
                            num_items_in_batch=torch.tensor(items)).loss)
    assert mine == pytest.approx(hf, rel=1e-5)


def test_chunked_loss_and_gradients_independent_of_chunk_size(cfg, tmp_path, monkeypatch):
    tr = make_trainer(cfg, tmp_path / "t_chunk")
    idxs = [0, 1, 2, 3]
    items = T.n_items([tr.tr[i] for i in idxs])
    batch = tr._batch(tr.tr, idxs)
    tr.model.train()

    def run(chunk):
        monkeypatch.setattr(T, "LOSS_CHUNK", chunk)
        tr.model.zero_grad(set_to_none=True)
        loss = T.masked_lm_loss(tr.model, batch, items)
        loss.backward()
        g = torch.cat([p.grad.flatten() for p in tr.trainable if p.grad is not None])
        return float(loss), g

    l1, g1 = run(100000)
    l2, g2 = run(7)                                                        # many tiny chunks, each recomputed in backward
    assert l1 == pytest.approx(l2, rel=1e-5)
    assert torch.allclose(g1, g2, rtol=1e-3, atol=1e-6) and g1.abs().sum() > 0


def test_loss_with_no_supervised_tokens_is_zero(cfg, tmp_path):
    tr = make_trainer(cfg, tmp_path / "t_zero")
    batch = tr._batch(tr.tr, [0])
    batch["labels"][:] = -100
    assert float(T.masked_lm_loss(tr.model, batch, 1)) == 0.0


def test_frozen_manifest_is_enforced(cfg, tmp_path):
    import shutil
    import json

    src = cfg.data_path("train").parent
    dst = tmp_path / "ds"
    dst.mkdir()
    for n in ("train.jsonl", "validation.jsonl", "test.jsonl", "MANIFEST.json"):
        shutil.copy(src / n, dst / n)
    ok = smoke_cfg(**{"data.train_path": str(dst / "train.jsonl"), "data.validation_path": str(dst / "validation.jsonl"),
                      "data.max_train_examples": 8, "data.max_eval_examples": 4})
    tok = T.load_tokenizer(ok)
    _, _, info = T.load_data(ok, tok)
    assert info["manifest"]["version"] == "qwen_jarvis_dataset_v2_final" and len(info["manifest_sha256"]) == 64
    with open(dst / "train.jsonl", "a", encoding="utf-8") as f:                 # tamper with the frozen file
        f.write(chr(10))
    with pytest.raises(ValueError, match="does not match"):
        T.load_data(ok, tok)
    bad = smoke_cfg(**{"data.train_path": str(dst / "train.jsonl"), "data.validation_path": str(dst / "validation.jsonl"), "data.dataset_version": "something_else",
                       "data.max_train_examples": 8, "data.max_eval_examples": 4})
    with pytest.raises(ValueError, match="version mismatch"):
        T.load_data(bad, tok)


def test_smoke_data_includes_tool_protocol_samples(cfg):
    tok, tr, va, info = tokenizer_and_data(smoke_cfg())
    text = " ".join(tok.decode(s["input_ids"]) for s in tr)
    assert "<|im_start|>" in text
