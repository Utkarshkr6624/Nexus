# Phase 2 QA — Account UX and accessibility auditor

**Scope covered:** every route and state on the signed-out credential surface and the whole settings surface.

- `frontend/src/pages/login-page.tsx`, `register-page.tsx`, `forgot-password-page.tsx`, `reset-password-page.tsx`, `settings-page.tsx`
- `frontend/src/features/auth/` — `components/password-field.tsx`, `components/password-rules-checklist.tsx`, `password-rules.ts`, `password-strength.ts`, `use-password-rules.ts`, `session-labels.ts`
- `frontend/src/features/settings/` — `profile-form.tsx`, `password-form.tsx`, `sessions-panel.tsx`, `preferences-panel.tsx`, `danger-zone.tsx`
- `frontend/src/services/auth.ts`, `errors.ts`, `users.ts`, `sessions.ts`; `frontend/src/stores/auth-store.ts`; `frontend/src/components/feedback/error-state.tsx`; `frontend/src/components/ui/input.tsx`, `label.tsx`, `spinner.tsx`; `frontend/src/components/layout/auth-shell.tsx`; `frontend/src/routes/router.tsx`, `guards.tsx`, `app-layout.tsx`
- Contract cross-check against the live `app.openapi()` dump and against `backend/app/schemas/user.py`, `backend/app/services/auth_service.py`, `backend/app/core/exceptions.py`.

**Audit trail note:** the backend writes `audit_logs` rows (`ACCOUNT_DELETED`, `ACCOUNT_REGISTERED`, password changes, …) but exposes **no** audit HTTP route — `/api/v1/audit*` is absent from the 145-path OpenAPI dump and no frontend module reads it. There is therefore no audit-trail UI in Phase 2 to audit; the trail is server-side only. Noted, not a finding.

**Runs executed** (all real output quoted below):

1. `cd backend && .venv/Scripts/python.exe -c "from app.main import app; ..."` — dumped all auth/user paths and schemas. `145` paths total; auth/user subset contains exactly `/api/v1/auth/{login,register,refresh,logout,logout-all,me,password,password/forgot,password/reset,sessions,sessions/{session_id}}` and `/api/v1/users/{me,/}`.
2. `password_rule_status('Correct-Horse-Battery-7')` → `min_length/uppercase/lowercase/digit/special` all `True`, labels exactly `Minimum length, Uppercase letter, Lowercase letter, Digit, Special character`.
3. Executable probe `frontend/src/__probe.p02b.probe.test.tsx` (created, run, **deleted** — `ls frontend/src | grep -i p02b` returns nothing), 5 runs of the real components in jsdom. Key printed lines quoted per finding.
4. `cd frontend && npx vitest run src/pages/settings-page.test.tsx src/pages/login-page.test.tsx src/pages/register-page.test.tsx src/pages/auth-form-errors.test.tsx src/features/settings src/features/auth --reporter=basic` → `Test Files 11 passed (11) / Tests 81 passed (81)`.
5. `cd frontend && npx eslint src/pages/{login,register,forgot-password,reset-password,settings}-page.tsx src/features/auth src/features/settings src/services/{auth,errors,users,sessions}.ts` → `Command executed successfully` (no findings).

---

## Findings

### P02b-01 — S2 — Registering with a 256-character display name fails silently: the 422 is swallowed whole

- **Component:** `frontend/src/pages/register-page.tsx:142-156` and `:245-255`
- **Category:** error-handling / correctness
- **Evidence:** probe rendered the real `RegisterPage` with `fetch` stubbed to return exactly what `backend/app/core/exceptions.py:260-275` produces for an over-long field:

  ```
  422 {"error":{"code":"validation_error","message":"The request body or query parameters failed validation.",
                "details":{"errors":[{"field":"display_name","message":"String should have at most 255 characters"}]}}}
  ```

  Probe output:
  ```
  P2 store error: "The request body or query parameters failed validation."
  P2 alert count: 0
  P2 body mentions "255": false
  P2 body mentions "String should": false
  P2 aria-invalid ids: []
  P2 display_name describedby: null
  ```
  The 300 typed characters are still sitting in the field; the form looks untouched.

- **Steps to reproduce:** 1. Go to `/register`. 2. Type 300 characters into **Display name**. 3. Fill Username `ada`, Email `ada@nexus.local`, Password `Str0ng!pass`, Confirm `Str0ng!pass`. 4. Click **Create account**. 5. Observe: no message anywhere; the button simply returns to idle.
- **Expected:** either the input is capped (`maxLength={255}`, exactly as `profile-form.tsx:201` already does) or the server's `display_name` message is rendered against that input.
- **Actual:** the message is dropped on the floor. `showBanner` (`register-page.tsx:156`) calls `bannerError(error)`, which returns `null` whenever *any* field error exists (`services/errors.ts:62-65`) — correct in principle — but `register-page.tsx:142-152` only maps `username`, `email` and `password`. `display_name` has no destination, and the Display name `Input` at `:245-255` takes no `error` prop and no `aria-describedby` at all. Backend limit confirmed in the spec dump: `UserCreate.display_name: anyOf[{type:string,maxLength:255},{type:null}]`.
- **Impact:** a real user (long display name, or a paste) hits a dead button with no feedback and no way to learn the constraint. Silent failure on the primary sign-up flow. It is the only `UserCreate` field whose 422 has nowhere to land — `username`, `email` and `password` all route correctly (verified: a `password` over-length renders inline via `fieldErrors.password`).
- **Suggested fix:** add `maxLength={255}` to the register Display name input (cheap, kills the round trip entirely) **and** wire `fieldErrors.display_name` into the same `error` / `aria-describedby` / `<p id="display_name-error">` triple the other three fields already use, so a deployment that raises the limit still surfaces the server's answer. Prefer defence-in-depth over relying on the client alone.

### P02b-02 — S2 — Every 401 on a credential screen says "Your session has expired. Sign in again to continue."

- **Component:** `frontend/src/components/feedback/error-state.tsx:37-41`; reached from `frontend/src/pages/login-page.tsx:87-144` and `frontend/src/features/settings/password-form.tsx:201-215,246`
- **Category:** ui-ux (misleading copy on a primary flow)
- **Evidence:** probe rendered the real `LoginPage` with `fetch` stubbed to the backend's real `_INVALID_CREDENTIALS` message (`backend/app/services/auth_service.py`, `= "Incorrect email or password."`) at 401. Full `role="alert"` textContent, verbatim:
  ```
  P3 ALERT TEXT >>> "Incorrect email or passwordSign in again to continue. Nothing you submitted has been lost.Incorrect email or password.Request ID req-1"
  ```
  Read as three sentences: *"Incorrect email or password"* / *"Sign in again to continue. Nothing you submitted has been lost."* / *"Incorrect email or password."*
- **Steps to reproduce:** 1. Sign out. 2. Enter a correct email and a wrong password on `/login`. 3. Submit. 4. Read the banner.
- **Expected:** either the generic 401 body is suppressed where a caller supplies its own title, or the whole copy is overridable.
- **Actual:** `ErrorState` takes a `title` prop but derives `message` from `describe(error)` and has no equivalent override. `login-page.tsx:84-87` acknowledges this — "The title is the one piece a caller can correct" — and corrects only the title, leaving the contradictory body in place. The user is told their session expired on the screen where they were trying to start one.
- **Impact:** directly confusing on the sign-in form (the screen every new user meets) and repeated on Settings → Security, where a wrong current password renders *"Your session has expired / Sign in again to continue"* above the inline *"That is not your current password."* Two adjacent sentences disagreeing about what happened.
- **Suggested fix:** add a `message?: string` override to `ErrorStateProps` and have `login-page.tsx` pass a body that matches the story it is telling (e.g. *"Check the address and try again."*), leaving `title` and `message` as one unit so the two cannot drift again. `password-form.tsx:246` should either suppress the banner when `currentError` is set or pass its own body.

### P02b-03 — S2 — Password recovery drops focus to `<body>` twice, and the "focus the email box" line is a no-op

- **Component:** `frontend/src/pages/forgot-password-page.tsx:108-118` and `:112-116`
- **Category:** a11y (focus management, live-region announcement)
- **Evidence:** probe rendered the real `ForgotPasswordPage` with `fetch` stubbed to the real 202 body `{accepted:true, dev_token:"…"}`:
  ```
  P4 focus after success panel: body#(no id).(no class)
  P4 focus is body? true
  P4 focus after "use a different address": body#(no id).(no class)
  P4 focus is body? true
  ```
  The submitted button is unmounted by the `issued ? <SentPanel/> : <form>` swap at `:108-118`, so focus falls to `document.body` and stays there. Nothing on the page is `role="status"` / `aria-live`, so a screen reader announces nothing at all: the user presses Enter on "Send reset link" and the screen goes silent.
- **Steps to reproduce:** 1. `/forgot-password`. 2. Type a valid address, submit. 3. Note (screen reader or devtools) that focus is on `<body>` and the success alert is not announced. 4. Click **Use a different address**. 5. Note focus is *still* on `<body>`, not in the Email field.
- **Expected:** focus moves to the confirmation heading (or the panel) when the form is replaced, the outcome is announced, and "Use a different address" restores focus to the Email input.
- **Actual:** step 4's handler is
  ```tsx
  onUseAnother={() => { setIssued(null); setAttempted(false); emailRef.current?.focus() }}
  ```
  `setIssued(null)` is batched, so the form is still unmounted and `emailRef.current` is `null` at the moment `focus()` is called — the optional-chaining makes the failure silent. (Contrast `login-page.tsx:96-102` and `register-page.tsx:176-196`, which move focus *after* an `await`/re-render boundary and do work.)
- **Impact:** keyboard and screen-reader users are dropped to the top of the document twice on the account-recovery flow, with no announcement of a state change that replaced the entire form. WCAG 2.4.3 Focus Order / 4.1.3 Status Messages.
- **Suggested fix:** wrap the confirmation in a container with `role="status"` (or add `aria-live="polite"`) and `tabIndex={-1}`, then focus it from an effect keyed on `issued`. For the "use another" path, move `emailRef.current?.focus()` into a `useEffect(() => { if (!issued) emailRef.current?.focus() }, [issued])` so it runs after the form is back in the DOM — or render the form in both branches and toggle its visibility, which removes the unmount entirely.

### P02b-04 — S2 — A taken username paints the field green and reports "Everything is fine" on the Settings → Profile form

- **Component:** `frontend/src/features/settings/profile-form.tsx:31-44`, `:191`, `:234-236`, `:243`
- **Category:** a11y / ui-ux
- **Evidence:** probe rendered the real `SettingsPage` (Profile tab) with `fetch` stubbed to the backend's real 409 (`_USERNAME_TAKEN = "That username is already taken."`, `backend/app/services/auth_service.py`):
  ```
  P8 A username class: … border-success focus-visible:ring-success
  P8 B has success ring: true
  P8 C has destructive ring: false
  P8 D aria-invalid: null
  P8 E describedby: profile-username-hint
  P8 F alert: ["That conflicts with something already hereRefresh and try again — the existing record wins.That username is already taken.Request ID r"]
  P8 G status line: Unsaved changes
  P8 H save disabled: false
  ```
  A 409 carries `details: null` (`ConflictError` is raised with no details), so `serverFieldErrors` returns `{}` and the field gets nothing. The `success` prop at `:234-236` is `touched.username && !usernameError && !fieldFailures.username && username !== ''` — all true — so the field wears the **success** border and ring immediately after the server rejected it.
- **Steps to reproduce:** 1. Sign in, open Settings → Profile. 2. Change Username to a value another account holds. 3. Click **Save changes**. 4. Observe the green field, the neutral hint still describing the username rules, and the conflict buried in a generic banner at the top of the card.
- **Expected:** the conflict is attached to the username input — `aria-invalid="true"`, the message in a `<p>` referenced by `aria-describedby`, and the success affordance suppressed.
- **Actual:** green = "valid", `aria-invalid` unset, `aria-describedby` points at the *hint*, message only in a banner whose title is the generic *"That conflicts with something already here"*. A screen-reader user who lands on the field after the failed save is told the value is fine.
- **Impact:** contradictory validation state on a save that failed; WCAG 3.3.1 Error Identification / 4.1.2 Name-Role-Value. This is the exact case `register-page.tsx:51-62` (`conflictTarget`) was written to solve — the same technique is simply missing here.
- **Suggested fix:** extract `conflictTarget` from `register-page.tsx` into `@/services/errors` and reuse it in `ProfileForm`: on a 409, set `fieldFailures.username` (or a dedicated `conflictMessage`) from the backend message, and change the `success` expression to require `!fieldFailures.username`. Cheap, and it makes the two surfaces stop disagreeing.

### P02b-05 — S2 — Settings → Security: the submit button is disabled with no explanation, and Tab silently steps over it

- **Component:** `frontend/src/features/settings/password-form.tsx:277-283`; same pattern at `register-page.tsx:162,349`
- **Category:** a11y (disabled control with no reachable reason; focus order)
- **Evidence:** probe on the real `SettingsPage` → Security tab:
  ```
  P7 D submit disabled (empty): true
  P7 E has "Meet every rule": false
  P7 I submit disabled (weak+mismatch): true
  P7 J explanation: true            <- only once a new password has been typed
  P7 K focus after tab from confirm: BUTTON
  P7 L submit disabled after tab: true
  ```
  The only explanatory copy, `{!ready && newPassword.length > 0 && !policyMet && …}` at `:281-283`, is gated on `newPassword.length > 0`. On a pristine form — the state a user first sees — the button is dead and the page says nothing about it. The register form documents this exact hazard at length (`register-page.tsx:157-162`: *"A disabled control cannot be focused, so blocking it before its explanation exists strands a keyboard user in front of a button that will not respond and will not say why"*) and then applies the same disabled-submit shape at `:349`.
- **Steps to reproduce:** 1. Sign in, Settings → Security. 2. Tab from the confirm field toward the submit button. 3. Observe that focus lands on the confirm field's own reveal toggle and the submit button is silently skipped. 4. On a pristine form, note there is no text anywhere explaining why the button is inert.
- **Expected:** every disabled submit either explains itself in text associated with the button, or stays enabled and validates on submit with focus moved to the offending field — which is what `register-page.tsx:164-196` already does for username/email/password.
- **Actual:** the Security tab uses neither strategy for the *current password* field; and in both surfaces a disabled submit is dropped from the tab order with no discoverable reason.
- **Impact:** a keyboard user cannot tell what is missing, cannot reach the control, and is not told where focus went. On the register form the mismatch explanation *is* reachable (it is the confirm field's own `aria-describedby`), so that half is acceptable — the blocker is the skipped focus stop.
- **Suggested fix:** drop `disabled={!ready}` in favour of `aria-disabled` + an `onSubmit` guard that focuses the first unfilled field and announces it (`aria-busy` on the card while `busy`), matching `register-page.tsx:181-196`. If the disabled state is kept, attach the reason with `aria-describedby` on the button and make it reachable — an `aria-disabled` button stays focusable and can carry that description.

### P02b-06 — S3 — Two password reveal toggles share the accessible name "Show password" on the same screen

- **Component:** `frontend/src/features/auth/components/password-field.tsx:107`; rendered twice by `register-page.tsx:312-343` and `reset-password-page.tsx:188-213`
- **Category:** a11y
- **Evidence:** probe, real `RegisterPage`:
  ```
  P5 toggles named "Show password": 2
  P5 all button names: ["Show password","Show password","Create account"]
  P5 after reveal, names: ["Show password:true","Show password:false"]
  ```
  Same on Reset password: `P9 toggle names: ["Show password","Show password"]`. Meanwhile the Settings → Security form, which uses the *other* component (`features/settings/password-form.tsx:76`), names them `P7 F toggle names: ["Show current password","Show new password","Show confirm new password"]`.
- **Steps to reproduce:** 1. `/register`. 2. Open a screen-reader button list or press Tab to the second toggle. 3. Both announce "Show password, toggle button"; nothing distinguishes the new-password field's toggle from the confirmation field's. 4. Repeat in Settings → Security and hear three distinct names.
- **Expected:** a toggle button's name identifies the control it operates on (WCAG 2.5.3 Label in Name; SC 2.4.6 requires the label to describe purpose).
- **Actual:** identical names within one form. `aria-pressed` does disambiguate *state* (false vs true, per the probe) but not *target*, so a screen-reader user cannot tell which field they are about to reveal.
- **Impact:** confusing on the two highest-traffic credential screens; no visual or AT ambiguity on Settings. Inconsistent affordance between screens that look identical.
- **Suggested fix:** pass a target word through `PasswordField` (e.g. a `nameForToggle` prop or derive it from `label`: `Show ${label.toLowerCase()}`) exactly as `features/settings/password-form.tsx:76` already does, and use the same construction in both components. Do not break `password-field.test.tsx:76` ("keeps its name stable") — that test asserts the name does not flip with the *state*, which a per-field name still satisfies.

### P02b-07 — S3 — Settings → Security never associates the password policy with the field

- **Component:** `frontend/src/features/settings/password-form.tsx:69` vs `frontend/src/features/auth/components/password-field.tsx:77-79`
- **Category:** a11y
- **Evidence:** probe, real Settings → Security tab, `aria-describedby` of every input as rendered:
  ```
  P7 G inputs: ["profile-display-name:text:null","profile-username:text:profile-username-hint",
                "profile-avatar-url:url:profile-avatar-url-hint","_r_2_-current:password:null",
                "_r_2_-new:password:null","_r_2_-confirm:password:null"]
  ```
  The register form, by contrast, wires the checklist in: `P9 pw describedby: new_password-error new_password-rules`.
- **Steps to reproduce:** 1. Settings → Security. 2. Focus "New password". 3. Note that the five-row policy checklist and the strength meter below it are not announced; they must be found by reading the page. 4. Repeat on `/register` and hear them read out at focus.
- **Expected:** identical behaviour across the three surfaces that show the same checklist.
- **Actual:** `features/settings/password-form.tsx:69` sets `aria-describedby={error ? errorId : undefined}` and stops there; the `withFeedback` block at `:91-136` has no id at all. The stricter component is the older one.
- **Impact:** the same policy is announced on sign-up and on password reset but not on password change — the screen where a user is least sure of the rules.
- **Suggested fix:** give the `withFeedback` block an id and include it in `aria-describedby` (together with `errorId`, as `features/auth/components/password-field.tsx:77` already computes), plus an `aria-label` on the `Progress` naming the level as the auth-field component does.

### P02b-08 — S3 — Switching to Settings → Sessions announces nothing while the list loads

- **Component:** `frontend/src/features/settings/sessions-panel.tsx:68-79`, `:209-214`
- **Category:** a11y (status message)
- **Evidence:** probe held the sessions request open and inspected the skeleton:
  ```
  P11 skeleton a11y attrs: [{"cls":"animate-pulse bg-muted size-9 …","aria":"true","busy":null,"role":null},
                            {"cls":"animate-pulse rounded-md bg-muted h-3.5 w-40","aria":"true","busy":null,"role":null},
                            {"cls":"animate-pulse rounded-md bg-muted h-3 w-56","aria":"true","busy":null,"role":null}]
  P11 card aria-busy: null
  ```
  Every loading placeholder is `aria-hidden="true"`, and neither the list nor the card carries `aria-busy` or a status message.
- **Steps to reproduce:** 1. Settings → Sessions with a slow network. 2. After the tab switch, note that nothing is announced and the accessibility tree below the card title is empty.
- **Expected:** `aria-busy="true"` on the region plus a visually-hidden "Loading your sessions" status, so the tab switch is not silent.
- **Impact:** a screen-reader user activating the Sessions tab gets silence; the empty tree reads as "no sessions", which is a different fact from "not loaded yet". The visual skeleton is doing the work sighted users get.
- **Suggested fix:** add `aria-busy` to the `<ul>` (or the card content) and one `role="status"` span reading "Loading your sessions…", removed once `data` arrives.

### P02b-09 — S3 — "Sign out everywhere" is disabled with no reason attached anywhere

- **Component:** `frontend/src/features/settings/sessions-panel.tsx:195-204` and `:170`
- **Category:** ui-ux / a11y
- **Evidence:** probe with a single live session (`is_current: true`):
  ```
  SE A sign-out-everywhere disabled: true
  SE B any tooltip/title: null null
  SE C nearby text: "Sign out everywhere"
  ```
  `canSignOutEverywhere` requires `otherCount > 0`; the button carries no `title`, no `aria-describedby`, and no adjacent copy explaining the disabled state. The count summary "1 device signed in — this one." is a separate paragraph and is not referenced.
- **Steps to reproduce:** 1. Sign in on a single device. 2. Settings → Sessions. 3. Tab to **Sign out everywhere** — it is inert, and a screen reader announces only "Sign out everywhere, button, dimmed" with nothing further.
- **Expected:** either a description attached via `aria-describedby` ("You are the only signed-in device"), or an enabled button that opens the confirm dialog and says so.
- **Impact:** an inert control with an unexplained reason on the security surface, where "is this broken or is it me?" is the wrong first impression.
- **Suggested fix:** give the button `aria-describedby="sessions-signout-everywhere-hint"` pointing at a visually-hidden sentence derived from `otherCount`/`countSummary`, or render it disabled *with* that visible hint.

### P02b-10 — S3 — A spent reset link leaves a fully enabled password form under it

- **Component:** `frontend/src/pages/reset-password-page.tsx:156-186`, `:216`
- **Category:** ui-ux / error-handling
- **Evidence:** probe rendered the real page with `fetch` stubbed to the backend's real `_INVALID_RESET` at 401:
  ```
  RP A alert: "That reset link is no longer validReset links can be redeemed once and expire quickly. A spent link is indistinguishable from a mistyped one, so this page cannot tell you which it was — request a fresh one and it will arrive valid.Request a new link"
  RP B update button still enabled: false
  ```
  The alert copy and the recovery link are exactly right. But the two password fields and **Update password** remain rendered and enabled underneath it — the probe's second submit is accepted by the client and can only ever 401 again.
- **Steps to reproduce:** 1. Open `/reset-password?token=<a token that is not valid>`. 2. Enter and submit a compliant new password. 3. Read the dead-link alert. 4. Type another password and press **Update password** again.
- **Expected:** once the link is known dead, the form is replaced by or demoted below the explanation, the submit is disabled, and the only action offered is "Request a new link".
- **Impact:** a user who does not read the alert keeps re-entering a password that can never be accepted — the worst possible behaviour for a credential field. Copy and focus are fine; only the control state is wrong.
- **Suggested fix:** when `linkDead`, render the password form's `disabled` state and skip the submit (or replace the whole form block with the alert), so the single offered action is the recovery link.

### P02b-11 — S4 — The in-flight submit button's accessible name says the same thing twice

- **Component:** `frontend/src/components/ui/spinner.tsx:29-37`; used at `login-page.tsx:198`, `register-page.tsx:352`, `forgot-password-page.tsx:151`, `reset-password-page.tsx:217`
- **Category:** a11y
- **Evidence:** `Spinner` renders `<span role="status">…<span class="sr-only">{label}</span></span>` next to the button's own visible text. On login the button is `aria-busy` and reads `Signing in` (sr-only) + `Signing in…` (visible). No probe run — this is a read of the component and its four call sites, consistent with the intent of the existing test `login-page.test.tsx:79` ("disables and announces the submit while the request is in flight").
- **Steps to reproduce:** 1. Throttle the network. 2. Submit any of the four forms. 3. Listen to the button.
- **Expected:** one name, plus a busy state.
- **Actual:** the same words twice.
- **Impact:** verbose announcement; no functional loss.
- **Suggested fix:** mark the `sr-only` label `aria-hidden="true"` when the spinner is used inside a control that already names the state, keeping `role="status"` only for standalone spinners (e.g. `BootScreen`).

### P02b-12 — S4 — The destructive "Delete my account" button sits outside its form, and Enter in the password field does nothing

- **Component:** `frontend/src/features/settings/danger-zone.tsx:121-161` and `:163-178`
- **Category:** ui-ux / a11y
- **Evidence:** probe on the real Settings → Account tab with the dialog open:
  ```
  DZ A focus on open: INPUT danger-password
  DZ G tabbables in dialog: ["input#danger-password[ok]","input#[ok]","button#"Cancel"[ok]",
                             "button#"Delete my account"[ok]","button#"Close"[ok]"]
  DZ H fetch called after Enter in password: false
  ```
  `<form onSubmit=…>` wraps the password input and the acknowledgement checkbox (`:121-161`); the destructive action is a `type="button"` in `DialogFooter`, *outside* that form (`:169-174`). Pressing Enter in the password input triggered no request.
- **Steps to reproduce:** 1. Settings → Account → **Delete account**. 2. Type the password, tick the acknowledgement. 3. Press Enter in the password field. 4. Observe no submission in jsdom.
- **Expected:** Enter submits.
- **Actual:** `DZ H` is `false` in jsdom. **UNVERIFIED in a real browser** — Chrome and Firefox do perform implicit submission for a form with no submit button when only one field blocks submission, so this may work in practice. What is unambiguous either way: the form has no submit button, so the destructive action's keyboard affordance depends on implicit submission rather than on anything declared.
- **Also:** this is the only password input in the product with no reveal toggle — `danger-zone.tsx:133-143` uses a bare `Input type="password"` while every other credential field routes through a `PasswordField`. Typing a password blind on a screen that will delete an account is the worst place to omit it.
- **Impact:** small, but on the one irreversible control in the phase.
- **Suggested fix:** move the destructive button inside the `<form>` as `type="submit"`, or add a visually-hidden submit button; and render the field through a `PasswordField` (or add the same `Show password` toggle) for consistency.

### P02b-13 — S4 — `TokenPair.session_id` is typed non-nullable; the backend declares it nullable and optional

- **Component:** `frontend/src/types/api.ts:67`
- **Category:** contract
- **Evidence:** OpenAPI dump:
  ```
  "session_id": {"anyOf": [{"type":"string","format":"uuid"},{"type":"null"}], "title":"Session Id"}
  required: ["access_token","refresh_token","expires_in"]
  ```
  The frontend declares `session_id: UUIDString` with the comment *"Optional on the wire only so a token minted outside the session flow still validates … `null` means 'unknown' — never 'any'."* The type contradicts its own comment.
- **Steps to reproduce:** read `types/api.ts:62-67` against the spec dump above.
- **Expected:** `session_id: UUIDString | null` (and `@default`/optional if the key can be absent).
- **Actual:** non-nullable. Nothing in the Phase 2 surface dereferences it, so there is no live crash today — it is a trap for the next reader.
- **Suggested fix:** widen the type to `UUIDString | null`.

### P02b-14 — S4 — Account tab copy: "Member since … / Last sign-in Never" reads ambiguously on a first session

- **Component:** `frontend/src/pages/settings-page.tsx:95`, `:106-108`
- **Category:** ui-ux
- **Evidence:** probe, real Settings → Account tab with a user whose `last_login_at` is `null`:
  ```
  ACC C role row: Roleuser1 permission
  ACC D member since: Member since1 Jan 2026
  ACC E last sign-in: Last sign-inNever
  ```
  `formatRelativeTime(null)` returns `"Never"` (`features/auth/session-labels.ts:150`), which is right for a *session* ("last used: never") and wrong for *last sign-in* on an account that has simply never signed in since it was created — it reads as "has not signed in again since…".
- **Steps to reproduce:** 1. Create an account in a fresh browser (the store's `user.last_login_at` is `null` until a login is recorded). 2. Settings → Account. 3. Read the **Last sign-in** row.
- **Also at `:95`:** `<span className="capitalize">{user?.role ?? '—'}</span>` produces `Super_admin` for any future multi-word role; `capitalize` is a CSS text transform, not a title-case helper.
- **Impact:** cosmetic; no data loss.
- **Suggested fix:** render `"Not yet"` (or omit the row) when `last_login_at` is `null`, and keep the raw role text — or map the two known roles to display labels.

### P02b-15 — S4 — The backend says the frontend renders server-returned policy flags; it does not

- **Component:** `backend/app/schemas/user.py` (`password_rule_status` docstring) vs `frontend/src/features/auth/password-rules.ts:1-115`
- **Category:** docs
- **Evidence:** the docstring reads *"The frontend renders the returned `satisfied` flags live as the user types."* No route in the 145-path OpenAPI dump exposes rule status, and the frontend hardcodes the mirror, correctly, with the opposite rationale: *"Nothing here gates a request… a client that is stricter sends work the server would have accepted."*
- **Steps to reproduce:** compare the docstring with `password-rules.ts:1-13` and the path dump.
- **Expected:** the docstring describes the actual arrangement (a mirrored constant, ids/labels shared by convention).
- **Actual:** it describes a call that does not exist. The mirror itself is **correct** — I verified every id and label against the live `password_rule_status()` output — so this is documentation drift only, not a defect.
- **Suggested fix:** reword the docstring to state that the client mirrors `PASSWORD_RULES` by id and label and the server remains authoritative, or add the route and switch the checklist to it.

---

## Checked and found correct

Actively exercised; these need no work.

**Login (`/login`)**
- Empty submit: inline messages for both fields, `aria-invalid="true"`, `aria-describedby` resolving to `email-error` / `password-error`, and focus moved to the first offending field — probe printed `P6 email invalid: true describedby: email-error`, `P6 pw invalid: true describedby: password-error`, `P6 focus: input#email`.
- Deliberately loose email shape: the client accepts `a@b.c`, which the backend's stricter pattern rejects; the resulting 422 lands inline on the email field rather than in a banner (`bannerError` suppression verified by reading `services/errors.ts:62-65`).
- `location.state.from` is built only from `location.pathname + location.search` (`routes/guards.tsx:41`), so there is no open-redirect surface; deep-link return is covered by the existing test and passes.
- `noValidate` on every auth form, so the inline messages are the ones that actually fire rather than a native bubble.
- `AuthShell` renders a `<main>` landmark, an `<h1>`, and a single `<form>`; label/`htmlFor` pairs resolve for every input.

**Register (`/register`)**
- 409 conflicts route to the correct input with no duplicate banner — `auth-form-errors.test.tsx` covers email and username; both pass.
- Password policy mirror matches the backend exactly. Verified by running `password_rule_status` for `'Correct-Horse-Battery-7'`, `'Ab1!'`, `'abc'`, `''` and comparing ids (`min_length, uppercase, lowercase, digit, special`) and labels against `password-rules.ts:62-96`. `validate_password_strength('Ab1!')` correctly rejects on `min_length`.
- `USERNAME_PATTERN` is character-for-character identical to `UserCreate.username.pattern` in the spec dump.
- Strength meter and the five-row checklist are correct: hidden until there is a value, level named in text as well as colour, meter scaled to `MAX_STRENGTH_SCORE` rather than 0–100.
- Focus moves to the first offending field on submit (probe: `document.activeElement?.id` = `username`).
- Reveal toggle flips `input.type` and reports state through `aria-pressed` without changing its own name (`password-field.test.tsx` passes).

**Password recovery**
- Forgot password keeps the success copy byte-identical between the dev-token and no-token branches, so the screen cannot be used as an account oracle — read at `forgot-password-page.tsx:44-45, 208, 253`; the panel copy at `:224-228` and `:254-260` carries the same neutral headline. This matches the backend's contract (`PasswordResetRequested.description`: *"`accepted` is always true … a false here would be a signal that the address is not registered"*).
- Reset token round-trips correctly: `dev_token` → `/reset-password?token=…` with `encodeURIComponent` (`:236`).
- Reset with no `?token=` renders a purposeful empty state with a recovery link instead of a broken form (`:99-130`).
- Dead-link alert uses `role="alert"` and correctly refuses to distinguish spent from mistyped, matching `_INVALID_RESET` (401) and the 409 reuse.
- Failure path on forgot-password renders the standard `ErrorState` banner and keeps the form intact (probe `FP A/B`).

**Settings → Profile**
- Client-side validation matches the server: username pattern, `maxLength={255}` on display name, `maxLength={32}` on username, `maxLength`/`http(s)`-only on avatar URL. **I checked the backend for the scheme restriction and it is genuinely enforced** — `backend/app/schemas/user.py:383-394` has an `_check_avatar_url` validator rejecting anything that is not an absolute http/https URL, so the comment at `profile-form.tsx:63-64` is accurate.
- `extra="forbid"` on `UserUpdate` means the profile form's `{display_name, username, avatar_url}` payload cannot silently no-op.
- Dirty tracking and the "Unsaved changes" / "Everything is saved" status line behave correctly across a failed save (probe `P8 G`).
- Email is correctly not editable, and the copy says why.

**Settings → Account / Danger zone**
- Dialog opens with focus on the password input, is wired to `aria-labelledby` / `aria-describedby`, and tab order is password → acknowledgement → Cancel → Delete → Close.
- Destructive action requires both the account password and an explicit acknowledgement; the copy states irreversibility plainly and points at the sessions list as the softer alternative.

**Settings → Security**
- Meter is scaled correctly; all three reveal toggles have distinct, field-specific names.

**Settings → Sessions**
- All three states exist and work: loading skeleton, `EmptyState` for zero sessions, and `ErrorState` with a working **Retry** that recovers on the second response (probe `P12 error text: … / P12 recovered ok`).
- Expired/revoked sessions are listed but excluded from every count; the count summary is derived from a single clock reading.
- 404 on revoke is treated as success, matching the documented "404, never 403" ownership rule.

**Settings → Preferences**
- Theme radiogroup is correctly implemented: `role="radiogroup"` labelled `Theme`, roving tabindex, arrow/Home/End navigation that both moves focus *and* selects. Probe: initial `System:true:tab0`; `ArrowRight` from System wrapped to `Light` and moved focus with it.
- Both switches persist: `localStorage` round-trip verified (`{"sb":"true","mot":"true"}`, re-read as `true` after remount).
- The reduce-motion override genuinely applies a stylesheet, not just an attribute: `style.textContent` length `308`, `sheet.cssRules.length === 1`, and the attribute plus the stylesheet are both removed when the switch is turned off.

**Cross-cutting**
- **Contracts all line up.** Every route, field, and status the Phase 2 clients call exists in the live dump with the expected shape: `POST /auth/login` → `TokenPair`; `POST /auth/register` → `UserRead`; `PATCH /auth/password` → 204; `POST /auth/password/forgot` → **202** (the client treats it as a normal JSON 200-class response, which `apiClient.request` does); `POST /auth/password/reset` → 204; `GET /auth/sessions` → `SessionListRead{sessions[], current_id}`; `DELETE /auth/sessions/{id}` → 204; `PATCH /users/me` → `UserRead`; `DELETE /users/me` with a body → 204; `POST /auth/logout-all` → 204. The error envelope the frontend parses (`{error:{code,message,details,request_id}}` with `details.errors[].field/message`) is exactly what `backend/app/core/exceptions.py:152-207` emits, including the `"body"` fallback field for unaddressed errors. `ApiError.fieldErrors`, `isConflict`, `isUnauthorized`, `isNotFound` all line up with the codes the backend raises. No mismatched routes, fields or enum values found.
- `/settings` is correctly behind `RequireAuth` (`routes/app-layout.tsx:12`); the four credential routes are behind `RequireAnonymous`, so a signed-in user cannot reach `/reset-password` (`routes/router.tsx:71-95`).
- `81/81` existing tests across the phase pass; `eslint` is clean over every file I own.
- `danger-zone.tsx` correctly refuses to send a reset link where a password is required, and the store's `deleteAccount`/`logout` ordering is right (`navigate('/login', {replace:true})` after the session has actually ended).

## Out-of-phase observations

- `backend/app/schemas/user.py` — `password_rule_status`'s docstring describes a frontend/server integration that does not exist (recorded as P02b-15, documentation only). Backend schemas are not my surface.
- `frontend/src/routes/` — there is no route-change focus management or document-title update anywhere in the app. After a successful sign-in, `navigate(redirectTo, {replace:true})` leaves focus on the unmounted submit button. The app-wide fix belongs in the router; I have flagged only the two credential-flow instances that are mine (P02b-03).
- `backend/app/api/v1/users.py` — `GET /api/v1/users/` (list accounts) is described in its own docstring as a permission-system fixture. It has no frontend caller, which is correct, but it is reachable by any admin token and returns an unbounded list. Out of my phase (backend API surface), flagged only because the phase description mentions permissions.