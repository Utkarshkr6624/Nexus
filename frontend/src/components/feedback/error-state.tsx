import { AlertTriangle, RefreshCw } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { errorDetailNotes } from '@/services/errors'
import { cn } from '@/lib/utils'
import type { ApiError } from '@/lib/api-client'

interface ErrorCopy {
  title: string
  message: string
}

const TRANSPORT_COPY: ErrorCopy = {
  title: 'Cannot reach the NEXUS backend',
  message:
    'The request never reached the API. Make sure the backend is running on port 8000, then try again.',
}

function describe(error: ApiError): ErrorCopy {
  if (error.isTimeout) {
    return {
      title: 'The request timed out',
      message: 'The backend did not respond in time. It may be under load or still starting up.',
    }
  }

  if (error.status === 0) {
    return TRANSPORT_COPY
  }

  switch (error.status) {
    case 400:
    case 422:
      return {
        title: 'That request was not valid',
        message: 'The backend rejected the values you sent. Adjust them and try again.',
      }
    case 401:
      return {
        title: 'Your session has expired',
        message: 'Sign in again to continue. Nothing you submitted has been lost.',
      }
    case 403:
      return {
        title: 'You do not have access to this',
        message: 'This record belongs to another account or role.',
      }
    case 404:
      return {
        title: 'That record does not exist',
        message: 'It may have been removed, or the link may be out of date.',
      }
    case 409:
      return {
        title: 'That conflicts with something already here',
        message: 'Refresh and try again — the existing record wins.',
      }
    case 429:
      return {
        title: 'Too many requests',
        message: 'The backend is rate limiting this endpoint. Wait a moment and retry.',
      }
    default:
      if (error.status >= 500) {
        // The request id is only promised when there is one to show. A failure
        // with no id behind it — a render crash normalised into this surface,
        // a gateway that answered 502 without a body — otherwise told the user
        // to quote a request ID the page then never displayed.
        return {
          title: 'The backend hit an unexpected error',
          message: error.requestId
            ? 'The failure was recorded on the server. Retry, and quote the request ID below.'
            : 'The failure was recorded on the server. Retry, and check the server log if it keeps happening.',
        }
      }
      return { title: 'Something went wrong', message: 'The request could not be completed.' }
  }
}

export interface ErrorStateProps {
  error: ApiError
  onRetry?: () => void
  /** Overrides the copy derived from the error code. */
  title?: string
  className?: string
  compact?: boolean
}

/**
 * The single way a failed request is surfaced. It renders the backend's
 * user-safe message plus the request ID for support — never a stack trace,
 * never raw response bodies.
 */
export function ErrorState({ error, onRetry, title, className, compact = false }: ErrorStateProps) {
  const copy = describe(error)
  const notes = errorDetailNotes(error)
  /**
   * A refused request is the one failure a retry provably cannot fix: the same
   * bytes get the same 400/422, so an offered Retry is a button that can only
   * fail again — and, on a query that fires on every render, a permanent red
   * panel the user cannot get rid of. The copy already says what to change.
   */
  const retry = onRetry && !error.isValidationError ? onRetry : undefined

  return (
    <div
      role="alert"
      className={cn(
        'flex flex-col rounded-lg border border-destructive/30 bg-destructive/[0.04]',
        compact ? 'gap-3 p-4' : 'gap-4 p-6',
        className,
      )}
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-destructive/10 text-destructive">
          <AlertTriangle className="size-4" aria-hidden="true" />
        </span>
        <div className="min-w-0 space-y-1">
          <p className="text-sm font-medium text-foreground">{title ?? copy.title}</p>
          <p className="text-sm leading-relaxed text-muted-foreground">{copy.message}</p>
          <p className="text-sm leading-relaxed text-foreground/80">{error.message}</p>
          {notes.length > 0 && (
            <ul className="space-y-0.5 pt-1 text-xs leading-relaxed text-muted-foreground">
              {notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {error.requestId && (
        <p className="font-mono text-xs text-muted-foreground">
          Request ID <span className="text-foreground/70">{error.requestId}</span>
        </p>
      )}

      {retry && (
        <div>
          <Button type="button" variant="outline" size="sm" onClick={retry}>
            <RefreshCw aria-hidden="true" />
            Retry
          </Button>
        </div>
      )}
    </div>
  )
}
