/**
 * Conversation state for the voice assistant — and nothing else.
 *
 * The split is deliberate. This store holds what the *user said*; the live
 * lifecycle (listening, processing, speaking) belongs to
 * `use-voice-assistant`, which owns it as component state. Keeping them apart
 * means the microphone has one owner and the transcript has another, and a
 * conversation can be rendered without a microphone ever being opened.
 *
 * **No persistence.** There is no `persist` middleware here and there should not
 * be one added casually. A transcript is the user speaking into their machine —
 * meeting notes, a task they dictated, a half-formed thought — and writing it to
 * `localStorage` would leave it readable by anything that can run script on this
 * origin, outliving the tab, and surviving a sign-out. NEXO's own retention
 * story puts records on the backend behind a session; browser storage is not a
 * substitute for that. The conversation lives as long as the panel is open and
 * no longer.
 *
 * **Bounded.** `MAX_CONVERSATION_TURNS` is enforced on write, not on read, so a
 * long session cannot grow without limit and a reader never has to truncate.
 */
import { create } from 'zustand'

import { MAX_CONVERSATION_TURNS, type VoiceContext, type VoiceTurn } from './types'
import type { IntentName } from '@/types/ml'

/**
 * Where the last turn that actually routed landed, and where it was asked from.
 *
 * This is the whole of NEXO's conversational memory, and it exists for one
 * reason: the classifier is stateless. When a turn comes back `uncertain` the
 * assistant can only offer "did you mean the thing you just asked for again?",
 * which means it has to have kept the previous answer. It cannot instead ask
 * the model to remember, because there is no model to ask.
 */
export interface AcceptedDestination {
  /** The intent the classifier named. */
  intent: IntentName
  /** Class name of the service, e.g. `TaskService`. */
  service: string
  /** The call that starts the work, e.g. `TaskService.list`. */
  entrypoint: string
  /** The router path behind it, e.g. `api/v1/tasks`. */
  destination: string
  /** The route the utterance was asked from, and what had been accepted before. */
  context: VoiceContext
}

interface AssistantState {
  turns: VoiceTurn[]
  accepted: AcceptedDestination | null
  addTurn: (turn: VoiceTurn) => void
  recordAccepted: (accepted: AcceptedDestination) => void
  clearTurns: () => void
}

export const useAssistantStore = create<AssistantState>()((set) => ({
  turns: [],
  accepted: null,

  addTurn: (turn) =>
    set((state) => ({
      // Appended, never spliced in place: the previous array is the one the
      // transcript component is holding, and mutating it would render a new
      // history under the same reference.
      turns: [...state.turns, turn].slice(-MAX_CONVERSATION_TURNS),
    })),

  recordAccepted: (accepted) => set({ accepted }),

  // `accepted` is cleared with the turns, not left behind: a suggestion chip
  // pointing at a destination from a conversation the user just deleted is a
  // claim about something that no longer exists.
  clearTurns: () => set({ turns: [], accepted: null }),
}))

/**
 * Resets every conversation fact. Tests call this between cases; the app calls
 * `clearTurns` through the assistant's own action.
 */
export function resetAssistantStore(): void {
  useAssistantStore.getState().clearTurns()
}