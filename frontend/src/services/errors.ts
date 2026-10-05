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
 * Flattens a 422's `details` into `{ field: message }`, per
 * `docs/api-conventions.md`.
 *
 * The envelope carries more than one shape for this and a renderer that
 * understood only the first left the other two blank:
 *
 * - `{"errors": [{"field", "message", "type"}]}` — the field and its own sentence.
 * - `{"fields": ["name"]}` — the field names only. The sentence explaining them
 *   lives on the envelope's `message`, so it is repeated onto each field: once a
 *   field carries a message {@link bannerError} suppresses the banner, and
 *   dropping it here instead would lose the only copy there was.
 * - `{"field": "url"}` — a single unnamed field, read as a one-entry `fields[]`.
 *
 * Entries that are not field-scoped are dropped and the first message wins when
 * a field repeats, so a form renders one message per input rather than a stack
 * of them. This is the single flattening rule for the whole app: a copy per
 * surface is a copy that will eventually disagree about which entries count.
 */
export function fieldErrorMessages(error: ApiError | null): Record<string, string> {
  if (!error) return {}
  const details = error.fieldErrors
  const messages: Record<string, string> = {}

  const errors = details.errors
  if (Array.isArray(errors)) {
    for (const entry of errors as Array<{ field?: unknown; message?: unknown }>) {
      const field = entry?.field
      const message = entry?.message
      if (typeof field !== 'string' || typeof message !== 'string') continue
      if (field === '' || field === 'body' || field in messages) continue
      messages[field] = message
    }
  }

  const named: unknown[] = []
  if (Array.isArray(details.fields)) named.push(...details.fields)
  else if (typeof details.field === 'string') named.push(details.field)
  if (named.length > 0 && error.message) {
    for (const entry of named) {
      const field =
        typeof entry === 'string' ? entry : (entry as { field?: unknown } | null)?.field
      if (typeof field !== 'string') continue
      if (field === '' || field === 'body' || field in messages) continue
      messages[field] = error.message
    }
  }

  return messages
}

/**
 * The extra sentences a `details` payload carries beyond any field message.
 *
 * `{"allowed": [...]}` names the orderings or state changes that would have been
 * accepted; `{"accepted": [...]}` does the same for a refused enum. Neither is a
 * field error, so nothing else on the page renders them — and a 422 that says
 * only what was wrong leaves the caller with no idea what to send instead.
 */
export function errorDetailNotes(error: ApiError | null): string[] {
  const details = error?.details
  if (!details) return []

  const notes: string[] = []
  const labels: ReadonlyArray<readonly [string, string]> = [
    ['allowed', 'Allowed'],
    ['accepted', 'Accepted'],
  ]
  for (const [key, label] of labels) {
    const value = details[key]
    if (!Array.isArray(value) || value.length === 0) continue
    const words = value
      .filter((entry) => typeof entry === 'string' || typeof entry === 'number')
      .map((entry) => String(entry))
    if (words.length === 0) continue
    notes.push(`${label}: ${words.join(', ')}`)
  }
  return notes
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
