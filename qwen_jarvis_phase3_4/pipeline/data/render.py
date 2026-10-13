"""Rendering + label masking for Qwen3 chat data.

THINKING-TEMPLATE QUIRK (verified from _hf_meta/tokenizer_config.json, see docs/CONTRACT.md)
  * apply_chat_template(..., enable_thinking=False) ends the *generation prompt* with
        <|im_start|>assistant\\n<think>\\n\\n</think>\\n\\n
    so at inference the model always continues from an empty think block, at every turn.
  * When a conversation is rendered *with* its answers, only the LAST assistant message (and assistant messages that carry
    reasoning) gets that empty think block; earlier assistant messages are rendered without it (history form).
  * Rendering a whole multi-turn conversation once therefore shows the model earlier answers in a form it will never be asked
    to generate, and the final answer in the form it will.

CONVENTION CHOSEN (default train_on="per_turn"; the only mode that is exact at every generation point)
  One training sample per assistant message t:
        prompt_t  = apply_chat_template(messages[:t], tools, add_generation_prompt=True, enable_thinking=False)
        target_t  = everything the model must generate = content (+ <tool_call> blocks) + <|im_end|>
        sample    = tokens(prompt_t) + tokens(target_t); loss only on target_t.
  target_t is obtained from the template itself (render messages[:t+1] and strip the shared prompt prefix), so the
  tool_call serialisation can never drift from the official template. prompt_t and target_t are tokenised separately and
  concatenated, which is exactly what inference does (prompt tokenised, model emits the target tokens).
  The history inside prompt_t is rendered by the template exactly as it would be at inference time.

Other modes (single sequence, one tokenisation of the whole rendered conversation, offsets used to find assistant spans):
  "last_assistant": loss on the final assistant message only (identical to the last per_turn sample).
  "all_assistant" : loss on every assistant message in the single rendering; earlier assistant turns lack the empty think block
                    (template history form), so they are slightly off-distribution relative to inference. Cheaper (1 sample per
                    conversation) but not exact; documented, available, not the default.
The header '<|im_start|>assistant\\n' and the empty think block are never labelled: at inference they are part of the prompt.
The label span covers content + <tool_call> blocks + the closing <|im_end|> (the newline the template appends after it is not part
of the sequence).
"""
from __future__ import annotations

from pathlib import Path

IGNORE = -100
HEADER = "<|im_start|>assistant\n"
THINK_EMPTY = "<think>\n\n</think>\n\n"
IM_END = "<|im_end|>"
PAD_ID = 151643          # <|endoftext|>
EOS_ID = 151645          # <|im_end|>
DEFAULT_TOKENIZER_DIR = Path(__file__).resolve().parents[2] / "_hf_meta"
MODES = ("per_turn", "last_assistant", "all_assistant")


def load_tokenizer(path=None):
    """Load the real Qwen3-8B tokenizer from local files only (no network, no weights)."""
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(str(path or DEFAULT_TOKENIZER_DIR), local_files_only=True, use_fast=True)


def _clean_messages(messages):
    out = []
    for m in messages:
        d = {"role": m["role"], "content": m.get("content", "")}
        if m.get("tool_calls"):
            d["tool_calls"] = m["tool_calls"]
        out.append(d)
    return out


def render_text(tok, messages, tools=None, add_generation_prompt=False):
    """Official template, non-thinking mode."""
    return tok.apply_chat_template(_clean_messages(messages), tools=tools or None, tokenize=False,
                                   add_generation_prompt=add_generation_prompt, enable_thinking=False)


def render_prompt(tok, messages, tools=None):
    """Exactly what the model sees at inference for the next assistant turn."""
    return render_text(tok, messages, tools, add_generation_prompt=True)


def assistant_indices(messages):
    return [i for i, m in enumerate(messages) if m["role"] == "assistant"]


def turn_texts(tok, rec, t):
    """(prompt_text, target_text) for assistant message t, derived from the template."""
    msgs, tools = rec["messages"], rec.get("tools")
    prompt = render_prompt(tok, msgs[:t], tools)
    full = render_text(tok, msgs[:t + 1], tools)
    if not full.startswith(prompt):
        raise ValueError(f"template prefix property violated at message {t} of {rec.get('id')}")
    target = full[len(prompt):]
    if not target.endswith(IM_END + "\n"):
        raise ValueError(f"unexpected target ending at message {t} of {rec.get('id')}: {target[-30:]!r}")
    return prompt, target[:-1]            # drop the template's newline after <|im_end|>


def _sample(ids, labels, truncated, rec, t):
    return {"input_ids": ids, "labels": labels, "attention_mask": [1] * len(ids), "n_tokens": len(ids), "truncated": truncated,
            "id": rec.get("id"), "category": (rec.get("metadata") or {}).get("category"), "turn_index": t,
            "n_label_tokens": sum(1 for x in labels if x != IGNORE)}


def render_per_turn(tok, rec, max_len=2048):
    """Returns (samples, dropped_turns). A turn whose prompt+target exceeds max_len cannot be shortened without changing what the
    model must generate, so it is dropped and counted (never cut mid-turn)."""
    samples, dropped = [], 0
    for t in assistant_indices(rec["messages"]):
        prompt, target = turn_texts(tok, rec, t)
        p = tok(prompt, add_special_tokens=False)["input_ids"]
        g = tok(target, add_special_tokens=False)["input_ids"]
        if len(p) + len(g) > max_len:
            dropped += 1
            continue
        samples.append(_sample(p + g, [IGNORE] * len(p) + g, False, rec, t))
    return samples, dropped


def _span_labels(tok, text, spans, rec, max_len, t_index):
    enc = tok(text, add_special_tokens=False, return_offsets_mapping=True)
    ids, offs = enc["input_ids"], enc["offset_mapping"]
    labels = []
    for (a, b), tid in zip(offs, ids):
        inside = any(a >= s and b <= e for s, e in spans)
        labels.append(tid if inside else IGNORE)
    return ids, labels


def _assistant_spans(text, only_last):
    spans, pos = [], 0
    while True:
        h = text.find(HEADER, pos)
        if h < 0:
            break
        start = h + len(HEADER)
        if text.startswith(THINK_EMPTY, start):
            start += len(THINK_EMPTY)                     # prompt-side at inference: never labelled
        end = text.index(IM_END, start) + len(IM_END)
        spans.append((start, end))
        pos = end
    return spans[-1:] if only_last else spans


def render_single(tok, rec, max_len=2048, train_on="all_assistant"):
    """Single-sequence modes. If too long, cut on whole-turn boundaries from the right (drop trailing exchanges) and flag
    truncated; returns None when not even the first exchange fits."""
    msgs, tools = rec["messages"], rec.get("tools")
    ends = [i + 1 for i, m in enumerate(msgs) if m["role"] == "assistant" and not m.get("tool_calls")]   # prefixes that end in a final answer
    for cut_i, k in enumerate(reversed(ends)):
        text = render_text(tok, msgs[:k], tools)
        text = text[:-1] if text.endswith("\n") else text
        spans = _assistant_spans(text, train_on == "last_assistant")
        ids, labels = _span_labels(tok, text, spans, rec, max_len, k)
        if len(ids) <= max_len:
            return _sample(ids, labels, cut_i > 0, rec, k - 1)
    return None


def render_samples(rec, tok, max_len=2048, train_on="per_turn"):
    """General entry point. Returns (samples, n_dropped_or_truncated_units)."""
    if train_on not in MODES:
        raise ValueError(f"train_on must be one of {MODES}")
    if train_on == "per_turn":
        return render_per_turn(tok, rec, max_len)
    s = render_single(tok, rec, max_len, train_on)
    return ([s] if s else []), (0 if s and not s["truncated"] else 1)


def render_example(rec, tok, max_len=2048, *, train_on="all_assistant"):
    """Contract signature: one sample per conversation (single-sequence modes). For per_turn use render_samples()."""
    if train_on == "per_turn":
        raise ValueError("per_turn produces several samples per conversation; use render_samples()")
    return render_single(tok, rec, max_len, train_on)


def tokenization_boundary_equal(tok, rec):
    """True if tokenising prompt and target separately equals tokenising the joined text, for every assistant turn."""
    for t in assistant_indices(rec["messages"]):
        prompt, target = turn_texts(tok, rec, t)
        a = tok(prompt, add_special_tokens=False)["input_ids"] + tok(target, add_special_tokens=False)["input_ids"]
        b = tok(prompt + target, add_special_tokens=False)["input_ids"]
        if a != b:
            return False
    return True
