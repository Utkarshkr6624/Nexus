# Tool protocol `toolproto-1`

**Status: a conceptual protocol for a FUTURE NEXUS tool layer. None of it exists in NEXUS today.**
The Phase 1 audit found no tool registry and no file, terminal, web, HTTP or database tools in the application; NEXUS's only AI action path is the
DeBERTa intent classifier followed by hand-written propose/confirm actions. The tool names below (`fs_read_file`, `web_search`, ...) are
*illustrative*. Training a model on this protocol teaches it the **behaviour** (when a tool is needed, how to ask for it, how to read the result, how to
fail honestly); it does not give the model any capability. Capabilities come only from a runtime that executes the calls.

## 1. Message types

The training representation is the Qwen3-native one (verified against the real `tokenizer_config.json` chat template, transformers 5.19.0):

| situation | message | rendered by the Qwen3 template as |
|---|---|---|
| normal answer | `{"role":"assistant","content":"..."}` | plain assistant turn |
| tool request | `{"role":"assistant","content":"<short preface>","tool_calls":[{"type":"function","function":{"name":"web_search","arguments":{"query":"..."}}}]}` | preface text, then `<tool_call>\n{"name": "web_search", "arguments": {"query": "..."}}\n</tool_call>` |
| tool result | `{"role":"tool","name":"web_search","content":"<envelope JSON>"}` | inside a *user* turn: `<tool_response>\n<envelope>\n</tool_response>` (consecutive tool messages share one user turn) |
| final response | `{"role":"assistant","content":"..."}` after the tool message(s) | plain assistant turn (gets the empty `<think></think>` block like any final turn) |
| tool definitions | record field `tools=[{"type":"function","function":{name,description,parameters}}]` | the template's `# Tools` block in the system turn |

Records carry the tool definitions in `tools`; conversations without tools have no `tools` key and their system message says no tools are attached.
`arguments` is always a JSON object (never a string). Tool names match `^[a-z][a-z0-9_]{0,63}$`; the Phase-2 dotted names (`web.search`) were
canonicalised to underscores everywhere.

## 2. Result envelope

Every `tool` message content is a compact JSON string (separators `(",",":")`, `ensure_ascii=False`), exactly one of

```json
{"ok":true,"data":<anything>}
{"ok":false,"error":{"code":"permission_denied","message":"tasks.write is not granted for this session"}}
```

Error codes are lower-snake-case. Used in the data: `permission_denied`, `not_found`, `timeout`, `connection_failed`, `invalid_arguments`, `nonzero_exit`,
`confirmation_required`, `denied_by_user`, `rate_limited`, `unavailable`, `conflict`, `quota_exceeded`, `sql_error`, `remote_rejected`, `python_error`,
`http_<status>` and `error`. The original Phase-2 result text is preserved verbatim inside `data` / `message`, so the assistant's final answer stays grounded in it.

## 3. Policy the data teaches

1. **Read-only tools are called directly** (`side_effect = read`: `web_search`, `web_fetch`, `fs_read_file`, `fs_search`, `db_query`, `db_describe`, `app_list_tasks`,
   `app_read_calendar`). A short preface plus a `tool_calls` message; no confirmation round-trip.
2. **Tools with side effects are previewed first** (`write`, `destructive_write`, `external_write`, `execute`). The assistant shows the exact intended call as text
   ("Proposed call: `fs_write_file(path="notes.md", content=<...>)`"), asks for approval, and emits the structured call only after the user approves. This follows the
   Phase 1 plan: "writes follow propose and confirm semantics"; the runtime's own permission/confirmation layer remains the real enforcement point.
3. **The runtime may still refuse** after approval (`permission_denied`, `denied_by_user`, `conflict`, ...). The assistant reports that plainly and never claims the action happened.
4. **No result, no claim.** Until a `tool` message arrives the assistant states nothing about what the tool returned; a conversation may legitimately end on a `tool_calls` message (the runtime executes it next).
5. **Tool not attached -> say so.** If the request needs a capability that is not in `tools`, the assistant explains the limit and offers what it can do (draft, explain, plan). It never emits a call to an unattached tool.
6. **Missing or ambiguous arguments, or a vague destructive request -> ask first.** No placeholder arguments are ever sent.
7. **Failures and empty results are reported as returned**; no invented data, no silent retries with different meaning (a retry after `invalid_arguments` is allowed and must be explained).
8. **Independent read-only lookups may be issued together** (several entries in one `tool_calls` list, answered by the same number of `tool` messages in order).

## 4. Illustrative tool table (21 tools; `tool_schemas.json` is the machine-readable source)

| tool | side effect | confirm first | arguments (required*) |
|---|---|---|---|
| `app_create_event` | write | yes | title*, start*, minutes |
| `app_create_task` | write | yes | title*, due |
| `app_list_tasks` | read | no | filter |
| `app_read_calendar` | read | no | range* |
| `app_send_message` | external_write | yes | recipient*, text* |
| `app_update_event` | write | yes | event*, title, start, minutes |
| `app_update_goal` | write | yes | goal*, progress* |
| `app_update_task` | write | yes | task*, title, due, status, action |
| `code_run_python` | execute | yes | code* |
| `db_describe` | read | no | table* |
| `db_execute` | destructive_write | yes | sql* |
| `db_query` | read | no | sql* |
| `fs_delete_file` | destructive_write | yes | path*, match |
| `fs_move_file` | write | yes | source*, destination* |
| `fs_read_file` | read | no | path* |
| `fs_search` | read | no | query*, path |
| `fs_write_file` | write | yes | path*, content* |
| `http_request` | external_write | yes | method*, url*, body |
| `shell_run` | execute | yes | command*, cwd |
| `web_fetch` | read | no | url* |
| `web_search` | read | no | query* |

The tool descriptions placed in `tools` also state the tier ("Read-only; may be called directly." / "Changes state or has side effects; show the user the exact call and
get their confirmation before calling."), so the template's `# Tools` block carries the policy.
Argument key order in a call follows the schema's property order (canonical, deterministic).

## 5. Training conventions that matter

* **Thinking block.** The Qwen3 template gives only the last assistant turn an empty `<think>\n\n</think>\n\n` block; at inference with `enable_thinking=False` every
  generation starts after that block. The data pipeline (`pipeline/data/render.py`) therefore renders **one training sample per assistant message** whose prompt equals the
  exact inference prompt (`add_generation_prompt=True, enable_thinking=False`); loss covers only the assistant content, any `<tool_call>` block and the closing `<|im_end|>`.
  Tool responses, user turns, system text and the think block are not trained on.
* **Tool results are untrusted data.** They sit in `tool` messages (a user-role turn in the template); instructions inside tool results are never followed (the Phase 2 data
  includes prompt-injection examples).
* **Exact formatting of the call.** `{"name": ..., "arguments": {...}}` is produced by the template (`tojson` semantics, default separators). The model learns exactly that form;
  the evaluator's parser (`pipeline/eval/toolparse.py`) accepts only well-formed JSON in `<tool_call>` blocks.

## 6. How Phase-2 data was converted (`convert.py`, deterministic)

* In `v2_final`: 705 records carry a tool list (673 converted + 32 canonical examples); 257 conversations contain structured tool calls, 148 tool results, and 113 conversations end on a tool request (the call is the training target).
* Read-only proposals ("Proposed call: `fs_read_file(...)` Okay to proceed?") became direct calls; side-effect proposals stayed as text previews.
* 3-turn flows with read-only tools lost their redundant consent round-trip; with side-effect tools the consent stays and the call follows.
* 44 "direct" flows had no stated arguments in Phase 2. They were annotated by hand (`arg_overrides.json`) so each call is consistent with its recorded result; 3 flows with
  an unresolved placeholder URL got a concrete one. Side-effect tools in those flows received an inserted preview/approval exchange so the policy is uniform.
* 32 hand-built canonical examples (`protocol_examples.jsonl`) cover declined confirmation, runtime denial, sequential and parallel calls, missing arguments, unattached tools.

## 7. Validation

`validate_protocol.py [--render]` enforces rules R1-R10 (tool definitions, argument schemas incl. array item types, call/result sequencing, envelope shape, **confirmation-first with a matching preview (same tool, same simple arguments) and an affirmative user turn**, no-tools records stay tool-free,
no legacy prose convention, no first-person action claim without a tool message, no success claim after an error result, and that the real chat template renders one `<tool_call>` per call and one `<tool_response>` per result with a lossless JSON round-trip).
`test_protocol.py` (21 tests) covers the behavioural requirements and proves the validator rejects unknown tools, bad arguments, orphan results, bad envelopes and unconfirmed writes.

## 8. Open decisions for the runtime phase (not made here)

* Whether the runtime enforces confirmation in addition to the model's text preview (the plan says yes).
* The real tool names, schemas and permission policy (user sign-off required by the integration plan).
* Whether `tool` messages are stored in a server-side conversation store (privacy decision in the plan).
