/**
 * Where an accepted routing decision actually lives in the product.
 *
 * The classifier answers with an API destination (`api/v1/projects`), because
 * that is what it was trained to emit and what the backend validates it against.
 * The browser has no such path — a user cannot be sent to `api/v1/projects` —
 * so without this map an accepted turn names a destination and leaves the reader
 * with no way to reach the thing it named. That is the whole of what this module
 * is for: it turns the announcement into a link, and nothing more.
 *
 * The routes come from the module catalog rather than from literals here, so the
 * label shown to the user is the same label the sidebar shows and there is one
 * place to change if a module moves.
 */
import { MODULES } from '@/features/modules/catalog'

/**
 * API destination → frontend route, for the destinations the taxonomy can emit
 * that have a page to land on.
 *
 * `api/v1/users` is absent on purpose: `account_admin` is a real intent and the
 * backend names a real destination, but the product ships no account page for a
 * user to be sent to. Guessing one — `/settings`, say — would produce a button
 * that navigates somewhere the destination does not describe. A turn with no
 * entry offers no button, which is the honest outcome.
 *
 * `large-model:unavailable` and `abstain` are not destinations at all and never
 * appear on an `accepted` decision, so they are not mapped either.
 */
const DESTINATION_ROUTES: Record<string, string> = {
  'api/v1/analytics': '/analytics',
  'api/v1/career': '/career',
  'api/v1/developer': '/developer',
  'api/v1/knowledge': '/knowledge',
  'api/v1/learning': '/learning',
  'api/v1/planner': '/planner',
  'api/v1/projects': '/projects',
  'api/v1/risks': '/risks',
  'api/v1/tasks': '/tasks',
}

export interface DestinationLink {
  /** Frontend route to navigate to. */
  to: string
  /** The module's own name, as the sidebar shows it. */
  label: string
}

/**
 * The link for a destination, or `null` when the destination names no page.
 *
 * A destination with no catalog entry is treated the same as an unmapped one:
 * the button is withheld rather than pointed at a route this table does not
 * vouch for.
 */
export function destinationLink(destination: string | undefined): DestinationLink | null {
  if (!destination) return null
  const to = DESTINATION_ROUTES[destination]
  if (!to) return null
  const module = MODULES.find((candidate) => candidate.to === to)
  if (!module) return null
  return { to, label: module.label }
}