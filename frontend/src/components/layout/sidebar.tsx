import { forwardRef, useEffect, useRef } from 'react'
import type { ComponentPropsWithoutRef, CSSProperties } from 'react'
import { NavLink } from 'react-router-dom'
import { ChevronsLeft, PanelLeft } from 'lucide-react'

import { Brand } from '@/components/brand/logo'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { NAV_GROUPS, SETTINGS_MODULE } from '@/features/modules/catalog'
import type { ModuleDefinition } from '@/features/modules/catalog'
import { cn } from '@/lib/utils'

export const SIDEBAR_WIDTH_EXPANDED = '260px'
export const SIDEBAR_WIDTH_COLLAPSED = '68px'

const FOCUSABLE_SELECTOR = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'

export interface AppSidebarProps {
  collapsed: boolean
  mobileOpen: boolean
  /** True while the sheet is parked off-screen below `lg`. */
  inert?: boolean
  onCollapseToggle: () => void
  onNavigate: () => void
}

function focusableWithin(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (element) => window.getComputedStyle(element).display !== 'none',
  )
}

/*
 * Grouping. The registry already clusters the modules, so the rail adds one
 * decision on top of it rather than inventing a second taxonomy: a group of one
 * is a home row, not a section. Since the Command Center landed, no group is a
 * single item — the two overview surfaces (the Command Center and the
 * Dashboard) answer the same question at different depths, and separating them
 * by a rule the reader cannot act on would be pretending they are different
 * kinds of thing. All five groups therefore carry headings. Five headings across
 * thirteen destinations is about as much as a rail can hold before it stops
 * being scannable, and each one earns its place by naming a different kind of
 * work — orienting yourself, doing it, reasoning over it, growing at it, and
 * extending the platform itself.
 */
const HOME_GROUP = NAV_GROUPS.find((group) => group.items.length === 1)
const HEADING_GROUPS = NAV_GROUPS.filter((group) => group.items.length > 1)

interface NavItemProps
  extends Omit<ComponentPropsWithoutRef<'a'>, 'children' | 'className'> {
  item: ModuleDefinition
  collapsed: boolean
  onNavigate: () => void
}

/**
 * One destination. The row states come from `.app-nav-row`, which keys the
 * active treatment off the `aria-current="page"` NavLink sets, so the fill, the
 * label weight and the hover are one definition rather than three conditions
 * kept in sync here. Only the leading indicator and the collapsed layout are
 * component concerns.
 *
 * `ref` and the remaining anchor props are forwarded because the collapsed
 * variant wraps this element in a `TooltipTrigger asChild`: Radix's Slot
 * attaches its pointer and focus handlers to whatever it clones, and an element
 * that swallows them leaves a tooltip that never opens.
 */
const NavItem = forwardRef<HTMLAnchorElement, NavItemProps>(function NavItem(
  { item, collapsed, onNavigate, ...rest },
  ref,
) {
  const Icon = item.icon

  return (
    <NavLink
      {...rest}
      ref={ref}
      to={item.to}
      onClick={onNavigate}
      // The collapsed rail hides the label, which would leave an icon-only link
      // with no accessible name. The name is stated explicitly so it survives
      // both layouts and matches the visible text in the expanded one.
      aria-label={item.label}
      className={cn('app-nav-row relative', collapsed && 'justify-center px-0')}
    >
      {({ isActive }) => (
        <>
          <span
            aria-hidden="true"
            className={cn(
              'absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-full bg-primary',
              'transition-opacity duration-150 ease-out',
              isActive ? 'opacity-100' : 'opacity-0',
            )}
          />
          <Icon aria-hidden="true" className={cn('size-4 shrink-0', isActive && 'text-primary')} />
          {!collapsed && <span className="truncate">{item.label}</span>}
        </>
      )}
    </NavLink>
  )
})

/**
 * Collapsed rows lose their label, so the label moves into a tooltip. Radix
 * opens it on pointer entry *and* on keyboard focus, which is the only way a
 * sighted keyboard user can read an icon-only rail.
 */
function CollapsibleNavItem({ item, collapsed, onNavigate }: NavItemProps) {
  const row = <NavItem item={item} collapsed={collapsed} onNavigate={onNavigate} />
  if (!collapsed) return row

  return (
    <Tooltip>
      <TooltipTrigger asChild>{row}</TooltipTrigger>
      <TooltipContent side="right">{item.label}</TooltipContent>
    </Tooltip>
  )
}

function NavGroupHeading({ label, collapsed }: { label: string; collapsed: boolean }) {
  if (collapsed) {
    return <div aria-hidden="true" className="mx-3 mb-1.5 h-px bg-sidebar-border first:hidden" />
  }

  return (
    <h2 className="flex h-5 items-center px-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-sidebar-muted">
      {label}
    </h2>
  )
}

export function AppSidebar({
  collapsed,
  mobileOpen,
  inert = false,
  onCollapseToggle,
  onNavigate,
}: AppSidebarProps) {
  const rootRef = useRef<HTMLDivElement>(null)

  const style = {
    '--sidebar-width': collapsed ? SIDEBAR_WIDTH_COLLAPSED : SIDEBAR_WIDTH_EXPANDED,
  } as CSSProperties

  // An open sheet covers the application, so it behaves modally: focus starts on
  // the first destination and Tab stays inside until the sheet closes.
  useEffect(() => {
    if (!mobileOpen) return undefined
    const root = rootRef.current
    if (!root) return undefined

    root.querySelector<HTMLElement>('nav a[href]')?.focus()

    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== 'Tab' || !root) return

      const items = focusableWithin(root)
      if (items.length === 0) return

      const first = items[0]
      const last = items[items.length - 1]
      if (!first || !last) return
      const active = document.activeElement

      if (event.shiftKey && (active === first || !root.contains(active))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [mobileOpen])

  return (
    <div
      ref={rootRef}
      inert={inert}
      style={style}
      className={cn(
        // The rail is chrome, not content: it takes the --sidebar plane so it
        // reads as a distinct surface from the canvas in both themes.
        'fixed inset-y-0 left-0 z-40 flex w-[260px] flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground',
        'transition-[width,transform] duration-200 ease-out',
        'lg:w-[var(--sidebar-width)]',
        // No translate class while the sheet is open, and that is load-bearing:
        // the backdrop below is `position: fixed`, and a transformed ancestor
        // becomes its containing block, which would size the backdrop to the
        // rail instead of the screen. `transform: none` still transitions from
        // `-translate-x-full`, so the slide-in is unaffected.
        mobileOpen ? 'shadow-2xl' : '-translate-x-full lg:translate-x-0',
      )}
    >
      {/* h-14 matches the top bar, so the two rails line up on the first row. */}
      <div
        className={cn(
          'flex h-14 shrink-0 items-center border-b border-sidebar-border',
          collapsed ? 'justify-center px-2' : 'justify-between px-4',
        )}
      >
        <Brand markOnly={collapsed} />
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className={cn(
            'hidden size-8 shrink-0 text-sidebar-muted lg:inline-flex',
            'hover:bg-sidebar-accent hover:text-sidebar-accent-foreground',
          )}
          onClick={onCollapseToggle}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <PanelLeft aria-hidden="true" /> : <ChevronsLeft aria-hidden="true" />}
        </Button>
      </div>

      <ScrollArea className="flex-1">
        <nav className="flex flex-col gap-6 px-3 py-4" aria-label="Primary">
          {HOME_GROUP && (
            <div className="flex flex-col gap-1">
              {HOME_GROUP.items.map((item) => (
                <CollapsibleNavItem
                  key={item.to}
                  item={item}
                  collapsed={collapsed}
                  onNavigate={onNavigate}
                />
              ))}
            </div>
          )}

          {HEADING_GROUPS.map((group) => (
            <div key={group.id} className="flex flex-col gap-1">
              <NavGroupHeading label={group.label} collapsed={collapsed} />
              {group.items.map((item) => (
                <CollapsibleNavItem
                  key={item.to}
                  item={item}
                  collapsed={collapsed}
                  onNavigate={onNavigate}
                />
              ))}
            </div>
          ))}
        </nav>
      </ScrollArea>

      <div className="shrink-0 border-t border-sidebar-border p-2">
        <CollapsibleNavItem
          item={SETTINGS_MODULE}
          collapsed={collapsed}
          onNavigate={onNavigate}
        />
        {!collapsed && (
          <p className="flex items-center gap-2 px-3 pt-2.5 text-[11px] text-sidebar-muted">
            <span aria-hidden="true" className="size-1.5 shrink-0 rounded-full bg-primary/70" />
            Local-first · your data stays here
          </p>
        )}
      </div>

      {/*
       * The dismiss control lives inside the rail, not beside it. It is a
       * button and therefore the last tabbable in the root, so the trap's cycle
       * ends on it: Tab reaches "Close navigation" and Shift+Tab reaches it
       * from the first destination. As a sibling it was unreachable — the trap
       * wrapped from the last destination straight back to the first, leaving
       * Escape as the only keyboard way out and nothing on screen to say so
       * (WCAG 2.1.1).
       *
       * It is inset by the rail's own width (`left-[260px]`, matching the
       * `w-[260px]` above, which is what applies below `lg`) so it paints over
       * the page only, exactly as it did when it was a sibling. Dismissing the
       * sheet is the same action as navigating away from it, so both call
       * `onNavigate`.
       */}
      {mobileOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={onNavigate}
          className="fixed inset-y-0 left-[260px] right-0 z-30 animate-in fade-in-0 bg-foreground/20 backdrop-blur-[1px] duration-150 ease-out lg:hidden"
        />
      )}
    </div>
  )
}
