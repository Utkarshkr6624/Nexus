"""Category 8 top-up: additional NEXUS facts (all from the Phase 1 report/docs) + classifier-output scenarios."""
from lib import *
from nexus_facts import STATUS_PREFIX

CAT = "nexus_knowledge"
R = seeded("nexus2")
out = []

# (id, status, topic, [3 questions], short, long)
F = [
("rate_limit", "C", "rate limiting", ["How does NEXUS rate limit requests?", "Does NEXUS protect itself from request floods?", "What are the rate limits in the NEXUS backend?"],
 "NEXUS has in-memory rate limiting: by default 600 requests per route per address per 60 seconds, and a stricter 120 for login and password-reset requests.",
 "The backend rate limiter is in-memory and keyed by route and client address. The defaults are 600 requests per window of 60 seconds, and 120 for the credential routes (/auth/login and /auth/password/forgot). It trusts the X-Forwarded-For header only if RATE_LIMIT_TRUST_FORWARDED_FOR is enabled, which should be done only behind a trusted reverse proxy, and a maximum-entries setting stops the store itself from growing without bound."),
("audit_retention", "C", "the audit log retention setting", ["Does NEXUS delete old audit logs automatically?", "What does AUDIT_LOG_RETENTION_DAYS do?", "How long are audit logs kept in NEXUS?"],
 "The setting exists (default 400 days) but is declared, not enforced: no pruning job exists, so audit logs are not deleted automatically.",
 "AUDIT_LOG_RETENTION_DAYS defaults to 400, but the README itself notes it is declared and not enforced. No job prunes the table, so old audit entries stay until someone deletes them. If retention matters to you, that is a gap to close rather than something NEXUS already handles."),
("password_policy", "C", "the password policy", ["What password rules does NEXUS enforce?", "How strong do NEXUS passwords have to be?", "Is there a minimum password length in NEXUS?"],
 "Passwords need at least 8 characters, with an uppercase letter, a lowercase letter, a digit and a special character.",
 "The default policy requires at least 8 characters (PASSWORD_MIN_LENGTH) plus uppercase, lowercase, digit and special characters. Password reset tokens expire after 30 minutes by default, and the DEV_EXPOSE_RESET_TOKEN option should stay off because enabling it makes the forgot-password endpoint reveal the raw token."),
("sessions", "C", "session limits", ["How many sessions can a NEXUS user have?", "How long do NEXUS sessions last?", "What are the session limits?"],
 "By default a user can have up to 20 active sessions, each with an absolute lifetime of 30 days.",
 "The settings MAX_ACTIVE_SESSIONS (default 20) and SESSION_ABSOLUTE_LIFETIME_DAYS (default 30) bound sessions. Access and refresh tokens are JWTs, and sessions can be listed and revoked as part of session management."),
("env_file", "C", "where NEXUS configuration lives", ["Where does NEXUS read its configuration from?", "Where should I put my NEXUS .env file?", "How do the backend and frontend find the .env?"],
 "A single .env file at the repository root, copied from .env.example, is read by both the backend and the Vite frontend.",
 "You copy .env.example to .env at the repository root. The backend's Settings class also checks ../.env and ../../.env, backend/run.py resolves the root file from its own location, and frontend/vite.config.ts points envDir at the repo root, so both processes find the same file regardless of the working directory. Real secrets must not be committed."),
("cors", "C", "CORS configuration", ["How is CORS configured in NEXUS?", "Which origins can call the NEXUS API?"],
 "Allowed origins come from the CORS_ORIGINS setting, a comma-separated list without trailing slashes.",
 "CORS_ORIGINS holds the allowed browser origins. In development the frontend avoids CORS entirely by using a relative /api/v1 URL that the Vite dev server proxies to the backend, which keeps requests same-origin."),
("planner_cfg", "C", "planner settings", ["What planner settings can be configured in NEXUS?", "What working hours does the NEXUS planner assume?"],
 "Planner defaults include a UTC timezone, a working day of 8 to 20, and session lengths between 15 and 240 minutes.",
 "The planner reads PLANNER_DEFAULT_TIMEZONE (UTC), PLANNER_DAY_START_HOUR (8) and PLANNER_DAY_END_HOUR (20) as the fallback working window for users with no availability rules, session length bounds of 15 and 240 minutes, up to 3 suggestions per task and a 30-day lookahead. Stored instants stay in UTC."),
("analytics_weights", "C", "analytics scoring weights", ["How is the NEXUS productivity score weighted?", "Can I change the analytics weights?"],
 "The productivity score weights completion 30, deadlines 25, consistency 20 and focus 25, and the four must sum to 100 or the app refuses to start.",
 "The four ANALYTICS_PRODUCTIVITY_WEIGHT_* settings default to 30, 25, 20 and 25. They are validated at startup, so changing them is allowed as long as they still total 100. A fifth setting, ANALYTICS_COMPARISON_WINDOWS, is declared but read by no code."),
("reserved_vars", "C", "reserved, unused settings", ["Are there NEXUS settings that do nothing?", "Which env variables are read by no code?"],
 "Yes: ANALYTICS_COMPARISON_WINDOWS on the backend, and VITE_API_SERVER_URL, VITE_APP_NAME and VITE_ENABLE_COMMAND_PALETTE on the frontend are declared but read by no code.",
 "The README is explicit that these are placeholders kept so names stay stable. Changing them has no effect today, so don't expect VITE_ENABLE_COMMAND_PALETTE to switch the palette off."),
("ml_env", "C", "the ML settings", ["Which environment variables control the NEXUS classifier?", "How do I turn the NEXUS ML off?"],
 "There are seven ML settings: ML_ENABLED, ML_MODEL_PATH, ML_DEVICE, ML_CONFIDENCE_THRESHOLD, ML_MAX_INPUT_CHARS, ML_REJECT_CREDENTIALS and ML_FAIL_FAST.",
 "ML_ENABLED (true) switches the feature on or off, ML_MODEL_PATH (empty, which resolves to backend/ml/artifacts/small-model/final) points at the checkpoint, ML_DEVICE (auto) picks CUDA if a working build exists, ML_CONFIDENCE_THRESHOLD is 0.90, ML_MAX_INPUT_CHARS is 2000, ML_REJECT_CREDENTIALS (true) refuses credential-shaped input, and ML_FAIL_FAST (false) controls whether a missing checkpoint stops startup. With ML off, the endpoints answer 503 ml_unavailable."),
("credential_screen", "C", "credential screening", ["Does NEXUS stop me from sending a password to the classifier?", "What does ML_REJECT_CREDENTIALS do?"],
 "Yes: with ML_REJECT_CREDENTIALS on (the default), the backend rejects input that looks like a credential before classifying it, and the submitted text is never logged.",
 "The backend applies the same validation, length bound and credential screening to a spoken or typed utterance. It logs the intent, confidence, destination and latency, but not the text itself."),
("training_pipeline", "C", "the Phase 10 training pipeline", ["How was the DeBERTa classifier trained?", "What was the Phase 10 pipeline?"],
 "The pipeline ran dataset, validation, training, evaluation and then a model artifact.",
 "Phase 10 followed dataset → validation → training → evaluation → model artifact for the 14-class intent classifier. The training code lives in backend/ml (datasets, preprocessing, training, evaluation and scripts), while the datasets and artifacts are gitignored so they aren't committed."),
("ml_artifacts", "C", "model artifacts", ["Where is the trained classifier stored?", "Is the model checkpoint in git?"],
 "The checkpoint sits under backend/ml/artifacts/ and is gitignored, as are the dataset JSONL files.",
 "backend/ml/artifacts/small-model contains the final model, intermediate checkpoints, metrics and predictions. The .gitignore excludes backend/ml/artifacts/ and backend/ml/datasets/*.jsonl, so a fresh clone won't have the weights unless you provide them."),
("docs_set", "C", "the project documentation", ["What documentation does NEXUS have?", "Where are the NEXUS design docs?"],
 "The docs folder holds architecture, development and API-conventions guides, the Phase 12 voice document and per-phase specifications and reports.",
 "Under docs/ there are architecture.md, development.md, api-conventions.md and phase-12-voice.md, a specifications folder with phase 3 to 11 contracts and reports, and QA notes. The voice document, in particular, states the limitations of speech recognition and the 'routes, does not answer' behaviour."),
("test_suites", "C", "NEXUS tests", ["What kinds of tests does NEXUS have?", "How is the NEXUS backend tested?"],
 "The backend uses pytest with roughly a hundred test files, and the frontend uses Vitest and React Testing Library with roughly fifty.",
 "Tests cover services, API routes, the ML integration and offline behaviour on the backend, and components, hooks and the speech wrappers on the frontend. The counts come from reading the repository; the suites were not run in the Phase 1 audit."),
("api_client", "C", "the frontend API client", ["How does the NEXUS frontend talk to the API?", "Does the frontend set request timeouts?"],
 "A typed API client sends bearer-token requests to a relative /api/v1 base URL, and the routing call sets an explicit 30-second timeout.",
 "services/ml.ts uses ROUTING_TIMEOUT_MS (30,000) rather than the client default and never retries, because a 503 won't clear in a retry window. Responses use a consistent error envelope that the UI maps to user-facing messages without leaking raw backend text."),
("voice_timeout", "C", "the speech recognition timeout", ["How long does NEXUS listen for speech?", "What happens if I say nothing?"],
 "Recognition stops after 15 seconds and reports a timeout, and an utterance that trims to nothing is reported as 'no speech'.",
 "RECOGNITION_TIMEOUT_MS (15,000) aborts a session left open, which releases the microphone. The recogniser is configured for a single utterance, with continuous mode off and one alternative."),
("voice_states", "C", "the voice assistant states", ["What states does the NEXUS voice assistant have?", "How does the assistant UI track what's happening?"],
 "Six states: unsupported, idle, listening, processing, speaking and error.",
 "A small state machine owns when the microphone is open and when a turn is sent. 'Unsupported' is derived during render rather than stored, and 'error' is a resting state from which a new utterance or a retry is allowed."),
("spoken_lines", "C", "what NEXUS says aloud", ["What does NEXUS actually say out loud?", "Does NEXUS read answers aloud?"],
 "It reads short routing statements such as 'Routing to Task.' or a note that a request is out of scope, never a composed answer.",
 "Every spoken line is about routing: accepted requests name the service, generation_unavailable says NEXUS has no generative model, out_of_scope says the request is outside what it can route, and uncertain asks you to name the surface you want."),
("status_codes", "C", "ML error handling in the UI", ["What happens in the UI when the classifier is unavailable?", "How does NEXUS show 401 and 503 errors?"],
 "A 503 ml_unavailable names the missing classifier and says everything else still works; 401 and 403 show distinct messages about authentication and permission.",
 "The assistant maps 422 to 'rephrase as one short instruction', transport failures and client timeouts to a retryable timeout, and unexpected 500s to a generic message; a test asserts no raw backend message leaks into the copy."),
("action_kinds", "C", "the kinds of actions NEXUS can propose", ["What actions can NEXUS propose from a sentence?", "Which things can I change through propose and confirm?"],
 "A closed set covering tasks, projects, knowledge, learning, the planner, developer repositories and your own account.",
 "ActionKind is a closed enum (create, update, delete, complete, schedule and similar for each area), each tied to a service entry point and flagged as destructive or not. Anything outside the set is refused with a stated reason, not guessed."),
("bootstrap", "C", "installing NEXUS dependencies", ["How do I install NEXUS dependencies?", "Why does pip fail on the torch version?"],
 "Run make install (scripts/bootstrap.py), or pip install -r requirements.txt with the PyTorch CPU extra index, because torch is pinned to 2.14.1+cpu.",
 "The pinned CPU wheel is published only on the PyTorch CPU index, so a plain install fails with 'No matching distribution found'. The Dockerfile and bootstrap script both pass the extra index URL. The requirements file also splits runtime and development sections at a marker line."),
("python_node", "C", "NEXUS runtime versions", ["Which Python and Node versions does NEXUS use?", "What does the dev machine run?"],
 "The backend targets CPython 3.13 and the frontend development environment uses Node 24.",
 "The pinned requirements are built for Python 3.13, and the notes for this project give Node 24 with npm 11 on Windows. The machine has about 15.6 GB of RAM and an RTX 3050 laptop GPU with roughly 4 GB."),
("docker_files", "C", "Docker files in the repo", ["Does NEXUS ship Docker files?", "Is Docker required to run NEXUS?"],
 "The repository includes Dockerfiles and a compose file, but the project owner's notes say they don't use Docker locally.",
 "docker-compose.yml, backend/Dockerfile and frontend/Dockerfile exist for container-based setups. Local development is described as running the backend and the Vite dev server directly, so Docker isn't required."),
("untracked_scripts", "C", "the older dataset scripts", ["What are the old dataset scripts in the repo root?", "Why is there a generate_dataset.py in NEXUS?"],
 "They are untracked files from an earlier dataset attempt that wrote to a qwen3-agent folder which doesn't currently exist; they are not trusted or modified in this project.",
 "The Phase 1 audit found about twenty untracked scripts such as generate_dataset.py and scripts/write_*.py. They targeted E:/Nexo/qwen3-agent/phase2/dataset/v2_final/, which isn't present. The Qwen work uses a separate, isolated workspace instead."),
("no_streaming", "N", "streaming responses", ["Does NEXUS stream model output?", "Is there a streaming endpoint for chat?"],
 "No. There's no chat endpoint, so nothing streams; a streamed (SSE) chat endpoint is planned for the Qwen integration.",
 "Today's endpoints return a single JSON response. The integration plan describes a chat endpoint with server-sent events, request limits like ML_MAX_INPUT_CHARS and no logging of message text, but none of that exists yet."),
("no_conversation_store", "N", "stored conversations", ["Does NEXUS store my chats on the server?", "Is there a conversations table?"],
 "No. The assistant keeps at most 20 turns in the browser, never persists them, and there is no server-side conversation table.",
 "A conversation store is a decision for the integration phase: server-side history would need new Alembic migrations and a privacy review, because the current design deliberately never persists transcripts."),
("command_center_exec", "N", "the Command Center executing actions", ["Does the Command Center run commands?", "Can the Command Center act on my behalf?"],
 "In the code it is mostly read-oriented; the only path that executes a sentence is the propose and confirm flow.",
 "The Command Center shows a priority queue, measured panels, recommendations, quick actions and an ML insight panel. The README's description of wider AI-assisted workflows goes further than what the audit found."),
("no_export", "N", "exporting data", ["Can NEXUS export my data?", "Is there a data export feature?"],
 "The audit did not find an export feature, so I wouldn't promise one.",
 "Nothing in the reviewed code was an obvious export route. If you need your data out, check the app itself or the database directly rather than assuming a feature exists."),
("dataset_v1", "P", "the Qwen training dataset", ["What's in the Qwen dataset for NEXUS?", "How is the Qwen training data organised?"],
 "A chat-format JSONL dataset of over ten thousand examples across twelve categories, split about 90/5/5 into train, validation and test.",
 "The categories are general conversation, general knowledge, reasoning, programming, mathematics, instruction following, JARVIS-style assistance, NEXUS knowledge, tool-aware reasoning, honest capability boundaries, multi-turn context and safety. Examples carry metadata, and more than a thousand are multi-turn."),
("system_context", "P", "why the dataset uses system messages", ["Why do the training examples include system messages about tools?", "How does the model learn what it can actually do?"],
 "Because what the assistant can do depends on the runtime, each example's system message states which tools (if any) are attached, and the reply follows from that.",
 "With no tools attached, the right behaviour is to say it can't act and offer text help. With a tool attached, it may propose a call but must not report results it hasn't received. Training on both teaches the model to read its runtime context instead of assuming capabilities."),
("lora_plan", "P", "the planned fine-tuning method", ["How will Qwen3-8B be fine-tuned?", "What is QLoRA and why use it here?"],
 "With QLoRA: the base model is loaded in 4-bit and small low-rank adapters are trained, which fits a free 16 GB Kaggle GPU.",
 "The plan is 4-bit NF4 loading, LoRA adapters on the linear layers (rank around 16 to 64), a learning rate near 1e-4 to 2e-4, one to three epochs, a maximum sequence length near 4096, and loss computed only on assistant turns. Exact values get settled in the dry-run phase."),
("kaggle_limits", "P", "Kaggle GPU constraints", ["What are the Kaggle limits for training?", "Can Kaggle's free GPU train an 8B model?"],
 "Yes with QLoRA, but with limits: a T4 has about 16 GB and no bf16, sessions are time-limited and weekly GPU hours are capped, so checkpoints must be saved and resumable.",
 "The plan saves checkpoints to /kaggle/working, supports resuming across sessions, uploads data as a Kaggle Dataset and exports the adapter at the end. Exact quotas haven't been verified yet and are a Phase 4 check."),
("eval_plan", "P", "the evaluation of the Qwen model", ["How will the Qwen model be evaluated?", "What tests will the tuned model have to pass?"],
 "Base and tuned models are compared on reasoning, NEXUS knowledge, tool selection, argument validity, multi-tool plans, clarification, failure recovery, hallucinated execution and retention of general ability.",
 "Training loss isn't accepted as evidence. The hallucinated-execution rate has to be near zero, and the model must say a tool is unavailable when it isn't in the list. If it fails the gate, the plan is to improve the data and not integrate."),
("permission_policy", "P", "permissions for tools", ["How will tool permissions work?", "Will the assistant need approval to act?"],
 "Each tool gets a policy of automatic, confirm or deny; anything that changes or deletes data will need your confirmation.",
 "The runtime, not the model, enforces schema-validated arguments, step and time limits, audit logging and owner scoping, injecting the user id instead of trusting model output."),
("file_tools_plan", "P", "future file tools", ["How would file access be limited?", "Will the assistant be able to read any file?"],
 "No. File tools would be restricted to allowlisted roots, modelled on the developer module's path allowlist.",
 "Reads and writes outside the allowed folders would be refused, destructive operations would need confirmation, and each tool would be enabled separately rather than all at once."),
("web_tools_plan", "P", "future web tools", ["How would web access work for the assistant?", "Will the assistant browse freely?"],
 "Through web search and fetch tools with size and domain limits, enabled as a separate gate, not unrestricted browsing.",
 "The assistant would only claim to have searched when a real search tool returned results, and it would state sources and uncertainty rather than inventing them."),
("local_dry_run", "P", "the local dry run", ["What happens on the local machine before Kaggle?", "How will the training script be tested first?"],
 "Dataset building, validation, tokenizer and template checks, and a tiny dry run on a small model such as Qwen3-0.6B, before any big GPU run.",
 "The local machine has about 4 GB of GPU memory, so it handles preparation only. The goal is to catch format and configuration errors for free before spending any Kaggle quota."),
("thinking_mode", "P", "Qwen's thinking mode", ["What is the plan for Qwen3's thinking mode?", "Will the model show its reasoning?"],
 "The mode must be chosen once and used identically in training and serving, since mismatches degrade behaviour.",
 "The Qwen3 chat template supports a thinking and a non-thinking style. The plan flags this as a risk to settle in Phase 4, along with the exact tool-call format, which Phase 3 defines."),
("phase3", "P", "Phase 3 of the Qwen roadmap", ["What comes after the dataset phase?", "What is the tool manifest phase?"],
 "Phase 3 defines the tool manifest and simulated tool traces: schemas, arguments, success and failure results and multi-tool examples, none of which execute for real.",
 "Training tools are simulated representations. Phase 3 also fixes the wire format for tool calls, which this dataset deliberately does not define."),
("phase4_gate", "P", "the training-preparation gate", ["What must pass before real training starts?", "What is the Phase 4 gate?"],
 "A successful dry run: model loading, tokenizer and template checks, memory estimates, dataset loading and a short training smoke test.",
 "No expensive GPU session starts until the pipeline completes a dry run. Hardware detection and quantisation compatibility are part of that check."),
("deberta_decision", "P", "the future of the classifier", ["Will the classifier stay when Qwen arrives?", "Is the DeBERTa model going to be removed?"],
 "That's undecided. It's kept for now, and the decision follows the Qwen evaluation.",
 "The classifier could remain as a cheap router or guard, or be retired. The roadmap explicitly says not to replace it automatically."),
("docs_update_needed", "P", "documentation that will need changing", ["What NEXUS docs will change when Qwen is integrated?", "What assumptions in the code say there's no LLM?"],
 "The requirements header, the Phase 12 voice document, offline/local-first tests and the README's Ollama claim all encode 'NEXUS runs no language model'.",
 "Those need deliberate updates in the same change as the integration, not silent edits. The assistant page's copy, 'routes, it does not answer', is also asserted by tests."),
]
# classifier-output scenarios: (what the user sees, what it means)
SCEN = [
 ("The assistant page says 'Routing to Task.' Did it create my task?", "No. That line only reports that the classifier assigned your request to the task area. A task is created only if you go through propose and confirm and approve the proposal."),
 ("NEXUS answered 'generation_unavailable' when I asked it to explain my code. Is it broken?", "No, that's a stated capability gap. The classifier recognised a code_assist request correctly, but NEXUS has no language model to answer it. It's meant to be filled later by a fine-tuned Qwen model."),
 ("I got 'uncertain' and two runner-up intents. What should I do?", "The classifier's confidence was below 0.90, so NEXUS didn't route the request. Rephrase it more specifically, name the area you want (for example 'tasks'), or pick one of the suggested runners-up."),
 ("The assistant said my request was out of scope. What does that mean?", "It means the classifier thinks the request doesn't fit any NEXUS capability. Note that it's not always right: the model sometimes sends conversational text to risk_query instead, so the label isn't a verdict on your request's quality."),
 ("I see a 503 ml_unavailable error. Is my account broken?", "No. It means the classifier checkpoint isn't loaded: ML may be disabled, or the model files are missing. Everything else in NEXUS should still work."),
 ("Why does the voice assistant not work in Firefox?", "Firefox doesn't implement the SpeechRecognition API, so there's no voice input there. The typed field takes exactly the same route to the classifier."),
 ("Why did my spoken request get transcribed wrongly?", "Speech recognition is done by the browser's service, not by NEXUS, so its accuracy depends on the browser, microphone and accent. Transcripts are generally more error-prone than typed text, and the classifier sees whatever text arrives."),
 ("The classifier sent my chatty message to 'risk' stuff. Why?", "That is a known weakness: on natural-language tests, several misses collapsed into risk_query, which acts as a sink for conversational or reflective text. The 0.90 threshold didn't always catch it."),
 ("Can I ask the assistant what NEXUS can do and get a written answer?", "Not from the classifier. It would only route the request. A written answer needs a generative model, which is part of the planned Qwen work."),
 ("I pasted a password and the assistant rejected my message. Why?", "That's the credential screen. With ML_REJECT_CREDENTIALS on, input that looks like a credential is refused before classification, and the text isn't logged."),
 ("My follow-up 'and the other one?' got classified as something random.", "The classifier sees one utterance at a time and has no dialogue memory, so pronouns like 'other one' can't be resolved. A conversational model with history is what would handle that."),
 ("Is the confidence number a percentage chance of being right?", "No. The 0.90 value is an integration threshold chosen from a synthetic held-out set. It isn't a calibrated probability, and some wrong predictions have scored above it."),
 ("The assistant says it can't act. Then why does it propose actions?", "Proposals come from a separate flow: hand-written extraction builds a proposal from a closed list of action kinds, and nothing runs until you confirm. The classifier and the proposal builder don't execute anything by themselves."),
 ("Can the classifier delete my project if I say so?", "It can propose a delete if the sentence matches that action kind, and the proposal is flagged destructive. Nothing is deleted until you confirm the proposal."),
]
RENDER_Q = ["Tell me about {t} in NEXUS.", "What should I know about {t}?"]
for fid, st, topic, qs, short, long_ in F:
    pre = R.choice(STATUS_PREFIX[st])
    sysm = lambda: R.choice(SYS_NEXUS_KNOW)
    out.append(mk(CAT, "fact_short", [("user", qs[0]), ("assistant", short)], "n2_" + fid, "easy", "authored", sysm()))
    out.append(mk(CAT, "fact_detail", [("user", qs[1] if len(qs) > 1 else qs[0]), ("assistant", long_)], "n2_" + fid, "medium", "authored", sysm()))
    body = short[0].lower() + short[1:] if pre.endswith(": ") or pre.endswith(", ") else short
    out.append(mk(CAT, "fact_status", [("user", qs[2] if len(qs) > 2 else R.choice(RENDER_Q).format(t=topic)), ("assistant", pre + body)], "n2_" + fid, "medium", "composed", sysm()))
    sents = [s.strip() for s in long_.replace("; ", ". ").split(". ") if s.strip()]
    if len(sents) >= 2:
        bullets = "\n".join("- " + s.rstrip(".") + "." for s in sents[:5])
        out.append(mk(CAT, "fact_bullets", [("user", R.choice(RENDER_Q).format(t=topic) + " Bullet points, please."), ("assistant", bullets)], "n2_" + fid, "medium", "composed", sysm()))
for i, (q, a) in enumerate(SCEN):
    out.append(mk(CAT, "ui_scenarios", [("user", q), ("assistant", a)], f"n2s_{i}", "medium", "authored", R.choice(SYS_NEXUS_KNOW)))
    out.append(mk(CAT, "ui_scenarios", [("user", "Quick question: " + q[0].lower() + q[1:]), ("assistant", a)], f"n2s_{i}", "medium", "composed", R.choice(SYS_NEXUS_NONE)))

# multi-turn follow-ups between related facts
PAIRS = [("rate_limit", "sessions"), ("password_policy", "audit_retention"), ("env_file", "ml_env"), ("ml_env", "credential_screen"), ("voice_states", "spoken_lines"), ("voice_timeout", "voice_states"),
         ("action_kinds", "permission_policy"), ("dataset_v1", "system_context"), ("lora_plan", "kaggle_limits"), ("kaggle_limits", "local_dry_run"), ("phase3", "phase4_gate"), ("eval_plan", "deberta_decision"),
         ("file_tools_plan", "permission_policy"), ("web_tools_plan", "eval_plan"), ("status_codes", "ml_env"), ("training_pipeline", "ml_artifacts"), ("docs_set", "docs_update_needed"), ("no_streaming", "no_conversation_store"),
         ("python_node", "bootstrap"), ("test_suites", "api_client")]
byid = {f[0]: f for f in F}
FOLLOW = ["Okay. And {q}", "Thanks. Next: {q}", "Got it. {q}", "Alright, {q}"]
for a_id, b_id in PAIRS:
    A, B = byid[a_id], byid[b_id]
    for v in range(2):
        qb = B[3][(v + 1) % len(B[3])]
        out.append(mk(CAT, "multi_turn_facts", [("user", A[3][v % len(A[3])]), ("assistant", A[4] if v == 0 else A[5]), ("user", R.choice(FOLLOW).format(q=qb[0].lower() + qb[1:])), ("assistant", B[5] if v == 0 else B[4])],
                      f"n2m_{a_id}_{b_id}", "medium", "composed", R.choice(SYS_NEXUS_KNOW), {"turns": 2}))

save("nexus2", out)
