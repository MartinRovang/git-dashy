// Dev-only mock of the Rust server's /api/* surface (src-tauri/src/web.rs), so `npm run dev` works
// with no backend. Interactive: mutations change in-memory state roughly like the real handlers. Not modelled:
// the drafts recurrence gate (promote just removes the draft) and the Host/token guard.
// Active when no backend answers on :7777; force with DASHY_MOCK=1, disable with DASHY_MOCK=0.
import { get as httpGet } from 'node:http'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Plugin } from 'vite'

const TARGET = 'http://127.0.0.1:7777'

const THEMES = ['pencil', 'dashy', 'dracula', 'gruvbox', 'nord']
const MODELS = ['opus', 'sonnet', 'fable']
const DEPTHS = ['adaptive', 'low', 'medium', 'high']
const EFFORTS = ['', 'low', 'medium', 'high', 'xhigh', 'max']
const VOICES = ['review', 'caveman', 'bot']
const HUNTERS = ['ponytail', 'security', 'tests', 'humanizer']
const SUBS = ['all', 'open', 'off']
const INTERVALS = [60, 120, 300, 600, 900]
const WINDOWS = [1, 4, 6, null]
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
  asks: [] as { kind: string; key: string; name: string; waiting?: string; text?: string; path?: string }[],
  refreshes: 0,
  cursor: 0,
  overlaps: { running: false, t0: 0, error: '', result: null as unknown[] | null, idle: true },
  dream: { running: false, t0: 0, error: '', result: null as unknown | null, idle: true },
}

const memoryText: Record<string, string> = {
  general: '# general\n\n- prefer small PRs\n- always rebase, never merge main\n- run make lint before flagging style\n',
  'acme/api': '# acme/api\n\n- run make lint before flagging style\n- uses tabs\n- old CI on jenkins, ignore\n',
  'acme/web': '# acme/web\n\n- session middleware is shared with the admin app\n',
}

function seed() {
  const m1 = mkPr(101, 'Add retry to webhook client', 'acme/api', 'alice', 2, 'MINE')
  m1.status = '· awaiting review'
  m1.reviewers = '✓bob ·carol'
  m1.checks = '✓'
  m1.pre = { at: T0 / 1000 - 3600, moved: false }
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
  ]
  S.shares = [
    { repo: 'acme/api', fact: 'run make lint before flagging style', sent: true, backers: ['alice'] },
    { repo: 'acme/api', fact: 'uses tabs', sent: false, backers: ['alice', 'bob'] },
    { repo: null, fact: 'prefer small PRs', sent: false, backers: [] },
  ]
  S.asks = [{ kind: 'publishing', key: 'acme', name: 'Acme Guild', waiting: '2 drafts · 1 fact' }]
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
    error: '',
    auto: S.auto,
    pending: S.rows.filter((r) => r.section === 'REVIEW REQUESTED').length,
    model: S.settings.model,
    running: S.rows.filter((r) => r.busy).length,
    update: '',
    settings: { ...S.settings },
    options: { model: MODELS, depth: DEPTHS, effort: EFFORTS, voice: VOICES, hunter: HUNTERS, subs: SUBS, window: WINDOWS, interval: INTERVALS, theme: THEMES },
    knowledge: {
      memory: '~/.prs_memory',
      store: '',
      teams: S.teams.map((t) => ({ key: t.key, name: t.name, arrived: 0 })),
      teamError: '',
      notes: [],
    },
    asks: S.asks,
    notices: S.notices,
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

function code(url: string, scope: string) {
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
  const filtered = rows.filter((r) => {
    const k = (r as { kind: string }).kind
    return k !== 'note' && k !== 'orphan'
  })
  return { url, pending: false, rows: scope === 'marks' ? rows : filtered }
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
  const r = S.rows.find((x) => x.url === url)
  if (!r) return json(404, { error: 'no such pr' })
  if (bool(b, 'self')) {
    r.pre = { at: secs(), moved: false }
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

function postSettings(b: Body) {
  const s = S.settings
  for (const k of ['theme', 'notify', 'subs', 'model', 'depth', 'effort', 'voice', 'hunter', 'interval', 'window', 'drafts', 'hinted']) {
    if (k in b) s[k] = b[k]
  }
  return json(200, { ok: true })
}

function handleApi(method: string, path: string, query: URLSearchParams, body: Body) {
  if (method === 'GET') {
    if (path === '/api/state') return json(200, buildPayload())
    if (path === '/api/asks') return json(200, { asks: S.asks })
    if (path === '/api/pr') return json(200, detail(query.get('url') || ''))
    if (path === '/api/diff') return json(200, code(query.get('url') || '', query.get('scope') || 'marks'))
    if (path === '/api/memory') {
      const repo = query.get('repo') || 'general'
      return json(200, { repo, path: `~/.prs_memory/${repo === 'general' ? 'general' : repo.replace('/', '__')}.md`, text: memoryText[repo] || '' })
    }
    if (path === '/api/drafts') return json(200, { promoteAt: PROMOTE_AT, items: S.drafts.map((d) => ({ ...d, team: d.repo ? teamOf(d.repo) : '' })) })
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
      return json(200, {
        path: `~/.prs_reviews/${r.repo.replace('/', '__')}-${r.number}.md`,
        text: `# Pre-review — ${r.repo}#${r.number}\n\n> **Not posted.** This is the mock reviewer.\n\n**Verdict (advisory):** ✗ changes requested — mock\n\n## Findings\n\n- \`api/handlers.py:88\` the pager reads \`total\` before the guard\n`,
        moved: r.pre.moved,
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
      }, 400)
      return json(200, { ok: true })
    }
    if (path === '/api/open') {
      const r = S.rows.find((x) => x.url === str(body, 'url'))
      return json(200, { ok: true, opened: r?.url || '' })
    }
    if (path === '/api/copy') return json(200, { ok: true, tool: 'xclip' })
    if (path === '/api/memory') {
      const repo = repoOf(body) || 'general'
      memoryText[repo] = str(body, 'text')
      return json(200, { ok: true, error: '' })
    }
    if (path === '/api/drafts') {
      const repo = repoOf(body)
      const fact = str(body, 'fact')
      const i = S.drafts.findIndex((d) => d.repo === repo && d.fact === fact)
      const op = str(body, 'op')
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
    if (path === '/api/consent') {
      const kind = str(body, 'kind')
      const key = str(body, 'key')
      S.asks = S.asks.filter((a) => !(a.kind === kind && a.key === key))
      return json(200, { ok: true })
    }
    if (path === '/api/path') return json(200, { ok: true })
    if (path === '/api/update') return json(200, { ok: true })
    if (path === '/api/quit') return json(200, { ok: true })
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
