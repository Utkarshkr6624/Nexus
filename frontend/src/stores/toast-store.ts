import { create } from 'zustand'

export type ToastVariant = 'default' | 'success' | 'warning' | 'destructive'

/**
 * Caller-facing tone. Distinct from `ToastVariant` because a tone is a *severity*
 * choice, while a variant only names how the toast looks — `info` is quiet
 * neutral styling, not a fourth status colour.
 */
export type ToastTone = 'info' | 'success' | 'warning' | 'error'

export interface ToastOptions {
  title: string
  description?: string
  variant?: ToastVariant
  durationMs?: number
  action?: { label: string; onClick: () => void }
}

export interface ToastRecord extends ToastOptions {
  id: string
  variant: ToastVariant
}

/**
 * The stack is a fixed window, not a queue. A failing background poll can emit
 * errors faster than a person can read them, and an unbounded queue would bury
 * the page behind a wall of toasts; the oldest entry is dropped instead, so the
 * newest news is always the thing on screen.
 */
export const MAX_VISIBLE_TOASTS = 4

const DEFAULT_DURATION_MS = 5000

/**
 * Timers live beside the store rather than in the components that render it, so
 * a toast dismissed by hand, by a caller holding its id, or by the stack cap
 * stops its own timer instead of firing against a record that is already gone.
 *
 * They are taken through `globalThis` rather than `window`: this module is
 * reachable from non-DOM code, and a `window` reference throws `ReferenceError`
 * in any environment that has no window — including one whose teardown happened
 * while a toast timer was still pending.
 */
const timers = new Map<string, ReturnType<typeof setTimeout>>()

let sequence = 0

function nextId(): string {
  sequence += 1
  return `toast-${sequence}`
}

function cancelTimer(id: string): void {
  const timer = timers.get(id)
  if (timer === undefined) return
  globalThis.clearTimeout(timer)
  timers.delete(id)
}

interface ToastState {
  toasts: ToastRecord[]
  /** Queues a toast and returns its id, for callers that intend to dismiss it. */
  push: (toast: ToastOptions) => string
  dismiss: (id: string) => void
  dismissAll: () => void
}

export const useToastStore = create<ToastState>()((set, get) => ({
  toasts: [],

  push(options) {
    const id = nextId()
    const record: ToastRecord = { ...options, id, variant: options.variant ?? 'default' }

    const overflow = get().toasts.length + 1 - MAX_VISIBLE_TOASTS
    if (overflow > 0) {
      const dropped = get().toasts.slice(0, overflow)
      for (const toast of dropped) cancelTimer(toast.id)
      set({ toasts: get().toasts.slice(overflow) })
    }

    set({ toasts: [...get().toasts, record] })

    const duration = record.durationMs ?? DEFAULT_DURATION_MS
    // A non-positive duration pins the toast open — the only way to say "this
    // needs acknowledgement" without inventing a `sticky` flag.
    if (duration > 0) {
      timers.set(
        id,
        globalThis.setTimeout(() => {
          get().dismiss(id)
        }, duration),
      )
    }

    return id
  },

  dismiss(id) {
    cancelTimer(id)
    set({ toasts: get().toasts.filter((toast) => toast.id !== id) })
  },

  dismissAll() {
    for (const timer of timers.values()) globalThis.clearTimeout(timer)
    timers.clear()
    set({ toasts: [] })
  },
}))

const TONE_VARIANTS: Record<ToastTone, ToastVariant> = {
  info: 'default',
  success: 'success',
  warning: 'warning',
  error: 'destructive',
}

/**
 * Imperative façade over the store so non-React code — API error handling, the
 * session store, anything outside a component — can announce something without
 * a hook or a provider. A plain object, deliberately: a hook-shaped helper
 * would tempt callers into a rule-of-hooks trap outside components.
 */
export const toast = {
  info(title: string, description?: string): string {
    return useToastStore.getState().push({ title, description, variant: TONE_VARIANTS.info })
  },
  success(title: string, description?: string): string {
    return useToastStore.getState().push({ title, description, variant: TONE_VARIANTS.success })
  },
  warning(title: string, description?: string): string {
    return useToastStore.getState().push({ title, description, variant: TONE_VARIANTS.warning })
  },
  error(title: string, description?: string): string {
    return useToastStore.getState().push({ title, description, variant: TONE_VARIANTS.error })
  },
  /** Escape hatch for the cases the four tones cannot express — durations, actions. */
  custom(options: ToastOptions): string {
    return useToastStore.getState().push(options)
  },
}
