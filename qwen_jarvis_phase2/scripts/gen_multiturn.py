"""Additional multi-turn examples.
 A. Tool flows: request -> proposed call -> user approval -> (simulated) tool result in the conversation -> honest report.
    The tool result is part of the conversation text; the assistant only reports what that result says.
 B. Knowledge dialogs built from the authored fact sheets (definition -> example -> common misunderstanding).
 C. Plan revisions: a computed weekly plan, then a replan after a constraint changes."""
from lib import *

R = seeded("multiturn")
out = []

# ======================================================================= A. tool flows
APPROVE = ["Yes, go ahead.", "Go for it.", "Do it.", "Yes please.", "Okay, run it.", "Sure, proceed."]
BRANCHES = ["feature/login-form", "main", "fix/date-parsing", "release/2.4", "chore/update-deps", "feature/search-index"]
FLOWS = []


def flow(req, tool, call, ok, result, report):
    FLOWS.append((req, tool, call, ok, result, report))


for v in ["3.13.14", "3.12.4", "3.11.9"]:
    flow("Check which Python version is installed.", "shell.run", 'command="python --version"', True, f"Python {v}", f"The command reported Python {v}, so that's the interpreter the shell is using. If your project expects a different version, a virtual environment will need to match it.")
for b in BRANCHES:
    flow("Which git branch am I on?", "shell.run", 'command="git branch --show-current"', True, b, f"You're on the `{b}` branch, according to the command's output.")
for n, ex in [(14, 2), (52, 0), (7, 1)]:
    flow("Run the unit tests.", "shell.run", 'command="pytest -q"', ex == 0, f"{n - ex} passed" if ex == 0 else f"exit code 1\n{ex} failed, {n - ex} passed", (f"All {n} tests passed according to the output." if ex == 0 else f"The run didn't fully pass: {n - ex} passed and {ex} failed. I can't tell from this output which tests failed, so I'd need the detailed report to say why. Want me to run it again with more detail?"))
for f, content, summary in [("settings.ini", "[server]\nport=8080\ndebug=false", "The file configures a server section with port 8080 and debug turned off."),
                            ("package.json", "{\n  \"name\": \"demo\",\n  \"version\": \"1.2.0\",\n  \"scripts\": {\"test\": \"vitest\"}\n}", "It's a package named demo at version 1.2.0 whose test script runs vitest."),
                            ("notes.txt", "Buy milk\nCall Sam\nFinish report", "It contains three lines: buy milk, call Sam and finish the report."),
                            ("config.yaml", "env: staging\nretries: 3\ntimeout: 30", "It sets the environment to staging, with 3 retries and a timeout of 30."),
                            ("todo.md", "- [x] Draft outline\n- [ ] Write intro\n- [ ] Add references", "One item is done (draft outline) and two remain: write the intro and add references.")]:
    flow(f"Read {f} and tell me what it says.", "fs.read_file", f'path="{f}"', True, content, summary + " That's based only on what the tool returned.")
    flow(f"Open {f}.", "fs.read_file", f'path="{f}"', False, f"ERROR: FileNotFoundError: {f}", f"The read failed: the tool says `{f}` doesn't exist, so I haven't seen its contents. The path might be different. Want me to search for similar names?")
for q, res, rep in [("recent changes in Python packaging", "1) Packaging guide: pyproject.toml is the standard place for build metadata.\n2) Release notes: pip now supports a new resolver flag.", "The search returned two results: the first says pyproject.toml is the standard place for build metadata, and the second mentions a new pip resolver flag. That's all I have from the search; I haven't opened either page, so I can't give more detail."),
                    ("opening hours city library", "1) citylibrary.example: Mon-Fri 9:00-20:00, Sat 10:00-16:00, Sun closed.", "According to the top result, the library is open Monday to Friday 9:00 to 20:00, Saturday 10:00 to 16:00 and closed on Sunday. It's one source, so check the library's own page for holiday changes."),
                    ("best practices for password storage", "1) OWASP cheat sheet: use Argon2id, scrypt or bcrypt with a unique salt.\n2) NIST guidance: favour length and breach checks over complexity rules.", "Two results came back. OWASP recommends Argon2id, scrypt or bcrypt with a unique salt, and NIST favours length and breached-password checks over complexity rules. I'm relying on those snippets rather than the full documents."),
                    ("weather Oslo tomorrow", "No results.", "The search returned nothing, so I don't have a forecast for Oslo. A weather service would be the better source, or I can try different search terms.")]:
    flow(f"Search the web for {q}.", "web.search", f'query="{q}"', not res.startswith("No results"), res, rep)
for c, res, rep in [("orders", "count: 1284", "The orders table has 1,284 rows, according to the query result."),
                    ("users", "count: 57", "There are 57 rows in the users table, per the query."),
                    ("tickets", "ERROR: relation \"tickets\" does not exist", "The query failed because the database says the table `tickets` doesn't exist, so I have no count. The table may be named differently.")]:
    flow(f"How many rows are in the {c} table?", "db.query", f'sql="SELECT COUNT(*) FROM {c}"', not res.startswith("ERROR"), res, rep)
for t, w, tid in [("Prepare slides", "Friday", 482), ("Pay the electricity bill", "tomorrow", 517), ("Call the dentist", "Monday", 233), ("Submit the expense report", "Thursday", 640)]:
    flow(f"Add a task '{t}' due {w}.", "app.create_task", f'title="{t}", due="{w}"', True, f"OK: created task #{tid} '{t}' due {w}", f"Done: the system confirms task #{tid}, '{t}', was created with a due date of {w}.")
flow("Add a task 'Renew passport' due Friday.", "app.create_task", 'title="Renew passport", due="Friday"', False, "ERROR 403: permission denied (tasks.write)", "I couldn't create the task: the system returned a permission error (`tasks.write` missing), so nothing was created. You may need to grant that permission, or add it yourself.")
for lst, rep in [("- 'Prepare slides' (due Friday, high)\n- 'Call the dentist' (due Monday, low)", "You have two open tasks: 'Prepare slides' due Friday (high priority) and 'Call the dentist' due Monday (low priority). By due date the dentist call comes first, but the slides are higher priority."),
                 ("(no tasks)", "The tool returned an empty list, so you have no open tasks right now."),
                 ("- 'Pay rent' (overdue by 2 days, high)\n- 'Water plants' (due today, low)\n- 'Plan sprint' (due Wednesday, medium)", "You have three open tasks. 'Pay rent' is 2 days overdue and high priority, so it comes first. 'Water plants' is due today, and 'Plan sprint' is due Wednesday.")]:
    flow("Show me my open tasks.", "app.list_tasks", 'filter="open"', True, lst, rep)
for st, rep in [("HTTP 200 OK (143 ms)", "The endpoint answered 200 OK in about 143 ms, so it's responding."), ("HTTP 503 Service Unavailable (10.0s)", "The request returned 503 Service Unavailable after 10 seconds, so the service isn't healthy right now. I got no data from it."), ("ERROR: connection refused", "The connection was refused, which usually means nothing is listening on that address. I didn't get a response at all.")]:
    flow("Check if the API is responding.", "http.request", 'method="GET", url=<health endpoint>', st.startswith("HTTP 200"), st, rep)
for cmd, res, rep in [("ls -la reports", "total 3\n-rw-r--r-- 1 me me 2048 q1.csv\n-rw-r--r-- 1 me me 4096 q2.csv", "The `reports` folder contains two files: q1.csv (2,048 bytes) and q2.csv (4,096 bytes)."),
                      ("wc -l notes.txt", "42 notes.txt", "The file `notes.txt` has 42 lines."),
                      ("df -h", "Filesystem  Size  Used Avail Use%\n/dev/sda1   100G   72G   28G  72%", "The main disk is 100 GB with 72 GB used, leaving 28 GB free (72% full)."),
                      ("git log --oneline -3", "a1b2c3d Fix date parsing\n9f8e7d6 Add search index\n5c4b3a2 Update dependencies", "The three latest commits are 'Fix date parsing', 'Add search index' and 'Update dependencies'.")]:
    flow(f"Run `{cmd}` and tell me what it shows.", "shell.run", f'command="{cmd}"', True, res, rep)

for n in range(260):
    req, tool, call, ok, res, rep = R.choice(FLOWS)
    tl = [tool] + R.sample([t for t in ["web.search", "fs.read_file", "shell.run", "db.query", "app.list_tasks"] if t != tool], R.choice([0, 1, 2]))
    writes = tool in ("shell.run", "app.create_task")
    prop = f"I'd use `{tool}`.\n\nProposed call: `{tool}({call})`\n\n" + R.choice(["Shall I run it? I'll only report what it returns.", "Want me to proceed? Nothing has been done yet.", "Okay to run that?"])
    turns = [("user", req), ("assistant", prop), ("user", R.choice(APPROVE)), ("assistant", f"Running `{tool}` now. I'll tell you exactly what it returns."), ("user", f"[tool result from {tool}]\n{res}"), ("assistant", rep)]
    out.append(mk("tool_aware", "tool_flow", turns, f"mt_flow_{n}", "hard", "composed", sys_tools(tl), {"turns": 3}))

# ======================================================================= B. knowledge dialogs
import re
ns = {}
for fname, marker in (("gen_knowledge.py", "Q_WHAT = "), ("gen_knowledge2.py", "for area, topic, d, m, e, c in T:")):
    src = open(Path(__file__).parent / fname, encoding="utf-8").read().split(marker)[0]
    src = src.replace("save(", "(lambda *a, **k: None)(")
    sub = {}
    exec(compile(src, fname, "exec"), sub)
    ns[fname] = sub["T"]
TOPICS = ns["gen_knowledge.py"] + ns["gen_knowledge2.py"]
Q1 = ["What is {t}?", "Can you explain {t}?", "Tell me about {t}."]
Q2 = ["Can you give me an example?", "What's a real-life example?", "Could you make that concrete with an example?"]
Q3 = ["What do people often get wrong about it?", "Is there a common misconception?", "Anything people usually misunderstand here?"]
for area, topic, d, m, e, c in R.sample(TOPICS, 190):
    turns = [("user", R.choice(Q1).format(t=topic)), ("assistant", f"{d} {m}"), ("user", R.choice(Q2)), ("assistant", e), ("user", R.choice(Q3)), ("assistant", c)]
    out.append(mk("general_knowledge", "knowledge_dialog", turns, f"mt_k_{topic}", "medium", "authored", pick(R, SYS_GENERAL), {"turns": 3}))

# ======================================================================= C. plan revisions (computed)
TASKS = [("write the design doc", 4), ("review the pull requests", 2), ("prepare the client demo", 3), ("clean the test data", 2), ("record the training video", 3), ("update the budget sheet", 2), ("fix flaky tests", 3),
         ("plan the next sprint", 2), ("study for the certification", 4), ("write the monthly newsletter", 2), ("interview prep", 2), ("draft the proposal", 4), ("write release notes", 1), ("organise the shared drive", 2)]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def place(ts, cap):
    remaining = dict(cap)
    plan = {d: [] for d in cap}
    left = []
    for name, h in ts:
        for d in cap:
            if remaining[d] >= h:
                plan[d].append((name, h))
                remaining[d] -= h
                break
        else:
            left.append((name, h))
    return plan, left


def render(plan, left, cap):
    lines = [f"**{d}** ({cap[d]}h): " + ("; ".join(f"{t} ({h}h)" for t, h in plan[d]) if plan[d] else "nothing assigned") for d in cap]
    s = "\n".join(lines)
    if left:
        s += "\n\nDidn't fit: " + "; ".join(f"{t} ({h}h)" for t, h in left) + "."
    return s


for n in range(110):
    ts = R.sample(TASKS, R.choice([4, 5]))
    days = R.sample(DAYS, R.choice([3, 4, 5]))
    days = [d for d in DAYS if d in days]
    cap = {d: R.choice([2, 3, 4, 5]) for d in days}
    plan1, left1 = place(ts, cap)
    task_txt = "; ".join(f"{t} ({h}h)" for t, h in ts)
    cap_txt = ", ".join(f"{d} {cap[d]}h" for d in days)
    typ = R.randrange(2)
    if typ == 0 and len(days) > 2:
        gone = R.choice(days)
        cap2 = {d: h for d, h in cap.items() if d != gone}
        plan2, left2 = place(ts, cap2)
        t2 = [("user", f"Actually {gone} is gone, I have a conflict all day. Replan."),
              ("assistant", f"Without {gone}, the same priority-order rule gives:\n\n{render(plan2, left2, cap2)}\n\n" + ("That pushes out the items listed under 'Didn't fit'; you could move them to next week or shorten a task." if left2 else "Everything still fits, using the remaining days."))]
    else:
        d = R.choice(days)
        extra = R.choice([1, 2])
        cap2 = dict(cap)
        cap2[d] += extra
        plan2, left2 = place(ts, cap2)
        t2 = [("user", f"Good news, I can add {extra} more hour{'s' if extra > 1 else ''} on {d}. Update the plan."),
              ("assistant", f"With {cap2[d]}h on {d}, the plan becomes:\n\n{render(plan2, left2, cap2)}\n\n" + ("Something still doesn't fit, so consider splitting it." if left2 else "Everything fits now."))]
    turns = [("user", f"Plan my week. Hours: {cap_txt}. Tasks in priority order: {task_txt}."), ("assistant", f"Filling days in priority order:\n\n{render(plan1, left1, cap)}\n\nI can't add this to a calendar from here.")] + t2
    out.append(mk("jarvis_assistant", "plan_revision", turns, f"mt_plan_{n}", "medium", "computed", R.choice(SYS_NEXUS_NONE + [None]), {"turns": 2}))

save("multiturn", out)
