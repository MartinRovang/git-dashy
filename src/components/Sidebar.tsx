import { useState } from 'react'
import { Bot, Database, Eye, BookOpen, Wrench, PanelLeftClose, PanelLeftOpen, type LucideIcon } from 'lucide-react'
import type { PostingRule, StateData } from '../types'
import { counts, postingTree, ruleSource, hasOwnRule } from '../board'
import { every, span } from '../tokens'
import { Chips, Row, Select } from './Controls'
import { close, open, repaint } from '../modals'
import { api } from '../api'
import { fuzzy, step } from '../stories'

type Props = {
  data: StateData | null
  setting: (name: string, value: unknown) => void
  onPath: (which: 'L' | 'C') => void
  onTeams: () => void
  onModal: (name: string) => void
  onAuto: () => void
  /** Ask who, then follow them. */
  onFollow: () => void
  /** Follow everyone the board shows working under one `team:`/`org:` scope. */
  onFollowScope: (scope: string) => void
  /** `target` is a row's own name: `acme/api`, or `acme/*` for the whole owner. */
  onPosting: (ran: 'manual' | 'auto', post: 'post' | 'hold' | 'none', target: string) => void
  /** Turn one owner's rule on for both kinds of review, or take it off both. */
  onGovern: (owner: string, on: boolean) => void
  /** Take a repo's own posting rules off, so it follows its owner's again. */
  onFollowOwner: (repo: string) => void
  onAskAgain: (kind: string, key: string) => void
  onReport: (op: 'start' | 'open') => void
  /** Point a repo or `acme/*` at its DB repo ("" for none), or take the rule away. */
  onDb: (op: 'set' | 'clear', target: string, db?: string) => void
  collapsed: boolean
  onCollapse: () => void
}

/** Where the board's TEAM and MERGED rows may come from: your teams, and the orgs you can see.
 *
 * ponytail: grouped, and the prefix is the heading. One flat wrapped row of `team:neomedsys`
 * `org:martinrovang` chips repeated the prefix on every one of them, right-aligned itself into a
 * ragged block, and gave no way to say "all of them" — which is the answer most of the time.
 */
function Sources({
  options,
  picked,
  onToggle,
  onAll,
}: {
  options: string[]
  picked: string[]
  onToggle: (v: string) => void
  onAll: (v: string[]) => void
}) {
  if (!options.length) {
    return (
      <>
        <div className="sub">
          <kbd className="hint">O</kbd> sources
        </div>
        <div className="rules none">nothing seen yet — a team or an org shows up once it has a PR</div>
      </>
    )
  }
  const groups: [string, string, string][] = [
    ['team:', 'teams', 'a repo bound to one of your teams'],
    ['org:', 'orgs', 'every repo under that owner'],
  ]
  const all = options.length === picked.length
  return (
    <>
      <div className="sub">
        <kbd className="hint">O</kbd> sources
        <em>
          {/* explicit, not a chip that means the opposite of itself when it is already on. Disabled when
              it is already true: pressing it otherwise posts a setting nothing changed and flashes it. */}
          <button className="lnk" aria-pressed={all} disabled={all} onClick={() => onAll(options)}>
            all
          </button>
          <button className="lnk" aria-pressed={!picked.length} disabled={!picked.length} onClick={() => onAll([])}>
            none
          </button>
        </em>
      </div>
      {groups.map(([prefix, label, why]) => {
        const mine = options.filter((v) => v.startsWith(prefix))
        if (!mine.length) return null
        return (
          <div className="srcs" key={prefix}>
            <b title={why}>{label}</b>
            <div className="chips">
              {mine.map((v) => (
                <i
                  key={v}
                  className={picked.includes(v) ? 'on' : ''}
                  title={`${v} — ${why}`}
                  onClick={() => onToggle(v)}
                >
                  {v.slice(prefix.length)}
                </i>
              ))}
            </div>
          </div>
        )
      })}
    </>
  )
}

/** A settings group: a caret, its icon and a one-line summary when open, just the icon when collapsed.
 *
 * ponytail: `flag` puts a dot on the icon. An icon alone says nothing about what a group holds, so the
 * collapsed rail keeps a dot for what costs someone else (a hold) or is a nudge to undo (held back).
 */
function Group({
  icon: Icon,
  label,
  summary,
  flag,
  open,
  onToggle,
  collapsed,
  children,
}: {
  icon: LucideIcon
  label: string
  summary: string
  flag?: string
  open: boolean
  onToggle: () => void
  collapsed: boolean
  children: React.ReactNode
}) {
  return (
    <div className={`sgrp${open && !collapsed ? ' open' : ''}`}>
      {/* ponytail: on the narrow rail the fields cannot render, so the click opens the rail ONTO this
          group instead of toggling a body nobody can see. */}
      <button
        className="summary"
        title={collapsed ? `${label}${flag ? ` (${flag})` : ''} — open the rail here` : undefined}
        aria-label={flag ? `${label}, ${flag}` : label}
        aria-expanded={open && !collapsed}
        onClick={onToggle}
      >
        <span className="car">▶</span>
        <span className="gi">
          <Icon size={collapsed ? 18 : 14} aria-hidden />
          {collapsed && flag ? <i className="dot" /> : null}
        </span>
        <span className="lb">{label}</span>
        <span className="sv">{summary}</span>
      </button>
      {open && !collapsed ? <div className="fields">{children}</div> : null}
    </div>
  )
}

/** A text input that fuzzy-suggests `options` as you type: ↑/↓ move, Enter or Tab or a click takes one. Anything typed
 *  still stands, so a repo not on the board can be named. The dialog reads the value back by `id`. */
function Fuzzy({ id, placeholder, options, value }: { id: string; placeholder: string; options: string[]; value: string }) {
  const [q, setQ] = useState(value)
  const [idx, setIdx] = useState(-1)
  const [focus, setFocus] = useState(false)
  // Esc shuts the list and leaves the dialog open; typing opens it again
  const [shut, setShut] = useState(false)
  const hits = q.trim() ? fuzzy(q.trim(), options).filter((o) => o !== q.trim()).slice(0, 6) : []
  const take = (v: string) => {
    setQ(v)
    setIdx(-1)
    setShut(false)
  }
  return (
    <div className="fuzzy">
      <input
        type="text"
        id={id}
        placeholder={placeholder}
        autoComplete="off"
        value={q}
        onChange={(e) => take(e.target.value)}
        onFocus={() => setFocus(true)}
        onBlur={() => setFocus(false)}
        onKeyDown={(e) => {
          if (!hits.length || shut) return
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault()
            setIdx((i) => step(i, hits.length, e.key === 'ArrowDown'))
          } else if (e.key === 'Escape') {
            e.stopPropagation()
            setShut(true)
          } else if ((e.key === 'Enter' || e.key === 'Tab') && idx >= 0) {
            // stop the dialog's Enter from saving: this Enter picks a suggestion
            e.preventDefault()
            e.stopPropagation()
            take(hits[idx])
          }
        }}
      />
      {focus && !shut && hits.length ? (
        <div className="hits">
          {hits.map((h, i) => (
            <div key={h} className={`opt${i === idx ? ' on' : ''}`} onMouseDown={(e) => { e.preventDefault(); take(h) }}>
              {h}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

/** The DB repo dialog: which repo or owner, and the repo its schema lives in. `target`/`db` prefill an edit. */
function configureDb(board: string[], onDb: Props['onDb'], target = '', db = '') {
  // the board's repos now, every repo you can reach once /api/repos answers: a DB repo rarely has open PRs
  let repos = board
  const owners = () => [...new Set(repos.map((r) => `${r.split('/')[0]}/*`))]
  api('/api/repos')
    .then((r) => (r.ok ? r.json() : { repos: [] }))
    .then((j: { repos: string[] }) => {
      repos = [...new Set([...board, ...j.repos])]
      repaint()
    })
    .catch(() => {})
  const val = (id: string) => (document.querySelector(id) as HTMLInputElement).value.trim()
  const save = () => {
    if (!val('#dbt')) return
    // an edit that renames the target replaces the rule instead of adding a second one
    if (target && val('#dbt') !== target) onDb('clear', target)
    onDb('set', val('#dbt'), val('#dbr'))
    close(m)
  }
  const m = open({
    title: 'Configure database',
    dismiss: false,
    focus: target ? '#dbr' : '#dbt',
    body: () => (
      <div className="dbdlg">
        <label htmlFor="dbt">Repo or owner</label>
        <p>
          The repo whose PRs get the database check. Use <code>acme/api</code> for one repo, or <code>acme/*</code> for every
          repo under an owner. A repo's own rule beats its owner's.
        </p>
        <Fuzzy id="dbt" placeholder="acme/api or acme/*" options={[...owners(), ...repos]} value={target} />
        <label htmlFor="dbr">DB repo</label>
        <p>
          The repo where that database's schema and migrations live, like <code>acme/db</code>. Reviews read it and say what a
          PR does to the database. Leave it empty for <b>none</b>: this repo reads no schema, even under an owner rule. A
          private schema's names can end up in a review posted to a public PR.
        </p>
        <Fuzzy id="dbr" placeholder="acme/db, empty for none" options={repos} value={db} />
      </div>
    ),
    foot: [
      ['Enter', 'save', save, 'go'],
      ['Esc', 'cancel', () => close(m)],
    ],
  })
  m.keys = { Enter: save, Escape: () => close(m) }
}

/** The left rail: the reviewer's settings as collapsible groups, then the session's outcomes. */
/** "you hold | auto posts": what one target does with each kind of review. */
const postWords = (r: PostingRule) =>
  `you ${r.manual === 'hold' ? 'hold' : 'post'} | auto ${r.auto === 'hold' ? 'holds' : 'posts'}`

/** A row that opens and shuts: a chevron, a name, and a one-line summary on the right. */
function OpenRow({ name, summary, open, onFlip, sub }: { name: string; summary: string; open: boolean; onFlip: () => void; sub?: boolean }) {
  return (
    <button className={`trow${sub ? ' sub' : ''}`} aria-expanded={open} onClick={onFlip}>
      <span className="car">{open ? '▾' : '▸'}</span>
      <b>{name}</b>
      <span className="tsum">{summary}</span>
    </button>
  )
}

/** The two settings for one target: what happens to a review you ran, and to one auto ran. */
function PostControls({
  r,
  onPosting,
}: {
  r: PostingRule
  onPosting: (ran: 'manual' | 'auto', post: 'post' | 'hold' | 'none', target: string) => void
}) {
  return (
    <>
      {(['manual', 'auto'] as const).map((ran) => (
        <div className="pair sub2" key={ran}>
          <span>{ran === 'manual' ? 'reviews you run' : 'reviews auto runs'}</span>
          <div className="seg" role="group">
            {(['post', 'hold'] as const).map((w) => (
              <button
                key={w}
                aria-pressed={r[ran] === w}
                title={
                  w === 'post'
                    ? 'the verdict goes on the PR as soon as it is written'
                    : 'the verdict waits on disk; Y reads it and posts or drops it'
                }
                onClick={() => r[ran] !== w && onPosting(ran, w, r.target)}
              >
                {w === 'post' ? 'post it' : 'hold it'}
              </button>
            ))}
          </div>
        </div>
      ))}
    </>
  )
}

export function Sidebar({ data: d, setting, onPath, onTeams, onModal, onAuto, onFollow, onFollowScope, onPosting, onGovern, onFollowOwner, onAskAgain, onReport, onDb, collapsed, onCollapse }: Props) {
  const s = d?.settings || {}
  const o = d?.options || { model: [], depth: [], effort: [], voice: [], hunter: [], subs: [], window: [], interval: [], theme: [], scopes: [] }
  const k = d?.knowledge || { memory: '', store: '', teams: [], teamError: '', notes: [], waiting: [] }
  const rep = k.report
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])
  // each group opens and shuts on its own; the rail scrolls if you open them all
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const flip = (name: string) => {
    if (collapsed) {
      // the rail is 52px: the fields have nowhere to render, so widen it and land on this group
      setOpen((o) => ({ ...o, [name]: true }))
      onCollapse()
      return
    }
    setOpen((o) => ({ ...o, [name]: !o[name] }))
  }
  // ponytail: `waiting` is what a NO is holding back, not what is waiting for an answer — the rows
  // below are the "ask again" ones. Anything still asking is in d.asks, and the launch dialog owns it.
  const held = k.waiting || []
  const rules = d?.postingRules || []
  /** Every target that holds a review on either axis: the collapsed rail flags the Agent icon while any do. */
  // a rule set on that row, not every repo that inherits one: an owner holding for four repos is one hold
  const holds = rules
    .filter((r) => (['manual', 'auto'] as const).some((ran) => r[ran] === 'hold' && ruleSource(r.target, r[`${ran}Via`]) === 'own'))
    .map((r) => r.target)
  const dbRules = d?.dbRules || []
  const boardRepos = [...new Set((d?.sections || []).flatMap((x) => x.prs.map((p) => p.repo)))].filter(Boolean).sort()
  const teamList = k.teams.map((t) => t.key + (t.arrived ? ` +${t.arrived}` : ''))
  const teams = teamList.join(', ')
  const win = s.window == null ? 'all' : span(s.window)

  return (
    <div className={`side${collapsed ? ' shut' : ''}`}>
      {/* ponytail: the drag grip stays on the expanded rail. A narrow rail is a fixed shelf, not a
          width you tune, so dragging it is the one gesture that would fight the collapse. */}
      {collapsed ? null : <div className="grip" data-grip="side" />}
      <div className="sh">
        <button className="iconbtn" title={collapsed ? 'Expand sidebar (S)' : 'Collapse sidebar (S)'} onClick={onCollapse}>
          {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
          {collapsed ? null : <kbd className="hint">S</kbd>}
        </button>
      </div>

      <div className="snav scroll">
        <div className="grpname">Reviewer</div>

        <Group
          icon={Bot}
          label="Agent"
          flag={holds.length ? `${holds.length} hold${holds.length === 1 ? 's' : ''} a review` : undefined}
          summary={[
            [s.model, s.depth, s.effort].filter(Boolean).join(' · '),
            holds.length ? `${holds.length} hold${holds.length === 1 ? 's' : ''} a review` : '',
          ]
            .filter(Boolean)
            .join(' · ')}
          open={!!open.agent}
          onToggle={() => flip('agent')}
          collapsed={collapsed}
        >
          <Row k="m" label="model">
            <Select value={s.model || ''} options={o.model} show={(v) => v} onChange={(v) => setting('model', v)} />
          </Row>
          <Row k="d" label="depth">
            <Select value={s.depth || ''} options={o.depth} show={(v) => v || 'default'} onChange={(v) => setting('depth', v)} />
          </Row>
          <Row k="e" label="effort">
            <Select value={s.effort || ''} options={o.effort} show={(v) => v || 'default'} onChange={(v) => setting('effort', v)} />
          </Row>
          <div className="sub">
            <kbd className="hint">x</kbd> voices <em>active</em>
          </div>
          <Chips values={s.voice || []} options={o.voice} onToggle={(v) => setting('voice', toggle(s.voice || [], v))} />
          <div className="sub">
            <kbd className="hint">h</kbd> hunters <em>active</em>
          </div>
          <Chips values={s.hunter || []} options={o.hunter} onToggle={(v) => setting('hunter', toggle(s.hunter || [], v))} />
          {/* ponytail: auto lives with the agent now, which is what it configures. The top bar keeps
              its switch too — it is the one setting you flip mid-session without opening anything. */}
          <button className="fld" aria-pressed={!!d?.auto} onClick={onAuto}>
            <kbd className="hint">a</kbd>
            <span>Auto-run on new PRs</span>
            <span className="sw" />
          </button>

          {/* ponytail: here, and spelled out. It was a modal behind `H` on a row showing two lines of
              "post it / wait for a key · via acme/*", so both options were never on screen at once
              and nothing said what else was set. Posting is the part you cannot take back, so the
              setting should not be hidden behind a key.
              ponytail: and every target, not the selected row's. A panel that answered for whichever PR
              happened to be picked could not be read as a setting -- the same control said different
              things depending on the list behind it, and half the rules it mentioned were unreachable. */}
          <div className="sub">when a review finishes</div>
          {rules.length ? (
            <div className="targets">
              {postingTree(rules).map((node) => {
                const owner = node.owner.target.slice(0, -2)
                const key = `row:${node.owner.target}`
                return (
                  <div className="onode" key={owner}>
                    {/* collapsed, the row already answers the question: one setting and what it is, or
                        one per repo -- whose settings are only drawn once it is opened */}
                    <OpenRow
                      name={owner}
                      open={!!open[key]}
                      onFlip={() => flip(key)}
                      // "all repos" only when it is true: a repo with a rule of its own still beats the owner
                      summary={
                        !node.governs
                          ? `per repo · ${node.repos.length}`
                          : node.exceptions.length
                            ? `${postWords(node.owner)} · ${node.exceptions.length} with a rule of its own`
                            : `all repos: ${postWords(node.owner)}`
                      }
                    />
                    {open[key] ? (
                      <div className="kids">
                        <button
                          className="fld"
                          aria-pressed={node.governs}
                          onClick={() => onGovern(owner, !node.governs)}
                        >
                          <span>one setting for every repo under {owner}</span>
                          <span className="sw" />
                        </button>
                        {node.governs ? (
                          <>
                            <PostControls r={node.owner} onPosting={onPosting} />
                            {node.repos.length > node.exceptions.length ? (
                              <div className="rules none">
                                applies to{' '}
                                {node.repos
                                  .filter((r) => !node.exceptions.includes(r))
                                  .map((r) => r.target.split('/')[1])
                                  .join(', ')}
                              </div>
                            ) : null}
                            {/* a repo whose own rule beats the owner: shown, changeable, and one press from
                                following the owner like the rest */}
                            {node.exceptions.map((r) => (
                              <div key={r.target} className="except">
                                <OpenRow
                                  sub
                                  name={r.target.split('/')[1]}
                                  open={!!open[`row:${r.target}`]}
                                  onFlip={() => flip(`row:${r.target}`)}
                                  summary={`its own rule: ${postWords(r)}`}
                                />
                                {open[`row:${r.target}`] ? (
                                  <>
                                    <PostControls r={r} onPosting={onPosting} />
                                    <button className="lnk follow" onClick={() => onFollowOwner(r.target)}>
                                      follow {owner}/* instead
                                    </button>
                                  </>
                                ) : null}
                              </div>
                            ))}
                          </>
                        ) : node.repos.length ? (
                          node.repos.map((r) => (
                            <div key={r.target}>
                              <OpenRow
                                sub
                                name={r.target.split('/')[1]}
                                open={!!open[`row:${r.target}`]}
                                onFlip={() => flip(`row:${r.target}`)}
                                summary={postWords(r)}
                              />
                              {open[`row:${r.target}`] ? <PostControls r={r} onPosting={onPosting} /> : null}
                            </div>
                          ))
                        ) : (
                          <div className="rules none">no repos under {owner} on the board</div>
                        )}
                        {/* per repo, the owner's rule is still the fallback: say what a repo with no PR here follows */}
                        {!node.governs && hasOwnRule(node.owner) ? (
                          <div className="rules none">a repo not listed here follows {owner}/*: {postWords(node.owner)}</div>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="rules none">no repos on the board yet; reviews post when written</div>
          )}
        </Group>

        <Group
          icon={Database}
          label="Database"
          summary={dbRules.length ? `${dbRules.length} DB repo rule${dbRules.length === 1 ? '' : 's'}` : 'no DB repos'}
          open={!!open.database}
          onToggle={() => flip('database')}
          collapsed={collapsed}
        >
          {/* the repo a repo's schema and migrations live in: its reviews read it and say what a PR does to the
              database. A repo's own rule beats its owner's; "none" leaves one repo out of an owner rule. */}
          <div className="sub">where each repo's database is defined</div>
          {dbRules.length ? (
            <div className="targets">
              {dbRules.map((r) => (
                <div className="dbrule" key={r.target}>
                  <button className="dbedit" title={`edit the rule for ${r.target}`} onClick={() => configureDb(boardRepos, onDb, r.target, r.db)}>
                    <span className="ln">
                      <em>{r.target.endsWith('/*') ? 'owner' : 'repo'}</em>
                      <b>{r.target}</b>
                    </span>
                    <span className="ln">
                      <em>schema from</em>
                      {r.db ? <b>{r.db}</b> : <i>none</i>}
                    </span>
                  </button>
                  <button className="ib" title={`remove the rule for ${r.target}`} onClick={() => onDb('clear', r.target)}>
                    ×
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <div className="rules none">reviews read no database schema</div>
          )}
          <button className="btn dbconf" onClick={() => configureDb(boardRepos, onDb)}>
            configure
          </button>
        </Group>

        <Group
          icon={Eye}
          label="View"
          summary={`${win} history · ${every(s.interval || 0)}`}
          open={!!open.view}
          onToggle={() => flip('view')}
          collapsed={collapsed}
        >
          <Row k="s" label="summaries">
            <Select value={s.subs || ''} options={o.subs} show={(v) => v} onChange={(v) => setting('subs', v)} />
          </Row>
          <button className="fld" aria-pressed={!!s.drafts} onClick={() => setting('drafts', !s.drafts)}>
            <kbd className="hint">D</kbd>
            <span>Show drafts</span>
            <span className="sw" />
          </button>
          {/* ponytail: .hidekeys has been in the stylesheet with nothing setting it. This is the
              switch it was waiting for, and the sheet carries the same one. */}
          <button className="fld" aria-pressed={s.keyhints !== false} onClick={() => setting('keyhints', s.keyhints === false)}>
            <span>Show key hints</span>
            <span className="sw" />
          </button>
          <Sources
            options={o.scopes}
            picked={s.scopes || []}
            onToggle={(v) => setting('scopes', toggle(s.scopes || [], v))}
            onAll={(v) => setting('scopes', v)}
          />
          {/* ponytail: these DO something, they do not toggle — following a team is "add everyone it
              has on the board right now", so the same chip pressed tomorrow adds whoever joined
              since. The tag shape is the one the Knowledge group already uses for an action. */}
          <div className="sub">
            <kbd className="hint">F</kbd> follow
            <em>
              <button className="lnk" onClick={onFollow}>
                someone
              </button>
            </em>
          </div>
          {o.scopes.length ? (
            <div className="tags">
              {o.scopes.map((v) => (
                <button className="tag" key={v} title={`follow everyone the board shows under ${v}`} onClick={() => onFollowScope(v)}>
                  {v.replace(':', ' ')}
                </button>
              ))}
            </div>
          ) : (
            <div className="rules none">a team or an org shows up here once it has a PR</div>
          )}
          <Row k="t" label="history">
            <Select
              value={s.window == null ? 'all' : String(s.window)}
              options={o.window.map((v) => (v == null ? 'all' : String(v)))}
              show={(v) => (v === 'all' ? 'all' : span(+v))}
              onChange={(v) => setting('window', v === 'all' ? null : +v)}
            />
          </Row>
          <Row k="i" label="refresh">
            <Select
              value={String(s.interval)}
              options={o.interval.map(String)}
              show={(v) => every(+v)}
              onChange={(v) => setting('interval', +v)}
            />
          </Row>
        </Group>

        <Group
          icon={BookOpen}
          label="Knowledge"
          flag={held.length ? `${held.length} held back` : undefined}
          summary={[teams || 'no team', held.length ? `${held.length} held back` : ''].filter(Boolean).join(' · ')}
          open={!!open.know}
          onToggle={() => flip('know')}
          collapsed={collapsed}
        >
          <Row k="L" label="memory">
            <b className="link" title={k.memory} onClick={() => onPath('L')}>
              {k.memory}
            </b>
          </Row>
          <Row k="T" label="team">
            <b className={`link${k.teamError ? ' err' : k.teams.length ? ' on' : ''}`} title={k.teamError} onClick={onTeams}>
              {k.teamError ? k.teamError.slice(0, 40) : teams || 'off'}
            </b>
          </Row>
          {k.store ? (
            <Row k="C" label="store">
              <b className="link" onClick={() => onPath('C')}>
                {k.store}
              </b>
            </Row>
          ) : null}
          {(k.notes || []).map((n, i) => (
            <div className="note" key={i}>
              ⚠ {n}
            </div>
          ))}
          {/* What a "no" is still holding back. Clicking one asks that question again. */}
          {held.map((w, i) => (
            <div className="note link" key={`w${i}`} title="ask me again" onClick={() => onAskAgain(w.kind, w.key)}>
              ⚠ {w.key}: {w.what} — ask again
            </div>
          ))}
          {/* ponytail: this OPENS something, it does not toggle. There were five buttons here -- waiting, shared,
              dream, general, learning -- each its own screen; they are one panel now, with tabs and actions. */}
          <div className="tags">
            <button className="tag" onClick={() => onModal('knowledge')}>
              knowledge panel <kbd className="hint">K</kbd>
            </button>
          </div>
        </Group>

        <Group
          icon={Wrench}
          label="Tools"
          summary={rep?.job.running ? 'writing Friday report…' : rep?.latest ? `report ${rep.latest}` : 'Friday report'}
          open={!!open.tools}
          onToggle={() => flip('tools')}
          collapsed={collapsed}
        >
          <div className="sub">
            Friday report <em>7 days</em>
          </div>
          <div className="tags">
            {/* one report a session: once it is written, open is all there is to do */}
            {rep?.job.running ? (
              <button className="tag" disabled>
                writing… {rep.job.elapsed || 0}s
              </button>
            ) : rep?.latest ? (
              <button className="tag" title={`open the ${rep.latest} report in the browser`} onClick={() => onReport('open')}>
                open {rep.latest}
              </button>
            ) : (
              <button className="tag" onClick={() => onReport('start')}>
                generate
              </button>
            )}
          </div>
          {rep?.job.error ? <div className="note">⚠ report: {rep.job.error}</div> : null}
        </Group>
      </div>

      {/* ponytail: the design captions this "last 4h". counts() reads every section, and only REVIEWED
          is cut by the history window — MINE and the two queues are whatever is open. So the caption
          says what the number actually covers. */}
      <div className="sfoot" title="The review state of every PR on the board">
        <div className="hd">
          <b>Status</b>
          <i>on the board</i>
        </div>
        {counts(d).map(([l, n, c]) => (
          <div key={l} className={`stat${n ? '' : ' zero'}`} title={`${n} ${l}`}>
            <i style={{ background: n ? c : 'var(--dim3)' }} />
            <span>{l}</span>
            <b>{n}</b>
          </div>
        ))}
      </div>
    </div>
  )
}
