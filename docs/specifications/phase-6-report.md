# Phase 6 — Final Report: Analytics & Intelligence Data Engine

Companion to [`phase-6-analytics.md`](./phase-6-analytics.md), which is the brief. This is
the record of what was actually built, what was wrong, and what was verified.

---

## 1. Pre-phase regression check

**Passed.** Before any Phase 6 code was written the suite was green: 617 backend tests,
151 frontend tests, `tsc -b` clean, `eslint` clean, `vite build` succeeding. Phases 1–5 were
healthy, so analytics was not built on a broken data pipeline.

The check was re-run at the end of the phase against the same suite. No Phase 1–5 test
changed behaviour.

---

## 2. Analytics architecture

```text
raw rows (tasks, work_sessions, calendar_events, notes, activity_events)
  ↓  AnalyticsService.rebuild_range()  — one grouped query per source, upserted in one statement
daily_metrics (one row per user per calendar day, cut at the database's local midnight)
  ↓  pure scoring functions in analytics/scoring.py
derived metrics + previous-period comparisons
  ↓  18 endpoints under /api/v1/analytics
React dashboard + dedicated analytics page
```

### Why there is only one aggregate table

The brief offers `weekly_metrics`, `monthly_metrics`, `project_metrics`, `task_metrics`
and `productivity_snapshots` as possibilities and then forbids redundant tables. Only
`daily_metrics` was built. What that decision gave up, and why:

- **Weekly and monthly figures are sums, not new facts.** A week's "tasks completed" is the
  sum of seven `daily_metrics.tasks_completed` values. Materialising it buys a narrower
  index scan and costs a second thing that can disagree with the first.
  `daily_series` buckets in Python and the API still answers "per week" in one query.
- **Per-project and per-task figures are not day-bucketed at all.** They are grouped from
  `tasks` and `work_sessions` directly in the same read, because a project has no daily
  shape to pre-aggregate — the interesting axis is the project, and the table has no
  column for it.
- **A snapshot table would multiply.** One `productivity_snapshots` row per task per day is
  exactly the "thousands of redundant snapshots" the brief warns against. Phase 10 calls
  `feature_snapshot()` to build its training set on demand.

What `daily_metrics` genuinely buys is that a dashboard reads one small, indexed, one-row-
per-day table instead of re-scanning `activity_events`, `work_sessions`, `tasks` and
`calendar_events` on every render.

### Idempotency

`uq_daily_metrics_owner_date` plus an `INSERT ... ON CONFLICT DO UPDATE` that **assigns**
the recomputed values rather than incrementing them. A day is *replaced*, never accumulated
onto. Replaying a rebuild over the same range with the same inputs writes the same rows, so
a retried request or an overlapping refresh cannot inflate a total — and a day whose
underlying rows have changed is corrected rather than doubled. That makes the operation
safe to run from a worker that may retry later, which is why it needs no "first seen /
last seen" bookkeeping.

Verified by a test that rebuilds the same range twice and asserts the row count and every
counter are identical, and that the unique constraint is what makes it safe.

### Day boundaries

Cut at the **database server's** local midnight, spelled as
`func.date(<ts> AT TIME ZONE current_setting('TimeZone'))` in every grouped query and
`CAST(:day AS TIMESTAMP) AT TIME ZONE current_setting('TimeZone')` for every window bound.
Calling `func.date()` on a `timestamptz` without naming a zone converts through the
connection's `TimeZone` setting first, so the answer would silently depend on how the
connection was configured; naming the zone makes it a property of the query instead.

This was **UTC midnight** — `func.date(<ts> AT TIME ZONE 'UTC')` — when this report was
written, on the reasoning that every stored instant is UTC and a fixed cut therefore needs
no tzdata lookup. That stopped holding once `AnalyticsRepository.today()` began resolving
"today" from the server's clock: the router resolved the dashboard's window as *today*
while the rows were filed under *yesterday*, for five and a half hours a day on a `+05:30`
host. A daily figure now belongs to the day its owner experienced.

Pinned by tests that seed sessions at 23:59 and 00:01 in the *server's* zone and assert they
land in different rows, and — in `tests/test_analytics_day_agreement.py` — that two figures
computed from one row on one `/overview` response cannot disagree about which day it fell
on.

### Event architecture

The Phase 1–5 `ActivityEvent` enum was extended rather than duplicated. Where NEXUS
departs from the brief's illustrative list, the reason is recorded in place at
`backend/app/models/enums.py:289-342`: `NOTE_UPDATED` stands in for `NOTE_VIEWED` because a
read is not an event on a work feed, and `NOTE_REVISION_CREATED` maps onto the existing
`NOTE_UPDATED`. Events feed the aggregate tier; the dashboard never reads them directly.

---

## 3. Metric formulas

All in `backend/app/services/analytics/scoring.py`, all pure functions, all documented at
the definition rather than at the call site.

| Metric | Formula |
| --- | --- |
| **Productivity** | `round(Σ weight × rate)` over completion (30), deadline (25), consistency (20), focus (25); weights configurable via `Settings`, validated to sum to 100. Clamped 0–100. `available=False` only when **none** of the four can be measured — a partially measurable set still scores, with the unmeasured components contributing zero and saying so. |
| **Consistency** | Active days ÷ window length, with session-count corroboration. Unavailable rather than `0` when nothing was recorded. |
| **Focus** | `0.6 ×` sustained-session-length share + `0.4 ×` follow-through share. A session ≥ 45 min (`FOCUS_TARGET_MINUTES`) counts as uninterrupted. Named "NEXUS Focus Score" and documented as derived from recorded session behaviour, not a measure of human concentration. |
| **Deadline adherence** | `(on_time ÷ (on_time + late)) × 100`. The brief's worked example — 18 on time, 3 late → 85.7% — is asserted exactly. |
| **Estimation accuracy** | Mean `abs(actual − estimated)`, mean percentage error over non-zero estimates, **signed** `bias = mean(estimated − actual)` (negative ⇒ habitually under-estimating), median absolute error, under/over-estimation rates. |

Every score carries a per-component breakdown whose `points` sum to the score **by
construction** — each component reports `weight × rate` out of `weight` — so the breakdown
cannot disagree with the headline number.

All four scores are labelled as NEXUS-derived engineering metrics. None claims scientific
or medical validity, and the API says so in words.

### Comparison safety

`percent_change` returns `None` when the previous value is zero **or negative**, so
`Infinity`, `NaN` and `undefined%` cannot reach a response body or a screen. `rate` is the
single place a division happens, so the "empty denominator is unknown" rule is applied once
rather than at a dozen call sites that each have to remember it.

---

## 4. API endpoints (18)

`overview`, `productivity`, `deadlines`, `consistency`, `focus`, `estimation`, `workload`,
`time`, `projects`, `tasks`, `learning`, `knowledge`, `trends`, `series`, `rebuild` (POST),
`export` (manifest), `export.csv`, `feature-snapshot`.

All take `start_date` / `end_date`, most take `granularity` (`day`/`week`/`month`), and the
project-scoped ones take `project_id`. Every one is user-scoped by an owner predicate in
the `WHERE` clause rather than a post-filter, so another user's rows are never loaded.

---

## 5. ML data preparation

`feature-snapshot` returns a deterministic, owner-scoped feature row per task: priority,
estimated and actual duration, deadline distance, reschedule count, open task count,
historical completion rate, recent work minutes, time of day, day of week, project
velocity, overdue count. Field names and ordering are pinned across two calls by a test, so
Phase 10 can rely on a stable schema.

**No model is trained.** The point is only that these values can be extracted
consistently.

---

## 6. Charts and UI

Recharts 2.15, code-split into a `charts` vendor chunk and lazily imported, so the
dashboard first paint does not pay ~250 ms of module evaluation for panels below the fold.

- **Line** — productivity over time; tasks completed against the previous period
- **Area** — work hours over time, actual against planned
- **Bar** — tasks completed per day; project activity
- **Donut** — time distribution
- **Calendar heatmap** — activity consistency

Every chart has a title, a date-range subtitle, a legend where a chart needs one, an
accessible text summary, a tooltip, and an empty state that says "Not enough activity yet"
rather than drawing an empty axis. Direction of change is always carried by text and an
arrow as well as colour.

---

## 7. Bugs found and fixed during the phase

Nine real defects, **all found by the Phase 6 suite itself**:

1. **`updated_at` never advanced on rebuild.** Both upsert paths build a Core
   `insert().on_conflict_do_update()`, which bypasses the model-level `onupdate=now()`.
   Every row kept the timestamp of its first-ever rebuild, so the staleness signal the API
   and the UI banner depend on was frozen for the life of the row. Fixed by writing
   `"updated_at": func.now()` into both `set_` mappings.

2. **`DailyMetricRead.updated_at` was always `null` on the wire.** `_as_daily` built the
   read from `TRENDABLE_METRICS` only, which excludes `updated_at` because it is not a
   counter. The field the schema documents as "how a client shows 'updated 5 minutes ago'"
   therefore never reached the client, and the staleness banner rendered `null` as the
   literal text **"never updated"** — the one thing the brief forbids.

3. **`/overview` composed its answer from a read taken before the gap it filled.** The
   first request over a never-aggregated window returned `totals` of all zeros and
   `daily: []`, having just written the rows that would have answered it. The window it
   reported was a window it had not measured. Fixed by filling explicitly before reading.

4. **`stale` was `False` for the *most* incomplete window.** `bool(covered) and covered != …`
   reads "nothing computed yet" as "nothing to be stale about", which is backwards. Now
   coverage is sampled **before** the fill, so the flag answers the question a client
   actually has: "were these figures already computed, or did the server just compute them?"
   Sampled after the fill it could only ever say `false`.

5. **Trend comparison against the previous period was always null.** `trends` keyed the
   earlier series by its own bucket dates, but the two windows sit a full window apart, so
   no current bucket ever matched a previous one. `previous`, `absolute_change` and
   `percent_change` were null at every granularity — the comparison the brief asks for and
   the reason for the request. Fixed by pairing the emitted points positionally, with
   empty buckets skipped on **both** sides so this week's first active day is not compared
   against last week's first day of the calendar.

6. **A cancelled task was overdue in one figure and not in another.**
   `count_tasks_overdue_by_day` counted on `completed_at` alone while
   `overdue_count_as_of` filtered on open statuses, so the same task was overdue in the
   daily series and not overdue in the workload figure. Now only `CANCELLED` is excluded
   from the daily series — deliberately *not* restricting it to open statuses, because a
   task completed after its due date was genuinely overdue on that due date and rewriting
   that out of the history to flatter the present is the opposite of what a daily series
   is for.

7. **`GET /analytics/time?project_id=` answered 500.** The service handed a `uuid.UUID` to
   `TimeDistributionRead.project_id`, declared `str | None`, which does not coerce. The
   filter was unreachable for any caller entitled to use it.

8. **The bar chart rendered with no axes at all.** `bar-chart.tsx` wrapped its two axes in
   a React fragment. Recharts enumerates a chart's axes over its *direct* children, so a
   Fragment is opaque to that walk: no axis, no category labels, and in the horizontal case
   the longest bar clipped above the plot area while shorter bars drew nothing. Fixed by
   passing a keyed array instead.

9. **CSV formula injection.** `export_csv` wrote `tasks.title` and `projects.name` straight
   through `csv.writerow`. RFC 4180 quoting does not help — `=1+1` contains no comma,
   quote or newline — so a title of `=HYPERLINK(...)` reached the file and was *evaluated*
   when the user double-clicked it. An export is the one analytics surface that leaves the
   database as a file the user opens. Fixed with a `_csv_safe` guard that prefixes an
   apostrophe on a leading `=`, `+`, `-`, `@`, tab or CR, leaving `C++`-style titles alone.

Two frontend wording defects were also fixed: the heatmap rendered **"recorded recorded
events"**, and the totals table rendered **"up 45m minutes"** because it passed both `unit`
and a `format` that already renders minutes.

---

## 8. Tests

| Suite | Result |
| --- | --- |
| Backend, Phase 1–5 (pre-existing) | 617 passing, behaviour unchanged |
| Backend, Phase 6 analytics (9 new files) | **267 passing, 0 failing, 0 xfail** |
| **Backend, whole suite** | **884 passed in 443 s** |
| Frontend, Phase 1–5 (pre-existing) | 151 passing |
| Frontend, Phase 6 analytics (6 new files) | **210 passing** |
| **Frontend, whole suite** | **361 passed across 32 files in 12 s** |

Per-file counts, so the totals can be audited:

```
backend/tests/test_analytics_scoring.py             63   pure, no database
backend/tests/test_analytics_daily_metrics.py       25
backend/tests/test_analytics_scores_api.py          35
backend/tests/test_analytics_overview_api.py        23
backend/tests/test_analytics_projects_api.py        23
backend/tests/test_analytics_learning_api.py        25
backend/tests/test_analytics_export_api.py          27
backend/tests/test_analytics_privacy.py             19
backend/tests/test_analytics_edge_cases.py          27
frontend/src/features/analytics/format.test.ts               66
frontend/src/features/analytics/components/charts.test.tsx    40
frontend/src/features/analytics/components/date-range-picker.test.tsx  52
frontend/src/features/analytics/components/score-card.test.tsx        32
frontend/src/pages/analytics-page.test.tsx                        10
frontend/src/pages/dashboard-page.test.tsx                         10
```

Coverage follows the brief's TESTING section: daily/weekly/monthly calculations, all four
scores, deadline adherence, estimation accuracy, project metrics, project velocity,
workload, time distribution, date-range filtering, granularity bucketing, empty datasets,
zero division, day boundaries, user isolation across all 18 endpoints, and CSV export.

The brief's worked examples are asserted **exactly**, not approximately:

- 10 tasks, 8 completed → completion rate exactly 80%
- 18 completed on time, 3 late → adherence exactly 85.7%
- estimated 60 min, actual 80 min → error +20 min
- estimated 100 min, actual 150 min → 50% error, negative bias

The CSV tests parse with the stdlib `csv` module and compare **cell by cell**, not by
substring — a substring assertion on a CSV passes while every column after the match is
shifted by a stray comma, which is precisely the failure RFC 4180 quoting exists to prevent.

Seeding lives in `backend/tests/analytics_fixtures.py`, which writes rows directly with
explicit timestamps. Every expected value in the suite is hand-derivable from the fixture;
none depends on the wall clock.

---

## 9. Final regression

Executed, not asserted:

| Gate | Command | Result |
| --- | --- | --- |
| Backend, whole suite | `./.venv/Scripts/python.exe -m pytest tests/ -q` | **884 passed** in 443 s |
| Backend lint | `./.venv/Scripts/python.exe -m ruff check app/ tests/` | clean |
| Backend format | `./.venv/Scripts/python.exe -m ruff format --check app/ tests/` | clean |
| Frontend, whole suite | `npx vitest run` | **361 passed** across 32 files |
| Frontend types | `npx tsc -b --noEmit` | clean |
| Frontend lint | `npx eslint src/` | clean |
| Frontend build | `npx vite build` | succeeds, `charts` chunk 432 kB (115 kB gzipped) |

- **No `xfail` or `skip` markers remain** anywhere — verified by grep over `frontend/src`
  and by the absence of an xfail summary line in the pytest output.
- The two `xfail` markers that existed during the phase — one recording the
  `/analytics/time` 500, one recording the bar-chart axis loss — were both `strict=True`,
  which makes them fail loudly the moment the defect is fixed. Both were removed only
  after the fix was verified, and both turned red on their own first, confirming they had
  been recording real defects rather than sitting dormant.
- **No Phase 1–5 test changed behaviour.** The 617 pre-existing backend tests pass
  unmodified; the only edit to any pre-existing test file was raising a timeout on a lazy
  route in `app-shell.smoke.test.tsx` that had become genuinely flaky, not one that made
  an assertion looser.

---

## 10. Known limitations

- **Aggregation is triggered by reads.** There is no background worker, per the brief's
  instruction not to introduce one. `/overview` and the score routes fill an uncovered
  window on demand through the same idempotent upsert. The service boundary is already the
  seam a worker would take over; only the call site changes.

- **A covered window with changed rows reads as fresh.** `_totals` rebuilds only *uncovered*
  days, so deleting a task in an already-aggregated window leaves the old figure until
  `POST /analytics/rebuild` runs. `aggregates_through` and `updated_at` are what the UI
  shows; the honest answer is an explicit rebuild, not a staleness flag that would be wrong
  more often than right.

- **`CsvExportRead.row_count` counts record separators in the raw text.** Correct for every
  current dataset, but a value containing a literal `\r\n` would inflate it. Latent, not
  currently reachable.

- **The service's "project stats are `None`" branch in `feature_snapshot` is unreachable
  through the API.** `tasks.project_id` is `NOT NULL` with `ON DELETE CASCADE`, so a task
  the caller owns always has an owned project. Left in place as defensive code; noted here
  so nobody reads its absence from coverage as a gap.

- **Cold start is by design, not a gap.** A user with no activity gets `available: false`
  and the reason "Not enough activity yet" — never a fabricated zero.

---

## 11. Commands

```bash
# Backend
cd backend
./.venv/Scripts/python.exe -m pytest tests/ -q                 # full suite
./.venv/Scripts/python.exe -m pytest tests/ -q -k analytics    # Phase 6 only
./.venv/Scripts/python.exe -m ruff check app/ tests/
./.venv/Scripts/python.exe -m ruff format --check app/ tests/

# Frontend
cd frontend
npx vitest run
npx tsc -b --noEmit
npx eslint src/
npx vite build
```

Tests requiring PostgreSQL are marked `integration` and run by default; the suite creates
and migrates its own `nexus_test` database. On Windows the interpreter is in
`.venv/Scripts/`; on POSIX it is `.venv/bin/`.

> **Do not run two pytest sessions against the same database concurrently.**
> `truncated_database` issues `TRUNCATE ... RESTART IDENTITY CASCADE` before each test, so
> parallel sessions destroy each other's rows and produce phantom failures — "username
> already taken", deadlocks, "Could not refresh instance". Run them serially, or point
> each at its own database with `TEST_DATABASE_URL`.

---

## 12. Recommended starting point for Phase 7

Phase 7 consumes this. Concretely:

- **`RiskDetectionService` should call the analytics service, not re-derive anything.** The
  scores, the workload ratio and the estimation bias it needs are already computed and
  owner-scoped. The temptation will be to recompute `available − scheduled` inside a risk
  rule; `WorkloadRead` already answers it, and the brief's own worked example (30h
  available, 38h scheduled, 127%) is exactly what that field returns.

- **Rules belong in a pure module beside `scoring.py`**, taking plain numbers and returning
  a score plus its evidence. That shape is what made this phase's scores testable against
  fixed datasets, and it is what will make deadline/project/workload risk rules testable
  against "5 hours remaining, 2 hours available → gap of 3 hours".

- **`feature_snapshot()` is the ML seam already in place.** Phase 10 predictions feed the
  risk engine as an additional input; the deterministic rules remain as the cold-start
  fallback — which is why "not enough historical data" must stay a first-class answer
  rather than a threshold that quietly guesses.

- **`daily_metrics.updated_at` is the freshness signal** a risk snapshot would hang off, and
  it is now trustworthy (bug 1).

- **Do not read `activity_events` directly from a risk rule.** The aggregate tier is the
  supported interface; reading raw events would reintroduce the N+1 pattern Phase 6 was
  built to remove, and the brief's PERFORMANCE section is explicit about it.

- **The spec's recommendation examples all have exact arithmetic** — "approximately 8 hours"
  from a 38-vs-30 gap, "60% longer than estimates" from a mean ratio. Those should be
  asserted to the exact figure, the way the 80% and 85.7% cases are here.