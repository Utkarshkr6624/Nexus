/**
 * The note reader: rendered Markdown plus the one component that shows it.
 *
 * **`dangerouslySetInnerHTML` is unavoidable here** — the whole point of the
 * surface is showing formatted Markdown — and it is also the only place in the
 * app where user-authored text becomes HTML. The mitigation is that
 * {@link renderMarkdown} never passes source HTML through: every character of
 * user text is escaped before any tag is emitted, and the generated string is
 * sanitised again on the way out, so a note containing `<script>` or a
 * `javascript:` link renders as the literal text it is.
 *
 * The parser is deliberately small — headings, emphasis, code, lists,
 * checklists, blockquotes, rules, links. It is a Phase 5 surface, not a
 * CommonMark implementation, and every construct it does not understand is
 * rendered as plain text rather than guessed at.
 *
 * **The typography lives here, not in the pages.** A note, a concept and a
 * preview are one reading surface; three copies of this class list would be
 * three scales that drift apart the first time one of them is tuned.
 *
 * There is no `@tailwindcss/typography` installed, so the block styling is done
 * with descendant variants against the existing tokens.
 */
import { renderMarkdown } from '@/types/knowledge'
import { cn } from '@/lib/utils'

export interface MarkdownViewProps {
  /** Markdown source. `null`/empty renders nothing rather than an empty box. */
  content: string | null | undefined
  className?: string
}

export function MarkdownView({ content, className }: MarkdownViewProps) {
  const html = renderMarkdown(content)
  if (!html) return null

  return (
    <div
      className={cn(
        // Body 1.0625rem on a 1.75 line height; spacing *between* blocks rather
        // than inside them, and headings that step up rather than jump.
        'text-[1.0625rem] leading-[1.75] text-foreground',
        '[&_h1]:mt-10 [&_h1]:mb-3 [&_h1]:text-2xl [&_h1]:font-semibold [&_h1]:tracking-tight',
        '[&_h2]:mt-9 [&_h2]:mb-3 [&_h2]:text-xl [&_h2]:font-semibold [&_h2]:tracking-tight',
        '[&_h3]:mt-7 [&_h3]:mb-2 [&_h3]:text-lg [&_h3]:font-semibold',
        '[&_h4]:mt-6 [&_h4]:mb-2 [&_h4]:text-base [&_h4]:font-semibold',
        '[&_h5]:mt-5 [&_h5]:mb-2 [&_h5]:text-sm [&_h5]:font-semibold [&_h5]:uppercase [&_h5]:tracking-wide',
        '[&_h6]:mt-5 [&_h6]:mb-2 [&_h6]:text-sm [&_h6]:font-semibold [&_h6]:text-muted-foreground',
        '[&_p]:my-4 [&_p:first-of-type]:mt-0',
        '[&_ul]:my-4 [&_ul]:list-disc [&_ul]:space-y-1.5 [&_ul]:pl-6',
        '[&_ol]:my-4 [&_ol]:list-decimal [&_ol]:space-y-1.5 [&_ol]:pl-6',
        '[&_li]:pl-1 [&_li::marker]:text-muted-foreground',
        '[&_li_p]:my-0',
        '[&_input[type=checkbox]]:mr-2 [&_input[type=checkbox]]:align-text-top',
        '[&_.task-list-item-complete]:text-muted-foreground [&_.task-list-item-complete]:line-through',
        '[&_blockquote]:my-5 [&_blockquote]:border-l-2 [&_blockquote]:border-primary/40 [&_blockquote]:pl-4 [&_blockquote]:italic [&_blockquote]:text-muted-foreground',
        '[&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[0.9em]',
        '[&_pre]:my-5 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-border [&_pre]:bg-muted/60 [&_pre]:p-4',
        '[&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-sm [&_pre_code]:leading-relaxed',
        '[&_a]:text-primary [&_a]:underline [&_a]:underline-offset-2 [&_a]:decoration-primary/40 hover:[&_a]:decoration-primary',
        '[&_hr]:my-8 [&_hr]:border-border',
        '[&_strong]:font-semibold',
        '[&_del]:text-muted-foreground',
        className,
      )}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}