import { describe, expect, it } from 'vitest'
import { build, clean } from './dbgraph'

describe('db graph', () => {
  it('hangs changed columns off their table and draws only foreign keys between touched tables', () => {
    const { nodes, links } = build(
      {
        tables: [
          { name: 'sessions', change: 'altered', refs: ['users', 'accounts', 'sessions'], columns: [
            { name: 'policy_version', change: 'added' },
            { name: 'token_hash', change: 'read' },
          ] },
          { name: 'users', change: 'read' },
          { change: 'dropped' },
        ],
      },
      180,
    )
    expect(nodes.map((n) => n.id)).toEqual(['pr', 't:sessions', 't:sessions.policy_version', 't:users'])
    const ref = links.filter((l) => l.kind === 'ref').map((l) => [l.source, l.target])
    expect(ref).toEqual([['t:sessions', 't:users']])
  })

  it('survives a model answer of the wrong shape', () => {
    expect(build({ tables: 'junk' as never }, 1).nodes).toHaveLength(1)
    const junk = {
      tables: [null, 7, ['x'], { name: 'a', change: 'altered', refs: 'b', columns: [null, { name: 3, change: 'added' }] }, { name: 'a' }],
      risks: [null, { kind: 5, loc: 12, text: null }],
    } as never
    const c = clean(junk)
    expect(c.tables).toEqual([{ name: 'a', change: 'altered', refs: [], columns: [{ name: '3', change: 'added', note: '', key: '', ref: '' }] }])
    expect(c.risks).toEqual([{ kind: '5', loc: '12', text: '' }])
    // the table named twice is one node, and its column hangs off it once
    expect(build(junk, 1).nodes.map((n) => n.id)).toEqual(['pr', 't:a', 't:a.3'])
    expect(clean(null).tables).toEqual([])
  })
})
