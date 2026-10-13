"""Render + label-masking tests (B4/C2/C3), including tool-call fixtures and property tests on >=500 real records."""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_testlib import WS, multi_record, real_records, run_module, tokenizer, tool_record  # noqa: E402
from pipeline.data.render import (HEADER, IGNORE, IM_END, THINK_EMPTY, render_example, render_prompt, render_samples,  # noqa: E402
                                  render_text, tokenization_boundary_equal, turn_texts)

CAND = WS / "dataset" / "v2_final_candidate"


def labelled_text(tok, s):
    return tok.decode([i for i, l in zip(s["input_ids"], s["labels"]) if l != IGNORE])


def test_single_turn_labels_exact():
    tok = tokenizer()
    rec = {"id": "a", "messages": [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello!"}]}
    (s,), dropped = render_samples(rec, tok)
    assert dropped == 0
    assert tok.decode(s["input_ids"]) == "<|im_start|>user\nHi<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\nHello!<|im_end|>"
    assert labelled_text(tok, s) == "Hello!" + IM_END
    n_lab = sum(1 for l in s["labels"] if l != IGNORE)
    assert all(l == IGNORE for l in s["labels"][:-n_lab]), "labels must be one contiguous block at the end"
    assert s["labels"][-1] == 151645, "the closing <|im_end|> must be a label (so the model learns to stop)"
    assert tok.decode(s["input_ids"][:-n_lab]).endswith(HEADER + THINK_EMPTY), "the generation header + empty think block are unlabelled prompt"
    assert s["n_tokens"] == len(s["input_ids"]) == len(s["labels"]) == len(s["attention_mask"])


def test_header_and_think_block_never_labelled():
    tok = tokenizer()
    for s in render_samples(multi_record(), tok)[0]:
        lt = labelled_text(tok, s)
        assert "<|im_start|>" not in lt and "<think>" not in lt and "</think>" not in lt


def test_per_turn_one_sample_per_assistant_and_prefix_exact():
    tok = tokenizer()
    rec = multi_record()
    samples, dropped = render_samples(rec, tok)
    assert dropped == 0 and len(samples) == 3
    for s, t in zip(samples, (2, 4, 6)):
        assert s["turn_index"] == t
        prompt = render_prompt(tok, rec["messages"][:t])
        prompt_ids = tok(prompt, add_special_tokens=False)["input_ids"]
        assert s["input_ids"][:len(prompt_ids)] == prompt_ids, "training prefix must equal the inference prompt at this generation point"
        assert all(l == IGNORE for l in s["labels"][:len(prompt_ids)])
        assert labelled_text(tok, s) == rec["messages"][t]["content"] + IM_END
        assert prompt.endswith(HEADER + THINK_EMPTY)


def test_tool_fixture_labels():
    tok = tokenizer()
    rec = tool_record()
    samples, dropped = render_samples(rec, tok)
    assert [s["turn_index"] for s in samples] == [2, 4] and dropped == 0
    call = labelled_text(tok, samples[0])
    assert call == '<tool_call>\n{"name": "web_search", "arguments": {"query": "Python 3.13 release notes é"}}\n</tool_call>' + IM_END
    assert labelled_text(tok, samples[1]) == rec["messages"][4]["content"] + IM_END
    full = tok.decode(samples[1]["input_ids"])
    assert "<tool_response>\n{\"results\": []}\n</tool_response>" in full
    # the tool result text itself is context, never a label
    assert '{"results": []}' not in labelled_text(tok, samples[1]) and "<tool_response>" not in labelled_text(tok, samples[1])
    assert "# Tools" in full and "web_search" in full.split("<|im_start|>user")[0], "tools block belongs to the system part"


def test_trailing_tool_call_conversation():
    tok = tokenizer()
    rec = tool_record()
    rec["messages"] = rec["messages"][:3]            # ends on the assistant tool request
    from pipeline.data.schema import validate_record
    assert validate_record(rec) == []
    (s,), _ = render_samples(rec, tok)
    assert labelled_text(tok, s).startswith("<tool_call>") and labelled_text(tok, s).endswith("</tool_call>" + IM_END)


def test_last_assistant_equals_last_per_turn_sample():
    tok = tokenizer()
    for rec in (multi_record(), tool_record()):
        last = render_samples(rec, tok)[0][-1]
        single = render_example(rec, tok, train_on="last_assistant")
        assert single["input_ids"] == last["input_ids"] and single["labels"] == last["labels"]


def test_all_assistant_labels_cover_exactly_assistant_content():
    tok = tokenizer()
    rec = multi_record()
    s = render_example(rec, tok, train_on="all_assistant")
    exp = "".join(m["content"] + IM_END for m in rec["messages"] if m["role"] == "assistant")
    assert labelled_text(tok, s) == exp
    assert tok.decode(s["input_ids"]) == render_text(tok, rec["messages"]).rstrip("\n")


def test_per_turn_rejects_render_example():
    try:
        render_example(multi_record(), tokenizer(), train_on="per_turn")
    except ValueError:
        return
    raise AssertionError("render_example(per_turn) must raise")


def test_unknown_mode_rejected():
    try:
        render_samples(multi_record(), tokenizer(), train_on="nope")
    except ValueError:
        return
    raise AssertionError("unknown train_on must raise")


def test_truncation_policy():
    tok = tokenizer()
    rec = multi_record()
    full_len = render_example(rec, tok, max_len=10 ** 6, train_on="all_assistant")["n_tokens"]
    # single-sequence modes: cut whole trailing exchanges, flag, labels still end on <|im_end|>
    s = render_example(rec, tok, max_len=full_len - 1, train_on="all_assistant")
    assert s is not None and s["truncated"] and s["n_tokens"] <= full_len - 1
    assert s["labels"][-1] == 151645 and tok.decode(s["input_ids"]).endswith("4." + IM_END)
    assert render_example(rec, tok, max_len=5, train_on="all_assistant") is None, "nothing fits -> None, never a dangling half turn"
    # per_turn: a turn that does not fit is dropped whole and counted
    samples, dropped = render_samples(rec, tok, max_len=60)
    assert dropped >= 1 and all(s["n_tokens"] <= 60 for s in samples)
    for s in samples:
        assert s["labels"][-1] == 151645


def test_property_real_phase2_records_500():
    """>=500 random real records: every per-turn sample's labelled text == that assistant's content + <|im_end|>;
    prompt prefix == inference prompt; labels contiguous at the end; single-sequence all_assistant labels == all assistant content."""
    tok = tokenizer()
    recs = real_records("train", 600, seed=1)
    assert len(recs) >= 500
    n_samples = 0
    for rec in recs:
        msgs = rec["messages"]
        samples, dropped = render_samples(rec, tok, max_len=8192)
        assert dropped == 0
        asst = [i for i, m in enumerate(msgs) if m["role"] == "assistant"]
        assert [s["turn_index"] for s in samples] == asst
        for s, t in zip(samples, asst):
            n_samples += 1
            assert labelled_text(tok, s) == msgs[t]["content"] + IM_END, rec.get("id")
            n_lab = s["n_label_tokens"]
            assert all(l == IGNORE for l in s["labels"][:-n_lab]) and all(l != IGNORE for l in s["labels"][-n_lab:])
            prompt_ids = tok(render_prompt(tok, msgs[:t]), add_special_tokens=False)["input_ids"]
            assert s["input_ids"][:len(prompt_ids)] == prompt_ids
        single = render_example(rec, tok, max_len=8192, train_on="all_assistant")
        assert labelled_text(tok, single) == "".join(m["content"] + IM_END for m in msgs if m["role"] == "assistant"), rec.get("id")
        # no label on system/user text: every labelled token lies inside an assistant span by construction; double-check content
        for m in msgs:
            if m["role"] in ("system", "user") and len(m["content"]) > 25:
                assert m["content"] not in labelled_text(tok, single)
    assert n_samples > len(recs)           # multi-turn expansion happened


def test_property_boundary_tokenisation_equal_500():
    tok = tokenizer()
    bad = [r.get("id") for r in real_records("train", 500, seed=2) if not tokenization_boundary_equal(tok, r)]
    assert not bad, f"separate tokenisation of prompt/target differs from joint tokenisation for {bad[:5]}"


def test_property_candidate_with_tools_500():
    """Same property on the converted chat+tools candidate (skipped if the directory does not exist yet)."""
    if not (CAND / "train.jsonl").is_file():
        print("  (skipped: dataset/v2_final_candidate missing)")
        return
    tok = tokenizer()
    recs = [json.loads(l) for l in open(CAND / "train.jsonl", encoding="utf-8") if l.strip()]
    with_tools = [r for r in recs if r.get("tools")]
    random.Random(3).shuffle(with_tools)
    random.Random(4).shuffle(recs)
    sample = with_tools[:300] + recs[:300]
    assert len(sample) >= 500
    for rec in sample:
        msgs = rec["messages"]
        samples, dropped = render_samples(rec, tok, max_len=8192)
        assert dropped == 0
        for s in samples:
            t = s["turn_index"]
            m = msgs[t]
            lt = labelled_text(tok, s)
            assert lt.endswith(IM_END)
            if m.get("tool_calls"):
                assert lt.count("<tool_call>") == len(m["tool_calls"]) and lt.count("</tool_call>") == len(m["tool_calls"])
                for c in m["tool_calls"]:
                    assert json.dumps(c["function"]["arguments"], ensure_ascii=False) in lt
            else:
                assert lt == m["content"] + IM_END
            assert "<tool_response>" not in lt
            prompt_ids = tok(render_prompt(tok, msgs[:t], rec.get("tools")), add_special_tokens=False)["input_ids"]
            assert s["input_ids"][:len(prompt_ids)] == prompt_ids
        assert tokenization_boundary_equal(tok, rec), rec.get("id")


if __name__ == "__main__":
    sys.exit(run_module(globals()))
