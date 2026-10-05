import { QueryClient } from '@tanstack/react-query'

import { isAbortError, toApiError } from '@/services/errors'

/**
 * The query retry rule, exported so a test that builds its own `QueryClient`
 * asserts this one instead of a copy of it.
 */
export const queryRetryPolicy = (failureCount: number, error: Error): boolean => {
  if (isAbortError(error)) return false
  const status = toApiError(error).status
  if (status >= 400 && status < 500) return false
  return failureCount < 2
}

/**
 * Shared client. Retries are suppressed for 4xx responses — a rejected request
 * will not become a successful one by asking again — and transport failures
 * get three tries in total, the initial request plus two retries, which covers
 * the common "backend still starting" case.
 *
 * `networkMode: 'always'` overrides the library default of `'online'`, which
 * *pauses* work whenever `navigator.onLine` is false and resumes it on the
 * `online` event. That default is a silent queue: a submit pressed in a tunnel
 * never leaves the tab, so its dialog sits on "Saving…" with Cancel and Save
 * both disabled and no message anywhere — indistinguishable from a server that
 * has hung, and the typed input silently gone if the tab is closed. With
 * `'always'` the request is attempted, fails immediately with the transport
 * error the failure surface already words honestly, and the form is released
 * with the reason on screen.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: queryRetryPolicy,
      networkMode: 'always',
    },
    mutations: {
      retry: false,
      networkMode: 'always',
    },
  },
})