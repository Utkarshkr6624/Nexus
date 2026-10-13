# Jarvis tool inventory and coverage matrix (Phase 7)

## Final status (2026-10-12, verified against the code and with the real model)

**21 trained tools. 17 implemented against real resources, 4 blocked.**

### Changes since the first version of this inventory

`web_search` is now **REAL**:
- Provider: `JARVIS_WEB_SEARCH_PROVIDER=wikipedia`, the free and keyless MediaWiki search API. `start.cmd` sets it by default.
- The real model called it live for "hey can u search good movies for me": 10 Wikipedia results, with real titles and URLs, in `evidence/e2e_real_model_final.json`.
- It searches **Wikipedia only**. For general web search, run a SearXNG instance and set `JARVIS_WEB_SEARCH_URL` (with `JARVIS_WEB_SEARCH_PROVIDER=searxng`).
- Wikimedia's robot policy requires a contact in the User-Agent (`JARVIS_WEB_SEARCH_CONTACT`); without one the API answers 403.

Configuration-dependent availability: tools that are implemented but **off unless configured**:
- `fs_read_file`, `fs_search`, `fs_write_file`, `fs_move_file` and `fs_delete_file` need `JARVIS_WORKSPACE_DIR`. `start.cmd` sets it to `Jarvis/jarvis_phase7/workspace`.
- `shell_run` needs `JARVIS_SHELL_ALLOWLIST`. It is empty by default and the launcher leaves it off on purpose.

Blocked, unchanged:

| Tool | What is needed |
|---|---|
| `app_send_message` | A messaging service in NEXUS. None exists; an adapter needs a real provider and credentials. |
| `code_run_python` | An isolated sandbox (container or VM). None exists on this host (no Docker or WSL), and arbitrary code must never run in-process. |
| `db_query`, `db_execute` | Blocked by policy: raw SQL bypasses per-user ownership scoping. NEXUS data stays reachable through the `app_*` tools. |

### Real-model behaviour (checkpoint_v31_139 on the local Q4_K_M runtime)

- **Read tools** (`app_list_tasks`, `web_search`): structured calls, executed for real.
- **Write tools**: the model writes the call in prose. NEXO turns an unambiguous, schema-valid prose call into an approval card (`_proposal_from_prose`). The user approves the exact arguments shown; only then does it run.
  - Verified live: "Buy milk" was created only after Approve.
  - A decline left the data unchanged.
  - A repeat confirm returned 404.
  - A failure ("Walk the dragon" not found) was reported as a failure.

## Sources inspected

- `qwen.txt`:
  - §8, the conceptual "tool-first" capability list;
  - §9 and §10, the honesty and multi-tool rules;
  - Phase 7 task list: registry, executor, permissions, NEXUS adapters, filesystem, terminal, browser, web, code and Git tools.
- `qwen_jarvis_phase1/QWEN_ARCHITECTURE_PLAN.md` and `QWEN_INTEGRATION_PLAN.md`: tool-layer design, propose/confirm, allowlists, "off by default".
- `qwen_jarvis_phase3_4/tool_protocol/TOOL_PROTOCOL.md` and `tool_schemas.json` (`toolproto-1`, sha `0d6a2498…`): the **21 tools the model was trained and evaluated on**. This is the identical content of `pipeline/eval/data/tool_schemas.snapshot.json`.
- NEXUS backend: there was no tool registry before this phase. The existing REST services and `app/ml/actions` (propose/confirm `ActionSpec`) are the de-facto NEXUS capabilities.

## The rule applied

The registry (`backend/app/jarvis/tools.py`) contains exactly the 21 `toolproto-1` tools, with their trained names, descriptions and schemas:
- No name was invented. The registry refuses to load if the schema file and the implementation table disagree.
- The §8 conceptual capabilities have **no schema and were never trained**. They are listed below with the trained tool that covers each one, or marked "no contract". Inventing new tool names or schemas is prohibited by both `qwen.txt` ("Do not invent tools") and the task brief.

**Status meanings:**
- **REAL** — executed against the real resource in automated tests: a real filesystem, real processes, a real HTTP server, the real Postgres test DB or real NEXUS routes.
- **CONTRACT** — tested only against a local stub of an external service.
- **BLOCKED** — interface present; it returns `unavailable` with the stated reason and never pretends to succeed.

## Trained tools (toolproto-1): checklist and coverage

| Tool | Side effect / confirmation | Implementation | Status | Tests (backend/tests/…) |
|---|---|---|---|---|
| `app_list_tasks(filter)` | read / no | `GET /tasks` with the caller's token; `open`, `overdue`, `today`, status and priority words, otherwise substring search | **REAL** | `test_jarvis_api::test_read_tool_runs_through_nexus_with_the_callers_scope` (incl. owner isolation), `…duplicate…`, `…step_limit…` |
| `app_read_calendar(range*)` | read / no | `GET /calendar?from&to`; range parsed in the user's IANA zone (`when.py`) | **REAL** | `test_calendar_read_and_db_describe_are_real`, `test_jarvis_tools::test_parse_window_and_day` |
| `app_create_task(title*, due)` | write / yes | `POST /tasks`. A NEXUS task needs a project and the contract has no project argument, so the task is created only when the user has exactly one open project; otherwise `conflict` | **REAL** (partial capability, documented) | `test_create_task_and_update_goal_against_real_services`, `test_create_task_without_a_single_project_fails_honestly` |
| `app_update_task(task*, title, due, status, action)` | write / yes | resolve by id or unique title (`ambiguous` → clarification); `PATCH /tasks/{id}` plus `/complete`, `/start`, `/reopen`, `/cancel`, `/block` | **REAL** | `test_update_task_and_update_event_against_real_services`, `test_failed_lookup_and_ambiguous_target` |
| `app_create_event(title*, start*, minutes)` | write / yes | `POST /calendar`; start parsed in the user's zone; default 60 min | **REAL** | `test_write_waits_for_confirmation_and_executes_exactly_once`, `test_declined_write_never_executes…`, `test_invalid_arguments_are_rejected…` |
| `app_update_event(event*, title, start, minutes)` | write / yes | resolve by id or unique title within −30 to +365 days; `PATCH /calendar/{id}` | **REAL** | `test_update_task_and_update_event_against_real_services` |
| `app_update_goal(goal*, progress*)` | write / yes | resolve the goal; `PATCH /learning/goals/{id} {"progress": n}` (0–100) | **REAL** | `test_create_task_and_update_goal_against_real_services` |
| `app_send_message(recipient*, text*)` | external_write / yes | — | **BLOCKED**: NEXUS has no messaging service | `test_jarvis_tools::test_blocked_tools_are_unavailable…` |
| `fs_read_file(path*)` | read / no | inside `JARVIS_WORKSPACE_DIR` only; size cap with `truncated` flag; binary refused | **REAL** | `test_fs_write_read_search_move_delete_round_trip`, `test_fs_errors_are_specific`, `test_fs_read_truncates_and_says_so` |
| `fs_search(query*, path)` | read / no | name or content search; at most 2,000 files scanned and 50 hits | **REAL** | round trip test |
| `fs_write_file(path*, content*)` | write / yes | atomic write (temp file then replace) | **REAL** | round trip; traversal, absolute and secret-path refusal |
| `fs_move_file(source*, destination*)` | write / yes | no overwrite (`conflict`) | **REAL** | round trip, `test_fs_errors_are_specific` |
| `fs_delete_file(path*, match)` | destructive_write / yes | files only; optional glob, at most 50 files; credential-named files are never matched; the workspace root is protected | **REAL** | round trip, `test_fs_glob_delete_is_bounded`, `test_fs_glob_delete_never_touches_credential_files` |
| `shell_run(command*, cwd)` | execute / yes | argv only (no shell); program must be on `JARVIS_SHELL_ALLOWLIST`; **interpreters and package runners are refused even if allowlisted** (python, node, pwsh, cmd, bash, npm, ...); git options that run commands or leave the workspace (`-c`, `-C`, `--exec-path`, `--config-env`, ...) refused; cwd inside the workspace; minimal environment (no backend secrets); timeout and output cap | **REAL** (`git`, `ping`) | `test_shell_*` (allowlist, interpreters, git options, metacharacters, nonzero exit, cwd, timeout, secret leakage) |
| `web_fetch(url*)` | read / no, **but confirmation is required once private data (fs, tasks, calendar, db, shell results) has been read in the same turn** (prompt-injection exfiltration) | public hosts only (rejects loopback, private, link-local and metadata IPs); credentials in the URL refused; redirects re-checked; size cap; HTML stripped | **REAL** — local server, plus one manual live fetch of `https://example.com/` (200) | `test_web_fetch_*`, `test_web_egress_after_reading_private_data_needs_confirmation` |
| `http_request(method*, url*, body)` | external_write / yes | same host guard; method allowlist; never retried | **REAL** (local server) | `test_http_request_sends_a_body_and_validates_the_method` |
| `web_search(query*)` | read / no (same egress confirmation rule as `web_fetch`) | `JARVIS_WEB_SEARCH_PROVIDER`: `wikipedia` (free MediaWiki search API, no key) or `searxng` (JSON API at `JARVIS_WEB_SEARCH_URL`) | **REAL** (Wikipedia, live; Wikipedia articles only). SearXNG: CONTRACT (stub), no instance available | `test_web_search_contract_against_a_local_stub`, `test_wikipedia_provider_parses_mediawiki_search_and_drops_malformed_rows`, `test_unknown_search_provider_is_unavailable`; live: `evidence/e2e_real_model_final.json` |
| `db_describe(table*)` | read / no | `information_schema.columns`, public schema, identifier-validated; account/security tables (`users`, `sessions`, `password_reset_tokens`, `audit_logs`) and `alembic*`/`pg_*` refused | **REAL** (Postgres) | `test_calendar_read_and_db_describe_are_real`, `test_private_tables_are_not_described` |
| `db_query(sql*)` | read / no | — | **BLOCKED** by policy: raw SQL bypasses per-user ownership scoping | `test_blocked_tools_…` |
| `db_execute(sql*)` | destructive_write / yes | — | **BLOCKED** by policy (same reason) | `test_blocked_tools_…` |
| `code_run_python(code*)` | execute / yes | — | **BLOCKED** by policy: arbitrary code execution with no isolated sandbox on this host (no Docker or WSL) | `test_blocked_tools_…` |

**Totals:**
- 16 REAL: 7 NEXUS adapters, 5 filesystem tools, `shell_run`, `web_fetch`, `http_request`, `db_describe`.
- 1 CONTRACT: `web_search`.
- 4 BLOCKED, each with an explicit reason.

**Default configuration:** `fs_*`, `shell_run` and `web_search` are off until their settings are provided. Only available tools are attached to the model's prompt (`test_plain_response_and_only_available_tools_are_attached`).

## Conceptual capabilities in qwen.txt §8 (not trained, no schema)

| Capability | Covered by | Note |
|---|---|---|
| FILESYSTEM create, read, write and delete file | `fs_write_file`, `fs_read_file`, `fs_delete_file` | — |
| move file | `fs_move_file` | — |
| search files | `fs_search` | — |
| edit_file, copy_file, list_directory, file_exists | — | no contract; achievable by composing read, write and search |
| TERMINAL run_command | `shell_run` | allowlisted |
| inspect_process, list_processes | — | no contract |
| BROWSER open, navigate, click, type, extract, back, forward | — | No contract and not trained. Playwright and Chromium are installed, so this is technically feasible. `qwen.txt` places "browser control" in **Phase 8**. Not implemented. |
| WEB search | `web_search` | provider blocker |
| WEB fetch | `web_fetch` | — |
| WEB research | multi-tool composition | — |
| CODE inspect, modify, test, format, analyze | `fs_*` plus `shell_run` with an allowlisted toolchain (e.g. `pytest`, `ruff`) | `code_run_python` is blocked |
| GIT status, diff, log, branch, add, commit | `shell_run` with `git` allowlisted | Every `shell_run` needs confirmation. No dedicated git contract. |
| NEXUS task operations | `app_list_tasks`, `app_create_task`, `app_update_task` | — |
| NEXUS planner operations | `app_read_calendar`, `app_create_event`, `app_update_event` | — |
| NEXUS learning operations | `app_update_goal` | — |
| NEXUS project, knowledge, analytics, career, account operations | — | No contract. Reachable for the user through the existing UI and REST routes. Adding them needs schemas, owner sign-off and training data. |

## Cross-cutting tests (runtime, not per tool)

| Concern | Test |
|---|---|
| Strict call parsing; prose is never a call | `test_jarvis_toolcalls.py` (20) |
| Argument validation before execution | `test_argument_validation`, `test_integer_arguments…`, `test_invalid_arguments_are_rejected_before_execution` |
| Unknown and unavailable tools | `test_unsupported_tools_are_refused` |
| Malformed JSON; model writing its own `<tool_response>` | `test_malformed_or_self_answered_calls_execute_nothing` |
| Confirmation required, single use, decline, expiry, cross-user | `test_write_waits_…`, `test_declined_…`, `test_another_users_or_expired_proposal…` |
| Duplicate-call prevention; bounded steps | `test_duplicate_calls_are_not_re_executed_and_steps_are_bounded` |
| Timeouts, exceptions and malformed tool results | `test_timeout_crash_and_malformed_results_are_reported` |
| Partial results (`truncated`) | `test_fs_read_truncates…`, `test_web_fetch_follows_redirects_and_truncates` |
| Honesty: no claimed action without a successful tool | `test_claimed_action_without_a_tool_is_replaced_by_the_truth`, `…fails_honestly`, `…ambiguous…` |
| Client cannot inject tool or system turns | `test_clients_cannot_inject_tool_or_system_turns` |
| Crisis guard, direct and tool-mediated | `test_jarvis_crisis_guard.py`, `test_crisis_*` in `test_jarvis_api.py` |
| Model unavailable, wrong adapter; NEXUS classifier unaffected | `test_chat_when_off_is_503_and_nexus_classifier_routes_are_untouched`, `test_wrong_adapter_or_unreachable_server_is_unavailable` |
