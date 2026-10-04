import * as React from 'react'

import { cn } from '@/lib/utils'

export interface InputProps extends React.ComponentPropsWithoutRef<'input'> {
  /** Paints the destructive border and focus ring after a failed validation. */
  error?: boolean
  /** Paints the success border once a field validates. */
  success?: boolean
  /** Trailing control, e.g. the show/hide-password button. */
  endAdornment?: React.ReactNode
}

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  (
    { className, type = 'text', error = false, success = false, endAdornment, ...props },
    ref,
  ) => {
    const input = (
      <input
        type={type}
        ref={ref}
        aria-invalid={error || undefined}
        className={cn(
          'flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors',
          'placeholder:text-muted-foreground',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
          'disabled:cursor-not-allowed disabled:opacity-50',
          'file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground',
          // These are last so they win over the neutral border/ring tokens; a
          // caller can still override either through `className`.
          error && 'border-destructive focus-visible:ring-destructive',
          success && !error && 'border-success focus-visible:ring-success',
          // Reserve room so the value never runs underneath the adornment.
          endAdornment && 'pr-10',
          className,
        )}
        {...props}
      />
    )

    // The wrapper is unconditional. Returning a bare `<input>` when there is no
    // adornment saved one `<span>`, and cost something far more expensive: React
    // unmounts and remounts a node when its tree shape changes, so any caller
    // that toggled `endAdornment` — a search box whose clear button appears on
    // the first keystroke, say — dropped focus and swallowed the character that
    // triggered the toggle. One always-present span is not worth that class of
    // bug.
    return (
      <span className="relative block w-full">
        {input}
        {endAdornment && (
          <span className="absolute inset-y-0 right-0 flex items-center pr-2">{endAdornment}</span>
        )}
      </span>
    )
  },
)
Input.displayName = 'Input'

export { Input }
