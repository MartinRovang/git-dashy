import { describe, expect, it } from 'vitest'
import { build } from './dbgraph'

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
  })
})
