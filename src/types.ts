// The JSON shapes web.rs emits. Hand-written against payload(), detail() and code().

/** web.rs pre_json: null, or the pre-review's timestamp and whether the PR moved since. */
export type Pre = { at: number; moved: boolean } | null

/** One row of the board, as payload() builds it. The client adds uid/section/older via visible(). */
export type Pr = {
  url: string
  number: number
  title: string
  repo: string
  author: string
  updatedAt: string
  isDraft: boolean
  status: string
  prev: string
  checks: string
  reviewers: string
  /** the newest review's summary line, "" when none. */
  review: string
  busy: boolean
  since?: number
  team: string
  summary: string
  reviewAt: string
  pre: Pre
}

export type Section = { name: string; prs: Pr[]; error: string }

/** A PR with the client-side fields visible()/group() add. */
export type Row = Pr & { uid: string; section: string; older: Row[] }

export type Team = { key: string; name: string; arrived: number }

export type Options = {
  model: string[]
  depth: string[]
  effort: string[]
  voice: string[]
  hunter: string[]
  subs: string[]
  window: (number | null)[]
  interval: number[]
  theme: string[]
}

export type Knowledge = {
  memory: string
  store: string
  teams: Team[]
  teamError: string
  notes: string[]
}

export type Ask = { kind: string; key: string; name: string; waiting?: string; text?: string; path?: string }

/** config::snapshot, only the keys the UI reads. */
export type Settings = {
  window?: number | null
  drafts?: boolean
  subs?: string
  theme?: string
  notify?: boolean
  model?: string
  depth?: string
  effort?: string
  voice?: string[]
  hunter?: string[]
  interval?: number
  [key: string]: unknown
}

export type StateData = {
  version: string
  sections: Section[]
  fetchedAt: number | null
  interval: number
  fetching: boolean
  error: string
  auto: boolean
  pending: number
  model: string
  running: number
  update: string
  settings: Settings
  options: Options
  knowledge: Knowledge
  asks: Ask[]
  notices: string[]
}

export type Finding = { kind: string; text: string; loc?: string }

export type Check = { name: string; state: string }

export type Review = {
  verdict: string
  summary: string
  model: string
  tag: string
  at: string
  findings: Finding[]
  text: string
}

/** detail(): the side pane's frame for one PR. */
export type Detail = {
  url: string
  pending: boolean
  branch: string
  add?: number
  del?: number
  files?: number
  checks: Check[]
  brief: { whose: string; empty: boolean }
  pre: Pre
  review: Review | null
}

/** code_rows(): the diff as a flat list. */
export type CodeRow =
  | { kind: 'file'; path: string; add: number; dele: number }
  | { kind: 'hunk'; header: string }
  | { kind: 'line'; n?: number; sign: string; text: string; del?: unknown; mark: string }
  | { kind: 'note'; mark: string; text: string }
  | { kind: 'orphan'; mark: string; text: string; loc: string; why: string }
  | { kind: 'gap' }

export type Code = { url: string; pending: boolean; rows: CodeRow[]; empty?: string }

export type Drafts = { promoteAt: number; items: { repo: string | null; n: number; fact: string; kind: string; team: string }[] }
