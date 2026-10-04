import { ShieldCheck } from 'lucide-react'
import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'

import { ErrorState } from '@/components/feedback/error-state'
import { AuthShell } from '@/components/layout/auth-shell'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Spinner } from '@/components/ui/spinner'
import { PasswordField } from '@/features/auth/components/password-field'
import { toast } from '@/stores/toast-store'
import { selectDisplayName, useAuthStore } from '@/stores/auth-store'
import { bannerError, fieldErrorMessages } from '@/services/errors'

interface LocationState {
  from?: string
}

interface LoginFields {
  email?: string
  password?: string
}

/**
 * Deliberately loose: it only has to reject what no mailbox could receive
 * before the round trip. Anything stricter starts rejecting addresses that
 * work, and the server is the authority anyway.
 */
const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const QUIET_LINK =
  'text-foreground underline-offset-4 transition-colors duration-150 hover:text-primary hover:underline'

function validate(email: string, password: string): LoginFields {
  const errors: LoginFields = {}
  const address = email.trim()

  if (address.length === 0) {
    errors.email = 'Enter your email address.'
  } else if (!EMAIL_SHAPE.test(address)) {
    errors.email = 'Enter a valid email address, for example you@nexus.local.'
  }

  if (password.length === 0) {
    errors.password = 'Enter your password.'
  }

  return errors
}

export default function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const login = useAuthStore((state) => state.login)
  const pending = useAuthStore((state) => state.pending)
  const error = useAuthStore((state) => state.error)
  const clearError = useAuthStore((state) => state.clearError)

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  // Client-side errors appear once a submit has been attempted, never before:
  // a form that marks itself wrong while someone is still filling it in reads
  // as a scolding machine rather than as help.
  const [attempted, setAttempted] = useState(false)

  const emailRef = useRef<HTMLInputElement>(null)
  const passwordRef = useRef<HTMLInputElement>(null)

  const fieldErrors = error ? fieldErrorMessages(error) : {}
  const localErrors = attempted ? validate(email, password) : {}

  // The server's field-scoped message is authoritative; the local one only
  // stands in until the request has been made.
  const emailError = fieldErrors.email ?? localErrors.email
  const passwordError = fieldErrors.password ?? localErrors.password

  // A 422 whose messages are all attached to fields is already fully visible
  // inline, so a banner saying "that request was not valid" would only repeat
  // it. Everything else — transport, auth, server faults — needs the banner.
  const showBanner = bannerError(error) !== null
  // `ErrorState`'s generic 401 copy talks about an expired session, which is
  // the wrong story on a sign-in form. The title is the one piece a caller
  // can correct, and the backend's own message is already human.
  const bannerTitle = error?.status === 401 ? 'Incorrect email or password' : undefined

  const redirectTo = (location.state as LocationState | null)?.from ?? '/dashboard'

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setAttempted(true)

    const errors = validate(email, password)
    if (errors.email) {
      emailRef.current?.focus()
      return
    }
    if (errors.password) {
      passwordRef.current?.focus()
      return
    }

    await login({ email: email.trim(), password })

    const { status, user } = useAuthStore.getState()
    // Only navigate on success; a failed attempt leaves the store's error to render.
    if (status !== 'authenticated') return

    toast.success('Signed in', `Welcome back, ${selectDisplayName(user)}.`)
    navigate(redirectTo, { replace: true })
  }

  return (
    <AuthShell
      title="Sign in"
      description="Your workspace lives on this machine."
      footer={
        <>
          New to NEXUS?{' '}
          <Link to="/register" className={QUIET_LINK}>
            Create an account
          </Link>
        </>
      }
    >
      <Card>
        {/* No `CardHeader` — `AuthShell` already owns the page masthead — so the
            content keeps the card's own side padding instead of `pt-0`. */}
        <CardContent className="pt-5">
          {/*
            `noValidate` because the validation below is the one that runs:
            `type="email"` would otherwise block submission with a native
            bubble before `onSubmit` ever fires, and the inline messages the
            form actually renders would never appear. The type stays for the
            mobile keyboard and for the semantics it carries.

            `gap-4` over the shared `gap-5`: the signed-out form is the one that
            has to fit a laptop screen whole, and this screen has nothing below
            it but a footer.
          */}
          <form onSubmit={onSubmit} noValidate className="app-form-stack gap-4">
            {showBanner && error && <ErrorState error={error} compact title={bannerTitle} />}

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

            <div className="app-form-field">
              <PasswordField
                id="password"
                label="Password"
                value={password}
                onChange={(value) => {
                  setPassword(value)
                  clearError()
                }}
                inputRef={passwordRef}
                autoComplete="current-password"
                placeholder="Your password"
                disabled={pending}
                error={passwordError}
              />
              <div className="flex justify-end">
                <Link
                  to="/forgot-password"
                  className="text-xs text-muted-foreground underline-offset-4 transition-colors duration-150 hover:text-foreground hover:underline"
                >
                  Forgot password?
                </Link>
              </div>
            </div>

            <div className="flex flex-col gap-3">
              <Button type="submit" className="w-full" disabled={pending} aria-busy={pending}>
                {pending && <Spinner size="sm" label="Signing in" />}
                {pending ? 'Signing in…' : 'Sign in'}
              </Button>

              <p className="flex items-start gap-2 text-xs leading-relaxed text-muted-foreground">
                <ShieldCheck className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                <span>
                  Credentials are exchanged for a token pair and held in this browser only. Signing
                  in opens a device session you can revoke later.
                </span>
              </p>
            </div>
          </form>
        </CardContent>
      </Card>
    </AuthShell>
  )
}
