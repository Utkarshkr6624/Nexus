"""Category 7 top-up: computed weekly plans and routines, meeting prep, drafting, 'what do you need from me', project next-actions."""
from lib import *

CAT = "jarvis_assistant"
R = seeded("jarvis2")
out = []


def lf(s):
    return s[0].lower() + s[1:] if s else s


def sysm():
    return R.choice(SYS_NEXUS_NONE + [None, None, R.choice(SYS_GENERAL)])


# ---------------------------------------------------------------- weekly plan (computed fill into days)
TASKS = [("write the design doc", 4), ("review the pull requests", 2), ("prepare the client demo", 3), ("clean the test data", 2), ("record the training video", 3), ("update the budget sheet", 2),
         ("fix flaky tests", 3), ("plan the next sprint", 2), ("study for the certification", 4), ("write the monthly newsletter", 2), ("migrate the old reports", 5), ("interview prep", 2),
         ("redesign the landing page", 6), ("organise the shared drive", 2), ("draft the proposal", 4), ("refactor the billing module", 5), ("learn the new framework", 3), ("write release notes", 1)]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
for n in range(110):
    k = R.choice([4, 5, 6])
    ts = R.sample(TASKS, k)
    cap = {d: R.choice([2, 3, 4, 5]) for d in R.sample(DAYS, R.choice([3, 4, 5]))}
    days = [d for d in DAYS if d in cap]
    remaining = {d: cap[d] for d in days}
    plan = {d: [] for d in days}
    unplaced = []
    for name, h in ts:
        placed = False
        for d in days:
            if remaining[d] >= h:
                plan[d].append((name, h))
                remaining[d] -= h
                placed = True
                break
        if not placed:
            unplaced.append((name, h))
    cap_txt = ", ".join(f"{d} {cap[d]}h" for d in days)
    task_txt = "; ".join(f"{t} ({h}h)" for t, h in ts)
    q = R.choice([f"Plan my week. Free hours: {cap_txt}. Tasks in priority order: {task_txt}.", f"I have these tasks: {task_txt}. My available hours are {cap_txt}. Lay out the week.", f"Can you schedule these for me? Tasks (priority order): {task_txt}. Capacity: {cap_txt}."])
    lines = [f"**{d}** ({cap[d]}h available): " + ("; ".join(f"{t} ({h}h)" for t, h in plan[d]) if plan[d] else "nothing assigned") + (f", {remaining[d]}h spare" if remaining[d] else "") for d in days]
    a = "I filled the days in priority order, putting each task on the first day with enough room:\n\n" + "\n".join(lines)
    if unplaced:
        a += "\n\nDidn't fit: " + "; ".join(f"{t} ({h}h)" for t, h in unplaced) + ". Options: split a task across days, extend a day, or move it to next week."
    else:
        a += "\n\nEverything fits, with the leftover time as a buffer for surprises."
    a += "\n\nI can't add this to a calendar from here, so copy what you need."
    out.append(mk(CAT, "weekly_plan", [("user", q), ("assistant", a)], f"j2w_{n}", "medium", "computed", sysm()))

# ---------------------------------------------------------------- routine builder (computed time blocks)
GOALS = [("study for an exam", "focused study"), ("learn guitar", "practice"), ("work on my side project", "project work"), ("train for a 10k", "running"), ("learn Spanish", "language practice"), ("write my thesis", "writing"),
         ("read more books", "reading"), ("get better at drawing", "drawing practice")]
for n in range(60):
    g, label = R.choice(GOALS)
    start = R.choice([6, 7, 18, 19, 20])
    block = R.choice([25, 30, 45, 60])
    brk = 10 if block >= 45 else 5
    reps = R.choice([2, 3])
    minutes = 0
    sched = []
    t = start * 60
    for r in range(reps):
        sched.append((t, t + block, label))
        t += block
        if r < reps - 1:
            sched.append((t, t + brk, "break"))
            t += brk
    fmt = lambda m: f"{m // 60:02d}:{m % 60:02d}"
    q = R.choice([f"I want to {g}. Build me a daily routine starting at {start}:00 with blocks of {block} minutes, {reps} blocks.", f"Make a simple daily schedule to {g}: {reps} blocks of {block} minutes starting {start}:00."])
    a = "Here's a simple routine:\n\n" + "\n".join(f"- {fmt(s)} to {fmt(e)}: {l}" for s, e, l in sched) + f"\n\nThat is {block * reps} minutes of {label} in total, finishing at {fmt(sched[-1][1])}. Keep it for a week, then adjust the block length if it feels too short or too long. I can't set alarms for you from here."
    out.append(mk(CAT, "routine_builder", [("user", q), ("assistant", a)], f"j2r_{n}", "easy", "computed", sysm()))

# ---------------------------------------------------------------- meeting prep (authored per topic)
MEET = [
 ("a project kickoff", "Align on the goal, scope, roles and timeline.", ["What does success look like?", "What is out of scope?", "Who owns each area?", "What are the biggest risks?"], "Send the agenda a day ahead and end with owners and dates for next steps."),
 ("a performance review with an employee", "Give honest, specific feedback and agree on growth goals.", ["What went well, with examples?", "Where is there room to grow?", "What support do they need?", "What are the goals for the next quarter?"], "Prepare examples in advance, and leave time for them to speak first."),
 ("a client status call", "Share progress, surface risks and confirm priorities.", ["What was delivered since last time?", "What is blocked or at risk?", "What do you need from the client?", "What happens next and when?"], "Send a short written summary after the call."),
 ("a retrospective", "Learn from the last sprint and agree on a few improvements.", ["What went well?", "What didn't?", "What will we try next sprint?"], "Limit yourselves to two or three improvements so they actually happen."),
 ("a budget discussion", "Agree on priorities and spending limits.", ["What are the fixed costs?", "Where can we cut or defer?", "What is the expected return of the new items?"], "Bring figures in one table and agree on the decision rule before looking at specifics."),
 ("an interview with a candidate", "Assess skills and fit fairly and consistently.", ["Can you describe a project you're proud of?", "How do you handle disagreement?", "Tell me about a mistake and what you learned."], "Use the same core questions for every candidate and score them against a rubric."),
 ("a one-to-one with my manager", "Get clarity on priorities, feedback and growth.", ["What should I focus on this month?", "How am I doing against expectations?", "What skills would help me progress?"], "Send a few topics beforehand and write down what you agree."),
 ("a conversation about a salary raise", "Make a clear, evidence-based case.", ["What impact have I had?", "What does the market pay for this role?", "What is a fair range to ask for?"], "Pick a specific number, rehearse your first two sentences and ask when you can expect an answer."),
 ("a parent-teacher meeting", "Understand progress and agree on support.", ["How is my child doing academically and socially?", "What are their strengths?", "How can we support them at home?"], "Bring your questions in writing and note the agreed actions."),
 ("a stand-up", "Share progress and blockers quickly.", ["What did you do yesterday?", "What will you do today?", "What's blocking you?"], "Keep it under 15 minutes and take long discussions offline."),
 ("a negotiation with a supplier", "Reach a fair deal on price, quality and terms.", ["What is our best alternative?", "Which terms matter most to us?", "What can we offer in return?"], "Decide your walk-away point beforehand and ask for the offer in writing."),
 ("a design review", "Get focused feedback on a proposal before building.", ["What problem does this solve?", "What alternatives did we consider?", "What could go wrong?"], "Share the document early and ask reviewers for blocking issues versus suggestions."),
 ("a community meeting about a local issue", "Gather views and agree on next steps.", ["What are the main concerns?", "Who is affected?", "What can we realistically do?"], "Agree ground rules for speaking, and record decisions and volunteers."),
 ("a team offsite planning session", "Decide goals, agenda and logistics.", ["What outcome do we want?", "Who needs to attend?", "What is the budget?"], "Send a short survey first so the session is spent deciding, not collecting."),
 ("a meeting with a mentor", "Make good use of their time.", ["What am I trying to solve?", "What have I tried?", "What would you do in my place?"], "Send a short update beforehand and follow up with what you did afterwards."),
 ("a product demo for stakeholders", "Show value and collect decisions.", ["What problem does this solve for them?", "What do we need them to approve?", "What questions might they ask?"], "Rehearse the happy path, prepare a backup recording and leave time for questions."),
 ("a difficult conversation with a colleague", "Resolve a recurring issue respectfully.", ["What exactly happened, in concrete terms?", "How did it affect the work?", "What do I want to change?"], "Choose a private time, describe behaviour rather than character and listen before proposing solutions."),
 ("a quarterly planning meeting", "Choose a few priorities for the next quarter.", ["What did we learn last quarter?", "What are the top three priorities?", "What will we stop doing?"], "Bring data and limit the number of priorities to what the team can finish."),
 ("a customer discovery call", "Learn about the customer's real problems.", ["What is hardest about this today?", "How do you solve it now?", "What would a good solution change for you?"], "Ask open questions, listen more than you talk and avoid pitching."),
 ("an incident post-mortem", "Understand what happened and prevent a repeat.", ["What was the timeline?", "What was the root cause?", "What will we change?"], "Keep it blameless and end with owners and dates for each action."),
]
FR = ["Help me prepare for {t}.", "I have {t} tomorrow. What should I do beforehand?", "Give me an agenda and questions for {t}.", "How do I make {t} go well?"]
for i, (t, goal, qs, tip) in enumerate(MEET):
    for k in range(3):
        q = FR[(i + k) % 4].format(t=t)
        a = f"Goal for {t}: {goal}\n\nQuestions worth covering:\n" + "\n".join(f"- {x}" for x in qs) + f"\n\nTip: {tip}\n\nTell me the people, time and context and I'll turn this into a proper agenda."
        out.append(mk(CAT, "meeting_prep", [("user", q), ("assistant", a)], f"j2m_{i}", "medium", "authored" if k == 0 else "composed", sysm()))

# ---------------------------------------------------------------- email / message drafting (intent x slots)
DRAFTS = [
 ("ask my manager for feedback on my project", "Subject: Quick feedback request on {proj}\n\nHi {name},\n\nI've finished the first version of {proj} and would value your feedback before I go further. Could we spend 15 minutes this week to go through it? I'm free {when}.\n\nThanks,\n[Your name]"),
 ("politely chase an unpaid invoice", "Subject: Reminder: invoice for {proj}\n\nHi {name},\n\nI hope you're well. This is a friendly reminder that the invoice for {proj}, sent on [date], is still unpaid. Could you let me know when I can expect payment? I've attached a copy for convenience.\n\nBest regards,\n[Your name]"),
 ("decline a meeting invitation", "Hi {name},\n\nThank you for the invitation to discuss {proj}. Unfortunately I can't attend because of a conflict. Could you share the notes afterwards, or would {when} work for a quick catch-up?\n\nBest,\n[Your name]"),
 ("introduce two colleagues to each other", "Hi {name} and [second person],\n\nI'd like to introduce you both. {name} works on {proj}, and I think you could help each other. I'll let you take it from here.\n\nBest,\n[Your name]"),
 ("ask for a deadline extension", "Subject: Request for a short extension on {proj}\n\nHi {name},\n\nI'd like to ask whether the deadline for {proj} could move to {when}. [One-sentence honest reason.] I'm making progress and will send an update by [date] if that helps.\n\nThank you for considering it,\n[Your name]"),
 ("follow up after an interview", "Subject: Thank you for the interview\n\nHi {name},\n\nThank you for taking the time to talk with me about the {proj} role. I enjoyed learning about the team and I'm very interested. Please let me know if I can provide anything else.\n\nBest regards,\n[Your name]"),
 ("thank a colleague for their help", "Hi {name},\n\nThank you for helping with {proj}. Your input on [specific thing] made a real difference, and I appreciate the time you took.\n\nBest,\n[Your name]"),
 ("report a delay on a project", "Subject: Update on {proj}\n\nHi {name},\n\nI wanted to let you know that {proj} is running about [X days] behind schedule because of [reason]. The revised date is {when}. I'm taking [specific action] to recover time and will update you on [date].\n\nPlease tell me if this affects anything on your side.\n\n[Your name]"),
 ("request a reference from a former boss", "Subject: Reference request\n\nHi {name},\n\nI hope you're doing well. I'm applying for [role] and wondered if you'd be willing to act as a reference. Our work on {proj} is something I'd like to highlight. I'm happy to send my CV and details.\n\nThank you,\n[Your name]"),
 ("ask a neighbour to keep the noise down", "Hi {name},\n\nI wanted to mention that the noise in the evenings has been carrying through the wall, and it's affecting my sleep. Would you mind keeping it down after [time]? Thanks very much, I appreciate it.\n\n[Your name]"),
 ("confirm a meeting time", "Hi {name},\n\nJust confirming our meeting about {proj} on {when}. Please let me know if anything changes. I'll bring [materials].\n\nBest,\n[Your name]"),
 ("give notice of resignation", "Subject: Resignation\n\nDear {name},\n\nI'm writing to resign from my position as [role], with my last day being [date], in line with my notice period. Thank you for the opportunities I've had here. I'm happy to help with the handover of {proj}.\n\nSincerely,\n[Your name]"),
]
PROJ = ["the onboarding redesign", "the Q3 report", "the website relaunch", "the data migration", "the vendor contract", "the training programme", "the mobile app", "the annual audit", "the customer survey"]
NAMES = ["Priya", "Sam", "Dana", "Omar", "Lena", "Ravi", "Maya", "Ken"]
WHENS = ["Thursday afternoon", "next Tuesday", "Friday morning", "the 14th", "early next week", "Wednesday at 3"]
for i, (intent, tmpl) in enumerate(DRAFTS):
    for k in range(6):
        proj, name, when = R.choice(PROJ), R.choice(NAMES), R.choice(WHENS)
        draft = tmpl.format(proj=proj, name=name, when=when)
        q = R.choice([f"Draft a message to {intent}. It's about {proj}, the person is {name}, and the date is {when}.", f"Write an email to {intent}. Details: {proj}, {name}, {when}.", f"Help me {intent}. Context: {proj}; recipient {name}; timing {when}."])
        a = draft + "\n\nI can't send it for you, so copy it into your email and fill in the bracketed parts. Want it shorter, warmer or more formal?"
        out.append(mk(CAT, "draft_message", [("user", q), ("assistant", a)], f"j2d_{i}_{k}", "medium", "composed", sysm()))

# ---------------------------------------------------------------- 'what do you need from me' for specific task types
NEED = [
 ("plan a birthday dinner", ["How many people and who are they?", "What's the budget?", "Any dietary needs or allergies?", "Home or restaurant?", "What date and time?"]),
 ("write a cover letter", ["The job description", "Your CV or key experience", "What you want to emphasise", "The company's tone or values"]),
 ("fix a bug in my code", ["The exact error message", "The code around where it happens", "What you expected and what actually happens", "How to reproduce it", "Language and library versions"]),
 ("create a study schedule", ["Exam dates and subjects", "How many hours a day you can study", "Which topics are weakest", "Your best times of day to focus"]),
 ("choose a database for my app", ["What the app does and the data it stores", "Expected number of users and data size", "Query patterns (lookups, reports, searches)", "Your team's experience and hosting constraints"]),
 ("plan a trip", ["Dates and length", "Budget", "Who's travelling", "What you like doing", "Any visa or health constraints"]),
 ("write a speech", ["The occasion and audience", "How long it should be", "The key message", "A few personal stories or facts", "The tone (formal, funny, heartfelt)"]),
 ("prepare for an interview", ["The role and company", "The job description", "Your background", "The interview format", "What worries you most"]),
 ("design a workout plan", ["Your goal", "Current fitness level", "How many days a week", "Equipment available", "Any injuries"]),
 ("review my business plan", ["The plan itself or a summary", "The target customers", "How you expect to make money", "Costs and funding", "Your biggest uncertainties"]),
 ("design a weekly meal plan", ["Number of people", "Dietary preferences or restrictions", "Budget", "Cooking time available", "Foods you dislike"]),
 ("improve my resume", ["Your current resume", "Target roles", "Highlights you're proud of", "Any gaps to explain", "Preferred length and format"]),
 ("set up a home office", ["Space and budget", "What kind of work you do", "Hours spent at the desk", "Equipment you already have", "Noise and lighting situation"]),
 ("write documentation for an API", ["The endpoints and their purposes", "Authentication method", "Request and response examples", "Error codes", "The audience (internal or public)"]),
 ("analyse my sales numbers", ["The data (period, columns)", "The question you want answered", "Any known events that affected sales", "What decision depends on it"]),
 ("choose a laptop", ["Budget", "Main tasks", "Portability needs", "Operating system preference", "How long you plan to keep it"]),
 ("organise a charity event", ["Cause and goal amount", "Expected number of guests", "Venue and date options", "Budget and volunteers", "Permits or insurance needs"]),
 ("learn to code", ["Your goal (job, hobby, project)", "Hours per week", "Previous experience", "Preferred learning style", "Any language or area of interest"]),
 ("budget for a wedding", ["Total budget", "Guest count", "Priorities (venue, food, photography)", "Date and location", "Who contributes"]),
 ("build a landing page", ["The product and audience", "The one action visitors should take", "Brand colours and tone", "Content you already have", "Tools you want to use"]),
 ("negotiate a lower rent", ["Current rent and lease end date", "Comparable listings", "Your history as a tenant", "Your flexibility on terms", "The landlord's likely concerns"]),
 ("write a thesis proposal", ["Your field and topic idea", "The research question", "Methods you might use", "Deadline and word limit", "Your supervisor's expectations"]),
 ("set up CI for my repo", ["Hosting platform", "Language and test commands", "Deployment target", "Secrets handling", "How long builds currently take"]),
 ("plan a product launch", ["The product and audience", "Launch date", "Channels available", "Budget", "Success metrics"]),
 ("get started with investing", ["Your goals and time horizon", "How much you can invest and keep invested", "Your emergency savings", "Your risk tolerance", "Your country, since rules and accounts differ"]),
]
NQ = ["I need help to {t}. What do you need from me?", "What information do you need to {t}?", "I want to {t}. Where do we start, and what should I tell you?", "Before we {t}, what questions do you have?"]
for i, (t, items) in enumerate(NEED):
    for k in range(3):
        q = NQ[(i + k) % 4].format(t=t)
        a = f"To help you {t}, it'd be useful to know:\n\n" + "\n".join(f"- {x}" for x in items) + "\n\nYou don't need all of it at once. Start with what you have, and I'll point out what's missing and make sensible assumptions that I'll tell you about."
        out.append(mk(CAT, "info_needed", [("user", q), ("assistant", a)], f"j2n_{i}", "easy", "authored" if k == 0 else "composed", sysm()))

# ---------------------------------------------------------------- project next-action (authored states)
STATES = [
 ("the website redesign", "designs are approved but nobody has started the build", "Break the build into a first vertical slice, such as the homepage, assign one owner, and set a date for a first working version. Early feedback on something real beats more design review."),
 ("my thesis", "I have lots of notes but no draft", "Write a rough outline with section headings, then spend 25 minutes a day turning notes into messy paragraphs. A bad first draft is far easier to improve than a blank page."),
 ("the mobile app", "it works but crashes on older phones", "Collect crash reports to find the top one or two causes, test on a real or emulated older device, and fix those first. Don't try to fix everything at once."),
 ("the budget spreadsheet", "I keep forgetting to update it", "Make it a 5-minute weekly habit on a fixed day, with a reminder, and simplify the sheet to a handful of categories so it's easy to keep up."),
 ("our team onboarding", "new people take months to be productive", "Write a short checklist for week one, pair each newcomer with a buddy and give them a small real task in the first few days. Ask your latest hire what was confusing."),
 ("the garden project", "I bought plants but haven't planted them", "Plant the hardiest ones first, water well and do the rest in the next free weekend. Plants in pots can wait a few days if kept in the shade and watered."),
 ("the data migration", "the staging test failed on three tables", "Look at the errors per table, fix the one with the clearest cause first, and re-run only that table. Keep a log of what you changed so the production run is repeatable."),
 ("the novel I'm writing", "I'm stuck in the middle", "Skip ahead and write a scene you're excited about, or write a list of ten things that could go wrong for your character next. Momentum matters more than order."),
 ("the product launch", "everything is ready except the marketing copy", "Write a plain-language one-paragraph description, test it on two people unfamiliar with the product and iterate once. Don't wait for perfect copy to set the date."),
 ("the home renovation", "the contractor quote is higher than expected", "Ask for an itemised quote, get at least one other quote and see which items can be phased or simplified. Compare scope, not only the total."),
 ("learning Spanish", "I stalled after three weeks", "Shrink the goal to 10 minutes a day, anchor it to an existing habit and add speaking with a partner once a week. Consistency beats intensity."),
 ("the open-source library I maintain", "there are 80 open issues", "Triage: close duplicates and stale items, label the rest, pick three small 'good first issue' tasks and one important bug to fix this week."),
 ("the study group", "attendance has dropped", "Ask each member what would make it more useful, shorten the sessions, and set a clear topic and a small task for each meeting."),
 ("the annual report", "data from two teams is late", "Send a polite message with a firm date and offer help, draft the other sections now, and leave placeholders. Escalate only if the date passes."),
 ("my side business", "I have few customers", "Talk to five potential customers about their problem this week, then change one thing in your offer based on what you hear. Distribution usually matters more than adding features."),
]
for i, (proj, state, adv) in enumerate(STATES):
    qs = [f"My status on {proj}: {state}. What should I do next?", f"I'm working on {proj}, and {state}. Suggest my next step.", f"Where should I go from here? Context: {proj}; {state}."]
    for k, q in enumerate(qs):
        out.append(mk(CAT, "next_action", [("user", q), ("assistant", adv + " If you tell me your constraints (time, people, deadline), I can make this more specific.")], f"j2s_{i}", "medium", "authored" if k == 0 else "composed", sysm()))

save("jarvis2", out)
