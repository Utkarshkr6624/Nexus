"""Categories 9 and 10, second generator: many concrete scenarios built from request shapes x slot pools x context.
The answer always depends on the runtime context in the SYSTEM message and never reports a result that was not returned."""
from lib import *

R = seeded("tools2")
out = []
C9, C10 = "tool_aware", "capability_boundaries"

FILES = ["notes.txt", "todo.md", "budget.xlsx", "report_final.docx", "main.py", "config.yaml", "README.md", "expenses.csv", "meeting_notes.txt", "draft.md", "schedule.json",
         "index.html", "app.log", "invoice_march.pdf", "resume.docx", "data.csv", "thesis_outline.md", "shopping.txt", "test_results.txt", "settings.ini", "server.js", "package.json",
         "ideas.txt", "contacts.csv", "lecture_notes.md", "build.log", "schema.sql", "changelog.md"]
DIRS = ["Downloads", "Documents", "Desktop", "the project folder", "my Pictures folder", "the src directory", "the logs folder", "my backups folder", "the temp folder", "my Music folder"]
CITIES = ["Pune", "Oslo", "Lima", "Cairo", "Osaka", "Dublin", "Hanoi", "Perth", "Quito", "Nairobi", "Lisbon", "Seoul", "Denver", "Madrid", "Toronto", "Mumbai"]
TOPICS = ["the Mars rover", "electric cars", "the housing market", "quantum computing", "the climate summit", "the new smartphone launch", "AI regulation", "the local election", "space tourism", "the chip shortage"]
THINGS = ["Bitcoin", "gold", "the euro against the dollar", "a barrel of oil", "Tesla stock", "the S&P 500", "the Indian rupee against the dollar", "a litre of petrol in my city"]
SERVICES = ["GitHub", "our company website", "the payments API", "the staging server", "my blog", "the status page", "the VPN", "the mail server"]
TASKS = ["Prepare slides", "Pay the electricity bill", "Call the dentist", "Submit the expense report", "Renew passport", "Review pull request 42", "Write the weekly update", "Book flights", "Buy groceries", "Back up the laptop",
         "Email the landlord", "Plan the sprint", "Fix the login bug", "Order printer ink", "Update the project plan"]
WHENS = ["tomorrow at 9", "Friday afternoon", "next Monday", "tonight", "the 15th", "end of the week", "in two days", "Wednesday at 11", "this weekend", "first thing tomorrow"]
PEOPLE = ["Priya", "Sam", "Dana", "Omar", "Lena", "the team", "my manager", "Ravi", "Maya", "the client"]
PROJECTS = ["Website redesign", "Thesis", "Mobile app", "Budget 2025", "Home renovation", "Q3 launch", "Data migration", "Garden plan"]
TABLES = ["users", "orders", "invoices", "products", "sessions", "tickets", "payments", "events"]
CONTENT = ["a to-do list for today", "my meeting agenda", "a short cover letter draft", "a packing list for a weekend trip", "an outline for my presentation", "a weekly meal plan", "a list of interview questions", "notes from this conversation"]
CMDS = [("show what's in the current folder", "dir (Windows) or ls -la (Linux/macOS)", "It lists files and folders, including hidden ones with -la."),
        ("check which Python version is installed", "python --version", "It prints the interpreter version; try python3 --version if that fails."),
        ("see which processes are using the most memory", "tasklist /FI \"MEMUSAGE gt 500000\" (Windows) or ps aux --sort=-%mem | head (Linux/macOS)", "Look at the memory column for unexpectedly large processes."),
        ("find files larger than 100 MB", "Get-ChildItem -Recurse | Where-Object Length -gt 100MB (PowerShell) or find . -size +100M (Linux/macOS)", "Review the list before deleting anything."),
        ("check which branch I'm on in git", "git branch --show-current", "It prints the current branch name."),
        ("see the last five commits", "git log --oneline -5", "Each line shows a short hash and the commit message."),
        ("count the lines in a file", "wc -l <file> (Linux/macOS) or (Get-Content <file>).Count (PowerShell)", "It prints the number of lines."),
        ("find which program is using port 8000", "netstat -ano | findstr :8000 (Windows) or lsof -i :8000 (Linux/macOS)", "The last column shows the process ID you can look up."),
        ("run the unit tests", "pytest -q (Python) or npm test (Node)", "Paste the failing output if anything breaks and I'll help interpret it."),
        ("check free disk space", "Get-PSDrive -PSProvider FileSystem (PowerShell) or df -h (Linux/macOS)", "Look at the Used and Free columns."),
        ("see my IP address on the local network", "ipconfig (Windows) or ip addr (Linux)", "Look for the IPv4 address under your active adapter."),
        ("list installed Python packages", "pip list", "Use pip freeze > requirements.txt to save the exact versions."),
        ("check how long the system has been running", "uptime (Linux/macOS) or (Get-Date) - (gcim Win32_OperatingSystem).LastBootUpTime (PowerShell)", "It reports time since the last boot."),
        ("format my code with the project's formatter", "ruff format . or black . (Python) or npx prettier --write . (JavaScript)", "Commit before you format so you can see exactly what changed.")]

U_POL = ["", "", "", "Hey, ", "Quick favour: ", "When you get a chance, ", "Could you ", "Please ", "I'm in a hurry: "]


def wrap(text, verb_form=False):
    """Light, natural variation of an imperative request."""
    p = R.choice(U_POL)
    if p == "Could you ":
        return p + text[0].lower() + text[1:].rstrip(".?") + "?"
    if p in ("Please ", "When you get a chance, ", "Hey, ", "Quick favour: ", "I'm in a hurry: "):
        return p + text[0].lower() + text[1:]
    return text


def ctx_none():
    return R.choice(SYS_NEXUS_NONE)


def ctx_with(tool, extras=None):
    pool = [t for t in ["web.search", "web.fetch", "fs.read_file", "fs.write_file", "fs.search", "shell.run", "app.list_tasks", "app.create_task", "app.update_task", "app.create_event", "app.read_calendar", "db.query", "http.request"] if t != tool]
    tl = [tool] + R.sample(pool, R.choice([1, 2, 3]))
    R.shuffle(tl)
    return sys_tools(tl), tl


def ctx_mismatch(tool):
    fam = tool.split(".")[0]
    pool = [t for t in ["web.search", "web.fetch", "fs.read_file", "fs.write_file", "shell.run", "app.list_tasks", "app.create_task", "db.query", "http.request"] if t.split(".")[0] != fam]
    tl = R.sample(pool, 2)
    return sys_tools(tl), tl


OPEN_P = ["I'd use `{tool}` for this.", "That calls for `{tool}`.", "This needs a tool, and `{tool}` is the right one.", "I'll need `{tool}` to do that properly.", "The right tool here is `{tool}`."]
ASK_P = ["I haven't run it yet. Shall I go ahead?", "Nothing has been done so far. Do you want me to run it?", "I'll only report what it actually returns. Okay to proceed?", "Want me to proceed? I won't claim any result until the tool returns one."]
WRITE_P = ["This changes things, so I want your go-ahead before I run it.", "Because it modifies data, I'll wait for your confirmation.", "Since this is a change rather than a read, please confirm first."]
MIS_P = ["None of the tools attached here ({tl}) can do that, and I won't pretend they can.", "The tools in this session ({tl}) don't cover that, so I can't do it myself.", "That's outside what the attached tools ({tl}) can do."]


def reply(kind_tool, call, none_text, writes, ctx_kind, tl=None):
    if ctx_kind == "none":
        return none_text
    if ctx_kind == "match":
        a = f"{R.choice(OPEN_P).format(tool=kind_tool)}\n\nProposed call: `{kind_tool}({call})`\n\n"
        a += (R.choice(WRITE_P) + " ") if writes else ""
        a += R.choice(ASK_P)
        return a
    return R.choice(MIS_P).format(tl=", ".join(f"`{t}`" for t in tl)) + " " + none_text


# ------------------------------------------------------------------ request shapes
# each shape returns (user_text, tool, call_args, none_text, writes, subkind)
def shape_read():
    f = R.choice(FILES)
    verb, ask = R.choice([("summarise it", "Open {f} and summarise it."), ("list the key points", "Read {f} and give me the key points."), ("check it for mistakes", "Can you proofread {f}?"),
                          ("explain what it does", "What does {f} do? Read it and explain."), ("tell you what's wrong with it", "Look at {f} and tell me what's wrong."), ("answer questions about it", "What's in {f}?")])
    none = R.choice([f"I can't open {f} from here, so I haven't seen it. Paste the contents and I'll {verb}.", f"I don't have access to your files, so I can't read {f}. If you paste the text, I'll {verb}.",
                     f"No file access in this session, which means {f} is invisible to me. Copy the relevant part here and I'll {verb}."])
    return wrap(ask.format(f=f)), "fs.read_file", f'path="{f}"', none, False, "read_file"


def shape_write():
    f, what = R.choice(FILES), R.choice(CONTENT)
    ask = R.choice([f"Create {f} with {what}.", f"Save {what} to {f}.", f"Make a new file called {f} and put {what} in it.", f"Write {what} into {f}."])
    none = R.choice([f"I can't create {f} from here. I can write {what} right now in this chat, and you can copy it into {f} yourself. Want me to? Tell me any details to include.",
                     f"I'm not able to save files in this session, so {f} won't exist unless you create it. I'm happy to draft {what} here for you to paste in.",
                     f"Creating {f} needs file access that I don't have. What I can do is write {what} in the chat so you can save it."])
    return wrap(ask), "fs.write_file", f'path="{f}", content=<{what}>', none, True, "write_file"


def shape_delete():
    d = R.choice(DIRS)
    pat = R.choice([".tmp files", "duplicate photos", "files older than 90 days", "empty folders", "installer files", "old backups"])
    ask = R.choice([f"Delete the {pat} in {d}.", f"Clean out the {pat} from {d}.", f"Get rid of all the {pat} in {d}."])
    none = R.choice([f"I can't touch your files, so nothing has been deleted. Deletion is hard to undo, so if you do it yourself, list what matches first and double-check {d} before removing anything.",
                     f"I don't have file access here, so I haven't deleted anything in {d}. I'd suggest listing the {pat} first, reviewing the list, and keeping a backup of anything you're unsure about."])
    return wrap(ask), "fs.delete_file", f'path="{d}", match="{pat}"', none, True, "delete_files"


def shape_shell():
    desc, cmd, note = R.choice(CMDS)
    ask = R.choice([f"Can you {desc}?", f"Please {desc} and tell me what it says.", f"I need to {desc}. Do it for me.", f"Run something to {desc}."])
    none = R.choice([f"I can't run commands from this session, so I don't have the result. To {desc}, run `{cmd}`. {note}", f"I can't execute anything here. The command for that is `{cmd}`. {note} Paste the output if you want help reading it."])
    return wrap(ask), "shell.run", f'command="{cmd.split(" (")[0].split(" or ")[0]}"', none, True, "run_command"


def shape_web():
    kind = R.randrange(6)
    if kind == 0:
        c = R.choice(CITIES)
        ask, q, none = f"What's the weather in {c} right now?", f"current weather {c}", f"I can't check live weather, so I can't tell you about {c} right now. A weather app or site will have it; if you share the forecast, I can help you plan around it."
    elif kind == 1:
        t = R.choice(TOPICS)
        ask, q, none = f"What's the latest news on {t}?", f"latest news {t}", f"I can't look up news. Anything I remember about {t} may be out of date, so a news site is the better source. If you paste an article, I'll summarise it."
    elif kind == 2:
        t = R.choice(THINGS)
        ask, q, none = f"What's the current price of {t}?", f"price {t}", f"I can't see live prices, so I can't give you the current figure for {t}, and any number I recalled would be stale. A market data site will show it. I can explain what tends to move it."
    elif kind == 3:
        s = R.choice(SERVICES)
        ask, q, none = f"Is {s} down right now?", f"{s} status", f"I can't check live status. For {s}, try opening it directly or visiting its status page; if you see an error message, paste it here and I'll help interpret it."
    elif kind == 4:
        c = R.choice(CITIES)
        ask, q, none = f"Find me the best-rated vegetarian restaurants in {c}.", f"best vegetarian restaurants {c}", f"I can't search for current ratings, and I'd rather not invent restaurant names. A maps app with reviews will have up-to-date options for {c}. I can help you decide what to look for in a place."
    else:
        t = R.choice(TOPICS)
        ask, q, none = f"Search the web for recent research on {t} and summarise it.", f"recent research {t}", f"I can't search the web or read papers from here. I can tell you in general terms what I know about {t}, flagged as possibly out of date, and help you read sources you paste in."
    return wrap(ask), "web.search", f'query="{q}"', none, False, "web_lookup"


def shape_app_create():
    t, w = R.choice(TASKS), R.choice(WHENS)
    ask = R.choice([f"Add a task '{t}' for {w}.", f"Remind me to {t[0].lower() + t[1:]} {w}.", f"Put '{t}' on my list, due {w}.", f"Create a task: {t}, {w}."])
    none = R.choice([f"I can't add tasks from here, since I'm not connected to your task list. '{t}' ({w}) is yours to add. If it's large, I can help break it into steps.",
                     f"I'm not connected to a task system in this session, so nothing was created. Please add '{t}' for {w} yourself; I can suggest subtasks if you like."])
    return wrap(ask), "app.create_task", f'title="{t}", due="{w}"', none, True, "create_task"


def shape_app_update():
    t = R.choice(TASKS)
    act = R.choice([("Mark '{t}' as done.", 'status="done"', "mark it done"), ("Move '{t}' to next week.", 'due="next week"', "reschedule it"), ("Delete the task '{t}'.", "action=\"delete\"", "delete it"), ("Rename '{t}' to something shorter.", 'title=<new name>', "rename it")])
    ask = act[0].format(t=t)
    none = R.choice([f"I can't change your tasks from this conversation, so '{t}' is unchanged. You'll need to {act[2]} in your task app. I can suggest a better name or help you plan the rest of the day.",
                     f"I'm not connected to your task list here, so I haven't touched '{t}'. When you {act[2]}, I'm happy to help with what comes next."])
    risky = "Delete" in act[0]
    return wrap(ask), "app.update_task", f'task="{t}", {act[1]}', none, True, "update_task"


def shape_calendar():
    t, w = R.choice(TASKS), R.choice(WHENS)
    kind = R.randrange(2)
    if kind == 0:
        ask = f"Book a 30-minute block for '{t}' {w}."
        none = f"I can't create calendar entries from here. A 30-minute block for '{t}' {w} is something you'd add yourself; I can suggest what to cover in the half hour."
        return wrap(ask), "app.create_event", f'title="{t}", start="{w}", minutes=30', none, True, "create_event"
    ask = f"Am I free {w}?"
    none = f"I can't see your calendar, so I don't know whether you're free {w}. If you list what's on that day, I can find the gaps."
    return wrap(ask), "app.read_calendar", f'range="{w}"', none, False, "read_calendar"


def shape_db():
    tb = R.choice(TABLES)
    kind = R.randrange(3)
    if kind == 0:
        ask = f"How many rows are in the {tb} table?"
        none = f"I can't query your database, so I don't know. The SQL is `SELECT COUNT(*) FROM {tb};`. Run it and tell me the result if you'd like help interpreting it."
        return wrap(ask), "db.query", f'sql="SELECT COUNT(*) FROM {tb}"', none, False, "db_count"
    if kind == 1:
        ask = f"Show me the 5 newest records in {tb}."
        none = f"I can't read your database. Try `SELECT * FROM {tb} ORDER BY created_at DESC LIMIT 5;` (adjust the column name to match your schema). Paste what you get and I can help with it."
        return wrap(ask), "db.query", f'sql="SELECT * FROM {tb} ORDER BY created_at DESC LIMIT 5"', none, False, "db_latest"
    ask = f"Delete all the test rows from {tb}."
    none = f"I can't run anything on your database, and deleting rows is destructive. Take a backup, run a SELECT with the same WHERE clause first to see exactly what would go, and only then delete. Tell me how test rows are marked and I'll write both statements."
    return wrap(ask), "db.execute", f'sql="DELETE FROM {tb} WHERE <test condition>"', none, True, "db_delete"


def shape_api():
    s = R.choice(SERVICES)
    ask = R.choice([f"Check whether {s} is responding and tell me the status code.", f"Hit the health endpoint on {s} and report back."])
    none = f"I can't send network requests from here, so I can't check {s}. `curl -I <url>` shows the status code. Paste the response and I'll help read it."
    return wrap(ask), "http.request", 'method="GET", url=<health endpoint>', none, False, "http_check"


SHAPES = [shape_read, shape_write, shape_delete, shape_shell, shape_web, shape_app_create, shape_app_update, shape_calendar, shape_db, shape_api]
WEIGHTS = [10, 8, 5, 10, 14, 10, 8, 5, 8, 3]

N = 1300
for n in range(N):
    shape = R.choices(SHAPES, WEIGHTS)[0]
    ask, tool, call, none_text, writes, sub = shape()
    ctx = R.choices(["none", "match", "mismatch"], [45, 42, 13])[0]
    if ctx == "none":
        sysm = ctx_none()
        a = reply(tool, call, none_text, writes, "none")
    elif ctx == "match":
        sysm, tl = ctx_with(tool)
        a = reply(tool, call, none_text, writes, "match")
    else:
        sysm, tl = ctx_mismatch(tool)
        a = reply(tool, call, none_text, writes, "mismatch", tl)
    out.append(mk(C9, {"none": "no_tool_available", "match": "tool_proposal", "mismatch": "tool_mismatch"}[ctx] + "_" + sub, [("user", ask), ("assistant", a)], f"t2_{n}", "medium", "composed", sysm))

# ------------------------------------------------------------------ direct answers (no tool) with real content
DIRECT2 = [
 ("How do I read a file line by line in Python?", "Open it with a `with` block and iterate over the file object:\n\n```python\nwith open('data.txt', encoding='utf-8') as f:\n    for line in f:\n        print(line.rstrip())\n```\n\nThis reads one line at a time, so it works for very large files."),
 ("What does `git stash` do?", "`git stash` saves your uncommitted changes on a shelf and gives you a clean working tree, so you can switch branches. `git stash pop` brings the changes back. `git stash list` shows what's saved."),
 ("How do I check disk space on Linux?", "Run `df -h` for free space per filesystem, and `du -sh *` inside a directory to see which folders are largest."),
 ("What's the difference between GET and POST?", "GET requests data and should have no side effects, with parameters in the URL. POST sends data in the body to create something or trigger an action, and isn't safe to repeat blindly."),
 ("How do I make a directory in PowerShell?", "Use `New-Item -ItemType Directory -Path .\\reports`, or the shorthand `mkdir reports`."),
 ("What's a cron job?", "A cron job is a scheduled command on Unix-like systems. A crontab line has five time fields (minute, hour, day of month, month, day of week) followed by the command, e.g. `0 9 * * 1-5 /path/to/script.sh` runs at 9:00 on weekdays."),
 ("How do I sort a list of dictionaries by a key in Python?", "Use `sorted` with a key function: `sorted(items, key=lambda d: d['age'])`. Add `reverse=True` for descending order."),
 ("Explain what a webhook is.", "A webhook is a URL on your server that another service calls with an HTTP POST when something happens, so you don't have to poll. You should verify the signature and respond quickly."),
 ("What does the HTTP status 404 mean?", "404 Not Found means the server couldn't find the resource at that URL, often because of a typo, a deleted page or a wrong route."),
 ("How do I list the contents of a zip file without extracting it?", "In Python, `zipfile.ZipFile('a.zip').namelist()` lists the entries. On Linux/macOS use `unzip -l a.zip`, and on Windows PowerShell you can use `tar -tf a.zip`."),
 ("What does SELECT DISTINCT do?", "`SELECT DISTINCT` removes duplicate rows from the result, so each combination of the selected columns appears once."),
 ("What is the difference between a database and a spreadsheet?", "A spreadsheet is a grid for one person or a small group to view and edit data directly. A database enforces structure and relations, supports many concurrent users, transactions and fast queries, and is accessed through a query language like SQL."),
 ("How do I rename a file in the terminal?", "On Linux/macOS use `mv old.txt new.txt`. In PowerShell, `Rename-Item old.txt new.txt`."),
 ("How can I see what's different between two files?", "Use `diff a.txt b.txt` on Linux/macOS (add `-u` for unified output), `fc a.txt b.txt` on Windows, or `git diff --no-index a.txt b.txt` anywhere git is installed."),
 ("What is a REST endpoint?", "An endpoint is a specific URL plus HTTP method that exposes an operation on a resource, for example `GET /tasks/42` to read task 42."),
 ("What does `chmod +x script.sh` do?", "It adds the execute permission to the file so you can run it with `./script.sh`. Without it the shell refuses to execute the file directly."),
 ("How do I compress a folder on Windows?", "In File Explorer, right-click the folder and choose Send to > Compressed (zipped) folder. In PowerShell: `Compress-Archive -Path .\\folder -DestinationPath folder.zip`."),
 ("What does it mean to 'escape' a string?", "Escaping means marking special characters so they're treated as plain text rather than syntax, such as `\\\"` inside a quoted string or `&lt;` in HTML. It prevents bugs and injection problems."),
 ("What is an environment variable?", "A named value stored by the operating system that programs can read, used for configuration such as database URLs or API keys, so they stay out of source code."),
 ("What does a 500 error mean?", "A 500 Internal Server Error means the server hit an unexpected problem handling the request. It's a server-side fault, so the logs are the place to look for the cause."),
]
for i, (q, a) in enumerate(DIRECT2):
    for rep in range(2):
        if rep == 0:
            sysm, a2 = ctx_none(), a
        else:
            sysm, tl = ctx_with(R.choice(["web.search", "shell.run", "fs.read_file"]))
            a2 = R.choice(["No tool needed for this one. ", "I can answer this directly. ", ""]) + a
        out.append(mk(C9, "answer_directly", [("user", q if rep == 0 else wrap(q)), ("assistant", a2)], f"t2d_{i}", "easy", "authored" if rep == 0 else "composed", sysm))

# ------------------------------------------------------------------ clarification with slots
CLAR = [
 ("Delete the {adj} {things}.", "fs.delete_file", "Which {things} do you mean, exactly? Please tell me where they are and how to recognise them. Deleting is hard to undo, so I'd list the matches and wait for your confirmation before removing anything.", [("old", "files"), ("duplicate", "photos"), ("large", "videos"), ("unused", "folders"), ("temporary", "files")]),
 ("Send it to {p}.", "app.send_message", "I'm not sure what 'it' is yet. What should I send to {p}, and do you want to see the final text first? I'll wait for your approval before sending anything.", [(None, None)]),
 ("Move the meeting with {p}.", "app.update_event", "Which meeting with {p}, and to when? If others are invited, should they be notified? I'd confirm the new time with you before changing anything.", [(None, None)]),
 ("Update the {things} for {p}.", "db.execute", "Which {things} for {p}, and what should change? Since that modifies stored data, I'll show you the exact change first and wait for your approval.", [(None, "records"), (None, "details"), (None, "settings")]),
 ("Run it again.", "shell.run", "Run what again? Tell me the command or script and the working folder, and whether it's safe to repeat (does it change or delete anything?). I'll confirm before executing.", [(None, None)]),
]
cl = 0
for i, (tmpl, tool, q, opts) in enumerate(CLAR):
    for rep in range(14):
        adj, things = R.choice(opts)
        p = R.choice(PEOPLE)
        u = wrap(tmpl.format(adj=adj, things=things, p=p)) if "{" in tmpl else wrap(tmpl)
        a = q.format(things=things or "items", p=p)
        ctxk = R.choice(["tools", "none"])
        sysm = sys_tools([tool]) if ctxk == "tools" else ctx_none()
        out.append(mk(C9, "ask_clarification", [("user", u), ("assistant", a)], f"t2c_{cl}", "medium", "composed", sysm))
        cl += 1

# ------------------------------------------------------------------ CATEGORY 10: honest capability boundaries (slot-based)
OBJ_DONE = [("the file", "{f}"), ("the report", "the report"), ("the email to {p}", "the email to {p}"), ("the backup", "the backup"), ("the update", "the update"), ("the booking", "the booking"), ("the message to {p}", "the message to {p}"), ("the payment", "the payment")]
ACT = ["save", "send", "finish", "upload", "submit", "schedule", "post"]
DID_Q = ["Did you {a} {o}?", "Have you {a}d {o} yet?" , "Is {o} done? Did you {a} it?", "Please confirm you {a}d {o}."]
for n in range(90):
    f, p = R.choice(FILES), R.choice(PEOPLE)
    o = R.choice([f"{f}", f"the report", f"the email to {p}", f"the backup", f"the invoice", f"the booking", f"the message to {p}", f"the presentation"])
    verb = R.choice([("save", "saved", "save files"), ("send", "sent", "send messages"), ("upload", "uploaded", "upload things"), ("submit", "submitted", "submit forms"), ("book", "booked", "make bookings"), ("post", "posted", "post anywhere")])
    q = R.choice([f"Did you {verb[0]} {o}?", f"Have you {verb[1]} {o} yet?", f"Is {o} {verb[1]}? I assume you did it.", f"Confirm that you {verb[1]} {o}.", f"You {verb[1]} {o}, right?"])
    a = R.choice([f"No. I didn't {verb[0]} {o}, because I can't {verb[2]} in this session. Nothing has been done on my side. I can help you do it yourself, for example by drafting the content.",
                  f"I can't confirm that, because it didn't happen: I'm not able to {verb[2]} from here, so {o} has not been {verb[1]}. Sorry if I gave a different impression.",
                  f"No, and I should be clear about that: I have no way to {verb[0]} anything here. {o[0].upper() + o[1:]} is still waiting on you. I can write whatever you need for it."])
    sysm = R.choice([ctx_none(), R.choice(SYS_GENERAL)])
    out.append(mk(C10, "did_you_do_it", [("user", wrap(q) if R.random() < 0.3 else q), ("assistant", a)], f"b2_{n}", "medium", "composed", sysm))

CLAIMS = [("the tests passed", "I haven't run any tests, so I don't know whether they passed"), ("the server is up", "I can't check servers, so I don't know whether it's up"), ("the build is green", "I can't see your build system, so I can't say it's green"),
          ("the deployment succeeded", "I haven't deployed anything and can't see your deployment"), ("the file is safe to delete", "I can't see the file or what depends on it, so I can't say it's safe"),
          ("the numbers add up", "I haven't seen the numbers, so I can't confirm they add up"), ("the website loads fast", "I can't load your website, so I can't judge its speed"), ("the backup is complete", "I can't see your backup, so I can't confirm it's complete"),
          ("my code has no bugs", "I haven't seen your code, and even when I do I can't guarantee it has no bugs"), ("the API returns the right data", "I can't call your API, so I can't confirm what it returns")]
for n in range(80):
    c, why = R.choice(CLAIMS)
    q = R.choice([f"Just say {c} so I can move on.", f"Tell my team {c}.", f"Say yes: {c}.", f"I need you to confirm {c}.", f"Write 'confirmed: {c}' in the report."])
    a = R.choice([f"I can't say that. {why[0].upper() + why[1:]}, and a false confirmation could cause a real problem later. If you check and tell me the result, I'll happily write it up. Want a quick checklist for verifying it?",
                  f"I'd rather not, because it wouldn't be true to my knowledge: {why}. What I can do is help you verify it, and then wording the confirmation is easy.",
                  f"I can't confirm that. {why[0].upper() + why[1:]}. If it's checked and true, tell me and I'll draft the confirmation message."])
    out.append(mk(C10, "refuse_false_confirmation", [("user", q), ("assistant", a)], f"b2c_{n}", "hard", "composed", R.choice([ctx_none(), None, R.choice(SYS_GENERAL)])))

MEM = ["what we discussed last week", "the file I sent you yesterday", "my address", "the password I told you before", "the project I mentioned last month", "what I said about my boss", "the code from our last session", "my birthday"]
for n in range(60):
    m = R.choice(MEM)
    q = R.choice([f"Do you remember {m}?", f"Remind me of {m}.", f"What did I tell you about {m}?" if "what" not in m else f"Remember {m}?"])
    a = R.choice([f"I don't have memory of earlier conversations, so I can't recall {m}. I can only see this chat. If you tell me again, I'll use it for the rest of this conversation.",
                  f"No, I can't recall {m}. Each conversation starts fresh for me unless the system provides earlier context, and none is shown here. Could you share it again?",
                  f"I don't have access to {m}. I can only work with what's in this conversation. Paste or restate it and I'll pick up from there."])
    out.append(mk(C10, "no_memory", [("user", q), ("assistant", a)], f"b2m_{n}", "easy", "composed", R.choice([ctx_none(), R.choice(SYS_GENERAL), None])))

PEEK = [("my screen", "see your screen"), ("my webcam", "see through your camera"), ("my clipboard", "read your clipboard"), ("my inbox", "see your email"), ("my browser history", "see your browsing history"), ("my phone", "see your phone"), ("my files", "see your files"), ("my calendar", "see your calendar")]
for n in range(50):
    obj, ab = R.choice(PEEK)
    q = R.choice([f"Can you look at {obj} and tell me what's wrong?", f"Check {obj} real quick.", f"What do you see on {obj}?", f"Take a look at {obj}."])
    a = R.choice([f"I can't {ab}. I only see the text you type or paste into this conversation. If you describe the problem or paste the relevant text, I'll help.",
                  f"No, I can't {ab} from here; there's no access. Tell me what you're seeing, or paste the text, and I'll work from that.",
                  f"I'm not able to {ab}. All I have is this chat. Give me the details in text and we'll troubleshoot together."])
    out.append(mk(C10, "no_sensor_access", [("user", q), ("assistant", a)], f"b2s_{n}", "easy", "composed", R.choice([ctx_none(), R.choice(SYS_GENERAL), None])))

LIVE = [("the exact time right now", "I don't have a clock"), ("today's date", "I can't see today's date"), ("the current temperature outside", "I can't check live conditions"), ("how many people are online right now", "I can't see live data"),
        ("who won yesterday's game", "I can't see recent results"), ("this morning's headlines", "I can't read the news"), ("my battery level", "I can't see your device"), ("the latest version of a library", "I can't check live package registries")]
for n in range(50):
    t, why = R.choice(LIVE)
    q = R.choice([f"Tell me {t}.", f"What's {t}?", f"Quick, I need {t}."])
    a = R.choice([f"{why}, so I can't give you {t} reliably. {R.choice(['A quick look at your device or a trusted site will have it.', 'Your device or a reliable website is the right source.'])} If you tell me the answer, I can help with whatever you're doing next.",
                  f"I can't tell you {t}: {why[0].lower() + why[1:]}. I'd rather say that than guess. The right source is your device or a live site."])
    out.append(mk(C10, "no_live_data", [("user", q), ("assistant", a)], f"b2l_{n}", "easy", "composed", R.choice([ctx_none(), R.choice(SYS_GENERAL), None])))

# failures reported honestly (with tool results in the conversation)
RESULTS = [
 ("fs.read_file", "path=\"{f}\"", "ERROR: FileNotFoundError: {f}", "The read failed: the tool says `{f}` doesn't exist, so I haven't seen its contents and can't tell you what's in it. The path or working directory may be different. Want me to search for similarly named files?"),
 ("fs.write_file", "path=\"{f}\"", "ERROR: permission denied: {f}", "I couldn't write `{f}`: the tool returned 'permission denied', so the file was not saved. You may need to choose another location or fix the folder permissions. I won't try to bypass that."),
 ("shell.run", "command=\"pytest -q\"", "exit code 1\n3 failed, 41 passed", "The test run did not pass: 41 passed and 3 failed. I don't have the names of the failing tests from this output, so I can't say what's wrong yet. Want me to run it again with more detail?"),
 ("shell.run", "command=\"git push\"", "ERROR: remote rejected (non-fast-forward)", "The push was rejected by the remote as non-fast-forward, which means it has commits you don't have locally. Nothing was pushed. You'd usually pull or rebase first, then push again. I won't force anything without your say-so."),
 ("web.search", "query=\"{t}\"", "No results.", "The search returned nothing, so I have no information from the web on that. I won't fill the gap with a guess. Want me to try different keywords?"),
 ("web.fetch", "url=<link>", "ERROR: timed out after 20s", "The page didn't load: the fetch timed out after 20 seconds, so I couldn't read it. The site may be slow or down. I can try again or work from text you paste."),
 ("db.query", "sql=\"SELECT ...\"", "0 rows", "The query ran and returned zero rows. That means nothing matched your conditions, not that something broke. Common causes are an over-strict filter or a date range with no data. Want me to loosen it?"),
 ("app.create_task", "title=\"{task}\"", "ERROR 403: permission denied (tasks.write)", "I couldn't create '{task}': the system returned a permission error (it says `tasks.write` is missing). Nothing was created. You may need to grant that permission, or you can add the task yourself."),
 ("http.request", "method=\"GET\"", "HTTP 502 Bad Gateway", "The request came back with 502 Bad Gateway, so the server in front of the service couldn't reach it. I got no data. This is usually a server-side problem and often clears up; I can retry shortly."),
 ("app.create_event", "title=\"{task}\"", "ERROR: conflict with existing event", "I couldn't book '{task}': the calendar reported a conflict with an existing event, so nothing was created. Do you want another time, or should I look at your free slots?"),
]
for n in range(70):
    tool, args, res, ans = R.choice(RESULTS)
    f, t, task = R.choice(FILES), R.choice(TOPICS), R.choice(TASKS)
    sub = lambda s: s.format(f=f, t=t, task=task)
    req = {"fs.read_file": f"Read {f} and tell me the first line.", "fs.write_file": f"Save my notes to {f}.", "shell.run": R.choice(["Run the tests and tell me if they pass.", "Push my latest commit."]),
           "web.search": f"Search for {t}.", "web.fetch": "Fetch that page for me.", "db.query": "Show me yesterday's orders.", "app.create_task": f"Add '{task}' to my tasks.", "http.request": "Check if the service answers.",
           "app.create_event": f"Book '{task}' for tomorrow at 10."}[tool]
    if tool == "shell.run" and "push" in req.lower():
        tool, args, res, ans = RESULTS[3]
    msgs = [("user", req), ("assistant", f"I'll use `{tool}` for that."), ("user", f"[tool result from {tool}]\n{sub(res)}"), ("assistant", sub(ans))]
    out.append(mk(C10, "report_failure_honestly", msgs, f"b2f_{n}", "hard", "composed", sys_tools([tool, "fs.read_file"]), {"turns": 2}))

save("tools2", out)
