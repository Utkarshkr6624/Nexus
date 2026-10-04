/**
 * What each provenance label is called and why it exists.
 *
 * **This is a `.ts` file with no JSX in it, and that is deliberate.** The project
 * runs `react-refresh/only-export-components`, which reports any module that
 * exports a component *and* a non-component; `allowConstantExport` exempts
 * literals and template expressions but not a table of metadata. The same
 * problem and the same remedy are already documented in
 * `features/risk/components/risk-vocabulary.tsx`.
 *
 * The three labels are not decoration. A count of rows, a score this browser
 * computed and a label a classifier guessed are three different kinds of claim,
 * and a reader who cannot tell them apart will believe the third is a
 * measurement.
 */
import { Calculator, Database, Sparkles } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import type { Provenance } from '@/features/command-center/priority'

export interface ProvenanceMeta {
  label: string
  icon: LucideIcon
  variant: 'secondary' | 'outline' | 'default'
  /** One sentence, rendered as the badge's title and readable by pointer users. */
  description: string
}

export const PROVENANCE_META: Record<Provenance, ProvenanceMeta> = {
  measured: {
    label: 'Measured',
    icon: Database,
    variant: 'secondary',
    description: 'Counted from stored records by a real endpoint.',
  },
  calculated: {
    label: 'Calculated',
    icon: Calculator,
    variant: 'outline',
    description:
      'A deterministic score computed in your browser from measured inputs. It is a ' +
      'ranking, not a measurement, and not a judgement about you.',
  },
  'model-derived': {
    label: 'Model-derived',
    icon: Sparkles,
    variant: 'default',
    description:
      'Produced by the intent classifier — a prediction about one sentence, never a ' +
      'measurement of your work.',
  },
}
