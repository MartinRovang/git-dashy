import { useState } from 'react'
import type { PostingRule, StateData } from '../types'
import { counts, hasOwnRule, postingTree, ruleSource } from '../board'
import { every, span } from '../tokens'
import { Chips, Row, Select } from './Controls'

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
  /** How many are followed, so the rail can say so when shut. */
  followed: number
  /** `target` is a row's own name: `acme/api`, or `acme/*` for the whole owner. */
  onPosting: (ran: 'manual' | 'auto', post: 'post' | 'hold' | 'none', target: string) => void
  /** Turn one owner's rule on for both kinds of review, or take it off both. */
  onGovern: (owner: string, on: boolean) => void
  onAskAgain: (kind: string, key: string) => void
  onReport: (op: 'start' | 'open') => void
  collapsed: boolean
  onCollapse: () => void
}

/** One label/value pair in a collapsed group's stack. */
function Ln({ label, value, off }: { label: string; value: string; off?: boolean }) {
  return (
    <span className="ln">
      <em>{label}</em>
      <s className={off ? 'off' : undefined}>{value}</s>
    </span>
  )
}

/** A setting that holds several at once, as the badges the expanded rail uses for the same thing.
 *
 * ponytail: joined with commas these ran off the narrow rail and you saw "review, cave…". One badge
 * per value, stacked, so every active one is readable at any width.
 */
function Pills({ label, values }: { label: string; values: string[] }) {
  return (
    <span className="ln">
      <em>{label}</em>
      {values.length ? (
        <span className="pills">
          {values.map((v) => (
            <i key={v}>{v}</i>
          ))}
        </span>
      ) : (
        <s className="off">none</s>
      )}
    </span>
  )
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

/** A settings group: a caret and a one-line summary when open, a stacked digest when collapsed.
 *
 * ponytail: the collapsed rail shows a digest. A column of icons tells you which group to click and
 * nothing about what it holds; label/value pairs tell you the model you are reviewing with without
 * expanding anything.
 */
function Group({
  k,
  label,
  summary,
  digest,
  open,
  onToggle,
  collapsed,
  children,
}: {
  k: string
  label: string
  summary: string
  digest: React.ReactNode
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
        title={collapsed ? `${label} — open the rail here` : undefined}
        aria-expanded={open && !collapsed}
        onClick={onToggle}
      >
        <span className="car">▶</span>
        <span className="ic">{k.toUpperCase()}</span>
        <span className="icv">{digest}</span>
        <span className="lb">{label}</span>
        <span className="sv">{summary}</span>
      </button>
      {open && !collapsed ? <div className="fields">{children}</div> : null}
    </div>
  )
}

/** The left rail: the reviewer's settings as collapsible groups, then the session's outcomes. */
/** One posting row: the words in force, and its two controls when it is open. Owners and repos draw the
 *  same row -- what differs is what sits under it, which is the tree's business, not the row's. */
function Target({
  r,
  open,
  onFlip,
  onPosting,
  count,
  note,
}: {
  r: PostingRule
  open: boolean
  onFlip: () => void
  onPosting: (ran: 'manual' | 'auto', post: 'post' | 'hold' | 'none', target: string) => void
  /** Only on an owner: how many repos it holds, so the row says what opening it will show. */
  count?: number
  note?: string
}) {
  const owner = r.target.endsWith('/*')
  const said = (ran: 'manual' | 'auto') =>
    ran === 'manual' ? `you ${r.manual === 'hold' ? 'hold' : 'post'}` : `auto ${r.auto === 'hold' ? 'holds' : 'posts'}`
  return (
    <>
      <button className={`trow${owner ? ' own' : ''}`} aria-expanded={open} onClick={onFlip} title={note || ''}>
        <span className="car">{open ? '▾' : '▸'}</span>
        <b>{owner ? r.target : r.target.split('/')[1]}</b>
        {count ? <span className="folds">{count}</span> : null}
        {(['manual', 'auto'] as const).map((ran) => (
          <i key={ran} className={ruleSource(r.target, r[`${ran}Via`])}>
            {said(ran)}
          </i>
        ))}
      </button>
      {open
        ? (['manual', 'auto'] as const).map((ran) => {
            const from = ruleSource(r.target, r[`${ran}Via`])
            return (
              <div className="pair sub2" key={ran}>
                <span>
                  reviews {ran === 'manual' ? 'you' : 'auto'} run{ran === 'manual' ? '' : 's'}
                  <em className={from}>
                    {{ own: 'set here', owner: 'from the owner', none: 'not set' }[from]}
                  </em>
                </span>
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
                      // a press that would write a rule this target already has writes nothing
                      disabled={r[ran] === w && from === 'own'}
                      onClick={() => onPosting(ran, w, r.target)}
                    >
                      {w === 'post' ? 'post it' : 'hold it'}
                    </button>
                  ))}
                </div>
              </div>
            )
          })
        : null}
    </>
  )
}

export function Sidebar({ data: d, setting, onPath, onTeams, onModal, onAuto, onFollow, onFollowScope, followed, onPosting, onGovern, onAskAgain, onReport, collapsed, onCollapse }: Props) {
  const s = d?.settings || {}
  const o = d?.options || { model: [], depth: [], effort: [], voice: [], hunter: [], subs: [], window: [], interval: [], theme: [], scopes: [] }
  const k = d?.knowledge || { memory: '', store: '', teams: [], teamError: '', notes: [], waiting: [] }
  const rep = k.report
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])
  // each group opens and shuts on its own; the rail scrolls if you open them all
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const flip = (name: string) => {
    if (collapsed) {
      // the rail is 106px: the fields have nowhere to render, so widen it and land on this group
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
  /** Every target that holds a review on either axis: what the collapsed rail shows of all this. */
  const holds = rules.filter((r) => r.manual === 'hold' || r.auto === 'hold').map((r) => r.target)
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
          ≡
          {collapsed ? null : <kbd className="hint">S</kbd>}
        </button>
      </div>

      <div className="snav scroll">
        <div className="grpname">Reviewer</div>

        <Group
          k="agent"
          label="Agent"
          summary={[
            [s.model, s.depth, s.effort].filter(Boolean).join(' · '),
            holds.length ? `${holds.length} hold${holds.length === 1 ? 's' : ''} a review` : '',
          ]
            .filter(Boolean)
            .join(' · ')}
          digest={
            <>
              <Ln label="model" value={s.model || '—'} />
              <Ln label="depth" value={s.depth || 'default'} />
              <Ln label="effort" value={s.effort || 'default'} off={!s.effort} />
              <Pills label="voices" values={s.voice || []} />
              <Pills label="hunters" values={s.hunter || []} />
              <Ln label="auto-run" value={d?.auto ? 'on' : 'off'} off={!d?.auto} />
              {/* the one setting here that changes what lands on someone else's PR, so it survives the
                  collapse. Names the targets, not a count: at 106px "2 hold" is a number you have to
                  open the rail to read. */}
              <Pills label="holds" values={holds} />
            </>
          }
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
                const name = node.owner.target
                const openOwner = !!open[`row:${name}`]
                return (
                  <div className={`onode${node.governs ? ' governs' : ''}`} key={name}>
                    <Target
                      r={node.owner}
                      open={openOwner}
                      onFlip={() => flip(`row:${name}`)}
                      onPosting={onPosting}
                      count={node.repos.length}
                    />
                    {openOwner ? (
                      <div className="kids">
                        {/* the override itself, on or off. Without it an owner rule, once written, decided
                            for every repo below it forever: the store could flip the word but never take
                            the rule away. */}
                        <button
                          className="fld"
                          aria-pressed={node.governs}
                          title={
                            node.governs
                              ? `turn off to set each repo under ${name} on its own`
                              : `turn on to decide for every repo under ${name} at once`
                          }
                          onClick={() => onGovern(name, !node.governs)}
                        >
                          <span>{name} decides for its repos</span>
                          <span className="sw" />
                        </button>
                        {node.repos.length === 0 ? (
                          <div className="rules none">no repos under this owner on the board</div>
                        ) : node.governs ? (
                          <>
                            {/* ponytail: names only. The owner decides for these, so a control here would
                                be a second place to set one thing -- the exact confusion this panel had. */}
                            <div className="kidcap">following it</div>
                            {node.repos.map((r) =>
                              hasOwnRule(r) ? (
                                <Target
                                  key={r.target}
                                  r={r}
                                  open={!!open[`row:${r.target}`]}
                                  onFlip={() => flip(`row:${r.target}`)}
                                  onPosting={onPosting}
                                  note={`a rule on this repo, which beats ${name}`}
                                />
                              ) : (
                                <div className="kidrow" key={r.target}>
                                  {r.target.split('/')[1]}
                                </div>
                              ),
                            )}
                          </>
                        ) : (
                          node.repos.map((r) => (
                            <Target
                              key={r.target}
                              r={r}
                              open={!!open[`row:${r.target}`]}
                              onFlip={() => flip(`row:${r.target}`)}
                              onPosting={onPosting}
                            />
                          ))
                        )}
                      </div>
                    ) : null}
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="rules none">no repos on the board yet — every review posts as soon as it is written</div>
          )}
        </Group>

        <Group
          k="view"
          label="View"
          summary={`${win} history · ${every(s.interval || 0)}`}
          digest={
            <>
              <Ln label="history" value={win} />
              <Ln label="refresh" value={every(s.interval || 0)} />
              <Ln label="drafts" value={s.drafts ? 'shown' : 'hidden'} off={!s.drafts} />
              <Ln label="key hints" value={s.keyhints === false ? 'hidden' : 'shown'} off={s.keyhints === false} />
              {/* a count, not the names: seven badges is the whole rail, and "4 of 7" is the thing
                  you actually want to know at this width */}
              <Ln label="following" value={String(followed)} off={!followed} />
              <Ln
                label="sources"
                value={
                  !o.scopes.length
                    ? 'none yet'
                    : !(s.scopes || []).length
                      ? 'none'
                      : (s.scopes || []).length === o.scopes.length
                        ? 'all'
                        : `${(s.scopes || []).length} of ${o.scopes.length}`
                }
                off={!(s.scopes || []).length}
              />
            </>
          }
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
          k="know"
          label="Knowledge"
          summary={[teams || 'no team', held.length ? `${held.length} held back` : ''].filter(Boolean).join(' · ')}
          digest={
            <>
              <Ln label="memory" value={k.memory || 'default'} />
              <Pills label="teams" values={teamList} />
              {k.store ? <Ln label="store" value={k.store} /> : null}
              {/* ponytail: a refused consent is a nudge, so it survives the collapse as a count.
                  Everything else in this digest is a setting; this one is something to go and undo. */}
              <Ln label="held back" value={String(held.length)} off={!held.length} />
            </>
          }
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
          {/* ponytail: these OPEN things, they do not toggle. The design drew them as on/off tags
              beside the voices, and they are the four screens W / P / Z / g reach. */}
          <div className="sub">
            screens <em>keys</em>
          </div>
          <div className="tags">
            {([['drafts', 'waiting', 'W'], ['share', 'shared', 'P'], ['dream', 'dream', 'Z'], ['general', 'general', 'g']] as const).map(
              ([name, label, key]) => (
                <button className="tag" key={name} onClick={() => onModal(name)}>
                  {label} <kbd className="hint">{key}</kbd>
                </button>
              ),
            )}
          </div>
        </Group>

        <Group
          k="tools"
          label="Tools"
          summary={rep?.job.running ? 'writing Friday report…' : rep?.latest ? `report ${rep.latest}` : 'Friday report'}
          digest={<Ln label="report" value={rep?.job.running ? 'writing…' : rep?.latest || 'none'} off={!rep?.latest && !rep?.job.running} />}
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
