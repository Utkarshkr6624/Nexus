/**
 * Pre-import the chart modules that `lazy-charts.tsx` loads on demand.
 *
 * The first test in a file that renders a `LazyChart` used to pay the cold
 * transform of recharts/d3 inside its own 10 s wait. Alone that takes 1-2 s, but in
 * the full parallel suite it measured over 10 s, so the *first* test of a chart
 * file failed and every later one passed. Paying it once in `beforeAll` (generous
 * hook timeout) keeps each test's own wait and assertions exactly as they were.
 */
export const WARM_LAZY_CHARTS_TIMEOUT_MS = 60_000

export async function warmLazyCharts(): Promise<void> {
  await Promise.all([
    import('@/features/analytics/components/trend-chart'),
    import('@/features/analytics/components/bar-chart'),
    import('@/features/analytics/components/time-distribution-chart'),
  ])
}
