import { QueryClientProvider, onlineManager, useMutation } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { queryClient } from '@/app/query-client'
import { apiClient } from '@/lib/api-client'

/**
 * What happens to a submit pressed while the machine is offline.
 *
 * React Query's default `networkMode` is `'online'`, which *pauses* work when
 * `navigator.onLine` is false and resumes it on the `online` event. A dialog
 * whose Save depends on such a mutation then sits on "Saving…" with Cancel and
 * Save both disabled and no message anywhere — for as long as the tunnel lasts,
 * the request having never left the tab at all. The honest failure is the one
 * the transport-error copy was written for.
 */

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

function renderSubmit() {
  return renderHook(
    () =>
      useMutation({
        mutationFn: () => apiClient.post('/projects', { name: 'Written in a tunnel' }),
      }),
    { wrapper },
  )
}

afterEach(() => {
  onlineManager.setOnline(true)
  queryClient.getMutationCache().clear()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('a submit pressed while offline', () => {
  it('fails immediately with the reason instead of pausing forever', async () => {
    const fetchMock = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    vi.stubGlobal('fetch', fetchMock)
    onlineManager.setOnline(false)

    const { result } = renderSubmit()
    act(() => {
      result.current.mutate()
    })

    // The request is actually attempted: a paused mutation never fires one.
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    // And it settles, which is what releases the dialog's Cancel and Save.
    await waitFor(() => expect(result.current.isPending).toBe(false))
    expect(result.current.isError).toBe(true)
    expect(result.current.error?.message).toBe('Failed to fetch')
  })
})
