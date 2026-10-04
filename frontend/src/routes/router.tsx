import { Navigate, Outlet, createBrowserRouter } from 'react-router-dom'

import { RouteErrorBoundary } from '@/components/feedback/app-error-boundary'
import { AppLayout } from '@/routes/app-layout'
import { AuthRouteBoundary } from '@/routes/auth-boundary'
import { RequireAnonymous } from '@/routes/guards'
import {
  AnalyticsPage,
  AssistantPage,
  CareerPage,
  CommandCenterPage,
  DashboardPage,
  DeveloperPage,
  DeveloperRepositoryPage,
  ExperimentsPage,
  ForgotPasswordPage,
  ConceptDetailPage,
  KnowledgePage,
  NoteDetailPage,
  LearningPage,
  LoginPage,
  NotFoundPage,
  MonthPage,
  PlannerPage,
  ProjectDetailPage,
  ProjectsPage,
  RecommendationsPage,
  RegisterPage,
  ResetPasswordPage,
  RiskCenterPage,
  SearchPage,
  SettingsPage,
  TasksPage,
} from '@/routes/lazy-pages'

// Paths mirror `@/features/modules/catalog`, which owns the navigation.
export const router = createBrowserRouter([
  // The product opens on the Command Center, not on an empty index: it is the
  // one surface that answers what needs attention before anything else does.
  { path: '/', element: <Navigate to="/command-center" replace /> },

  {
    element: <AppLayout />,
    errorElement: <RouteErrorBoundary />,
    children: [
      { path: '/command-center', element: <CommandCenterPage /> },
      { path: '/dashboard', element: <DashboardPage /> },
      { path: '/projects', element: <ProjectsPage /> },
      { path: '/projects/:projectId', element: <ProjectDetailPage /> },
      { path: '/tasks', element: <TasksPage /> },
      { path: '/planner', element: <PlannerPage /> },
      { path: '/planner-month', element: <MonthPage /> },
      { path: '/knowledge', element: <KnowledgePage /> },
      { path: '/knowledge/notes/:noteId', element: <NoteDetailPage /> },
      { path: '/knowledge/concepts/:conceptId', element: <ConceptDetailPage /> },
      { path: '/search', element: <SearchPage /> },
      { path: '/analytics', element: <AnalyticsPage /> },
      { path: '/risks', element: <RiskCenterPage /> },
      { path: '/recommendations', element: <RecommendationsPage /> },
      { path: '/developer', element: <DeveloperPage /> },
      { path: '/developer/:repositoryId', element: <DeveloperRepositoryPage /> },
      { path: '/learning', element: <LearningPage /> },
      { path: '/career', element: <CareerPage /> },
      { path: '/assistant', element: <AssistantPage /> },
      { path: '/experiments', element: <ExperimentsPage /> },
      { path: '/settings', element: <SettingsPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },

  {
    element: (
      <RequireAnonymous>
        <AuthRouteBoundary>
          <Outlet />
        </AuthRouteBoundary>
      </RequireAnonymous>
    ),
    errorElement: <RouteErrorBoundary />,
    children: [
      { path: '/login', element: <LoginPage /> },
      { path: '/register', element: <RegisterPage /> },
      // Password recovery lives on the anonymous branch too: there is no
      // session to authenticate with, and a signed-in user has no reason to see
      // either screen.
      //
      // `/reset-password` is reached from the emailed link, which carries the
      // reset token as a `?token=` query parameter. The route table stays token
      // -free on purpose — `ResetPasswordPage` reads it with `useSearchParams`
      // and is the single place that does, so a token never reaches a loader or
      // an error message.
      { path: '/forgot-password', element: <ForgotPasswordPage /> },
      { path: '/reset-password', element: <ResetPasswordPage /> },
    ],
  },
])