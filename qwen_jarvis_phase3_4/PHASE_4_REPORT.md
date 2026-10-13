# Phase 4 report - complete verification

Everything below was executed in this workspace; the one-command summary is `bash scripts/run_all_checks.sh` -> `reports/final_checks.txt`.
Status words: **PASS** (ran and passed), **FAILED -> FIXED** (a real failure was found, fixed and re-run), **PENDING PHASE 5** (needs Kaggle/GPU; not run, not claimed).
**No Qwen3-8B weights were downloaded, no production training ran, no Kaggle GPU was used, and no fine-tuned checkpoint exists.**

## 1. Results at a glance (final run)
| check | result |
|---|---|
| Dataset `v2_final` validation (content checks, duplicates, leakage incl. any shared >=100-char assistant turn / first prompt, code blocks) | PASS - hard failures: none |
| Tool-protocol validator (R1-R10) over 11,288 records + chat-template rendering of 705 tool-bearing records | PASS - 0 violations |
| Tokenizer / schema / render+masking / loader tests | PASS - 10 + 12 + 13 + 9 = 44 |
| Tool-protocol tests | PASS - 21 |
| Trainer, hardening, checkpoint, failure-recovery CLI, data and evaluation tests (pytest) | PASS - 272 passed |
| Trainer safe modes: `--validate-config`, `--dry-run`, `--smoke-test` | PASS (exit 0); production without unlock -> exit 3 as required |
| Evaluation scorer self-test | PASS - oracle 682/682; every adversarial stub <= 1.5% overall (bound 5%), worst suite 9.7% (bound 10%) |
| Kaggle notebook static validation (`--strict`) | PASS - 39 passed, 0 failed, 1 pending (Kaggle execution: **PENDING PHASE 5**) |
| Kaggle notebook gate test (production cells inert; MODE switch) | PASS |
| Security audit (static + runtime) | PASS |
| Reproducibility (5 build steps run twice) | PASS - all identical |
| Git safety | PASS - 0 tracked modifications, `git diff` empty, only the 3 workspaces + the 20 older untracked files |
Total automated tests passing: **337** (44 + 21 + 272), 0 failing.

## 2. Static testing (C1)
Python syntax and imports of all first-party modules (AST parse in `security/audit.py`; every test imports its modules); YAML configs loaded and validated by the config loader; JSON/JSONL validated by the dataset validator and `validate_protocol.py`; the notebook validated structurally (nbformat, empty outputs, compile of every cell, use-before-define, imports vs requirements, CLI flags vs the real `--help`, gate ordering). Type-checking was not run (no type annotations policy in this workspace) - not claimed.

## 3. Dataset and loader tests (C2/C3)
* 44 loader tests: strict/phase-2 schema, think-block convention, labels cover exactly assistant content + `<tool_call>` + closing `<|im_end|>` (property tests on 500-600 real records), padding/collate shapes, batching, truncation reporting.
* Final statistics on the frozen set (max_len 1024): train 10,298 records -> 12,777 samples / 1,867,413 tokens; validation 497 -> 576 / 83,292; test 493 -> 582 / 78,911; longest sample 795 tokens; 0 truncated.
* Independent auditor re-rendered 20 records with `apply_chat_template` and confirmed the label span matches (see section 11).

## 4. Micro training test (C4) - **PIPELINE SMOKE TEST - NOT QWEN PRODUCTION TRAINING**
A tiny random Qwen3-architecture model (16,384 trainable LoRA parameters) with the real tokenizer ran 6 optimiser steps on 65 real training samples: forward pass, loss (~11.9, i.e. ~ln 151,936: a random model, **not a result**), backward, optimizer, evaluation (13 validation samples), checkpoint at step 4, simulated interruption, resume to step 6. PASS. An interrupted-and-resumed run produced adapter weights and validation loss bit-identical to an uninterrupted one (unit test). The 4-bit/bitsandbytes path cannot run on this machine (no CUDA): **NOT TESTED LOCALLY / PENDING PHASE 5**.

## 5. Config dry run (C5)
`--validate-config` and `--dry-run` on the production config: tokenizer + frozen-dataset manifest/hash check + batch shapes + step plan, never building a model (a test monkeypatches the model constructors to raise). Plan (ESTIMATED, 250 tok/s assumption): ~746 steps/epoch, ~1,492 steps, ~3.6 h.

## 6. Checkpoint tests (C6)
Create/save/metadata/reload/resume; missing, corrupt, truncated and hash-mismatched checkpoints refused; incompatible model/dataset/config identity refused unless `--force-resume`; atomic write leaves no partial directory after a simulated disk-full; `trainer_state.pt` loaded with `weights_only` (a tampered file is not executed); retention keeps last 2 + best; pruning by save time (a restored higher-numbered old checkpoint cannot evict new ones); a fresh production run in a non-empty run directory is refused.

## 7. Evaluation tests (C7)
150 `test_eval_*` tests including the loophole suite; deterministic suites (instruction following 62, multi-turn 40, NEXUS facts 128, tool awareness 114, capability boundaries 54 incl. 24 positive controls, safety 59, general 113, held-out test split 168 items derived from the 493-record test split) built reproducibly; reports carry `STUB - NOT A MODEL RESULT` and a real model loads only with `--allow-model-load`. **No result is a measurement of Qwen.**

## 8. Tool-protocol tests (C8)
21 tests: a normal answer yields no tool call; a tool-required example yields a schema-valid `tool_calls` message; results are compact JSON envelopes; final answers are grounded in results; failed tools lead to honest failure reports (no success claims); the converter is byte-deterministic; side-effect tools are previewed (matching tool and arguments) and approved affirmatively before the call; and 13 negative tests prove the validator rejects unknown tools, bad arguments, orphan results, bad envelopes, unconfirmed writes, calls after a refusal, mismatched previews, action claims without a tool result, success claims after an error, and wrongly typed array items. Rendering through the real chat template round-trips every call.

## 9. Security tests (C9) - `security/audit.py`, `security/security_audit.json`
* No `eval/exec/pickle/os.system/shell=True` on dataset text (the only `exec` runs this workspace's own notebook cells under a recorded-subprocess harness; allow-listed with a written justification); `torch.load` only with `weights_only=True`; `trust_remote_code` false everywhere and **rejected by config validation**.
* No `.env` access, no secret-looking strings, notebook has no outputs and prints no secrets; token read only through Kaggle Secrets.
* Importing `train.py` loads no ML library; production refuses (exit 3) with no condition, flag only, flag + config without the environment variable, a wrong environment value, or the environment variable only.
* `git status`: 0 tracked modifications; the 20 older untracked files unchanged (all modified on 2026-10-08); no model weights anywhere in the workspace (the Hugging Face cache holds only 16 MB of Qwen3-8B tokenizer/config files).
* Dataset text is treated only as data: first-party subprocess calls use fixed argument lists; `validate_clean.py` passes extracted code only to syntax-only checks (`python compile`, `node --check`, `gcc/g++ -fsyntax-only`, `javac`).

## 10. Kaggle notebook validation (C10)
Static: 39 checks pass, 0 fail, 1 pending. The notebook (17 cells) is built by `build_notebook.py`: production cells (train, resume, eval, base-vs-fine-tuned compare, loss/perplexity) all sit behind `production_allowed()`; `MODE = "train"|"resume"` prevents a "Run all" from starting a fresh run over restored checkpoints; the install cell prints resolved library versions next to the locally tested ones and runs `pip check`. The zip (64 files, 2.7 MB) was extracted into a clean directory and `--validate-config`, `--dry-run`, the lock refusal and the stub evaluation were run from there. **Kaggle execution: PENDING PHASE 5** - not claimed.

## 11. Failure recovery (C11) - `tests/test_failure_recovery_cli.py` (15 CLI tests)
Missing config, malformed YAML, unsupported override key, invalid value, `trust_remote_code`, a second model id, missing dataset path, dataset hash mismatch, `--resume` with safe modes, resume-latest with no run, every missing production-lock condition, evaluation without `--allow-model-load`, unknown suite, missing data directory, corrupt record: each gives a clear message and a non-zero exit with no traceback and no run started.

## 12. Reproducibility (C12) - `scripts/check_reproducibility.py`
Dataset rebuild (preflight -> conversion -> freeze), evaluation-suite build, data statistics, the Kaggle package zip, and tiny-model smoke training were each run twice: **all identical** (`reports/reproducibility.json`).

## 13. Failures found during Phase 4 and how they were fixed
| # | failure | found by | fix |
|---|---|---|---|
| 1 | 5 records listed the same tool twice in `tools` | Data agent (strict schema) | converter de-duplicates; protocol rule R1 added |
| 2 | 6 exact-duplicate conversations and later cross-split prompt collisions after read-only flows lost their consent round-trip | dataset validator | freeze script deduplicates on messages and re-runs leakage control |
| 3 | `--set train.learning_rate=2e-4` parsed as a string | trainer tests | override parser fixed |
| 4 | five scorer loopholes (empty output, bare refusal, echo, hallucinated grounding, "I've done it") | adversarial stubs | scorers hardened and pinned by tests |
| 5 | notebook evaluation cells used non-existent CLI flags | notebook validation | cells rewritten to `pipeline.eval.cli run/compare/loss` |
| 6 | memory plan section 12 described a stale config (seq 2048, 1200 tok/s) | manual review | section generated from the real config |
| 7 | `--smoke-test --resume X` silently ignored the argument | failure-recovery tests | CLI rejects `--resume` with the safe modes |
| 8 | scanner false positives (`model.eval()`, notebook "Secrets") | security audit | AST-precise rules + a written allow-list |
| 9 | **Independent audit, 5 MAJOR:** emulated bf16 chosen on T4; manifest check skipped on Linux (file-name case); silent fallback renderer; no guard against a run that silently stops learning; 20 held-out records sharing train answers (the earlier "0 leakage" claim was too narrow) | independent auditor | compute-capability check; case-insensitive manifest + hard fail in production; fallback refused outside smoke mode; consecutive-skip abort + NaN guard + adapter-changed check; broadened leakage control (41 records moved to train in the final freeze) |
| 10 | audit minors: non-empty run dir accepted; pruning by step; notebook "Run all" order hazard; validator weaker than documented; constant-hedge scoring 22.8%; 9 unperformed-search claims, 42 mis-numbered lists, template slips; requirements lower-bounds only; config accepted `quant.enabled: false`/flash-attn | independent auditor | all fixed (see `audit/FINAL_AUDIT.md`) |
Two shell-heredoc quoting accidents (mangled regexes) were caught by syntax checks and repaired.

## 14. Final independent audit (C13) - `audit/FINAL_AUDIT.md`
First audit verdict: NOT READY (5 MAJOR). After the fixes above the auditor re-verified: all five MAJOR findings **resolved**, every minor finding fixed or documented, and the verdict became **READY FOR PHASE 5**. The re-audit independently re-ran the tests (316 passed at that moment), the protocol validator and its six bypass records (four now rejected, two were valid records), the notebook validation (39/0/1), the security audit and the git check; re-rendered 20 records to confirm label spans; re-checked the chunked loss against the standard Hugging Face loss on a tiny random Qwen3 (difference ~5e-7, gradients ~3e-8); recomputed the memory-plan arithmetic (8,190,735,360 parameters; 5.656 GiB NF4 weights); and tried 8 production-lock refusal paths (all exit 3; no bypass found by code reading). Non-blocking notes it raised: the R9/R10 validator checks are heuristic (documented); 4 lowercase 'what is how' prompts and 3 held-out turns of 100-149 characters overlapping train; and CPU-only coverage of the new production paths (so Phase 5 starts with a short run).

**Post-audit polish and final state.** The two data notes were then fixed (prompts repaired; long-turn overlap threshold lowered from 150 to 100 characters), which moved 3 more held-out records to train and re-froze the dataset one last time: train 10,298 / validation 497 / test 493. All derived artifacts (token statistics, evaluation suites, memory plan, notebook, package) were rebuilt and the complete check suite was re-run on this final state: all PASS, reproducibility ALL IDENTICAL. The independent auditor's verdict was issued on the immediately preceding freeze (10,295 / 498 / 495); the differences are the minor fixes above, verified by the full check suite, not by a third audit.
