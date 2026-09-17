// Dev-only mock of the Rust server's /api/* surface (src-tauri/src/web.rs), so `npm run dev` works
// with no backend. Interactive: mutations change in-memory state roughly like the real handlers. Not modelled:
// the drafts recurrence gate (promote just removes the draft) and the Host/token guard.
// Active when no backend answers on :7777; force with DASHY_MOCK=1, disable with DASHY_MOCK=0.
// DASHY_MOCK_N=400 adds that many synthetic PRs, to try the graph on a big board.
import { spawn } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import { get as httpGet } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Plugin } from 'vite'

const TARGET = 'http://127.0.0.1:7777'

const THEMES = ['pencil', 'dashy', 'dracula', 'gruvbox', 'nord']
// several of each kind, so the sources rows are worth grouping in dev too
const SCOPES = [
  'team:teamdashy',
  'team:acme-guild',
  'team:platform',
  'org:acme',
  'org:a',
  'org:other',
  'org:infra',
]
const MODELS = ['opus', 'sonnet', 'fable']
const DEPTHS = ['adaptive', 'low', 'medium', 'high']
const EFFORTS = ['', 'low', 'medium', 'high', 'xhigh', 'max']
const VOICES = ['review', 'caveman', 'bot']
const HUNTERS = ['ponytail', 'security', 'tests', 'perf', 'humanizer']
const SUBS = ['all', 'open', 'off']
const INTERVALS = [60, 120, 300, 600, 900]
const WINDOWS = [1, 3, 6, 24, 168, 720, null]
const PROMOTE_AT = 2

type Pre = { at: number; moved: boolean } | null
type Finding = { kind: string; loc?: string; text: string }
type Verdict = 'approve' | 'request_changes' | 'comment'
type ReviewInfo = { verdict: Verdict; summary: string; body: string; findings: Finding[] }

type Row = {
  section: string
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
  busy: boolean
  since?: number
  pre: Pre
  waiting?: boolean
}

const T0 = Date.now()
const iso = (hoursAgo: number) => new Date(T0 - hoursAgo * 3600e3).toISOString()
const secs = () => Date.now() / 1000
const VT: Record<Verdict, string> = { approve: '✓ approved', request_changes: '✗ changes requested', comment: '~ commented' }

function mkPr(n: number, title: string, repo: string, author: string, hours: number, section: string, draft = false): Row {
  return {
    section,
    url: `https://github.com/${repo}/pull/${n}`,
    number: n,
    title,
    repo,
    author,
    updatedAt: iso(hours),
    isDraft: draft,
    status: '',
    prev: '',
    checks: '',
    reviewers: '',
    busy: false,
    pre: null,
  }
}

const S = {
  follow: [{ login: 'frank' }, { login: 'alice' }] as { login: string }[],
  storyCalls: {} as Record<string, number>,
  rows: [] as Row[],
  reviewText: {} as Record<string, string>,
  reviewInfo: {} as Record<string, ReviewInfo>,
  reviewAt: {} as Record<string, string>,
  binding: {} as Record<string, string>,
  drafts: [] as { repo: string | null; n: number; kind: string; fact: string }[],
  shares: [] as { repo: string | null; fact: string; sent: boolean; backers: string[] }[],
  teams: [{ key: 'acme', name: 'Acme Guild', description: 'everything under acme/*', checkout: '~/.prs_teams/acme', remote: 'github.com/acme/guild' }],
  settings: {
    theme: 'pencil',
    notify: false,
    hinted: true,
    keyhints: true,
    subs: 'all',
    model: 'opus',
    depth: 'adaptive',
    effort: '',
    voice: ['review'],
    hunter: [],
    interval: 300,
    window: null as number | null,
    drafts: true,
  } as Record<string, unknown>,
  auto: false,
  fetching: false,
  fetchedAt: secs(),
  notices: [] as string[],
  reportAt: 0,
  reportLatest: null as string | null,
  // shown once per dev server start, as after an update; closing it clears it like /api/changelog does
  changelog: "v2.21.0\n\n## What's Changed\n* feat: release notes after an update\n* fix: the refresh button shows a spinner",
  asks: [] as { kind: string; key: string; name: string; waiting?: string; text?: string; path?: string }[],
  // what a "no" left held back, so the rail's consent rows and its asks count are reachable in dev
  refused: [] as { kind: string; key: string; what: string }[],
  // what happens to each repo's reviews, and the ones that finished and are waiting for a key
  // one axis at a time, like the store: a repo can set `auto` and inherit `manual` from its owner
  posting: {} as Record<string, { manual?: string; auto?: string }>,
  // owner rules live apart from repo rules, so `o` has something to flip that a repo row can carve
  postingOwners: {} as Record<string, { manual?: string; auto?: string }>,
  /** Owners switched to each repo on its own; their rule stays as the fallback. */
  perRepo: {} as Record<string, boolean>,
  held: {} as Record<string, MockTalk & { verdict: string; summary: string; body: string; at: number; moved?: boolean }>,
  /** Pre-reviews by PR url: their text, and the conversation beside it. */
  pres: {} as Record<string, MockTalk & { verdict: string; text: string }>,
  refreshes: 0,
  ticks: 0,
  cursor: 0,
  overlaps: { running: false, t0: 0, error: '', result: null as unknown[] | null, idle: true },
  dream: { running: false, t0: 0, error: '', result: null as unknown | null, idle: true },
}

const memoryText: Record<string, string> = {
  general: '# general\n\n- prefer small PRs\n- always rebase, never merge main\n- run make lint before flagging style\n',
  'acme/api': '# acme/api\n\n- run make lint before flagging style\n- uses tabs\n- old CI on jenkins, ignore\n',
  'acme/web': '# acme/web\n\n- session middleware is shared with the admin app\n',
  // a team's files, keyed "<team>:<repo>"
  'acme:general': '- verify claims against the pushed head, not the PR body\n',
  'acme:acme/api': '- the retry client owns backoff; callers never sleep\n',
  'acme:doc:brief': '# What we are building\n\nA billing platform for small clinics. Reviews should care most about money paths.\n',
  'acme:doc:agents': '# For agent sessions\n\nFile what you work out with `gitdashy remember`.\n',
}

/** A conversation about a saved review, as the mock keeps it: the same for a held review and a pre-review. */
type MockTalk = {
  model: string
  instructions?: string
  thread?: { who: string; text: string; at: number }[]
  proposed?: { verdict: string; summary: string; body: string } | null
  busyUntil?: number
}

/** The `talk` object the server sends, from what the mock keeps. */
function talkView(t: MockTalk) {
  return {
    instructions: t.instructions || '',
    thread: t.thread || [],
    proposed: t.proposed || null,
    // like the server: only a claude-CLI review with a saved session can be discussed
    cannotDiscuss: t.model.includes(':') ? `discussion needs the claude CLI; this review ran on ${t.model}` : '',
    busy: (t.busyUntil || 0) > Date.now(),
  }
}

/** discuss / revise / accept / keep, like web.rs `talk`. `accept` hands the revision to whoever stores it. */
function talkOp(t: MockTalk, op: string, b: Body, accept: (v: { verdict: string; summary: string; body: string }) => void) {
  if ((t.busyUntil || 0) > Date.now()) return json(409, { error: 'the agent is still working on this review' })
  t.thread = t.thread || []
  if (op === 'accept') {
    if (!t.proposed) return json(409, { error: 'no revision is waiting' })
    accept(t.proposed)
    t.proposed = null
    return json(200, { ok: true })
  }
  if (op === 'keep') {
    t.proposed = null
    return json(200, { ok: true })
  }
  if (t.model.includes(':')) return json(409, { error: `discussion needs the claude CLI; this review ran on ${t.model}` })
  if (op === 'discuss') {
    const text = str(b, 'text').trim()
    if (!text) return json(400, { error: 'say something' })
    t.thread.push({ who: 'you', text, at: secs() })
    // a turn takes a moment, so the polling and the disabled controls are reachable in dev
    t.busyUntil = Date.now() + 2500
    setTimeout(() => {
      t.thread!.push({ who: 'agent', text: `Looking again: the caller at export.py:88 already guards that, so the finding is weaker than I made it. (mock reply to: "${text}")`, at: secs() })
    }, 2400)
    return json(200, { ok: true })
  }
  t.busyUntil = Date.now() + 2500
  setTimeout(() => {
    t.proposed = { verdict: 'comment', summary: 'one question left, no blocker', body: '## Notes\n\n- the retry reads a value it wrote two lines earlier; worth a comment, not a blocker' }
  }, 2400)
  return json(200, { ok: true })
}

const SYNTH_PREFIX = ['feat: ', 'fix: ', 'chore: ', 'docs: ', '']
const SYNTH_SECTION = ['MINE', 'REVIEW REQUESTED', 'ASSIGNED', 'REVIEWED']

function seed() {
  const m1 = mkPr(101, 'Add retry to webhook client', 'acme/api', 'alice', 2, 'MINE')
  m1.status = '· awaiting review'
  m1.reviewers = '✓bob ·carol'
  m1.checks = '✓'
  m1.pre = { at: T0 / 1000 - 3600, moved: false }
  S.pres[m1.url] = {
    model: 'opus',
    verdict: 'request_changes',
    text: `# Pre-review — ${m1.repo}#${m1.number}\n\n> **Not posted.** This is the mock reviewer.\n\n**Verdict (advisory):** ✗ changes requested — mock\n\n## Findings\n\n- \`api/handlers.py:88\` the pager reads \`total\` before the guard\n`,
    thread: [],
    proposed: null,
  }
  const m2 = mkPr(98, 'WIP: migrate to pydantic v2 and drop the hand-rolled validators in the ingest and export paths', 'acme/api', 'alice', 30, 'MINE', true)
  const r1 = mkPr(212, 'Fix off-by-one in pagination', 'acme/web', 'bob', 1, 'REVIEW REQUESTED')
  r1.checks = '✗'
  r1.reviewers = '~erin ·me'
  const r2 = mkPr(207, 'Cache user lookups in session middleware', 'acme/web', 'carol', 5, 'REVIEW REQUESTED')
  r2.checks = '●'
  const r3 = mkPr(55, 'Rotate signing keys and bump KMS alias', 'acme/infra', 'dave', 48, 'REVIEW REQUESTED')
  const a1 = mkPr(300, 'Flaky integration test in CI', 'acme/api', 'erin', 72, 'ASSIGNED')
  const v1 = mkPr(180, 'Refactor auth middleware', 'acme/api', 'frank', 3, 'REVIEWED')
  const v2 = mkPr(44, 'Add S3 lifecycle rules', 'acme/infra', 'grace', 5, 'REVIEWED')
  S.rows = [m1, m2, r1, r2, r3, a1, v1, v2]
  for (let i = 0; i < Number(process.env.DASHY_MOCK_N || 0); i++) {
    const title = SYNTH_PREFIX[i % SYNTH_PREFIX.length] + 'synthetic change number ' + i
    S.rows.push(mkPr(1000 + i, title, `acme/r${i % 25}`, `dev${(i * 7) % 40}`, i % 90, SYNTH_SECTION[i % SYNTH_SECTION.length]))
  }
  S.binding = { 'acme/api': 'acme', 'acme/web': 'acme', 'acme/infra': 'acme' }
  S.reviewInfo[v1.url] = {
    verdict: 'approve',
    summary: 'Splits auth middleware into token parsing and policy checks.',
    body: 'LGTM. Clean split, existing tests still cover both paths.',
    findings: [
      { kind: 'note', loc: 'api/auth.py:40', text: 'policy.check now runs on the parsed user; the old order is gone' },
      { kind: 'nit', loc: 'api/handlers.py:12', text: 'the retry helper is unused after this change' },
    ],
  }
  S.reviewInfo[v2.url] = {
    verdict: 'request_changes',
    summary: 'Expires logs after 30 days, moves backups to Glacier.',
    body: '- `infra/s3.tf:31` rule also matches the `backups/` prefix, would delete backups after 30d\n- no plan output attached',
    findings: [{ kind: 'blocking', loc: 'infra/s3.tf:31', text: 'rule also matches the backups/ prefix' }],
  }
  for (const v of [v1, v2]) {
    S.reviewText[v.url] = VT[S.reviewInfo[v.url].verdict]
    S.reviewAt[v.url] = v.updatedAt
  }
  S.drafts = [
    { repo: 'acme/api', n: 1, kind: 'self', fact: 'old CI on jenkins, ignore' },
    { repo: null, n: 2, kind: 'review', fact: 'always rebase, never merge main' },
    // yours in team acme's draft pool: a teammate's review finding the same moves it into the team's knowledge
    { repo: 'acme/api', n: 1, kind: 'team', fact: 'the webhook client retries with backoff, callers never sleep' },
  ]
  S.shares = [
    { repo: 'acme/api', fact: 'run make lint before flagging style', sent: true, backers: ['alice'] },
    { repo: 'acme/api', fact: 'uses tabs', sent: false, backers: ['alice', 'bob'] },
    { repo: null, fact: 'prefer small PRs', sent: false, backers: [] },
  ]
  S.asks = [{ kind: 'publishing', key: 'acme', name: 'Acme Guild', waiting: '2 drafts · 1 fact' }]
  // both cases of the posting panel: acme is one setting for all its repos, tools is set per repo
  S.postingOwners['acme'] = { manual: 'hold', auto: 'hold' }
  // and a rule left on acme/web from before the switch existed, which beats the owner: the panel must show it
  S.posting['acme/web'] = { auto: 'post' }
  S.rows.push(mkPr(61, 'Add --json output to the status command', 'tools/cli', 'hana', 6, 'REVIEW REQUESTED'))
  S.rows.push(mkPr(14, 'Document the release checklist', 'tools/docs', 'ivan', 20, 'REVIEW REQUESTED'))
  S.posting['tools/cli'] = { manual: 'hold', auto: 'hold' }
  S.posting['tools/docs'] = { manual: 'post', auto: 'hold' }
  const rr = S.rows.find((r) => r.section === 'REVIEW REQUESTED')
  if (rr) {
    rr.waiting = true
    S.held[`${rr.repo}#${rr.number}`] = {
      verdict: 'request_changes',
      summary: 'one real bug, the rest is small',
      body: '## Blocking\n\n- the export job drops the last page when the cursor is empty\n\n## Notes\n\n- the retry reads a value it wrote two lines earlier',
      model: 'opus',
      at: secs(),
      moved: true,
      instructions: 'focus on the export job; ignore style',
      // an example exchange, so the conversation is on screen before anything is typed
      thread: [
        { who: 'you', text: 'Is the empty-cursor case really blocking? The job never runs with an empty table.', at: secs() - 600 },
        { who: 'agent', text: 'It can: the nightly run starts before the import on Mondays, so the table is empty for that run. I would keep it blocking, but a guard at export.py:41 would settle it.', at: secs() - 540 },
      ],
      proposed: null,
    }
  }
}
seed()

const teamOf = (repo: string) => S.binding[repo] || ''

function buildPayload() {
  const sections = ['MINE', 'REVIEW REQUESTED', 'ASSIGNED', 'REVIEWED'].map((name) => ({
    name,
    prs: S.rows
      .filter((r) => r.section === name)
      .map((r) => {
        const reviewed = name === 'REVIEWED' && S.reviewInfo[r.url]
        return {
          url: r.url,
          number: r.number,
          title: r.title,
          repo: r.repo,
          author: r.author,
          updatedAt: r.updatedAt,
          isDraft: r.isDraft,
          // one row without sizes, as when GitHub does not say
          add: r.number === 98 ? null : 62 + (r.number % 40),
          del: r.number === 98 ? null : 14 + (r.number % 9),
          status: r.status,
          prev: r.prev,
          checks: r.checks,
          reviewers: r.reviewers,
          review: S.reviewText[r.url] || '',
          busy: r.busy,
          since: r.since,
          team: teamOf(r.repo),
          summary: reviewed ? S.reviewInfo[r.url].summary : '',
          reviewAt: reviewed ? S.reviewAt[r.url] || '' : '',
          pre: r.pre,
          waiting: !!r.waiting,
        }
      }),
    error: '',
  }))
  return {
    version: '0.0.0-mock',
    sections,
    fetchedAt: S.fetchedAt,
    interval: S.settings.interval,
    fetching: S.fetching,
    ticks: S.ticks,
    error: '',
    auto: S.auto,
    pending: S.rows.filter((r) => r.section === 'REVIEW REQUESTED').length,
    model: S.settings.model,
    running: S.rows.filter((r) => r.busy).length,
    update: '',
    settings: { ...S.settings },
    options: { model: MODELS, depth: DEPTHS, effort: EFFORTS, voice: VOICES, hunter: HUNTERS, subs: SUBS, window: WINDOWS, interval: INTERVALS, theme: THEMES, scopes: SCOPES },
    knowledge: {
      // the Friday report takes 4s here, so the row's writing state is visible in dev
      report: {
        job: S.reportAt && secs() - S.reportAt < 4 ? { running: true, elapsed: Math.round(secs() - S.reportAt) } : { running: false },
        latest: S.reportAt && secs() - S.reportAt >= 4 ? new Date().toISOString().slice(0, 10) : S.reportLatest,
      },
      memory: '~/.prs_memory',
      store: '',
      teams: S.teams.map((t) => ({ key: t.key, name: t.name, arrived: 0 })),
      teamError: '',
      notes: [],
      // the consent rows #60 added. Nothing supplied them here, so the rail's asks count and the
      // "ask again" row were not reachable in `pnpm dev` at all.
      waiting: S.refused,
    },
    asks: S.asks,
    notices: S.notices,
    postingRules: postingRules(),
    changelog: S.changelog,
  }
}

function detail(url: string) {
  const r = S.rows.find((x) => x.url === url)
  if (!r) return { error: 'no such pr' }
  const n = r.number
  const info = S.reviewInfo[url]
  return {
    url,
    pending: false,
    branch: `${r.author}/pr-${n}`,
    add: 62 + (n % 40),
    del: 14 + (n % 9),
    files: 3 + (n % 4),
    checks: [
      { name: 'unit tests', state: n === 212 ? 'err' : 'ok' },
      { name: 'typecheck', state: 'ok' },
      { name: 'lint', state: n === 207 ? 'run' : 'ok' },
      { name: 'preview deploy', state: 'ok' },
    ],
    brief: { whose: teamOf(r.repo), empty: false },
    pre: r.pre,
    review: info
      ? {
          verdict: S.reviewText[url],
          summary: info.summary,
          model: 'opus',
          tag: '#mock',
          at: S.reviewAt[url] || r.updatedAt,
          findings: info.findings,
          text: info.body,
        }
      : null,
  }
}

function code(url: string) {
  const info = S.reviewInfo[url]
  if (!info) return { url, pending: false, rows: [], empty: 'no review yet — r reviews this PR, p pre-reviews it' }
  const rows: unknown[] = [
    { kind: 'file', path: 'api/auth.py', add: 3, dele: 2 },
    { kind: 'hunk', header: '@@ -36,8 +36,9 @@ def middleware(request):' },
    { kind: 'line', n: 36, sign: ' ', text: "    token = request.headers.get('Authorization')", del: null, mark: '' },
    { kind: 'line', n: null, sign: '-', text: '    policy.check(request.user)', del: true, mark: '' },
    { kind: 'line', n: null, sign: '-', text: '    user = parse(token)', del: true, mark: '' },
    { kind: 'note', mark: 'note', text: 'policy.check now runs on the parsed user; the old order is gone' },
    { kind: 'line', n: 40, sign: '+', text: '    policy.check(user)', del: null, mark: 'note' },
    { kind: 'line', n: 41, sign: '+', text: '    request.user = user', del: null, mark: '' },
    { kind: 'line', n: 42, sign: ' ', text: '    return handle(request)', del: null, mark: '' },
    { kind: 'gap' },
    { kind: 'file', path: 'infra/s3.tf', add: 3, dele: 0 },
    { kind: 'hunk', header: '@@ -28,6 +28,9 @@ resource "aws_s3_bucket" "logs" {' },
    { kind: 'line', n: 28, sign: ' ', text: '  bucket = var.name', del: null, mark: '' },
    { kind: 'line', n: 29, sign: '+', text: '  lifecycle_rule {', del: null, mark: 'blocking' },
    { kind: 'line', n: 30, sign: '+', text: "    prefix  = \"\"", del: null, mark: '' },
    { kind: 'line', n: 31, sign: '+', text: '    expiration { days = 30 }', del: null, mark: 'blocking' },
    { kind: 'gap' },
    { kind: 'orphan', mark: 'nit', text: 'the retry helper is unused after this change', loc: 'api/handlers.py:12', why: 'not in this diff' },
  ]
  return { url, pending: false, rows }
}

const json = (status: number, body: unknown) => ({ status, body })

function jobShape(j: { running: boolean; t0: number; error: string; result: unknown; idle: boolean }) {
  return {
    running: j.running,
    idle: j.idle,
    elapsed: j.idle ? 0 : Math.max(0, Math.round(secs() - j.t0)),
    error: j.error,
    result: j.running || j.idle ? null : j.result,
  }
}

type Body = Record<string, unknown>
const str = (b: Body, k: string) => {
  const v = b[k]
  return v == null ? '' : String(v)
}
const bool = (b: Body, k: string) => {
  const v = b[k]
  return Array.isArray(v) ? v.length > 0 : !!v
}
const repoOf = (b: Body) => {
  const r = str(b, 'repo')
  return r || null
}

function postReview(b: Body) {
  const url = str(b, 'url')
  if (str(b, 'ask').length > 8000) return json(400, { error: 'instructions are too long' })
  const r = S.rows.find((x) => x.url === url)
  if (!r) return json(404, { error: 'no such pr' })
  if (bool(b, 'self')) {
    r.pre = { at: secs(), moved: false }
    S.pres[r.url] = { model: 'opus', verdict: 'comment', text: `# Pre-review — ${r.repo}#${r.number}\n\n> **Not posted.** This is the mock reviewer.\n\n**Verdict (advisory):** comment — mock\n`, thread: [], proposed: null }
    return json(200, { ok: true })
  }
  if (r.busy) return json(409, { error: 'already running' })
  r.busy = true
  r.since = secs()
  const verdicts: Verdict[] = ['approve', 'request_changes', 'comment']
  const v = verdicts[S.cursor++ % verdicts.length]
  setTimeout(() => {
    r.busy = false
    r.since = undefined
    S.reviewText[r.url] = VT[v]
    S.reviewAt[r.url] = new Date().toISOString()
    S.reviewInfo[r.url] = {
      verdict: v,
      summary: `Reviewed #${r.number}: ${r.title}.`,
      body: v === 'approve' ? 'LGTM.' : v === 'request_changes' ? '- `api/auth.py:40` guard the empty case' : 'One question about the rollover, see above.',
      findings: v === 'approve' ? [] : [{ kind: v === 'request_changes' ? 'blocking' : 'note', loc: 'api/auth.py:40', text: 'guard the empty case' }],
    }
    r.section = 'REVIEWED'
    if (v === 'request_changes') S.notices.push(`review of #${r.number} requested changes`)
  }, 1800)
  return json(200, { ok: true })
}

/** Six weeks of made-up learning, steady with a stall in the middle, so every view and filter has something to draw. */
function learningEvents() {
  const out: { at: number; kind: string; repo: string; team?: string; who?: string; source?: string }[] = []
  const now = secs()
  const repos = ['acme/api', 'acme/web', '']
  let seed = 7
  const rnd = (n: number) => ((seed = (seed * 9301 + 49297) % 233280), Math.floor((seed / 233280) * n))
  for (let d = 42; d >= 0; d--) {
    if (d > 20 && d < 26) continue // a quiet week
    const at = now - d * 86400
    for (let i = rnd(6); i > 0; i--) out.push({ at: at + i * 60, kind: 'draft', repo: repos[rnd(3)], who: 'alice', source: ['review', 'review', 'pre-review', 'session'][rnd(4)] })
    for (let i = rnd(3); i > 0; i--) out.push({ at: at + i * 90, kind: 'fact', repo: repos[rnd(3)], who: 'alice', source: d > 30 ? '' : ['seen twice', 'seen twice', 'hand', 'teammate'][rnd(4)] })
    for (let i = rnd(4); i > 0; i--) out.push({ at: at + i * 120, kind: 'arrival', repo: repos[rnd(2)], team: 'acme', who: ['bob', 'carol', 'alice'][rnd(3)], source: rnd(5) ? 'draft' : 'fact' })
  }
  return out
}

/** repo beats owner beats the default, the same chain the store resolves. One place, so the detail
 *  and the /api/posting route cannot disagree. */
function postingOf(repo: string) {
  const owner = repo.split('/')[0]
  const mine = S.posting[repo]
  const theirs = S.postingOwners[owner]
  const one = (ran: 'manual' | 'auto') => ({
    value: mine?.[ran] ?? theirs?.[ran] ?? 'post',
    via: mine?.[ran] ? 'repo' : theirs?.[ran] ? 'owner' : '',
    ownerValue: theirs?.[ran] ?? 'post',
  })
  return { repo, owner, manual: one('manual'), auto: one('auto') }
}

/** Every target the panel lists — every repo on the board and every ruled owner — owners first and each
 *  half sorted, the way posting_rules_json does it. */
function postingRules() {
  const by = (a: { target: string }, b: { target: string }) => a.target.localeCompare(b.target)
  const onBoard = [...new Set(S.rows.map((r) => r.repo))]
  const ownerNames = [...new Set([...Object.keys(S.postingOwners), ...onBoard.map((r) => r.split('/')[0])])]
  const owners = ownerNames
    .map((o) => {
      const r = S.postingOwners[o] || {}
      return {
        target: `${o}/*`,
        manual: r.manual || 'post',
        auto: r.auto || 'post',
        manualVia: r.manual ? 'owner' : '',
        autoVia: r.auto ? 'owner' : '',
        perRepo: !!S.perRepo[o],
      }
    })
    .sort(by)
  // like the server: a repo row shows the EFFECTIVE word, marked when it comes from the owner
  const repos = [...new Set([...Object.keys(S.posting), ...onBoard])]
    .map((t) => {
      const r = S.posting[t] || {}
      const own = S.postingOwners[t.split('/')[0]]
      const one = (axis: 'manual' | 'auto') =>
        r[axis] ? [r[axis], 'repo'] : own?.[axis] ? [own[axis], 'owner'] : ['post', '']
      const [manual, manualVia] = one('manual')
      const [auto, autoVia] = one('auto')
      return { target: t, manual, auto, manualVia, autoVia }
    })
    .sort(by)
  return [...owners, ...repos]
}

function postSettings(b: Body) {
  const s = S.settings
  for (const k of ['theme', 'notify', 'subs', 'model', 'depth', 'effort', 'voice', 'hunter', 'interval', 'window', 'drafts', 'scopes', 'read', 'hinted', 'keyhints']) {
    if (k in b) s[k] = b[k]
  }
  if ('window' in b) {
    // like the app: a new window refetches, and every section's pages take a while
    S.fetching = true
    setTimeout(() => {
      S.fetching = false
      S.fetchedAt = secs()
    }, 4000)
  }
  return json(200, { ok: true })
}

// a followed user's story; every poll after the first finds one more PR, so the "new work" pop-up shows
function story(login: string) {
  const n = (S.storyCalls[login] = (S.storyCalls[login] || 0) + 1)
  const pr = (i: number) => ({ repo: 'acme/api', number: 200 + i, title: `${login}'s change #${i}`, url: `https://github.com/acme/api/pull/${200 + i}`, head: `h${i}` })
  return {
    summary: `- ${login} is reworking auth middleware in acme/api\n- reviewing small fixes in acme/dashboard`,
    prs: Array.from({ length: n + 1 }, (_, i) => pr(i)),
    at: n === 1 ? secs() - 120 : secs(),
  }
}

function handleApi(method: string, path: string, query: URLSearchParams, body: Body) {
  if (method === 'GET') {
    if (path === '/api/state') return json(200, buildPayload())
    if (path === '/api/stories') return json(200, { follow: S.follow })
    if (path === '/api/changelog') return json(200, { text: S.changelog || 'v2.21.0\n\n(the mock has no older notes)' })
    if (path === '/api/story') return json(200, story(query.get('login') || ''))
    if (path === '/api/posting') {
      const repo = query.get('repo') || ''
      const key = `${repo}#${query.get('number') || ''}`
      const h = S.held[key]
      return json(200, {
        ...postingOf(repo),
        held: h
          ? { verdict: h.verdict, summary: h.summary, body: h.body, model: h.model, at: h.at, moved: !!h.moved, talk: talkView(h) }
          : null,
      })
    }
    if (path === '/api/asks') return json(200, { asks: S.asks })
    if (path === '/api/pr') return json(200, detail(query.get('url') || ''))
    if (path === '/api/diff') return json(200, code(query.get('url') || ''))
    if (path === '/api/memory/files') {
      const files: { team: string; repo?: string; doc?: string }[] = [{ team: '', repo: '' }, ...Object.keys(memoryText).filter((k) => !k.includes(':') && k !== 'general').map((repo) => ({ team: '', repo }))]
      for (const t of S.teams) files.push({ team: t.key, repo: '' }, ...Object.keys(memoryText).filter((k) => k.startsWith(`${t.key}:`) && k !== `${t.key}:general` && !k.endsWith(':doc:brief') && !k.endsWith(':doc:agents')).map((k) => ({ team: t.key, repo: k.slice(t.key.length + 1) })), { team: t.key, doc: 'brief' }, { team: t.key, doc: 'agents' })
      return json(200, { files })
    }
    if (path === '/api/memory') {
      const team = query.get('team') || ''
      const doc = query.get('doc') || ''
      if (doc) return json(200, { team, doc, path: `~/.prs_teams/${team}/memory/${doc === 'brief' ? 'project' : 'agents'}.md`, text: memoryText[`${team}:doc:${doc}`] || '' })
      const repo = query.get('repo') || 'general'
      const file = `${repo === 'general' ? 'general' : repo.replace('/', '__')}.md`
      const text = memoryText[team ? `${team}:${repo}` : repo] || ''
      const facts = text.split('\n').filter((l) => /^\s*[-•]/.test(l)).map((l) => l.replace(/^\s*[-•\s]+/, '').trim()).filter(Boolean)
      return json(200, { repo, team, path: team ? `~/.prs_teams/${team}/memory/${file}` : `~/.prs_memory/${file}`, facts })
    }
    if (path === '/api/learning') return json(200, { events: learningEvents() })
    if (path === '/api/drafts') return json(200, { promoteAt: PROMOTE_AT, items: S.drafts.map((d) => ({ ...d, team: d.kind === 'team' ? 'acme' : d.repo ? teamOf(d.repo) : '' })) })
    if (path === '/api/share') {
      const items = S.shares.map((s) => ({ ...s, team: s.repo ? teamOf(s.repo) : teamOf(query.get('about') || '') }))
      return json(200, { inTeam: true, items })
    }
    if (path === '/api/overlaps') {
      return json(200, jobShape(S.overlaps))
    }
    if (path === '/api/teams') {
      const key = query.get('brief')
      if (key) {
        const t = S.teams.find((x) => x.key === key)
        return t ? json(200, { key, path: `~/.prs_teams/${key}/memory/project.md`, text: `# ${t.name}\n\n${t.description}\n` }) : json(404, { error: `not in team ${key}` })
      }
      return json(200, {
        teams: S.teams.map((t) => ({
          key: t.key,
          name: t.name,
          description: t.description,
          checkout: t.checkout,
          linked: false,
          remote: t.remote,
          used: `acme/* · ${Object.values(S.binding).filter((v) => v === t.key).length} repos`,
          arrived: 0,
          undecided: [],
        })),
        error: '',
      })
      }
    if (path === '/api/bind') {
      const repo = query.get('repo') || ''
      if (!repo) return json(400, { error: 'no row selected' })
      return json(200, {
        repo,
        kind: 'repo',
        to: teamOf(repo),
        owner: repo.split('/')[0],
        teams: S.teams.map((t) => ({ key: t.key, name: t.name })),
      })
    }
    if (path === '/api/dream') {
      if (S.dream.idle) return json(200, { running: false, idle: true })
      const j = jobShape(S.dream)
      return json(200, { ...j, result: j.result ? { ...(j.result as Record<string, unknown>), new: undefined } : null })
    }
    if (path === '/api/collaborators') {
      const url = query.get('url') || ''
      const r = S.rows.find((x) => x.url === url)
      const all = ['alice', 'bob', 'carol', 'dave', 'erin']
      return json(200, { logins: all.filter((l) => l !== (r?.author || '')) })
    }
    if (path === '/api/prereview') {
      const r = S.rows.find((x) => x.url === (query.get('url') || ''))
      if (!r || !r.pre) return json(404, { error: `no pre-review of #${r?.number ?? '?'} yet — p runs one` })
      const t = S.pres[r.url]
      return json(200, {
        path: `~/.prs_reviews/${r.repo.replace('/', '__')}__${r.number}.md`,
        text: t ? t.text : `# Pre-review — ${r.repo}#${r.number}\n\n> **Not posted.** This is the mock reviewer.\n`,
        moved: r.pre.moved,
        talk: t ? { ...talkView(t), verdict: t.verdict } : null,
      })
    }
    if (path === '/api/debug') {
      const p = buildPayload()
      return json(200, {
        at: secs(),
        version: '0.0.0-mock',
        pid: 0,
        os: 'linux',
        arch: 'x64',
        debug: true,
        demo: true,
        model: S.settings.model,
        interval: S.settings.interval,
        paths: { settings: '~/.config/gitdashy', memory: '~/.prs_memory', log: '~/.prs_reviews/log.jsonl', debugLog: '~/.prs_reviews/debug.log', selfReviews: '~/.prs_reviews', backups: '~/.prs_backups', bindings: '~/.prs_reviews/bindings.json', teams: '~/.prs_teams', registry: '~/.prs_reviews/mirrors', corpus: '~/.prs_corpus' },
        state: {
          fetchedAt: S.fetchedAt,
          fetching: S.fetching,
          error: '',
          auto: S.auto,
          pending: p.pending,
          running: [],
          sections: p.sections.map((s) => ({ name: s.name, count: s.prs.length, error: null })),
          caches: { details: S.rows.length, detailing: 0, diffs: 0, diffing: 0, seen: S.rows.length, known: S.rows.length },
          sweeping: false,
          asks: S.asks.length,
          notices: S.notices,
        },
        log: '(mock) no debug log',
      })
    }
    return json(404, { error: 'not found' })
  }

  if (method === 'POST') {
    if (path === '/api/review') return postReview(body)
    if (path === '/api/stories') {
      S.follow = ((body as { follow?: { login: string }[] }).follow || []).map((f) => ({ login: f.login }))
      return json(200, { follow: S.follow })
    }
    if (path === '/api/auto') {
      S.auto = bool(body, 'on')
      return json(200, { ok: true })
    }
    if (path === '/api/settings') return postSettings(body)
    if (path === '/api/refresh') {
      S.fetching = true
      S.refreshes += 1
      if (!S.rows.some((r) => r.number === 301)) S.rows.push(mkPr(301, 'Bump base image to fix CVE', 'acme/infra', 'erin', 0, 'ASSIGNED'))
      if (S.refreshes >= 2 && !S.rows.some((r) => r.number === 213)) S.rows.push(mkPr(213, 'Hotfix: null check in export job', 'acme/web', 'bob', 0, 'REVIEW REQUESTED'))
      setTimeout(() => {
        S.fetching = false
        S.fetchedAt = secs()
        S.ticks += 1
      }, 400)
      return json(200, { ok: true, answeredBy: S.ticks + 1 })
    }
    if (path === '/api/open') {
      const r = S.rows.find((x) => x.url === str(body, 'url'))
      return json(200, { ok: true, opened: r?.url || '' })
    }
    if (path === '/api/copy') return json(200, { ok: true, tool: 'xclip' })
    if (path === '/api/memory') {
      const repo = repoOf(body) || 'general'
      const team = str(body, 'team')
      const op = str(body, 'op')
      const pr = { ok: true, url: `https://github.com/acme/guild-memory/pull/${40 + Math.floor(Math.random() * 50)}`, branch: 'gitdashy/propose-mock', note: '' }
      if (op === 'propose') return json(200, pr)
      if (op !== 'remove') return json(400, { error: 'op must be remove or propose' })
      if (team) return json(200, pr) // a team's file changes only when its pull request is approved
      const fact = str(body, 'fact')
      const lines = (memoryText[repo] || '').split('\n')
      const at = lines.findIndex((l) => l.replace(/^\s*[-•\s]+/, '').trim() === fact)
      if (at < 0) return json(404, { error: 'that fact is not in this file any more' })
      lines.splice(at, 1)
      memoryText[repo] = lines.join('\n')
      return json(200, { ok: true, error: '' })
    }
    if (path === '/api/drafts') {
      const repo = repoOf(body)
      const fact = str(body, 'fact')
      const i = S.drafts.findIndex((d) => d.repo === repo && d.fact === fact)
      const op = str(body, 'op')
      // a team draft accepted by hand is a pull request; it stays a draft until that is approved
      if (op === 'promote' && body.pooled) return json(200, { ok: true, url: 'https://github.com/acme/guild-memory/pull/88', branch: 'gitdashy/propose-acme-api-mock', note: '' })
      if (op === 'promote' || op === 'drop') {
        if (i >= 0) S.drafts.splice(i, 1)
        return json(200, { ok: true })
      }
      return json(400, { error: 'op must be promote or drop' })
    }
    if (path === '/api/overlaps') {
      if (str(body, 'op') === 'start') {
        S.overlaps = { running: true, t0: secs(), error: '', result: null, idle: false }
        setTimeout(() => {
          S.overlaps.running = false
          S.overlaps.result = [
            { repo: 'acme/api', a: 'uses tabs', b: 'run make lint before flagging style', would: 2, says: 'same rule twice', promotes: true },
          ]
        }, 1600)
        return json(200, { ok: true })
      }
      if (str(body, 'op') === 'merge') {
        const keep = str(body, 'keep')
        S.drafts = S.drafts.filter((d) => d.repo !== repoOf(body) || d.fact === keep)
        return json(200, { ok: true, count: 1 })
      }
      return json(400, { error: 'op must be start or merge' })
    }
    if (path === '/api/share') {
      const repo = repoOf(body)
      const fact = str(body, 'fact')
      const item = S.shares.find((s) => s.repo === repo && s.fact === fact)
      if (str(body, 'op') === 'send') {
        if (item) item.sent = true
        return json(200, { ok: true })
      }
      if (str(body, 'op') === 'forget') {
        S.shares = S.shares.filter((s) => !(s.repo === repo && s.fact === fact))
        return json(200, { ok: true })
      }
      return json(400, { error: 'op must be send or forget' })
    }
    if (path === '/api/teams') {
      const op = str(body, 'op')
      const key = str(body, 'key')
      if (op === 'new') {
        const name = str(body, 'name')
        if (!name) return json(400, { error: 'a team needs a name' })
        const k = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
        S.teams.push({ key: k, name, description: str(body, 'desc'), checkout: `~/.prs_teams/${k}`, remote: '' })
        S.asks = []
        return json(200, { ok: true, key: k })
      }
      if (op === 'join') {
        const repo = str(body, 'repo')
        const name = repo.split('/').pop() || 'team'
        S.teams.push({ key: name.toLowerCase(), name, description: '', checkout: `~/.prs_teams/${name.toLowerCase()}`, remote: repo })
        return json(200, { ok: true, key: name.toLowerCase(), warning: '' })
      }
      const t = S.teams.find((x) => x.key === key)
      if (!t) return json(404, { error: `not in team ${JSON.stringify(key)}` })
      if (op === 'describe') {
        t.description = str(body, 'desc')
        return json(200, { ok: true })
      }
      if (op === 'connect') {
        t.remote = str(body, 'url')
        return json(200, { ok: true, remote: t.remote })
      }
      if (op === 'cover') return json(200, { ok: true })
      if (op === 'brief') return json(200, { ok: true, error: '' })
      if (op === 'claim') return json(200, { ok: true })
      if (op === 'leave') {
        S.teams = S.teams.filter((x) => x.key !== key)
        return json(200, { ok: true })
      }
      return json(400, { error: 'unknown team op' })
    }
    if (path === '/api/bind') {
      const repo = str(body, 'repo')
      if (!repo) return json(400, { error: 'no row selected' })
      const op = str(body, 'op')
      if (op === 'forget') delete S.binding[repo]
      else S.binding[repo] = str(body, 'team')
      return json(200, { ok: true })
    }
    if (path === '/api/dream') {
      const op = str(body, 'op')
      if (op === 'start') {
        S.dream = { running: true, t0: secs(), error: '', result: null, idle: false }
        setTimeout(() => {
          S.dream.running = false
          S.dream.result = {
            summary: "merged 2 duplicate lines about tabs in acme/api\nmoved 'run make lint' to general\ndropped a stale note about the old CI",
            files: [
              { name: 'acme/api', before: 3, after: 2, deleted: false },
              { name: 'general', before: 3, after: 4, deleted: false },
              { name: 'acme/web', before: 1, after: 0, deleted: true },
            ],
            lost: 1,
            detail: 'merged 2 duplicate lines about tabs in acme/api\n\n--- acme/api\n-uses tabs\n-uses tabs\n+uses tabs\n',
            new: {},
          }
        }, 1800)
        return json(200, { ok: true })
      }
      if (op === 'discard') {
        S.dream = { running: false, t0: 0, error: '', result: null, idle: true }
        return json(200, { ok: true })
      }
      if (op === 'apply') {
        S.dream = { running: false, t0: 0, error: '', result: null, idle: true }
        return json(200, { ok: true, error: '' })
      }
      return json(400, { error: 'op must be start, apply or discard' })
    }
    if (path === '/api/request-review') {
      const r = S.rows.find((x) => x.url === str(body, 'url'))
      const login = str(body, 'login')
      if (!r) return json(404, { error: 'no such pr' })
      if (!login) return json(400, { error: 'a login is needed' })
      r.reviewers = `${r.reviewers} ·${login}`.trim()
      return json(200, { ok: true })
    }
    if (path === '/api/prereview') {
      const op = str(body, 'op')
      if (!['discuss', 'revise', 'accept', 'keep'].includes(op)) return json(400, { error: 'op must be discuss, revise, accept or keep' })
      const t = S.pres[str(body, 'url')]
      if (!t) return json(409, { error: 'this pre-review was written before discussions were saved; run it again to discuss it' })
      // an accepted revision replaces the pre-review's text, as the server rewrites the markdown
      return talkOp(t, op, body, (v) => {
        t.verdict = v.verdict
        t.text = t.text.split('**Verdict (advisory):**')[0] + `**Verdict (advisory):** ${v.verdict} — ${v.summary}\n\n${v.body}\n`
      })
    }
    if (path === '/api/posting') {
      const repo = str(body, 'repo')
      const op = str(body, 'op')
      const key = `${repo}#${body.number ?? ''}`
      if (['discuss', 'revise', 'accept', 'keep'].includes(op)) {
        const h = S.held[key]
        if (!h) return json(404, { error: 'nothing waiting for that PR' })
        return talkOp(h, op, body, (v) => Object.assign(h, v))
      }
      if (op === 'govern') {
        // like autorev::govern: ON holds a kind if the owner or any repo under it (board or store) holds it, and
        // clears every repo rule under it; OFF pins each repo on the board and keeps the owner's rule as the
        // fallback, marking the owner per repo
        if (typeof body.on !== 'boolean') return json(400, { error: 'govern needs on: true or on: false' })
        const owner = str(body, 'owner').replace(/\/\*$/, '').toLowerCase()
        if (!owner || owner.includes('/')) return json(400, { error: `${str(body, 'owner')} is not an owner` })
        const under = [...new Set(S.rows.map((r) => r.repo))].filter((r) => r.split('/')[0] === owner)
        const stored = Object.keys(S.posting).filter((r) => r.split('/')[0] === owner)
        for (const ran of ['manual', 'auto'] as const) {
          const own = S.postingOwners[owner]?.[ran]
          const of = (r: string) => S.posting[r]?.[ran] ?? own ?? 'post'
          if (body.on) {
            const word = own === 'hold' || [...under, ...stored].some((r) => of(r) === 'hold') ? 'hold' : 'post'
            S.postingOwners[owner] = { ...S.postingOwners[owner], [ran]: word }
            for (const r of stored) {
              const next = { ...S.posting[r] }
              delete next[ran]
              S.posting[r] = next
            }
          } else if (own) {
            for (const r of under) S.posting[r] = { ...S.posting[r], [ran]: of(r) }
          }
        }
        S.perRepo[owner] = !body.on
        return json(200, { ok: true })
      }
      if (op === 'discard' || op === 'release') {
        const h = S.held[key]
        if (!h) return json(404, { error: 'nothing waiting for that PR' })
        if ((h.busyUntil || 0) > Date.now()) return json(409, { error: 'a review of this PR is already running' })
        if (op === 'release' && h.proposed) return json(409, { error: 'accept or keep the revision first' })
        delete S.held[key]
        const r = S.rows.find((x) => x.repo === repo && x.number === body.number)
        if (r) {
          r.waiting = false
          if (op === 'release')
            S.reviewText[r.url] = h.verdict === 'approve' ? '✓ approved' : '✗ changes requested'
        }
        return json(200, { ok: true })
      }
      const ran = str(body, 'ran')
      // like the route: `owner` is the owner NAME, and it is one or the other
      const owner = str(body, 'owner')
      if (!repo && !owner) return json(400, { error: 'name a repo or an owner' })
      if (repo && owner) return json(400, { error: 'name a repo or an owner, not both' })
      const word = str(body, 'post')
      if (word !== 'post' && word !== 'hold' && word !== 'none')
        return json(400, { error: 'post must be post, hold or none' })
      // like the store: `none` takes the rule off rather than writing one
      const put = (at: Record<string, { manual?: string; auto?: string }>, k: string) => {
        const next = { ...at[k] }
        if (word === 'none') delete next[ran as 'manual' | 'auto']
        else next[ran as 'manual' | 'auto'] = word
        at[k] = next
      }
      put(owner ? S.postingOwners : S.posting, owner || repo)
      return json(200, { ok: true })
    }
    if (path === '/api/consent') {
      const kind = str(body, 'kind')
      const key = str(body, 'key')
      if (str(body, 'op') === 'again') {
        const held = S.refused.find((w) => w.kind === kind && w.key === key)
        S.refused = S.refused.filter((w) => w !== held)
        if (held) S.asks = [...S.asks, { kind, key, name: key, waiting: '2 drafts · 1 fact' }]
        return json(200, { ok: true })
      }
      S.asks = S.asks.filter((a) => !(a.kind === kind && a.key === key))
      if (body.yes === false && !S.refused.some((w) => w.kind === kind && w.key === key))
        S.refused = [...S.refused, { kind, key, what: kind === 'agents' ? 'agents.md not read' : 'not publishing' }]
      return json(200, { ok: true })
    }
    if (path === '/api/path') return json(200, { ok: true })
    if (path === '/api/update') return json(200, { ok: true })
    if (path === '/api/quit') return json(200, { ok: true })
    if (path === '/api/report') {
      if (body.op === 'start') S.reportAt = secs()
      if (body.op === 'open') {
        // like the server: a file, opened by the OS. The real page comes from src-tauri/src/report.rs.
        const file = join(tmpdir(), 'gitdashy-mock-report.html')
        writeFileSync(file, '<!doctype html><meta charset="utf-8"><title>Friday report · mock</title><body style="font:15px/1.6 system-ui;max-width:760px;margin:40px auto;padding:0 16px"><p style="color:#777">Friday report · mock</p><h1>The week</h1><p>The mock has no model; the real report is written by the review model.</p></body>')
        spawn(process.platform === 'darwin' ? 'open' : 'xdg-open', [file], { stdio: 'ignore', detached: true }).on('error', () => {}).unref()
      }
      return json(200, { ok: true })
    }
    if (path === '/api/changelog') {
      S.changelog = ''
      return json(200, { ok: true })
    }
    if (path === '/api/notices') {
      S.notices = []
      return json(200, { ok: true })
    }
    return json(404, { error: 'not found' })
  }

  return json(404, { error: 'not found' })
}

function readBody(req: IncomingMessage): Promise<Body> {
  return new Promise((resolve) => {
    let data = ''
    req.on('data', (c) => (data += c))
    req.on('end', () => {
      try {
        resolve(data ? JSON.parse(data) : {})
      } catch {
        resolve({})
      }
    })
    req.on('error', () => resolve({}))
  })
}

function backendUp(): Promise<boolean> {
  return new Promise((resolve) => {
    const req = httpGet(`${TARGET}/api/state`, { timeout: 400 }, (res) => {
      res.resume()
      resolve(true)
    })
    req.on('timeout', () => {
      req.destroy()
      resolve(false)
    })
    req.on('error', () => resolve(false))
  })
}

export function dashyMock(): Plugin {
  return {
    name: 'gitdashy-mock-api',
    apply: 'serve',
    async configureServer(server) {
      const force = process.env.DASHY_MOCK
      if (force === '0') return
      // ponytail: unforced, ask the backend again at most every 2s, so starting gitdashy after vite takes over
      let seen = { at: 0, up: false }
      const up = async () => {
        if (Date.now() - seen.at > 2000) seen = { at: Date.now(), up: await backendUp() }
        return seen.up
      }
      server.config.logger.info(`[gitdashy] mock /api answers while nothing is on ${TARGET} (DASHY_MOCK=0 to disable, =1 to force)`)
      server.middlewares.use(async (req: IncomingMessage, res: ServerResponse, next) => {
        const raw = req.url || ''
        if (!raw.startsWith('/api/') || (force !== '1' && (await up()))) return next()
        res.setHeader('X-Dashy-Mock', '1')
        const u = new URL(raw, 'http://localhost')
        void readBody(req).then((body) => {
          const { status, body: out } = handleApi(req.method || 'GET', u.pathname, u.searchParams, body)
          res.statusCode = status
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify(out))
        })
      })
    },
  }
}
