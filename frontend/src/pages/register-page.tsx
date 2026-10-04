import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { ErrorState } from '@/components/feedback/error-state'
import { AuthShell } from '@/components/layout/auth-shell'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Spinner } from '@/components/ui/spinner'
import { PasswordField } from '@/features/auth/components/password-field'
import { usePasswordRules } from '@/features/auth/use-password-rules'
import { toast } from '@/stores/toast-store'
import { selectDisplayName, useAuthStore } from '@/stores/auth-store'
import type { ApiError } from '@/lib/api-client'
import { bannerError, fieldErrorMessages } from '@/services/errors'

type ConflictField = 'email' | 'username'

interface RegisterFields {
  username?: string
  email?: string
  password?: string
  confirm?: string
}

/**
 * The backend's `USERNAME_PATTERN`, mirrored so the field can say what is
 * wrong before the round trip. Kept character-for-character with
 * `backend/app/schemas/user.py`: a client that is stricter sends work the
 * server would have accepted, and a client that is looser only learns about it
 * from a 422.
 */
const USERNAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$/

const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const PASSWORD_MISMATCH = 'Passwords do not match.'

const QUIET_LINK =
  'text-foreground underline-offset-4 transition-colors duration-150 hover:text-primary hover:underline'

/**
 * A 409 arrives as one flat message — "An account with this email already
 * exists." / "That username is already taken." — with nothing pointing at a
 * field. A taken email or handle is the most actionable thing this form can be
 * told, so it is attached to the input that caused it; only a conflict that
 * cannot be located falls back to the banner.
 */
function conflictTarget(error: ApiError | null): { field: ConflictField; message: string } | null {
  if (error === null || !error.isConflict) return null

  const scoped = fieldErrorMessages(error)
  if (typeof scoped.email === 'string') return { field: 'email', message: scoped.email }
  if (typeof scoped.username === 'string') return { field: 'username', message: scoped.username }

  const text = error.message.toLowerCase()
  if (text.includes('username')) return { field: 'username', message: error.message }
  if (text.includes('email')) return { field: 'email', message: error.message }
  return null
}

function validate(values: {
  username: string
  email: string
  password: string
  confirm: string
  passwordSatisfied: boolean
}): RegisterFields {
  const errors: RegisterFields = {}
  const handle = values.username.trim()

  if (handle.length === 0) {
    errors.username = 'Choose a username.'
  } else if (!USERNAME_PATTERN.test(handle)) {
    if (handle.startsWith('_') || handle.startsWith('-')) {
      errors.username = 'Start with a letter or a number.'
    } else if (handle.length < 3) {
      errors.username = 'Use at least 3 characters.'
    } else if (handle.length > 32) {
      errors.username = 'Use at most 32 characters.'
    } else {
      errors.username = 'Use letters, numbers, underscores and hyphens only.'
    }
  }

  const address = values.email.trim()
  if (address.length === 0) {
    errors.email = 'Enter your email address.'
  } else if (!EMAIL_SHAPE.test(address)) {
    errors.email = 'Enter a valid email address, for example you@nexus.local.'
  }

  if (values.password.length === 0) {
    errors.password = 'Enter a password.'
  } else if (!values.passwordSatisfied) {
    errors.password = 'Meet every requirement in the checklist.'
  }

  if (values.confirm.length === 0) {
    errors.confirm = 'Confirm your password.'
  } else if (values.confirm !== values.password) {
    errors.confirm = PASSWORD_MISMATCH
  }

  return errors
}

export default function RegisterPage() {
  const navigate = useNavigate()
  const register = useAuthStore((state) => state.register)
  const pending = useAuthStore((state) => state.pending)
  const error = useAuthStore((state) => state.error)
  const clearError = useAuthStore((state) => state.clearError)

  const [displayName, setDisplayName] = useState('')
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')

  // Errors stay hidden until a submit has been attempted, except the
  // confirmation mismatch: that one is a fact the form can settle on its own,
  // and it surfaces as soon as the field is left — which is also when a
  // password manager filling both fields gets to see it.
  const [attempted, setAttempted] = useState(false)
  const [confirmTouched, setConfirmTouched] = useState(false)

  const usernameRef = useRef<HTMLInputElement>(null)
  const emailRef = useRef<HTMLInputElement>(null)
  const passwordRef = useRef<HTMLInputElement>(null)
  const confirmRef = useRef<HTMLInputElement>(null)

  const { allSatisfied } = usePasswordRules(password)
  const errors = validate({ username, email, password, confirm, passwordSatisfied: allSatisfied })

  const fieldErrors = error ? fieldErrorMessages(error) : {}
  const conflict = conflictTarget(error)
  const confirmMismatch = errors.confirm === PASSWORD_MISMATCH

  const usernameError =
    fieldErrors.username ??
    (conflict?.field === 'username' ? conflict.message : undefined) ??
    (attempted ? errors.username : undefined)
  const emailError =
    fieldErrors.email ??
    (conflict?.field === 'email' ? conflict.message : undefined) ??
    (attempted ? errors.email : undefined)
  const passwordError = fieldErrors.password ?? (attempted ? errors.password : undefined)
  const confirmReported = attempted || confirmTouched
  const confirmError = confirmReported ? (errors.confirm ?? undefined) : undefined

  // Nothing else is reported twice: the banner stays for what a field cannot
  // express — transport failures, a conflict with no locatable field, a 5xx.
  const showBanner = conflict === null && bannerError(error) !== null

  // The button is blocked only once the mismatch is actually on screen. A
  // disabled control cannot be focused, so blocking it before its explanation
  // exists strands a keyboard user in front of a button that will not respond
  // and will not say why.
  const confirmBlocked = confirmReported && confirmMismatch

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setAttempted(true)

    // The mismatch is the one error that blocks the button, and only once it
    // is visible. A disabled submit with no explanation is a keyboard dead
    // end, so the button stays enabled for everything the server could have an
    // opinion about — the policy checklist and the shape of the username are
    // already on screen above it, and submitting hands them over for
    // judgement. The confirmation is different: it is a transcription of a
    // value the form already holds, the client settles it definitively with no
    // round trip, and showing the error first costs nothing.
    if (confirmMismatch) {
      confirmRef.current?.focus()
      return
    }

    if (errors.username) {
      usernameRef.current?.focus()
      return
    }
    if (errors.email) {
      emailRef.current?.focus()
      return
    }
    if (errors.password) {
      passwordRef.current?.focus()
      return
    }
    if (errors.confirm) {
      confirmRef.current?.focus()
      return
    }

    await register({
      username: username.trim(),
      email: email.trim(),
      password,
      ...(displayName.trim() ? { display_name: displayName.trim() } : {}),
    })

    const { status, user } = useAuthStore.getState()
    if (status !== 'authenticated') return

    toast.success('Welcome to NEXUS', `Signed in as ${selectDisplayName(user)}.`)
    navigate('/dashboard', { replace: true })
  }

  return (
    <AuthShell
      title="Create your account"
      description="One account per browser profile. Nothing leaves this machine."
      footer={
        <>
          Already have an account?{' '}
          <Link to="/login" className={QUIET_LINK}>
            Sign in
          </Link>
        </>
      }
    >
      <Card>
        {/* No `CardHeader` — `AuthShell` already owns the page masthead — so the
            content keeps the card's own side padding instead of `pt-0`. */}
        <CardContent className="pt-5">
          {/* `noValidate` for the same reason as the sign-in form: the validation
              below is the one that runs, and a native bubble would pre-empt
              every inline message it replaces.

              `gap-4` over the shared `gap-5`: this is the tallest form in the
              product, and it is the one screen that has to fit a 900px laptop.
              Five fields, a live checklist and a strength meter add up, so the
              rhythm is tightened here rather than anywhere the gap is load
              bearing. */}
          <form onSubmit={onSubmit} noValidate className="app-form-stack gap-4">
            {showBanner && error && <ErrorState error={error} compact />}

            <div className="app-form-field">
              <Label htmlFor="display_name" optional>
                Display name
              </Label>
              <Input
                id="display_name"
                autoComplete="name"
                placeholder="How NEXUS should greet you"
                value={displayName}
                disabled={pending}
                onChange={(event) => {
                  setDisplayName(event.target.value)
                  clearError()
                }}
              />
            </div>

            <div className="app-form-field">
              <Label htmlFor="username">Username</Label>
              <Input
                ref={usernameRef}
                id="username"
                autoComplete="username"
                placeholder="ada"
                value={username}
                disabled={pending}
                error={Boolean(usernameError)}
                success={USERNAME_PATTERN.test(username.trim())}
                aria-describedby={usernameError ? 'username-error' : 'username-hint'}
                onChange={(event) => {
                  setUsername(event.target.value)
                  clearError()
                }}
              />
              {usernameError ? (
                <p id="username-error" className="app-form-error">
                  {usernameError}
                </p>
              ) : (
                <p id="username-hint" className="app-form-hint">
                  3–32 characters. Must start with a letter or number; _ and - allowed.
                </p>
              )}
            </div>

            <div className="app-form-field">
              <Label htmlFor="email">Email</Label>
              <Input
                ref={emailRef}
                id="email"
                type="email"
                inputMode="email"
                autoComplete="email"
                placeholder="you@nexus.local"
                value={email}
                disabled={pending}
                error={Boolean(emailError)}
                success={EMAIL_SHAPE.test(email.trim())}
                aria-describedby={emailError ? 'email-error' : undefined}
                onChange={(event) => {
                  setEmail(event.target.value)
                  clearError()
                }}
              />
              {emailError && (
                <p id="email-error" className="app-form-error">
                  {emailError}
                </p>
              )}
            </div>

            <PasswordField
              id="password"
              label="Password"
              value={password}
              onChange={(value) => {
                setPassword(value)
                clearError()
              }}
              inputRef={passwordRef}
              autoComplete="new-password"
              placeholder="Choose something you have not used here before"
              disabled={pending}
              error={passwordError}
              showStrength
              showRules
            />

            <PasswordField
              id="confirm_password"
              label="Confirm password"
              value={confirm}
              onChange={(value) => {
                setConfirm(value)
                clearError()
              }}
              inputRef={confirmRef}
              autoComplete="new-password"
              placeholder="Type it again"
              disabled={pending}
              error={confirmError}
              onBlur={() => setConfirmTouched(true)}
            />

            <div className="flex flex-col gap-3">
              <Button
                type="submit"
                className="w-full"
                disabled={pending || confirmBlocked}
                aria-busy={pending}
              >
                {pending && <Spinner size="sm" label="Creating your account" />}
                {pending ? 'Creating account…' : 'Create account'}
              </Button>
              <p className="app-form-hint">
                Creating the account signs you in. Nothing leaves this machine.
              </p>
            </div>
          </form>
        </CardContent>
      </Card>
    </AuthShell>
  )
}
