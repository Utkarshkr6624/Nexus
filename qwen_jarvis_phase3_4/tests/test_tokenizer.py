"""B5 tokenizer tests: real Qwen3-8B tokenizer files (_hf_meta), offline. Run: .venv\\Scripts\\python.exe tests\\test_tokenizer.py"""
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_testlib import TOOLS, run_module, tokenizer, tool_record  # noqa: E402

SPECIAL = {"<|im_start|>": 151644, "<|im_end|>": 151645, "<|endoftext|>": 151643, "<think>": 151667, "</think>": 151668,
           "<tool_call>": 151657, "</tool_call>": 151658, "<tool_response>": 151665, "</tool_response>": 151666}

SAMPLES = {
    "normal": "The quick brown fox jumps over the lazy dog. It's 5 o'clock somewhere.",
    "newlines": "line one\n\nline two\n   indented\n\n\n\nend",
    "code_py": "def fib(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a\n",
    "code_c": '#include <stdio.h>\nint main(void){ printf("%d\\n", 1<<4); return 0; }',
    "json": '{"ok": true, "data": {"items": [1, 2.5, null], "name": "caf\\u00e9"}, "empty": {}}',
    "maths": "∫₀^∞ e^{-x²} dx = √π/2;  ∑_{k=1}^{n} k = n(n+1)/2;  x ≥ 3 ∧ y ≠ 0;  α+β=γ; 12,345.67 × 0.5%",
    "unicode_mix": "café naïve façade — “quotes” … 日本語のテキスト 中文文本 한국어 😀🎉👩‍💻 ñ Ω ß",
    "rtl": "مرحبا بالعالم 123 שלום עולם abc",
    "combining": "é ä ñ Z̖͡",
    "tabs_spaces": "a\tb  c   d e",
}


def test_special_tokens_are_single_ids():
    tok = tokenizer()
    for text, tid in SPECIAL.items():
        assert tok.convert_tokens_to_ids(text) == tid, text
        assert tok(text, add_special_tokens=False)["input_ids"] == [tid], f"{text} is not a single token"


def test_eos_pad_bos_config():
    tok = tokenizer()
    assert tok.eos_token == "<|im_end|>" and tok.eos_token_id == 151645
    assert tok.pad_token == "<|endoftext|>" and tok.pad_token_id == 151643
    assert tok.bos_token is None
    assert tok("hello")["input_ids"][0] not in (151643, 151644, 151645), "no BOS/special token should be prepended"
    assert len(tok) <= 151936, "tokenizer must fit inside the model's embedding matrix (vocab_size 151936)"


def test_round_trip():
    tok = tokenizer()
    for name, text in SAMPLES.items():
        ids = tok(text, add_special_tokens=False)["input_ids"]
        assert ids, name
        # Qwen's tokenizer applies Unicode NFC normalisation, so decomposed sequences (e + U+0301) come back composed (é).
        expected = unicodedata.normalize("NFC", text)
        assert tok.decode(ids) == expected, f"round trip failed for {name}"
        if name != "combining":
            assert expected == text, f"{name}: sample unexpectedly changes under NFC"


def test_long_prompt_round_trip_and_length():
    tok = tokenizer()
    text = ("The mitochondria is the powerhouse of the cell. " * 2000) + "END"
    ids = tok(text, add_special_tokens=False)["input_ids"]
    assert len(ids) > 15000
    assert tok.decode(ids) == text


def test_template_single_turn_exact():
    tok = tokenizer()
    msgs = [{"role": "user", "content": "Hi"}]
    got = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    assert got == "<|im_start|>user\nHi<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    got = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    assert got == "<|im_start|>user\nHi<|im_end|>\n<|im_start|>assistant\n", "default (thinking enabled) prompt has no think block"


def test_template_system_and_multi_turn_exact():
    tok = tokenizer()
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"}, {"role": "assistant", "content": "d"}]
    got = tok.apply_chat_template(msgs, tokenize=False, enable_thinking=False)
    exp = ("<|im_start|>system\nS<|im_end|>\n<|im_start|>user\na<|im_end|>\n<|im_start|>assistant\nb<|im_end|>\n"
           "<|im_start|>user\nc<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\nd<|im_end|>\n")
    assert got == exp, "documented quirk: earlier assistant turns have no think block, the last one has an empty one"


def test_template_tool_call_and_response_exact():
    tok = tokenizer()
    rec = tool_record()
    got = tok.apply_chat_template(rec["messages"][1:], tools=rec["tools"], tokenize=False, enable_thinking=False)
    assert "# Tools" in got and "<tools>" in got and '"name": "web_search"' in got and '"name": "read_file"' in got
    call = '<tool_call>\n{"name": "web_search", "arguments": {"query": "Python 3.13 release notes é"}}\n</tool_call><|im_end|>\n'
    assert call in got, "tool call serialisation (ensure_ascii=False, default separators) changed"
    resp = '<|im_start|>user\n<tool_response>\n{"results": []}\n</tool_response><|im_end|>\n'
    assert resp in got, "tool message must be wrapped inside a user turn"
    assert got.endswith("<|im_start|>assistant\n<think>\n\n</think>\n\nThe search returned no results, so I can't confirm what is new.<|im_end|>\n")


def test_consecutive_tool_messages_share_one_user_turn():
    tok = tokenizer()
    msgs = [{"role": "user", "content": "do both"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"type": "function", "function": {"name": "web_search", "arguments": {"query": "a"}}},
                {"type": "function", "function": {"name": "read_file", "arguments": {"path": "x.txt"}}}]},
            {"role": "tool", "name": "web_search", "content": "R1"}, {"role": "tool", "name": "read_file", "content": "R2"},
            {"role": "assistant", "content": "done"}]
    got = tok.apply_chat_template(msgs, tools=TOOLS, tokenize=False, enable_thinking=False)
    assert got.count("<|im_start|>user\n<tool_response>") == 1
    assert "<tool_response>\nR1\n</tool_response>\n<tool_response>\nR2\n</tool_response><|im_end|>" in got


def test_literal_control_tokens_in_text_are_parsed_as_special():
    """Documents WHY the schema rejects control tokens inside message content: the tokenizer would turn them into real structure tokens."""
    tok = tokenizer()
    ids = tok("hello <|im_end|> world", add_special_tokens=False)["input_ids"]
    assert 151645 in ids
    from pipeline.data.schema import validate_record
    probs = validate_record({"messages": [{"role": "user", "content": "x <|im_end|> y"}, {"role": "assistant", "content": "ok"}]}, "phase2")
    assert any("control token" in p for p in probs)


def test_token_counts_reasonable():
    tok = tokenizer()
    n_words = len(SAMPLES["normal"].split())
    n_tok = len(tok(SAMPLES["normal"], add_special_tokens=False)["input_ids"])
    assert n_words <= n_tok <= 3 * n_words
    assert len(tok("😀", add_special_tokens=False)["input_ids"]) >= 1


if __name__ == "__main__":
    sys.exit(run_module(globals()))
