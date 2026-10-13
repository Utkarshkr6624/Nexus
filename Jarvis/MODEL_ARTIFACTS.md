# NEXO model artifacts (kept on disk, not in Git)

These files are excluded by `.gitignore`: several exceed GitHub's 100 MB file limit, and Git for Windows
truncates files over 4 GiB (that is how `Qwen3-8B-Q4_K_M.gguf` was once corrupted to 732,816,192 bytes).
Back them up outside Git. A clone needs them in exactly these paths for `start.cmd` to load NEXO.

## Required at runtime

| Path | Bytes | SHA-256 | Source |
|---|---|---|---|
| `Jarvis/jarvis_phase7/runtime/models/Qwen3-8B-Q4_K_M.gguf` | 5027783488 | `d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785` | `https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/7c41481f57cb95916b40956ab2f0b139b296d974/Qwen3-8B-Q4_K_M.gguf` |
| `Jarvis/jarvis_phase7/runtime/models/jarvis_v31_139_lora.gguf` | 87328864 | `73300722d59c31a0cbbce5dc5b531f7967357d908d057d3de17c58e55dda9b68` | converted from the v31_139 adapter; see `runtime/LORA_GGUF.json` |
| `Jarvis/jarvis_v31_remediation_train/runs/qwen3_8b_jarvis_v3_1_remediation/checkpoint-139/adapter/adapter_model.safetensors` | 174655536 | `d754a3c6b77fb80bf24e67527d5b32f04606d8496e9e7c2b9d7cccef48f48fa8` | Kaggle `jarvis-v31-remediation-train` (the configured adapter) |
| `Jarvis/jarvis_phase7/runtime/llama.cpp/` | | | llama.cpp release b11570, Windows CUDA 12.4 build (`llama-server.exe` + DLLs) |

The base GGUF can be restored with a resumable download, then checked:

```
curl -L --fail -C - -o Jarvis/jarvis_phase7/runtime/models/Qwen3-8B-Q4_K_M.gguf.part <source URL above>
sha256sum Jarvis/jarvis_phase7/runtime/models/Qwen3-8B-Q4_K_M.gguf.part   # must equal the value above
```

Rename `.part` to the final name only after the hash matches. `serve_jarvis.py` refuses a base GGUF or adapter
whose hash does not match, so a wrong file never loads.

## Training checkpoints (evidence; not needed to run NEXO)

- Main run, steps 800, 1100, 1200, 1300 (best val_loss), 1400, 1500, 1596: `Jarvis/checkpoint-*/`.
  Verify with `cd Jarvis && sha256sum -c CHECKPOINT_SHA256SUMS.txt` (35 files).
- V3 remediation, `Jarvis/v3_train_output/runs/qwen3_8b_jarvis_v3_remediation/`:
  - checkpoint-600 adapter `39f626cdf50a143a55e9b88f4b6711f2c067b5498295f25bcaa86b3a4afe35ae`, trainer_state `4a65b29c8349513356c6f7257f941d6c60c3fe46bcb1d338522ebfa6eb49969a`
  - checkpoint-700 adapter `0bc6c192baab1e84c5e92da389c17ca04c358450189ba264a7a567604ca97ca9`, trainer_state `819f163b6957c77ce58409a97368a44de336d891585d93410763c4b2c4a209c0`
- V3.1 remediation, `Jarvis/jarvis_v31_remediation_train/runs/qwen3_8b_jarvis_v3_1_remediation/`:
  - checkpoint-100 adapter `bf9f4cafcae9088df200434eba7ba28160335677b9af7f1fa02a75c9fe26c2eb`, trainer_state `727455394d44e7be878a870760198fa0c097f9b3d71b6c02a6c2586f9c59e847`
  - checkpoint-139 trainer_state `3b85193fbfde2ec3d73b5984d13855f3e961008ac61039ca3b54cc93bc6525a2`

Also excluded: `Jarvis/**/*.zip` (upload bundles), `runtime/downloads/`, `runtime/src/` (llama.cpp b11570
source), and `qwen_jarvis_phase3_4/v3_build/external/` (third-party datasets, one file is 75 MB).
