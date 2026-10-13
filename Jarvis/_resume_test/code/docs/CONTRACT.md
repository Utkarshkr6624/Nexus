# Phase 3/4 workspace contract (shared by all agents)

Workspace root: `E:\Nexo\qwen_jarvis_phase3_4\` (isolated). Read-only inputs: `E:\Nexo\qwen_jarvis_phase2\` (dataset v2 + scripts), `E:\Nexo\qwen_jarvis_phase1\` (NEXUS facts).
**Never modify anything outside this workspace.** Never touch NEXUS source, the 20 pre-existing untracked files, `E:\Nexo\.venv`, `.env`. Never download model weights (tokenizer/config metadata in `_hf_meta/` is already fetched and is enough). No Kaggle, no production training.
Use the **Write/Edit tools** for files (bash heredocs mangle backslashes). Python for this workspace: `E:\Nexo\qwen_jarvis_phase3_4\.venv\Scripts\python.exe` (CPU torch + transformers + peft + trl + datasets + accelerate + pyyaml + jsonschema; installed by the coordinator; check `E:\Nexo\qwen_jarvis_phase3_4\.venv\Scripts\python.exe -c "import torch,transformers"` before relying on it; if the flag file `C:\Users\utkar\AppData\Local\Temp\pip_done.flag` / `/tmp/pip_done.flag` does not exist yet, install is still running - work on code that does not need it first). Plain `python` (3.13, stdlib + yaml + jinja2 + nbformat + numpy) is also available.

## Verified facts about Qwen3-8B (from the real files in `_hf_meta/`)
- `config.json`: Qwen3ForCausalLM, 36 layers, hidden 4096, 32 heads / 8 KV heads, head_dim 128, intermediate 12288, vocab 151936, max_position_embeddings 40960, torch_dtype bfloat16, transformers_version 4.51.0 (so transformers >= 4.51 is required), tie_word_embeddings false.
- `tokenizer_config.json`: eos `<|im_end|>` (id 151645), pad `<|endoftext|>` (151643), no BOS, model_max_length 131072.
- Chat template (ChatML): `<|im_start|>role\ncontent<|im_end|>\n`. Tools: `tools=[{type:function,function:{name,description,parameters}}]` are rendered in the system block under `# Tools` with `<tools></tools>`; an assistant message with `tool_calls=[{type:function,function:{name,arguments(dict)}}]` renders as `<tool_call>\n{"name": "...", "arguments": {...}}\n</tool_call>`; messages with `role:"tool"` render inside a *user* turn as `<tool_response>\n...\n</tool_response>` (consecutive tool messages share one user turn).
- Thinking: the template inserts `<think>\n\n</think>\n\n` before the content of the LAST assistant message (and any assistant message after the last real user query that has reasoning). Earlier assistant turns get no think block. `enable_thinking=False` at inference appends `<think>\n\n</think>\n\n` to the generation prompt. Our data is non-thinking; the training renderer must be consistent with `enable_thinking=False` inference.

## Data record format (Phase 3 final; "chat+tools")
One JSON object per line:
```json
{"id": "qj2f-<12hex>", "messages": [
   {"role": "system", "content": "..."},
   {"role": "user", "content": "..."},
   {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "web_search", "arguments": {"query": "..."}}}]},
   {"role": "tool", "name": "web_search", "content": "{...}"},
   {"role": "assistant", "content": "final answer"}],
 "tools": [{"type": "function", "function": {"name": "...", "description": "...", "parameters": {"type": "object", "properties": {}, "required": []}}}],
 "metadata": {"category": "...", "subcategory": "...", "difficulty": "...", "source_type": "...", "group": "...", "turns": 1, "source_id": "qj2-...", "edited": false}}
```
- `tools` is present only when the conversation has tools attached. Examples without tools (the majority) have no `tools` key and their system message says no tools are attached.
- Roles: system (optional, first, at most one), user, assistant, tool. Assistant with `tool_calls` may have empty/short content. `arguments` is a JSON object (dict), never a string.
- Splits: `dataset/v2_final/{train,validation,test}.jsonl`. Messages-only variants are not needed.

## Directory ownership (do not write outside your own dirs)
- `preflight/` + `dataset/v2_clean/` : Preflight agent. `tool_protocol/` + `dataset/v2_final/` : Tool-protocol agent (final conversion done by the coordinator).
- `pipeline/data/` (+ `tests/test_data_*.py`, `tests/test_tokenizer*.py`) : Data agent.
- `pipeline/train/` , `configs/` (+ `tests/test_train_*.py`, `tests/test_checkpoint*.py`) : Training agent.
- `pipeline/eval/` (+ `tests/test_eval_*.py`) : Evaluation agent.
- `kaggle/` , `docs/MEMORY_PLAN.md` : Kaggle agent.
- `docs/`, `reports/`, `FINAL_*` : coordinator.

## Interfaces between components
- `pipeline/data/render.py`: `render_example(rec, tokenizer, max_len, *, train_on="all_assistant"|"last_assistant") -> dict(input_ids:list[int], labels:list[int], attention_mask:list[int], n_tokens:int, truncated:bool)`; uses `tokenizer.apply_chat_template(messages, tools=rec.get("tools"), tokenize=False, enable_thinking=False)` pieces and builds `labels` = -100 except assistant content/tool_call tokens (and their closing `<|im_end|>`), consistent with the template. `pipeline/data/dataset.py`: `load_split(path, tokenizer, max_len, ...) -> list[dict]`, `collate(batch, pad_token_id) -> dict(tensors)`, `stats(...)`.
- `pipeline/train/train.py` consumes the above plus `configs/qlora_sft.yaml`; checkpoints in `runs/<name>/checkpoint-<step>/` with `checkpoint_meta.json`.
- `pipeline/eval/` consumes a generate-function interface `generate(messages, tools) -> str` so it works with any model (including a stub for tests).
