import initSqlJs from 'sql.js'
import { describe, expect, it } from 'vitest'
import fixtureSql from '../data/generated/nl2sql-review-sandbox.sql?raw'
import type { SqlExecutionRequest } from '../sql/contracts'
import { executeSql, sqliteVersion, verifyFixture } from '../sql/runtime'

async function runtime() {
  const SQL = await initSqlJs()
  return { SQL, version: sqliteVersion(SQL) }
}

function request(sql: string, id = 'sqlrun-test'): SqlExecutionRequest {
  return { executionId: id, completionId: 'chatcmpl-test', rawSql: sql }
}

describe('browser SQL runtime', () => {
  it('verifies and executes the canonical frozen fixture', async () => {
    const { SQL, version } = await runtime()
    await expect(verifyFixture(fixtureSql)).resolves.toContain('CREATE TABLE employees')

    const result = await executeSql(
      SQL,
      fixtureSql,
      request('SELECT name, salary FROM employees ORDER BY salary DESC LIMIT 2'),
      version,
    )

    expect(result.status).toBe('succeeded')
    expect(result.columns).toEqual(['name', 'salary'])
    expect(result.rows).toEqual([['Grace', 170000], ['Linus', 160000]])
    expect(result.dataset.mode).toBe('browser_ephemeral_wasm')
    expect(result.dataset.schemaSha256).toBe('688cfaf8a4fff6743b541dcfec2c2de10793232458342c8160178394c510631d')
  })

  it('executes the schema-grounded Engineering sample query', async () => {
    const { SQL, version } = await runtime()
    const result = await executeSql(
      SQL,
      fixtureSql,
      request("SELECT e.name FROM employees AS e JOIN departments AS d ON d.id = e.department_id WHERE e.active = 1 AND d.name = 'Engineering' ORDER BY e.name"),
      version,
    )

    expect(result.status).toBe('succeeded')
    expect(result.rows).toEqual([['Ada'], ['Frances'], ['Grace']])
  })

  it('executes a complete Markdown fence without silently selecting a statement', async () => {
    const { SQL, version } = await runtime()
    const success = await executeSql(SQL, fixtureSql, request('```sql\nSELECT name FROM departments WHERE id = 1;\n```'), version)
    const multiple = await executeSql(SQL, fixtureSql, request('```sql\nSELECT 1; SELECT 2;\n```'), version)

    expect(success.executedSql).toBe('SELECT name FROM departments WHERE id = 1;')
    expect(success.rows).toEqual([['Engineering']])
    expect(multiple.status).toBe('rejected')
    expect(multiple.error?.code).toBe('not_single_statement')
  })

  it('rejects writes and exposes real SQLite errors', async () => {
    const { SQL, version } = await runtime()
    const write = await executeSql(SQL, fixtureSql, request('DELETE FROM employees'), version)
    const badColumn = await executeSql(SQL, fixtureSql, request('SELECT DepartmentName FROM departments'), version)

    expect(write.status).toBe('rejected')
    expect(write.error?.code).toBe('not_read_only_query')
    expect(badColumn.status).toBe('sqlite_error')
    expect(badColumn.error?.message).toContain('no such column')
  })

  it('caps output at 100 rows', async () => {
    const { SQL, version } = await runtime()
    const result = await executeSql(
      SQL,
      fixtureSql,
      request('WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 150) SELECT x FROM n'),
      version,
    )

    expect(result.status).toBe('succeeded')
    expect(result.rows).toHaveLength(100)
    expect(result.truncated).toBe(true)
  })

  it('preserves NULL values and reports a genuine zero-row result', async () => {
    const { SQL, version } = await runtime()
    const nullResult = await executeSql(SQL, fixtureSql, request('SELECT NULL AS optional_value'), version)
    const emptyResult = await executeSql(SQL, fixtureSql, request('SELECT name FROM employees WHERE id = -1'), version)

    expect(nullResult.rows).toEqual([[null]])
    expect(emptyResult.status).toBe('succeeded')
    expect(emptyResult.columns).toEqual(['name'])
    expect(emptyResult.rows).toEqual([])
    expect(emptyResult.rowCount).toBe(0)
  })

  it('proves repeated requests are fresh executions rather than canned rows', async () => {
    const { SQL, version } = await runtime()
    const sql = "SELECT name, hex(randomblob(16)) AS live_nonce FROM employees WHERE id = 1"
    const first = await executeSql(SQL, fixtureSql, request(sql, 'sqlrun-first'), version)
    const second = await executeSql(SQL, fixtureSql, request(sql, 'sqlrun-second'), version)

    expect(first.rows[0]?.[0]).toBe('Ada')
    expect(second.rows[0]?.[0]).toBe('Ada')
    expect(first.rows[0]?.[1]).not.toBe(second.rows[0]?.[1])
    expect(first.executionId).not.toBe(second.executionId)
  })
})
