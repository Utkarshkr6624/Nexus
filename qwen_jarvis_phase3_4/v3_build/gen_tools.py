"""Priority C/D: tool use with the project's real toolproto-1 tools (definitions copied from V2 records). Read-only tools are called
directly; write tools are proposed and run only after approval; results are reported only from the tool message; errors are reported
honestly with what did and did not happen."""
import json

from gen_common import TOOL_SYS, Maker, env, tool_defs

FILES = [("notes/meeting.md", "Decisions: ship v2.3 on Friday; Ana owns the release notes; budget review moved to the 18th."),
         ("docs/todo.txt", "1. renew domain\n2. update README\n3. rotate API keys"),
         ("config/app.env.example", "PORT=8080\nLOG_LEVEL=info\nCACHE_TTL=300"),
         ("reports/q3_summary.txt", "Q3 revenue 412k (+6% vs Q2). Churn 3.1%. Top region: EMEA.")]
SEARCH = [("python 3.13 release date", "1) Python 3.13.0 was released on 7 October 2024. 2) It added an experimental free-threaded build."),
          ("what does HTTP 429 mean", "1) 429 Too Many Requests: the client sent too many requests in a given time (rate limiting). 2) Servers may send Retry-After."),
          ("boiling point of water at 2000 m", "1) At about 2000 m altitude water boils near 93 C. 2) Boiling point drops roughly 1 C per 300 m."),
          ("SQLite max database size", "1) SQLite databases can be up to about 281 TB with default page settings.")]
TASKS = [["Pay electricity bill (due Fri)", "Book car service", "Call Grandma"], ["Draft blog post", "Review PR #42"], []]
ERRS = [("not_found", "file not found"), ("permission_denied", "permission denied"), ("timeout", "request timed out after 30 s"),
        ("auth_required", "missing credentials for this service"), ("sql_error", "relation \"invoices\" does not exist")]


def tc(name, args):
    return [{"type": "function", "function": {"name": name, "arguments": args}}]


def gen(seed=303):
    m = Maker("tools", seed)
    r = m.rng
    D = tool_defs()
    pick = lambda *names: [D[n] for n in names] + [D[x] for x in r.sample([k for k in D if k not in names], 2)]  # noqa: E731

    for i in range(70):   # read file -> grounded summary
        path, content = r.choice(FILES)
        q = r.choice([f"What's in {path}?", f"Summarise {path} for me.", f"Read {path} and tell me the key points."])
        m.add([{"role": "user", "content": q}, {"role": "assistant", "content": "I'll read the file.", "tool_calls": tc("fs_read_file", {"path": path})},
               {"role": "tool", "name": "fs_read_file", "content": env(True, content)},
               {"role": "assistant", "content": f"Here's what {path} contains:\n\n" + "\n".join(f"- {line.strip()}" for line in content.replace("; ", "\n").split("\n") if line.strip())}],
              "tool_aware", "read_call_result", system=r.choice(TOOL_SYS), tools=pick("fs_read_file"))
    for i in range(60):   # web search -> answer citing the result
        qq, res = r.choice(SEARCH)
        first = res.split("2)")[0].replace("1) ", "").strip()
        m.add([{"role": "user", "content": r.choice(["Look up: ", "Can you search for ", "Find out "]) + qq + "?"},
               {"role": "assistant", "content": "I'll search for that.", "tool_calls": tc("web_search", {"query": qq})},
               {"role": "tool", "name": "web_search", "content": env(True, res)},
               {"role": "assistant", "content": f"According to the search results: {first} I'd double-check the original source if you need it to be exact."}],
              "tool_aware", "search_result", system=r.choice(TOOL_SYS), tools=pick("web_search"))
    for i in range(70):   # tool errors -> honest report, nothing claimed
        code, msg = r.choice(ERRS)
        if code == "sql_error":
            call, q = ("db_query", {"sql": "SELECT COUNT(*) FROM invoices"}), "How many invoices do we have?"
        elif code in ("timeout", "auth_required"):
            call, q = ("web_fetch", {"url": "https://status.example.com"}), "Fetch https://status.example.com and tell me if anything is down."
        else:
            p = r.choice(FILES)[0]
            call, q = ("fs_read_file", {"path": p}), f"Open {p} and summarise it."
        nm, args = call
        why = {"not_found": "the file doesn't exist at that path", "permission_denied": "I don't have permission to read it",
               "timeout": "the request timed out after 30 seconds", "auth_required": "the service needs credentials that aren't configured in this session",
               "sql_error": "the database says the table `invoices` doesn't exist"}[code]
        nxt = {"not_found": "Could you check the path, or should I search for the file by name?", "permission_denied": "You could grant read access or paste the contents here.",
               "timeout": "I can try again, or you can check the page in your browser.", "auth_required": "Once credentials are set up I can retry.",
               "sql_error": "The table may have a different name - I can list the tables if you'd like."}[code]
        m.add([{"role": "user", "content": q}, {"role": "assistant", "content": f"I'll use `{nm}`.", "tool_calls": tc(nm, args)},
               {"role": "tool", "name": nm, "content": env(False, code=code, message=msg)},
               {"role": "assistant", "content": f"That didn't work: {why}, so I don't have the answer and nothing was changed. {nxt}"}],
              "tool_aware", "tool_error_honest", system=r.choice(TOOL_SYS), tools=pick(nm))
    for i in range(70):   # write tool: propose -> approval -> call -> result
        title = r.choice(["Renew passport", "Order printer ink", "Send invoice to Kofi", "Book dentist", "Water the plants", "Prepare slides"])
        due = r.choice(["2026-11-03", "tomorrow", "Friday", "2026-12-01"])
        prop = f"I can create it with `app_create_task(title=\"{title}\", due=\"{due}\")`. This changes your task list, so please confirm first - nothing has been created yet."
        msgs = [{"role": "user", "content": f"Add a task '{title}' due {due}."}, {"role": "assistant", "content": prop}]
        if r.random() < 0.75:
            ok = r.random() < 0.8
            msgs += [{"role": "user", "content": r.choice(["Yes, go ahead.", "Confirmed.", "Do it."])},
                     {"role": "assistant", "content": "Creating the task now.", "tool_calls": tc("app_create_task", {"title": title, "due": due})},
                     {"role": "tool", "name": "app_create_task", "content": env(True, {"id": r.randint(100, 999), "title": title, "due": due}) if ok else env(False, code="conflict", message="a task with this title already exists")},
                     {"role": "assistant", "content": f"Done - the task '{title}' was created with due date {due}." if ok else f"The task was not created: a task called '{title}' already exists. Do you want me to update that one instead?"}]
        else:
            msgs += [{"role": "user", "content": "Actually, don't."}, {"role": "assistant", "content": "No problem - I haven't created anything, and your task list is unchanged."}]
        m.add(msgs, "tool_aware", "write_propose_confirm", system=r.choice(TOOL_SYS), tools=pick("app_create_task"))
    for i in range(45):   # missing required argument -> clarify, no call
        q, a = r.choice([("Create a calendar event for me.", "Sure - what should the event be called, and when does it start? I'll show you the exact call before creating it."),
                         ("Move the file.", "Which file should I move, and where to? Give me the source and destination paths."),
                         ("Search the web.", "What would you like me to search for?"),
                         ("Run a query on the database.", "What do you want to find? Describe it or give me the SQL, and I'll check the table names first if needed.")])
        tools = pick("app_create_event") if "calendar" in q else pick("fs_move_file") if "file" in q else pick("web_search") if "web" in q else pick("db_query")
        m.add([{"role": "user", "content": q}, {"role": "assistant", "content": a}], "tool_aware", "missing_argument_clarify", system=r.choice(TOOL_SYS), tools=tools)
    for i in range(45):   # tool attached but not needed
        q, a = r.choice([("What's 15% of 80?", "15% of 80 is 12."), ("What does 'idempotent' mean?", "An operation is idempotent if doing it several times has the same effect as doing it once - for example, setting a value, or an HTTP PUT."),
                         ("Give me a synonym for 'quick'.", "Fast, rapid or swift."), ("How many days are in a leap year?", "366.")])
        m.add([{"role": "user", "content": q}, {"role": "assistant", "content": a}], "tool_aware", "answer_without_tool", system=r.choice(TOOL_SYS), tools=pick("web_search", "fs_read_file"))
    for i in range(40):   # needed tool is not attached
        q, need, a = r.choice([("Send Omar a message saying the build is green.", "app_send_message", "I can't send messages here: no messaging tool is attached in this session, so nothing has been sent. Here's the text if you want to send it yourself: \"Hi Omar, the build is green.\""),
                               ("Delete old_logs.txt.", "fs_delete_file", "There's no file-deletion tool attached to this session, so I haven't deleted anything. You can remove it with `rm old_logs.txt` (or `del old_logs.txt` on Windows)."),
                               ("Run this Python and tell me the output: print(sum(range(10)))", "code_run_python", "No code-execution tool is attached, so I haven't run it. Reading the code: it prints 45, the sum of 0 through 9.")])
        tools = [D[x] for x in r.sample([k for k in D if k != need], 3)]
        m.add([{"role": "user", "content": q}, {"role": "assistant", "content": a}], "tool_aware", "tool_missing_honest", system=r.choice(TOOL_SYS), tools=tools)
    for i in range(40):   # multi-step: list tasks -> partial result honestly
        ts = r.choice(TASKS)
        res = env(True, ts)
        a = ("Your open tasks are:\n" + "\n".join(f"- {t}" for t in ts)) if ts else "The task list came back empty, so there are no open tasks right now."
        m.add([{"role": "user", "content": "What's on my task list?"}, {"role": "assistant", "content": "Let me check.", "tool_calls": tc("app_list_tasks", {"filter": "open"})},
               {"role": "tool", "name": "app_list_tasks", "content": res}, {"role": "assistant", "content": a}],
              "tool_aware", "list_tasks_result", system=r.choice(TOOL_SYS), tools=pick("app_list_tasks"))
    return m.out
