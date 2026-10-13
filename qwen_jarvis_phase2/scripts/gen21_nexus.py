"""Phase 2.1 NEXUS knowledge generator: aggregates the hand-written banks (gen21_nexus_a/b/c, plus bank D below) and adds a limited number of
composed examples built from the fact base (nexus_facts.py).  Every claim traces to nexus_facts.py / qwen_jarvis_phase1 docs.
Hand-written examples are the majority; composed ones use fact answers with new framings (status quiz, doc wording, paired follow-ups)."""
import importlib
from gen21_common import *
import nexus_facts as NF

FACT = {f[0]: f for f in NF.FACTS}
rng = seeded("nexus21")
recs = []
for mod in ("gen21_nexus_a", "gen21_nexus_b", "gen21_nexus_c"):
    recs += importlib.import_module(mod).B.records()
n_hand_banks = len(recs)

# ---------------- bank D: extra hand-written ----------------
D = Bank("nexus_knowledge", "n21d")
d = D.d
K1 = "You are the NEXUS assistant. Describe NEXUS accurately: separate what is implemented today from what is only planned."
K2 = "You are an assistant that knows the NEXUS project. Be precise about what exists now and what is future work. Do not invent features."
d("n21_misc", "Is the audit saying NEXUS is badly built?", "No. The audit found the application architecture sound and well layered. Its findings are about claims that outrun the code (Ollama, web intelligence), a documentation mismatch about the default database, and a schema drift in the ML status type. None of them was fixed, because the audit was read-only.")
d("n21_misc", "What would break if I deleted the ML checkpoint?", "Only the ML features. The classifier endpoints would answer 503 ml_unavailable, and the assistant page couldn't route requests, but projects, tasks, planner, knowledge, search and the rest keep working.")
d("n21_misc", "Can two users see each other's tasks?", "They shouldn't. Data access is owner-scoped and routes check role-based permissions. I can't vouch for every endpoint from the audit, so a security review would be the way to be sure.")
d("n21_misc", "Which part of NEXUS is the most 'AI'?", "The /assistant page together with the DeBERTa classifier and the propose and confirm flow. Everything else is deterministic application logic over your data.")
d("n21_misc", "Why does NEXUS answer 'generation_unavailable' instead of just failing?", "Because it is a known gap, not an error. Two of the 14 intents, code_assist and deep_reasoning, were designed for a language model that doesn't exist yet. Reporting the gap honestly is better than a generic failure or a made-up reply.")
d("n21_misc", "How did the 14 intents get decided?", "They are the taxonomy nexo_intents.v1: twelve map to existing NEXUS areas or services, plus out_of_scope and the two generative ones, code_assist and deep_reasoning. I don't have the design discussion behind them, only the resulting list.")
d("n21_misc", "Does the classifier handle other languages?", "The audit doesn't say, and I have no evidence that it was trained or tested on anything other than English. I wouldn't claim multilingual support.")
d("n21_misc", "Can I tune the 0.90 threshold?", "It is the ML_CONFIDENCE_THRESHOLD setting. Lowering it routes more requests but with more mistakes; raising it routes fewer. The audit notes that the current value was picked on synthetic data and should be tuned against real traffic.")
d("n21_misc", "What would I need to change in the app to add a new intent?", "That is a retraining job, not a configuration change: the taxonomy, the datasets and the model's 14-class head would all change, and a destination for the intent would need to exist. I'd treat it as a planned piece of ML work, and the audit doesn't describe a shortcut.")
d("n21_misc", "Which document describes the voice design?", "docs/phase-12-voice.md describes it, including the one-model constraint and a note on the MlStatusRead schema drift. The code is in frontend/src/features/assistant.")
d("n21_misc", "Where is the speech recognition code?", "In the frontend's assistant feature, in speech-recognition.ts, with a state machine in use-voice-assistant.ts. There is no backend voice code.")
d("n21_misc", "What's the permission needed to call the classifier endpoints?", "analytics.read guards the /ml routes, together with a valid bearer token.")
d("n21_misc", "Do I need to install Qwen to run NEXUS?", "No. Nothing in NEXUS depends on Qwen, and the requirements file deliberately excludes Ollama, Qwen packages and cloud LLM SDKs. Qwen is a planned addition, developed in isolation.")
d("n21_misc", "Who gets to approve the permission policy for tools?", "The integration plan lists user sign-off on the permission policy, a threat model for file, terminal and web tools, a chosen serving runtime and a Phase 6 pass report as prerequisites before integration starts.")
d("n21_misc", "Could the Qwen model run on Kaggle all the time and serve NEXUS?", "That isn't the plan. Kaggle is for training sessions, which have time and quota caps. Serving is planned as a local process, with Kaggle or Colab only as a possible free option for demos if local latency is unacceptable.")
d("n21_misc", "What does 'zero-cost stance' mean for the Qwen plan?", "The plan uses open weights, free Kaggle GPU time and local inference, with no paid APIs. If local speed on the 4 GB GPU is unacceptable, the fallbacks are also free: a smaller quantisation, CPU offload or a temporary Kaggle or Colab session.")
d("n21_misc", "Is Qwen3-8B licensed for this?", "The training plan notes the Qwen3 release is Apache-2.0 according to its announcement, but it says to re-verify the license text before use. I'd treat it as to-be-confirmed.")
d("n21_misc", "How does the dataset treat NEXUS facts it can't verify?", "The NEXUS examples are written from the Phase 1 documents only. Where the code and README disagree, the examples follow the code and label the README claim as unsupported. Planned features are marked planned, and absent ones are marked absent.")
d("n21_misc", "Why does the dataset include 'NEXUS doesn't have this' examples?", "So the future model learns not to invent features. If it only saw positive examples it might confidently describe an Ollama setup or a web tool that doesn't exist, which is exactly the kind of mistake the plan wants to avoid.")
d("n21_misc", "Will the model know about NEXUS changes after training?", "No. A fine-tuned model holds the facts from its training time. If NEXUS changes, the model's knowledge goes stale until it is retrained or given current information through a tool or its prompt.")
d("n21_misc", "Is the Phase 1 audit the source of truth for NEXUS facts?", "For this project, yes: the NEXUS examples are based on the Phase 1 report and its plan documents, and cross-checked against a fact base. It is a snapshot from the audit date, so later changes to NEXUS wouldn't appear in it.", sys=K2)
d("n21_misc", "In plain English, what is 'slot filling'?", "Turning a sentence into the exact fields an action needs. In 'add a task to call Sam on Friday', the title is 'call Sam' and the date is Friday. NEXUS does this with hand-written parsers and does not use a language model for it.")
d("n21_misc", "Why is a classifier cheaper than an LLM for routing?", "It is a small model with a fixed output set, so it runs quickly on a CPU, gives a confidence number and is easy to evaluate. That fits the original local-first, one-model design. It's also less flexible: it can only pick one of 14 labels.")
d("n21_misc", "What would make me trust Qwen's tool use?", "Evidence from the evaluation gate on held-out, differently written tests, plus runtime enforcement: results only from the executor, schema-validated arguments, confirmation for writes, step limits and audit logs. Neither has happened yet.")
d("n21_misc", "Do the unit tests encode the 'NEXUS routes, it does not answer' stance?", "Yes, according to the architecture plan: assistant-page tests assert that copy, so they would have to change deliberately when a chat model is integrated.")
d("n21_misc2", "If I had to demo the AI in NEXUS in two minutes, what would I show?", "Open the /assistant page in Chrome or Edge and say something like 'add a task to review the budget'. Show the routing decision with its confidence, then the proposal, and confirm it. Be upfront that this is intent routing plus a confirm-gated action, not a conversational model.")
d("n21_misc2", "My manager asked if NEXUS can 'understand' documents. What do I say?", "It can't, in the sense of reading and reasoning about documents. It stores notes and resources and has search across them, and the classifier labels short requests. Document understanding would need the planned generative model plus a tool to fetch the document.")
d("n21_misc2", "Which is more reliable: the classifier's label or the propose-confirm step?", "The confirm step. The label can be wrong (75% on hand-written requests), and it has a threshold that is not a calibrated probability. Confirm is a human check on a concrete change, so it catches a wrong guess before data is touched.")
d("n21_misc2", "Does the classifier get better the more I use NEXUS?", "No. It's a fixed checkpoint loaded by the backend. It doesn't learn from your requests while running. Improving it would mean retraining with better data.")
d("n21_misc2", "Does the backend log what I say to the assistant?", "The integration plan says the planned chat endpoint should not log message text, matching the current ML logging policy. That suggests the existing ML routes don't log request text, but I can't confirm the logging details from the audit alone.")
d("n21_misc2", "What's ML_MAX_INPUT_CHARS?", "A setting that limits the length of text sent to the ML routes. The integration plan mentions it as a model for request limits on the planned chat endpoint.")
d("n21_misc2", "Who owns the data in NEXUS?", "The signed-in user. Records are owner-scoped, and the design principle is local first, so your data stays in your own backend and database. The exception to note is browser speech recognition, which sends audio to the browser vendor.")
d("n21_misc2", "What is the difference between 'risk_query' and the risk module?", "The risk module is the feature: stored risks, a detection engine, recommendations. risk_query is one of the classifier's intent labels, meaning a request is about risks, so it can be routed there.")
d("n21_misc2", "Why would a model-based assistant need a conversation manager when the classifier didn't?", "The classifier maps one sentence to one label, so history is irrelevant. A generative model uses earlier turns, a system prompt and a context window, so something has to store and supply them. The architecture plan marks that layer as missing today.")
d("n21_misc2", "Is the planned chat endpoint going to stream?", "The integration plan describes server-sent events for streaming, with request limits and a status route that answers 503 when the model is unavailable. It's a plan, and no such endpoint exists.")
d("n21_misc2", "What does 'thinking mode' have to do with the plan?", "Qwen3 can run in a thinking or non-thinking mode, and the training plan says to decide once and use the same mode in training and serving. The dataset deliberately contains no thinking blocks, leaving the choice to a later phase.")
d("n21_misc2", "What are 'replay' data in the training plan?", "A small set of open-licensed general instruction data mixed into training so the model keeps its broad abilities. The aim is retention and not new teaching, and licenses have to be checked.")
d("n21_misc2", "Could DeBERTa handle code_assist if we retrained it?", "It could learn to label such requests better, but labelling isn't answering. A classifier can only point at a destination, and for code_assist there isn't one. Answering needs a generative model.")
d("n21_misc2", "Does NEXUS have a mobile app?", "The audit doesn't mention one. It describes a React web frontend, so I wouldn't claim a mobile app.")
d("n21_misc2", "Is there an admin UI for the roles?", "The audit confirms role-based permissions and an audit log, but doesn't describe an admin interface for managing roles, so I can't say. Check the frontend if you need to know.")
d("n21_misc2", "What does the README get right about the AI?", "It correctly describes the DeBERTa intent classifier from Phase 10 and its integration in Phase 11, and the browser-based voice work in Phase 12. The unsupported parts are the Ollama integration and the web intelligence.")
d("n21_misc2", "Can I run NEXUS without internet?", "The backend and database are local, so most features don't need the internet. The caveats are voice input in Chrome and Edge, which uses the vendor's transcription service, and any dependency downloads during setup. The audit didn't test offline use.")
d("n21_misc2", "Will training Qwen change the DeBERTa training data?", "No. The Qwen work is isolated, and the plan says not to touch backend/ml or its artifacts. The DeBERTa datasets, which are gitignored, remain separate.")
d("n21_misc2", "A user asks the future assistant to book a flight. What should the dataset teach?", "That without a tool for booking it should say it can't do it. It could offer to help compare options or draft a message, and it must never say it booked anything. NEXUS has no booking or email tool today, and training doesn't create one.")
d("n21_misc2", "Which hallucination does the plan worry about most?", "Claiming an action or tool result that didn't happen. The plan lists hallucinated execution as an evaluation criterion and says the runtime must enforce that every claim of action matches a recorded successful tool call.")
d("n21_misc2", "I want to add a new REST module. Does it show up in the assistant automatically?", "No. The assistant routes to areas known by the 14-intent taxonomy, and actions only exist for a closed set of kinds. A new module would need new intents, retraining and action definitions to be reachable by sentence. In the planned design, a new tool wrapper would be needed.")
d("n21_misc2", "Does NEXUS have rate limiting?", "Yes, in-memory rate limiting is part of the security layer. Because it's in memory, I wouldn't assume it is shared across multiple backend processes.")
d("n21_misc2", "Is the audit log visible to users?", "The audit confirms a security audit log is written. Whether it's exposed in the interface isn't described, so I can't say.")
d("n21_misc2", "What would count as a 'second generative model' under the rules?", "Any additional language model that produces text, such as an Ollama-served model alongside Qwen. The roadmap's rule is one generative model, Qwen3-8B. DeBERTa is a classifier, and keeping it wouldn't count.")
d("n21_misc2", "Explain the 'out_of_scope' intent.", "It's the classifier's label for requests that don't belong to any NEXUS area, for example small talk or general trivia. Since NEXUS has no model to answer such things, they currently get no useful answer.")
d("n21_misc2", "So what does a user get today if they ask a general knowledge question?", "No answer. The classifier would likely label it out_of_scope or deep_reasoning, and neither leads anywhere, so the user gets a routing result or generation_unavailable, not an answer.")
d("n21_misc2", "Which phase makes NEXUS conversational?", "In the Qwen roadmap, runtime and NEXUS integration (phase 7) adds the chat endpoint and the model, and the JARVIS experience (phase 8) builds on it. Neither has started.")
d("n21_misc2", "How do I phrase 'NEXUS will have an AI assistant' safely?", "Say 'NEXUS plans to add a fine-tuned Qwen3-8B assistant; it has not been built yet.' Include the plan status every time, so the sentence can't be read as describing a current feature.")
d("n21_misc2", "Do tests exist for the classifier?", "The backend has pytest files and the ML package has evaluation code, but the audit didn't run them, and I don't have a list of which tests cover the classifier.")
d("n21_misc2", "Will the Qwen dataset include the Phase 10 DeBERTa data?", "No. The Phase 2 dataset is a new, separate, isolated one for assistant behaviour, and it doesn't reuse the DeBERTa intent datasets.")
recs += D.records()
n_hand = len(recs)

# ---------------- composed 1: status quiz ----------------
TOPIC = {
 "no_llm": "a built-in generative chatbot", "no_ollama": "an Ollama integration", "no_web": "web search or internet access", "no_tools": "file, terminal and browser tools",
 "no_chat_ui": "a chat user interface", "no_server_voice": "server-side speech recognition", "qwen_not_trained": "a trained Qwen3-8B model",
 "qwen_plan": "Qwen3-8B as the generative assistant", "tool_layer_plan": "a registry of tools the model can call", "serving_plan": "a separate local inference process for the model",
 "voice_future": "speaking the model's replies through the assistant page", "kaggle_plan": "QLoRA training on Kaggle", "evaluation_gate": "an evaluation gate before integration",
 "ml_model": "the DeBERTa intent classifier", "propose_confirm": "propose and confirm actions", "slot_extraction": "hand-written slot extraction for actions",
 "voice_stt": "browser-based speech recognition", "voice_tts": "browser text-to-speech", "assistant_page": "the /assistant voice and text page", "search": "global search across your own data",
 "palette": "the Ctrl+K command palette", "command_center": "the Command Center page", "developer_module": "git-based developer analytics", "risks": "rule-based risk detection with evidence",
 "auth": "JWT authentication with roles and an audit log", "generation_unavailable": "the generation_unavailable response", "history_not_sent": "conversation history sent to the backend",
 "kaggle_not_used": "any Kaggle GPU training so far", "dataset_phase": "a Qwen fine-tuning dataset", "deberta_future": "retiring the DeBERTa router",
}
VERB = {"C": ["It exists today.", "That is implemented.", "Built and in the current codebase."], "N": ["It does not exist today.", "That is absent.", "Not present in the current system."],
        "P": ["It is planned, not built.", "That is future work.", "Only on the roadmap so far."]}
QUIZ = ["Quick check: where does {t} stand: implemented, planned, or not present?", "Status of {t} in NEXUS? Built, planned or absent?", "Is {t} something NEXUS has now, something planned, or neither?", "Sort this into 'exists', 'planned' or 'not there': {t}."]
qi = 0
for fid, topic in TOPIC.items():
    st = FACT[fid][1]
    # kaggle_not_used / history_not_sent / qwen_not_trained are phrased as absences
    if fid == "history_not_sent":
        st = "N"
        ans = "Not present. Only the current utterance is sent to the backend; the 20-turn history stays in the browser."
    elif fid == "dataset_phase":
        st = "P"
        ans = "Planned, and in preparation: the dataset is built for a later training run, but nothing has been trained or integrated."
    else:
        ans = rng.choice(VERB[st]) + " " + FACT[fid][3]
    q = QUIZ[qi % 4].format(t=topic)
    qi += 1
    recs.append(mk("nexus_knowledge", "n21_status_quiz", [("user", q), ("assistant", ans)], f"n21q-{fid}", diff="easy", src="composed", system=None if qi % 3 else K1))

# ---------------- composed 2: documentation wording ----------------
DOC = ["Draft one sentence for our docs that answers: {q}", "I need doc wording for this question: {q}", "Write a README-ready line: {q}", "Give me an accurate two-sentence FAQ entry for '{q}'"]
DOC_IDS = ["ml_model", "no_llm", "no_ollama", "no_web", "no_tools", "voice_stt", "voice_privacy", "voice_tts", "voice_scope", "propose_confirm", "slot_extraction", "classifier_accuracy", "confidence_threshold",
           "generation_unavailable", "history_not_sent", "database", "qwen_plan", "training_not_access", "kaggle_plan", "isolation", "evaluation_gate", "tool_layer_plan", "serving_plan", "local_inference", "deberta_future",
           "one_model_rule", "ml_runtime", "no_chat_ui", "assistant_page", "command_center"]
for j, fid in enumerate(DOC_IDS):
    f = FACT[fid]
    q = f[2][j % len(f[2])]
    tag = {"C": "", "N": " (state this as not existing today)", "P": " (mark it as planned)"}[f[1]]
    if f[1] == "C":
        a = "Suggested wording: \"" + f[3] + "\""
    elif f[1] == "N":
        a = "Suggested wording, written as an absence: \"" + f[3] + "\""
    else:
        a = "Suggested wording, marked as a plan: \"" + f[3] + "\""
    recs.append(mk("nexus_knowledge", "n21_doc_wording", [("user", DOC[j % 4].format(q=q) + tag), ("assistant", a)], f"n21doc-{fid}", diff="medium", src="composed"))

# ---------------- composed 3: paired follow-ups (two user turns) ----------------
PAIRS = [("ml_model", "intent_list"), ("ml_model", "classifier_accuracy"), ("classifier_accuracy", "confidence_threshold"), ("voice_stt", "voice_privacy"), ("voice_tts", "voice_scope"),
         ("propose_confirm", "slot_extraction"), ("no_llm", "qwen_plan"), ("qwen_plan", "brain_body"), ("brain_body", "training_not_access"), ("kaggle_plan", "local_inference"),
         ("roadmap", "evaluation_gate"), ("tool_layer_plan", "training_not_access"), ("no_tools", "tool_layer_plan"), ("no_ollama", "no_llm"), ("no_web", "no_tools"), ("generation_unavailable", "intent_list"),
         ("assistant_page", "history_not_sent"), ("what_is", "modules"), ("frontend", "backend"), ("backend", "database"), ("auth", "api_versioning"), ("risks", "command_center"), ("developer_module", "learning_career"),
         ("search", "palette"), ("serving_plan", "deberta_future"), ("voice_future", "no_chat_ui"), ("isolation", "kaggle_plan"), ("one_model_rule", "qwen_plan"), ("ml_runtime", "ml_endpoints"), ("dataset_phase", "training_not_access")]
BRIDGE = ["And ", "Next question: ", "Related thing: ", "Also, ", "Following on: ", "Okay. Then "]
for j, (a, b) in enumerate(PAIRS):
    fa, fb = FACT[a], FACT[b]
    qa = fa[2][(j + 1) % len(fa[2])]
    qb = fb[2][(j + 2) % len(fb[2])]
    qb = BRIDGE[j % len(BRIDGE)] + qb[0].lower() + qb[1:]
    first = fa[3] if j % 2 == 0 else fa[4]
    second = fb[4] if j % 2 == 0 else fb[3]
    recs.append(mk("nexus_knowledge", "n21_paired_followup", [("user", qa), ("assistant", first), ("user", qb), ("assistant", second)], f"n21p-{a}-{b}", diff="medium", src="composed",
                   system=[None, K2, None, K1][j % 4]))

if __name__ == "__main__":
    save("nexus21", recs)
    print("hand-written:", n_hand, " composed:", len(recs) - n_hand, " multi-turn:", sum(1 for r in recs if sum(m['role'] == 'user' for m in r['messages']) > 1))
