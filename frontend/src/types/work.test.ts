import { describe, expect, it } from 'vitest'

import { WORK_EVENT_META, workEventMeta } from '@/types/work'
import type { StatusTone, WorkEventType } from '@/types/work'

/**
 * The activity vocabulary, which is the one place on this surface where the
 * client keeps its own copy of a server-owned list.
 *
 * `WorkEventType` and `WORK_EVENT_META` are both derived from
 * `ActivityEvent`, so the guard between them is partly a compile-time one:
 * `Record<WorkEventType, StatusMeta>` rejects a map that is missing a member,
 * and the `Expect` below rejects one that has a key the union does not name.
 * Neither notices the number 63, though — a union that quietly lost members
 * still typechecks against a map that lost the same ones. The runtime count is
 * what catches that, and it is a count rather than a transcription on purpose:
 * copy the enum's list out by hand and this file drifts from the backend the
 * first time either side changes.
 */

/** The map's keys, as the union would spell them. */
const META_KEYS = Object.keys(WORK_EVENT_META) as WorkEventType[]

type IsAssignable<From, To> = [From] extends [To] ? true : false

/** Fails to compile unless `T` is exactly `true`. */
type Expect<T extends true> = T

/** The map may not name an event the type does not, which is the drift that
 *  renders as an empty chip on a row nobody can explain. */
export type _MapKeysAreEventTypes = Expect<
  IsAssignable<(typeof META_KEYS)[number], WorkEventType>
>

const TONES: readonly StatusTone[] = ['neutral', 'info', 'success', 'warning', 'danger']

/**
 * The members the Phase 3 vocabulary never named. Every one of them is a real
 * row on a real account, and the sixteen-member map indexed by `event_type`
 * returned `undefined` for each — which is what crashed `/dashboard`.
 */
const OUTSIDE_THE_ORIGINAL_SIXTEEN: WorkEventType[] = [
  'note_created',
  'risk_detected',
  'recommendation_accepted',
  'repository_scanned',
  'commit_detected',
  'learning_session_recorded',
  'career_evidence_added',
]

describe('work event vocabulary', () => {
  it('covers every ActivityEvent member', () => {
    // 63 as of the enum this file was written against. A backend that grows the
    // enum and not this map is exactly the bug this count is here to catch, so
    // the fix is to add the entry — not to edit the number.
    expect(META_KEYS).toHaveLength(63)
    for (const eventType of OUTSIDE_THE_ORIGINAL_SIXTEEN) {
      expect(WORK_EVENT_META[eventType]).toBeDefined()
    }
  })

  it('gives every event a label, an icon, a tone and a description', () => {
    for (const eventType of META_KEYS) {
      const meta = WORK_EVENT_META[eventType]
      expect(meta.label, eventType).not.toBe('')
      expect(meta.icon, eventType).toBeTruthy()
      expect(TONES, eventType).toContain(meta.tone)
      expect(meta.description, eventType).not.toBe('')
    }
  })

  it('never puts the machine spelling in front of a reader', () => {
    for (const eventType of META_KEYS) {
      expect(WORK_EVENT_META[eventType].label, eventType).not.toContain('_')
    }
  })

  it('answers a known event type with its mapped presentation', () => {
    expect(workEventMeta('risk_detected')).toBe(WORK_EVENT_META.risk_detected)
  })

  it('degrades an unknown event type to a derived label and the fallback presentation', () => {
    // The column is unconstrained free text, so this is a value the server can
    // send without any code changing here. It must cost the row its wording —
    // never the route.
    const meta = workEventMeta('constellation_aligned')
    expect(meta.label).toBe('Constellation aligned')
    expect(meta.icon).toBeTruthy()
    expect(meta.tone).toBe('neutral')

    // Nothing to describe: the fallback says the event was recorded by an
    // unfamiliar build rather than inventing a claim about what happened.
    expect(meta.description).toMatch(/not by a version this build knows/)
  })

  it('falls back to a generic label when the value carries no words', () => {
    for (const empty of ['', '   ', '_', '-']) {
      expect(workEventMeta(empty).label).toBe('Unrecognised event')
    }
  })
})