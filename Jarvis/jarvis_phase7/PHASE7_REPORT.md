# Phase 6 remediation and Phase 7 NEXO (Jarvis) integration: final report

Date: 2026-10-13. Branch: `phase7-jarvis-integration`. HEAD: `5a8346c` (unchanged). **Nothing was committed.**

> **Phase 7b update (2026-10-13, later the same day)** is §12 at the end. It adds:
> - on-demand model loading;
> - automatic voice submission;
> - 13 new tools;
> - the repetition investigation;
> - a second cleanup;
> - fresh test and live-model evidence.
>
> Where §12 and an earlier section disagree, **§12 is current**. In particular:
> - `start.cmd` no longer loads the model (§10);
> - the tool count is now 34 (§4).

The internal code name stays "Jarvis": the `JARVIS_*` settings, `/api/v1/jarvis/*` and `backend/app/jarvis/`. Users see the assistant as **NEXO**.

## Final status

| # | Component | Status | Evidence |
|---|---|---|---|
| 1 | Phase 6 historical model acceptance | **FAIL** (unchanged) | v31_139: safety 46/59 = 0.780 (v1), 52/59 = 0.881 (v2.2); capability honesty 42/54 = 0.778 (`V31_FINAL_ACCEPTANCE.json`) |
| 2 | Phase 6 local remediation engineering | **PASS** (local scope) | §1; retraining and re-evaluation **NOT DONE** (no GPU quota) |
| 3 | AI Assistant (`/assistant`, DeBERTa) | **PASS** | identity restored; 217/217 assistant, layout and smoke tests |
| 4 | NEXO Chat (`/nexo`, Qwen3-8B) | **PASS** as a development prototype | real generations through the backend, `evidence/e2e_real_model_final.json` |
| 5 | NEXO voice feature | **PASS (automated)** / **NOT TESTED by a human with a real microphone** | page tests with browser API fakes; no human run |
| 6 | Local Qwen3-8B inference | **PASS** (quantized runtime, not the evaluated configuration) | `evidence/real_generation_*`, `runtime/README.md` |
| 7 | Tool approval workflow | **PASS** | backend and UI tests; live approve, decline and repeat-confirm |
| 8 | Required tools | **17 of 21 implemented, 4 BLOCKED** | §4, `TOOL_INVENTORY.md` |
| 9 | Backend regression suite | **PASS**: 3,968 passed, 3 skipped, 14 xfailed, 0 failed; Jarvis and affected suites re-run on the final code | §6 |
| 10 | Frontend regression suite | **PASS with 1 load-flaky test**: 1,038/1,039 in the full run; the failing chart test passes 15/15 alone; tsc and eslint clean | `evidence/frontend_full_suite_final.txt` |
| 11 | Security verification | **PASS** for implemented controls; accepted limits listed | §5 |
| 12 | Cleanup | **PASS** | §8 |
| 13 | End-to-end testing | **PASS through the real API and the real model**; **NOT TESTED by clicking in a browser** | §3 |
| 14 | Production readiness | **NOT READY** | §9 |

## 1. Phase 6 (historical, unchanged)

All historical reports, scores, thresholds and scorers are unchanged. Detail is in `jarvis_phase6_final/reports/PHASE6_LOCAL_REMEDIATION_REPORT.md`.

**Mandatory criteria** (safety ≥ 0.90, capability honesty ≥ 0.80): not met by any checkpoint.

**The figures in the task brief belong to checkpoint_v3_700.** These are v3_700's figures: 44/54, 34/35, 78/91, 45/59, 44/52. checkpoint_v31_139's actual figures:

| Metric | checkpoint_v31_139 |
|---|---|
| Format | 15/16 |
| P11 JSON | 6/6 |
| Capability honesty | 42/54 |
| Tool-call schema | 35/35 |
| Tool selection | 77/91 |
| NEXUS knowledge | 17/25 |
| Safety | 46/59 |
| Over-refusal controls | 43/52 |

**Local remediation done:**
- **Crisis-data audit.** Three V3.1 replay targets teach "In the US … 988" with no stated country. A correction candidate exists, `v3_1_1_crisis_fix_candidate`, and has **not** been trained.
- **Scorer v2.2:** re-executed locally, 21/21 tests pass, every re-scored row identical to the stored results.
- **Runtime crisis guard:** added (§5).

A guard that works does not make the model pass Phase 6.

## 2. Architecture

```
/assistant  AI Assistant: microsoft/deberta-v3-base intent classifier (unchanged; voice/typed routing, Phase 12)
/nexo       NEXO: browser ─► POST /api/v1/jarvis/chat ─► orchestrator ─► serve_jarvis.py :8765 ─► llama-server :8766
                                    │                                       (Qwen3-8B Q4_K_M + checkpoint_v31_139 LoRA)
                                    ├─ strict <tool_call> parser ─► registry/schema/availability check
                                    ├─ prose proposal ─► schema check ─► approval card (never executed from prose)
                                    ├─ read tool ─► execute ─► result back to the model
                                    ├─ side-effect tool ─► confirmation_required ─► POST /jarvis/confirm (single use, same user)
                                    └─ honesty guard ─► crisis guard (always last) ─► response
```

**Two separate systems.** NEXO never routes a conversation through DeBERTa as if it were generative. Chat only reads the classifier's prediction, as a diagnostic.

**Classifier unchanged.** `backend/ml/artifacts/small-model/final` still has its 2026-10-04 files, and `git status backend/app/ml backend/ml` is empty.

## 3. Real model runtime and end-to-end results

### Runtime

| Item | Detail |
|---|---|
| Base | `Qwen3-8B-Q4_K_M.gguf` from Qwen/Qwen3-8B-GGUF@`7c41481f`; sha256 `d98cdcbd…5745785` = HF LFS |
| Adapter | checkpoint_v31_139 (`d754a3c6…`, unmodified), converted with llama.cpp b11570 `convert_lora_to_gguf.py` to `jarvis_v31_139_lora.gguf` (`73300722…`) |
| Conversion checks | Exit 0. 504 tensors = 36 layers × 7 modules × A/B. Every target exists in the base. `LORA_GGUF.json` ties both hashes together. |
| Runtime | llama.cpp b11570, CUDA 12.4, 16 GPU layers, 8k context, q8_0 KV cache. `/lora-adapters` confirms the adapter at scale 1. |
| Speed | About 87 prompt tokens/s and 9.6 generated tokens/s |
| Memory (all components running) | llama-server about 3.6 GB RAM and 3.45/4 GB VRAM; backend about 1.8 GB; about 0.6 GB RAM left |
| Template | llama-server renders the template embedded in the GGUF. It is byte-identical to HF `apply_chat_template` in 4/4 cases (`evidence/template_parity_result.json`), which saved about 1 GB. |
| Differs from Phase 6 | Q4_K_M base instead of nf4; q8_0 KV cache; DRY repetition sampler; at most 512 tokens per reply. **Outputs are not the evaluated outputs.** |

### Real-model end-to-end results (through `/api/v1/jarvis/chat` and `/confirm`, real model, real DB)

| Workflow | Result |
|---|---|
| A. Text | Answers as NEXO (identity turn, 6.8 s). |
| C. "hey can u search good movies for me" | The model called `web_search(query="good movies")`, which **executed** against live Wikipedia (10 results). The answer listed the real titles. |
| D. Read | `app_list_tasks` executed and the answer matched the DB ("Buy milk", "Renew passport"). |
| E. Write | The model wrote the call in prose. It became an approval card for `app_create_task {"title": "Buy milk"}`. The DB was unchanged before Approve, the task existed after it, and a repeat confirm returned **404**. |
| F. Decline | Outcome `denied_by_user`; the DB was unchanged. |
| G. Failure | Approving `app_update_task("Walk the dragon")` returned `tool_failed` ("The task wasn't found, so nothing was changed"). No false success. |
| H. Crisis, unknown country | Guard result "pass"; no numbers. |
| H. Crisis, forged "I'm in the UK" history | The model produced numbers; the guard **replaced** them (`country_not_established`, `unapproved_number`). |

### Defects found with the real model, and what was done

| # | Defect | Action |
|---|---|---|
| 1 | NEXO said it couldn't search. `web_search` had no provider, so the tool was never offered to the model. | Fixed: Wikipedia provider (§4). |
| 2 | The model claimed "I've called app_create_task … the task was created" with no call made, and the honesty guard let it through. | Fixed: the guard now catches "called / invoked / used" and passive outcome claims about the user's things. Tested. |
| 3 | Write tools never executed, because the model writes side-effect calls in prose. | Fixed: unambiguous, schema-valid prose calls become approval cards, and are never run from prose. 5 tests, verified live. |
| 4 | Generations hit the 120 s timeout (HTTP 502) through repetition loops. | Fixed: replies capped at 512 tokens; DRY sampler added. |
| 5 | Word repetition ("returned returned returned"). | **Not fixed.** This runtime only: 0 of 423 Phase 6 evaluation outputs show it. It disappears at LoRA scale 0.5, so it is caused by the nf4-trained adapter running on a Q4_K_M base. Changing the scale would alter the checkpoint, so it was **not** done (`evidence/decoding_repetition_experiment.json`). |
| 6 | Quality. | Search answers repeat the raw titles, and Wikipedia keyword relevance is weak. Model honesty is still 0.778 by evaluation. |

## 4. Tools (21 trained, `toolproto-1`)

`TOOL_INVENTORY.md` has the full matrix: schema, implementation, permissions, approval, tests and blockers.

| Status | Tools |
|---|---|
| **REAL, run directly** (read) | `app_list_tasks`, `app_read_calendar`, `db_describe`, `fs_read_file`, `fs_search`, `web_fetch`, `web_search` |
| **REAL, approval required** | `app_create_task`, `app_update_task`, `app_create_event`, `app_update_event`, `app_update_goal`, `fs_write_file`, `fs_move_file`, `fs_delete_file`, `http_request`, `shell_run` |
| **BLOCKED** | `app_send_message` (no messaging service), `code_run_python` (no isolated sandbox), `db_query` and `db_execute` (by policy: they bypass owner scoping) |

**Configuration notes:**
- `fs_*` needs `JARVIS_WORKSPACE_DIR`; `start.cmd` sets `Jarvis/jarvis_phase7/workspace`.
- `shell_run` needs `JARVIS_SHELL_ALLOWLIST`; it is off by default.
- `web_fetch` and `web_search` also need approval once private data has been read in the same turn.
- `app_create_task` works only when the user has exactly one project; the contract has no project argument.

**Search provider:**
- `JARVIS_WEB_SEARCH_PROVIDER=wikipedia`: the free, keyless MediaWiki API. It searches Wikipedia articles only.
- Wikimedia answers 403 unless the User-Agent carries a contact (`JARVIS_WEB_SEARCH_CONTACT`).
- For general web search, run SearXNG and set `JARVIS_WEB_SEARCH_PROVIDER=searxng` and `JARVIS_WEB_SEARCH_URL`. No paid service was enabled.

**Real versus stub testing:**
- **Real:** filesystem, subprocess, local HTTP server, a live `example.com` fetch, live Wikipedia, Postgres and every NEXUS route.
- **Stub:** the model in unit tests (scripted), and SearXNG (contract only).

## 5. Security and guardrails

**Fixed in this phase (all with tests):**
- **DNS rebinding.** `web_fetch` and `http_request` connect to the IP that was checked, with `Host` and TLS SNI kept, so certificates are still verified. Redirects are re-checked; private, loopback, link-local and metadata addresses are refused. A live HTTPS fetch through the pin returned 200.
- **Crisis country provenance.** A country is established only from a user statement in a request the server itself received; forged client history cannot unlock numbers. A disclosure is remembered server-side for 1 h, so trimmed history keeps the guard active.
- **Confirm requests are `extra="forbid"`.** The client approves an id, never an action, so altered arguments return 422 and nothing runs. An unknown, expired or used id returns 404: "Nothing was run".
- **Honesty guard extended** (defect 2).
- **Prose proposals:** only one named, available, approval-required tool with schema-valid arguments qualifies.

**Kept from the earlier review:**
- Credential and device-name file denials, and symlink-escape checks.
- `shell_run`: no shell interpreter, an allowlist, interpreters refused, a minimal environment.
- `RecursionError` → `invalid_json`.
- Per-user cap on pending approvals.
- Egress after reading private data needs approval.
- Tool data withheld in crisis conversations.
- `httpx` guarded imports, so NEXUS boots without it.

**Accepted and documented:**
- Pending approvals live in process memory, by design, so chat transcripts are not persisted. They are lost on restart and fail closed (404), with the UI showing "expired, already used, or server restarted. Nothing was run". This requires a single backend process (`run.py`).
- Server-side crisis and country memory is per process too.
- Browser speech recognition (Chrome, Edge) sends audio to Google or Microsoft. The UI says so (`RECOGNITION_PRIVACY_NOTICE`).

## 6. Tests

**Backend full suite, final run 3:** 12 sequential batches, one process each (`run_backend_suite_batched.py`). Run 2 was killed at about 66% when the system ran out of memory, and is not counted.
- Batches 1–7: 2,359 passed, 3 skipped.
- Batches 8–12: 1,609 passed, 14 xfailed.
- **Total: 3,968 passed, 3 skipped, 14 xfailed, 0 failed.** Evidence: `evidence/full_suite_run3_batched.txt`.

**Changed code re-run on the final code** (`evidence/final_affected_backend_suites.txt`):
- Suites: all Jarvis suites, offline/local-first, ML capabilities, documentation claims, permissions, RBAC and config.
- First run: 369 passed, 2 skipped, 1 failed.
- The failure: the README env table lacked the 2 new settings. That was a real documentation gap, now fixed; the docs and config suites then passed 23/23.

**Current Jarvis inventory** (`pytest --collect-only`): crisis guard 60, parser 20, tools 109, API 51.

**Skipped tests:** all 3 need symlink privilege, which Windows grants only in developer mode:
- `test_developer_git.py:1306`;
- `test_jarvis_tools.py:229` and `:290`.

**Frontend:**
- 70 files, 1,039 tests: 1,038 passed, 1 failed.
- The failure is the developer-dashboard lazy chart, which timed out after 12 s under parallel load. It passes 15/15 alone. The same flake was seen before these changes, in a page these changes don't touch.
- `tsc -b` and `eslint src` are clean.
- NEXO page: 13 tests, covering voice states, transcript → the same chat request, talk-back, mute, permission denied, unsupported browser, approval explanation, decline, expiry and status.

## 7. NEXO UI (frontend)

**NEXO Chat (`/nexo`):**
- The model status comes from the server.
- The tool list is grouped as "run directly", "asks before running" and "unavailable (reason)", and explains what approval means.
- Confirmation cards show the action, what it does, the target, why approval is needed, the exact arguments, and Approve and Decline buttons. They report approved, declined and expired outcomes.

**Voice — "Talk to NEXO":**
- One utterance per click, using the existing browser recogniser (`features/assistant/speech-recognition.ts`). There is no second voice stack.
- The transcript goes into the message box for review; Send uses the same `/jarvis/chat` path as typing, so all guards apply.
- Using voice turns on spoken replies (`speechSynthesis`). It reads only the guarded server reply, and the speaker button mutes it and stops speech.
- States shown: ready, listening, processing, speaking, permission denied (with instructions for enabling the microphone), unsupported browser, microphone unavailable, recognition unavailable, model unavailable and error. Empty speech shows "I didn't catch any speech".

**AI Assistant (`/assistant`):**
- My earlier "NEXO Voice" relabel was reverted to the committed original.
- Its user-facing self-references now say "the AI Assistant", so the two systems never share a name.
- Its original microphone is the Phase 12 DeBERTa voice-routing feature (commit `afdb71d`), so it was **kept**.

`/jarvis` redirects to `/nexo`.

## 8. Cleanup

**Removed:** 35 entries, 531.2 MB, every one recorded in `CLEANUP_MANIFEST.json` with its hash and authoritative copy.
- 34 entries from the first pass.
- 1 in this session: a temporary evidence backup whose bytes were the exact start of the main evidence file.

**Re-verified after cleanup:**
- No removed path exists.
- Every authoritative copy is present.
- Original checkpoints: 35/35 OK.
- v31_139 adapter is still `d754a3c6…`; v3_700 adapter still `0bc6c192…`.
- V2, V3 and V3.1 each match their manifest (3/3 files).
- No historical result was modified.

**Kept on purpose:** `runtime/downloads/` (the digest-verified original zips), the source tarball, every dataset, checkpoint, report and scorer, and all evidence.

## 9. Production readiness: NOT READY

The model fails Phase 6 acceptance (safety, honesty).

Writes depend on the prose-to-approval-card path, because the model does not reliably emit structured calls for side effects.

Quantized-runtime repetition is visible to users.

Memory headroom is about 0.6 GB on this machine.

Pending approvals and crisis memory are process-local.

Real messaging and code-sandbox tools are blocked, and search covers Wikipedia only.

Voice has not been exercised by a human with a real microphone.

## 10. How to run

```
E:\Nexo\start.cmd
```

The launcher starts, in order:
1. The model server: verifies the adapter and the GGUF hashes, then loads them.
2. The backend: NEXO enabled; Wikipedia search; a sandbox workspace.
3. The frontend.

It checks every health endpoint and prints the URLs; Ctrl+C stops only what it started. `start.cmd --no-jarvis` starts NEXUS without NEXO.

| What | URL |
|---|---|
| NEXUS | http://localhost:5173 (sign in or register) |
| NEXO Chat | http://localhost:5173/nexo |
| AI Assistant | http://localhost:5173/assistant |
| Backend | http://127.0.0.1:8000 (API docs at /docs) |
| Model server | http://127.0.0.1:8765 (started by the launcher) |

**Prerequisites:**
- PostgreSQL running on 127.0.0.1:5432.
- Close large applications for RAM headroom.
- Use Chrome or Edge for voice input.

## 11. Remaining blockers

| Blocker | Unblocks |
|---|---|
| GPU quota (Kaggle refreshes 2026-10-17) | Retraining with the crisis-fix candidate, and re-evaluation |
| A messaging service plus credentials | `app_send_message` |
| An isolated sandbox (container or VM) | `code_run_python` |
| A SearXNG instance, or an authorized paid search API | General web search |
| ≥ 6 GB VRAM | Running the evaluated nf4 configuration, which would remove the repetition artifact |

## 12. Phase 7b: on-demand model, voice, new tools, live verification

All of this is uncommitted work on `phase7-jarvis-integration`.

### 12.1 Status

| # | Component | Status | Evidence |
|---|---|---|---|
| 1 | Phase 6 historical acceptance | **FAIL**, unchanged, nothing re-scored | Re-read from `jarvis_phase6_final/results/kaggle_run_v31/V31_FINAL_ACCEPTANCE.json`: safety v1 46/59 = 0.780 and v2 52/59 = 0.881 (both fail ≥ 0.90); honesty 42/54 = 0.778 (fails ≥ 0.80); schema 35/35; format 15/16; P11 6/6. Selection 77/91 and knowledge 17/25 are from §1 and were not re-read in this phase. |
| 2 | Two separate AIs | **PASS** | DeBERTa `/ml/route` answered `task_manage` (0.986) with NEXO **off**, and again after unload (`evidence/e2e_writes.json`). `git status backend/app/ml backend/ml` is empty; classifier files are still dated 2026-10-04. |
| 3 | On-demand load / unload / idle unload | **PASS (live, real model)** | §12.2 |
| 4 | Text chat with real Qwen3-8B | **PASS** as a development prototype | `evidence/e2e_on_demand.json` |
| 5 | Voice: automatic send and spoken reply | **PASS in automated page tests**; **NOT verified with a physical microphone** | §12.3 |
| 6 | Repetition ("returned returned") | **NOT FIXED**; root cause narrowed (§12.4) | `evidence/repetition_followup_*.json` |
| 7 | Tools | **34 registered: 27 available, 7 unavailable** with stated reasons | §12.5 |
| 8 | Writes through approval (real model, real DB) | **PASS** for task create, decline, repeat-protection, file write and file move; the model failed to produce update-task and create-event calls (nothing ran) | §12.6 |
| 9 | Backend affected suites | **PASS: 413 passed, 2 skipped, 0 failed** (§12.8) | `evidence/final_affected_backend_suites_phase7b.txt` |
| 10 | Frontend full suite | **PASS: 1,042/1,042, 70 files** (2 workers; the earlier chart timeout did not recur) | `evidence/frontend_full_suite_phase7b.txt` |
| 11 | Cleanup | **PASS**: a further 662 MB removed, all verified | §12.7 |
| 12 | Production readiness | **NOT READY** | §12.9 |

### 12.2 On-demand model loading

`start.cmd` no longer loads Qwen3-8B. It hands the backend the exact model-server command (`JARVIS_MODEL_LAUNCH_ARGV`), and the backend's `app/jarvis/model_manager.py` owns that single process.

**Endpoints** (`jarvis.use` permission):
- `POST /api/v1/jarvis/model/load`
- `POST /api/v1/jarvis/model/unload`
- `GET /api/v1/jarvis/status`, which now carries `lifecycle`.

**States:** off, loading, ready, busy (ready with a turn in flight), unloading, error and external.

**Loading:**
- Refused with a clear reason if the config is missing, a runtime file is missing, free RAM is under 3,000 MB, or free VRAM is under 2,600 MB.
- A second load while loading or ready starts nothing.
- A server already answering that NEXUS did not start is reported as `external` and is **never stopped** by NEXUS.
- Readiness means a real `/health` that names the expected adapter `d754a3c6…`.

**Unloading:**
- New turns are refused (503), and running turns get up to 30 s to finish.
- Then only the owned process tree is killed (`taskkill /T /F /PID <own pid>`).
- The manager then checks that the process exited, that the port no longer answers, and records GPU memory before and after.

**Idle unload:** after `JARVIS_IDLE_UNLOAD_MINUTES` (default 10; 0 disables) with no chat or confirm turn. Time during a running turn does not count.

**Live, real model** (`evidence/e2e_on_demand.json`, `e2e_writes.json`):

| Step | Result |
|---|---|
| NEXUS started, model off | VRAM 1,139 MB, 0 llama-server processes; chat → 503 "NEXO is not available" |
| Load (second call during loading) | state `loading`, no second server |
| Ready | 14 s (warm file cache), VRAM 3,688 MB, 1 llama-server |
| Unload | process exited, server no longer answers, VRAM 3,671 → 946 MB, 0 llama-server; chat → 503; NEXUS `/health` 200 |
| Second cycle | ready in 14 s; unload VRAM 3,668 → 944 MB |

Tests (`tests/test_jarvis_workspace_tools.py`) use a real child process: a stub HTTP server, not the model.
- Load only on request, then unload stops it.
- A server NEXUS did not start is never stopped.
- Missing config is refused.
- Turns are blocked while unloading.
- Idle unload fires, and not during a running turn.

**Limits:**
- Any user with `jarvis.use` can switch the shared model on or off.
- State is per backend process, like pending approvals.
- If the backend is killed without the launcher, the model process can outlive it. The launcher's Ctrl+C stops the whole tree.

### 12.3 Voice (NEXO page only; the AI Assistant page is untouched)

**Automatic send is now the default.** When the utterance ends, the final transcript goes through the same `submit()` → `POST /jarvis/chat` path as typed text, so authorization, tool validation, approvals and the crisis guard all apply. No Send press is needed.

**Review mode:** "Send automatically when I stop speaking" is a checkbox. Turned off, the transcript lands in the message box for review.

**Spoken replies:**
- Using voice switches on spoken replies.
- Only the server's guarded reply text is spoken.
- The speaker toggle mutes it, and "Stop speaking" cancels playback.
- Fixed in this phase: spoken-reply state is now read through a ref. Before, a reply arriving after an `await` could read a stale "off".

**States shown:**
- ready, listening (with "sent when you stop speaking"), transcribing (interim text), and "Waiting for NEXO…" (sending or generating);
- speaking, permission denied (with how to allow it), unsupported browser, and microphone unavailable;
- "Off — turn the NEXO model on first" (the model is off: the mic is disabled, so nothing can be answered by a placeholder), and error.

**Privacy:** recording starts only on a button press and stops after one utterance. Recognition is the browser's Web Speech API, which sends audio to Google (Chrome) or Microsoft (Edge). It is **not local**, and the page says so.

**Tests:** 16/16 NEXO page tests, including automatic send without pressing Send, review mode, spoken reply then mute, permission denied, unsupported browser, and the model switch. They use browser-API fakes.

**Not done:** a human speaking into a physical microphone in Chrome or Edge. Manual check:
1. `start.cmd`, then sign in.
2. Open `/nexo`, then press "Turn NEXO on" and wait for "ready".
3. Press "Talk to NEXO", allow the microphone, and speak.
4. The reply should appear without pressing Send, and be read aloud.

### 12.4 Repetition: what was tested

| Experiment (same prompts, greedy) | Result |
|---|---|
| Current config (Q4_K_M base, f16 LoRA at scale 1, q8_0 KV, DRY 0.8) | "returned returned returned returned", "boils boils", "between between between" |
| f16 KV cache instead of q8_0 | the same stutters, so the **KV cache is not the cause** |
| + `repeat_penalty` 1.3 over the last 8 tokens | adjacent repeats mostly gone, but replaced by garbled text ("Water boils at sea level boils boiling point is…", "Cult filmilm") |
| LoRA scale 0.5 (Phase 7a) | gone, but that changes the checkpoint |

**Cause:** the adapter was trained against the nf4 base, and here it runs on the Q4_K_M base. The sampler only masks it, at a quality cost, so **no sampler change was adopted**, and the config is unchanged.

**User-visible impact,** seen live in this phase:
- A tool **argument** stuttered: `fs_move_file` destination `e2e/movedoved.txt`.
- The approval card showed the exact argument, so the user could have declined. It was approved in the test and moved the file to that name.
- The following delete of `e2e/moved.txt` honestly failed with not found.

**Fix:** needs either ≥ 6 GB VRAM to run the evaluated nf4 configuration, or an adapter trained or merged for this base. Neither was done: no retraining, no Kaggle GPU.

### 12.5 Tool inventory (live `/jarvis/status`, launcher configuration)

**34 registered: the 21 trained `toolproto-1` tools plus 13 new ones.** The model **never saw the 13 new names**, so its use of them is unmeasured. Live, it did call `calculate` and `workspace_list_files` correctly.

| State | Tools |
|---|---|
| Run directly (15) | `app_list_tasks`, `app_read_calendar`, `db_describe`, `fs_read_file`, `fs_search`, `web_fetch`, `web_search`, `workspace_list_files`, `workspace_file_metadata`, `workspace_compare_files`, `code_search_symbols`, `git_inspect`, `json_yaml_validate`, `csv_analyze`, `calculate` |
| Approval required (12) | `app_create_task`, `app_update_task`, `app_create_event`, `app_update_event`, `app_update_goal`, `fs_write_file`, `fs_move_file`, `fs_delete_file`, `http_request`, `workspace_create_file`, `workspace_backup_file`, `code_apply_patch` |
| Unavailable (7) | `app_send_message`: NEXUS has no messaging service, and none is configured anywhere (no SMTP or webhook setting exists). `code_run_python`: no isolated sandbox; WSL is not installed and Docker is absent, so it stays blocked by policy. `db_query` and `db_execute`: blocked by policy, because they bypass ownership. `shell_run`: no allowlist configured (opt-in). `run_project_tests` and `run_project_lint`: no approved commands configured (`JARVIS_PROJECT_COMMANDS`, opt-in). |

**The 20 requested capabilities:** 13 are new handlers, 2 are existing equivalents, 4 are model abilities, and 1 is blocked.

| # | Requested | Implementation | Tested |
|---|---|---|---|
| 1 | workspace_list_files | **new** | unit (real fs); **live with real model** |
| 2 | workspace_search_files | existing `fs_search` (name and content search, same sandbox) | existing tests |
| 3 | workspace_read_file | existing `fs_read_file` | existing tests |
| 4 | workspace_file_metadata | **new** (size, extension, times, sha256) | unit |
| 5 | workspace_compare_files | **new** (unified diff) | unit |
| 6 | workspace_create_file | **new**: create-only (`open("x")`), never overwrites; approval | unit |
| 7 | code_search_symbols | **new** (definitions and references across 14 languages) | unit |
| 8 | code_explain | model ability over `fs_read_file`; no tool, because a tool would be a fake wrapper | — |
| 9 | code_review_diff | model ability over `git_inspect` diff and `workspace_compare_files` | — |
| 10 | code_generate_patch | model ability (text only; nothing applied) | — |
| 11 | code_apply_patch | **new**: `git apply --check` then `git apply`; every path goes through the workspace sandbox; binary and symlink patches are refused; git is stopped from finding the outer repo (`GIT_CEILING_DIRECTORIES`); approval | unit (apply, stale patch, `..` escape) |
| 12 | run_project_tests | **new**: only the operator-configured argv, no shell, workspace cwd, 600 s; approval | unit (real exit codes 0 and 3) |
| 13 | run_project_lint | **new**, same model | unit |
| 14 | git_inspect | **new**: status, log, diff (summary, or one file) and branch only; no reset or checkout; folder diffs and credential names refused | unit (real repo; nothing discarded) |
| 15 | json_yaml_validate | **new**: syntax, with line and column (no schema validation) | unit |
| 16 | csv_analyze | **new**: rows, missing values, duplicates, numeric min, max and mean, top values, ragged rows | unit |
| 17 | workspace_backup_file | **new**: copies to `.nexo_backups/`, verified by sha256; approval | unit |
| 18 | calculate | **new**: AST-whitelisted arithmetic, unit conversion (length, mass, volume, time, data, temperature), date add and difference | unit (7 values, 6 unsafe inputs refused); **live with real model** |
| 19 | structured_planner | model ability; no tool | — |
| 20 | code_run_python | **BLOCKED**: no sandbox on this host | status reason |

**Security of the new tools:**
- Paths go through the same `_inside()` sandbox: relative only, no `..`, no drive, stream or device paths, no credential-like names, no symlink escape.
- The read tools join the private-data set, so web egress after them needs approval.
- Subprocesses use argv with no shell and a minimal environment.

**Search, unchanged:**
- The Wikipedia provider (MediaWiki API) is the only one configured. It searches **Wikipedia articles only**, not the general web.
- No SearXNG instance exists on this machine, and none was set up. No public SearXNG endpoint was trusted, and no paid API was enabled.
- Live, "Find good movies for me" was answered **without** calling `web_search`: the model chose not to search. Phase 7a recorded a live search for "hey can u search good movies for me".

### 12.6 Writes through approval (real model, real DB: `evidence/e2e_writes.json`)

| Case | Result |
|---|---|
| `app_create_task` → Approve | executed; the task existed in the DB afterwards |
| The same proposal confirmed again | **404**, nothing ran twice |
| `app_create_task` → Decline | `denied_by_user`; DB unchanged |
| `fs_write_file` → Approve | file written (15 bytes) |
| `fs_move_file` → Approve | moved, to the stuttered name the card showed (§12.4) |
| `fs_delete_file` (`moved.txt`) → Approve | `tool_failed`, not found; **no false success** |
| `app_update_task` and `app_create_event` requests | the model wrote stuttering prose with no arguments, so there was no proposal and nothing ran (honest, but the feature failed for this input) |
| First run (`e2e_writes_run1.json`), user with no project | `app_create_task` failed honestly: the contract needs exactly one project |

Phase 7a covered altered arguments: confirm is `extra="forbid"`, and a changed request returns 422. That is unchanged and tested in `test_jarvis_api.py`.

**Test data left behind:** the user `nexo.e2e@example.com`, its project "NEXO e2e project" and the task "NEXO e2e check". The workspace test files were removed.

### 12.7 Cleanup (`CLEANUP_MANIFEST.json` → `removed_later`)

**Removed: 662 MB in 3 files**, the archives in `runtime/downloads/`:
- `llama-b11570-bin-win-cuda-12.4-x64.zip`, sha256 `7e8cfe94…`
- `cudart-llama-bin-win-cuda-12.4-x64.zip`, sha256 `8c79a9b2…`
- `llama.cpp-b11570-src.tar.gz`, sha256 `940114c9…`

**How they were verified:**
- Each of the 3,824 members was hashed and found identical to the extracted copies in `runtime/llama.cpp/` and `runtime/src/`.
- Both zip digests equal the GitHub release digests.
- They can be re-downloaded; the digests are recorded in the manifest and in `runtime/README.md`.

**Scanned and kept:**
- An exact-duplicate scan of every file of 20 MB or more under `Jarvis/` and `qwen_jarvis_phase*` found **0 duplicate groups** (34 files, 12.5 GB).
- Everything else was kept: all checkpoints, datasets, reports, scorers, logs and evidence.
- `__pycache__` (186 MB, regenerable) was **not** removed; it is not a project artefact worth the risk of a bulk delete.

**Integrity after cleanup:**
- `CHECKPOINT_SHA256SUMS.txt`: 35/35 OK (checkpoints 800, 1100, 1200, 1300, 1400, 1500 and 1596).
- checkpoint_v31_139 adapter `d754a3c6…`; checkpoint_v3_700 adapter `0bc6c192…`.
- Base GGUF `d98cdcbd…`; LoRA GGUF `73300722…`.
- V2, V3, V3.1 and the crisis-fix candidate manifests are present.

### 12.8 Tests in this phase

| Suite | Result |
|---|---|
| Backend: Jarvis API, crisis guard, tool calls, tools, new workspace tools and manager; docs, config, permissions, RBAC, offline and ML capabilities | **413 passed, 2 skipped, 0 failed** (`evidence/final_affected_backend_suites_phase7b.txt`). The 2 skips need symlink privilege. The first run had 2 real failures in guard tests, both fixed deliberately. `test_ml_capabilities`: the route count rose 194 → 196, with a comment. `test_offline_local_first`: the process-launch scan now has an exact, documented exemption for `model_manager.py` and `workspace_tools.py`, with a stale-exemption check. |
| Frontend full (`vitest --maxWorkers=2`) | 1,042 passed, 0 failed, 70 files |
| Frontend `tsc -b`, `eslint` (changed files) | clean |
| Backend `ruff check` and `ruff format` (changed files) | clean |
| Live real-model scripts | `e2e_on_demand.py`, `e2e_writes.py` (re-runnable) |

The full backend suite (about 4,000 tests) was **not** re-run. Only `app/jarvis`, the Jarvis API, config and docs changed, and their suites plus the permission and RBAC suites were run.

### 12.9 Production readiness: NOT READY

All of §9 still applies, plus:
- The model stutters inside tool arguments.
- It does not reliably produce update and event calls.
- The 13 new tools are untrained for the model.
- Model on/off is shared by all users with `jarvis.use`.

### 12.10 How to run (current)

```
E:\Nexo\start.cmd
```

1. PostgreSQL must be running on 127.0.0.1:5432.
2. The launcher starts the backend and the frontend; **the model stays off**.
3. Open http://localhost:5173/nexo and sign in.
4. Press **Turn NEXO on**. Loading takes about 15 s with a warm cache and a few minutes cold.
5. Chat by typing, or press **Talk to NEXO** in Chrome or Edge.
6. Press **Turn NEXO off** to free the GPU. It also unloads by itself after 10 minutes idle.
7. The AI Assistant (http://localhost:5173/assistant, DeBERTa) works whether NEXO is on or off.
8. Ctrl+C in the launcher window stops everything it started, including a loaded model. The model server is a child of the backend, so the launcher's tree kill reaches it.
   - Verified by killing the launcher's process tree with the model loaded: llama-server went 1 → 0, ports 8000, 5173, 8765 and 8766 were closed, and VRAM fell to 938 MB.
   - The interactive Ctrl+C keypress itself was not exercised.

| Option | Effect |
|---|---|
| `start.cmd --no-jarvis` | NEXUS without NEXO |
| `JARVIS_IDLE_UNLOAD_MINUTES=0` | never idle-unload |
| `JARVIS_PROJECT_COMMANDS` | enables the project test and lint tools |
| `JARVIS_SHELL_ALLOWLIST` | enables `shell_run` |
