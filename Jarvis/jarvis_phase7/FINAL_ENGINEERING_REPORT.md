# NEXO and NEXUS: final engineering report

Date: 2026-10-13. Machine: Windows 11, Python 3.13 (backend venv), Node 24, RTX 3050 Laptop 4 GB, 16 GB RAM.
Earlier reports are unchanged: `PHASE7_REPORT.md` (Phase 7 and 7b) and `../jarvis_phase6_final/` (Phase 6).
Nothing was committed. Every number below comes from a run made in this pass, unless it is marked historical.

## 1. Executive summary

**NEXUS** (`/`, the main app, and `/assistant`, the DeBERTa intent classifier) works. It was checked in a real
browser, with sign-in, route protection and classification.

**NEXO** (`/nexo`, Qwen3-8B with the checkpoint_v31_139 adapter) runs on this laptop and is safe to try:
- nothing with a side effect runs without an approval card;
- the server enforces that approval, single use and per user;
- a failure is reported as a failure.

**The model itself is still weak:**
- It stutters ("CelsiusCelsius", "returned returned").
- It picks tools inconsistently: it calls `web_search` for some phrasings and claims it can't search for others.
- It can alter tool arguments. In a live run it turned a task title's random suffix into `<PASSWORD>`, and the
  approval card showed this before approval.
- Natural-language write requests rarely become approval cards.

The cause of the stutter is narrowed to the adapter running on the Q4_K_M base:
- the base alone does not stutter;
- the adapter's scaling is consistent;
- no sampler setting removes it.

It cannot be fixed on this hardware without retraining or a ≥ 6 GB GPU. Phase 6 acceptance is still FAIL.

**Verdict: B. FUNCTIONAL PROTOTYPE — LIMITATIONS REMAIN.**

## 2. Initial findings (before changes)

| # | Severity | Finding | Reproduced |
|---|---|---|---|
| F1 | High | When an approval got no HTTP answer (network failure or timeout), the card still said **"Approved — the result is below."** The action might or might not have run. A 503 "busy" (proposal not taken) also left the card with no buttons, so a still-valid proposal could not be approved. | yes, page test |
| F2 | Medium | Two Approve clicks in one tick sent **two** confirms (`busy` is render state). The server ran it once, and the second call got 404. That 404 then overwrote the real result with "expired… Nothing was run", a false failure. | yes: with the guard removed the new test sees 2 confirm requests |
| F3 | Medium | A failed tool could be hidden behind a confident model answer. If the search provider was down and the model wrote "Top picks: …", the honesty guard passed it, because it contains no claim verb. | yes, API test |
| F4 | Medium | Search results carried no scope, so a Wikipedia hit list could be presented as "the web". | code read |
| F5 | Low | Search sources were visible only through the model's own words, not as the real retrieved rows. | code read |
| F6 | Low | A failed spoken reply (speech synthesis error) was silent. | code read; page test |
| F7 | Low (tests) | 3 chart-page tests failed in the full frontend run (1,044/1,047). | yes |
| F8 | Low (docs) | README said `start.cmd` starts the model server. It has not since Phase 7b: the model loads on demand. | code read |
| F9 | High (model) | Repetition, inconsistent tool selection, argument corruption, writes rarely proposed (§5, §8). | yes, real model |
| F10 | Medium | There is no Stop for an in-flight generation. A turn is bounded (512 tokens per step, at most 5 steps, 120 s per model call), but it cannot be cancelled. | code read |

Checked and found sound (no change needed):
- `ConfirmationStore.take` is single use under a lock, keyed to the owner, and answers 404 for another user's id.
- `ConfirmRequest` is `extra="forbid"`.
- Writes go through NEXUS routes with the caller's own token.
- Egress after private reads needs approval.
- Path sandboxing works, and `code_run_python`, `db_query`/`db_execute` and `app_send_message` stay disabled with reasons.
- CORS is an explicit origin list.
- `.env` is gitignored.
- The note Markdown is escaped before rendering.
- `npm audit --omit=dev` shows 0 vulnerabilities.

## 3. Fixes completed

| Fix | Root cause | Files | Implementation | Verification |
|---|---|---|---|---|
| F1 honest approval outcome | Every non-404 error was treated as "approved". | `frontend/src/pages/jarvis-page.tsx` | New `unknown` state. No HTTP answer, or a 5xx other than 503, after Approve shows "Approved, but no answer came back, so it is not known whether the action ran… this approval cannot be reused." A refusal before the proposal was taken (503 busy, 4xx) puts the card back to **pending**. | 2 new page tests (dropped connection; 503) |
| F2 one confirm per click burst | The guard read render state. | `jarvis-page.tsx` | `inFlight` ref checked synchronously at the start of `run()`, for both chat and confirm. | New test; mutation check: guard removed → 2 requests, test fails; restored → passes. Live: double-clicked Approve made exactly 1 task (§4.4). |
| F3 failures cannot be hidden | The honesty guard only looked for claim verbs. | `backend/app/jarvis/orchestrator.py` | `_failure_note()`: every tool that ran and failed adds a fixed server sentence `[web_search did not complete.]` to the reply, and so to the spoken text. The reason stays in the trace, which a crisis conversation withholds. Guard value `failure_noted`. Skipped when the guard already replaced the text or the text already says "did not complete". | `test_a_failed_search_cannot_be_hidden_behind_an_invented_answer` |
| F4 search scope | No coverage field. | `backend/app/jarvis/tools.py` | `web_search` data gains `coverage`: "Wikipedia articles only, not the general web" (or "the configured SearXNG instance"). | Tool tests updated. Live: the model said "Wikipedia only, not the web" for an empty search. |
| F5 real sources shown | — | `jarvis-page.tsx` | Under each successful `web_search`: coverage plus the result titles as links. Only `http(s)` URLs are rendered, so a `javascript:` URL is dropped. | Page test, including a `javascript:` row |
| F6 speech failure shown | `speak()` had no `onError`. | `jarvis-page.tsx` | Message: "… The reply is still shown as text." | Page test (synthesis throws) |
| F7 chart test flake | The first test in each chart file paid the cold Vite transform of recharts/d3 inside its own 10 s wait (over 10 s under full parallel load; later tests in the file were warm). Not a product defect. | `frontend/src/test/warm-lazy-charts.ts` (new); 8 chart test files | `beforeAll(warmLazyCharts, 60 s)` pre-imports the 3 lazy chart modules. Each test's wait and assertions are unchanged. | Full suite 1,047/1,047 on 3 consecutive runs (it was 1,044/1,047) |
| F8 docs | Stale text. | `README.md`, `runtime/README.md` | Correct startup description, link to this report, new repetition evidence. | — |

Not changed, deliberately:
- model configuration, adapter, sampler, prompts and the training contract;
- approval architecture;
- tool boundaries.

## 4. Test results (this pass, final code)

### 4.1 Backend

- Full suite: `run_backend_suite_batched.py --batches 12`, 105 files, one process per batch, exit 0 in every batch.
  **4,025 passed, 3 skipped, 14 xfailed, 0 failed.** Evidence: `evidence/final_backend_full_suite.txt`.
- The 3 skips need Windows symlink privilege. The 14 xfails are pre-existing and expected.
- After the last code change (the failure-note wording), these suites were re-run on the final code: all Jarvis suites
  plus `test_documentation_claims` and `test_offline_local_first`. **326 passed, 2 skipped, 0 failed.**
  Evidence: `evidence/final_jarvis_suites_after_last_change.txt`.

Jarvis suites on the changed code, run before the full suite:
- `test_jarvis_api/tools/toolcalls/crisis_guard/workspace_tools`: 281 passed, 2 skipped, 1 failed.
- The failure was the SearXNG contract test, which pins the exact payload and lacked the new `coverage` field. The test was updated to include it, and the targeted re-run passed: 18 passed, 1 skipped.
- The 2 skips need Windows symlink privilege (developer mode).

### 4.2 Frontend

- `vitest run` (70 files): **1,047 passed, 0 failed**, on 3 consecutive full runs after the F7 fix.
  - Before the fix: 1,044 passed, 3 failed (the chart flake).
- NEXO page: 21/21, including 6 new tests.
- `tsc -b`: clean. `eslint`: clean on all changed files.
- Evidence: `evidence/final_frontend_full_suite.txt`.

### 4.3 Security and authorization (automated)

Covered by the backend suite (permissions, RBAC, ownership, Jarvis confirm and crisis guard). The live checks are in §4.4.

### 4.4 Real browser end-to-end

Label: **REAL END-TO-END**. Chrome 154 (headless) driven by playwright-core 1.56.1, which was installed in a scratch
folder, not the repo. The stack was started fresh with `start.cmd`, against the real backend, PostgreSQL and Qwen3-8B.
- Evidence: `evidence/final_browser_e2e*.json`.
- Console logs, authoritative for PASS/FAIL: `evidence/final_browser_e2e*_console.log`.
- Scripts: `final_pass/`.

| Check | Result |
|---|---|
| `start.cmd` fresh start: PostgreSQL check, backend `/health` and `/api/v1/health`, frontend reachable, model OFF | PASS |
| API `/tasks` without a token | PASS (401) |
| `/nexo` signed out redirects to `/login` | PASS |
| Register through the UI, which signs in and lands on `/dashboard` | PASS |
| `/` opens the Command Center | PASS |
| Sign out (account menu), then sign in through the form | PASS |
| `/assistant` (DeBERTa) classifies "show me my open tasks" | PASS: task_manage, 97% |
| `/ml/route` with NEXO off | PASS (200, task_manage 0.970) |
| Chat with the model off | PASS: 503 "NEXO is not available…", no fake answer |
| "Talk to NEXO" disabled while the model is off | PASS |
| Turn NEXO on | PASS: ready in 16 s |
| Real conversational reply | PASS (it answered), with a stutter: "France. France France's capital is Paris." |
| Approval card shows the tool, effect, target, reason and exact JSON before anything runs | PASS for display. **FAIL for fidelity:** the model changed the requested title suffix to `<PASSWORD>`, and the card showed exactly that. |
| Decline in the browser: nothing created; UI says "Declined — nothing was run." | PASS |
| Approving a declined proposal later | PASS (404, nothing created) |
| Another user approving this proposal id | PASS (404, nothing created) |
| Forged id / altered arguments / no token | PASS (404 / 422 / 401, nothing created) |
| Approve double-clicked in the browser | PASS: one task created; one confirm request; UI says "Approved — the result is below." (The script's automatic check printed FAIL because it compared against the requested title, which the model had altered; the listing shows exactly one task.) |
| Same proposal approved again | PASS (404, still one task) |
| Turn NEXO off: llama-server 1 → 0, VRAM 3,545 → 866 MB; DeBERTa still answers | PASS |
| Cleanup: test tasks, projects and both accounts deleted; login afterwards 401 | PASS |
| Launcher tree killed: ports 8000 and 5173 closed, 0 llama-server, PostgreSQL untouched | PASS |
| Backend killed under the launcher: the launcher names it, stops the frontend, both ports close | PASS |

How the write-path checks were phrased:
- The approval-lifecycle checks used the prompt `Please do exactly this: app_create_task {"title": "…"}`, the same
  phrasing Phase 7b used.
- Natural phrasing is in §4.5. With it, the model rarely produces a card.

### 4.5 Model inference (real model, real tools)

Single-turn natural-language requests (API, a fresh conversation each; `final_browser_e2e_2.json`):

| Request | What the model did |
|---|---|
| "Search Wikipedia for Christopher Nolan films." | called `web_search`. Real results (*The Odyssey (2026 film)*, *Christopher Nolan filmography*, …). Answer listed them with Wikipedia links. 68 s. |
| "search for xqzvbnmplk" | called `web_search`, which returned 0 results. Said so honestly, and that it was Wikipedia only. |
| "hey can u search good movies for me" | **no call**: "I can't search the web, but I can suggest ideas…" |
| "Look up the Eiffel Tower on Wikipedia." | **no call**: listed the attached tools and said none applied, which is wrong |
| "What tasks do I have open?" | called `app_list_tasks`, answered accurately (empty list) |
| `Create a task titled "Buy milk".` | approval card (prose → proposal). Declined by the script; nothing ran. |
| "Add a task to buy milk tomorrow" | **no card**: asked "Shall I go on?" in prose. Nothing can run from that. |
| Messaging request (multi-turn) | "I can't send messages. I haven't sent any message" (honest, no call) |
| "Run this Python code: print(2+2)" | "I can't run code" (honest, no call) |

In one long multi-turn browser conversation, the model refused every tool after the first answer: two search
requests and two write requests (`final_browser_e2e.json`). Earlier refusals in the history seem to prime later ones.

Memory, measured live:

| State | VRAM | Free RAM | llama-server processes |
|---|---|---|---|
| model off | 918 MB | 5.2 GB | 0 |
| model ready | 3,588 MB | **0.67 GB** | 1 |
| after unload | 866 MB | 6.2 GB | 0 |

A second load measured 3,545 MB VRAM and 0.94 GB free RAM.

### 4.6 Repetition: controlled comparison

Real model, same server session, `/apply-template` + `/completion` on llama-server: the same path `serve_jarvis.py` uses.

| Configuration | Adjacent repeats (5 or 6 prompts) |
|---|---|
| Shipped: greedy, DRY 0.8, LoRA scale 1 | 7 (run 1), 9 (run 2) |
| Qwen's recommended non-thinking sampling (T 0.7, top-p 0.8, top-k 20) + DRY, seed 1 / seed 2 | 13 / 6 |
| **LoRA scale 0 (base Q4_K_M alone), greedy** | **0**, fluent |

Further checks:
- The adapter's scaling is consistent: r 16, alpha 32, no rsLoRA or DoRA. PEFT applies 2.0, and llama.cpp
  applies alpha/r = 2.0. So a conversion scaling error is ruled out.
- Stutters appear inside words ("CelsiusCelsius", "key-value-value", "indexindex"), and once the system prompt leaked
  into an answer. The probability distribution itself is damaged.

Conclusion: the adapter, trained against the nf4 base, misfits the Q4_K_M base. This is now supported by a direct
base-only control. No sampler change can fix it without hurting quality, so the runtime configuration was not changed.

Evidence: `final_lora_scale_repetition.json`, `final_browser_e2e.json` → `repetition`.

### 4.7 Voice

- **MOCKED (page tests):**
  - automatic send;
  - review mode;
  - spoken reply, then mute;
  - permission denied;
  - unsupported browser;
  - model-off state;
  - new: synthesis failure is shown.
- **REAL END-TO-END:** "Talk to NEXO" is disabled while the model is off.
- **NOT TESTED:** a physical microphone. Headless Chrome on this machine has no microphone, and nobody spoke.
- Transcription is the browser's Web Speech API. Chrome sends the audio to Google and Edge to Microsoft: it is **not
  local**, and the page says so. Speech output is local (OS voices).

## 5. Feature status

| Feature | Status |
|---|---|
| Authentication, registration, sign-in/out, route protection | WORKING AND VERIFIED |
| `/assistant` (DeBERTa) separate from NEXO | WORKING AND VERIFIED |
| Startup (`start.cmd`), health-gated readiness, crash and stop handling | WORKING AND VERIFIED (the interactive Ctrl+C keypress itself was not pressed; the tree kill it performs was) |
| Model load and unload on demand | WORKING AND VERIFIED |
| Conversation | WORKING WITH LIMITATIONS (stutter; quality) |
| Model inference | WORKING WITH LIMITATIONS (not the evaluated configuration) |
| Search | WORKING WITH LIMITATIONS: Wikipedia articles only. The model calls it for some phrasings only. Real results and an honest empty case verified. |
| Approval workflow (decline, approve once, repeat, cross-user, forged, altered) | WORKING AND VERIFIED (server and browser) |
| Supported writes | PARTIALLY VERIFIED: the execution path works; natural-language requests rarely produce a proposal, and the model can alter arguments (always visible on the card) |
| Voice output | PARTIALLY VERIFIED (automated with fakes; a real speaker was not exercised) |
| Voice input | PARTIALLY VERIFIED / physical microphone NOT TESTED |
| Messaging | BLOCKED (no messaging service); the model answered honestly |
| Python execution | BLOCKED (no isolated sandbox); the model answered honestly |
| Raw database access | BLOCKED by policy (bypasses ownership scoping) |
| Phase 6 behavioral acceptance | FAILED (historical, unchanged) |

## 6. Model and artifact integrity

| Artifact | Method | Result |
|---|---|---|
| Qwen checkpoints 800, 1100, 1200, 1300, 1400, 1500, 1596 | `sha256sum -c Jarvis/CHECKPOINT_SHA256SUMS.txt` | 35/35 OK |
| checkpoint_v31_139 adapter | `serve_jarvis.py` hash check at every load (log: `adapter verified: d754a3c6…`) | match |
| LoRA GGUF | sha256 vs `runtime/LORA_GGUF.json` | match (`73300722…`) |
| Base GGUF | `--gguf-sha256` check at load (the server refuses on mismatch, and it loaded) | match |
| DeBERTa classifier (`backend/ml/artifacts/small-model`) | files newer than 2026-10-05: 0; `git status backend/ml backend/app/ml`: clean | unchanged |
| Datasets | not touched by any change in this pass | unchanged |

## 7. Security findings

Confirmed and fixed:
- F1 and F2: misleading approval outcome, and a double-submit that produced a false "nothing was run".
- F3: a failure hidden behind a confident answer.
- The search-source links render only `http(s)` URLs.

Verified live:
- 401 without a token;
- a protected route redirects;
- another user's proposal id gets 404;
- a forged id gets 404;
- altered arguments get 422;
- repeat approval gets 404;
- decline does nothing;
- tool calls run with the caller's own token, through NEXUS routes.

Unresolved risks (accepted for local development, not fixed):

| Risk | Detail |
|---|---|
| Model on/off is shared | Any user with `jarvis.use` can switch the shared model on or off. |
| Process-local state | Pending approvals and crisis memory live in one backend process. |
| No request-body size limit | There is no app-level limit. Pydantic caps the Jarvis fields only after the body has been read. There is a rate limit. |
| Model argument corruption | Shown on the approval card, so the user can decline. It is not prevented. |
| `pip-audit` not run | It is not installed. Backend dependency vulnerabilities were not checked. |
| Prompt injection through search or fetch results | Mitigated, not eliminated: the content is untrusted and every write still needs approval. |

## 8. Remaining blockers

| Blocked | Why | Kind | Needed |
|---|---|---|---|
| Removing the stutter | The adapter is trained for the nf4 base; this GPU cannot run nf4 at 8B. | hardware | ≥ 6 GB VRAM to run the evaluated configuration, or an adapter trained or merged for this GGUF base |
| Phase 6 acceptance (safety 0.88 < 0.90, honesty 0.78 < 0.80) | The model fails it | model / evidence | Retraining and re-evaluation on a GPU (Kaggle quota) |
| Reliable natural-language tool use | Model behavior | model | Training data with natural phrasings |
| General web search | Free options are Wikipedia only. SearXNG needs a host: it does not support Windows natively, and Docker and WSL are excluded. | infrastructure | A SearXNG instance (`JARVIS_WEB_SEARCH_PROVIDER=searxng`, `JARVIS_WEB_SEARCH_URL`) |
| Messaging | No service | infrastructure | A messaging service and credentials |
| Python execution | No isolated sandbox | infrastructure | A container or VM sandbox |
| Cancelling a running generation | `serve_jarvis.py` blocks on llama-server, and the backend does not cancel on client disconnect | software | A streaming generation path with disconnect checks; larger than this pass |
| Physical microphone test | Not available here | permissions | A human with Chrome or Edge and a microphone |

## 9. Launch instructions

```
E:\Nexo\start.cmd
```

1. PostgreSQL must be running on 127.0.0.1:5432.
2. The launcher starts the backend (http://127.0.0.1:8000, docs at /docs) and the frontend (http://localhost:5173), and
   prints them only after their health checks pass.
3. Open http://localhost:5173 and register or sign in.
   - NEXUS main app: `/` (it opens the Command Center).
   - AI Assistant (DeBERTa): `/assistant`.
   - NEXO: `/nexo`.
4. On `/nexo`, press **Turn NEXO on** (about 16 s warm), then chat. Press **Turn NEXO off** to free about 2.7 GB of VRAM and about 5 GB of RAM.
5. Ctrl+C in the launcher stops everything it started. If a service dies, the launcher names it and stops the rest.

Logs: `Jarvis/jarvis_phase7/logs/backend.log`, `frontend.log`, `jarvis-model.log`. Model runtime: `runtime/README.md`.

## 9a. Follow-up audit and cleanup (same day)

Deleted, as approved by the owner (all gitignored and regenerable):
- `.uxsweep`, `.tmpchk`, `report.txt` and `frontend/dist`: about 132 MB;
- 1,757 `__pycache__`, `.pytest_cache` and `.ruff_cache` folders, outside the virtual environments.

Not deleted, by owner decision:
- DeBERTa step checkpoints (13 GB);
- training-only venvs (2.3 GB);
- llama.cpp source (171 MB).

Audit fixes:
- `frontend/src/pages/module-page.tsx` deleted: 121 lines, imported nowhere.
- NEXO page:
  - Send is disabled, with the hint "Turn NEXO on above to start chatting", while the model is known to be off.
    The server still refuses with 503 either way.
  - The conversation scrolls to the newest message.
  - Enter that confirms an IME composition no longer sends.
  - 2 new tests (IME behavior proven by mutation).
- Frontend full suite after these changes: **1,049 passed, 0 failed**. `tsc` and `eslint` clean.
- NEXO write requests:
  - Prose like `` `title`: "Buy milk" `` now becomes an approval card. Before, "Add a task to buy milk tomorrow" gave
    no card at all. The keys must be in the tool's own schema, and conflicting values give no proposal.
  - Every proposal now lists `unverified` text arguments: values not found word for word in the user's message. The
    card warns "Check title: not in your words…", which catches the `<PASSWORD>` and `movedoved.txt` corruption seen
    live. It warns only; it never blocks or changes a value.
  - Tests: Jarvis API 55/55 (4 new); tools, toolcalls and crisis 200 passed, 2 skipped; NEXO page 24/24 (1 new).
- Still open: a Stop button for a running reply (needs a streaming path through `serve_jarvis.py`).

## 10. Final verdict

**B. FUNCTIONAL PROTOTYPE — LIMITATIONS REMAIN.**

What works and is verified:
- the platform, authentication, the DeBERTa assistant and startup;
- NEXO's safety machinery: approval, single use, per-user, and honest failures.

Why it is not A:
- the NEXO model fails Phase 6 acceptance;
- it stutters on this hardware;
- it uses tools inconsistently;
- it can corrupt arguments.

No unresolved critical security issue is known. All changes in this pass are uncommitted.
