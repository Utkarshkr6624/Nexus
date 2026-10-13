"""Loader / collation / batching / stats / CLI tests, plus safe-failure behaviour (C2, C3, C11)."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_testlib import PHASE2_SPLITS, WS, multi_record, run_module, tokenizer, tool_record  # noqa: E402
from pipeline.data import cli  # noqa: E402
from pipeline.data.dataset import (DataError, TorchDataset, collate, length_grouped_batches, load_split, padding_waste,  # noqa: E402
                                   read_jsonl, token_budget_batches)
from pipeline.data.render import IGNORE, PAD_ID  # noqa: E402
from pipeline.data.stats import compute_stats  # noqa: E402


def _tmp():
    return Path(tempfile.mkdtemp(prefix="p34_data_"))


def _write(path, recs):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")
    return path


def test_load_split_phase2_sample():
    tok = tokenizer()
    data = load_split(PHASE2_SPLITS / "validation.jsonl", tok, 2048, validate="phase2", limit=200)
    rep = data.report
    assert rep.records == 200 and rep.invalid == 0 and rep.samples == len(data) >= 200
    for s in data:
        assert len(s["input_ids"]) == len(s["labels"]) == len(s["attention_mask"]) <= 2048
        assert any(l != IGNORE for l in s["labels"])


def test_collate_right_padding_shapes_and_values():
    import torch
    tok = tokenizer()
    data = load_split(PHASE2_SPLITS / "validation.jsonl", tok, 2048, validate="phase2", limit=40)
    batch = [TorchDataset(data)[i] for i in range(8)]
    b = collate(batch)
    L = max(len(x["input_ids"]) for x in batch)
    assert b["input_ids"].shape == b["labels"].shape == b["attention_mask"].shape == (8, L)
    assert b["input_ids"].dtype == torch.long
    for i, x in enumerate(batch):
        n = len(x["input_ids"])
        assert b["input_ids"][i, :n].tolist() == x["input_ids"]
        assert (b["input_ids"][i, n:] == PAD_ID).all() and (b["attention_mask"][i, n:] == 0).all() and (b["labels"][i, n:] == IGNORE).all()
        assert (b["attention_mask"][i, :n] == 1).all()


def test_collate_left_padding_and_multiple():
    tok = tokenizer()
    data = load_split(PHASE2_SPLITS / "validation.jsonl", tok, 2048, validate="phase2", limit=10)
    batch = [TorchDataset(data)[i] for i in range(4)]
    b = collate(batch, padding_side="left", pad_to_multiple_of=8)
    assert b["input_ids"].shape[1] % 8 == 0
    for i, x in enumerate(batch):
        n = len(x["input_ids"])
        assert b["input_ids"][i, -n:].tolist() == x["input_ids"] and (b["attention_mask"][i, :-n] == 0).all()
    for bad in ((lambda: collate([])), (lambda: collate(batch, padding_side="middle"))):
        try:
            bad()
        except (DataError, ValueError):
            continue
        raise AssertionError("expected a clear error")


def test_dataloader_integration():
    import torch
    from torch.utils.data import DataLoader
    tok = tokenizer()
    data = load_split(PHASE2_SPLITS / "validation.jsonl", tok, 1024, validate="phase2", limit=64)
    dl = DataLoader(TorchDataset(data), batch_size=4, shuffle=False, collate_fn=collate)
    first = next(iter(dl))
    assert first["input_ids"].shape[0] == 4 and first["labels"].dtype == torch.long


def test_length_grouping_deterministic_and_complete():
    lengths = [(i * 37) % 500 + 5 for i in range(1000)]
    a = length_grouped_batches(lengths, 8, seed=1, epoch=0)
    b = length_grouped_batches(lengths, 8, seed=1, epoch=0)
    c = length_grouped_batches(lengths, 8, seed=1, epoch=1)
    assert a == b and a != c
    assert sorted(i for batch in a for i in batch) == list(range(1000))
    rnd = [list(range(i, min(i + 8, 1000))) for i in range(0, 1000, 8)]
    assert padding_waste(lengths, a) < padding_waste(lengths, rnd), "length grouping must reduce padding"


def test_token_budget_batches():
    lengths = [(i * 53) % 700 + 10 for i in range(600)]
    batches = token_budget_batches(lengths, max_tokens=2048, seed=3)
    assert sorted(i for b in batches for i in b) == list(range(600))
    for b in batches:
        assert len(b) == 1 or max(lengths[i] for i in b) * len(b) <= 2048
    assert batches == token_budget_batches(lengths, max_tokens=2048, seed=3)


def test_tool_records_load_and_validate_modes():
    tok = tokenizer()
    d = _tmp()
    p = _write(d / "t.jsonl", [tool_record(), multi_record()])
    data = load_split(p, tok, 2048, validate="strict")
    assert data.report.invalid == 0 and len(data) == 5      # 2 + 3 assistant turns
    try:
        load_split(p, tok, 2048, validate="phase2")
    except DataError as e:
        assert "failed phase2 validation" in str(e)
    else:
        raise AssertionError("phase2 mode must reject tool records")


def test_safe_failures_have_clear_messages():
    tok = tokenizer()
    d = _tmp()
    for fn, needle in (
        (lambda: read_jsonl(d / "missing.jsonl"), "not found"),
        (lambda: read_jsonl(_write(d / "empty.jsonl", [])), "empty"),
    ):
        try:
            fn()
        except DataError as e:
            assert needle in str(e)
        else:
            raise AssertionError(needle)
    (d / "broken.jsonl").write_text('{"messages": []}\n{not json}\n', encoding="utf-8")
    try:
        read_jsonl(d / "broken.jsonl")
    except DataError as e:
        assert ":2:" in str(e) and "invalid JSON" in str(e)
    else:
        raise AssertionError("malformed JSON must raise with the line number")
    bad = multi_record(); bad["id"] = "bad-1"; bad["messages"][2]["content"] = ""
    p = _write(d / "bad.jsonl", [multi_record(), bad])
    try:
        load_split(p, tok, validate="strict")
    except DataError as e:
        assert "bad-1" in str(e)
    else:
        raise AssertionError("invalid record must raise")
    data = load_split(p, tok, validate="skip")
    assert data.report.invalid == 1 and data.report.records == 2 and len(data) == 3


def test_stats_keys_and_cli(tmp=None):
    tok = tokenizer()
    recs = read_jsonl(PHASE2_SPLITS / "validation.jsonl")[:60]
    data = load_split(PHASE2_SPLITS / "validation.jsonl", tok, 2048, validate="phase2", limit=60)
    st = compute_stats(recs, data, tok, data.report, 2048)
    for k in ("records", "samples", "per_sample_tokens", "per_conversation_tokens", "label_tokens", "conversations_fit_pct", "histogram_per_sample",
              "per_category_tokens", "multi_turn_records", "tool_records", "truncation_pct_at_max_len"):
        assert k in st, k
    assert st["per_sample_tokens"]["max"] >= st["per_sample_tokens"]["median"] > 0
    out = _tmp()
    rc = cli.main(["stats", "--split", "validation", "--data-dir", str(PHASE2_SPLITS), "--mode", "phase2", "--limit", "30", "--out-dir", str(out)])
    assert rc == 0 and (out / "data_stats_validation.json").is_file() and (out / "data_stats_validation.md").is_file()
    assert cli.main(["validate", "--split", "validation", "--data-dir", str(PHASE2_SPLITS), "--mode", "phase2", "--limit", "50"]) == 0
    assert cli.main(["validate", "--split", "train", "--data-dir", str(_tmp())]) in (1, 2)


if __name__ == "__main__":
    sys.exit(run_module(globals()))
