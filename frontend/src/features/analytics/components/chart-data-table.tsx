import { useId, useState } from 'react'
import { ChevronDown } from 'lucide-react'

import { cn } from '@/lib/utils'

/**
 * The numbers behind a chart, for anyone the drawing does not serve.
 *
 * **One fallback for every chart on this surface.** A chart is a picture; a
 * picture is unreadable with a screen reader, at 200% zoom, in print, or to
 * anyone who simply wants the figure. So every chart on this page carries a
 * focusable disclosure over the same rows it plots, and the disclosure is the
 * answer to "what does this chart say" for anyone who cannot see it.
 *
 * **Collapsed until asked for.** The table is only in the DOM once the button is
 * pressed: a page with four charts would otherwise ship four hundred table rows
 * that no one reads, and a reader who never opens it pays for it on every window
 * change. The button is a real `<button>` with `aria-expanded`/`aria-controls`,
 * so it is reachable and operable from the keyboard.
 */
export interface ChartDataTableProps {
  /** Names the table for a screen reader: "Tasks by status". */
  caption: string
  /** The first column's heading — what a row is: "Day", "Status", "Project". */
  rowHeading: string
  /** One heading per series, in the order the values appear. */
  columns: readonly string[]
  rows: ReadonlyArray<ReadonlyArray<string>>
  /** Overrides the disclosure's label. */
  label?: string
  className?: string
}

export function ChartDataTable({
  caption,
  rowHeading,
  columns,
  rows,
  label = 'Show the numbers',
  className,
}: ChartDataTableProps) {
  const [open, setOpen] = useState(false)
  const tableId = `chart-table-${useId().replace(/:/g, '')}`

  if (rows.length === 0) return null

  return (
    <div className={cn('mt-3', className)}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={tableId}
        onClick={() => setOpen((value) => !value)}
        className="inline-flex min-h-11 items-center gap-1.5 rounded-md px-1 text-xs font-medium text-muted-foreground underline-offset-4 hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
      >
        <ChevronDown
          aria-hidden="true"
          className={cn('size-3.5 shrink-0 transition-transform', open && 'rotate-180')}
        />
        {label}
      </button>

      {open && (
        <div id={tableId} className="mt-2 max-h-64 overflow-auto rounded-md border border-border">
          <table className="w-full text-xs">
            <caption className="sr-only">{caption}</caption>
            <thead>
              <tr className="border-b border-border">
                <th
                  scope="col"
                  className="px-3 py-2 text-left font-medium text-muted-foreground"
                >
                  {rowHeading}
                </th>
                {columns.map((column) => (
                  <th
                    key={column}
                    scope="col"
                    className="px-3 py-2 text-right font-medium text-muted-foreground"
                  >
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((cells, rowIndex) => (
                <tr
                  // Two categories can share a name, so the row index is part of
                  // the key rather than the label alone.
                  key={`${cells[0] ?? ''}-${rowIndex}`}
                  className="border-b border-border last:border-b-0"
                >
                  {cells.map((cell, index) =>
                    index === 0 ? (
                      <th
                        key={`${cell}-${index}`}
                        scope="row"
                        className="px-3 py-1.5 text-left font-normal text-foreground"
                      >
                        {cell}
                      </th>
                    ) : (
                      <td
                        key={`${cell}-${index}`}
                        className="px-3 py-1.5 text-right tabular-nums text-muted-foreground"
                      >
                        {cell}
                      </td>
                    ),
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}