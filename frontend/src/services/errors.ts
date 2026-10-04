import { ApiError } from '@/lib/api-client'

/**
 * Normalises anything thrown by a query function or store action into an
 * `ApiError`, so UI code has exactly one error shape to branch on and never
 * has to render a raw `Error` message that might contain internals.
 */
export function toApiError(cause: unknown): ApiError {
  if (cause instanceof ApiError) return cause

  if (cause instanceof DOMException && cause.name === 'AbortError') {
    return new ApiError({ status: 0, code: 'aborted', message: 'Request cancelled' })
  }

  return new ApiError({
    status: 0,
    code: 'unknown_error',
    message: cause instanceof Error ? cause.message : 'Something went wrong',
  })
}

/** True when React Query (or the caller) cancelled the request. */
export function isAbortError(cause: unknown): boolean {
  return cause instanceof DOMException && cause.name === 'AbortError'
}

/**
 * Flattens a 422's `details.errors[]` into `{ field: message }`, per
 * `docs/api-conventions.md`.
 *
 * Entries that are not field-scoped are dropped and the first message wins when
 * a field repeats, so a form renders one message per input rather than a stack
 * of them. This is the single flattening rule for the whole app: a copy per
 * surface is a copy that will eventually disagree about which entries count.
 */
export function fieldErrorMessages(error: ApiError | null): Record<string, string> {
  if (!error) return {}
  const { errors } = error.fieldErrors
  if (!Array.isArray(errors)) return {}

  const messages: Record<string, string> = {}
  for (const entry of errors as Array<{ field?: unknown; message?: unknown }>) {
    const field = entry?.field
    const message = entry?.message
    if (typeof field !== 'string' || typeof message !== 'string') continue
    if (field === '' || field === 'body' || field in messages) continue
    messages[field] = message
  }
  return messages
}

/**
 * The failure a form should show as a banner, or `null` when no banner is
 * warranted.
 *
 * A banner is only worth showing when no field already says it: a 422 whose
 * messages all landed on inputs is on screen inline, and repeating it above the
 * form is noise. Everything else needs the banner — transport failures, auth
 * refusals, server faults, and a 422 the backend declined to attach to any
 * field at all, which would otherwise be swallowed entirely.
 */
export function bannerError(error: ApiError | null): ApiError | null {
  if (!error) return null
  return error.isValidationError && Object.keys(fieldErrorMessages(error)).length > 0 ? null : error
}
