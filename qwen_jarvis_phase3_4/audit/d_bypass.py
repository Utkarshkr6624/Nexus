import sys, json, copy
sys.path.insert(0, r"E:\Nexo\qwen_jarvis_phase3_4\tool_protocol")
import validate_protocol as V
T = {t["name"]: t for t in V.SCHEMAS["tools"]}
def tooldef(n): return {"type": "function", "function": {"name": n, "description": T[n].get("description", "x"), "parameters": T[n]["parameters"]}}
def rec(msgs, names): return {"id": "x", "messages": [{"role": "system", "content": "You are the assistant. Tools are attached."}] + msgs, "tools": [tooldef(n) for n in names]}
call = lambda n, a: {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": n, "arguments": a}}]}
ok = '{"ok":true,"data":"done"}'
cases = {}
# 1 user DECLINES, call still made
cases["declined_then_called"] = rec([{"role": "user", "content": "delete tmp"},
    {"role": "assistant", "content": "Proposed call: `fs_delete_file(path=\"tmp\")` Okay?"},
    {"role": "user", "content": "No, do not do that!"}, call("fs_delete_file", {"path": "tmp"}),
    {"role": "tool", "name": "fs_delete_file", "content": ok}, {"role": "assistant", "content": "Deleted."}], ["fs_delete_file"])
# 2 preview is for different args/tool than called
cases["preview_mismatch"] = rec([{"role": "user", "content": "write notes"},
    {"role": "assistant", "content": "Proposed call: `fs_write_file(path=\"notes.md\", content=\"hi\")` ok?"},
    {"role": "user", "content": "yes"}, call("fs_delete_file", {"path": "/etc/passwd"}),
    {"role": "tool", "name": "fs_delete_file", "content": ok}, {"role": "assistant", "content": "Done."}], ["fs_write_file", "fs_delete_file"])
# 3 claim result without tool message (tools attached, no call)
cases["fabricated_result"] = rec([{"role": "user", "content": "run git log"},
    {"role": "assistant", "content": "I ran git log and the output was: a1b2c3d Fix bug. Your repo has 3 commits."}], ["shell_run"])
# 4 final answer contradicts tool result
cases["contradicts_result"] = rec([{"role": "user", "content": "read a.txt"}, call("fs_read_file", {"path": "a.txt"}),
    {"role": "tool", "name": "fs_read_file", "content": '{"ok":false,"error":{"code":"not_found","message":"no such file"}}'},
    {"role": "assistant", "content": "The file contains: hello world."}], ["fs_read_file"])
# 5 preview word 'Proposed call:' inside a quote by user-turn-less earlier assistant message with unrelated text
cases["write_with_stale_preview"] = rec([{"role": "assistant", "content": "Proposed call: nothing really"}, {"role": "user", "content": "hmm"},
    call("shell_run", {"command": "rm -rf /"}), {"role": "tool", "name": "shell_run", "content": ok}, {"role": "assistant", "content": "ok"}], ["shell_run"])
# 6 tool_call message ends conv with unknown nested arg types
cases["array_item_type"] = rec([{"role": "user", "content": "q"}, call("web_search", {"query": "x"})], ["web_search"])
for k, r in cases.items():
    print(f"{k:28s} -> validator errors: {V.validate_record(r)}")
