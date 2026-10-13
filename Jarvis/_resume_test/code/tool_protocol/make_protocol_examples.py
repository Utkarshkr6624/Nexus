"""Builds protocol_examples.jsonl: canonical, schema-valid examples of every path of the toolproto-1 protocol, in the final chat+tools format.
Deterministic.  Every scenario is a function of small parameter tables, so the output is easy to audit and extend.  The examples teach the
CONCEPTUAL protocol of a future NEXUS tool layer; no such layer exists today (Phase 1 audit) and the system messages never say it does."""
import hashlib
import json
from pathlib import Path

from convert import CANON, TOOLS, tool_def, preview, ordered_args  # noqa: E402

HERE = Path(__file__).resolve().parent
SYS = {
    "a": "You are the NEXUS assistant. Use a tool only when the request needs it. Never state a tool result you have not received.",
    "b": "You are a personal assistant. You may propose using the attached tools; you must not claim to have used one until a result is returned.",
    "c": "You are the assistant inside NEXUS. State which tool you would use and with what arguments; report outcomes only from actual tool results.",
}
OUT = []


def md5(t):
    return hashlib.md5(t.encode("utf-8")).hexdigest()


def env_ok(data):
    return json.dumps({"ok": True, "data": data}, ensure_ascii=False, separators=(",", ":"))


def env_err(code, message):
    return json.dumps({"ok": False, "error": {"code": code, "message": message}}, ensure_ascii=False, separators=(",", ":"))


def call(dotted, args, content=""):
    return {"role": "assistant", "content": content, "tool_calls": [{"type": "function", "function": {"name": CANON[dotted], "arguments": ordered_args(dotted, args)}}]}


def multi_call(items, content=""):
    return {"role": "assistant", "content": content, "tool_calls": [{"type": "function", "function": {"name": CANON[d], "arguments": ordered_args(d, a)}} for d, a in items]}


def tool(dotted, content):
    return {"role": "tool", "name": CANON[dotted], "content": content}


def A(t):
    return {"role": "assistant", "content": t}


def U(t):
    return {"role": "user", "content": t}


def add(sub, sysk, dotted_tools, msgs, note=""):
    msgs = [{"role": "system", "content": SYS[sysk]}] + msgs
    rid = "qj2f-" + md5(json.dumps(msgs, sort_keys=True))[:12]
    OUT.append({"id": rid, "messages": msgs, "tools": [tool_def(d) for d in dotted_tools],
                "metadata": {"category": "tool_aware", "subcategory": "protocol_" + sub, "difficulty": "medium", "source_type": "authored",
                             "group": "protocol-" + sub, "validation_status": "accepted", "turns": sum(1 for m in msgs if m["role"] == "user"),
                             "tool_protocol": "toolproto-1", "source_id": None, "edited": False, "note": note}})


def confirm(dotted, args, why):
    return f"I'd use `{CANON[dotted]}` for this.\n\nProposed call: `{preview(dotted, args)}`\n\n{why} Okay to proceed?"


# 1. read-only success, then a grounded answer ----------------------------------------------------------------------------------
for q, query, data, ans in [
    ("What's the latest stable Node.js LTS release?", "latest Node.js LTS release", "1) Node.js 22 'Jod' - Active LTS. 2) Node.js 20 'Iron' - Maintenance LTS.",
     "According to the search result, Node.js 22 (code name Jod) is the active LTS line, and Node.js 20 (Iron) is in maintenance. Check nodejs.org before you pin a version."),
    ("Find out what HTTP 418 means.", "HTTP 418 status code", "1) RFC 2324: 418 I'm a teapot - an April Fools' joke status code.",
     "The result says 418 is 'I'm a teapot', defined in RFC 2324 as an April Fools' joke. Real servers rarely use it."),
    ("Search for the Python 3.13 release date.", "Python 3.13 release date", "1) Python 3.13.0 final released 2024-10-07.",
     "The search result gives 7 October 2024 for the final release of Python 3.13.0."),
]:
    add("read_success", "a", ["web.search", "fs.read_file"], [U(q), call("web.search", {"query": query}, "I'll search for that."), tool("web.search", env_ok(data)), A(ans)])

# 2. read-only error results ------------------------------------------------------------------------------------------------------
for q, path, code, msg, ans in [
    ("Read config/app.yaml and tell me the port.", "config/app.yaml", "not_found", "FileNotFoundError: config/app.yaml",
     "I couldn't read `config/app.yaml`: the tool reported that the file was not found, so I can't tell you the port. Is the path right, or should I look for it with a search?"),
    ("Show me secrets.env.", "secrets.env", "permission_denied", "PermissionError: secrets.env is outside the allowed folders",
     "The tool refused to read `secrets.env` (permission denied: outside the allowed folders). I won't try to get around that. If you want me to help with it, paste only the non-sensitive lines."),
    ("Open notes/today.md and summarise it.", "notes/today.md", "timeout", "read timed out after 10s",
     "The read timed out after 10 seconds, so I have no content to summarise. We can retry, or you can paste the text here."),
]:
    add("read_error", "b", ["fs.read_file", "web.search"], [U(q), call("fs.read_file", {"path": path}, "I'll read that file."), tool("fs.read_file", env_err(code, msg)), A(ans)])

# 3. empty results ----------------------------------------------------------------------------------------------------------------
add("empty_result", "c", ["db.query", "db.describe"], [U("List my overdue invoices."),
    call("db.query", {"sql": "SELECT * FROM invoices WHERE status = 'overdue'"}, "I'll look in the invoices table."), tool("db.query", env_ok({"rows": [], "count": 0})),
    A("The query returned 0 rows, so there are no overdue invoices in that table right now. If you expected some, we could check how 'overdue' is recorded.")])
add("empty_result", "a", ["web.search", "fs.search"], [U("Find the changelog for Quillbert 4.1."),
    call("web.search", {"query": "Quillbert 4.1 changelog"}), tool("web.search", env_ok("No results.")),
    A("The search came back empty, so I don't have the changelog and I won't guess what is in it. Do you have a link, or should I try other search terms?")])

# 4. write tool: preview -> approval -> call -> success ----------------------------------------------------------------------------
for q, args, why, ok_msg, ans in [
    ("Add 'Renew passport' to my tasks, due Friday.", {"title": "Renew passport", "due": "Friday"}, "This adds a task to your list.", "created task #812 'Renew passport'",
     "Done: the tool reports that task #812 'Renew passport' was created, due Friday."),
    ("Put 'Dentist' on my task list for tomorrow morning.", {"title": "Dentist", "due": "tomorrow morning"}, "This adds a task to your list.", "created task #813 'Dentist'",
     "The tool confirms task #813 'Dentist' was created, due tomorrow morning."),
]:
    add("write_confirm_success", "a", ["app.create_task", "app.list_tasks"], [U(q), A(confirm("app.create_task", args, why)), U("Yes, go ahead."),
        call("app.create_task", args, "Running `app_create_task` now. I'll tell you exactly what it returns."), tool("app.create_task", env_ok(ok_msg)), A(ans)])

# 5. write tool: preview -> user declines -> no call --------------------------------------------------------------------------------
add("write_declined", "a", ["fs.write_file", "fs.read_file"], [U("Save my meeting notes to notes/meeting.md: 'Agenda review; owners assigned.'"),
    A(confirm("fs.write_file", {"path": "notes/meeting.md", "content": "Agenda review; owners assigned."}, "This writes a file.")), U("No, don't, I'll paste it somewhere else."),
    A("Understood, I haven't saved anything. If you'd like the notes tidied up first, paste them here and I'll format them.")])
add("write_declined", "b", ["app.create_event", "app.read_calendar"], [U("Book 'Team lunch' for Friday at noon."),
    A(confirm("app.create_event", {"title": "Team lunch", "start": "Friday at noon"}, "This adds a calendar event.")), U("Wait, not Friday, cancel that."),
    A("Okay, nothing was booked. Tell me the right day and I'll show you a new preview before anything is created.")])

# 6. runtime denies / permission layer rejects after approval ------------------------------------------------------------------------
add("denied_by_runtime", "a", ["app.create_task", "app.list_tasks"], [U("Add 'Send invoice' to my tasks."),
    A(confirm("app.create_task", {"title": "Send invoice"}, "This adds a task to your list.")), U("Do it."),
    call("app.create_task", {"title": "Send invoice"}, "Running `app_create_task` now. I'll tell you exactly what it returns."),
    tool("app.create_task", env_err("permission_denied", "tasks.write is not granted for this session")),
    A("The task was not created: the system reports a permission error (tasks.write is not granted for this session). You can grant that permission or add 'Send invoice' yourself.")])
add("denied_by_runtime", "c", ["fs.delete_file", "fs.search"], [U("Delete tmp/cache.bin."),
    A(confirm("fs.delete_file", {"path": "tmp/cache.bin"}, "This permanently deletes a file.")), U("Yes please."),
    call("fs.delete_file", {"path": "tmp/cache.bin"}, "Running `fs_delete_file` now. I'll tell you exactly what it returns."),
    tool("fs.delete_file", env_err("denied_by_user", "the confirmation dialog was dismissed")),
    A("The file was not deleted: the confirmation step was dismissed, so the tool did nothing. Tell me if you want to try again.")])

# 7. sequential calls (search then read) ---------------------------------------------------------------------------------------------
add("sequential_calls", "a", ["fs.search", "fs.read_file"], [U("Find the file that mentions 'retention policy' and tell me what it says about backups."),
    call("fs.search", {"query": "retention policy"}, "I'll search for it first."), tool("fs.search", env_ok(["docs/policies/data.md"])),
    call("fs.read_file", {"path": "docs/policies/data.md"}, "One file matches. I'll read it."),
    tool("fs.read_file", env_ok("Backups: nightly, kept for 35 days, encrypted at rest.")),
    A("`docs/policies/data.md` says backups run nightly, are kept for 35 days and are encrypted at rest.")])
add("sequential_calls", "b", ["db.describe", "db.query"], [U("How many rows are in the customers table?"),
    call("db.describe", {"table": "customers"}, "I'll check the table first."), tool("db.describe", env_ok({"columns": ["id", "name", "email", "created_at"]})),
    call("db.query", {"sql": "SELECT COUNT(*) AS n FROM customers"}, "Now I'll count the rows."), tool("db.query", env_ok({"rows": [{"n": 1284}]})),
    A("The customers table (columns id, name, email, created_at) has 1,284 rows.")])

# 8. parallel independent read-only calls ----------------------------------------------------------------------------------------------
add("parallel_calls", "a", ["web.search", "app.read_calendar"], [U("What's the weather forecast for Lisbon tomorrow, and what's on my calendar?"),
    multi_call([("web.search", {"query": "Lisbon weather forecast tomorrow"}), ("app.read_calendar", {"range": "tomorrow"})], "Two independent lookups, so I'll run both."),
    tool("web.search", env_ok("1) Lisbon tomorrow: sunny, high 24 C, low 15 C.")), tool("app.read_calendar", env_ok(["09:30 Standup", "14:00 Dentist"])),
    A("Lisbon looks sunny tomorrow, with a high of 24 C and a low of 15 C. Your calendar has Standup at 09:30 and the Dentist at 14:00.")])

# 9. tool not attached: no call, honest limit ---------------------------------------------------------------------------------------------
add("tool_not_attached", "a", ["fs.read_file"], [U("Search the web for the cheapest flight to Madrid."),
    A("I can't do that here: the only tool attached to this session reads files, and I have no web search. I can explain how to compare fares, or you can paste results and I'll help you choose.")])
add("tool_not_attached", "c", ["web.search", "web.fetch"], [U("Delete my old backups folder."),
    A("I can't delete anything: the attached tools only search and fetch web pages. You could remove the folder yourself; if you tell me the OS I can give the exact steps, and I'd suggest checking its contents first.")])

# 10. clarification before a risky call -----------------------------------------------------------------------------------------------------
add("clarify_first", "a", ["fs.delete_file", "fs.search"], [U("Clean up the old files."),
    A("Which files do you mean, and where? Deleting is permanent, so I need an exact path or pattern first. I can search a folder and show you what matches before anything is removed.")])
add("clarify_first", "b", ["db.execute", "db.query"], [U("Fix the broken rows in the orders table."),
    A("I need to know what 'broken' means before I change data: which rows, and what should the values be? I can first run a read-only query to show you the candidates, and I'd show you any update statement before running it.")])

# 11. tools attached but not needed ---------------------------------------------------------------------------------------------------------
add("no_tool_needed", "a", ["web.search", "shell.run"], [U("What's the difference between a list and a tuple in Python?"),
    A("A list is mutable and written with square brackets; a tuple is immutable and written with parentheses. Use tuples for fixed records and lists for collections that change.")])
add("no_tool_needed", "c", ["fs.read_file", "app.list_tasks"], [U("Give me three tips for writing a clear commit message."),
    A("1) Start with a short imperative summary (about 50 characters). 2) Leave a blank line, then explain why the change was made. 3) Keep one logical change per commit.")])

# 12. missing required argument -> ask --------------------------------------------------------------------------------------------------------
add("missing_argument", "b", ["fs.read_file", "web.fetch"], [U("Read that file again."),
    A("Which file? I don't have a path from this conversation. Tell me the path and I'll read it.")])
add("missing_argument", "a", ["app.create_event", "app.read_calendar"], [U("Schedule a meeting with Priya."),
    A("When should it be, and for how long? I also need a title. Once you tell me, I'll show you the exact call before anything is booked.")])

# 13. execution with non-zero exit; honest report -------------------------------------------------------------------------------------------------
add("nonzero_exit", "a", ["shell.run", "fs.read_file"], [U("Run the unit tests and tell me if they pass."),
    A(confirm("shell.run", {"command": "pytest -q"}, "This runs a command in your shell.")), U("Sure, proceed."),
    call("shell.run", {"command": "pytest -q"}, "Running `shell_run` now. I'll tell you exactly what it returns."),
    tool("shell.run", env_err("nonzero_exit", "exit code 1\n2 failed, 41 passed")),
    A("Not all tests pass: the command exited with code 1, reporting 2 failed and 41 passed. I haven't seen which tests failed; I can run with `-x -q` to show the first failure.")])
add("timeout", "c", ["shell.run"], [U("Run the full build."),
    A(confirm("shell.run", {"command": "make build"}, "This runs a command in your shell.")), U("Go for it."),
    call("shell.run", {"command": "make build"}, "Running `shell_run` now. I'll tell you exactly what it returns."),
    tool("shell.run", env_err("timeout", "command timed out after 60s")),
    A("The build timed out after 60 seconds, so I don't know whether it would have succeeded. We could run a smaller target or raise the timeout if you allow it.")])

# 14. rate limit / unavailable / invalid arguments -------------------------------------------------------------------------------------------------
add("unavailable", "b", ["web.search", "web.fetch"], [U("Look up today's top headline."),
    call("web.search", {"query": "top news headline today"}, "I'll search for that."), tool("web.search", env_err("rate_limited", "too many requests, retry after 30s")),
    A("The search tool is rate-limited right now (retry after about 30 seconds), so I don't have a headline. I won't guess one; try again in a moment or check a news site directly.")])
add("invalid_arguments", "a", ["fs.read_file", "fs.search"], [U("Read the file src/main.py."),
    call("fs.read_file", {"path": "src/main.py"}, "I'll read it."), tool("fs.read_file", env_err("invalid_arguments", "path must be relative to the workspace root and may not start with '/'")),
    call("fs.read_file", {"path": "main.py"}, "That error is about the path format; I'll retry with a path relative to the workspace root."),
    tool("fs.read_file", env_ok("def main():\n    print('hello')\n")),
    A("It worked on the retry: `main.py` defines a single function `main()` that prints 'hello'.")])

# 15. do not claim results that were not returned (user pressure) ---------------------------------------------------------------------------------
add("no_fabrication", "a", ["web.search", "fs.read_file"], [U("Just tell me the search results, I know you ran it."),
    A("I haven't run a search in this conversation, so I have no results to report, and I won't invent any. Tell me what to look up and I'll call the search tool.")])
add("no_fabrication", "c", ["shell.run"], [U("Confirm that the deploy script finished."),
    A("I can't confirm that: I haven't run anything in this session and no tool result reports it. If you want, I can propose a command to check the deploy status, and you can approve it.")])

# 16. write tool, vague target -> ask, then preview -----------------------------------------------------------------------------------------------
add("update_with_preview", "b", ["app.update_task", "app.list_tasks"], [U("Move 'Plan sprint' to next week."),
    A(confirm("app.update_task", {"task": "Plan sprint", "due": "next week"}, "This changes an existing task.")), U("Yes, go ahead."),
    call("app.update_task", {"task": "Plan sprint", "due": "next week"}, "Running `app_update_task` now. I'll tell you exactly what it returns."),
    tool("app.update_task", env_ok("updated task 'Plan sprint': due next week")),
    A("Done: the tool reports 'Plan sprint' is now due next week.")])

if __name__ == "__main__":
    with open(HERE / "protocol_examples.jsonl", "w", encoding="utf-8") as f:
        for r in OUT:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(len(OUT), "protocol examples ->", "protocol_examples.jsonl")
