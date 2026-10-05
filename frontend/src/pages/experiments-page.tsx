import { Info, Layers } from 'lucide-react'

import { EmptyState } from '@/components/feedback/empty-state'
import { PageHeader } from '@/components/feedback/page-header'
import { Alert } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { NAV_GROUPS, getModule } from '@/features/modules/catalog'

/**
 * The Experiments route.
 *
 * Experiments has no backend, and that is a checked fact rather than an
 * assumption: no `/api/v1/experiments` path in the OpenAPI document, no router
 * in `app/api/v1/router.py`, no model, repository or table behind one, and
 * `GET /api/v1/experiments` answers 404 against a running server.
 *
 * So this page ships no "Record experiment" control. There is no endpoint for it
 * to submit to, and the two alternatives were rejected on purpose: a button that
 * saved nothing is worse than no button, and one that kept records in browser
 * storage would show the reader a list that no other device can see and no report
 * can count — a private list wearing the name of a record.
 *
 * That leaves the job this page can actually do honestly: describe what the
 * module is for, and name precisely what is missing, so the absence of a way to
 * record reads as the state of the product rather than an oversight in the UI.
 */

const EXPERIMENTS = getModule('/experiments')

const GROUP_LABEL =
  NAV_GROUPS.find((group) => group.items.some((item) => item.to === EXPERIMENTS.to))?.label ??
  'Platform'

export default function ExperimentsPage() {
  const Icon = EXPERIMENTS.icon

  return (
    <div className="app-container space-y-6 py-6 lg:py-8">
      <PageHeader
        eyebrow={GROUP_LABEL}
        title={EXPERIMENTS.label}
        description={EXPERIMENTS.summary}
        badges={
          <Badge variant="outline" className="gap-1 font-normal">
            <Icon aria-hidden="true" className="size-3.5" />
            Planned — Phase {EXPERIMENTS.phase}
          </Badge>
        }
      />

      <Alert>
        <Info aria-hidden="true" />
        <div className="space-y-1">
          <p className="font-medium">There is no way to record an experiment yet, and that is deliberate.</p>
          <p className="leading-relaxed text-muted-foreground">
            The experiments module has no backend. There is no experiment endpoint to create, read
            or update against, so this page offers no control that would look like it saved
            something. Recording appears here when that backend module ships.
          </p>
        </div>
      </Alert>

      <div className="grid gap-4 lg:grid-cols-12">
        <Card className="lg:col-span-7">
          <CardHeader>
            <div className="flex items-center gap-2.5">
              <span className="flex size-7 items-center justify-center rounded-md border border-border bg-muted text-muted-foreground">
                <Icon className="size-4" aria-hidden="true" />
              </span>
              <CardTitle>What experiments will do</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm leading-relaxed text-muted-foreground">{EXPERIMENTS.vision}</p>

            <Separator />

            <ul className="divide-y divide-border">
              {EXPERIMENTS.capabilities.map((capability) => (
                <li key={capability.title} className="flex gap-3 py-3 first:pt-0 last:pb-0">
                  <span
                    aria-hidden="true"
                    className="mt-1 size-1.5 shrink-0 rounded-full bg-border"
                  />
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-foreground">{capability.title}</p>
                    <p className="mt-0.5 text-sm leading-relaxed text-muted-foreground">
                      {capability.description}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        <Card className="lg:col-span-5">
          <CardHeader>
            <CardTitle>Available today</CardTitle>
            <CardDescription>Nothing is stored here before Phase {EXPERIMENTS.phase}.</CardDescription>
          </CardHeader>
          <CardContent>
            <EmptyState
              icon={Layers}
              title="No experiments recorded yet"
              description="Experiments arrive with their backend module. Until then there is no record to show and no control that would save one."
            />
          </CardContent>
        </Card>
      </div>
    </div>
  )
}