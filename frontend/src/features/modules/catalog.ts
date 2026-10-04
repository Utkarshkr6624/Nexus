import {
  BarChart3,
  BrainCircuit,
  Briefcase,
  CalendarDays,
  Code2,
  FlaskConical,
  FolderKanban,
  GraduationCap,
  LayoutDashboard,
  Lightbulb,
  ListTodo,
  Search,
  Settings,
  ShieldAlert,
  Sparkles,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

/**
 * Every destination in the product is declared once, here. The sidebar, the
 * command palette, the breadcrumbs and the placeholder page bodies all render
 * from this registry, so adding a module is a single-file change.
 */

export interface ModuleCapability {
  title: string
  description: string
}

export interface ModuleMetric {
  label: string
  hint: string
}

export interface ModuleDefinition {
  /** Route path, unique and stable. */
  to: string
  label: string
  /** Page-header copy. Specific to the module, never generic. */
  summary: string
  /** Long-form explanation of what ships when the module is built. */
  vision: string
  phase: number
  icon: LucideIcon
  capabilities: ModuleCapability[]
  metrics: ModuleMetric[]
  /** Extra search terms for the command palette. */
  keywords: string[]
}

export interface NavGroup {
  id: string
  label: string
  items: ModuleDefinition[]
}

export const MODULES: ModuleDefinition[] = [
  {
    to: '/dashboard',
    label: 'Dashboard',
    summary:
      'Your cross-module briefing: what needs attention today, which surfaces are live, and the state of the platform underneath.',
    vision:
      'The dashboard and Settings are the modules that ship in Phase 1, and the dashboard stays deliberately thin until the modules around it have real data to report. It reads live service health from the backend today and, since the account system landed, the signed-in identity; once Projects, Tasks and Planner exist it becomes the daily triage surface, surfacing overdue work, stale notes and the decisions waiting on you.',
    phase: 1,
    icon: LayoutDashboard,
    keywords: ['home', 'overview', 'today', 'briefing'],
    capabilities: [
      {
        title: 'Cross-module briefing',
        description:
          'One view that ranks what needs a decision rather than repeating every list in the product.',
      },
      {
        title: 'Live service health',
        description: 'Real status, version and database latency read from GET /api/v1/health.',
      },
      {
        title: 'Surface readiness',
        description:
          'Honest per-module readiness so you always know what is live and what is still planned.',
      },
    ],
    metrics: [
      { label: 'Live modules', hint: 'Counted from the module registry' },
      { label: 'Planned modules', hint: 'Scheduled across Phases 2 to 10' },
      { label: 'Open decisions', hint: 'Requires the Decision module' },
      { label: 'Focus hours', hint: 'Requires Planner time blocks' },
    ],
  },
  {
    to: '/projects',
    label: 'Projects',
    summary: 'Long-running efforts with their own goals, milestones, files and decision history.',
    vision:
      'Projects give NEXUS a place to hold work that outlives a single task: an outcome, a health signal, linked notes and the decisions that shaped the direction. Each project tracks momentum without pretending to be a delivery methodology — the goal is context you can return to, not ceremony.',
    phase: 2,
    icon: FolderKanban,
    keywords: ['work', 'initiatives', 'portfolio', 'spaces'],
    capabilities: [
      {
        title: 'Outcome and scope',
        description:
          'A stated outcome, a working definition of done and a health signal per project.',
      },
      {
        title: 'Linked records',
        description:
          'Tasks, knowledge entries and decisions attach to the project that spawned them.',
      },
      {
        title: 'Decision history',
        description: 'Every call made inside a project is captured with its reasoning and date.',
      },
    ],
    metrics: [
      { label: 'Active projects', hint: 'Requires project records' },
      { label: 'At risk', hint: 'Requires health signals' },
      { label: 'Shipped this quarter', hint: 'Requires outcome tracking' },
    ],
  },
  {
    to: '/tasks',
    label: 'Tasks',
    summary:
      'The execution layer: individual commitments with owners, due dates and links to the project or goal they serve.',
    vision:
      'Tasks are deliberately small and boring — capture fast, triage deliberately. Each one can be attached to a project, a goal or a knowledge entry, which is what lets the analytics module later answer "where did the time actually go" without you maintaining a spreadsheet alongside the product.',
    phase: 2,
    icon: ListTodo,
    keywords: ['todo', 'work', 'actions', 'checklist'],
    capabilities: [
      {
        title: 'Fast capture',
        description: 'Add a task in one line, then enrich it with links when the context arrives.',
      },
      {
        title: 'Triage views',
        description:
          'Filter by due date, project, status and energy cost without changing the underlying data.',
      },
      {
        title: 'Effort tracking',
        description:
          'Optional effort estimates feed the analytics module rather than replacing it.',
      },
    ],
    metrics: [
      { label: 'Open tasks', hint: 'Requires task records' },
      { label: 'Due this week', hint: 'Requires due dates' },
      { label: 'Completed this week', hint: 'Requires completion events' },
    ],
  },
  {
    to: '/planner',
    label: 'Planner',
    summary:
      'Time blocking across real weeks: what you intend to work on, for how long, and what it displaces.',
    vision:
      'The planner answers a question task lists cannot: not "what is left" but "what will this week actually contain". Blocks are laid against a weekly capacity you control, conflicts are surfaced explicitly, and completed focus time is the raw signal the analytics module will later interpret.',
    phase: 3,
    icon: CalendarDays,
    keywords: ['calendar', 'schedule', 'week', 'time', 'blocks'],
    capabilities: [
      {
        title: 'Weekly capacity',
        description: 'A capacity you set, so over-committed weeks are visible before they happen.',
      },
      {
        title: 'Time blocks',
        description: 'Drag work into blocks and see conflicts before they become a broken week.',
      },
      {
        title: 'Focus log',
        description: 'Finished blocks record real elapsed time for later analysis.',
      },
    ],
    metrics: [
      { label: 'Blocks this week', hint: 'Requires planner blocks' },
      { label: 'Capacity used', hint: 'Requires a weekly capacity setting' },
      { label: 'Focus hours logged', hint: 'Requires completed blocks' },
    ],
  },
  {
    to: '/knowledge',
    label: 'Knowledge',
    summary:
      'A durable personal knowledge base: notes, references and claims that other modules can cite.',
    vision:
      'Knowledge is the substrate the rest of NEXUS reasons over. Notes are written once, linked to projects and decisions, and stay quotable so the assistant and analytics modules can point back at your own words instead of a generic summary. Structure stays lightweight: a good link graph beats a rigid hierarchy.',
    phase: 4,
    icon: BrainCircuit,
    keywords: ['notes', 'wiki', 'writing', 'vault'],
    capabilities: [
      {
        title: 'Linked notes',
        description: 'Bidirectional links between notes, projects, tasks and decisions.',
      },
      {
        title: 'Citations',
        description: 'Other modules reference a note directly instead of paraphrasing it.',
      },
      {
        title: 'Retrieval index',
        description:
          'Notes are chunked and indexed so search and the assistant stay grounded in your text.',
      },
    ],
    metrics: [
      { label: 'Notes', hint: 'Requires note records' },
      { label: 'Linked notes', hint: 'Requires the link graph' },
      { label: 'Indexed passages', hint: 'Requires the retrieval index' },
    ],
  },
  {
    to: '/search',
    label: 'Search',
    summary:
      'One query across every surface you own — projects, tasks, notes, decisions and career history.',
    vision:
      'Search is the retrieval front door for the whole platform. A single ranked result set spans structured records and indexed prose, with filters per source and a keyboard-first interface so finding something is faster than remembering where you filed it. The command palette in the top bar is its navigational sibling.',
    phase: 4,
    icon: Search,
    keywords: ['find', 'query', 'lookup', 'global'],
    capabilities: [
      {
        title: 'Unified results',
        description: 'Structured records and prose passages ranked into one list.',
      },
      {
        title: 'Source filters',
        description: 'Narrow to projects, tasks or notes without rewriting the query.',
      },
      {
        title: 'Keyboard first',
        description: 'Open, query, filter and jump without leaving the home row.',
      },
    ],
    metrics: [
      { label: 'Indexed sources', hint: 'Requires the search index' },
      { label: 'Queries this month', hint: 'Requires query logging' },
      { label: 'Result latency', hint: 'Measured once the index is live' },
    ],
  },
  {
    to: '/analytics',
    label: 'Analytics',
    summary:
      'Where your time and attention actually went, computed from data you produced rather than guessed at.',
    vision:
      'Analytics only earns trust by being derived from real records: completion events, focus blocks, decision timestamps and knowledge growth. Every figure links back to the records behind it, and every metric states its own limitations. Nothing here is decorative — if the underlying data does not exist yet, the module says so instead of drawing a chart.',
    phase: 5,
    icon: BarChart3,
    keywords: ['metrics', 'insights', 'reports', 'trends', 'stats'],
    capabilities: [
      {
        title: 'Traceable metrics',
        description: 'Every aggregate links to the records that produced it.',
      },
      {
        title: 'Attention over time',
        description: 'Focus blocks and completions combined into an honest picture of capacity.',
      },
      {
        title: 'Stated limits',
        description: 'Each chart declares its sample size and known blind spots.',
      },
    ],
    metrics: [
      { label: 'Tasks completed', hint: 'Requires completion events' },
      { label: 'Focus hours', hint: 'Requires planner blocks' },
      { label: 'Knowledge growth', hint: 'Requires note history' },
    ],
  },
  {
    to: '/developer',
    label: 'Developer',
    summary:
      'What your local git repositories have recorded: commits per day, branches, changed lines and the language git can see — read with the git CLI on the machine NEXUS runs on, with no hosted account to connect.',
    vision:
      'The developer surface reads local work trees and reports the evidence, and it is deliberate that it reports nothing else. A commit timestamp proves that work happened at an instant and nothing about how long anyone was at it, so there is no hours figure, no focus score and no productivity verdict anywhere on this surface — the metrics are counts of commits, of days that carried a commit, and of lines added and removed, and each one states the arithmetic it was built from. Every scan is recorded whatever its outcome: a repository git cannot read becomes a row carrying a sentence, never an exception that takes the page down, because one unreadable directory must not be able to break the product. Nothing re-reads a repository on its own, so every figure is as of a named scan and the page says how old that is rather than presenting it as current.',
    phase: 8,
    icon: Code2,
    keywords: ['git', 'code', 'repos', 'engineering', 'commits'],
    capabilities: [
      {
        title: 'Local repository scan',
        description:
          'Reads git history from disk with the git CLI; no hosted account, token or pull-request forge involved.',
      },
      {
        title: 'Recorded activity over a window',
        description:
          'Commits, active days and changed lines bucketed by day, week or month, with quiet days plotted as zeros rather than skipped.',
      },
      {
        title: 'Eight explained metrics',
        description:
          'Each one states how it is computed, repeats itself with the figures behind it, and says why it could not be measured rather than reporting zero.',
      },
    ],
    metrics: [
      { label: 'Repositories', hint: 'Registered local work trees, each validated as a git work tree' },
      { label: 'Commits (30d)', hint: 'Commits git recorded inside the window' },
      { label: 'Days with a commit', hint: 'Distinct days carrying at least one commit — not hours spent' },
    ],
  },
  {
    to: '/risks',
    label: 'Risk Center',
    summary:
      'Conditions the detection engine has found in your recorded work — deadlines without booked time, planned load beyond the hours available — each with the evidence it was scored from.',
    vision:
      'The Risk Center is the surface where a pattern becomes a question you can answer. Six detectors run over the analytics Phase 6 already computes and over the planner signals the workload needs, and each one that finds a gap writes a single finding rather than a stream of them: the same condition re-detected is refreshed in place, a condition that has disappeared is closed, and a detector with too little history to judge produces no row at all and says so. Every finding carries its score, its band, the inputs that produced the score and the sample they came from, because a number a person cannot check is a number they have to take on trust. The three answers — acknowledge, resolve, dismiss — are deliberately not graded, because none of them is destructive and a layout that made "Dismiss" look dangerous would push people towards the wrong one.',
    phase: 7,
    icon: ShieldAlert,
    keywords: ['risk', 'detection', 'signals', 'pressure', 'deadlines', 'workload', 'warnings'],
    capabilities: [
      {
        title: 'Findings with their evidence',
        description:
          'Every row states the score, the band, and each input that moved it, so the finding can be argued with.',
      },
      {
        title: 'Band and type filters',
        description:
          'Narrow by severity, lifecycle status or which of the six detectors raised it; the view lives in the URL.',
      },
      {
        title: 'Three honest answers',
        description:
          'Acknowledge keeps it live but quiet, resolve closes it, dismiss says it does not apply. None is destructive.',
      },
    ],
    metrics: [
      { label: 'Live risks', hint: 'Active and acknowledged findings' },
      { label: 'High or critical', hint: 'Raised above the severity threshold' },
      { label: 'Resolved this week', hint: 'Requires the detection run history' },
    ],
  },
  {
    to: '/recommendations',
    label: 'Recommendations',
    summary:
      'Actions the risks have raised, each with the reason it was proposed and the choice to accept it, complete it or set it aside.',
    vision:
      'Recommendations are the half of Phase 7 a person acts on. One rule per risk type, one suggestion per entity, and each one has to carry what, why, and what would be done — an imperative with nothing behind it is not a recommendation, so the reason is enforced in the schema rather than left to the next rule that forgets it. The engine proposes and never performs: accepting a suggestion records that the user said they would, and it is the user who reschedules the task or blocks the time. Every answer is kept, including a decline, because a risk worth surfacing is a risk worth acting on by declining it — and a suggestion declined once may legitimately be raised again if the condition still holds.',
    phase: 7,
    icon: Lightbulb,
    keywords: ['suggestions', 'actions', 'next steps', 'advice', 'proposals'],
    capabilities: [
      {
        title: 'WHAT / WHY / ACTION',
        description:
          'Every suggestion states the ask, the reason with its numbers, and the action being proposed.',
      },
      {
        title: 'Grouped by priority',
        description:
          'Priority is derived from the risk severity server-side, so the ordering cannot disagree with the finding behind it.',
      },
      {
        title: 'Answers are recorded',
        description:
          'Accept, complete and set-aside each store a different fact, and the suggestion closes when its risk does.',
      },
    ],
    metrics: [
      { label: 'Awaiting an answer', hint: 'Open suggestions by priority' },
      { label: 'Accepted', hint: 'Taken on as work to do' },
      { label: 'Completed', hint: 'Acted on and closed' },
    ],
  },
  {
    to: '/learning',
    label: 'Learning',
    summary:
      'Goals, skills and the learning activity you record against them — with every level shown beside where it came from, and nothing measured that nobody recorded.',
    vision:
      'Learning is built on one refusal: NEXUS does not know how good you are at something, so it never says. A skill level is either one you set or one NEXUS estimated from recorded activities, and the badge beside the number says which, so a claim and an inference are never mistaken for one another. The same discipline runs through the rest of the surface. Progress on a goal is your own percentage because a percentage derived from the absence of a record would be a statement about your commitment rather than about the work; a gap between your level and your target is computed on read from both and never stored, so it cannot go stale and disagree with the card beside it; and a figure that could not be measured is a dash with the backend’s own reason rather than a zero, because zero would claim something was counted that nobody counted. Everything here is a count of records you created — goals written down, skills tracked, activities logged, minutes you attached to them — and the empty states say what fills them.',
    phase: 9,
    icon: GraduationCap,
    keywords: ['study', 'courses', 'reading', 'review', 'skills'],
    capabilities: [
      {
        title: 'Goals with your own progress',
        description:
          'A title, the skill or topic it is for, an optional deadline you set, and a progress figure that is yours — NEXUS never fills one in.',
      },
      {
        title: 'Levels with their source',
        description:
          'Every skill level renders beside whether you set it or NEXUS estimated it from recorded activity, with the evidence count underneath.',
      },
      {
        title: 'Gaps with their working shown',
        description:
          'The distance to your target, computed on read, with the sentence naming both levels and the number of recorded activities behind them.',
      },
      {
        title: 'Recorded activity, never inferred',
        description:
          'Study sessions, completed tasks, notes, concepts and opened resources are counted as the separate kinds of event they are.',
      },
    ],
    metrics: [
      { label: 'Open goals', hint: 'Not completed and not archived' },
      { label: 'Tracked skills', hint: 'Each one carrying a level and its source' },
      { label: 'Activities recorded', hint: 'Learning events inside the selected window' },
    ],
  },
  {
    to: '/career',
    label: 'Career',
    summary:
      'A profile, dated records and portfolio evidence — all of it supplied by you, with a development-areas panel that reports distance and evidence and never a verdict.',
    vision:
      'Career is the slowest-moving surface in NEXUS and the one with the longest half-life, so it is also the one where an invented detail would do the most damage: a certification, an employer or a date that NEXUS made up would sit on your profile looking exactly like the ones you typed. Nothing here is written for you. The profile is your words field for field, the summary is your paragraph and is never rewritten, and every dated record and every manually added piece of evidence is stored verbatim. What NEXUS contributes is provenance rather than content: each evidence row says whether you entered it or a subsystem derived it from a record you created, repository evidence names the code events a scan recorded rather than dressing them as delivered projects, and the skill tiles pair every level with the source of that level. The development-areas panel is deliberately the dullest thing here — a distance between a level and a target you chose, with the recorded activity behind it — because that is all the records support. There is no readiness score, no employer match and no suitability claim anywhere on this surface.',
    phase: 9,
    icon: Briefcase,
    keywords: ['growth', 'goals', 'review', 'profile', 'history'],
    capabilities: [
      {
        title: 'A profile that is only yours',
        description:
          'Target role, headline, summary, location and links — stored as typed, with no generated paragraph and no inferred target.',
      },
      {
        title: 'Portfolio evidence with provenance',
        description:
          'Rows you added and rows a subsystem derived, each labelled with which, and linked to the project, skill or repository they came from.',
      },
      {
        title: 'Dated records by kind',
        description:
          'Education, work experience and certifications counted separately — a CV section is not an achievements section.',
      },
      {
        title: 'Development areas, neutrally',
        description:
          'Skills whose recorded level sits below the target you set, with the activity recorded in the window stated beside them.',
      },
    ],
    metrics: [
      { label: 'Experience records', hint: 'Roles you listed, with the dates you gave' },
      { label: 'Evidence items', hint: 'Yours and those derived, counted separately by kind' },
      { label: 'Tracked skills', hint: 'Each one carrying a level and its source' },
    ],
  },
  {
    to: '/assistant',
    label: 'AI Assistant',
    summary:
      'A voice and typed interface over NEXO’s single intent classifier: it decides which part of NEXUS you meant and names the call.',
    vision:
      'NEXO runs one model — microsoft/deberta-v3-base, a fourteen-class intent classifier trained in Phase 10 — and this surface is the voice interface for it. It hears one utterance, returns one intent with a confidence and the validated NEXUS service behind it, and the interface says exactly that. It routes rather than answers, because there is no generative model behind it: code assistance and deep reasoning both come back as needing generation, and NEXO reports that rather than improvising. Recognition comes from the browser’s own Web Speech API, which adds no model NEXO does not already run, and each request is classified alone — a single-utterance classifier has no use for a history and no business being sent one.',
    phase: 12,
    icon: Sparkles,
    keywords: ['ai', 'assistant', 'ask', 'copilot', 'llm'],
    capabilities: [
      {
        title: 'Speak or type one request',
        description:
          'Chrome and Edge supply the microphone, Firefox does not, and typing works everywhere — so voice is never the only way in.',
      },
      {
        title: 'A named destination',
        description:
          'An accepted intent names the service and the call it would make — TaskService.list — so the decision is visible before anything runs.',
      },
      {
        title: 'Gaps reported as gaps',
        description:
          'Out-of-scope requests and the two generation-only intents are reported as what they are. NEXO runs no generative model and does not pretend to.',
      },
    ],
    metrics: [
      { label: 'Turns in this session', hint: 'Kept in your browser, never sent as history' },
      { label: 'Intents', hint: 'Fourteen classes, from Phase 10 training' },
      { label: 'Routing threshold', hint: 'Below it the classifier abstains' },
    ],
  },
  {
    to: '/experiments',
    label: 'Experiments',
    summary:
      'Ship small, measure honestly, and keep or kill the idea on evidence rather than on enthusiasm.',
    vision:
      'Experiments holds the ideas that are not yet commitments: a hypothesis, a bounded build, a success metric and a decision at the end. The module makes killing a failed experiment a first-class outcome, so the backlog stays small and the kept ideas carry the evidence that justified them.',
    phase: 10,
    icon: FlaskConical,
    keywords: ['experiments', 'ideas', 'hypothesis', 'validate', 'labs'],
    capabilities: [
      {
        title: 'Hypothesis and metric',
        description: 'Each experiment states what it expects to move and by how much.',
      },
      {
        title: 'Bounded scope',
        description: 'An explicit cap keeps exploratory work from leaking into committed work.',
      },
      {
        title: 'Keep or kill',
        description:
          'A recorded verdict either promotes the idea or archives it with its evidence.',
      },
    ],
    metrics: [
      { label: 'Running experiments', hint: 'Requires experiment records' },
      { label: 'Kept', hint: 'Requires recorded verdicts' },
      { label: 'Killed', hint: 'Requires recorded verdicts' },
    ],
  },
]

export const SETTINGS_MODULE: ModuleDefinition = {
  to: '/settings',
  label: 'Settings',
  summary: 'Your account, appearance and the platform preferences that apply everywhere.',
  vision:
    'Settings is the account surface. Identity, credentials and sessions are served from the backend, so they are the same everywhere and survive a new browser; appearance and layout preferences are device-local and stored in this browser alone. Anything that needs a stored record NEXUS does not have yet is stated as missing rather than stubbed with a control that would do nothing.',
  phase: 1,
  icon: Settings,
  keywords: ['preferences', 'account', 'profile', 'theme', 'configuration'],
  capabilities: [
    {
      title: 'Account (live)',
      description:
        'Profile, password, active sessions and account deletion, all served by the backend.',
    },
    {
      title: 'Appearance (live)',
      description: 'Light, dark or system theme, plus layout and motion. Browser-local.',
    },
    {
      title: 'Notification and retention',
      description:
        'These arrive with the modules that own the underlying records and event stream.',
    },
  ],
  metrics: [
    { label: 'Theme', hint: 'Stored in this browser' },
    { label: 'Identity', hint: 'Stored on the backend' },
    { label: 'Notifications', hint: 'Requires a notification stream' },
  ],
}

export interface NavGroupDefinition {
  id: string
  label: string
  items: ModuleDefinition[]
}

/**
 * Resolves a module by route path. Throws on an unknown path, which can only
 * happen if a route and the registry have drifted apart.
 */
export function getModule(path: string): ModuleDefinition {
  const match = MODULES.find((module) => module.to === path)
  if (!match) throw new Error(`Unknown module path: ${path}`)
  return match
}

export const NAV_GROUPS: NavGroupDefinition[] = [
  { id: 'overview', label: 'Overview', items: [getModule('/dashboard')] },
  {
    id: 'work',
    label: 'Work',
    items: [getModule('/projects'), getModule('/tasks'), getModule('/planner')],
  },
  {
    id: 'intelligence',
    label: 'Intelligence',
    // The Risk Center sits with Analytics rather than with Work because it reads
    // from the analytics the engine already computes: the findings are a
    // conclusion drawn from measured work, and putting the two apart would split
    // a single question across two sidebar groups.
    items: [
      getModule('/knowledge'),
      getModule('/analytics'),
      getModule('/risks'),
      getModule('/recommendations'),
      getModule('/search'),
    ],
  },
  {
    id: 'growth',
    label: 'Growth',
    items: [getModule('/developer'), getModule('/learning'), getModule('/career')],
  },
  {
    id: 'platform',
    label: 'Platform',
    items: [getModule('/assistant'), getModule('/experiments')],
  },
]

/** Flat, ordered list used by the command palette. */
export const ALL_NAV_ITEMS: ModuleDefinition[] = [
  ...NAV_GROUPS.flatMap((group) => group.items),
  SETTINGS_MODULE,
]

export function findModule(path: string): ModuleDefinition | undefined {
  const exact = ALL_NAV_ITEMS.find((module) => module.to === path)
  if (exact) return exact
  // Nested routes carry an id: `/projects/:id`, `/knowledge/notes/:noteId`.
  // Resolving to the longest registered prefix keeps the breadcrumb and the
  // active-nav highlight correct on a detail page instead of falling through to
  // "Not found". The separator check stops `/projects` matching `/projectsfoo`.
  return ALL_NAV_ITEMS.filter(
    (module) => path.startsWith(`${module.to}/`),
  ).sort((a, b) => b.to.length - a.to.length)[0]
}
