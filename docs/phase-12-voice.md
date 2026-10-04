# Phase 12 — Voice

**The voice layer is an interface around NEXO's existing AI system. It is not a new AI
system.**

NEXO runs exactly one model — `microsoft/deberta-v3-base`, a 14-class *intent classifier*
trained in Phase 10 and served from Phase 11. It maps one utterance to one intent name and
a confidence. It is not a language model: it cannot answer a question, hold a conversation
or write prose, and Phase 12 did not give it any of those.

What Phase 12 added is a way to *say* a request instead of typing it, and a surface at
`/assistant` that shows exactly what came back:

- **Speech to text** through the browser's own Web Speech API (`SpeechRecognition`, or
  `webkitSpeechRecognition` on Safari).
- **Text to speech** through `window.speechSynthesis`, which is the operating system's
  voice and stays on the machine.
- **A voice assistant UI** at `/assistant`, which previously rendered a placeholder.
- **A lifecycle state machine** — `unsupported · idle · listening · processing · speaking ·
  error` — that owns when the microphone is open and when a turn is sent.

**There is no second model, no LLM, no Whisper, no cloud AI API and no paid service.** The
Web Speech APIs are facilities the browser already ships, not models NEXO trains, serves or
pays for — which is exactly why they were the right thing to build on, and also why they
carry a limitation NEXO cannot design away. That limitation is the first section, and it is
first on purpose.

---

## ⚠️ 1. Speech recognition leaves your device

**Read this before using voice in NEXO, and before writing anything that says otherwise.**

`SpeechRecognition` does not transcribe on the user's machine. In Chrome and Edge it streams
the microphone audio to the browser vendor's own servers — Google for Chrome, Microsoft for
Edge — and transcribes there. **Using this API means the user's voice leaves their device.**
And **Firefox implements `SpeechRecognition` not at all**, so on Firefox there is no
voice input at any quality level.

This is a real, unresolved limitation. It is not a footnote, it is not a configuration
option, and no code in this repository changes it.

| | |
| --- | --- |
| **With Chrome or Edge** | Your microphone audio is sent to the browser vendor for transcription. It works; the audio leaves your machine. |
| **With Firefox** | Speech-to-text is not available at all. The surface renders a permanent explanation and the typed field becomes the only way in. |
| **With Safari** | `webkitSpeechRecognition` is the only spelling that ships; the wrapper reads both. |
| **Text-to-speech** | Local. `window.speechSynthesis` uses the OS voice, so nothing is uploaded when NEXO reads a result aloud. |

### 1.1 Why NEXO accepts it

The only fully-local alternative to vendor recognition is a **local speech-recognition
model** — Whisper or something equivalent, running in the browser via WASM or shipped as
weights. Phase 12's hard constraint is *one model only*: the Phase 10 classifier.

Adding Whisper would breach that constraint outright. It would be a second set of weights to
train, serve, version and trust, a second memory and latency budget, and a second claim on
the project's central promise — that NEXO's headline behaviour is explainable, local and
derived from one measurable artefact. The project's standing rules already refuse a second
model once; this is the same refusal applied at the microphone.

The browser engine is the alternative that does not require a model at all. Its cost is
this section, which is why the cost is stated in the product rather than hidden in a source
file.

### 1.2 What a user can do

- **Use Chrome or Edge and accept the trade-off.** Nothing else about the feature changes:
  the transcript is still sent to NEXO's own local backend, and the history stays in the
  browser.
- **Use the typed path.** The field is always present, always works, and takes exactly the
  same route to the classifier. In Firefox it is the *only* way in, and the panel labels it
  as such. Voice is never the only route to a capability.

### 1.3 The disclosure is in the product

`speech-recognition.ts` exports the canonical text and the UI renders that string verbatim —
it is imported, not retyped, so the panel cannot drift from the code that makes the promise.
This is what a user sees on `/assistant`, below the microphone button:

> Your browser transcribes your voice on its own servers, so the audio leaves this device.
> Chrome and Edge send it to Google and Microsoft respectively; Firefox has no speech
> recognition at all. NEXO uses the browser engine rather than a local model because NEXO
> runs no speech model of its own.

The test suite asserts that this string still contains the phrase *"leaves this device"*, and
that it does **not** claim recognition is local. A reword that softened it would turn a
limitation into a reassurance.

This section is referenced again from §11 (limitations) and §9 (configuration), because those
are the places a reader is most likely to come looking for a way to turn it off. There isn't
one.

---

## 2. Implementation summary

| Piece | What it does |
| --- | --- |
| `features/assistant/types.ts` | `VoiceState`, `VoiceErrorCode`, `VoiceError`, `VoiceTurn`, `VoiceAction`, `VoiceContext`, `MAX_CONVERSATION_TURNS` (20), `RECOGNITION_TIMEOUT_MS` (15 000) |
| `features/assistant/speech-recognition.ts` | A callback-shaped handle over `SpeechRecognition`. Feature detection, the browser-error-code → `VoiceError` table, the timeout guard, and `RECOGNITION_PRIVACY_NOTICE` |
| `features/assistant/speech-synthesis.ts` | A handle over `window.speechSynthesis`. Feature detection, `speak`, `cancel`, overlap prevention, the twelve browser codes translated |
| `features/assistant/use-voice-assistant.ts` | The lifecycle state machine, the one-request mutation, `describeRoutingFailure`, and the spoken confirmation copy |
| `features/assistant/assistant-store.ts` | The bounded, non-persisted conversation store — what the user *said*, and nothing else |
| `features/assistant/components/record-button.tsx` | The one start/stop control, one label and one glyph per state |
| `features/assistant/components/voice-state-pill.tsx` | `VoiceStatePill` and `RoutingStatusBadge` — icon *and* word for every state and outcome |
| `features/assistant/components/conversation-log.tsx` | `RoutingOutcome` and `ConversationLog` — one decision rendered as a decision |
| `features/assistant/components/voice-assistant.tsx` | The panel: control, disclosure, typed field, suggestion, latest decision, conversation |
| `pages/assistant-page.tsx` | The route — header copy, the `Voice · Phase 12` badge, the model badge |
| `types/speech-recognition.d.ts` | Ambient declarations. TypeScript 5.7's `lib.dom.d.ts` ships the *result* interfaces but no `SpeechRecognition` constructor, no `SpeechRecognitionEvent` and no error-code union — this file supplies them, on both the spec name and `webkitSpeechRecognition` |

No backend file changed. The voice layer consumes the Phase 11 endpoints exactly as a typed
request would; there is no new route, no new permission and no new table.

---

## 3. Architecture

```
   microphone ──► SpeechRecognition (browser, vendor-transcribed)
                        │  onInterim ─────────────────────► live partial text (display only)
                        │  onFinal   ──── ONE utterance ──┐
                        │  onError   ──── VoiceError ─────┤
                        │  onEnd     ──────────────────────┤
                        ▼                                ▼
              use-voice-assistant  ── the lifecycle ──►  assistant-store (Zustand)
                        │            unsupported/idle/listening/processing/speaking/error
                        │                                │
                        │  POST /api/v1/ml/route         │  turns[] — bounded at 20,
                        │  { text }  exactly,            │  never persisted, never
                        │  bearer + analytics.read       │  sent anywhere but the panel
                        ▼                                ▼
              FastAPI → app/ml/classifier → app/ml/router
                        │
                        ▼
              RoutingDecisionRead ──► store, UI copy, spoken confirmation
                        │
                        └──► window.speechSynthesis (local, OS voice)
```

### 3.1 Three properties the diagram encodes

**Recognition and synthesis are not symmetrical.** Recognition is a network round trip to
the browser vendor; synthesis is local. They are wrapped separately, documented
separately, and only one of them carries a privacy cost.

**There is no React in either browser wrapper.** A microphone session outlives any render —
the browser owns it, not the component tree — so both files expose an imperative handle that
reports through callbacks. The hook owns the state; the wrappers own nothing. That is also
why the same handle can be tested without a component.

**The only text that crosses the network is the utterance being classified right now.**
There is no code path that could attach the history; see §11.1.

---

## 4. The lifecycle

`VoiceState` has six members and they are the order the states actually occur:

| State | Meaning | How it is entered |
| --- | --- | --- |
| `unsupported` | This browser has no `SpeechRecognition`. Terminal — it can never be left | **Derived during render**, never entered |
| `idle` | Ready. The only state in which a new utterance may begin | Mount, or the end of any turn |
| `listening` | The microphone is open | `startListening()` |
| `processing` | One request is in flight to `/ml/route` | A final transcript, or a typed submission |
| `speaking` | The outcome is being read aloud | `speak()` returned `true` |
| `error` | The last turn failed. A resting state, not a busy one | Any recognised failure |

### 4.1 Transitions

```
        ┌──────────────► unsupported   (terminal, derived at mount)
        │
   ┌────┴────┐   startListening()   ┌────────────┐
   │  idle   │─────────────────────►│ listening  │
   │         │◄─────────────────────│            │
   └─────────┘   stopListening()    └─────┬──────┘
        ▲                                 │ final transcript / typed submission
        │                                 │ recognised failure, timeout, or
        │                                 │ an end with nothing heard
        │                                 ▼
        │   speak() === false       ┌─────────────┐
        ├───────────────────────────│ processing  │
        │                           └─────┬───────┘
        │                                 │ accepted | generation_unavailable
        │                                 │ out_of_scope | uncertain
        │                                 ▼
        │                           ┌─────────────┐
        │                           │  speaking   │
        │        onEnd() ───────────┤             │
        ├───────────────────────────┴─────────────┘
        │                                 │
        │                           any failure
        ▼                                 ▼
   ┌────────┐   startListening()   ┌────────┐
   │ error  │─────────────────────►│ (cycle)│
   │        │   retry()            └────────┘
   └────────┘   dismissError() ──► idle
```

Two rules are worth stating because the UI depends on them:

- **`unsupported` is derived, not entered.** Feature detection runs during render and is
  deliberately *not* stored in state. A browser's speech APIs do not appear or disappear
  mid-session, so a stored copy could only ever disagree with the truth. The consequence is
  that `unsupported` is a state the UI renders rather than an error it catches.
- **`error` is a resting state.** Starting again from `error` is exactly what the user asked
  for when they dismissed the failure, so `error` is a legal origin for both a new
  utterance and a retry. Every other busy state refuses both.

`cancel()` and unmount both abandon work in flight and return to `idle`: the recogniser is
aborted, the browser's speech queue is cancelled and the in-flight request is aborted.
Responses that arrive after unmount are dropped rather than written into a dead tree.

---

## 5. Speech recognition

`createSpeechRecogniser(options)` returns `{ start, stop, abort }`. It is a thin, literal
wrapper, and the guarantees it makes are the contract the state machine is written against:

- **`onEnd` fires exactly once per session**, whenever it started — after a final result,
  after an error, after the timeout, or after a bare stop. The spec guarantees it; the
  wrapper latches it anyway, so a browser that breaks the guarantee degrades into one
  redundant call rather than a state reset mid-turn.
- **`onFinal` fires at most once per session, and never alongside an `onError`.**
- **An utterance that trims to nothing is reported as `no_speech`**, not as a successful
  empty transcript. An empty string sent to the classifier is a request with no content, and
  a 422 would be a worse description of it than "I didn't hear anything".
- **A fresh instance per session.** A recogniser that has ended carries the previous
  session's handlers and cannot be restarted while its `onend` is still pending — which is
  exactly the window a fast retry lands in.
- **A second `start()` while a session is running is ignored.** The browser throws
  `InvalidStateError`, and the start button is reachable twice within milliseconds of
  itself by pointer and by keyboard. Ignoring the second press is the honest outcome.
- **One utterance per session.** `continuous` is `false` and `maxAlternatives` is `1`,
  because the model behind this is single-utterance too.

### 5.1 The timeout

A recogniser left running never returns on its own: it holds the microphone open
indefinitely and leaves the user in a listening state with no way out. `RECOGNITION_TIMEOUT_MS`
(15 000 ms) turns an unbounded session into a reported `timeout`, and the abort releases the
hardware.

### 5.2 Browser errors, translated

Every code the API can raise that means something to a user has copy written for it. There
is no `unknown` case by design — an unrecognised condition maps to `recognition` rather
than surfacing a raw browser string, because *"service-not-allowed"* shown to a user is a
worse experience than an honest *"speech recognition failed"*.

| Browser code | `VoiceErrorCode` | Retryable | Why |
| --- | --- | --- | --- |
| `not-allowed` | `permission_denied` | yes | The user or the browser refused the microphone. Worth retrying once a setting changes. |
| `service-not-allowed` | `permission_denied` | yes | The browser is blocking its own recognition service. |
| `audio-capture` | `microphone_unavailable` | yes | No microphone, or the OS would not give us one. |
| `no-speech` | `no_speech` | yes | Listening started but nothing arrived. Not a failure. |
| `network` | `recognition_failed` | yes | Recognition needs a network connection and the service could not be reached. |
| `language-not-supported` | `not_supported` | **no** | A language this browser will never support. |
| *(no recogniser at all)* | `not_supported` | **no** | Firefox. Rendered as a state, not an error. |
| *(the 15 s deadline)* | `timeout` | yes | NEXO stopped listening without a full request. |

`aborted` is **excluded from the table at the type level** rather than given an entry. It is
what the API reports when *NEXO* stops a session, so surfacing it would turn every
deliberate stop into a failure — and excluding it from `SurfacedErrorCode` means routing it
to `onError` is a compile error rather than a runtime mistake.

---

## 6. Speech synthesis

`speak(text, options)` returns **whether audio actually started**. `false` means nothing was
spoken and **no callback will fire at all**, which is why the hook branches on the return
value rather than waiting for an `onEnd` that is never coming — waiting is how an assistant
gets stuck saying *speaking* for ever.

Three rules are load-bearing:

- **Whitespace-only text is refused outright.** An empty utterance is a known way to wedge a
  synthesis queue: the browser may accept it, report no error and never emit `end`. There is
  nothing to say, so there is nothing to risk. Refusing also means a blank cannot cancel a
  real utterance in progress.
- **A new utterance cancels the previous one, and the cancelled one's `onEnd` settles before
  the replacement starts.** Without this, a user who asks a second question during the first
  reply hears both answers at once and cannot tell which belongs to which. Callbacks stay
  ordered — `onEnd` (old), then `onStart` (new) — which is what makes the count
  independent of which of the browser's own events arrives first.
- **The `canceled` and `interrupted` codes are settled, not reported.** A browser that
  cancels on *our* `cancel()` has told us nothing we do not already know, and reporting it
  would raise an error for a deliberate action.

Voice selection prefers an exact BCP-47 match, falls back to the base language, and falls
back again to the browser default rather than failing. A slightly wrong accent is better than
no spoken output at all, and an empty voice list — a normal state in some browsers, because
voices load asynchronously — is not an error.

### 6.1 What is ever spoken aloud

Every spoken line is a statement about *routing*, never an answer:

| Outcome | Spoken |
| --- | --- |
| `accepted` | "Routing to Task." |
| `generation_unavailable` | "NEXO recognised that request, but it runs no generative model, so there is nothing here that can answer it." |
| `out_of_scope` | "That is outside what NEXUS can route." |
| `uncertain` | "I am not confident enough to route that. Try naming the surface you want." |

That bounds the awkward cases: an utterance is a sentence or two, never a paragraph to read
out, and no copy on this surface implies NEXO composed something.

---

## 7. The conversation store

`assistant-store.ts` holds what the **user said**, and nothing else. The live lifecycle
belongs to the hook, which owns it as component state. Keeping them apart means the
microphone has one owner and the transcript has another, and a conversation can be rendered
without a microphone ever being opened.

Two properties are enforced by construction:

- **Bounded.** `MAX_CONVERSATION_TURNS` (20) is applied **on write**, not on read, so a long
  session cannot grow without limit and a reader never has to truncate. Turns are appended
  and the array is re-sliced; the previous array is never mutated in place, because the
  transcript component may be holding it.
- **Not persisted.** There is no `persist` middleware, and a test asserts the conversation
  is never written to browser storage. A transcript is the user speaking into their machine —
  meeting notes, a dictated task, a half-formed thought — and `localStorage` would leave it
  readable by anything that can run script on this origin, outliving the tab and surviving a
  sign-out. The conversation lives as long as the panel is open and no longer.

`accepted` records where the last *routed* turn landed, and is cleared with the turns rather
than left behind: a suggestion pointing at a destination from a conversation the user just
deleted is a claim about something that no longer exists.

---

## 8. The API surface

The voice layer consumes the two Phase 11 endpoints and adds nothing to them.

| Method | Path | Body | Auth | Used by |
| --- | --- | --- | --- | --- |
| `POST` | `/api/v1/ml/route` | `{ text }` — `extra: 'forbid'`, `min_length=1`, `max_length=ML_MAX_INPUT_CHARS` (2000) | bearer + `analytics.read` | **Every turn**, spoken or typed |
| `GET` | `/api/v1/ml/status` | — | bearer + `analytics.read` | Diagnostics; not on the voice path |

`services/ml.ts` sends `text` verbatim, sets `ROUTING_TIMEOUT_MS` (30 000) explicitly rather
than inheriting the client default, and never retries — a 503 `ml_unavailable` means the
checkpoint is not loaded and will not be loaded in the two seconds a retry would wait. A
turn is retried by the user, not by the client.

### 8.1 Status codes, and what the UI says about each

| Code | `VoiceErrorCode` | Retryable | Copy, in short |
| --- | --- | --- | --- |
| 200 — `accepted` | — | — | "Routing to Task." Names the service and the entrypoint. |
| 200 — `uncertain` | — | — | Below the threshold; names up to two runners-up with their probabilities. |
| 200 — `out_of_scope` | — | — | Names the surfaces NEXO does have. |
| 200 — `generation_unavailable` | — | — | A capability gap, not a failure. `code_assist` and `deep_reasoning` both land here. |
| 401 | `not_authenticated` | no | "NEXO does not know who is asking." |
| 403 | `not_permitted` | no | Same copy, a different code — a rejected session and a missing capability are different problems for the bug log. |
| 422 | `invalid_response` | no | "Rephrase it as a single short instruction", with the field-level detail appended. |
| 503 `ml_unavailable` | `classifier_unavailable` | no | Names the missing classifier rather than blaming the network, and says everything else still works. |
| transport failure | `timeout` | yes | Could not reach the backend. |
| client timeout | `timeout` | yes | The classifier did not answer in time; "it is the only model NEXO runs". |
| 500 and anything else | `invalid_response` | yes | A server fault NEXO cannot explain. A test asserts no raw backend message leaks into the copy. |

The order of those checks matters and is not arbitrary: a 503 arrives as an `ApiError`
alongside transport codes, and a timeout is *also* a transport failure, so the specific
cases are tested before the general one.

### 8.2 A note on `MlStatusRead`

`frontend/src/types/ml.ts` and `backend/app/schemas/ml.py` do **not** currently agree field
for field on the status response: the frontend declares `reason` and `detail` where the
backend publishes `unavailable_reason`, and the backend publishes `taxonomy_version` which
the frontend does not declare. This is Phase 11 drift rather than a Phase 12 change, and the
voice path does not consume it — nothing in `features/assistant/` calls `fetchMlStatus`. It
is recorded here because a reader tracing the assistant to its diagnostics endpoint will land
on it.

---

## 9. Configuration

**Phase 12 introduced no new configuration.** There is no new environment variable, no new
setting, no new flag and no new file in `.env.example`.

| What | Where it is decided |
| --- | --- |
| Whether the classifier is enabled, which checkpoint it loads, the confidence threshold, the input length bound and the credential screen | The existing seven `ML_*` settings, documented in `docs/development.md` §11.6 and in the README |
| Whether the microphone is available | **The browser, at the permission prompt.** Not a server setting and not something NEXO can grant |
| Which language the recogniser listens in | The browser's own default, unless a caller passes `lang` to the hook |
| Whether the outcome is read aloud | The browser's synthesis support, and the hook's `speechEnabled` option |

The one thing a user may reasonably look for here — a switch that keeps the microphone
local — **does not exist and cannot be added without a second model**. See §1.

---

## 10. Security

**Voice input is ordinary text input.** The browser produces a string; everything downstream
is the same code path a typed request takes.

- **The transcript goes to `POST /api/v1/ml/route`** with the same bearer token and the same
  `analytics.read` permission as a typed request. There is no privileged voice route, and no
  route that trusts a spoken utterance more than a typed one.
- **The backend applies the same validation, the same length bound (`ML_MAX_INPUT_CHARS`,
  2000 characters) and the same credential screening (`ML_REJECT_CREDENTIALS`) to a spoken
  utterance as to a typed one.** The text is passed through byte for byte — normalising it
  would be a distribution shift the model was never trained on — and the submitted text is
  never logged. What is logged is the intent, the confidence, the destination and the
  latency.
- **NEXO never executes anything a voice command says.** The classifier produces a
  *validated, named action* against a closed set of fourteen intents. Executing it is Phase
  13's Command Center and **does not exist yet**. A decision names `TaskService.list`; it
  does not call it, and the caller makes the call through the same authenticated,
  owner-scoped route they would have used had they typed the request themselves.
- **The conversation never leaves the browser.** It is bounded at 20 turns, held in a Zustand
  store with no persistence middleware, and cleared by the user from the panel.
- **Only the current utterance is ever sent.** A test asserts the request body is exactly
  `{ text }` — no history, no context, no page — on a second turn *and* on a retry.

### 10.1 Refused by design

| Refused | Why |
| --- | --- |
| Executing a spoken instruction | Phase 13's job. Phase 12 shows the decision and stops. |
| Shell commands and arbitrary commands | There is no command interpreter anywhere in this path. |
| Filesystem paths taken from speech | Nothing in the assistant resolves a path or reads one. |
| A second model — Whisper, an LLM, a cloud AI API | The one-model constraint. It is why §1 exists. |
| Sending secrets to the browser or the model | The backend screens credential-shaped input; no secret is rendered to the client, and none is placed in a spoken confirmation. |
| Auto-retrying a failed turn | A 503 will not resolve in the two seconds a retry would wait. The failed turn is offered back to the user, who decides. |

---

## 11. Limitations

These are the honest edges of the feature. None of them is a defect awaiting a fix, and
none of them is softened anywhere in the product copy.

### 11.1 Single-utterance context

**The classifier has no dialogue state.** It maps a string to one of fourteen labels. It
cannot be told what "that" refers to.

Conversation history is therefore **never sent to the backend**. Not to protect privacy
alone — although it does that too — but because the model would ignore it. History is used
for exactly two things: rendering the log, and offering a *"You last routed Tasks"* chip when
a turn comes back `uncertain`. That chip is the entire substitute for conversational memory:
the assistant cannot ask *"what did you mean by it?"* and have the model reason about the
exchange, so the only continuity available is to name the destination it accepted a moment
ago and let the user accept it again.

**Referential follow-ups are a known weak point.** *"Tell me more about it"*, *"and the
trends"*, *"what about the other one"* — these classify as whatever the model thinks they
are, and on the natural-language set measured in Phase 11 they were among the misses. The UI
surfaces the previous surface rather than pretending to resolve the pronoun, and the typed
field's own hint says *"Only this request is sent. The history below stays in your
browser."*

### 11.2 NEXO routes, it does not answer

There is no generative model behind this surface, so a voice request produces a **routing
decision naming an existing NEXO service** — not prose. Twelve of the fourteen intents name
a real service and a real entrypoint. The remaining two, `code_assist` and
`deep_reasoning`, return `generation_unavailable`: NEXO recognised the request correctly and
has nowhere to send it, because NEXO runs no language model.

`generation_unavailable` is a **stated capability gap, not a bug**. It is reported as such in
the badge (warning, never danger), in the body copy, and in the spoken confirmation. Only
the `intent` field — never the `status` — would let a reader turn a correct recognition into
an apparent failure.

### 11.3 The accuracy finding carries forward

**Phase 11 measured 42/56 — 75.0% — on hand-written natural language**, against 34/34 on
representative phrasings. Two findings sit behind that number and neither is softened here:

- **Seven of the fourteen misses collapse into `risk_query`.** The `out_of_scope` class was
  meant to absorb "nothing here fits", but the model treats `risk_query` as the sink for
  anything conversational, uncertain or reflective it cannot place. It is a wrong *read*
  rather than a wrong write — `RiskDetectionService.evaluate` does not mutate — but it is a
  wrong answer delivered confidently.
- **Five of those misses were predicted at or above the shipped 0.90 threshold.**

So the confidence gate is a **routing guard, not an accuracy defence**. Branch on `status`,
show `reason` and `alternatives`, and never treat `accepted` as a command to issue.

**Voice does not improve any of this.** It is the same model behind a different input method.
Speaking an utterance does not make it more representative, and the transcriptions are more
error-prone than typed text, not less. Anyone extending this surface should read
[`specifications/phase-11-report.md`](./specifications/phase-11-report.md) §8 before they
write a line of UI over it.

### 11.4 And back to the microphone

**Chrome and Edge send your audio to the browser vendor. Firefox cannot listen at all.**
This is §1, restated here so nobody reaches the limitations list and believes it is
complete without it.

---

## 12. Tests

The frontend suite is `52 files` under `frontend/src` — counted, not estimated. The
assistant tree contributes **8 of those files** and **154 test cases**, roughly a fifth of
the suite.

| File | Cases | What it is the only place that proves |
| --- | --- | --- |
| `speech-recognition.test.ts` | 42 | Feature detection across both spellings, `onEnd` exactly once, `onFinal` never alongside `onError`, the last-final-not-concatenation rule, all six browser error codes, the 15 s abort-and-report, and that the privacy notice still says the audio leaves the device |
| `speech-synthesis.test.ts` | 25 | Feature detection, callback arity, **overlap** (the old utterance settles before the new one starts; a late `end` from the cancelled one cannot settle the replacement), blank-text refusal, five-step voice selection, all twelve browser codes, and a browser that throws instead of reporting |
| `use-voice-assistant.test.ts` | 45 | The whole state machine, the six guards, every failure mapping, unmount cleanup, and the store |
| `components/conversation-log.test.tsx` | 10 | That a decision is never phrased as a reply, that both honest gaps are stated, and that a turn is only offered for retry when retrying could work |
| `components/record-button.test.tsx` | 10 | Destructive styling while recording, the pulse, reduced motion, and that a disabled control calls nothing |
| `components/voice-assistant.test.tsx` | 10 | The disclosure renders, typing survives with no microphone, the live region mounts empty and stays mounted, and exactly one heading renders below the page masthead |
| `components/voice-state-pill.test.tsx` | 7 | Shape-distinct glyph beside every word, and that a capability gap is not dressed as a failure |
| `pages/assistant-page.test.tsx` | 5 | One masthead, the honest one-liner, the model named, and that the page is **no longer** described as *Planned* |

`features/modules/catalog.test.ts` sits alongside: it asserts every nav entry declares a
unique route, label, phase and named icon, and that the sidebar and the command palette read
the same non-duplicated registry. The `/assistant` entry's `phase` is now `12`, so that
assertion covers the rewrite.

### 12.1 How the browser APIs are stubbed

**jsdom implements neither speech recognition nor speech synthesis**, so every one of these
tests installs fakes — three of them in the hook tests, because the backend is also the only
thing that can classify anything.

The fakes are **literal on purpose**: a `FakeSpeechRecognition` with counters for `start`,
`stop` and `abort` and explicit `emitResult` / `emitError` / `emitEnd` methods; a
`FakeUtterance` that records itself and fires nothing on its own; and a stubbed
`globalThis.speechSynthesis` whose `getVoices()` returns whatever a test needs.

Recording rather than firing is deliberate. The wrappers' job is to create the right object
and to guarantee that callbacks arrive exactly once, and a fake that fired events itself
would be asserting against its own choreography rather than against the wrapper. Anything
cleverer would test the fake.

`RECOGNITION_TIMEOUT_MS` is exercised with `vi.useFakeTimers()` and
`vi.advanceTimersByTime`, so the 15-second guard is verified without fifteen seconds passing.

### 12.2 What was not verified here

Standing rule 5 applies: **a test that was not run is not a passing test.** This document
was written by reading the repository. The file count (52) and the assistant tree's case
counts were counted from the test files themselves; the whole-suite pass count reported for
this phase is the runner's figure and was **not** reproduced by this documentation pass.

Nothing here has been exercised against a real microphone, a real browser speech service or
a real audio device. Every behavioural claim about recognition and synthesis is a claim about
the wrappers, which is exactly the layer that can be tested in jsdom.

---

## 13. What Phase 12 did not do

| Not done | Why |
| --- | --- |
| **Execute anything** | Naming `TaskService.list` is Phase 12. Calling it is Phase 13's Command Center. |
| **Add a model** | One model. A local recogniser was the only way to keep audio on the machine, and it was refused (§1.1). |
| **Send conversation history** | The classifier could not use it (§11.1). |
| **Retry automatically** | The user retries; the client does not. |
| **Persist anything** | No `localStorage`, no IndexedDB, no server-side transcript store. |
| **Add a backend route, permission or table** | The existing two `/ml` endpoints are the whole surface. |
| **Add configuration** | See §9. |
| **Improve accuracy** | Same model, different input method (§11.3). |

---

## 14. Related documents

| Document | Covers |
| --- | --- |
| [`specifications/phase-11-ml-integration.md`](./specifications/phase-11-ml-integration.md) | The two endpoints this surface consumes, and the serving boundary behind them |
| [`specifications/phase-11-report.md`](./specifications/phase-11-report.md) | The 75.0% generalisation result, the threshold table, latency, and the limits of all three |
| [`specifications/phase-10-report.md`](./specifications/phase-10-report.md) | The training run behind the checkpoint this assistant talks to |
| [`development.md`](./development.md) | The conventions the code follows, and the seven `ML_*` settings |
| [`api-conventions.md`](./api-conventions.md) | The endpoint contract, error envelope and codes |
| [`../README.md`](../README.md) | Installation, environment variables and the command catalogue |