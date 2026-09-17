import { describe, expect, it } from 'vitest'
import { build, columnNodes, kind, search, split } from './dbschema'

describe('schema graph', () => {
  it('tells a column kind from its key, then its type', () => {
    expect(kind('fk', 'text')).toBe('fk')
    expect(kind('pk', 'uuid')).toBe('pk')
    expect(kind('', 'text[]')).toBe('array')
    expect(kind('', 'float []')).toBe('array')
    expect(kind('', 'varchar(255)')).toBe('text')
    expect(kind('', 'timestamp without time zone')).toBe('time')
    expect(kind('', 'timestamptz')).toBe('time')
    expect(kind('', 'bigint')).toBe('number')
    expect(kind('', 'numeric(10, 2)')).toBe('number')
    expect(kind('', 'jsonb')).toBe('json')
    expect(kind('', 'boolean')).toBe('bool')
    expect(kind('', 'bytea')).toBe('binary')
    expect(kind('', 'top_bodypart_enum')).toBe('other')
    expect(split('restricted.users')).toEqual(['restricted', 'users'])
    expect(split('users')).toEqual(['public', 'users'])
  })

  it('links foreign keys between tables it has, counts them, and groups tables by schema', () => {
    const { tables, links, spaces } = build({
      tables: [
        { name: 'restricted.users', refs: [], columns: [{ name: 'id', note: 'text', key: 'pk' }] },
        { name: 'sessions', refs: ['restricted.users', 'restricted.users', 'gone', 'sessions'], columns: [{ name: 'user_id', note: 'text', key: 'fk', ref: 'restricted.users' }] },
      ],
    })
    expect(links.map((l) => [l.source, l.target])).toEqual([['sessions', 'restricted.users']])
    expect(tables.map((t) => [t.id, t.space, t.short, t.degree])).toEqual([
      ['restricted.users', 'restricted', 'users', 1],
      ['sessions', 'public', 'sessions', 1],
    ])
    expect(tables[1].columns).toEqual([{ name: 'user_id', type: 'text', kind: 'fk', ref: 'restricted.users' }])
    expect(spaces).toEqual(['public', 'restricted'])
  })
  it('clusters tables inside a schema by the columns that reference across them', () => {
    const t = (name: string, refs: string[] = [], cols: string[] = refs) => ({ name, refs, columns: cols.map((r, i) => ({ name: `c${i}`, note: 'text', key: 'fk', ref: r })) })
    // two tight triangles, a single weak link between them, a loner, and a table in another schema
    const { tables } = build({
      tables: [
        t('a1', ['a2', 'a3']), t('a2', ['a3']), t('a3'),
        t('b1', ['b2', 'b3', 'a1'], ['b2', 'b2', 'b3', 'a1']), t('b2', ['b3']), t('b3'),
        t('lonely'), t('pair1', ['pair2']), t('pair2'),
        t('other.x', ['a1']),
      ],
    })
    const g = Object.fromEntries(tables.map((x) => [x.id, x.group]))
    expect(g.a2).toBe(g.a1)
    expect(g.a3).toBe(g.a1)
    expect(g.b2).toBe(g.b1)
    expect(g.b1).not.toBe(g.a1)
    expect(g.b1.startsWith('public/')).toBe(true)
    expect(g.lonely).toBe('public/')
    expect(g.pair1).toBe('public/')
    // a foreign key to another schema does not pull a table into a cluster there
    expect(g['other.x']).toBe('other/')
  })
  it('searches tables and columns, lights every table either matches, best match first', () => {
    const { tables } = build({
      tables: [
        { name: 'restricted.users', columns: [{ name: 'user_uid', note: 'text' }, { name: 'created', note: 'timestamp' }] },
        { name: 'sessions', columns: [{ name: 'user_uid', note: 'text', key: 'fk', ref: 'restricted.users' }] },
        { name: 'user_settings', columns: [{ name: 'theme', note: 'text' }] },
        { name: 'audit', columns: [{ name: 'actor', note: 'text' }] },
      ],
    })
    const { hits, lit } = search(tables, ' User')
    expect([...lit].sort()).toEqual(['restricted.users', 'sessions', 'user_settings'])
    // tables that start with it, then columns that start with it
    expect(hits.map((h) => `${h.table}.${h.column}`)).toEqual(['restricted.users.', 'user_settings.', 'restricted.users.user_uid', 'sessions.user_uid'])
    expect(search(tables, 'restricted.').lit).toEqual(new Set(['restricted.users']))
    expect(search(tables, 'created').hits).toEqual([{ table: 'restricted.users', column: 'created', type: 'timestamp' }])
    expect(search(tables, '  ').lit.size).toBe(0)
  })
  it('folds columns by name across tables, keeping the kind most of them are', () => {
    const { tables } = build({
      tables: [
        { name: 'a', columns: [{ name: 'user_uid', note: 'text', key: 'pk' }, { name: 'created', note: 'timestamp' }] },
        { name: 'b', columns: [{ name: 'user_uid', note: 'text', key: 'fk', ref: 'a' }] },
        { name: 'c', columns: [{ name: 'user_uid', note: 'text', key: 'fk', ref: 'a' }] },
      ],
    })
    expect(columnNodes(tables)).toEqual([
      { id: 'col:created', name: 'created', kind: 'time', tables: ['a'] },
      { id: 'col:user_uid', name: 'user_uid', kind: 'fk', tables: ['a', 'b', 'c'] },
    ])
  })
})
