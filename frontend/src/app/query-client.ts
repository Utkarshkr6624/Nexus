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
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: queryRetryPolicy,
    },
    mutations: {
      retry: false,
    },
  },
})