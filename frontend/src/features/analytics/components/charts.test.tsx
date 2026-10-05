import type { ReactElement, ReactNode } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ChartShell, ChartTooltip } from '@/features/analytics/components/chart-shell'
import { EmptyAnalytics } from '@/features/analytics/components/empty-analytics'
import { Heatmap, type HeatmapDay } from '@/features/analytics/components/heatmap'
import {
  AnalyticsBarChart,
  LazyChart,
  TimeDistributionChart,
  TrendChart,
} from '@/features/analytics/components/lazy-charts'
import { NO_VALUE } from '@/features/analytics/format'
import type { TimeBucketRead } from '@/types/analytics'

/*
 * The chart surfaces of the analytics page.
 *
 * Four decisions shape this file, and each of them exists to keep the tests tied
 * to what a reader actually gets rather than to how it is drawn.
 *
 * **Recharts is given a size, and only a size.** jsdom has no layout engine, so
 * `ResponsiveContainer` measures 0×0 and renders an empty `<div>` — every chart
 * would come back as a title above an empty box, and half the assertions below
 * would pass without a single mark having been drawn. The mock replaces exactly
 * that measurement with a fixed 640×256 box; the axes, the scales, the areas and
 * the sectors are all the library's own. The mock also tags what it wraps in
 * `data-chart-surface`, which is how "the chart drew nothing" is told apart from
 * "the chart was never mounted" without reaching into a library's class names.
 *
 * **Nothing asserts on a library internal.** No `d` attribute, no recharts class
 * name, no pixel coordinate. Every expectation below is on text a reader can
 * see, a role, a `title`, or the mark the mock itself put there — so a recharts
 * upgrade cannot fail this file.
 *
 * **The charts are rendered the way the app renders them.** The three recharts
 * charts sit behind `lazy()` in `lazy-charts.tsx`, so they go through the same
 * `LazyChart` boundary the page uses and every assertion waits for the real
 * component to arrive. `ChartShell`, `EmptyAnalytics` and `Heatmap` are not
 * lazily loaded in the app, so they are imported directly.
 *
 * **The dates are fixed, and in a completed year.** `formatShortDate` omits the
 * year for dates in the current one, so a fixture written for "this year" would
 * start failing in January. The window here is 7–15 January 2019 — a Monday
 * start, which is also what the heatmap's Monday-first week convention expects —
 * and the expected cell titles are built with the same `Intl` call the formatter
 * uses, so the assertion does not depend on the machine's locale.
 */

/**
 * A stand-in for the layout jsdom cannot compute, wrapping whatever
 * `ResponsiveContainer` was given. The `data-chart-surface` marker is this
 * file's own: it is the only handle on "a chart is mounted here" that does not
 * depend on a third-party class name surviving an upgrade.
 */
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  const react = await import('react')
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children?: ReactElement<Record<string, unknown>> }) =>
      children ? (
        <div data-chart-surface="true">
          {react.cloneElement(children, { width: 640, height: 256 })}
        </div>
      ) : null,
  }
})

/** A Monday, so the heatmap's columns are whole weeks with no leading blanks. */
const WINDOW_START = '2019-01-07'
const WINDOW_END = '2019-01-13'
const WINDOW_LABEL = '7 Jan – 13 Jan 2019'

/**
 * The title of the `LazyChart` boundary, which no real chart renders. Seeing it
 * means the lazy module has not arrived; its *absence* is the signal that the
 * real component is mounted, which is what every assertion below needs before it
 * starts looking at the tree.
 */
const PLACEHOLDER_TITLE = 'Loading chart'

/**
 * How long the first lazy resolution is allowed to take.
 *
 * Only the *first* test to touch a lazily loaded chart pays for it: the
 * `import()` has to fetch and transform the chart module, and the very first of
 * them also pulls in the whole of Recharts. That is tens of files through
 * Vite's transform pipeline on a cold cache, which measures ~1 s — right on top
 * of `waitFor`'s 1 s default, so the first chart in the file intermittently
 * timed out on a perfectly correct render. Every later test finds its module
 * already in the registry and resolves in milliseconds. The bound is generous
 * on purpose: it only ever has to cover module loading, never a real assertion,
 * so raising it cannot turn a genuine failure into a pass.
 */
const LAZY_RESOLVE_TIMEOUT_MS = 10_000

/**
 * Render a chart through the real `Suspense` boundary and resolve once the lazy
 * module has been swapped in.
 *
 * `waitFor` is deliberate rather than a blind `findBy` on the chart's own title:
 * the fallback paints that same title, so awaiting the title would happily pass
 * against a skeleton. The fallback and the real card are different elements, so
 * waiting for the fallback to leave the document is what actually proves the
 * chart resolved. `ChartFallback` renders its `title` into a `CardTitle`, and no
 * real chart is ever given that title, so the string is a marker only the
 * fallback can produce. When the module is already in the registry from an
 * earlier test the fallback is never painted, and the first check passes.
 */
async function renderLazy(node: ReactNode) {
  const view = render(<LazyChart title={PLACEHOLDER_TITLE}>{node}</LazyChart>)
  await waitFor(
    () => {
      expect(screen.queryByText(PLACEHOLDER_TITLE)).not.toBeInTheDocument()
    },
    { timeout: LAZY_RESOLVE_TIMEOUT_MS },
  )
  return view
}

/** The element the size shim wrapped a chart in; `null` when no chart is mounted. */
function chartSurface(container: HTMLElement): Element | null {
  return container.querySelector('[data-chart-surface]')
}

function skeleton(container: HTMLElement): Element | null {
  return container.querySelector('[data-slot="skeleton"]')
}

function pulse(container: HTMLElement): Element | null {
  return container.querySelector('.animate-pulse')
}

/**
 * No figure on this surface may print a number it does not have. `NaN`,
 * `Infinity` and the word `undefined` are the three ways a nullable value leaks
 * into a rendered string, and the spec names all three.
 */
function expectNoFabricatedNumbers(): void {
  const text = document.body.textContent ?? ''
  expect(text).not.toMatch(/NaN/)
  expect(text).not.toMatch(/Infinity/)
  expect(text).not.toMatch(/undefined/)
}

/** The `Heatmap`'s per-cell title: `<short date> · <count> <unit>`. */
function cellTitle(day: Date, value: number, valueName: string): string {
  const short = new Intl.DateTimeFormat(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(day)
  return `${short} · ${value} ${valueName}`
}

/**
 * The heatmap's accessible summary.
 *
 * The sentence is split across `<span>`s, so `getByText` cannot see it: its
 * matcher reads only an element's *direct* text children. This predicate reads
 * the whole paragraph instead, which keeps the assertion on the exact sentence
 * and finds nothing else on the card.
 */
const SUMMARY_SHAPE = /^\d+ days? with recorded .+ in the last \d+$/

function querySummary(): HTMLElement | null {
  const matches = screen.queryAllByText((_content, element) => {
    if (element?.tagName !== 'P') return false
    return SUMMARY_SHAPE.test((element.textContent ?? '').replace(/\s+/g, ' ').trim())
  })
  return matches[0] ?? null
}

const january = (dayOfMonth: number) => new Date(2019, 0, dayOfMonth)

/* ------------------------------------------------------------------ fixtures */

/** A full week of daily rows, with a maximum of 8 on Thursday 10 January. */
const DAILY_ROWS = [
  { label: 'Mon 7 Jan', completed: 3, previous: 1 },
  { label: 'Tue 8 Jan', completed: 5, previous: 2 },
  { label: 'Wed 9 Jan', completed: 0, previous: 4 },
  { label: 'Thu 10 Jan', completed: 8, previous: 3 },
  { label: 'Fri 11 Jan', completed: 2, previous: 0 },
  { label: 'Sat 12 Jan', completed: 1, previous: 1 },
  { label: 'Sun 13 Jan', completed: 6, previous: 2 },
]

const COMPLETED_SERIES = [{ key: 'completed', label: 'Tasks completed' }]

/**
 * Three days, with a zero in the middle. A bar of zero height is the case that
 * separates "counted and found none" from "not measured", and it still gets a
 * slot on the axis.
 */
const BAR_ROWS = [
  { label: 'Mon 7 Jan', completed: 3 },
  { label: 'Tue 8 Jan', completed: 5 },
  { label: 'Wed 9 Jan', completed: 0 },
]

const PROJECT_ROWS = [
  { label: 'NEXUS', minutes: 100 },
  { label: 'DSA', minutes: 60 },
  { label: 'React', minutes: 40 },
]

/** 100 + 60 + 40 = 200 minutes, i.e. shares of 50% / 30% / 20% exactly. */
const PROJECT_BUCKETS: TimeBucketRead[] = [
  { key: 'p-nexus', label: 'NEXUS', minutes: 100, share: 50 },
  { key: 'p-dsa', label: 'DSA', minutes: 60, share: 30 },
  { key: 'p-react', label: 'React', minutes: 40, share: 20 },
]

/** 150 attributed to projects plus 50 unassigned = the 200 the API reports. */
const PROJECT_BUCKETS_WITH_UNASSIGNED: TimeBucketRead[] = [
  { key: 'p-nexus', label: 'NEXUS', minutes: 100, share: 50 },
  { key: 'p-dsa', label: 'DSA', minutes: 30, share: 15 },
  { key: 'p-react', label: 'React', minutes: 20, share: 10 },
]

/** 100 + 50 + 30 + 20 + 20 = 220; the tail past `maxSlices` sums to 70. */
const SEVEN_BUCKETS: TimeBucketRead[] = [
  { key: 'p-a', label: 'NEXUS', minutes: 100, share: 45.45 },
  { key: 'p-b', label: 'DSA', minutes: 50, share: 22.73 },
  { key: 'p-c', label: 'React', minutes: 30, share: 13.64 },
  { key: 'p-d', label: 'Books', minutes: 20, share: 9.09 },
  { key: 'p-e', label: 'Papers', minutes: 20, share: 9.09 },
]

/**
 * `satisfies`, not an annotation: `HeatmapDay['value']` is `unknown` because it
 * comes off the wire, and an annotation would widen every fixture value here to
 * `unknown` too, leaving the expectations below unable to name the number they
 * are checking. The shape is still checked against the real prop type.
 */
const HEATMAP_DAYS = [
  { date: '2019-01-07', value: 3 },
  { date: '2019-01-08', value: 0 },
  { date: '2019-01-09', value: 1 },
  { date: '2019-01-10', value: 0 },
  { date: '2019-01-11', value: 6 },
  { date: '2019-01-12', value: 0 },
  { date: '2019-01-13', value: 0 },
] satisfies readonly HeatmapDay[]

/* -------------------------------------------------------------- chart shell */

describe('ChartShell', () => {
  it('renders its title, subtitle and actions as the card header', () => {
    render(
      <ChartShell
        title="Tasks completed per day"
        subtitle={WINDOW_LABEL}
        actions={<button type="button">Export CSV</button>}
      >
        <p>chart body</p>
      </ChartShell>,
    )

    // A card title is a real heading, so the surface has a document outline
    // rather than a row of bold divs.
    expect(screen.getByRole('heading', { name: 'Tasks completed per day' })).toBeInTheDocument()
    expect(screen.getByText(WINDOW_LABEL)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Export CSV' })).toBeInTheDocument()
    expect(screen.getByText('chart body')).toBeInTheDocument()
  })

  it('accepts a node as the subtitle, because the window label is composed', () => {
    render(
      <ChartShell
        title="Recorded time"
        subtitle={
          <span>
            {WINDOW_LABEL} <span>· actual against planned</span>
          </span>
        }
      >
        <p>chart body</p>
      </ChartShell>,
    )

    expect(screen.getByText(WINDOW_LABEL)).toBeInTheDocument()
    expect(screen.getByText('· actual against planned')).toBeInTheDocument()
  })

  it('replaces the body with the caller\'s empty copy instead of an empty axis', () => {
    const { container } = render(
      <ChartShell title="Tasks completed per day" empty={<p>Nothing recorded in this window</p>}>
        <div data-chart-surface="true">chart body</div>
      </ChartShell>,
    )

    expect(screen.getByText('Nothing recorded in this window')).toBeInTheDocument()
    expect(chartSurface(container)).toBeNull()
  })

  it('shows a skeleton while loading, in place of the body', () => {
    const { container } = render(
      <ChartShell title="Tasks completed per day" isLoading>
        <div data-chart-surface="true">chart body</div>
      </ChartShell>,
    )

    expect(skeleton(container)).not.toBeNull()
    expect(screen.queryByText('chart body')).not.toBeInTheDocument()
    expect(chartSurface(container)).toBeNull()
  })

  it('prefers the loading state over the empty state, so a refetch never says "empty"', () => {
    const { container } = render(
      <ChartShell
        title="Tasks completed per day"
        isLoading
        empty={<p>Nothing recorded in this window</p>}
      >
        <div data-chart-surface="true">chart body</div>
      </ChartShell>,
    )

    expect(skeleton(container)).not.toBeNull()
    expect(screen.queryByText('Nothing recorded in this window')).not.toBeInTheDocument()
  })
})

/* ---------------------------------------------------------- empty analytics */

describe('EmptyAnalytics', () => {
  it('gives every metric its own sentence, because the fix differs per metric', () => {
    // "Not enough activity yet" alone does not tell a reader what to do: a
    // missing trend and a missing deadline rate are filled by different
    // behaviour, so each metric names the ingredient that would fill it.
    const cases: Array<[Parameters<typeof EmptyAnalytics>[0]['metric'], string]> = [
      [
        'trend',
        'A trend needs recorded days to plot. Each point is a day something happened, so an unrecorded stretch is a gap rather than a zero.',
      ],
      [
        'time',
        'Time is read from finished work sessions in the window. Run or complete one and the breakdown appears here.',
      ],
      ['heatmap', 'No day in this window has recorded activity yet.'],
      [
        'deadlines',
        'Give a task a due date and finish it — the rate is on-time over everything finished. Until then there is nothing to be on time about.',
      ],
    ]

    for (const [metric, description] of cases) {
      const { unmount } = render(<EmptyAnalytics metric={metric} />)
      expect(screen.getByText('Not enough activity yet')).toBeInTheDocument()
      expect(screen.getByText(description)).toBeInTheDocument()
      unmount()
    }
  })

  it('renders the backend\'s reason word for word, in preference to its own copy', () => {
    const reason = 'No finished work sessions in 7 Jan 2019 – 13 Jan 2019.'
    render(<EmptyAnalytics metric="time" reason={reason} />)

    expect(screen.getByText(reason)).toBeInTheDocument()
    expect(
      screen.queryByText(
        'Time is read from finished work sessions in the window. Run or complete one and the breakdown appears here.',
      ),
    ).not.toBeInTheDocument()
  })

  it('falls back to the metric copy when the reason carries no words', () => {
    render(<EmptyAnalytics metric="heatmap" reason="   " />)

    expect(screen.getByText('No day in this window has recorded activity yet.')).toBeInTheDocument()
  })
})

/* ----------------------------------------------------------------- tooltip */

describe('ChartTooltip', () => {
  /**
   * Recharts hands a null straight through for a bucket it could not measure,
   * which is precisely the case this tooltip exists to render. `TooltipEntry`
   * types `value` as `string | number | undefined`, so the null is asserted once
   * here — at the boundary that produces it — rather than cast at every use.
   */
  const GAP_PAYLOAD = [
    { dataKey: 'completed', name: 'Tasks completed', value: null },
    { dataKey: 'previous', name: 'Previous period', value: 4 },
  ] as unknown as NonNullable<Parameters<typeof ChartTooltip>[0]['payload']>

  function row(key: string): HTMLElement {
    const cell = screen.getByText(key).closest('li')
    expect(cell).not.toBeNull()
    return cell as HTMLElement
  }

  it('names every series in the payload and formats each by its unit', () => {
    render(
      <ChartTooltip
        active
        label="Mon 7 Jan"
        payload={[
          { dataKey: 'recorded', name: 'Recorded', value: 135 },
          { dataKey: 'planned', name: 'Planned', value: 90 },
          { dataKey: 'completion', name: 'Completion', value: 42.4 },
        ]}
        units={{ recorded: 'minutes', planned: 'minutes', completion: 'percent' }}
      />,
    )

    expect(screen.getByText('Mon 7 Jan')).toBeInTheDocument()
    // 135 minutes is "2h 15m" and 90 is a flat "1h 30m"; neither may print as a
    // raw minute count, because the axis and the tooltip share a unit. 42.4% is
    // rendered to the whole percent the surface uses everywhere else.
    expect(row('recorded')).toHaveTextContent(/^recorded\s*2h 15m$/)
    expect(row('planned')).toHaveTextContent(/^planned\s*1h 30m$/)
    expect(row('completion')).toHaveTextContent(/^completion\s*42%$/)
  })

  it('renders NO_VALUE for a series with no measurement, never NaN', () => {
    render(
      <ChartTooltip
        active
        label="Wed 9 Jan"
        payload={GAP_PAYLOAD}
        units={{ completed: 'count', previous: 'count' }}
      />,
    )

    // The bucket is a gap, not a zero. `formatChartValue` collapses a null to
    // the same dash the rest of the surface uses, which is why the expectation
    // is that exact string rather than "some dash".
    expect(row('completed')).toHaveTextContent(new RegExp(`^completed\\s*${NO_VALUE}$`))
    expect(row('previous')).toHaveTextContent(/^previous\s*4$/)
    expectNoFabricatedNumbers()
  })

  it('collapses every non-finite value to NO_VALUE', () => {
    render(
      <ChartTooltip
        active
        label="Tue 8 Jan"
        payload={[
          { dataKey: 'a', name: 'a', value: Number.NaN },
          { dataKey: 'b', name: 'b', value: Number.POSITIVE_INFINITY },
          { dataKey: 'c', name: 'c', value: undefined },
        ]}
      />,
    )

    expect(row('a')).toHaveTextContent(new RegExp(`^a\\s*${NO_VALUE}$`))
    expect(row('b')).toHaveTextContent(new RegExp(`^b\\s*${NO_VALUE}$`))
    expect(row('c')).toHaveTextContent(new RegExp(`^c\\s*${NO_VALUE}$`))
    expectNoFabricatedNumbers()
  })

  it('falls back to the series name when the entry carries no key', () => {
    render(
      <ChartTooltip active label="Mon 7 Jan" payload={[{ name: 'Tasks created', value: 3 }]} />,
    )

    expect(row('Tasks created')).toHaveTextContent(/^Tasks created\s*3$/)
  })

  it('renders nothing while it is inactive or has nothing to say', () => {
    const { rerender, container } = render(
      <ChartTooltip active={false} label="Mon 7 Jan" payload={[{ dataKey: 'a', value: 1 }]} />,
    )
    expect(container).toBeEmptyDOMElement()

    rerender(<ChartTooltip active label="Mon 7 Jan" payload={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})

/* -------------------------------------------------------------- trend chart */

describe('TrendChart', () => {
  it('plots its rows under a title that names the window', async () => {
    const { container } = await renderLazy(
      <TrendChart
        title="Tasks completed against the previous period"
        subtitle={WINDOW_LABEL}
        data={DAILY_ROWS}
        series={[
          { key: 'completed', label: 'This period' },
          { key: 'previous', label: 'Previous period', colorIndex: 4 },
        ]}
      />,
    )

    expect(
      screen.getByRole('heading', { name: 'Tasks completed against the previous period' }),
    ).toBeInTheDocument()
    expect(screen.getByText(WINDOW_LABEL)).toBeInTheDocument()
    expect(chartSurface(container)).not.toBeNull()
  })

  it('labels every day it plots, and the y axis reaches the true maximum', async () => {
    await renderLazy(
      <TrendChart
        title="Tasks completed"
        subtitle={WINDOW_LABEL}
        data={DAILY_ROWS}
        series={COMPLETED_SERIES}
      />,
    )

    for (const entry of DAILY_ROWS) {
      expect(screen.getByText(entry.label)).toBeInTheDocument()
    }
    // The busiest day is 8. A default domain that stopped at 4 would clip it, so
    // the top tick is the data's own maximum rather than a rounded-up default.
    expect(screen.getByText('8')).toBeInTheDocument()
    expect(screen.queryByText('12')).not.toBeInTheDocument()
  })

  it('renders the line variant from the same props', async () => {
    const { container } = await renderLazy(
      <TrendChart
        title="Planned against recorded"
        subtitle={WINDOW_LABEL}
        kind="line"
        data={DAILY_ROWS}
        series={COMPLETED_SERIES}
      />,
    )

    expect(screen.getByRole('heading', { name: 'Planned against recorded' })).toBeInTheDocument()
    expect(screen.getByText('Mon 7 Jan')).toBeInTheDocument()
    expect(chartSurface(container)).not.toBeNull()
  })

  it('renders the empty state, and no chart, when the window has no rows', async () => {
    const { container } = await renderLazy(
      <TrendChart
        title="Tasks completed"
        subtitle={WINDOW_LABEL}
        data={[]}
        series={COMPLETED_SERIES}
        emptyMetric="trend"
      />,
    )

    // The frame survives so the card does not collapse and reflow the grid; only
    // the body is replaced.
    expect(screen.getByRole('heading', { name: 'Tasks completed' })).toBeInTheDocument()
    expect(screen.getByText('Not enough activity yet')).toBeInTheDocument()
    expect(
      screen.getByText(
        'A trend needs recorded days to plot. Each point is a day something happened, so an unrecorded stretch is a gap rather than a zero.',
      ),
    ).toBeInTheDocument()
    expect(chartSurface(container)).toBeNull()
  })

  it('honours an explicit isEmpty over rows that exist but measure nothing', async () => {
    // A window can return daily rows whose buckets are all zero. Plotting them
    // would claim the days were measured and found empty, and the backend's
    // `reason_if_unavailable` is the authority on why the number is absent.
    const { container } = await renderLazy(
      <TrendChart
        title="Tasks completed"
        subtitle={WINDOW_LABEL}
        data={DAILY_ROWS}
        series={COMPLETED_SERIES}
        isEmpty
        emptyMetric="overview"
        emptyReason="No task activity between 7 Jan and 13 Jan 2019."
      />,
    )

    expect(screen.getByText('No task activity between 7 Jan and 13 Jan 2019.')).toBeInTheDocument()
    expect(chartSurface(container)).toBeNull()
    expect(screen.queryByText('Mon 7 Jan')).not.toBeInTheDocument()
  })

  it('plots an absent value as a gap and prints no fabricated number', async () => {
    const rows = [
      { label: 'Mon 7 Jan', completed: 3 },
      // Two shapes of "not measured": an explicit null and a missing key.
      { label: 'Tue 8 Jan', completed: null },
      { label: 'Wed 9 Jan' },
      { label: 'Thu 10 Jan', completed: 6 },
    ]

    const { container } = await renderLazy(
      <TrendChart
        title="Tasks completed"
        subtitle={WINDOW_LABEL}
        data={rows}
        series={COMPLETED_SERIES}
      />,
    )

    // The days are still labelled and the chart is still mounted — a gap in the
    // line, not a hole in the axis and not a printed NaN.
    expect(chartSurface(container)).not.toBeNull()
    expect(screen.getByText('Tue 8 Jan')).toBeInTheDocument()
    expectNoFabricatedNumbers()
  })

  it('shows a skeleton rather than a zeroed axis while loading', async () => {
    const { container } = await renderLazy(
      <TrendChart
        title="Tasks completed"
        subtitle={WINDOW_LABEL}
        data={DAILY_ROWS}
        series={COMPLETED_SERIES}
        isLoading
      />,
    )

    expect(skeleton(container)).not.toBeNull()
    expect(chartSurface(container)).toBeNull()
    expect(screen.queryByText('Mon 7 Jan')).not.toBeInTheDocument()
  })
})

/* ---------------------------------------------------------------- bar chart */

describe('AnalyticsBarChart', () => {
  it('renders its title and window, and mounts the chart', async () => {
    const { container } = await renderLazy(
      <AnalyticsBarChart
        title="Tasks completed per day"
        subtitle={WINDOW_LABEL}
        data={BAR_ROWS}
        series={COMPLETED_SERIES}
      />,
    )

    expect(screen.getByRole('heading', { name: 'Tasks completed per day' })).toBeInTheDocument()
    expect(screen.getByText(WINDOW_LABEL)).toBeInTheDocument()
    expect(chartSurface(container)).not.toBeNull()
  })

  it('mounts in the horizontal orientation without throwing', async () => {
    // Six project names rotated into slanted text is the reason orientation is a
    // prop rather than a second component. What is asserted here is that the
    // orientation reaches the chart at all; the labels it should be carrying are
    // covered by the skipped case below, which is waiting on a fix.
    const { container } = await renderLazy(
      <AnalyticsBarChart
        title="Time per project"
        subtitle={WINDOW_LABEL}
        orientation="horizontal"
        data={PROJECT_ROWS}
        series={[{ key: 'minutes', label: 'Recorded', unit: 'minutes' }]}
      />,
    )

    expect(screen.getByRole('heading', { name: 'Time per project' })).toBeInTheDocument()
    expect(chartSurface(container)).not.toBeNull()
  })

  /*
   * Both axes used to be wrapped in a React fragment in `bar-chart.tsx`.
   * Recharts enumerates a chart's axes over its direct children, so a Fragment
   * is opaque to that walk and neither axis was ever registered: the chart
   * rendered with no axis and no category labels, and in the horizontal case
   * the longest bar clipped above the plot area while shorter bars drew
   * nothing. The axes are now passed as a keyed array instead, so these labels
   * are assertable exactly as written.
   */
  it('labels every category of the window on the axis', async () => {
    await renderLazy(
      <AnalyticsBarChart
        title="Tasks completed per day"
        subtitle={WINDOW_LABEL}
        data={BAR_ROWS}
        series={COMPLETED_SERIES}
      />,
    )

    for (const entry of BAR_ROWS) {
      expect(screen.getByText(entry.label)).toBeInTheDocument()
    }
  })

  it('keeps project names intact on the horizontal category axis', async () => {
    await renderLazy(
      <AnalyticsBarChart
        title="Time per project"
        subtitle={WINDOW_LABEL}
        orientation="horizontal"
        data={PROJECT_ROWS}
        series={[{ key: 'minutes', label: 'Recorded', unit: 'minutes' }]}
      />,
    )

    for (const entry of PROJECT_ROWS) {
      expect(screen.getByText(entry.label)).toBeInTheDocument()
    }
  })

  it('renders the empty state, and no chart, when there is nothing to count', async () => {
    const { container } = await renderLazy(
      <AnalyticsBarChart
        title="Tasks completed per day"
        subtitle={WINDOW_LABEL}
        data={[]}
        series={COMPLETED_SERIES}
        emptyMetric="tasks"
      />,
    )

    expect(screen.getByText('Not enough activity yet')).toBeInTheDocument()
    expect(
      screen.getByText(
        'Task throughput comes from recorded tasks: created, completed, blocked and overdue. Create your first task to start the series.',
      ),
    ).toBeInTheDocument()
    expect(chartSurface(container)).toBeNull()
  })

  it('shows a skeleton rather than an empty axis while loading', async () => {
    const { container } = await renderLazy(
      <AnalyticsBarChart
        title="Tasks completed per day"
        subtitle={WINDOW_LABEL}
        data={BAR_ROWS}
        series={COMPLETED_SERIES}
        isLoading
      />,
    )

    expect(skeleton(container)).not.toBeNull()
    expect(chartSurface(container)).toBeNull()
    expect(screen.queryByText('Mon 7 Jan')).not.toBeInTheDocument()
  })
})

/* ------------------------------------------------------- time distribution */

describe('TimeDistributionChart', () => {
  function legendRow(label: string): HTMLElement {
    const item = screen.getByText(label).closest('li')
    expect(item).not.toBeNull()
    return item as HTMLElement
  }

  it('labels every slice with a name, a duration and a share — never colour alone', async () => {
    const { container } = await renderLazy(
      <TimeDistributionChart
        title="Where the time went"
        subtitle={`${WINDOW_LABEL} · by project`}
        buckets={PROJECT_BUCKETS}
        totalMinutes={200}
      />,
    )

    // 100/200, 60/200 and 40/200 are asserted exactly because 60/200 is a third
    // and 40/200 a fifth: a rounding that drifts would show up here, and the
    // three shares are meant to sum to 100%.
    expect(legendRow('NEXUS')).toHaveTextContent(/^NEXUS\s*1h 40m\s*50%$/)
    expect(legendRow('DSA')).toHaveTextContent(/^DSA\s*1h\s*30%$/)
    expect(legendRow('React')).toHaveTextContent(/^React\s*40m\s*20%$/)

    // The colour swatch is decorative; the words beside it are the legend, so a
    // reader who cannot see the colour still reads the share.
    expect(legendRow('NEXUS').querySelector('span[aria-hidden="true"]')).not.toBeNull()
    expect(chartSurface(container)).not.toBeNull()
  })

  it('states the window total in the middle of the donut', async () => {
    await renderLazy(
      <TimeDistributionChart
        title="Where the time went"
        subtitle={`${WINDOW_LABEL} · by project`}
        buckets={PROJECT_BUCKETS}
        totalMinutes={200}
      />,
    )

    // 200 minutes is "3h 20m", which matches 100 + 60 + 40 exactly, so the
    // centred figure can be checked against the legend by eye.
    expect(screen.getByText('3h 20m')).toBeInTheDocument()
    expect(screen.getByText('tracked')).toBeInTheDocument()
  })

  it('shows unassigned time as its own slice rather than folding it into a project', async () => {
    await renderLazy(
      <TimeDistributionChart
        title="Where the time went"
        subtitle={`${WINDOW_LABEL} · by project`}
        buckets={PROJECT_BUCKETS_WITH_UNASSIGNED}
        unassignedMinutes={50}
        totalMinutes={200}
      />,
    )

    // 150 attributed plus 50 unassigned is the 200 the API reports, so the donut
    // still sums to the total. 50/200 is a quarter, not "some share", and 30/200
    // is 15% — a number that only comes out right if the unassigned minutes are
    // in the denominator and not in a bucket.
    expect(legendRow('NEXUS')).toHaveTextContent(/^NEXUS\s*1h 40m\s*50%$/)
    expect(legendRow('DSA')).toHaveTextContent(/^DSA\s*30m\s*15%$/)
    expect(legendRow('Unassigned')).toHaveTextContent(/^Unassigned\s*50m\s*25%$/)
    expect(screen.getByText('3h 20m')).toBeInTheDocument()
  })

  it('folds the tail past maxSlices into one named slice, and says how many', async () => {
    await renderLazy(
      <TimeDistributionChart
        title="By task"
        subtitle={`${WINDOW_LABEL} · by task`}
        buckets={SEVEN_BUCKETS}
        totalMinutes={220}
        maxSlices={2}
      />,
    )

    // Three buckets (30 + 20 + 20 = 70 minutes) are summarised rather than
    // silently dropped, and 70/220 rounds to 32%.
    expect(legendRow('3 smaller sources')).toHaveTextContent(/^3 smaller sources\s*1h 10m\s*32%$/)
    expect(screen.queryByText('Books')).not.toBeInTheDocument()
    expect(screen.queryByText('Papers')).not.toBeInTheDocument()
    // The two survivors are named too, so the assertion pins *which* two are
    // kept: the fold has to take the biggest sources, and dropping this half
    // would let a fold of any two buckets pass the checks above.
    expect(legendRow('NEXUS')).toHaveTextContent(/^NEXUS\s*1h 40m/)
    expect(legendRow('DSA')).toHaveTextContent(/^DSA\s*50m/)
  })

  it('renders the empty state, and no legend, when no minutes were recorded', async () => {
    const { container } = await renderLazy(
      <TimeDistributionChart
        title="Where the time went"
        subtitle={`${WINDOW_LABEL} · by project`}
        buckets={[{ key: 'p-nexus', label: 'NEXUS', minutes: 0, share: null }]}
        totalMinutes={0}
        reasonIfUnavailable="No finished work sessions in the window."
      />,
    )

    expect(screen.getByText('Not enough activity yet')).toBeInTheDocument()
    expect(screen.getByText('No finished work sessions in the window.')).toBeInTheDocument()
    expect(chartSurface(container)).toBeNull()
    // "tracked" is the label under the centred total, so it is the marker that
    // the donut was replaced rather than drawn at zero.
    expect(screen.queryByText('tracked')).not.toBeInTheDocument()
    expect(screen.queryByText('NEXUS')).not.toBeInTheDocument()
  })

  it('shows a skeleton rather than an empty donut while loading', async () => {
    const { container } = await renderLazy(
      <TimeDistributionChart
        title="Where the time went"
        subtitle={`${WINDOW_LABEL} · by project`}
        buckets={PROJECT_BUCKETS}
        totalMinutes={200}
        isLoading
      />,
    )

    expect(skeleton(container)).not.toBeNull()
    expect(chartSurface(container)).toBeNull()
    expect(screen.queryByText('NEXUS')).not.toBeInTheDocument()
    expect(screen.queryByText('tracked')).not.toBeInTheDocument()
  })
})

/* ------------------------------------------------------------------ heatmap */

describe('Heatmap', () => {
  it('draws one cell per day in the window, including the days with no activity', () => {
    render(
      <Heatmap
        title="Active days"
        subtitle={WINDOW_LABEL}
        days={HEATMAP_DAYS}
        start={WINDOW_START}
        end={WINDOW_END}
        valueName="work sessions"
      />,
    )

    expect(screen.getByRole('heading', { name: 'Active days' })).toBeInTheDocument()
    expect(screen.getByText(WINDOW_LABEL)).toBeInTheDocument()

    // 7–13 January is a whole week, so there are exactly seven titled cells and
    // no leading blanks. A day with nothing recorded is still a day: dropping it
    // would shorten the week and quietly re-plot the rest.
    expect(screen.getAllByTitle(/\d/)).toHaveLength(7)
    for (const entry of HEATMAP_DAYS) {
      const date = january(Number(entry.date.slice(-2)))
      expect(screen.getByTitle(cellTitle(date, entry.value, 'work sessions'))).toBeInTheDocument()
    }
  })

  it('gives every cell a title naming that day and its activity', () => {
    render(
      <Heatmap
        title="Active days"
        subtitle={WINDOW_LABEL}
        days={HEATMAP_DAYS}
        start={WINDOW_START}
        end={WINDOW_END}
        valueName="work sessions"
      />,
    )

    // The grid is aria-hidden, so the title is all a reader gets per day. It
    // carries both halves — which day, and how much — or neither is usable.
    expect(screen.getByTitle(cellTitle(january(7), 3, 'work sessions'))).toBeInTheDocument()
    expect(screen.getByTitle(cellTitle(january(11), 6, 'work sessions'))).toBeInTheDocument()
    expect(screen.getByTitle(cellTitle(january(13), 0, 'work sessions'))).toBeInTheDocument()
  })

  it('draws an inactive day in its own level rather than an active one', () => {
    render(
      <Heatmap
        title="Active days"
        subtitle={WINDOW_LABEL}
        days={HEATMAP_DAYS}
        start={WINDOW_START}
        end={WINDOW_END}
        valueName="work sessions"
      />,
    )

    // The grid is a colour scale, so zero must not borrow an active level: the
    // intensity bucket comes from the day's value, not from its presence.
    const inactive = screen.getByTitle(cellTitle(january(8), 0, 'work sessions'))
    const active = screen.getByTitle(cellTitle(january(11), 6, 'work sessions'))
    expect(inactive.className).toContain('bg-muted')
    expect(active.className).not.toContain('bg-muted')
  })

  it('summarises the window in words, because a screen reader cannot read a colour', () => {
    render(
      <Heatmap
        title="Active days"
        subtitle={WINDOW_LABEL}
        days={HEATMAP_DAYS}
        start={WINDOW_START}
        end={WINDOW_END}
        valueName="work sessions"
      />,
    )

    // 7, 9 and 11 January carry a value: three active days out of seven.
    expect(querySummary()).toHaveTextContent('3 days with recorded work sessions in the last 7')
  })

  it('uses the singular for a single active day', () => {
    render(
      <Heatmap
        title="Active days"
        subtitle={WINDOW_LABEL}
        days={[{ date: '2019-01-07', value: 2 }]}
        start={WINDOW_START}
        end={WINDOW_END}
        valueName="work sessions"
      />,
    )

    expect(querySummary()).toHaveTextContent('1 day with recorded work sessions in the last 7')
  })

  it('pads a window that starts mid-week so every column is a whole week', () => {
    const { container } = render(
      <Heatmap
        title="Active days"
        subtitle={WINDOW_LABEL}
        days={[{ date: '2019-01-09', value: 2 }]}
        start="2019-01-09"
        end="2019-01-15"
        valueName="work sessions"
      />,
    )

    // Wednesday 9 January needs two leading blanks so the first column runs
    // Monday to Sunday. The blanks are untitled, which is why only seven of the
    // nine squares are found by title.
    const grid = container.querySelector('div.grid')
    expect(grid).not.toBeNull()
    expect(grid?.children).toHaveLength(9)
    expect(screen.getAllByTitle(/\d/)).toHaveLength(7)
    expect(querySummary()).toHaveTextContent('1 day with recorded work sessions in the last 7')
  })

  it('renders the empty state when no day in the window has activity', () => {
    const { container } = render(
      <Heatmap
        title="Active days"
        subtitle={WINDOW_LABEL}
        days={HEATMAP_DAYS.map((entry) => ({ ...entry, value: 0 }))}
        start={WINDOW_START}
        end={WINDOW_END}
        valueName="work sessions"
      />,
    )

    // A grid of seven identical squares says nothing; the empty state says why.
    expect(screen.getByText('Not enough activity yet')).toBeInTheDocument()
    expect(screen.getByText('No day in this window has recorded activity yet.')).toBeInTheDocument()
    expect(container.querySelector('div.grid')).toBeNull()
    expect(querySummary()).toBeNull()
  })

  it('shows a skeleton rather than an empty grid while loading', () => {
    const { container } = render(
      <Heatmap
        title="Active days"
        subtitle={WINDOW_LABEL}
        days={HEATMAP_DAYS}
        start={WINDOW_START}
        end={WINDOW_END}
        valueName="work sessions"
        isLoading
      />,
    )

    expect(pulse(container)).not.toBeNull()
    expect(container.querySelector('div.grid')).toBeNull()
    expect(querySummary()).toBeNull()
  })
})
