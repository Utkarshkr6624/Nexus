/**
 * The provenance label, rendered.
 *
 * The table it reads lives in `../provenance` rather than beside this component
 * because `react-refresh/only-export-components` forbids a module exporting
 * both, and because the table is plain data with no reason to re-render.
 *
 * **Each badge carries a word and an icon, never colour alone** — the same rule
 * the risk surface discharges in `severity-badge.tsx`. A badge whose only signal
 * was a hue is unreadable in dark mode, to a reader with a colour vision
 * deficiency, and to a screen reader, so the word is the claim and the icon only
 * reinforces it.
 */
import { Badge } from '@/components/ui/badge'
import { PROVENANCE_META } from '@/features/command-center/provenance'
import { cn } from '@/lib/utils'
import type { Provenance } from '@/features/command-center/priority'

export interface ProvenanceBadgeProps {
  provenance: Provenance
  className?: string
}

/**
 * `title` carries the longer sentence for a pointer user; the badge's own text
 * is the whole claim, so a screen reader announces "Model-derived" rather than
 * "sparkles".
 */
export function ProvenanceBadge({ provenance, className }: ProvenanceBadgeProps) {
  const meta = PROVENANCE_META[provenance]
  const Icon = meta.icon

  return (
    <Badge variant={meta.variant} className={cn('gap-1 font-normal', className)} title={meta.description}>
      <Icon aria-hidden="true" className="size-3" />
      {meta.label}
    </Badge>
  )
}
