"""Category 8: NEXUS knowledge (target ~750). All facts come from nexus_facts.py (Phase 1 report)."""
from lib import *
from nexus_facts import FACTS, FALSE_PREMISES, STATUS_PREFIX

CAT = "nexus_knowledge"
rng = seeded("nexus")
out = []


def sysk():
    return pick(rng, SYS_NEXUS_KNOW)


# --- 1. per-fact Q&A (3 each, different answer styles) ---
for fid, st, qs, short, long_ in FACTS:
    # a) short answer, first phrasing
    out.append(mk(CAT, "fact_short", [("user", qs[0]), ("assistant", short)], fid, "easy", "authored", sysk()))
    # b) long answer, second phrasing (or first if only one)
    q2 = qs[1] if len(qs) > 1 else qs[0]
    out.append(mk(CAT, "fact_detail", [("user", q2), ("assistant", long_)], fid, "medium", "authored", sysk()))
    # c) status-labelled answer
    q3 = qs[2] if len(qs) > 2 else qs[-1]
    pre = pick(rng, STATUS_PREFIX[st])
    body = short[0].lower() + short[1:] if pre.endswith(": ") or pre.endswith(", ") else short
    out.append(mk(CAT, "fact_status", [("user", q3), ("assistant", pre + body)], fid, "medium", "composed", sysk()))

# --- 2. false-premise corrections (each in 2 framings) ---
for i, (q, a) in enumerate(FALSE_PREMISES):
    out.append(mk(CAT, "false_premise", [("user", q), ("assistant", a)], f"fp{i}", "hard", "authored", sysk()))
    out.append(mk(CAT, "false_premise", [("user", "Quick check before I rely on this: " + q[0].lower() + q[1:]), ("assistant", "Worth checking, because the premise is off. " + a)],
                  f"fp{i}", "hard", "composed", sysk()))

# --- 3. 'can NEXUS do X today?' capability questions: (question, verdict, explanation) ---
CAPS = [
 ("create a task from a typed sentence", "C", "Yes, through the propose and confirm flow: the sentence is classified, a task proposal is built with hand-written extraction, and the task is created only after you confirm."),
 ("create a task just by speaking", "C", "Partly. The browser transcribes your speech to text and the same classifier path can be used, but the voice assistant page currently shows a routing decision; creating the task still requires the review and confirm step."),
 ("answer a general knowledge question", "N", "No. There is no generative model, so a question like that is at most classified, typically as out_of_scope or deep_reasoning, and NEXUS reports it cannot answer."),
 ("write or debug code for me", "N", "No. A coding request is recognised as code_assist, but NEXUS then answers generation_unavailable because it has no language model."),
 ("show my overdue tasks", "C", "Yes, through the task and search surfaces and the Command Center priority queue; these are ordinary application features backed by your own data."),
 ("plan my week", "C", "In the sense of the planner module, yes: availability, time blocks, focus sessions and scheduling suggestions exist. A conversational 'plan my week' reply from an AI does not exist yet."),
 ("browse a website for me", "N", "No. There is no browser or web tool in NEXUS today."),
 ("open or edit a file on my computer", "N", "No. NEXUS has no file tools. The only local-system feature is the developer module, which runs git against registered repositories."),
 ("analyse my git commits", "C", "Yes. Register a local repository and the developer module scans commits, branches, changed lines and languages."),
 ("remember what we talked about yesterday", "N", "No. The assistant keeps at most 20 turns in the browser, does not persist them and the classifier sees one utterance at a time."),
 ("speak its responses aloud", "C", "Yes, in the sense that the assistant page reads a routing statement aloud with the browser's text-to-speech. It does not read out composed answers because it does not produce any."),
 ("transcribe my voice on-device", "N", "No. Transcription is done by the browser's speech service, which in Chrome and Edge sends audio to the vendor. NEXUS has no local speech model."),
 ("search across my notes, tasks and projects", "C", "Yes. Global search and the command palette cover projects, tasks, notes, resources, concepts, repositories, learning goals, skills, risks and more, scoped to your account."),
 ("tell me what needs my attention", "C", "Yes. The risk detection engine, recommendations and the Command Center priority queue exist for exactly that, based on your recorded data."),
 ("send an email or message for me", "N", "No. NEXUS has no email or messaging capability."),
 ("run a shell command", "N", "No. There is no terminal tool."),
 ("track my learning goals and skills", "C", "Yes, the learning module covers goals, skills, activities, progress and skill gaps."),
 ("keep records for my career and portfolio", "C", "Yes, the career module stores a profile, records and portfolio evidence."),
 ("delete a project by voice without asking", "N", "No. Destructive actions can only go through propose and confirm, so you must review and confirm; nothing runs automatically from speech."),
 ("run a local Qwen model", "N", "No. Qwen is planned but nothing has been trained or integrated."),
 ("have a back-and-forth conversation", "N", "Not yet. A conversational assistant is the goal of the Qwen plan. Today the assistant page routes single requests."),
 ("work offline", "C", "Mostly yes. The backend, database and classifier run locally. The exceptions are browser speech recognition, which needs a network connection, and anything you host remotely."),
 ("explain why a risk was flagged", "C", "Yes. Each detected risk keeps the evidence it was scored from, so the Risk Center can show what triggered it."),
 ("prioritise my tasks automatically", "C", "In part: the Command Center priority queue and the planner's suggestions rank work using recorded data. They are rule-based and analytic, not an AI that reasons about your goals."),
 ("call external APIs for me", "N", "No. There is no external-API tool, and no generic tool layer."),
 ("update a task's status through the assistant", "C", "Yes, as a proposed action. SET_TASK_STATUS-style proposals exist in the closed action set, and you confirm before the service runs."),
 ("export my data to a spreadsheet", "N", "I haven't found an export feature in the audit, so I wouldn't promise one. Check the app's pages, or treat it as not available."),
]
CAP_FRAMES = [
 "Can NEXUS {x} today?",
 "Right now, is NEXUS able to {x}?",
 "I want to know if NEXUS can {x}. Does that work at the moment?",
 "does nexus support the ability to {x}?",
]
for i, (x, st, expl) in enumerate(CAPS):
    for k in range(2):
        q = CAP_FRAMES[(i + k * 2) % 4].format(x=x)
        lead = {"C": ["Yes. ", "Yes, with limits. "], "N": ["No. ", "Not today. "]}[st]
        # 'partly' answers already start with their own qualifier; avoid double yes/no
        first = expl.split()[0].strip(",.")
        if first in ("Yes", "No", "Not", "Partly", "Mostly", "In", "I"):
            a = expl
        else:
            a = pick(rng, lead) + expl
        out.append(mk(CAT, "capability_check", [("user", q), ("assistant", a)], f"cap{i}", "medium", "composed", sysk()))

# --- 4. built vs planned quiz ---
FEATS = [
 ("a DeBERTa intent classifier", "C"), ("propose and confirm actions", "C"), ("browser-based voice input", "C"),
 ("global search", "C"), ("a Command Center page", "C"), ("a PostgreSQL database layer", "C"),
 ("a generative chat model", "N"), ("Ollama integration", "N"), ("a web search tool", "N"), ("a file-editing tool", "N"),
 ("a terminal tool", "N"), ("a chat interface", "N"), ("Whisper speech recognition", "N"),
 ("a fine-tuned Qwen3-8B", "P"), ("a tool registry with permissions", "P"), ("a separate inference process for Qwen", "P"),
 ("a chat endpoint with streaming", "P"), ("conversation history stored on the server", "P"),
]
for i in range(70):
    items = rng.sample(FEATS, 4)
    names = "; ".join(f for f, _ in items)
    q = pick(rng, ["Which of these exist in NEXUS today and which don't: {n}?", "Sort these into built, planned, or not present: {n}.",
                   "Quick status check on {n}. What's real right now?"]).format(n=names)
    lines = []
    for f, st in items:
        lab = {"C": "exists today", "N": "does not exist today", "P": "planned, not built"}[st]
        lines.append(f"- {f}: {lab}")
    a = "\n".join(lines)
    if any(st == "N" for _, st in items):
        a += "\n\nOne caution: the README's wording is broader than the code for some AI features, so I am going by the code audit."
    out.append(mk(CAT, "built_vs_planned", [("user", q), ("assistant", a)], f"quiz{i}", "medium", "composed", sysk()))

# --- 5. multi-turn pairs ---
byid = {f[0]: f for f in FACTS}
PAIRS = [("what_is", "modules"), ("modules", "ml_model"), ("ml_model", "classifier_accuracy"), ("ml_model", "no_llm"), ("no_llm", "qwen_plan"),
 ("qwen_plan", "training_not_access"), ("qwen_plan", "kaggle_plan"), ("kaggle_plan", "isolation"), ("voice_stt", "voice_privacy"),
 ("voice_stt", "voice_tts"), ("voice_scope", "voice_future"), ("assistant_page", "history_not_sent"), ("propose_confirm", "slot_extraction"),
 ("propose_confirm", "generation_unavailable"), ("intent_list", "generation_unavailable"), ("no_tools", "tool_layer_plan"),
 ("no_web", "tool_layer_plan"), ("no_ollama", "no_llm"), ("deberta_future", "qwen_plan"), ("auth", "database"), ("frontend", "backend"),
 ("backend", "api_versioning"), ("api_versioning", "frontend_backend"), ("command_center", "risks"), ("risks", "planner"),
 ("developer_module", "learning_career"), ("search", "palette"), ("ml_runtime", "ml_endpoints"), ("confidence_threshold", "classifier_accuracy"),
 ("roadmap", "evaluation_gate"), ("roadmap", "dataset_phase"), ("serving_plan", "local_inference"), ("local_inference", "kaggle_plan"),
 ("one_model_rule", "qwen_plan"), ("design_principles", "propose_confirm"), ("no_chat_ui", "voice_future"), ("brain_body", "tool_layer_plan"),
 ("tests", "ml_runtime"), ("history_not_sent", "voice_scope"), ("qwen_not_trained", "kaggle_plan")]
FOLLOW = ["Okay. And {q}", "Got it. Next question: {q}", "Thanks. Separately, {q}", "Interesting. {q}", "Alright, {q}", "Understood. Now, {q}"]
for i, (a_id, b_id) in enumerate(PAIRS):
    A, B = byid[a_id], byid[b_id]
    for v in range(3):
        qa = A[2][v % len(A[2])]
        qb = B[2][(v + 1) % len(B[2])]
        ans_a = A[3] if v % 2 == 0 else A[4]
        ans_b = B[4] if v % 2 == 0 else B[3]
        fq = pick(rng, FOLLOW).format(q=qb[0].lower() + qb[1:])
        out.append(mk(CAT, "multi_turn_facts", [("user", qa), ("assistant", ans_a), ("user", fq), ("assistant", ans_b)],
                      f"mt_{a_id}_{b_id}", "medium", "composed", sysk(), {"turns": 2}))

# --- 6. synthesis / reasoning about NEXUS ---
SYN = [
 ("Why can't the current NEXUS assistant just answer my questions?", "Because the only model behind it is an intent classifier. It maps a sentence to one of 14 labels such as task_manage or risk_query. It was never trained to write text, so it can route a request but cannot compose an answer. That is the gap the planned Qwen3-8B model is meant to fill."),
 ("If Qwen gets integrated, what stays the same in NEXUS?", "The data model, accounts and permissions, REST services, planner, analytics, risks and the rest of the application would stay. Qwen would be added as a new reasoning layer that calls those services through controlled tools. The propose and confirm safety flow for changing data should carry over, because it keeps the user in control."),
 ("What is the main risk of giving the assistant file and terminal access?", "Those are the largest security surface. A model can misunderstand or be manipulated, so the plan is to expose such tools only behind allowlisted paths and commands, schema-validated arguments, step limits, audit logging and confirmation for anything destructive. Training the model to be careful is not enough; the runtime has to enforce the limits."),
 ("Why not just let the model write straight to the database?", "NEXUS deliberately keeps the model away from the database. Services own the business rules and owner scoping, so a model should request an action that a service validates and, for writes, that the user confirms. That keeps behaviour predictable even if the model is wrong."),
 ("Is NEXUS local-first even with voice?", "Mostly, with one exception. The app, database and classifier run on your machine, and speech output is local. But speech recognition through Chrome or Edge sends audio to the browser vendor, so voice input is not fully local, and NEXUS states this on the assistant page."),
 ("What would I lose if the classifier checkpoint were missing?", "Only the ML features. The backend would still start and serve every other route, and the ML endpoints would answer 503 ml_unavailable, so tasks, projects, planner, analytics and the rest would keep working."),
 ("Why is the 75% result important?", "It shows the classifier is less accurate on real wording than its headline number suggests. The 0.974 score came from template-generated data, while hand-written requests scored 42 of 56. It is a warning against trusting synthetic test sets, and it is why the Qwen dataset work stresses diverse, human-style phrasing."),
 ("How does the README differ from the code about AI features?", "The README describes Ollama integration and web intelligence, but a code audit found no Ollama code and no web client in the backend, and the project's own docs say NEXUS runs no language model. When the README and the code disagree, trust the code and treat the README claims as unimplemented."),
 ("What is the difference between routing and answering in NEXUS?", "Routing means deciding which part of NEXUS a request belongs to, which the classifier does. Answering means producing a reply in language, which needs a generative model. NEXUS routes today and does not answer."),
 ("Where would a future chat feature plug in?", "At a new backend chat endpoint backed by a separate Qwen inference process, with a chat view or the existing /assistant hook calling it. The tool layer would sit between the model and the existing services. None of these pieces exist yet."),
 ("Why was Whisper rejected for voice?", "Because of the one-model rule: adding Whisper would have meant a second set of weights to train, serve and trust. The cost of using the browser instead is that audio goes to the vendor in Chrome and Edge, and Firefox has no recognition at all."),
 ("What does 'evidence over assumptions' mean in NEXUS?", "NEXUS tries to separate what it knows from what it estimates. For example, a git commit is evidence that a commit happened, not that a certain number of hours were worked, and analytics are based only on information actually recorded."),
]
for i, (q, a) in enumerate(SYN):
    out.append(mk(CAT, "synthesis", [("user", q), ("assistant", a)], f"syn{i}", "hard", "authored", sysk()))
    out.append(mk(CAT, "synthesis", [("user", "In one or two sentences: " + q[0].lower() + q[1:]), ("assistant", ". ".join(a.split(". ")[:2]).rstrip(".") + ".")],
                  f"syn{i}", "medium", "composed", sysk()))

save("nexus", out)
