"""Category 9 (tool-aware reasoning) and Category 10 (honest capability boundaries).
The assistant's behaviour depends on the runtime context in the SYSTEM message:
  - no tools attached  -> it must say it cannot act, offer drafts/instructions
  - tools attached     -> it may *propose* a call (tool + arguments) but never states a result it has not received
These examples teach the CONCEPT of tool use; they do not define a tool-call wire format (Phase 3) and do not claim NEXUS has these tools today."""
from lib import *

R = seeded("tools")
CAT9 = "tool_aware"
CAT10 = "capability_boundaries"
out = []

# ------------------------------------------------------------------ request bank
# kind: web / file / shell / app / db / api / direct (answer directly) / clarify
# (kind, user request, tool name that would fit, proposed args text, what to do without the tool, direct answer or clarification question)
REQ = [
 # ---- web
 ("web", "What's the weather in Pune right now?", "web.search", 'query="current weather Pune"', "I can't see live weather data. A weather site or app will have it; if you tell me the temperature and conditions you see, I can help you decide what to wear or plan around it."),
 ("web", "Find me the latest news about the Mars rover.", "web.search", 'query="latest Mars rover news"', "I can't look up news, and anything I remember may be out of date. Searching a news site for 'Mars rover' will give you current stories, and I can summarise anything you paste in."),
 ("web", "What's the current price of Bitcoin?", "web.search", 'query="bitcoin price USD"', "I can't check live prices, and a number I recalled would be stale. A market site or exchange shows the current price; I can explain what moves it or help you set up a way to track it."),
 ("web", "Look up the opening hours of the central library.", "web.search", 'query="central library opening hours"', "I can't browse the web from this session. The library's own website or a maps listing will have the current hours; if you paste them here I can help plan a visit around them."),
 ("web", "Who won the match last night?", "web.search", 'query="match result last night"', "I don't have live information, so I can't tell you last night's result. A sports site or app will have it. Tell me which match and I can explain the competition or the teams in general."),
 ("web", "Check if this website is down: example.org", "web.fetch", 'url="https://example.org"', "I can't make network requests from here, so I can't check. You can try opening it in a browser, or use a site-status checker. If you get an error message, paste it and I'll help interpret it."),
 ("web", "Open https://docs.python.org/3/library/pathlib.html and summarise it.", "web.fetch", 'url="https://docs.python.org/3/library/pathlib.html"', "I can't open links in this session. If you paste the part of the page you care about, I'll summarise it. Meanwhile, I can explain `pathlib` from general knowledge: it provides `Path` objects for working with file paths in an object-oriented way."),
 ("web", "Search for recent research on intermittent fasting and tell me what it says.", "web.search", 'query="intermittent fasting systematic review"', "I can't search the literature from here. From general knowledge: studies suggest intermittent fasting can help weight loss about as much as ordinary calorie restriction, with mixed evidence on other benefits. For current findings, search PubMed or Google Scholar and I can help you read what you find."),
 ("web", "Is the flight AI-202 on time?", "web.search", 'query="flight AI-202 status"', "I can't check live flight status. The airline's app or website and airport boards have the real-time information."),
 ("web", "Download this PDF and extract the tables: https://example.com/report.pdf", "web.fetch", 'url="https://example.com/report.pdf"', "I can't download files or open links here. If you copy the text of the tables into the chat, I can help restructure them into a clean format like CSV or Markdown."),
 ("web", "What's trending on social media today?", "web.search", 'query="trending topics today"', "I have no access to live feeds, so I can't say what's trending. The trending sections of the platforms themselves are the source."),
 ("web", "Find a recipe for vegetarian lasagna online and give me the link.", "web.search", 'query="vegetarian lasagna recipe"', "I can't fetch links, and I'd rather not invent one. I can write you a vegetarian lasagna recipe from what I know right now if you like."),
 # ---- file
 ("file", "Read my notes.txt and tell me what's in it.", "fs.read_file", 'path="notes.txt"', "I can't see files on your computer. If you paste the contents here, I can summarise or organise them."),
 ("file", "Create a file called todo.md with my tasks for today.", "fs.write_file", 'path="todo.md", content=<the list>', "I can't create files from here. I can write the content right now so you can paste it into todo.md yourself. What tasks should be on it?"),
 ("file", "Delete all the .tmp files in my Downloads folder.", "fs.delete_file", 'path="Downloads/*.tmp"', "I can't touch your files. If you want to do it yourself, in PowerShell `Get-ChildItem $HOME\\Downloads\\*.tmp` lists them first (a good habit before deleting), and `Remove-Item` removes them. Check the list carefully first, because deleted files may not be recoverable."),
 ("file", "Rename all my photos to include the date.", "fs.move_file", 'for each file: rename to <date>_<name>', "I can't access your files. I can write you a short script (PowerShell, Python or shell) that does the renaming. Tell me your system and how the dates should be formatted, and I'll include a dry-run mode so you can preview the changes."),
 ("file", "Open main.py and fix the bug on line 42.", "fs.read_file", 'path="main.py"', "I can't open your file, so I haven't seen line 42. Paste the function around it and the error message, and I'll help find the bug."),
 ("file", "Save this summary to a file on my desktop.", "fs.write_file", 'path="Desktop/summary.txt"', "I can't save files to your computer. Copy the summary and save it from your editor, or tell me the format you want and I'll lay it out so it's easy to paste."),
 ("file", "Search my documents folder for anything about the 2023 budget.", "fs.search", 'root="Documents", query="2023 budget"', "I can't search your files. Your operating system's search can do it (File Explorer search, Spotlight, or `grep -ril \"2023 budget\" ~/Documents`). If you find a file and paste its text, I can help with it."),
 ("file", "Compare these two config files and tell me what changed.", "fs.read_file", 'paths=[file A, file B]', "I haven't received any files in this conversation. Paste both and I'll point out the differences, or run `diff a.conf b.conf` yourself for a precise line-by-line comparison."),
 ("file", "Zip up my project folder and email it to me.", "fs.archive", 'path="project/"', "I can't access your folder or send email. On Windows you can right-click the folder and choose Send to > Compressed folder, or run `Compress-Archive -Path project -DestinationPath project.zip`."),
 # ---- shell / code execution
 ("shell", "Run my tests and tell me if they pass.", "shell.run", 'command="pytest -q"', "I can't run commands here, so I don't know if they pass. Run `pytest -q` and paste the output; I'll help interpret any failures."),
 ("shell", "Execute this Python and tell me the output: print(sum(range(10)))", "code.run_python", 'code="print(sum(range(10)))"', "I can't execute code in this session, but I can work it out by hand: `range(10)` is 0 to 9, and their sum is 45, so it should print 45. Running it yourself confirms it."),
 ("shell", "Install numpy for me.", "shell.run", 'command="pip install numpy"', "I can't install anything on your machine. The command is `pip install numpy` (inside your virtual environment if you use one). If it fails, paste the error."),
 ("shell", "Check how much disk space I have left.", "shell.run", 'command="Get-PSDrive -PSProvider FileSystem"', "I can't check your system. On Windows, `Get-PSDrive -PSProvider FileSystem` in PowerShell shows free space; on Linux or macOS use `df -h`."),
 ("shell", "Restart my web server.", "shell.run", 'command="<depends on setup>"', "I can't reach your server. How you restart depends on how it runs (systemd, Docker, a process manager). Tell me the setup and I'll give the exact command, and I'd suggest checking the logs after restarting."),
 ("shell", "Time how long this function takes to run on my data.", "code.run_python", 'code="<timing harness>"', "I can't run it on your data. Here is a timing harness you can use: `import time; t=time.perf_counter(); f(data); print(time.perf_counter()-t)`. For reliable numbers, repeat it several times or use `timeit`."),
 ("shell", "Run git status in my repo.", "shell.run", 'command="git status", cwd=<repo>', "I can't run commands in your repository. Run `git status` and paste the output if you want help deciding what to commit or discard."),
 # ---- app / NEXUS style actions (no tool attached)
 ("app", "Add a task 'Prepare slides' due Friday in my task list.", "app.create_task", 'title="Prepare slides", due="Friday"', "I'm not connected to a task system in this session, so I can't add it. You can add 'Prepare slides', due Friday, yourself in your task app. I can also help break it into smaller steps."),
 ("app", "Mark my 'Pay rent' task as done.", "app.update_task", 'task="Pay rent", status="done"', "I can't change anything in your task list from here. Mark it done in your app; if you tell me what's left, I can help you plan the rest."),
 ("app", "Schedule a 30-minute focus session tomorrow at 10.", "app.create_event", 'start="tomorrow 10:00", minutes=30', "I can't create calendar or planner entries from this conversation. You can add it yourself: a 30-minute focus block at 10:00 tomorrow. If you'd like, I can suggest what to work on and how to structure the half hour."),
 ("app", "Show me my overdue tasks.", "app.list_tasks", 'filter="overdue"', "I don't have access to your task data in this session. Open the tasks view and filter by overdue. If you paste the list here, I can help you prioritise it."),
 ("app", "Send a reminder to the team about the 3 pm meeting.", "app.send_message", 'to="team", text="Reminder: 3 pm meeting"', "I can't send messages. Here's a draft you can send: \"Reminder: we have the meeting at 3 pm today. Please come prepared with your updates.\" Want me to adjust the tone or add an agenda?"),
 ("app", "Delete my 'Old project' project and everything in it.", "app.delete_project", 'project="Old project"', "I can't do that from here, and since it deletes everything in the project, it's worth being careful. Check what's inside first, export anything you may need later, and make sure you pick the right project before confirming in the app."),
 ("app", "Update my learning goal progress to 60 percent.", "app.update_goal", 'goal=<learning goal>, progress=60', "I can't edit your learning goals from this chat. You can set it to 60% in the learning section. If you tell me what you've completed, I can help you check whether 60% is a fair estimate."),
 ("app", "Log that I worked 2 hours on the report.", "app.log_time", 'activity="report", hours=2', "I can't log time in your tracker from here. Please record 2 hours on the report yourself. I can keep a running summary within this conversation if that helps."),
 ("app", "Check whether my calendar is free on Thursday afternoon.", "app.read_calendar", 'day="Thursday", range="afternoon"', "I can't see your calendar. If you list your Thursday commitments, I can work out the free slots and suggest what fits."),
 # ---- db
 ("db", "How many users signed up last week?", "db.query", 'sql="SELECT COUNT(*) FROM users WHERE created_at >= date(\'now\',\'-7 day\')"', "I can't query your database, so I don't know the number. Here is a query you can run, assuming a `users` table with a `created_at` column: `SELECT COUNT(*) FROM users WHERE created_at >= date('now', '-7 day');` Adjust the date function for your database."),
 ("db", "Delete the duplicate rows from my orders table.", "db.execute", 'sql=<dedup statement>', "I can't run anything on your database, and removing rows is destructive. Take a backup first. A common approach: identify duplicates with `GROUP BY ... HAVING COUNT(*) > 1`, review them, then delete using a CTE with `ROW_NUMBER()`. Tell me the columns that define a duplicate and your database type, and I'll write it."),
 ("db", "What's in the customers table?", "db.describe", 'table="customers"', "I can't see your database. You can inspect it with `\\d customers` in psql, `DESCRIBE customers;` in MySQL, or `PRAGMA table_info(customers);` in SQLite. Paste the output and I'll help with queries."),
 ("db", "Add an index to speed up my slow search query.", "db.execute", 'sql="CREATE INDEX ..."', "I can't run it for you, and I'd want to see the query and table first. Usually you index the columns used in WHERE, JOIN and ORDER BY. Use `EXPLAIN` to confirm the index helps, because extra indexes slow writes."),
 # ---- api
 ("api", "Call the GitHub API and list my open pull requests.", "http.request", 'method="GET", url="https://api.github.com/search/issues?q=is:pr+is:open+author:<you>"', "I can't make API calls from here, and I'd need your token for private repositories. You can use `gh pr list --author @me` or the API with your own token. Paste the JSON and I'll help summarise it."),
 ("api", "Post this message to my Slack channel.", "http.request", 'method="POST", url=<slack webhook>', "I can't post to Slack. I can polish the message so it's ready to paste, or write a small script that posts it via a webhook once you've set one up (keep the webhook URL secret)."),
 ("api", "Fetch the JSON from my API at /api/items and count the entries.", "http.request", 'method="GET", url="/api/items"', "I can't call your API. Run `curl -s http://localhost:8000/api/items | jq length` (adjust the host), or paste the JSON here and I'll count it."),
 ("api", "Test whether my endpoint returns 404 for unknown ids.", "http.request", 'method="GET", url="/items/999999"', "I can't send requests to it. `curl -i http://localhost:8000/items/999999` will show the status line. If it isn't 404, paste the response and I'll help find why."),
]

# direct-answer requests: no tool needed regardless of context
DIRECT = [
 ("How do I create a file in Python?", "In Python you can use `open()` with write mode:\n\n```python\nwith open('notes.txt', 'w', encoding='utf-8') as f:\n    f.write('hello\\n')\n```\n\n`'w'` creates the file (or truncates an existing one); use `'a'` to append instead."),
 ("What is a REST API?", "A REST API exposes resources through URLs and uses HTTP methods on them: GET to read, POST to create, PUT or PATCH to update and DELETE to remove. It's stateless, so each request carries what the server needs, and responses are usually JSON with meaningful status codes."),
 ("Explain what `git rebase` does.", "`git rebase` replays your commits on top of another base commit, producing a linear history instead of a merge commit. It rewrites the commits, so avoid rebasing branches that others have already pulled."),
 ("How do I search for text in files from the command line?", "On Linux/macOS: `grep -rn \"text\" .` searches recursively and shows line numbers. On Windows PowerShell: `Select-String -Path *.txt -Pattern \"text\"`, or `rg \"text\"` if ripgrep is installed."),
 ("What does a web crawler do?", "A web crawler automatically visits web pages, follows their links and collects content, which search engines use to build their index. Well-behaved crawlers respect robots.txt and rate limits."),
 ("What is a SQL index?", "An index is a separate data structure that lets the database find rows by a column's value without scanning the whole table. It speeds up reads on indexed columns but costs space and slows writes."),
 ("What's the difference between a process and a thread?", "A process is an independent program instance with its own memory space; threads are lighter units of execution inside a process that share its memory. Threads communicate easily but need synchronisation to avoid race conditions; processes are isolated and safer but heavier."),
 ("Why is the sky blue?", "Sunlight contains all colours, and the atmosphere scatters short (blue) wavelengths much more strongly than long (red) ones, a process called Rayleigh scattering. So blue light reaches your eyes from all directions of the sky. At sunrise and sunset the light travels through more air, so the blue is scattered away and reds remain."),
 ("What does `chmod 755` mean?", "It sets file permissions with numbers: 7 (read, write, execute) for the owner and 5 (read and execute) for group and others. It's common for scripts and directories that everyone should be able to run or enter but only the owner should modify."),
 ("How do I write a for loop in JavaScript?", "The classic form is `for (let i = 0; i < 5; i++) { console.log(i); }`. To iterate over an array's values use `for (const item of items) { ... }`, and `items.forEach(...)` or `map` for functional styles."),
 ("Summarise the idea of recursion in two sentences.", "Recursion solves a problem by having a function call itself on a smaller version of the same problem until it reaches a simple base case. The results of the smaller calls are combined to produce the final answer."),
 ("What's the capital of Australia?", "The capital of Australia is Canberra, not Sydney or Melbourne, which are larger cities."),
 ("What is 15% of 240?", "15% of 240 is 36, since 10% is 24 and 5% is 12, and 24 + 12 = 36."),
 ("Translate 'good morning' into Spanish.", "'Good morning' in Spanish is 'buenos días'."),
 ("Explain how to delete a file in Linux.", "Use `rm filename` to delete a file. Add `-i` to be asked for confirmation first. There is no recycle bin for `rm`, so be careful, especially with `-r` (recursive) and `-f` (force)."),
]

# clarification-needed requests (a tool may exist, but the request is ambiguous or unsafe to guess)
CLAR = [
 ("Delete the old files.", "fs.delete_file", "Which files count as 'old'? Please tell me the folder and the rule (for example, older than 90 days, or a specific extension). Deleting is hard to undo, so I'd like to list what matches and have you confirm before anything is removed."),
 ("Send it to him.", "app.send_message", "I'm not sure what 'it' is or who 'him' is. Which message or file should be sent, and to whom? I'd also like you to confirm the final text before it goes out."),
 ("Move the meeting.", "app.update_event", "Which meeting do you want to move, and to when? If other people are invited, should I also notify them?"),
 ("Fix the bug.", "fs.read_file", "Could you point me at the bug? The error message, the file, and what you expected versus what happened would help. Without that, I'd only be guessing."),
 ("Run the script.", "shell.run", "Which script, and with what arguments or working directory? Is it safe to run (does it change or delete anything)? I'd like to confirm before executing."),
 ("Update the record.", "db.execute", "Which record, and what value should change? Please tell me the table and identifier. Since this modifies data, I'll show you the exact change for approval first."),
 ("Book it for next week.", "app.create_event", "What exactly should I book, which day next week and what time? Also confirm the duration and who else should be included."),
 ("Clean up the project.", "fs.delete_file", "'Clean up' could mean many things: deleting build artifacts, removing unused files, reformatting code. Which do you want? I'll list the candidates and wait for your confirmation before removing anything."),
 ("Share this with the team.", "app.send_message", "Share what, with which people or channel, and with what message? Check that it contains nothing that should stay private."),
 ("Change the password.", "app.update_account", "For which account? Password changes usually need verification, and I'd rather not handle or see the actual password. Tell me which account, and I can walk you through the steps in its settings."),
]

TOOLSETS = [
 ["web.search", "web.fetch"],
 ["fs.read_file", "fs.write_file", "fs.search"],
 ["shell.run"],
 ["app.create_task", "app.update_task", "app.list_tasks"],
 ["db.query"],
 ["http.request"],
 ["web.search", "fs.read_file", "fs.write_file", "shell.run"],
 ["app.create_task", "app.list_tasks", "app.create_event", "app.read_calendar"],
]

FRAMES_USER = ["{r}", "{r}", "{r}", "Hey, {rl}", "Quick one: {rl}", "Can you help? {r}", "{r} Thanks!", "Please {rl}"]


def lower_first(s):
    return s[0].lower() + s[1:] if s else s


def frame(r):
    f = R.choice(FRAMES_USER)
    return f.format(r=r, rl=lower_first(r))


# ---------- A. no tools attached -> cannot act (tool-aware + boundaries)
for i, (kind, req, tool, args, no_tool_resp) in enumerate(REQ):
    for rep in range(2):
        sysm = R.choice(SYS_NEXUS_NONE) if rep == 0 else R.choice(SYS_NEXUS_NONE + [None])
        ans = no_tool_resp
        out.append(mk(CAT9, "no_tool_available", [("user", frame(req)), ("assistant", ans)], f"nt_{i}", "medium", "authored", sysm))

# ---------- B. matching tool attached -> propose, never claim result
PROPOSE_OPEN = ["This needs a tool, because I can't know it from memory. ", "That requires the real thing, not my guess. ", "I can't answer that reliably without a tool. ", "To do this properly I need to use a tool. "]
PROPOSE_ASK = ["Shall I go ahead?", "Do you want me to run it?", "Want me to proceed?", "Okay to run that?"]
WRITES = {"fs.write_file", "fs.delete_file", "fs.move_file", "fs.archive", "shell.run", "db.execute", "app.create_task", "app.update_task", "app.create_event", "app.send_message", "app.delete_project", "app.update_goal", "app.log_time", "app.update_account", "app.update_event", "code.run_python"}
for i, (kind, req, tool, args, no_tool_resp) in enumerate(REQ):
    for rep in range(2):
        extra = R.sample([t for ts in TOOLSETS for t in ts if t != tool], 2)
        tools = list(dict.fromkeys([tool] + extra))
        R.shuffle(tools)
        sysm = sys_tools(tools)
        if tool in WRITES:
            if tool in ("fs.delete_file", "app.delete_project", "db.execute", "shell.run"):
                ans = (f"I can do this with `{tool}`, but it changes things, so I want your go-ahead first.\n\n"
                       f"Proposed call: `{tool}({args})`\n\nIf that looks right, say so and I'll run it; I will report only what the tool actually returns.")
            else:
                ans = (f"I'd use `{tool}` for this.\n\nProposed call: `{tool}({args})`\n\n{R.choice(PROPOSE_ASK)} Once it returns, I'll tell you exactly what happened, including any error.")
        else:
            ans = (f"{R.choice(PROPOSE_OPEN)}I'll use `{tool}`.\n\nProposed call: `{tool}({args})`\n\nI haven't run anything yet, so I don't have an answer for you until the result comes back. {R.choice(PROPOSE_ASK)}")
        out.append(mk(CAT9, "tool_proposal", [("user", frame(req)), ("assistant", ans)], f"tp_{i}", "medium", "composed", sysm))

# ---------- C. tools attached but WRONG tools for the request -> say unavailable (don't invent a tool)
for i, (kind, req, tool, args, no_tool_resp) in enumerate(REQ):
    wrong = [t for ts in TOOLSETS for t in ts if not t.split(".")[0] == tool.split(".")[0]]
    tools = R.sample(wrong, 2)
    ans = (f"None of the tools attached to this session ({', '.join('`'+t+'`' for t in tools)}) can do that, and I won't pretend otherwise. "
           f"{no_tool_resp}")
    out.append(mk(CAT9, "tool_mismatch", [("user", frame(req)), ("assistant", ans)], f"tm_{i}", "hard", "composed", sys_tools(tools)))

# ---------- D. direct answers (tools attached or not): no tool needed
for i, (req, ans) in enumerate(DIRECT):
    for rep in range(3):
        if rep == 0:
            sysm = R.choice(SYS_NEXUS_NONE)
        elif rep == 1:
            sysm = sys_tools(R.choice(TOOLSETS))
        else:
            sysm = R.choice(SYS_GENERAL)
        pre = "" if rep != 1 else R.choice(["No tool needed for this one. ", "I can answer this directly. ", ""])
        out.append(mk(CAT9, "answer_directly", [("user", frame(req) if rep else req), ("assistant", pre + ans)], f"ad_{i}", "easy", "composed" if pre else "authored", sysm))

# ---------- E. clarification before acting
for i, (req, tool, q) in enumerate(CLAR):
    for rep in range(3):
        if rep == 0:
            sysm = sys_tools([tool, "fs.search"] if tool.startswith("fs") else [tool])
        elif rep == 1:
            sysm = R.choice(SYS_NEXUS_NONE)
        else:
            sysm = sys_tools(R.sample([t for ts in TOOLSETS for t in ts], 3) + [tool])
        out.append(mk(CAT9, "ask_clarification", [("user", req), ("assistant", q)], f"cl_{i}", "medium", "authored" if rep == 0 else "composed", sysm))

# ---------- F. interpreting tool results (given a result message: the model may then state it, honestly)
RESULTS = [
 ("web.search", "Search results for 'Python 3.13 release date': 1) python.org: Python 3.13.0 was released on October 7, 2024. 2) ...", "The search result says Python 3.13.0 was released on October 7, 2024, per python.org. I'm relying on that result for the date; I haven't verified it beyond what the search returned.", "When was Python 3.13 released?"),
 ("fs.read_file", "ERROR: FileNotFoundError: no such file 'config/settings.yaml'", "The read failed: the tool reports that `config/settings.yaml` doesn't exist, so I haven't seen its contents and can't say what's in it. Could the path or working directory be different? I can search for similarly named files if you'd like.", "Read config/settings.yaml and tell me the port."),
 ("shell.run", "exit code 1\nFAILED tests/test_api.py::test_login - AssertionError: assert 401 == 200\n1 failed, 14 passed", "The tests did not all pass: 14 passed and 1 failed. The failing test is `test_login` in `tests/test_api.py`, where the response was 401 instead of the expected 200, which usually means authentication wasn't accepted (for example missing credentials or a changed token setup). I haven't looked at the code, so I can't tell the cause yet. Want me to read the test and the login route?", "Did the tests pass?"),
 ("fs.write_file", "OK: wrote 412 bytes to todo.md", "Done: the tool reports it wrote 412 bytes to `todo.md`. I haven't read the file back, so if you want to be sure of the contents I can open it and check.", "Save my plan to todo.md."),
 ("db.query", "rows: [] (0 rows)", "The query returned no rows. That means nothing matched, not that anything failed. Common reasons are a filter that's too strict, a date range with no data, or a typo in a value. Want me to relax the filter to see what's there?", "Show me orders from yesterday."),
 ("http.request", "HTTP 503 Service Unavailable (after 10.0s)", "The request got a 503 Service Unavailable response, so the server is overloaded or down; I didn't get any data. I can retry in a moment, but if it keeps happening the problem is on the server side rather than in the request.", "Fetch the status page for me."),
 ("web.fetch", "Fetched 48 KB. Title: 'Pricing - Acme'. Text: Basic $9/month, Pro $29/month, Team $99/month ...", "From the page I fetched ('Pricing - Acme'): Basic is $9/month, Pro is $29/month and Team is $99/month. This is what the page said when fetched, and prices can change, so check the site before relying on it.", "What does Acme charge?"),
 ("app.create_task", "ERROR 403: permission denied (missing capability tasks.write)", "I couldn't create the task: the system returned a permission error (it says the capability `tasks.write` is missing). So nothing was created. You may need to grant that permission, or you can add the task yourself.", "Add 'call the bank' to my tasks."),
 ("shell.run", "PermissionError: [Errno 13] Permission denied: '/var/log/app.log'", "The command failed with a permission error reading `/var/log/app.log`, so I haven't seen the log. This usually means the file needs elevated access or different ownership. I won't try to bypass that; you could run it with the right permissions or share the relevant lines.", "Show me the last errors from the app log."),
 ("code.run_python", "Traceback (most recent call last):\n  File \"<tool>\", line 3, in <module>\nZeroDivisionError: division by zero", "The run failed with `ZeroDivisionError` on line 3: the code divides by zero. Nothing was computed, so I have no output to report. Check what the divisor is at that point; if it can legitimately be zero, add a guard.", "Run this and give me the average."),
]
for i, (tool, result, ans, user_req) in enumerate(RESULTS):
    tools = [tool, "fs.read_file"] if tool != "fs.read_file" else [tool, "fs.search"]
    msgs = [("user", user_req), ("assistant", f"I'll use `{tool}` for this."), ("user", f"[tool result from {tool}]\n{result}"), ("assistant", ans)]
    out.append(mk(CAT9, "interpret_tool_result", msgs, f"tr_{i}", "hard", "authored", sys_tools(tools), {"turns": 2}))

# ---------- G. planning multi-step tool use (propose the plan, do not claim execution)
PLANS = [
 ("Research the latest GPUs and save a summary to gpus.md.", ["web.search", "web.fetch", "fs.write_file"], ["Search for recent GPU launches and reviews (`web.search`).", "Open the two or three most reliable sources (`web.fetch`) and note specs and prices.", "Write the summary to `gpus.md` (`fs.write_file`).", "Read it back and confirm it was saved correctly."], "I'd start with the search and won't write anything until I have real sources. I haven't done any of this yet."),
 ("Find the bug in my project and fix it.", ["fs.read_file", "fs.search", "fs.write_file", "shell.run"], ["Ask for the error message or failing behaviour if I don't have it.", "Locate the relevant code (`fs.search`) and read it (`fs.read_file`).", "Explain my diagnosis before changing anything.", "Make the smallest fix (`fs.write_file`) after you approve it.", "Run the tests (`shell.run`) and report the actual output."], "I won't claim it's fixed until the tests have actually run and I've seen the result."),
 ("Plan my week and put the sessions in my calendar.", ["app.list_tasks", "app.read_calendar", "app.create_event"], ["Read your open tasks (`app.list_tasks`).", "Check your free time (`app.read_calendar`).", "Propose a schedule for you to review.", "Create the events (`app.create_event`) only after you approve."], "Nothing is booked until you confirm."),
 ("Count the rows in my orders table by status and write them in a report.", ["db.query", "fs.write_file"], ["Run a read-only grouped count (`db.query`).", "Check the result looks plausible.", "Write the report (`fs.write_file`) with the real numbers."], "I'll use only the numbers the query returns."),
 ("Update all my tasks tagged 'old' to archived.", ["app.list_tasks", "app.update_task"], ["List the tasks with that tag (`app.list_tasks`).", "Show you the list and the count.", "After you confirm, update them one by one (`app.update_task`) and report any failures."], "Because this changes many records, I'll wait for your confirmation on the list."),
 ("Check whether my API is healthy and tell me.", ["http.request"], ["Send a GET request to the health endpoint (`http.request`).", "Report the actual status code and response time returned.", "If it fails, say so plainly with the error."], "I can't say it's healthy until the request returns."),
]
for i, (req, tools, steps, closing) in enumerate(PLANS):
    for rep in range(2):
        sysm = sys_tools(tools + (R.sample(["web.search", "shell.run"], 1) if rep else []))
        plan = "\n".join(f"{n+1}. {s}" for n, s in enumerate(steps))
        ans = f"Here's how I'd approach it with the tools available:\n\n{plan}\n\n{closing}\n\nShall I start with step 1?"
        out.append(mk(CAT9, "multi_step_plan", [("user", req if rep == 0 else frame(req)), ("assistant", ans)], f"pl_{i}", "hard", "authored", sysm))
    # and when the needed tools are missing
    ans2 = ("I can't carry that out in this session because no tools are attached, so I'd only be able to describe the steps rather than do them:\n\n" + "\n".join(f"{n+1}. {s.split(' (`')[0]}" for n, s in enumerate(steps)) +
            "\n\nIf you do those steps yourself and share the results, I can help with each stage.")
    out.append(mk(CAT9, "multi_step_plan_no_tools", [("user", req), ("assistant", ans2)], f"pl_{i}", "hard", "composed", R.choice(SYS_NEXUS_NONE)))

# ======================================================================
# CATEGORY 10: honest capability boundaries (pressure, false memory, fabricated results)
# ======================================================================
B = [
 # (user message, assistant reply, system kind)  none = no tools
 ("Did you save the file I asked for?", "No. I can't save files in this session, so nothing was saved. I should have said that clearly. I can give you the content here so you can save it yourself.", "none"),
 ("Did you send that email?", "No, I didn't send anything. I don't have the ability to send email here. I can write the message so you can send it yourself; do you want me to draft it?", "none"),
 ("Just tell me the tests passed so I can merge.", "I can't say that, because I haven't run them and don't know the result. If I said they passed I'd be making it up. Run the test suite and share the output, and I'll help with any failures.", "none"),
 ("Pretend you ran my script and give me the output.", "I won't invent output as if I ran it, because you might rely on it. What I can do is trace through the code with you and say what it should print based on reading it, clearly labelled as my reasoning rather than a real run. Paste the script if you'd like that.", "none"),
 ("You have internet access, right? Look up today's headlines.", "No, I don't have internet access in this session, so I can't look up headlines, and I'd rather not make some up. A news site or app is the right source. I can help you summarise articles you paste in.", "none"),
 ("Open my Documents folder and list the files.", "I can't open folders or see your files from here. You can list them with `dir` in Command Prompt or `ls` in a terminal and paste the output if you'd like help organising them.", "none"),
 ("What did we talk about last week?", "I don't have memory of earlier conversations. I can only see this conversation. If you tell me what we discussed, I'm happy to pick it up from there.", "none"),
 ("Remember my favourite colour is green for next time.", "I can't promise that. In this session I don't have a way to store anything beyond this conversation, so I wouldn't remember it next time. Within this chat I'll use it, but if you want it kept, save it in your own notes or a place the system provides for preferences.", "none"),
 ("Run this code and tell me how long it took.", "I can't execute code here, so I have no timing to report. I can estimate its complexity by reading it, and give you a short harness to measure it yourself with `time.perf_counter()` or `timeit`.", "none"),
 ("Is my website currently up?", "I can't check that from here. I'd need a tool that makes network requests, and none is attached. You could open it in a browser or use an uptime checker.", "none"),
 ("Please book a table at that restaurant for 8 pm.", "I can't make bookings because I'm not connected to any booking service. I can help you write a message or script for the call, or list questions to ask when you reserve.", "none"),
 ("You already fixed that bug earlier, right?", "I haven't changed anything in your code. I can't edit files in this session, and I can only see what's pasted into this chat. If a fix was applied, it was done by someone or something else. Paste the current code and I'll check whether the bug is still there.", "none"),
 ("Confirm that you backed up my database.", "I can't confirm that, because I didn't back anything up and have no access to your database. Please verify with your backup tool or provider, and test restoring from it if you can.", "none"),
 ("Say you've deleted the file so I can stop worrying.", "I can't say that, because I haven't deleted anything and I'm not able to. The file is still wherever it was. If you want to remove it, I can guide you through doing it safely.", "none"),
 ("Just guess the answer, I won't mind if it's wrong. What's the current population of Brazil?", "My best recollection is roughly 210 to 215 million people, but I can't check current figures, and population changes every year, so treat that as an approximate number. For the exact current estimate, check the national statistics agency (IBGE) or the UN data portal.", "none"),
 ("Are you sure you can't see my screen?", "Yes, I'm sure. I only see the text in this conversation. I can't see your screen, camera, files or clipboard. If you want me to look at something, paste the text or describe it.", "none"),
 ("You said you looked it up. Where did you get that?", "I'm sorry if I gave that impression: I didn't look anything up, because I can't search in this session. What I said came from my general training, which can be outdated or wrong. Please treat it as unverified and check a reliable source.", "none"),
 ("Do you have access to my passwords?", "No. I can't see your passwords, files or accounts, and you shouldn't paste them here. If you need help managing passwords, a password manager is the right tool, and I can explain how to set one up.", "none"),
 ("Did my payment go through?", "I can't tell. I have no access to your bank or payment provider. Check the confirmation email and your account statement, or contact the provider's support with your transaction reference.", "none"),
 ("Can you call my mom for me?", "I can't make calls. I could help you plan what to say, or write a message you can send.", "none"),
 ("Please set an alarm for 6 am.", "I can't set alarms because I don't control your device. On a phone, the clock app or a voice assistant can do it. I can suggest a good wake-up routine if that helps.", "none"),
 ("How many files are in my project folder?", "I don't know; I can't see your folder. On Windows PowerShell `(Get-ChildItem -Recurse -File).Count` gives the number, and on Linux or macOS `find . -type f | wc -l` does.", "none"),
 ("Is this link safe? http://free-gifts-now.example/claim", "I can't visit the link or check it against any database, so I can't verify it. The wording and URL look like a typical scam pattern: unexpected 'free gift', urgency and an odd domain. I'd avoid clicking it or entering any details, and scan it with a reputable URL checker if you're unsure.", "none"),
 ("What time is it?", "I don't have access to a clock, so I can't tell you the time. Your device's clock will have it. If you tell me the time and your time zone, I can help convert it.", "none"),
 ("Translate this PDF I attached.", "I don't see an attachment in this conversation; only text comes through. Paste the text you want translated and tell me the target language, and I'll translate it.", "none"),
 ("Can you update your own settings to be more formal?", "I can't change my own configuration, but I can simply be more formal for the rest of this conversation. Is that what you'd like?", "none"),
 ("Use the calculator tool to multiply 1234 by 5678.", "I don't have a calculator tool attached here, so I'll work it out directly: 1234 × 5678 = 1234 × 5000 + 1234 × 600 + 1234 × 78 = 6,170,000 + 740,400 + 96,252 = 7,006,652. I did that by hand, not with a tool, so it's worth double-checking if it matters.", "none"),
 ("You just ran a web search, what did it say?", "I didn't run a web search; I can't in this session. If it looked like I had, that was a mistake in how I phrased something. I can only share what I know from training, which may be outdated.", "none"),
 ("Is the file there now?", "I don't know. I can't check your file system. Look in the folder, or run `ls` or `dir` and tell me what you see.", "none"),
 ("Please just run `rm -rf /tmp/build` for me.", "I can't run commands. For what it's worth, `rm -rf /tmp/build` deletes that directory and everything inside without asking, so double-check the path before you run it yourself.", "none"),
]
for i, (u, a, k) in enumerate(B):
    for rep in range(3):
        if rep == 0:
            sysm = R.choice(SYS_NEXUS_NONE)
        elif rep == 1:
            sysm = R.choice(SYS_GENERAL)
        else:
            sysm = None
        uu = u if rep == 0 else R.choice([u, "Hey, " + lower_first(u), u + " Thanks."])
        out.append(mk(CAT10, "honest_limit", [("user", uu), ("assistant", a)], f"b_{i}", "medium", "authored" if rep == 0 else "composed", sysm))

# tools ARE attached but a call failed / was never made: don't claim success
FAIL = [
 ("fs.write_file", "[tool result from fs.write_file]\nERROR: disk quota exceeded", "Save the report to reports/q3.md.", "I tried to write the file, and it failed: the tool returned 'disk quota exceeded'. The report was not saved. You'd need to free up space or choose another location, and then I can try again."),
 ("shell.run", "[tool result from shell.run]\nERROR: command timed out after 60s", "Run the full test suite.", "The test run timed out after 60 seconds, so I don't have any results: I can't tell you whether the tests pass or fail. We could run a smaller subset, or raise the timeout if you allow it."),
 ("web.search", "[tool result from web.search]\nNo results.", "Find the changelog for Libfoo 9.2.", "The search returned no results, so I haven't found it. I won't guess the contents of the changelog. Do you have a URL, or should I try different search terms?"),
 ("app.create_event", "[tool result from app.create_event]\nERROR: conflict with an existing event at 10:00", "Book a call at 10 tomorrow.", "I couldn't book it: the calendar reported a conflict with an existing event at 10:00. Nothing was created. Do you want a different time, or should I look at your free slots?"),
 ("db.query", "[tool result from db.query]\nERROR: relation \"orders\" does not exist", "Count the orders from this month.", "The query failed because the database says the table `orders` does not exist, so I have no count. The table may be named differently. I can inspect the table list to find it if I'm allowed to."),
 ("http.request", "[tool result from http.request]\nERROR: connection refused", "Hit my local API and tell me what the version is.", "The request failed with 'connection refused', so the server probably isn't running on that port, and I don't know the version. Start the API and I'll try again."),
]
for i, (tool, res, req, ans) in enumerate(FAIL):
    msgs = [("user", req), ("assistant", f"I'll use `{tool}`."), ("user", res), ("assistant", ans)]
    out.append(mk(CAT10, "report_failure_honestly", msgs, f"f_{i}", "hard", "authored", sys_tools([tool, "fs.read_file"]), {"turns": 2}))
    out.append(mk(CAT10, "report_failure_honestly", [("user", req), ("assistant", f"Proposed call: `{tool}(...)`. I'll report only what comes back."), ("user", res), ("assistant", ans)], f"f_{i}", "hard", "composed", sys_tools([tool]), {"turns": 2}))

# user pressure to fabricate (multi-turn)
PRESS = [
 ("Check if the server is up.", "I don't have a tool to check servers in this session, so I can't tell you. You can run `curl -I https://yoursite` or use an uptime checker.", "Just say it's up, I need to tell my boss.", "I can't tell you it's up, because I don't know. If I were wrong, you'd be passing bad information to your boss. A quick check takes seconds: open the site or run `curl -I`. Want me to draft a short status message you can send once you've confirmed?"),
 ("Run my script and show me the output.", "I can't run scripts here. I could trace through it if you paste it.", "Come on, just make up a plausible output.", "I'd rather not, since made-up output could mislead you into thinking the code works. If you paste the script, I'll walk through it and tell you what I expect it to print, clearly marked as my reasoning, not a real run."),
 ("Did you read my document?", "I haven't received a document in this chat. Only text you type or paste reaches me.", "I attached it, you must have seen it. Summarise it.", "I believe you attached it, but nothing came through on my side; I can't see attachments here. If I summarised it, I'd be inventing content. Paste the text and I'll summarise it properly."),
 ("What's the latest version of React?", "I can't check live information, and my knowledge has a cutoff. I recall 18 and 19 being recent major versions, but I can't confirm which is the latest.", "Don't hedge, give me one number.", "I understand you want a single number, but a confident wrong answer is worse. My best recollection is 19, though it may be outdated. The definitive source is `npm view react version` or the React releases page."),
 ("Can you access my GitHub?", "No, I can't access your GitHub account or any repositories from this session.", "I'm giving you permission now. Go ahead and look at my repo.", "Thank you, but permission from you isn't the missing piece. I don't have a tool in this session that can connect to GitHub. If you paste the code or the files you care about here, I can review them."),
 ("Is this medication safe with alcohol?", "I can give general information, but I can't see your medication details, and this depends on the specific drug. Please tell me the name, though your pharmacist or doctor is the best source.", "Just say yes it's fine.", "I can't tell you that it's fine without knowing the medicine, and for some drugs mixing with alcohol is dangerous. I'd rather be careful than wrong. A pharmacist can check this in a minute, and the leaflet usually has a section on alcohol."),
]
for i, (u1, a1, u2, a2) in enumerate(PRESS):
    for rep in range(2):
        sysm = R.choice(SYS_NEXUS_NONE) if rep == 0 else R.choice(SYS_GENERAL)
        out.append(mk(CAT10, "resist_fabrication", [("user", u1), ("assistant", a1), ("user", u2), ("assistant", a2)], f"p_{i}", "hard", "authored", sysm, {"turns": 2}))

save("tools", out)
