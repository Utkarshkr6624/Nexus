/**
 * Typed wrappers over the Phase 7 Jarvis endpoints.
 *
 * Jarvis is a separate generative model (Qwen3-8B + a LoRA adapter) served by its
 * own process; NEXUS's intent classifier is unaffected by it. The backend owns every
 * guard: tool validation, confirmation of side effects and the crisis-response check.
 * This client only carries messages and renders what came back.
 *
 * None of these retries: a chat turn may have run a tool, and `confirm` is single-use.
 */
import { apiClient } from '@/lib/api-client'

export const JARVIS_ENDPOINTS = {
  status: '/jarvis/status',
  chat: '/jarvis/chat',
  confirm: '/jarvis/confirm',
  load: '/jarvis/model/load',
  unload: '/jarvis/model/unload',
} as const

/** Loading Qwen3-8B can take minutes; the load call only starts it and returns at once. */
const JARVIS_MODEL_SWITCH_TIMEOUT_MS = 90_000

export type JarvisModelState =
  | 'off'
  | 'loading'
  | 'ready'
  | 'busy'
  | 'unloading'
  | 'error'
  | 'external'

export interface JarvisLifecycle {
  state: JarvisModelState
  error: string | null
  on_demand: boolean
  owned: boolean
  idle_unload_minutes: number
  idle_seconds: number
  last_unload: Record<string, unknown> | null
}

/** Generation can take up to five model calls of 120 s each on the server. */
export const JARVIS_TURN_TIMEOUT_MS = 620_000

/** The server accepts at most this many messages of client-held history. */
export const JARVIS_MAX_MESSAGES = 40

export interface JarvisMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface JarvisToolStatus {
  name: string
  side_effect: string
  requires_confirmation: boolean
  available: boolean
  reason: string | null
}

export interface JarvisStatusRead {
  enabled: boolean
  model: { available: boolean; reason?: string; model_id?: string; adapter_sha256?: string }
  lifecycle?: JarvisLifecycle
  expected_adapter_sha256: string
  qualification: string
  tools: JarvisToolStatus[]
}

export interface JarvisTraceEntry {
  tool: string
  arguments: Record<string, unknown>
  status: string
  result: { ok: boolean; data?: unknown; error?: { code: string; message: string } } | null
}

export interface JarvisTurnRead {
  outcome: string
  text: string
  trace: JarvisTraceEntry[]
  proposal_id: string | null
  /** `unverified`: text arguments not found word for word in the user's request; check them before approving. */
  proposal: { name: string; arguments: Record<string, unknown>; unverified?: string[] }[] | null
  guards: Record<string, unknown>
  steps: number
  model: { adapter_sha256: string; qualification: string }
  nexus_classification: Record<string, unknown> | null
}

function timezone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone
  } catch {
    return undefined
  }
}

export function fetchJarvisStatus(signal?: AbortSignal): Promise<JarvisStatusRead> {
  return apiClient.get<JarvisStatusRead>(JARVIS_ENDPOINTS.status, { signal, auth: true })
}

export function sendJarvisChat(messages: JarvisMessage[]): Promise<JarvisTurnRead> {
  return apiClient.post<JarvisTurnRead>(
    JARVIS_ENDPOINTS.chat,
    { messages: messages.slice(-JARVIS_MAX_MESSAGES), timezone: timezone() },
    { auth: true, timeoutMs: JARVIS_TURN_TIMEOUT_MS },
  )
}

export function setJarvisModel(on: boolean): Promise<JarvisLifecycle> {
  return apiClient.post<JarvisLifecycle>(
    on ? JARVIS_ENDPOINTS.load : JARVIS_ENDPOINTS.unload,
    {},
    { auth: true, timeoutMs: JARVIS_MODEL_SWITCH_TIMEOUT_MS },
  )
}

export function confirmJarvisAction(proposalId: string, approve: boolean): Promise<JarvisTurnRead> {
  return apiClient.post<JarvisTurnRead>(
    JARVIS_ENDPOINTS.confirm,
    { proposal_id: proposalId, approve, timezone: timezone() },
    { auth: true, timeoutMs: JARVIS_TURN_TIMEOUT_MS },
  )
}
