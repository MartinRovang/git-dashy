// The schema graph's side panel (SchemaGraph.tsx): the tables that have a picked column, the chosen table's
// columns with a filter by name, type or kind, the tables pointing at it, or with nothing chosen the schemas and
// their clusters. Every table named here is a button that goes to it.
import { KINDS, ORDER, split, tint, type build, type Column, type Kind, type Table } from '../dbschema'

type Schema = ReturnType<typeof build>

type Props = {
  g: Schema
  table: Table | undefined
  shown: Column[]
  colQuery: string
  onColQuery: (q: string) => void
  kinds: Set<Kind>
  onKinds: (k: Set<Kind>) => void
  colPick: string
  pickedTables: Table[]
  onClearPick: () => void
  go: (id: string) => void
}

/** A cluster, named for its busiest table, which it goes to; the unclustered tables have nowhere to go. */
function Cluster({ g, c, go }: { g: Schema; c: string; go: (id: string) => void }) {
  const hub = c.slice(c.indexOf('/') + 1)
  const size = g.tables.filter((t) => t.group === c).length
  return hub ? (
    <button className="cref mono" title={`go to ${hub}, the busiest table in this cluster`} onClick={() => go(hub)}>
      cluster {split(hub)[1]} · {size}
    </button>
  ) : (
    <span className="mono dim">unclustered · {size}</span>
  )
}

function TableTags({ g, tables, go }: { g: Schema; tables: Table[]; go: (id: string) => void }) {
  return (
    <div className="tags">
      {tables.map((t) => (
        <button className="tag" key={t.id} onClick={() => go(t.id)} style={{ borderColor: tint(g.spaces, t.space) }}>
          {t.id}
        </button>
      ))}
    </div>
  )
}

export function SchemaPanel({ g, table, shown, colQuery, onColQuery, kinds, onKinds, colPick, pickedTables, onClearPick, go }: Props) {
  const filtering = !!colQuery.trim() || kinds.size > 0
  // chips for what this table has, and any still switched on
  const present = ORDER.filter((k) => kinds.has(k) || table?.columns.some((c) => c.kind === k))
  const flip = (k: Kind) => {
    const now = new Set(kinds)
    if (!now.delete(k)) now.add(k)
    onKinds(now)
  }
  const size = (c: string) => g.tables.filter((t) => t.group === c).length
  // clusters biggest first, the unclustered last
  const clusters = [...new Set(g.tables.map((t) => t.group))].sort((a, b) => +a.endsWith('/') - +b.endsWith('/') || size(b) - size(a))
  const pointedBy = table ? g.tables.filter((t) => t !== table && t.refs.includes(table.id)) : []

  return (
    <aside className="schcols">
      {colPick && (
        <div className="schpick">
          <div className="sub">
            column <b className="mono">{colPick}</b> · in {pickedTables.length} tables
            <button className="cref mono" onClick={onClearPick}>
              clear
            </button>
          </div>
          <TableTags g={g} tables={pickedTables} go={go} />
        </div>
      )}
      {table ? (
        <>
          <div className="schhead">
            <em style={{ color: tint(g.spaces, table.space, 70) }}>{table.space}</em>
            <b>{table.short}</b>
            <span className="mono">
              {table.columns.length} columns · {table.degree} foreign keys
            </span>
            <Cluster g={g} c={table.group} go={go} />
          </div>
          <div className="schfilter">
            <div className="search">
              <input placeholder="filter columns…" value={colQuery} onChange={(e) => onColQuery(e.target.value)} />
            </div>
            <div className="tags">
              {present.map((k) => (
                <button className="tag" key={k} aria-pressed={kinds.has(k)} onClick={() => flip(k)}>
                  <i className="kdot" style={{ background: KINDS[k][1] }} /> {KINDS[k][0]}
                </button>
              ))}
            </div>
            {filtering && (
              <div className="sub">
                {shown.length} of {table.columns.length}
                <button
                  className="cref mono"
                  onClick={() => {
                    onColQuery('')
                    onKinds(new Set())
                  }}
                >
                  clear
                </button>
              </div>
            )}
          </div>
          <ul>
            {shown.map((c) => {
              const to = c.ref && g.tables.some((t) => t.id === c.ref) ? c.ref : ''
              return (
                <li key={c.name} title={KINDS[c.kind][0]}>
                  <span className="cn">{c.name}</span>
                  {c.kind === 'pk' && <span className="ck pk mono">pk</span>}
                  {to ? (
                    <button className="cref mono" title={`go to ${to}`} onClick={() => go(to)}>
                      → {to}
                    </button>
                  ) : (
                    <span className="ct mono">{c.type}</span>
                  )}
                </li>
              )
            })}
          </ul>
          {pointedBy.length > 0 && (
            <>
              <div className="sub">referenced by</div>
              <TableTags g={g} tables={pointedBy} go={go} />
            </>
          )}
        </>
      ) : (
        <div className="schhint">
          <p>Click a table to see its columns.</p>
          <div className="sub">schemas</div>
          {g.spaces.map((s) => (
            <div key={s}>
              <div className="schkey">
                <i style={{ background: tint(g.spaces, s) }} /> <b>{s}</b> <span className="mono">{g.tables.filter((t) => t.space === s).length}</span>
              </div>
              {clusters
                .filter((c) => c.startsWith(`${s}/`))
                .map((c) => (
                  <div className="schkey sub2" key={c}>
                    <Cluster g={g} c={c} go={go} />
                  </div>
                ))}
            </div>
          ))}
        </div>
      )}
    </aside>
  )
}
