# NEXO local model runtime (llama.cpp)

NEXO is the generative assistant at `http://localhost:5173/nexo`: **Qwen/Qwen3-8B + the `checkpoint_v31_139` LoRA
adapter**. It is a separate system from the AI Assistant at `/assistant` (NEXUS's `microsoft/deberta-v3-base` intent
classifier), which needs none of this.

The adapter is a **development candidate, not acceptance-qualified**: Phase 6 verdict FAIL. This runtime also differs
from the Phase 6 evaluation (bitsandbytes nf4 on a T4/P100): the base is the official Q4_K_M GGUF, the LoRA is applied
at runtime in f16, and the KV cache is q8_0. Outputs from here are **not** the evaluated outputs.

## Artifacts (verified 2026-10-12)

| File | Source | sha256 |
|---|---|---|
| `models/Qwen3-8B-Q4_K_M.gguf` (5,027,783,488 bytes) | `huggingface.co/Qwen/Qwen3-8B-GGUF`, revision `7c41481f57cb95916b40956ab2f0b139b296d974` | `d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785` (equals the Hugging Face LFS oid) |
| `models/jarvis_v31_139_lora.gguf` (87.3 MB, f16, 504 tensors) | converted from `Jarvis/jarvis_v31_remediation_train/runs/qwen3_8b_jarvis_v3_1_remediation/checkpoint-139/adapter` (sha256 `d754a3c6…`, unchanged by conversion) | `73300722d59c31a0cbbce5dc5b531f7967357d908d057d3de17c58e55dda9b68` |
| `LORA_GGUF.json` | conversion manifest: ties the GGUF LoRA to the verified adapter; `serve_jarvis.py` refuses to start if either hash differs | — |
| `llama.cpp/` | `llama-b11570-bin-win-cuda-12.4-x64.zip` + `cudart-llama-bin-win-cuda-12.4-x64.zip`, release `b11570` (commit `ccdbe2905`) | zips `7e8cfe94…` and `8c79a9b2…`, both equal to the GitHub release digests |
| `src/llama.cpp-b11570/` | source tag `b11570`, used only for `convert_lora_to_gguf.py` and `gguf-py` | — |
| `downloads/` | removed 2026-10-13 after every member was verified byte-identical to `llama.cpp/` and `src/` (see `../CLEANUP_MANIFEST.json`); re-download release b11570 and check the digests above to restore | — |

## Conversion (already done; repeat only if the adapter changes)

```
cd Jarvis\jarvis_phase7\runtime\src\llama.cpp-b11570
set PYTHONPATH=gguf-py
set HF_HUB_OFFLINE=1
E:\Nexo\backend\.venv\Scripts\python.exe convert_lora_to_gguf.py --outtype f16 ^
  --outfile ..\..\models\jarvis_v31_139_lora.gguf ^
  --base %USERPROFILE%\.cache\huggingface\hub\models--Qwen--Qwen3-8B\snapshots\b968826d9c46dd6066d109eabc6255188de91218 ^
  E:\Nexo\Jarvis\jarvis_v31_remediation_train\runs\qwen3_8b_jarvis_v3_1_remediation\checkpoint-139\adapter
```

`--base` needs only the HF config, which is in the local cache; no base weights are downloaded. Then rewrite
`LORA_GGUF.json` with the new hashes. Log of the run: `../evidence/lora_conversion.log` (exit 0).

Checks done:
- `general.type = adapter`, `adapter.type = lora`, `adapter.lora.alpha = 32`, architecture `qwen3`.
- 504 tensors = 36 layers × 7 modules (q, k, v, o, gate, up, down) × A/B.
- Every target tensor exists in the base GGUF.

## Running

`start.cmd` (→ `scripts/launch_nexus.py`) starts this automatically when all files above exist. By hand:

```
cd Jarvis\jarvis_phase7
E:\Nexo\backend\.venv\Scripts\python.exe serve_jarvis.py ^
  --adapter E:/Nexo/Jarvis/jarvis_v31_remediation_train/runs/qwen3_8b_jarvis_v3_1_remediation/checkpoint-139/adapter ^
  --expect-sha256 d754a3c6b77fb80bf24e67527d5b32f04606d8496e9e7c2b9d7cccef48f48fa8 ^
  --llama-server runtime/llama.cpp/llama-server.exe --gguf runtime/models/Qwen3-8B-Q4_K_M.gguf ^
  --gguf-sha256 d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785 ^
  --lora-gguf runtime/models/jarvis_v31_139_lora.gguf
```

- `serve_jarvis.py` listens on `127.0.0.1:8765` (`/health`, `/generate`); its llama-server child is on `:8766`. Both are loopback only.
- Defaults: 16 GPU layers, 8192-token context, q8_0 KV cache with flash attention, greedy decoding (temperature 0, top-k 1), `enable_thinking=False`.
- Prompts are rendered by llama-server's `/apply-template` from the template embedded in the GGUF. It is byte-identical to the HF tokenizer's `apply_chat_template` for plain, tool-bearing and tool-round-trip conversations (`../evidence/template_parity_result.json`), so the server does not import transformers. That saves about 1 GB of RAM.

## Measured on this machine (RTX 3050 Laptop 4 GB, 15.6 GB RAM)

- Load: about 10–30 s, including hashing the 5 GB base.
- Speed: about 87 prompt tokens/s and 9.6 generated tokens/s.
- Memory with the model, backend and frontend all running:
  - llama-server: about 3.6 GB private RAM and 3.45 GB VRAM;
  - backend: about 1.8 GB (it holds the DeBERTa classifier);
  - available RAM: about 0.6 GB.
- Close large applications for headroom; with less free RAM, Windows pages.
- The first configuration (14 layers, f16 KV, transformers in the wrapper) left only 0.25 GB available. It was replaced.

## Known model behaviour in this runtime (real generations, `../evidence/e2e_real_model.json`)

- Read tools work: `app_list_tasks` was called with a valid structured call and answered accurately.
- Write tools do **not** complete:
  - the model proposes the call in text;
  - after "Yes" it fabricates success ("I've called app_create_task … the task was created") without emitting a call;
  - the honesty guard replaces that answer, and nothing is executed.
- Occasional word repetition ("services services").
- Final pass, 2026-10-13 (`../evidence/final_lora_scale_repetition.json`): same prompts, same server, greedy:
  LoRA scale 1 gave 9 adjacent repeats in 6 answers ("CelsiusCelsius", "key-value-value"); the base alone (scale 0)
  gave 0. Adapter scaling is consistent (r 16, alpha 32, no rsLoRA/DoRA), and Qwen's recommended sampling did not
  remove it (`../evidence/final_browser_e2e.json`). The stutter comes from this adapter on this Q4_K_M base.
- Crisis prompts: unapproved and country-specific numbers are replaced by the crisis guard.

## Troubleshooting

- `/health` is not answering: check `../logs/jarvis-model.log` and `llama-server.log`.
- `REFUSING`: a hash differs from the expected value. Do not override it; re-verify the files.
- Out of memory: lower `--gpu-layers` only if VRAM is the problem (`nvidia-smi`), lower `--ctx` (for example 6144) if RAM is.
