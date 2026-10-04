import { useNavigate } from 'react-router-dom'
import { ChevronRight, LogOut, Settings, ShieldCheck, User } from 'lucide-react'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Spinner } from '@/components/ui/spinner'
import { selectDisplayName, selectInitials, useAuthStore } from '@/stores/auth-store'
import { toast } from '@/stores/toast-store'

/**
 * Each item targets the settings section that actually serves it. The section
 * lives in `?tab=`, so these are real destinations rather than three labels
 * for the same scroll position — and each one is shareable and back-navigable.
 */
const ACCOUNT_ROUTES = {
  profile: '/settings?tab=profile',
  preferences: '/settings?tab=preferences',
  security: '/settings?tab=security',
} as const

/** Chevron marking an item that navigates, so the destination is never a guess. */
function NavigateHint() {
  return <ChevronRight aria-hidden="true" className="ml-auto text-muted-foreground" />
}

function ProfileAvatar({ className }: { className?: string }) {
  const user = useAuthStore((state) => state.user)
  const initials = selectInitials(user)

  return (
    <Avatar className={className}>
      {user?.avatar_url ? <AvatarImage src={user.avatar_url} alt="" /> : null}
      <AvatarFallback>{initials}</AvatarFallback>
    </Avatar>
  )
}

export function UserMenu() {
  const navigate = useNavigate()
  const user = useAuthStore((state) => state.user)
  const logout = useAuthStore((state) => state.logout)
  const pending = useAuthStore((state) => state.pending)

  const displayName = selectDisplayName(user)
  const handle = user ? (user.username ? `@${user.username}` : user.email) : 'Not signed in'

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={`Account menu for ${displayName}`}
          className="flex items-center gap-2 rounded-md py-1 pl-1 pr-1.5 transition-colors duration-150 ease-out hover:bg-accent"
        >
          <ProfileAvatar className="size-7" />
          <span className="hidden min-w-0 leading-tight md:block">
            <span className="block max-w-32 truncate text-sm font-medium text-foreground">
              {displayName}
            </span>
            <span className="block max-w-32 truncate text-xs text-muted-foreground">{handle}</span>
          </span>
        </button>
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel className="px-2 py-2.5">
          <span className="flex items-center gap-2.5">
            <ProfileAvatar className="size-9" />
            <span className="flex min-w-0 flex-col leading-tight">
              <span className="truncate text-sm font-semibold text-foreground">{displayName}</span>
              <span className="truncate text-xs font-normal text-muted-foreground">{handle}</span>
            </span>
          </span>
        </DropdownMenuLabel>

        <DropdownMenuSeparator />

        <DropdownMenuItem onSelect={() => navigate(ACCOUNT_ROUTES.profile)}>
          <User aria-hidden="true" />
          Profile
          <NavigateHint />
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => navigate(ACCOUNT_ROUTES.preferences)}>
          <Settings aria-hidden="true" />
          Settings
          <NavigateHint />
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => navigate(ACCOUNT_ROUTES.security)}>
          <ShieldCheck aria-hidden="true" />
          Security
          <NavigateHint />
        </DropdownMenuItem>

        <DropdownMenuSeparator />

        <DropdownMenuItem
          variant="destructive"
          disabled={pending}
          onSelect={() => {
            // Navigate only once the local session is gone: navigating while
            // `status` is still `authenticated` bounces off the anonymous guard
            // and drops the user back on the dashboard. The toast is queued
            // before the redirect because the toaster lives above the router and
            // survives it, whereas a component-level one would not.
            void logout()
              .then(() => toast.success('Signed out', 'This device no longer holds a session.'))
              // The local session is gone either way, so a teardown that threw
              // must not cost the redirect — a user left on a signed-in shell
              // the backend has already forgotten is the worse failure.
              .catch(() => toast.error('Signed out', 'The sign-out could not be completed cleanly.'))
              .then(() => navigate('/login', { replace: true }))
          }}
        >
          {pending ? <Spinner size="sm" /> : <LogOut aria-hidden="true" />}
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
