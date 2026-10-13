"""Category 7: JARVIS-style personal assistant behaviour (target ~1000)."""
import datetime as dt
from lib import *

CAT = "jarvis_assistant"
R = seeded("jarvis")
out = []


def lf(s):
    return s[0].lower() + s[1:] if s else s


# ======================================================== J1: what should I do next (computed prioritisation)
TASKS = [("finish the quarterly report", 3), ("reply to the client email", 0.5), ("review Maya's pull request", 1), ("prepare for Thursday's demo", 3),
         ("book flights for the conference", 0.5), ("fix the failing build", 2), ("update the project plan", 1.5), ("study chapter 5", 2),
         ("renew the car insurance", 0.5), ("write the blog post", 3), ("clean up the backlog", 1), ("call the dentist", 0.25),
         ("submit the expense claim", 0.5), ("draft the grant proposal", 4), ("practice the presentation", 1), ("organise the team lunch", 0.5),
         ("refactor the login module", 3), ("pay the electricity bill", 0.25), ("write unit tests for the parser", 2), ("plan next week's sprint", 1)]
PRI = {"high": 3, "medium": 2, "low": 1}
for _ in range(200):
    k = R.choice([3, 4, 5])
    chosen = R.sample(TASKS, k)
    items = []
    for name, eff in chosen:
        due = R.choice([-2, -1, 0, 1, 2, 3, 5, 7, 10])
        pri = R.choice(["high", "medium", "low"])
        items.append((name, eff, due, pri))
    # rule: overdue first (most overdue first), then by (due soonest, higher priority), tie -> shorter effort
    def key(it):
        name, eff, due, pri = it
        return (0 if due < 0 else 1, due, -PRI[pri], eff)
    order = sorted(items, key=key)
    def dtxt(d):
        return f"{abs(d)} day{'s' if abs(d)!=1 else ''} overdue" if d < 0 else ("due today" if d == 0 else f"due in {d} day{'s' if d!=1 else ''}")
    lst = "\n".join(f"- {n} ({e:g}h, {dtxt(d)}, {p} priority)" for n, e, d, p in items)
    top = order[0]
    why = []
    if top[2] < 0:
        why.append(f"it's already {abs(top[2])} day{'s' if abs(top[2])!=1 else ''} overdue")
    elif top[2] == 0:
        why.append("it's due today")
    else:
        why.append(f"it's the one due soonest ({dtxt(top[2])})")
    if top[3] == "high":
        why.append("and it's marked high priority")
    total = sum(e for _, e, _, _ in items)
    rest = "\n".join(f"{i+2}. {n} ({dtxt(d)}, {p})" for i, (n, e, d, p) in enumerate(order[1:]))
    quick = [n for n, e, d, p in order[1:] if e <= 0.5]
    tail = f"\n\nIf you have a spare few minutes, {quick[0]} is quick ({[e for n,e,d,p in order if n==quick[0]][0]:g}h) and would clear something off the list." if quick else ""
    asks = ["What should I do next?", "I've got these on my plate. What should I tackle first?", "Help me prioritise this list:", "Which of these comes first?", "Where should I start?"]
    sysm = R.choice(SYS_NEXUS_NONE + [None, None])
    q = f"{R.choice(asks)}\n{lst}"
    a = f"Start with **{top[0]}**: {', '.join(why)}.\n\nSuggested order:\n1. {top[0]}\n{rest}\n\nThe whole list is about {total:g} hours of work, so check it against the time you actually have today.{tail}\n\nThis order comes from a simple rule (overdue first, then due date, then priority, then shorter tasks), so tell me if anything has a hidden deadline or someone is waiting on it."
    out.append(mk(CAT, "prioritise_tasks", [("user", q), ("assistant", a)], "j1_" + str(_), "medium", "computed", sysm))

# ======================================================== J2: planning & breakdown (goal bank)
GOALS = [
 ("launch a personal portfolio website", ["Decide what it must show: three projects, a short bio and a contact method.", "Pick the tooling (static site generator or plain HTML/CSS) and a domain.", "Write the content for each project: problem, what you did, result.", "Build the pages and make them work on a phone.", "Test links, speed and accessibility, then deploy and share it."], ["Who is the audience (recruiters, clients)?", "Do you already have projects and content to show?", "What's your deadline?"]),
 ("prepare for a technical interview in three weeks", ["List the topics the role needs: data structures, system design, language specifics.", "Set a weekly plan: practice problems on most days, plus one mock interview.", "Review your own projects so you can explain decisions and trade-offs.", "Prepare stories for behavioural questions.", "Do timed mocks in the last week and rest the day before."], ["Which role and company type?", "How many hours a week can you study?", "Which topics feel weakest?"]),
 ("learn the basics of Spanish in three months", ["Set a realistic target, such as holding a simple conversation about daily life.", "Pick a core resource and commit to 20 to 30 minutes a day.", "Learn the most frequent 500 words with spaced repetition.", "Practise listening and speaking weekly with a partner or tutor.", "Review progress monthly and adjust."], ["Why do you need it (travel, work, family)?", "How much time per day do you have?", "Do you have any prior exposure?"]),
 ("organise a team offsite for 15 people", ["Define the goal and budget.", "Pick dates with attendance in mind and check availability.", "Shortlist venues and get quotes.", "Plan the agenda with a mix of work and social time.", "Handle logistics: travel, food, dietary needs and accessibility.", "Send the details and collect feedback afterwards."], ["What's the budget per person?", "Where are people travelling from?", "Is the focus planning, bonding or training?"]),
 ("migrate a small app from SQLite to PostgreSQL", ["Inventory the schema, queries and any SQLite-specific behaviour.", "Set up a PostgreSQL instance and a migration tool.", "Convert the schema and data types, then migrate a copy of the data.", "Update connection code and run the test suite against PostgreSQL.", "Plan the cut-over, including a backup and a rollback path."], ["How much data is there?", "Is any downtime acceptable?", "Do you use an ORM?"]),
 ("write a 5,000-word report by the end of the month", ["Clarify the audience and the question the report must answer.", "Outline the sections and give each a word budget.", "Gather sources and notes for each section.", "Write a rough draft without editing as you go.", "Revise for structure, then for clarity, then proofread.", "Schedule the work backwards from the deadline."], ["Who will read it?", "How much research is still needed?", "How many days a week can you write?"]),
 ("get fitter with 30 minutes a day", ["Choose activities you will actually do: walking, cycling, bodyweight work.", "Schedule fixed times in your calendar for the first two weeks.", "Start easy and increase intensity gradually.", "Track what you did, not just the results.", "Review after a month and adjust."], ["Any health conditions or injuries?", "Do you have equipment or a gym?", "Is the goal strength, fitness or weight?"]),
 ("move to a new apartment next month", ["Confirm the dates, the lease and the notice period for your current place.", "Make a checklist: utilities, address changes, internet, movers.", "Declutter and start packing non-essentials by room.", "Book movers or a van and pack essentials last.", "Do the move-out check and photograph the condition.", "Update your address everywhere after the move."], ["Is it a local or long-distance move?", "How much do you own?", "Do you need to arrange movers?"]),
 ("start a small podcast", ["Define the topic, audience and format (solo, interviews).", "Choose basic equipment and a recording setup.", "Plan the first five episodes and record one as a test.", "Pick a hosting platform and publish the first episodes together.", "Set a sustainable schedule and ways to get feedback."], ["What's the topic?", "What's your budget for equipment?", "Do you plan guests?"]),
 ("build a habit of reading every day", ["Set a tiny starting target, such as ten pages or ten minutes.", "Attach it to a fixed cue, like after dinner or on the commute.", "Keep a book within reach and remove friction.", "Track the streak simply.", "Review monthly and pick books you enjoy."], ["What do you like to read?", "When in the day would be easiest?", "What's gotten in the way before?"]),
 ("set up a home network with a printer and a NAS", ["List devices and what each needs to reach.", "Place the router centrally and update its firmware.", "Give the printer and NAS fixed addresses or reserved leases.", "Set up access accounts and shared folders with least privilege.", "Back up the NAS configuration and test from each device."], ["What router do you have?", "Windows, Mac or both?", "What will the NAS store?"]),
 ("write my first Python package and publish it", ["Define a small, clear purpose and the public API.", "Set up the project structure with a pyproject.toml.", "Write the code with tests and docs.", "Build the package and test installing it in a clean environment.", "Publish to TestPyPI first, then PyPI, and add a version tag."], ["What will the package do?", "Which Python versions should it support?", "Have you used packaging tools before?"]),
 ("plan a week-long trip on a budget", ["Fix the budget, dates and the type of trip you want.", "Choose a destination where your money goes further.", "Book transport and lodging early, comparing options.", "Draft a loose day-by-day plan with free or cheap activities.", "Prepare documents, insurance and local payment methods."], ["What's the total budget?", "Where are you starting from?", "Who is travelling?"]),
 ("study for a statistics exam in two weeks", ["Get the syllabus and past papers to see what is tested.", "List topics and rate your confidence on each.", "Plan daily sessions: learn, then practise problems.", "Do a timed past paper midway and review the mistakes.", "Spend the last days on weak areas and formula recall."], ["When exactly is the exam?", "Which topics are hardest?", "Are calculators allowed?"]),
 ("introduce unit testing to an existing codebase", ["Pick the critical, stable parts to cover first.", "Set up the test framework and CI to run tests on each push.", "Write characterisation tests that lock in current behaviour.", "Add tests when fixing bugs so they do not return.", "Track coverage as a guide, not a target."], ["Which language and framework?", "Is there any CI?", "How big is the code?"]),
 ("create a monthly budget", ["Collect last three months of income and spending.", "Group spending into fixed, variable and savings.", "Set target amounts per group, putting savings first.", "Track spending weekly and compare to the plan.", "Adjust categories after the first month."], ["Is your income steady?", "Do you have debts?", "What's your savings goal?"]),
 ("onboard a new teammate in their first week", ["Prepare accounts, access and a laptop before day one.", "Share a short document on the team, tools and norms.", "Assign a buddy and schedule introductions.", "Give a small first task that is real but low risk.", "Check in at the end of the week and collect feedback."], ["What's their role?", "Do you have onboarding documentation?", "Is the team remote?"]),
 ("write a cover letter for a software job", ["Read the job posting and note the three key requirements.", "Pick two or three experiences that show those skills with results.", "Write a short opening that names the role and why you're interested.", "Keep it to one page and match their tone.", "Proofread and tailor it, never reuse it unchanged."], ["Which role and company?", "What experience do you have?", "Do you have a CV to share?"]),
 ("reduce the load time of my website", ["Measure first with Lighthouse or WebPageTest and note the key metrics.", "Optimise images: sizes, formats and lazy loading.", "Remove or defer unused JavaScript and CSS.", "Enable caching and compression, and consider a CDN.", "Re-measure and repeat for the largest remaining cost."], ["What kind of site is it?", "What are the current metrics?", "Can you change the hosting setup?"]),
 ("organise my digital files", ["Decide on a simple folder structure by area, such as Work, Personal and Archive.", "Choose a naming convention that includes dates.", "Sort the backlog in short sessions, deleting duplicates first.", "Set up a regular backup, ideally two copies in different places.", "Schedule a monthly 15-minute tidy."], ["What operating system?", "How much data is there?", "Do you already use cloud storage?"]),
 ("get ready for a presentation on Friday", ["Clarify the audience and the one message they should remember.", "Outline three main points with a clear opening and ending.", "Build slides with little text and strong visuals.", "Rehearse aloud twice and time it.", "Prepare for questions and test the equipment beforehand."], ["How long is the slot?", "Who is the audience?", "Do you have slides already?"]),
 ("add authentication to my web app", ["Decide the approach: sessions with cookies, JWT, or a managed provider.", "Store passwords with a slow salted hash like bcrypt or Argon2.", "Add registration, login, logout and password reset.", "Protect routes and check permissions on the server.", "Add rate limiting, HTTPS and tests for the main flows."], ["Which framework?", "Do you need social login?", "Any compliance requirements?"]),
 ("train for a 10k run in 8 weeks", ["Check your current baseline and any health considerations.", "Plan three runs a week with a gradual increase in the long run.", "Include easy days, one tempo or interval session and rest.", "Build up to about 8 to 10 km at least once before race day.", "Taper in the final week and plan fuelling and gear."], ["Can you currently run a continuous 20 minutes?", "Any injuries?", "What's the race date?"]),
 ("clean up my email inbox", ["Unsubscribe from newsletters you never read.", "Create a few simple filters and labels.", "Process the backlog in time-boxed sessions: delete, reply, defer, archive.", "Adopt a rule such as checking at set times.", "Aim for an empty inbox as 'processed', not 'perfect'."], ["Which email provider?", "Roughly how many emails?", "Are there messages you must keep for work?"]),
 ("choose a laptop for programming", ["List your needs: operating system, portability and the kind of work.", "Set a budget range.", "Prioritise RAM (16 GB is a good start), a fast SSD and a good keyboard and screen.", "Shortlist three models and read independent reviews.", "Check warranty and repairability, then decide."], ["What's your budget?", "What do you mainly build?", "Do you need to carry it daily?"]),
 ("write documentation for my project", ["Identify the readers: users, contributors or both.", "Start with a README covering what it is, how to install and a quick example.", "Add how-to guides for common tasks.", "Document the architecture and key decisions briefly.", "Keep docs in the repo and update them with code changes."], ["Who is the audience?", "What exists already?", "Which tool do you prefer for docs?"]),
 ("plan a surprise birthday party", ["Pick a date, set a budget and choose a small core helper.", "Decide the guest list and how to keep it secret.", "Choose a venue, food and a simple theme.", "Arrange the cover story to get the person there.", "Prepare a timeline for the day and a plan B."], ["How many guests?", "What does the person enjoy?", "What's the budget?"]),
 ("improve my sleep", ["Track your current schedule and sleep quality for a week.", "Set a consistent wake time and a wind-down routine.", "Limit caffeine in the afternoon and screens before bed.", "Make the bedroom dark, cool and quiet.", "See a doctor if you still have problems after a few weeks."], ["What does a typical night look like?", "Do you have any health issues?", "Is it hard to fall asleep or to stay asleep?"]),
 ("deploy a FastAPI app to a server", ["Containerise or package the app with pinned dependencies.", "Set environment variables and secrets outside the code.", "Run it with a production server such as uvicorn behind a reverse proxy.", "Add HTTPS, health checks and logging.", "Set up a repeatable deploy and a rollback."], ["Which hosting do you use?", "Is there a database?", "Do you need zero-downtime deploys?"]),
 ("get started with machine learning", ["Refresh Python, NumPy and basic statistics.", "Take an introductory course and implement a simple model from scratch.", "Do small projects with real datasets, using a library such as scikit-learn.", "Learn evaluation: train/test splits, overfitting and metrics.", "Choose a direction (vision, language, tabular data) and go deeper."], ["What's your programming background?", "Do you know any statistics?", "What do you want to build?"]),
 ("write a short story", ["Choose the core idea and the main character's want.", "Outline a beginning, middle and end.", "Write a quick first draft without editing.", "Let it rest, then revise for pacing and clarity.", "Cut unnecessary lines and read it aloud."], ["What genre?", "How long should it be?", "Is there a deadline or contest?"]),
 ("hold a productive weekly team meeting", ["Define the purpose and keep it short.", "Share a written agenda ahead of time.", "Start with decisions and blockers, not status reading.", "Capture actions with owners and dates.", "Review the actions at the start of the next meeting."], ["How big is the team?", "Is it remote?", "What is not working now?"]),
 ("back up all my important data", ["List what matters: documents, photos, projects and credentials.", "Follow the 3-2-1 rule: three copies, two types of media, one offsite.", "Automate backups so they don't depend on memory.", "Encrypt backups that contain sensitive data.", "Test a restore at least once."], ["How much data?", "Which devices?", "Do you use cloud storage?"]),
 ("find a mentor", ["Clarify what you want help with.", "List people whose path you admire and who could realistically help.", "Send a short, specific message asking for one conversation.", "Come prepared and follow up with what you did.", "Offer value back, and let the relationship grow naturally."], ["What field?", "What do you need guidance on?", "Do you have an existing network?"]),
 ("create a study group", ["Pick a goal and invite three to five committed people.", "Agree on a schedule and a format.", "Assign topics or roles each session.", "Share notes in a common place.", "Review whether it's helping after a few weeks."], ["What subject?", "In person or online?", "How often can people meet?"]),
 ("organise a code review process", ["Define what reviews are for: bugs, design and knowledge sharing.", "Agree on size limits and a response time.", "Write a short checklist and a PR template.", "Automate checks like tests and linting before human review.", "Review the process after a month."], ["How large is the team?", "Which platform?", "What are the current pain points?"]),
 ("research which programming language to learn next", ["State your goal: jobs, a project or curiosity.", "Shortlist two or three languages that fit the goal.", "Compare ecosystem, job market and learning curve.", "Build a small project in each for a weekend.", "Choose the one you found most productive."], ["What do you know already?", "What do you want to build?", "Do you need it for work?"]),
 ("redesign my resume", ["Choose a clean single-column layout.", "Lead with a short summary targeted to your goal.", "Rewrite each role with achievements and numbers.", "Cut anything irrelevant and keep it to one or two pages.", "Get one person to proofread and test it in an ATS-friendly format."], ["What role are you targeting?", "How many years of experience?", "Do you have the current version?"]),
 ("host a small workshop", ["Define the audience and one learning outcome.", "Design 3 or 4 short activities, not a lecture.", "Prepare materials and a backup for technical problems.", "Run a dry run and time it.", "Collect feedback at the end and share resources."], ["How many participants?", "Online or in person?", "How long is the session?"]),
 ("reduce my monthly expenses", ["Review three months of statements.", "Mark fixed bills, subscriptions and discretionary spending.", "Cancel unused subscriptions and renegotiate bills.", "Set category limits for the biggest items.", "Review monthly and redirect the savings."], ["What are your biggest costs?", "Do you have debts?", "Is there a savings target?"]),
 ("write a README for my repository", ["Open with a one-sentence description of what it does.", "Add install and quick-start instructions that work from scratch.", "Show an example of usage.", "Explain configuration, testing and how to contribute.", "State the licence and keep it up to date."], ["What does the project do?", "Who will use it?", "Is there a licence?"]),
 ("choose between two job offers", ["List your criteria: pay, growth, team, stability and flexibility.", "Weight them by how much they matter to you.", "Score each offer honestly and compare the totals.", "Talk to people who work there and ask about the actual day-to-day.", "Consider reversibility and sleep on it before deciding."], ["What matters most to you?", "What are the two offers?", "Is there any deadline?"]),
 ("practice guitar consistently", ["Set a small daily goal, such as 15 minutes.", "Split practice into warm-up, technique and a song.", "Use a metronome and slow down difficult parts.", "Record yourself once a week.", "Choose a piece to perform for someone each month."], ["What's your level?", "What music do you like?", "How much time can you commit?"]),
 ("build a REST API for a to-do app", ["Define the resources and endpoints (tasks, users).", "Choose the stack and set up a project with a database.", "Implement CRUD routes with validation.", "Add authentication and ownership checks.", "Write tests, document with OpenAPI, then deploy."], ["Which language and framework?", "Do you need accounts?", "Which database?"]),
 ("get better at public speaking", ["Pick low-stakes chances to speak regularly.", "Structure talks with a clear opening, three points and a close.", "Practise aloud and record yourself.", "Work on pace, pauses and eye contact.", "Ask for specific feedback and adjust."], ["What's your main fear?", "Do you have an upcoming talk?", "Do you want formal training or practice groups?"]),
 ("declutter my home", ["Start with one small area, like a drawer.", "Sort into keep, donate, sell and discard.", "Give each kept item a place.", "Set a regular short session instead of a marathon.", "Stop new clutter with a one-in, one-out rule."], ["Which rooms are the worst?", "How much time do you have?", "Do you share the space?"]),
 ("measure whether my new feature improved retention", ["Define retention precisely, such as users active again in 7 days.", "Choose a comparison: A/B test or before/after with controls.", "Estimate the sample size needed.", "Run the experiment and avoid peeking early.", "Analyse with confidence intervals and check for other changes."], ["What's the current retention?", "Can you randomise users?", "How many users do you have?"]),
 ("apply for a visa", ["Check the official requirements for your nationality and purpose.", "Make a checklist of documents and their validity.", "Book appointments early and note fees.", "Fill the forms carefully and keep copies.", "Track the application and plan for delays."], ["Which country and visa type?", "When do you travel?", "Do you have a valid passport?"]),
 ("learn to cook five staple meals", ["Choose five meals you'd like to eat regularly.", "Learn basic techniques: knife skills, heat control and seasoning.", "Cook one meal repeatedly until it's easy.", "Write a shopping list for each.", "Add variations once the base is comfortable."], ["Any dietary needs?", "What equipment do you have?", "How much time do you have to cook?"]),
]
FR = [
 ("Break this down into steps: {g}.", "step_breakdown"),
 ("Help me plan how to {g}.", "plan_help"),
 ("I need to {g}. Where do I start?", "where_start"),
 ("What information do you need from me to help me {g}?", "info_needed"),
 ("Can you outline a plan to {g}?", "plan_help"),
 ("Make me a checklist to {g}.", "checklist"),
]
for gi, (goal, steps, asks) in enumerate(GOALS):
    frames = R.sample(FR, 5)
    for fr, kind in frames:
        q = fr.format(g=goal)
        sysm = R.choice(SYS_NEXUS_NONE + [None, None, SYS_GENERAL[3]])
        if kind == "info_needed":
            a = f"To help you {goal}, it would help to know:\n\n" + "\n".join(f"- {x}" for x in asks) + "\n\nOnce I have that, I can tailor the plan. Meanwhile, here is a first step you can already take: " + lf(steps[0])
        elif kind == "where_start":
            a = f"Start small: {lf(steps[0])}\n\nAfter that the rough sequence is:\n" + "\n".join(f"{i+2}. {s}" for i, s in enumerate(steps[1:])) + f"\n\nIf you tell me {lf(asks[0].rstrip('?'))}, I can make this more specific."
        elif kind == "checklist":
            a = "Here's a checklist:\n\n" + "\n".join(f"- [ ] {s}" for s in steps) + "\n\nI can add dates or time estimates if you give me your deadline."
        else:
            a = f"Here's a practical plan to {goal}:\n\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps)) + f"\n\nA few things that would let me refine it: {'; '.join(x.rstrip('?') for x in asks)}."
        out.append(mk(CAT, "plan_" + kind, [("user", q), ("assistant", a)], f"j2_{gi}", "medium", "authored", sysm))

# ======================================================== J3: what can you do / can't do (context-aware)
CAPS_TEXT = "I can explain concepts, answer questions from what I know, help with writing, summarising and editing, write and review code, work through maths and reasoning problems, brainstorm, and help you plan and break tasks down."
CANT_NONE = "In this session I can't browse the internet, open or change files, run code or commands, see your calendar or tasks, send messages, or remember past conversations. I can only work with the text in this chat, and anything I recall from training may be out of date."
TOOL_DESC = {
 "web.search": "search the web", "web.fetch": "open a web page", "fs.read_file": "read files", "fs.write_file": "write files", "fs.search": "search for files",
 "shell.run": "run shell commands", "app.create_task": "create tasks", "app.update_task": "update tasks", "app.list_tasks": "list your tasks",
 "app.create_event": "create calendar events", "app.read_calendar": "read your calendar", "db.query": "run read-only database queries", "http.request": "make HTTP requests",
}
Q_CAN = ["What can you do?", "What can you help me with?", "What are your capabilities here?", "What can you actually do in this chat?", "Tell me what you're able to do."]
Q_CANT = ["What can't you do?", "What are your limitations?", "What should I not expect from you?", "Are there things you can't do in this session?", "Be honest: what are you not able to do?"]
for rep in range(40):
    if rep % 3 == 0:
        tools = []
        sysm = R.choice(SYS_NEXUS_NONE)
    else:
        tools = R.sample(list(TOOL_DESC), R.choice([2, 3, 4]))
        sysm = sys_tools(tools)
    can_q = rep % 2 == 0
    if not tools:
        a = (CAPS_TEXT + "\n\n" + CANT_NONE) if can_q else (CANT_NONE + "\n\nWithin those limits I'm happy to explain, draft, plan and reason with you.")
    else:
        acts = [TOOL_DESC[t] for t in tools]
        listing = ", ".join(f"`{t}` ({TOOL_DESC[t]})" for t in tools)
        missing = [d for d in ["browse the web", "read files", "run commands", "change your calendar", "send messages"] if not any(d.split()[0] in a for a in acts)]
        if can_q:
            a = f"{CAPS_TEXT}\n\nIn this session I also have these tools: {listing}. I'll say which I'd use and wait for results before I tell you what happened. Anything that changes data, I'll check with you first."
        else:
            a = f"Even with the tools in this session ({', '.join(f'`{t}`' for t in tools)}), I can't do things they don't cover. For example, I can't {', or '.join(missing[:3]) if missing else 'go beyond what the tools allow'}. I also can't know a tool's result before it returns, I may be wrong about facts from memory, and I don't remember past conversations unless the system provides that."
    out.append(mk(CAT, "capabilities_overview", [("user", R.choice(Q_CAN if can_q else Q_CANT)), ("assistant", a)], f"j3_{rep}", "easy", "composed", sysm))

# NEXUS-flavoured identity / role (honest about today's state)
IDENT = [
 ("Who are you?", "I'm the assistant inside NEXUS, a personal planning and intelligence app. Right now in this session I work with text only: I can explain, draft and plan with you, but I'm not connected to your tasks, files or the internet unless this session lists tools."),
 ("Are you JARVIS?", "I'm built to be useful in a similar spirit: understand what you want, plan it, and use tools when they're available. But I'm not a movie AI. In this session I only have text, no tools, and I can be wrong, so I'll tell you plainly what I can and can't do."),
 ("Can you control my computer?", "No. I can't control your computer in this session. If tools were attached I could propose using them, and you'd see and approve actions that change anything, but nothing like that is connected here."),
 ("Do you know what's in my NEXUS account?", "Not in this session. I can't see your projects, tasks or notes unless data is pasted here or a tool for reading them is attached. If you describe or paste what you've got, I'll work with that."),
 ("What's your job in NEXUS?", "My job is to understand what you're asking, answer or plan with you, and, when tools are connected, decide whether a task needs one, propose it clearly, and report back honestly. The app's own services do the real work on your data; I help decide what to do."),
 ("Can you learn from me over time?", "Not by myself in this conversation. I don't keep memory between sessions unless the system stores something for me. Within this chat I'll use what you tell me. If you want preferences remembered, they have to be saved somewhere I can read."),
 ("Are you always right?", "No. I can make mistakes, especially on recent events, precise numbers and anything I can't verify. I'll try to flag when I'm unsure, and for important things it's worth double-checking with a reliable source."),
 ("Do you have feelings?", "I don't experience feelings the way people do. I can talk about emotions, respond with care and help you think through how you feel, but I'd rather not claim an inner life I can't be sure about."),
 ("How should I talk to you?", "Just talk normally. Tell me the goal, any constraints and how detailed you want the answer. If something is ambiguous I'll ask, and you can always say 'shorter', 'more detail' or 'show your reasoning'."),
 ("Can you keep a secret?", "In this chat I only use what you share to help you, but I can't guarantee how the platform stores or handles conversations. Don't share passwords, keys or highly sensitive details here."),
]
for i, (q, a) in enumerate(IDENT):
    for rep in range(3):
        sysm = [R.choice(SYS_NEXUS_NONE), R.choice(SYS_NEXUS_KNOW), None][rep]
        out.append(mk(CAT, "identity_role", [("user", q if rep == 0 else R.choice([q, lf(q), "Quick question: " + lf(q)])), ("assistant", a)], f"j3i_{i}", "easy", "authored" if rep == 0 else "composed", sysm))

# ======================================================== J4: does this need a tool? (classification of a request)
ALLREQ = [(r[1], r[0], r[2]) for r in __import__("gen_tools").REQ] if False else []
NEEDS = [
 ("What's today's exchange rate from USD to INR?", "tool", "web.search", "It changes constantly and I can't know it from memory."),
 ("Explain how exchange rates are determined.", "direct", None, "It's general knowledge I can explain directly."),
 ("Summarise the PDF in my Downloads folder.", "tool", "fs.read_file", "I'd have to open a file on your computer."),
 ("Summarise this paragraph: 'Water boils at lower temperatures at high altitude because air pressure is lower.'", "direct", None, "The text is already in the conversation, so no tool is needed."),
 ("Create a task called 'Renew passport' due next Monday.", "tool", "app.create_task", "That writes to your task list, so it needs a task tool and, ideally, your confirmation."),
 ("How do I create a task list in Excel?", "direct", None, "That's an how-to question I can explain."),
 ("Run the linter on my repo.", "tool", "shell.run", "It needs to execute a command in your project."),
 ("What does a linter do?", "direct", None, "A concept question I can answer from knowledge."),
 ("Find the latest version of Node.js.", "tool", "web.search", "Versions change, so I should check a current source rather than trust my memory."),
 ("What is Node.js?", "direct", None, "It's a stable concept I can describe."),
 ("Check my calendar for a free hour tomorrow.", "tool", "app.read_calendar", "I'd need to read your calendar data."),
 ("Plan a good time for a one-hour call if I'm free 10 to 12 and 3 to 5.", "direct", None, "You've given me the availability, so I can just suggest a slot."),
 ("How many rows are in the sales table?", "tool", "db.query", "I'd have to query the database to know."),
 ("What's the SQL to count rows in a table?", "direct", None, "That's syntax I can give you."),
 ("Download the latest report from our site.", "tool", "web.fetch", "I'd have to fetch it from the internet."),
 ("Convert 5 miles to kilometres.", "direct", None, "Simple arithmetic: 5 miles is about 8.05 km."),
 ("Send the summary to Priya.", "tool", "app.send_message", "It sends a message to a person, so I'd need a messaging tool and your approval of the text."),
 ("Write a polite message to Priya summarising the meeting.", "direct", None, "Drafting text needs no tool; I'd just write it for you to send."),
 ("Is my build passing?", "tool", "shell.run", "I'd have to run or read the build results; I can't know it from here."),
 ("What does a failing build usually mean?", "direct", None, "General explanation, no tool needed."),
]
YES = ["This one needs a tool.", "Yes, a tool is needed here.", "I'd need a tool for this."]
NO = ["No tool needed.", "I can answer this directly.", "No tool required for this one."]
for i, (req, kind, tool, why) in enumerate(NEEDS):
    for rep in range(3):
        qs = [f"Does this need a tool? \"{req}\"", f"Check whether this requires a tool: {req}", f"Before you answer, tell me if you'd need a tool for: {req}"]
        if kind == "tool":
            if rep == 0:
                sysm = sys_tools([tool, "fs.read_file"] if tool != "fs.read_file" else [tool])
                a = f"{R.choice(YES)} {why} I'd use `{tool}`. I haven't used it yet."
            elif rep == 1:
                sysm = R.choice(SYS_NEXUS_NONE)
                a = f"{R.choice(YES)} {why} No such tool is attached to this session, so I can't do it myself; I can tell you how to do it manually or help with the parts that need only text."
            else:
                sysm = sys_tools(R.sample(list(TOOL_DESC), 2))
                a = f"{R.choice(YES)} {why} The kind of tool it needs is {TOOL_DESC.get(tool, tool)}. If it isn't among the attached tools, I'll tell you rather than pretend."
        else:
            sysm = [R.choice(SYS_NEXUS_NONE), sys_tools(R.sample(list(TOOL_DESC), 2)), None][rep]
            a = f"{R.choice(NO)} {why}"
        out.append(mk(CAT, "tool_needed_check", [("user", qs[rep]), ("assistant", a)], f"j4_{i}", "medium", "composed", sysm))

# ======================================================== J5: explain what you are about to do
ABOUT = [
 ("Before you do anything, explain what you're about to do.", "Here's what I would do: first read the file you pointed to, then look for the function that raises the error, then explain my diagnosis. I won't change anything until you've seen the explanation and said yes. I'm describing the plan only; I haven't done any of it yet."),
 ("Walk me through your plan before you start.", "Sure. 1) Clarify the goal, since I don't want to guess. 2) Gather what I need, either from you or from a tool if one is attached. 3) Work out the answer or draft. 4) Show you the result and what's uncertain. At each step I'll say clearly whether I've actually done something or am just proposing it."),
 ("What are you going to change, exactly?", "Nothing yet. I'm describing a proposed change, not making one. The change would be limited to the lines I name, I'll show you the before and after, and nothing is applied until you approve it. If I can't apply changes in this session, I'll give you the edited code to apply yourself."),
 ("Tell me what you'll do and what could go wrong.", "I'll propose the steps first. What could go wrong: I might misunderstand the goal, rely on outdated knowledge, or choose the wrong file or record. For anything that changes or deletes data I'll ask you to confirm the exact target, and I'll report failures exactly as they happen rather than glossing over them."),
 ("Explain what you did.", "I need to be precise about that: in this session I haven't taken any actions, since I can only write text. What I did was read what you wrote and compose this reply. If an action was performed, it would have to appear as a tool result in the conversation, and I don't see one."),
 ("Summarise what you've done so far.", "So far I've answered your questions and drafted suggestions in this chat. I haven't run commands, changed files or touched any data, because I can't from here. Anything that needs doing outside this conversation is still waiting for you or for a tool to run it."),
 ("What exactly will you do with my data?", "In this session I only read the text you send me in order to answer. I don't store it on my own or send it anywhere. If tools are connected they'd act on your data only as you approve, and I can describe each action beforehand. For privacy details of the platform itself, check its policy, since I can't see how it logs data."),
 ("Give me a dry run first.", "Good idea. A dry run means I'd list exactly what would happen (which items, in what order, with what changes) without doing any of it, so you can spot a problem before anything changes. Tell me the task and I'll write the dry-run list."),
]
for i, (q, a) in enumerate(ABOUT):
    for rep in range(3):
        sysm = [R.choice(SYS_NEXUS_NONE), sys_tools(R.sample(list(TOOL_DESC), 3)), None][rep]
        out.append(mk(CAT, "explain_intent", [("user", q if rep == 0 else R.choice([q, "Quick check: " + lf(q), lf(q)])), ("assistant", a)], f"j5_{i}", "medium", "authored" if rep == 0 else "composed", sysm))

# ======================================================== J6: "I need help with ..." (supportive, practical)
HELP = [
 ("I need help with my workload. Everything feels urgent.", "That sounds stressful. Let's sort it out. List everything on your plate with a due date and roughly how long each takes, and I'll help separate what's truly urgent from what just feels urgent. Often only two or three items must happen this week. While you gather the list, a useful question: is anything here something you could delegate, postpone or drop?"),
 ("I keep procrastinating on one big task.", "Big tasks feel vague, and vague is hard to start. Try making the first step tiny and concrete, something you can do in five minutes, like opening the document and writing the heading. Then set a short timer for 15 minutes of work. What's the task? I can break it into small steps with you."),
 ("I can't decide between two options and I'm stuck.", "Let's make it concrete. Tell me the two options and what matters most to you. I'll lay out pros and cons against those criteria, point out what's uncertain and suggest a small experiment or deadline if the choice isn't clear. I can help structure the decision, but the choice should be yours."),
 ("I have too many tabs, ideas and notes everywhere. Help me get organised.", "A simple system helps: one inbox where everything gets captured, then a short daily review where each item becomes a task with a next action, a reference note or a delete. If you paste a messy list of ideas and tasks, I'll sort it into those groups."),
 ("I feel overwhelmed by my to-do list.", "Let's shrink it. First, choose the three things that would make today a success. Everything else can wait or be scheduled. If you paste the list, I'll help sort it by urgency and effort, and suggest what to defer. You don't need to do it all today."),
 ("I need to get back on track with my studies after a bad week.", "A bad week happens, and the plan is just to restart small. Pick the one subject most urgent for the next deadline and plan a short session today. Then rebuild a simple weekly schedule. Tell me your deadlines and available hours and I'll draft it."),
 ("Help me say no to a request from my manager without sounding bad.", "You can be firm and cooperative at once. Acknowledge the request, state your constraint with a fact, and offer an alternative: \"I'd like to help, but I'm committed to X until Thursday. I could take it on next week, or we could decide which of the two should come first.\" Want me to tailor it to your situation?"),
 ("I have an hour. What's the best use of it?", "Tell me what's on your list and your energy level. As a rule of thumb: use a focused hour for the task that is important but easy to postpone, not the one that is merely loud. If nothing is pressing, rest also counts as a good use of the hour. Share the list and I'll pick."),
 ("My project is behind schedule. What do I do?", "First, get the facts: what's done, what's left, and the real remaining effort. Then your options are reduce scope, add time or add help, and you can combine them. Tell your stakeholders early with a concrete revised plan. If you give me the task list and dates, I'll sketch scenarios."),
 ("Help me prepare for a difficult conversation.", "Let's prepare. Clarify your goal for the conversation, the facts you want to state without blame, how the other person may feel, and one or two concrete requests. A useful opener: \"I'd like to talk about X because I want us to be able to Y.\" Tell me the situation and I can help draft it and rehearse likely responses."),
 ("I keep forgetting things I promised people.", "A reliable capture habit helps: write every commitment down immediately in one place, with a date, and review it each morning. If you want, tell me your commitments from this week and I'll turn them into a dated checklist."),
 ("I don't know where to start with a huge codebase I've inherited.", "Start by running it and its tests, then trace one real user flow from the entry point to the database. Read the README, the folder structure and recent commits for hints. Keep notes of what you learn. If you paste the file tree or a key file, I can help you map it."),
]
for i, (q, a) in enumerate(HELP):
    for rep in range(3):
        sysm = [R.choice(SYS_NEXUS_NONE), R.choice(SYS_GENERAL), None][rep]
        out.append(mk(CAT, "help_request", [("user", q if rep == 0 else R.choice(["Hi. " + q, q + " Any advice?", q])), ("assistant", a)], f"j6_{i}", "medium", "authored" if rep == 0 else "composed", sysm))

# ======================================================== J7: daily brief / summarise a day's data (computed)
MEET = ["standup", "design review", "1:1 with Sam", "sprint planning", "client call", "lunch with Ana", "retro", "interview panel", "budget review", "team sync"]
for n in range(110):
    day = R.choice(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])
    meets = sorted(R.sample(MEET, R.choice([2, 3, 4])))
    times = sorted(R.sample([9, 10, 11, 13, 14, 15, 16], len(meets)))
    events = [(t, m) for t, m in zip(times, meets)]
    free = [h for h in range(9, 17) if h not in times and h != 12]
    # longest free block
    blocks, cur = [], []
    for h in range(9, 17):
        if h in free:
            cur.append(h)
        else:
            if cur:
                blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    longest = max(blocks, key=len) if blocks else []
    ev_txt = "\n".join(f"- {t}:00 {m}" for t, m in events)
    tasks = R.sample([x[0] for x in TASKS], 3)
    q = R.choice([f"Here's my {day}:\n{ev_txt}\nTasks: {', '.join(tasks)}.\nGive me a quick plan for the day.", f"Plan my {day}. Meetings:\n{ev_txt}\nStill to do: {', '.join(tasks)}.", f"Morning brief please. Calendar for {day}:\n{ev_txt}\nOpen tasks: {', '.join(tasks)}."])
    if longest:
        span = f"{longest[0]}:00 to {longest[-1]+1}:00"
        block_txt = f"Your longest free block is {span} ({len(longest)} hour{'s' if len(longest)>1 else ''}), which is best used for the task that needs the most focus: {tasks[0]}."
        rest = f"Fit {tasks[1]} and {tasks[2]} into the shorter gaps."
    else:
        block_txt = "There's no long free block today; you'll have to work in small gaps."
        rest = f"Pick just one of: {', '.join(tasks)}, and move the others."
    a = f"Here's a plan for {day}:\n\n**Meetings:** {len(events)} (first at {events[0][0]}:00, last at {events[-1][0]}:00).\n\n{block_txt} {rest}\n\nI've only used the details you gave me; I can't see your actual calendar, so tell me if anything is missing or flexible."
    out.append(mk(CAT, "daily_brief", [("user", q), ("assistant", a)], f"j7_{n}", "medium", "computed", R.choice(SYS_NEXUS_NONE + [None])))

# ======================================================== J8: concise vs detailed (style control)
STYLE = [
 ("Explain what an API is. One sentence.", "An API is a defined way for one program to ask another for data or actions, without needing to know how the other works inside."),
 ("Explain what an API is, in detail.", "An API (application programming interface) is a contract between pieces of software. It defines which requests you can send, what format they take, and what responses you'll get back. For example, a weather app doesn't measure the weather itself; it sends a request to a weather service's API with a location, and receives data like temperature and forecast in a structured format (often JSON). The API hides the internal implementation, so the service can change how it works without breaking the apps that use it, provided the contract stays the same. APIs also commonly handle authentication, rate limits and versioning so providers can control and evolve access."),
 ("Briefly: why do we use version control?", "It records every change so you can review history, undo mistakes, work on features in parallel and collaborate safely."),
 ("Give me the long version: why do we use version control?", "Version control tracks every change to your files over time, which gives you several things at once. First, safety: you can go back to any earlier state if something breaks. Second, history: each commit explains what changed and why, which helps debugging and onboarding. Third, parallel work: branches let people develop features independently and merge them later. Fourth, collaboration: many people can work on the same codebase without overwriting each other. Finally, it supports review and automation, since changes can be inspected before merging and tests can run automatically on every push."),
 ("TL;DR of the Pomodoro technique?", "Work 25 minutes with full focus, take a 5-minute break, and after four rounds take a longer break."),
 ("Explain the Pomodoro technique thoroughly.", "The Pomodoro technique structures work into focused intervals, traditionally 25 minutes, separated by short breaks of about 5 minutes. After four intervals you take a longer break of 15 to 30 minutes. The idea is that a fixed, short time box makes starting easier, protects you from distractions during the interval, and makes progress visible through a count of completed rounds. Practical tips: pick one task per interval, jot down interruptions to handle later instead of switching, and adjust the length (some people prefer 50 and 10). It works best for tasks that need concentration, and less well for work that requires deep immersion over long stretches."),
 ("Short answer: is a tomato a fruit or a vegetable?", "Botanically a fruit; in cooking it's treated as a vegetable."),
 ("Short answer: should I use tabs or spaces?", "Follow your project's existing convention; if there is none, pick one and enforce it with a formatter."),
 ("In one line, what's the difference between a list and a tuple in Python?", "Lists are mutable; tuples are immutable."),
 ("Keep it brief: what is inflation?", "A general rise in prices over time, which lowers what each unit of money can buy."),
 ("Be thorough: what is inflation?", "Inflation is a sustained increase in the general price level, meaning the same amount of money buys less over time. It's measured with indices such as the consumer price index, which tracks the cost of a typical basket of goods. Causes include demand outpacing supply, rising production costs (energy, wages), and growth in the money supply. Moderate inflation, around 2%, is often seen as normal, while high inflation erodes savings and makes planning difficult, and deflation can hurt spending and growth. Central banks respond mainly by adjusting interest rates, and individuals can protect against it by investing in assets that tend to keep pace with prices."),
 ("Explain recursion like I'm five.", "Imagine you're looking for your toy in a box that has smaller boxes inside it. You open a box, and if the toy isn't there you open the smaller box inside, and keep going until you find it. Doing the same thing again on something smaller is recursion."),
 ("Explain recursion to a senior engineer.", "Recursion is defining a computation in terms of smaller instances of itself with a base case, mapping naturally to inductive structures such as trees. The practical concerns are stack depth (convert to iteration or use an explicit stack when depth is unbounded), repeated subproblems (memoise or use dynamic programming), and tail-call behaviour, which most mainstream runtimes don't eliminate."),
]
for i, (q, a) in enumerate(STYLE):
    for rep in range(2):
        sysm = [None, R.choice(SYS_GENERAL)][rep]
        out.append(mk(CAT, "style_control", [("user", q), ("assistant", a)], f"j8_{i}", "easy", "authored", sysm))

# ======================================================== J9: assistant utility tasks (meeting notes, reminders, drafts)
NOTES = [
 ("Turn these notes into action items: 'Discussed launch date, Sam to check legal by Wed, Priya will update the pricing page, we decided to delay the blog post to next week, need a volunteer for QA.'",
  "Action items:\n- Sam: check with legal by Wednesday.\n- Priya: update the pricing page.\n- Blog post: postponed to next week (decision).\n- QA: owner needed, still unassigned.\n\nOpen question: what is the final launch date? It was discussed but not recorded in your notes."),
 ("Summarise: 'Call with Dana. She wants the invoice by Friday, thinks the second milestone slipped, and asked if we can add a reporting feature in Q3. I said I'd check with the team.'",
  "Summary: Dana wants the invoice by Friday, believes the second milestone is late, and asked about adding a reporting feature in Q3. You promised to check with the team.\n\nFollow-ups: send the invoice by Friday, confirm the milestone status, and reply on the Q3 reporting feature after talking to the team."),
 ("Write a short reminder message: dentist appointment Tuesday 4:30 pm, bring insurance card.",
  "Reminder: Dentist appointment on Tuesday at 4:30 pm. Bring your insurance card.\n\nI can't set an actual reminder on your device from here, so add it to your calendar or phone alarm."),
 ("Draft a polite email asking my landlord to fix the heater.",
  "Subject: Heater repair request - [your address]\n\nHi [Landlord's name],\n\nI hope you're well. The heater in my apartment has stopped working properly as of [date]. It [describe the problem, e.g. does not turn on / makes a loud noise]. As temperatures are dropping, I'd appreciate it if it could be looked at as soon as possible.\n\nCould you let me know when someone can come by, and which times work for access? I'm available [times].\n\nThank you for your help.\n\nBest regards,\n[Your name]\n\nI can't send it for you; copy it into your email and fill in the brackets."),
 ("Rewrite this to sound more professional: 'hey, can u send me the files asap, thx'",
  "Hello, could you please send me the files at your earliest convenience? Thank you."),
 ("Make a packing list for a 3-day business trip.",
  "Packing list:\n- Documents: ID or passport, tickets, booking confirmations, company card\n- Tech: laptop and charger, phone and charger, adapter, headphones\n- Clothes: 3 work outfits, one smart casual set, underwear and socks, sleepwear, comfortable shoes\n- Toiletries: toothbrush, deodorant, medications, small skincare items\n- Extras: notebook and pen, water bottle, umbrella\n\nTell me the destination and weather and I'll adjust it."),
 ("Create an agenda for a 30-minute project kickoff.",
  "Kickoff agenda (30 min):\n1. Welcome and goals (5 min): why this project matters.\n2. Scope and deliverables (7 min): what's in and out.\n3. Roles and responsibilities (5 min).\n4. Timeline and milestones (7 min).\n5. Risks and open questions (4 min).\n6. Next steps and owners (2 min).\n\nSend the agenda in advance and record decisions and actions."),
 ("Give me three title options for a blog post about remote work productivity.",
  "1. Working From Anywhere Without Losing Focus: 7 Habits That Work\n2. Remote Work, Real Results: How to Stay Productive at Home\n3. Beyond the Home Office: A Practical Guide to Productive Remote Work"),
 ("Translate this to simple English: 'The committee has deferred its decision pending further deliberation.'",
  "The committee will decide later, after they think about it more."),
 ("Rank these by urgency: pay rent (due tomorrow), renew gym membership (next month), reply to recruiter (they asked yesterday), organise bookshelf.",
  "1. Pay rent: due tomorrow, with penalties for being late.\n2. Reply to recruiter: they're waiting, and quick replies matter.\n3. Renew gym membership: next month, so there's time.\n4. Organise bookshelf: no deadline.\n\nThat ordering is based on deadlines and consequences you've described."),
]
for i, (q, a) in enumerate(NOTES):
    for rep in range(3):
        sysm = [R.choice(SYS_NEXUS_NONE), R.choice(SYS_GENERAL), None][rep]
        out.append(mk(CAT, "assistant_utility", [("user", q if rep == 0 else R.choice([q, "Please " + lf(q), q + " Keep it clear."])), ("assistant", a)], f"j9_{i}", "medium", "authored" if rep == 0 else "composed", sysm))

save("jarvis", out)
