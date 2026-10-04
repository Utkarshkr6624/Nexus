/**
 * Focus containment and background inertness for the palette.
 *
 * The palette is modal: it covers the page, it takes the keyboard, and closing
 * it must hand focus back exactly where it came from. Two mechanisms do that,
 * and both are deliberately **imperative** rather than declarative. The palette
 * portals onto `document.body`, so the elements it has to neutralise belong to
 * somebody else's tree — the app shell's root, the skip link — and a React prop
 * on them is not something this component may render.
 *
 * The focus-trap key handling mirrors `sidebar.tsx`: a `keydown` listener on
 * `document` that only acts on `Tab`, so Tab from the middle of the palette
 * behaves exactly as the browser intends and only the two edges are redirected.
 */

/**
 * Inputs are in here because the palette's own list of controls is mostly
 * inputs. `sidebar.tsx` can get away with links and buttons alone.
 */
const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled]):not([tabindex="-1"])',
  'input:not([disabled]):not([tabindex="-1"])',
  'select:not([disabled]):not([tabindex="-1"])',
  'textarea:not([disabled]):not([tabindex="-1"])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

export function focusableWithin(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (element) => window.getComputedStyle(element).display !== 'none',
  )
}

/**
 * Cycles `Tab` and `Shift+Tab` inside `root`.
 *
 * Returns a teardown function; there is nothing to call otherwise. When the
 * palette holds no focusable control at all — before its first paint, or with
 * the search view replaced mid-render — the listener does nothing rather than
 * trapping the user somewhere they cannot leave with the keyboard.
 */
export function trapFocus(root: HTMLElement): () => void {
  function onKeyDown(event: KeyboardEvent) {
    if (event.key !== 'Tab') return

    const items = focusableWithin(root)
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
}

/**
 * Takes everything outside `overlay` out of the page while the palette is open.
 *
 * `inert` rather than `aria-hidden`: it removes the subtree from the tab order
 * *and* from the accessibility tree in one attribute, which is what a modal
 * overlay actually needs — a background that is merely hidden from a screen
 * reader is still reachable with a keyboard and still announces itself.
 *
 * The attribute is set directly rather than through the IDL property because
 * that is the form React itself emits for `inert`, and because it is the only
 * one that survives being asserted in a jsdom test. Elements that were already
 * inert — the app shell parks its sidebar that way on small viewports — are left
 * alone and are not marked for restore, so closing the palette can never hand
 * focus back into something that was deliberately unreachable.
 *
 * Returns a teardown function that restores exactly what it changed.
 */
export function makeBackgroundInert(overlay: HTMLElement): () => void {
  const touched: HTMLElement[] = []

  for (const child of Array.from(document.body.children)) {
    const element = child as HTMLElement
    if (element === overlay || element.contains(overlay)) continue
    if (element.hasAttribute('inert')) continue
    element.setAttribute('inert', '')
    touched.push(element)
  }

  return () => {
    for (const element of touched) element.removeAttribute('inert')
  }
}
