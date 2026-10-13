# FINAL AUDIT - qwen_jarvis_phase3_4 (independent auditor)

Date of audit: 2026-10-09. All evidence below was produced by commands run by the auditor; scratch scripts are in `audit/` (b_check*.py, b_leak.py, b_defects.py, c_check.py, d_bypass.py, d_data.py, e_check.py, f_loss_check.py, g_loophole.py, _sample60.txt, _nexus40.txt, _eval/, _progress_notes.md). QWEN_PHASE5_UNLOCK was never set, no model weights were loaded or downloaded, no GPU/Kaggle was used.

Disclosure: running the requested validators rewrote their own output files inside the workspace (`tool_protocol/protocol_validation.json`, `kaggle/notebook_validation.json`, `runs/smoke*`, `runs/smoke_cli_test`). The zip in `kaggle/dist` is unaffected (only `protocol_validation.json` now differs from its PACKAGE_MANIFEST hash). No code or dataset file was edited.

## Verdict table

| | area | verdict |
|---|---|---|
| A | Git / isolation | PASS |
| B | Dataset | FAIL (held-out contains train answers, contradicts the preflight claim; finding 4) |
| C | NEXUS claims | PASS |
| D | Tool protocol | PASS for the data; validator weaker than documented (finding 9) |
| E | Tokenizer / template / loader | PASS |
| F | Training engine | FAIL (findings 1, 3, 5; lock, loss, checkpointing themselves PASS) |
| G | Evaluation | PASS (scorer loophole, finding 8) |
| H | Kaggle package | FAIL (finding 2); gating, inert defaults, no secrets, no weights, no outputs: PASS |
| I | Security | PASS |
| J | Honesty of claims | FAIL (minor overstatements and one contradicted claim; findings 4, 11) |
| K | Phase 5 blockers | NOT VERIFIABLE LOCALLY (GPU/Kaggle parts); locally detectable blockers are findings 1, 2, 3, 5, 6, 7, 12 |

## Findings (ordered by severity)

No BLOCKER-class defect (file corruption, lock bypass, secret leak) was found. There are 5 MAJOR findings that would degrade or silently invalidate the production run and must be fixed before Phase 5.

### MAJOR

**1. `precision: auto` selects emulated bf16 on a T4 (and P100), not fp16.**
Evidence: `pipeline/train/train.py:70` `return "bf16" if torch.cuda.is_bf16_supported() else "fp16"`. The installed torch source (2.14.1) is `def is_bf16_supported(including_emulation: bool = True)` and returns True for any device whose bf16 tensor can be created, even when compute capability < 8 (`_check_bf16_tensor_supported`). On Kaggle T4 (cc 7.5) it therefore returns True, so the run uses bf16 autocast with no GradScaler, `bnb_4bit_compute_dtype=bfloat16` and `dtype=bfloat16` weights (train.py:113-115). T4 has no bf16 tensor cores: expect large slowdowns, invalidating the 2.6-7.8 h estimate and the fp16 path the design documents. The same call is in `pipeline/train/repro.py:45` and in notebook cell 2 (prints "use bf16" on a T4). `docs/TRAINING_DESIGN.md:28` says "bf16 on Ampere+".
Fix: `torch.cuda.get_device_capability()[0] >= 8` or `is_bf16_supported(including_emulation=False)` in train.py, repro.py and build_notebook.py; add a unit test with a monkeypatched capability (7, 5).

**2. The frozen-dataset integrity check is silently skipped on Linux (Kaggle) because the packaged manifest is renamed.**
Evidence: `unzip -l kaggle/dist/qwen_jarvis_kaggle_package.zip` lists `dataset/v2_final/manifest.json` (lower case); `kaggle/build_package.py:81` adds `"manifest.json"` (matches `MANIFEST.json` only on case-insensitive Windows); `pipeline/train/train.py:164` looks for `MANIFEST.json`, and `check_manifest` returns `{"manifest": None, "manifest_sha256": None}` when absent (train.py:165-166). On Kaggle this disables the dataset-version and train/validation SHA-256 match and removes `dataset_manifest_sha256` from every checkpoint identity. Existing mitigation: the notebook verifies PACKAGE_MANIFEST.json hashes after extraction. The Windows-only test suite cannot catch this.
Fix: package it as `MANIFEST.json`, make `check_manifest` accept both names, raise in production mode if none is found, add a case-sensitive test.

**3. Silent fallback to a different data renderer.**
Evidence: `pipeline/train/data_adapter.py:19-27` `_try_data_agent()` swallows any exception while importing `pipeline.data.dataset` and sets `SOURCE="fallback"`; the fallback (`_fallback_render`, lines 62-87) trains on the final assistant turn only, ignores `train_on`/`validate`, and truncates mid-turn at max_len. `cmd_production` (train.py:487-503) never checks `SOURCE`. Any import problem on Kaggle (missing dependency, different library version) would silently train on about 12% of the intended turns with different masking; the exit code does not reveal it (only `loader: fallback` inside the dataset info).
Fix: in `cmd_production` and `cmd_dry_run` require `D.SOURCE == "pipeline.data"`; ideally remove the fallback outside smoke mode.

**4. Held-out splits contain training answers; the preflight "0 leakage" claim is false.**
Evidence (audit/b_leak.py plus an inline check): 20 validation/test records (11 val, 9 test; general_knowledge and nexus_knowledge) contain an assistant turn >150 characters identical (normalised) to a train answer, usually under a paraphrased prompt ("Tell me about the Silk Road." vs "Explain the Silk Road to me."); 3 with the identical prompt. 20 held-out records have a first user prompt equal to a train first prompt. `preflight/PREFLIGHT_REPORT.md` (Validation of v2_final) states "0 train/validation/test leakage (identical or near-identical prompts, identical long answers)". Magnitude is about 2% of held-out records; the 24/26 last-turn prompt matches are context-dependent follow-ups ("What's on it now?") with different answers and are acceptable.
Impact: validation loss (early stopping, best-checkpoint choice) and the testsplit eval are slightly optimistic.
Fix: move/drop the 20 held-out records (new dataset version, since the current one is "FROZEN"), correct the report sentence.

**5. No guard against a run that silently stops learning (fp16 overflow / repeated non-finite gradients).**
Evidence: train.py:366-384. With a GradScaler a non-finite gradient is skipped, `self.step += 1` still happens, the LR scheduler is not advanced, and the loop continues without limit. The project itself lists Qwen fp16 overflow as an untested risk. If the forward pass yields inf/NaN every step (not just the first scale-calibration steps) the run burns the quota, evaluates a never-updated adapter and reports completion. NaN loss is only logged.
Fix: abort after N consecutive skipped steps (for example 20), abort on non-finite loss before backward, record `skipped_steps` in checkpoint meta, print tokens/s for the first steps.

### MINOR

**6. A fresh production run in a non-empty run directory is not refused, and pruning is keyed on step number.** `checkpoint.prune` keeps the newest `keep_last` by step. If `runs/<run_name>` already holds `checkpoint-1000` (restored earlier session) and a run starts without `--resume`, new low-step checkpoints are deleted immediately unless they are the best. Fix: refuse a non-empty run dir without `--resume` (code reading; production cannot be started here).

**7. Notebook cell-order hazard after a killed session.** Cell 10 (train) and cell 11 (resume) share the same gate; "Run all" in a restarted session runs cell 10 (step 0) before cell 11. No `MODE = "train" | "resume"` switch. Fix: add the switch.

**8. Eval scorer loophole: constant non-answers score well.** `audit/g_loophole.py`: one fixed string combining limitation, "planned, not built", refusal, alternative, confirmation question and crisis resources scores 152/666 (22.8%) overall, 30/30 (100%) on capability_boundaries, 31/59 safety, 32/114 tool_awareness, 44/120 testsplit. capability_boundaries has no control items where acting is correct, so an over-refusing fine-tune would look perfect there. Fix: add positive control items and a combined-hedge stub with an upper bound to `selftest`; read Phase 5 deltas together with the benign look-alike safety items.

**9. `validate_protocol.py` is weaker than TOOL_PROTOCOL.md claims.** `audit/d_bypass.py` builds six records the validator accepts with no errors: a write call after the user answered "No, do not do that!"; a preview for `fs_write_file` followed by a call to `fs_delete_file` with other args (R5 only tests the substring "Proposed call:"); "I ran git log and the output was ..." with tools attached and no tool message (rule 4 not enforced); a final answer contradicting an `ok:false` envelope; array item types unchecked. The shipped data is clean on these properties by independent scan (`audit/d_data.py`: 84 side-effect calls, 0 after a refusal-looking turn, 0 preview/call mismatch, 0 unsupported action claims), so the gap is latent. Fix: compare preview with call name/args, require an affirmative user turn, add an action-claim heuristic, add these cases to test_protocol.py.

**10. Dataset content defects (small).** (a) 9 train `reasoning` records say "A search of n = 1 to 2000 finds no counterexample" (an unperformed action, against the dataset's own rule); (b) 42 `jarvis_assistant` answers (37 train, 3 test, 2 val) show a numbered list starting at "2." with step 1 folded into the intro; (c) 7 prompts "What is how bridges carry loads?" (template slip); (d) odd generated tool query `web_search(query="price the S&P 500")`; (e) "No, I don't have access to your spreadsheet, so I can't access it." redundancy. Otherwise 60 + 40 randomly read records had correct arithmetic and consistent multi-turn state; all 440 Python and 106 JSON blocks parse. Fix: patch in the next dataset version.

**11. Documentation inconsistencies / overstatements.** `TRAINING_DESIGN.md` says "~45% of tokens supervised" and "1.6-1.8 M tokens per epoch"; `reports/data_stats_train_final.md` (reproduced: 12717 samples, max 795) says 38.7% and 1,861,819. `PREFLIGHT_REPORT.md` A4 lists tool_aware 1,007 vs 1,002 in MANIFEST.json. `MEMORY_PLAN.md` says the notebook "prints the measured tokens/s"; it does not (the trainer logs `tokens` and `t` only). External facts labelled VERIFIED (for example "NF4 supported on Pascal, HF bitsandbytes docs table") cannot be verified offline; treat as ASSUMED. Positive: all memory/time numbers are labelled ESTIMATED; no measured-looking GPU numbers and no claim that a model was trained.

**12. Dependency and config risks for Kaggle.** `kaggle/requirements_kaggle.txt` has only lower bounds (`transformers>=4.51.0`, `peft>=0.15.0`, `bitsandbytes>=0.45.0`): pip may resolve versions never tested here (all tests ran on CPU with transformers 5.19.0, peft 0.21.2, trl 1.15.0, torch 2.14.1), and the `dtype=` / `torch_dtype=` fallback (train.py:121-123) for transformers 4.51-4.55 was never exercised. `validate_config` does not reject in production mode `quant.enabled: false` (bf16 8B will OOM), `attn_implementation: flash_attention_2` (unsupported on T4/P100) or a non-official `tokenizer_path`. P100 (sm_60) support in current default CUDA PyTorch wheels is doubtful (NOT VERIFIABLE LOCALLY); prefer T4. Fix: pin the exact versions after a Kaggle install check, run `pip check`, add the config validations, log the tokenizer/chat-template hash and compare with `_hf_meta` (verified today: the HF-cache copy of the `Qwen/Qwen3-8B` tokenizer/config files has identical sha256 to `_hf_meta` for tokenizer_config.json, tokenizer.json, vocab.json, merges.txt, config.json; the cache holds no weights, 16 MB).

### NOTE

13. The unlock is a fixed, documented string plus two switches: it prevents accidents, not determined misuse (acceptable). `production_locked` is fail-closed for 0/"no"/null.
14. Time-sensitive statements are baked into training data (for example `qj2f-788dd4106086`: "No Qwen weights have been downloaded, no training has run"); true today, false after Phase 5; drop or regenerate in a v3 dataset.
15. `--smoke-test` cannot be run from the packaged zip (`_hf_meta` not packaged); the package's `--validate-config`, `--dry-run` and production refusal work from an extracted copy.
16. `--dry-run` on the production config calls `AutoTokenizer.from_pretrained("Qwen/Qwen3-8B")` (tokenizer files only, a network call; it worked offline here because the files were already cached).
17. The notebook has no `pipeline.eval.cli loss` cell (assistant-token loss/perplexity base vs fine-tuned) although the CLI supports it.

## Section details

### A. Git/isolation - PASS
`git status --porcelain` = 23 lines: 3 workspace dirs + exactly 20 untracked files (generate_dataset.py, qwen.txt, trim_and_validate.py, scripts/dedup.py, gen_examples.py, gen_part1.py, validate.py, write_a2a_batch3.py, write_batch1-5, write_fs_batch1-5, write_fs_final, write_fs_unique); `git diff --name-only` = 0. mtimes of the 20 files: 2026-10-08 19:30 to 20:41. Files modified since 2026-10-08 06:00 outside the three workspaces (excluding .git/.venv/node_modules): only those same 20 files. Status still 23 after all my runs.

### B. Dataset - FAIL (finding 4; otherwise sound)
train 10257 / val 516 / test 515; counts and sha256 equal MANIFEST.json; total 11288; multi-turn 1413; all 12 categories present in val (24-75) and test (22-74); schema/roles 0 bad; 0 duplicate ids; 0 exact duplicate conversations; 440 Python + 106 JSON blocks valid. Last-turn prompt equality vs train: val 24, test 26 (Jaccard >= 0.7: 26 / 30), all context-dependent follow-ups with different answers. Long identical answers across splits: 11 val + 9 test (finding 4).

### C. NEXUS claims - PASS
Ground truth PHASE_1_REPORT.md. 40 random nexus_knowledge records read; every assistant sentence in all 11288 records mentioning Ollama / Qwen / Whisper / tool registry / chat endpoint / LLM / terminal / file access without a negation or future cue (84 sentences) is a denial, a plan or a refusal. Figures (0.974 synthetic, 75% = 42/56 hand-written, 20-turn history, 14 classes) match the Phase 1 report; global search, Command Center, risk evidence and ML_MAX_INPUT_CHARS exist in source. No violation.

### D. Tool protocol - PASS (data) / finding 9 (validator)
`test_protocol.py` 14 tests OK; `validate_protocol.py dataset\v2_final --render`: 11288 records, 261 tool calls, 705 rendered with the real chat template, 0 violations. Independent checks in finding 9.

### E. Tokenizer/template/loader - PASS
test_tokenizer 10/10, test_data_loader 9/9, test_data_render 13/13, test_data_schema 12/12. Independent render of 20 random train records (6 with tool calls) via `apply_chat_template(add_generation_prompt=True, enable_thinking=False)`: labels cover exactly [len(prompt), end) = content + `<tool_call>` blocks + one closing `<|im_end|>`; no user/system/tool text, `<think>`, `<tool_response>` or `<|im_start|>` is labelled; the prompt ends with the empty think block. Whole train split re-rendered at max_len 1024: 12717 samples, 0 dropped, max 795 tokens (equals reports/data_stats_train_final.md). No dataset message contains literal control tokens.

### F. Training engine - FAIL (findings 1, 3, 5); lock/loss/checkpoint PASS
93 tests passed (checkpoint, train_common, train_config, lock_dryrun, smoke, failure_recovery_cli). `--validate-config`, `--dry-run` (model_loaded false) and `--smoke-test` (tiny random Qwen3, interrupt + resume) pass. Refusals attempted (env unset or wrong, never satisfied): no flags; `--confirm-production`; `--set production_locked=false`; both; `production_locked=0`; `=no`; `--set mode=smoke` + flag + unlocked; `--resume`: all exit 3 REFUSED listing the missing conditions; `--dry-run --resume` exit 2. `check_production_unlock` runs before tokenizer/data/model in `cmd_production`; smoke mode requires `local_tiny` and builds from a random config with a local tokenizer dir; `load_production_model` re-checks `unlocked`; `trust_remote_code=true` is rejected by validation. No bypass found by code reading (aliases, `--set`, env variants, import side effects).
Loss: `audit/f_loss_check.py` (tiny random Qwen3, LOSS_CHUNK=7, right padding, prompt masks and holes): chunked 6.23316002 vs HF 6.23315954; max gradient difference 3e-8 over 25 tensors; eval path identical; two micro-batches normalised by the group token count sum to the joint loss. Config coherent (LoRA targets exist in Qwen3; r16/alpha32; LR 1e-4 cosine; effective batch 16; 1590 steps). Checkpoints atomic, hashed, `torch.load(weights_only=True)`, identity-checked, deterministic order from (seed, epoch).

### G. Evaluation - PASS (finding 8)
127 eval tests pass. `selftest`: oracle 666/666, adversarial stubs 0-2.6%. Base-vs-finetuned compare with stubs works; eval_results.json, eval_report.md and comparison.md carry `STUB — NOT A MODEL RESULT`. `run --model Qwen/Qwen3-8B` without `--allow-model-load` exits "refusing to load a model"; `loss` likewise; HFGenerator imports torch lazily; oracle text is never given to generators.

### H. Kaggle package - FAIL only because of finding 2
`validate_notebook.py --strict`: 26 passed, 0 failed, 1 pending (Kaggle execution, labelled PENDING PHASE 5). `test_notebook_gate.py`: 3 scenarios PASS, 0 unsafe launches. Notebook: no outputs, no execution counts, GPU/internet metadata off, production cells tagged and gated by `production_allowed()` (PHASE5_AUTHORIZED, CONFIRM_PRODUCTION, human-set env var, Kaggle, CUDA, package); nothing sets the env var; eval cells call `pipeline.eval.cli run/compare` with `--allow-model-load` inside the gate; HF token read only inside `load_hub_auth()`, child output redacted. Zip (sha256 dbb3157534af...) extracted to a scratch dir: 62 files, 12 MB, no .env, weights, keys or tokens (only "password =" hits are code samples in train.jsonl); PACKAGE_MANIFEST hashes match the workspace except the re-written protocol_validation.json. From the extracted copy `--validate-config` and `--dry-run` pass and production is refused with exit 3. requirements has `transformers>=4.51.0`. MEMORY_PLAN arithmetic recomputed from `_hf_meta/config.json`: 8,190,735,360 parameters; per-layer linear 192,937,984; NF4 linear weights 3.23 GiB + embed/lm_head/norms fp16 2.32 GiB + double-quant absmax 0.10 GiB = 5.656 GiB (5.958 without double quant); LoRA r16 = 43,646,976 params; AdamW state 0.65 GiB: all equal `kaggle/memory_estimates.json`. Peak-memory, tokens/s and hours are labelled ESTIMATED; Kaggle limits "verify at Phase 5".

### I. Security - PASS
`security/audit.py`: no issues; tracked_modifications 0; production refusal exit 3. My review: no `eval`/`exec`/`pickle`/`shell=True`/`os.system` on dataset text (the only `exec` is `kaggle/test_notebook_gate.py` running the notebook's own cells under stubs); `torch.load(..., weights_only=True)` only; `trust_remote_code` False everywhere and validated; no network at import time (`from_pretrained` only inside functions/gated paths; data/render.py uses `local_files_only=True`); notebook redacts token patterns; reproducibility snapshot records no environment variables. Tests wrote nothing outside the workspace.

### J. Honesty of claims - FAIL (minor)
No claim that a model was trained or evaluated; no measured-looking GPU numbers; estimates labelled; Kaggle execution consistently PENDING. Problems: the "0 identical long answers" claim (finding 4); documented fp16/bf16 handling not matching code (finding 1); items in finding 11.

### K. Phase 5 blockers
Must fix first: 1, 2, 3, 5. Should fix: 6, 7, 12, 4, 17. Not verifiable locally: bitsandbytes 4-bit on T4/P100 with Kaggle's torch, real fp16 stability of Qwen3-8B, peak VRAM (12.5 GiB estimated at micro-batch 4; OOM ladder documented), real tokens/s, Kaggle disk/quota/session limits, `dtype=` kwarg on older transformers, `save_pretrained` of a 4-bit PEFT model on GPU. Missing for Phase 5: tokenizer/chat-template hash comparison against `_hf_meta` at run time, tokens/s print in the first steps, post-training check that adapter weights changed.

FIRST-PASS VERDICT (superseded by the re-audit below): NOT READY FOR PHASE 5 (fix MAJOR findings 1-5 first: a T4 would be auto-selected into emulated bf16; the dataset manifest check is silently disabled on Linux; a silent fallback renderer can replace the real data pipeline; a run can silently stop learning on repeated non-finite gradients; held-out splits contain 20 train-identical answers contradicting the preflight claim. Lock, loss, masking, checkpointing, eval and package gating are otherwise sound.)


---

# RE-AUDIT (second pass, from scratch, after the fixes)

Method: re-ran my own scripts (audit/b_check.py, b_check2.py, b_leak.py, d_data.py, d_bypass.py, e_check.py, f_loss_check.py, g_loophole.py) against the new files, plus `pytest tests` (316 passed), `test_protocol.py` (21 OK), `validate_notebook.py --strict` (39 passed, 0 failed, 1 pending = Kaggle execution), `security/audit.py` (PASS), and read the new train.py / repro.py / checkpoint.py / validate_protocol.py / notebook code. Fresh unzip of the new package into a scratch dir. QWEN_PHASE5_UNLOCK was never set; no weights loaded; git status still 23 entries (3 workspaces + 20 old files), 0 tracked modifications.

## Status of the original findings

| # | finding | status | evidence |
|---|---|---|---|
| 1 | emulated bf16 chosen on T4 | RESOLVED | `train.py:70` now `repro.bf16_native()` = `get_device_capability()[0] >= 8`; no remaining `is_bf16_supported()` decision (only printed for information in repro.py:56 and notebook cell 2); validator checks the notebook cell |
| 2 | manifest skipped on Linux | RESOLVED | `unzip -l`: `dataset/v2_final/MANIFEST.json`; `find_manifest` matches case-insensitively; production raises if missing; version/sha256 mismatch raises; package PACKAGE_MANIFEST (63 files) matches the zip contents and the workspace files exactly (script check, 0 differences) |
| 3 | silent fallback renderer | RESOLVED | `require_primary_loader` called in `cmd_production` and dry-run for production configs; dry-run from the extracted zip reports `loader: pipeline.data` |
| 4 | held-out contains train answers | RESOLVED (with a small residual, see N2) | new split 10,295 / 498 / 495, counts+sha256 equal MANIFEST; my check: first-user-prompt equality train vs val/test = 0/0; identical assistant turns >=150 chars = 0/0 (b_check2, b_leak); exact duplicate conversations 0, duplicate ids 0; all 12 categories still present in val and test; multi-turn 1413, total 11,288. PREFLIGHT/PHASE_3_REPORT now states the earlier claim was too narrow |
| 5 | no guard against non-finite steps | RESOLVED | `_register_skip` + `NonFiniteError` after `max_consecutive_skipped_steps` (20); non-finite loss without scaler aborts before backward; `skipped_steps` stored in loop state, checkpoint metrics; adapter-unchanged check (`lora_B_norm`) fails the run; `tok_per_s` and `first_steps_throughput` logged. Covered by passing tests (NaN injection) |
| 6 | fresh run in non-empty dir / pruning by step | RESOLVED | `ensure_fresh_run_dir` called when no `--resume`; `list_checkpoints` orders by `saved_ns`; tests pass |
| 7 | notebook run-all hazard | RESOLVED | gate cell has `MODE = "train" | "resume"`; train cell refuses when checkpoints exist; gate test (MODE logic) PASS; notebook has 0 outputs, 0 execution counts, GPU/internet metadata off, PHASE5_AUTHORIZED/CONFIRM_PRODUCTION False |
| 8 | eval constant-hedge loophole | RESOLVED | my kitchen-sink string now 0/682 overall (was 152/666; capability_boundaries 0/54, was 30/30); selftest: oracle 682/682, stubs <=1.5% overall; 24 positive-control items added; stubs still stamped NOT A MODEL RESULT |
| 9 | validator gaps | MOSTLY RESOLVED | d_bypass.py: declined_then_called, preview_mismatch, fabricated_result, write_with_stale_preview now rejected (R5/R9); "yes... actually no, don't" and differing preview args also rejected; 0 violations on the shipped data (11,288 records, 705 rendered). Residual gaps: see N1 |
| 10 | dataset content defects | MOSTLY RESOLVED | "A search of n..." claims 0 (was 9); numbered-list-from-2 template 1 left (was 42; the one left looks like a legitimate "45." list item); "What is how ..." prompts 4 left (was 7), see N2 |
| 11 | doc inconsistencies | RESOLVED | TRAINING_DESIGN / MEMORY_PLAN / PHASE_3_REPORT figures now agree with the re-frozen data (12,772 train samples, 1,866,981 tokens, max 795 - I re-rendered the whole train split and got 12,772 / 0 dropped / 795); all GPU numbers still labelled ESTIMATED |
| 12 | dependency / config risks | MOSTLY RESOLVED | requirements now bounded (`transformers>=4.51.0,<6`, `peft<1`, `bitsandbytes<1`, ...; still ranges, not exact pins); config validation now rejects `trust_remote_code`, `quant.enabled=false`, `flash_attention_2`, non-official tokenizer in production (verified with --validate-config); tokenizer fingerprint (chat template, vocab, special tokens) compared with `_hf_meta` or `configs/tokenizer_fingerprint.json`, which is in the zip and equal to my own fingerprint of `_hf_meta`; extracted-zip dry-run reports `match: true`. P100 support remains NOT VERIFIABLE LOCALLY |
| 13-16 | notes | unchanged / acceptable | lock is an intent guard; 8 refusal paths again all exit 3 and `--dry-run --resume` exits 2 |
| 17 | no loss cell | RESOLVED | five gated production cells (train, resume, eval, compare, loss) |

Unchanged and re-confirmed OK: lock behaviour (refusals exit 3), chunked-loss equivalence (loss diff 5e-7, grad diff 3e-8, accumulation-sum exact), label masking on 20 fresh random records (0 bad turns), tool-call labels, 0 control tokens in data, eval testsplit ids all exist in the new test split (168/168), zip has no .env/weights/keys, security audit PASS, `scripts/reports/final_checks.txt` agrees with what I re-ran.

## New findings from the re-audit (none blocks Phase 5)

**N1 (MINOR). Validator R9/R10 are heuristics with easy gaps.** Records the validator still accepts (tested): final answer "The file contains: hello world." after an `ok:false` not_found result (R10 only fires on explicit success words); "I went ahead and executed the script; it printed 42." with no tool message (R9 verbs list); approval "Sure, what does it do first?" followed by the call (any affirmative word counts). The shipped data is clean on these by my independent scan, so this is latent only. Fix: extend verb/claim patterns, require the approval turn not to contain a question mark.

**N2 (MINOR). Small dataset residuals.** 4 prompts still read "What is how ..." (bridges, thermostats, nutrition label, vaccines); with a 100-character threshold 1 validation and 2 test records still share an identical assistant turn with train (the freeze threshold is 150 characters, so these are short answers, not a violation of the stated rule). Fix: patch in the next dataset version.

**N3 (NOTE). Not exercised locally.** The new production-only code paths (`ensure_fresh_run_dir`, tokenizer check on a hub download, `NonFiniteError` abort, adapter-updated check on a real 4-bit PEFT model, throughput logging) are covered only by CPU unit tests with a tiny model; behaviour with bitsandbytes, fp16 GradScaler and the real Qwen3-8B on a T4 remains NOT VERIFIABLE LOCALLY. First Phase 5 step should be a short run (e.g. `--set train.max_steps=20`) to read the first-step throughput, skipped-step count and peak memory before committing the full quota.

**Checked for regressions and found none:** re-freeze consumers are consistent (MANIFEST hashes = files = zip = PACKAGE_MANIFEST; data_stats_train_final shows 10,295 records / 12,772 samples; MEMORY_PLAN uses 1,866,981 tokens and ~798 steps/epoch; eval testsplit built from the new test file, 495 records, 168 kept); the three repaired regexes in `validate_protocol.py` (AFFIRM, NEGATE, PREVIEW, ACTION_CLAIM, SUCCESS_CLAIM) behave sensibly on my probes and produce 0 false positives on 11,288 records; train.py edits did not change the loss math (my independent numeric check is unchanged).

Verdict table update: A PASS, B PASS, C PASS, D PASS, E PASS, F PASS, G PASS, H PASS, I PASS, J PASS, K NOT VERIFIABLE LOCALLY for GPU/Kaggle items only (bitsandbytes 4-bit on T4, fp16 stability, real memory and tokens/s, P100 support, Kaggle quotas); all locally detectable blockers are resolved.

AUDITOR VERDICT: READY FOR PHASE 5
