import { useState } from 'react'
import { Bot, Database, Share2, Eye, BookOpen, Skull, Wrench, PanelLeftClose, PanelLeftOpen, ListFilter, type LucideIcon } from 'lucide-react'
import type { PostingRule, Row as Pr, StateData } from '../types'
import { canCastOn, counts, postingTree, ruleSource, hasOwnRule } from '../board'
import { Glyph } from './Glyph'
import { every, span } from '../tokens'
import { Row, Select } from './Controls'
import { close, open, repaint } from '../modals'
import { api } from '../api'
import { fuzzy, step } from '../stories'
import { Out, SecFilter, useSections } from '../sections'

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
  /** Set one target's inline rule. Its own axis: see PostControls. */
  onInline: (inline: 'on' | 'off', target: string) => void
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
  selected: Pr | null
  onCast: (p: Pr, spell: string) => void
  onBook: () => void
  onSchema: (db: string) => void
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
  x,
  loading,
  children,
}: {
  icon: LucideIcon
  label: string
  summary: string
  flag?: string
  open: boolean
  onToggle: () => void
  collapsed: boolean
  /** its place in the rail: switched off, dragged, popped out (see useSections) */
  x: ReturnType<ReturnType<typeof useSections>['sec']>
  /** no data yet: a bar where the summary will be, not a summary of empty defaults */
  loading: boolean
  children: React.ReactNode
}) {
  if (x.off) return null
  return (
    <div className={`sgrp${open && !collapsed && !x.out ? ' open' : ''}${x.cls}`} style={x.style} {...x.wrap}>
      {/* ponytail: on the narrow rail the fields cannot render, so the click opens the rail ONTO this
          group instead of toggling a body nobody can see. */}
      <button
        className="summary"
        title={
          x.out
            ? `${label} is popped out: click to put it back`
            : collapsed
              ? `${label}${flag ? ` (${flag})` : ''} — open the rail here`
              : 'click to open, drag to reorder, drag out of the rail to pop it out'
        }
        aria-label={flag ? `${label}, ${flag}` : label}
        aria-expanded={open && !collapsed}
        // the 52px rail is a click target, not a drag handle: a small slip there would pop a group out
        {...(collapsed ? {} : x.head)}
        onClick={x.out ? x.dock : onToggle}
      >
        <span className="car">▶</span>
        <span className="gi">
          <Icon size={collapsed ? 18 : 14} aria-hidden />
          {collapsed && flag ? <i className="dot" /> : null}
        </span>
        <span className="lb">{label}</span>
        <span className="sv">{loading ? <i className="sk" /> : summary}</span>
      </button>
      {x.out ? (
        <Out id={`side-${label}`} label={label} onDock={x.dock}>
          <div className="fields">{children}</div>
        </Out>
      ) : open && !collapsed ? (
        <div className="fields">{children}</div>
      ) : null}
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
  onInline,
}: {
  r: PostingRule
  onPosting: (ran: 'manual' | 'auto', post: 'post' | 'hold' | 'none', target: string) => void
  onInline: (inline: 'on' | 'off', target: string) => void
}) {
  return (
    <>
      {/* the third axis. Not split by who ran the review: whether a repo wants comments on its lines
          is a property of the repo, and its conversation-resolution setting, not of who pressed what */}
      <div className="pair sub2">
        <span>findings on their lines</span>
        <div className="seg" role="group">
          {(['on', 'off'] as const).map((w) => (
            <button
              key={w}
              aria-pressed={r.inline === (w === 'on')}
              title={
                w === 'on'
                  ? 'each finding is also a comment on the line it names; these open resolvable threads'
                  : 'findings stay in the review body only'
              }
              onClick={() => r.inline !== (w === 'on') && onInline(w, r.target)}
            >
              {w === 'on' ? 'on the lines' : 'body only'}
            </button>
          ))}
        </div>
      </div>
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

const GROUPS: [string, string][] = [
  ['agent', 'Agent'],
  ['necro', 'Necronomicon'],
  ['database', 'Database'],
  ['view', 'View'],
  ['know', 'Knowledge'],
  ['tools', 'Tools'],
]

export function Sidebar({ data: d, setting, onPath, onTeams, onModal, onAuto, onFollow, onFollowScope, onPosting, onInline, onGovern, onFollowOwner, onAskAgain, onReport, onDb, collapsed, onCollapse, selected, onCast, onBook, onSchema }: Props) {
  const s = d?.settings || {}
  // the server already drops a spell whose file was deleted
  const equipped = s.spells || []
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
  const [filtering, setFiltering] = useState(false)
  const { lay, sec, switchOff } = useSections('side', GROUPS.map(([n]) => n), s.side, '.side')

  return (
    <div className={`side${collapsed ? ' shut' : ''}`}>
      {/* ponytail: the drag grip stays on the expanded rail. A narrow rail is a fixed shelf, not a
          width you tune, so dragging it is the one gesture that would fight the collapse. */}
      {collapsed ? null : <div className="grip" data-grip="side" />}
      <div className="sh">
        {collapsed ? null : (
          <button className="iconbtn" title="Choose which groups the sidebar shows" aria-pressed={filtering} onClick={() => setFiltering(!filtering)}>
            <ListFilter size={16} />
          </button>
        )}
        <button className="iconbtn" title={collapsed ? 'Expand sidebar (S)' : 'Collapse sidebar (S)'} onClick={onCollapse}>
          {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
          {collapsed ? null : <kbd className="hint">S</kbd>}
        </button>
      </div>

      {filtering && !collapsed ? <SecFilter secs={GROUPS} off={lay.off} onFlip={switchOff} /> : null}
      <div className="snav scroll">
        <div className="grpname">Reviewer</div>

        <Group
          loading={!d}
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
          x={sec('agent')}
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
          {/* ponytail: the switch every target with no rule of its own falls back to, and the only
              setting here that writes to someone else's PR. Until now it could be turned on with
              --inline and never off from inside the app (#161). */}
          <button className="fld" aria-pressed={!!s.inline} onClick={() => setting('inline', !s.inline)}>
            <span>findings on their lines, by default</span>
            <span className="sw" />
          </button>

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
                            <PostControls r={node.owner} onPosting={onPosting} onInline={onInline} />
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
                                    <PostControls r={r} onPosting={onPosting} onInline={onInline} />
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
                              {open[`row:${r.target}`] ? <PostControls r={r} onPosting={onPosting} onInline={onInline} /> : null}
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
          loading={!d}
          icon={Skull}
          label="Necronomicon"
          summary={[`${(s.voice || []).length} voice${(s.voice || []).length === 1 ? '' : 's'}`, `${(s.hunter || []).length} passive${(s.hunter || []).length === 1 ? '' : 's'}`, `${equipped.length} spell${equipped.length === 1 ? '' : 's'}`].join(' · ')}
          open={!!open.necro}
          onToggle={() => flip('necro')}
          collapsed={collapsed}
          x={sec('necro')}
        >
          {/* ponytail: what is on, read-only. Equipping lives in the book, so the rail and the book cannot disagree about how. */}
          {([['voices', 'voice', s.voice || []], ['passives', 'passive', s.hunter || []]] as const).map(([label, kind, on]) => (
            <div key={label}>
              <div className="sub">{label}</div>
              {on.length ? (
                <div className="tags">
                  {on.map((name) => (
                    <span className="tag still" key={name}>
                      <Glyph kind={kind} name={name} /> {name}
                    </span>
                  ))}
                </div>
              ) : (
                <div className="rules none">none equipped</div>
              )}
            </div>
          ))}
          <div className="sub">
            spells <em>{selected && canCastOn(selected) ? `cast on #${selected.number}` : 'pick a PR'}</em>
          </div>
          {equipped.length ? (
            <div className="tags">
              {equipped.map((name) => (
                <button
                  className="tag"
                  key={name}
                  disabled={!selected || !canCastOn(selected)}
                  title={selected ? `cast ${name} on #${selected.number}` : 'select a PR'}
                  onClick={() => selected && onCast(selected, name)}
                >
                  <Glyph kind="spell" name={name} /> {name}
                </button>
              ))}
            </div>
          ) : (
            <div className="rules none">no spells equipped</div>
          )}
          <button className="btn dbconf" onClick={onBook}>
            open the book
          </button>
        </Group>

        <Group
          loading={!d}
          icon={Database}
          label="Database"
          summary={dbRules.length ? `${dbRules.length} DB repo rule${dbRules.length === 1 ? '' : 's'}` : 'no DB repos'}
          open={!!open.database}
          onToggle={() => flip('database')}
          collapsed={collapsed}
          x={sec('database')}
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
                  {r.db && (
                    <button className="ib" title={`graph of ${r.db}'s tables, read from its .sql files`} onClick={() => onSchema(r.db)}>
                      <Share2 size={13} aria-hidden />
                    </button>
                  )}
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
          loading={!d}
          icon={Eye}
          label="View"
          summary={`${win} history · ${every(s.interval || 0)}`}
          open={!!open.view}
          onToggle={() => flip('view')}
          collapsed={collapsed}
          x={sec('view')}
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
          loading={!d}
          icon={BookOpen}
          label="Knowledge"
          flag={held.length ? `${held.length} held back` : undefined}
          summary={[teams || 'no team', held.length ? `${held.length} held back` : ''].filter(Boolean).join(' · ')}
          open={!!open.know}
          onToggle={() => flip('know')}
          collapsed={collapsed}
          x={sec('know')}
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
          loading={!d}
          icon={Wrench}
          label="Tools"
          summary={rep?.job.running ? 'writing Friday report…' : rep?.latest ? `report ${rep.latest}` : 'Friday report'}
          open={!!open.tools}
          onToggle={() => flip('tools')}
          collapsed={collapsed}
          x={sec('tools')}
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
            <b>{d ? n : <i className="sk" style={{ width: '1.5em' }} />}</b>
          </div>
        ))}
      </div>
    </div>
  )
}
