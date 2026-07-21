import type { SqlJsStatic, SqlValue } from 'sql.js'
import manifest from '../data/generated/nl2sql-review-sandbox.json'
import type {
  SqlCell,
  SqlDatasetIdentity,
  SqlExecutionRequest,
  SqlExecutionResult,
  SqlExecutionStage,
  SqlExecutionStatus,
} from './contracts'
import { assertReadOnlySql, normalizeModelSql, SqlRejectedError } from './safety'

export const SQL_JS_VERSION = '1.14.1'
export const MAX_RESULT_ROWS = 100

export async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

export async function verifyFixture(fixtureSql: string): Promise<string> {
  const canonical = fixtureSql.endsWith('\n') ? fixtureSql.slice(0, -1) : fixtureSql
  const actual = await sha256(canonical)
  if (actual !== manifest.schemaSha256) {
    throw new Error(`Frozen SQL fixture identity mismatch: expected ${manifest.schemaSha256}, got ${actual}`)
  }
  return canonical
}

export function sqliteVersion(SQL: SqlJsStatic): string {
  const database = new SQL.Database()
  try {
    const result = database.exec('SELECT sqlite_version() AS version')
    return String(result[0]?.values[0]?.[0] ?? 'unknown')
  } finally {
    database.close()
  }
}

export async function executeSql(
  SQL: SqlJsStatic,
  fixtureSql: string,
  request: SqlExecutionRequest,
  engineVersion: string,
  emit: (stage: SqlExecutionStage) => void = () => undefined,
): Promise<SqlExecutionResult> {
  const started = performance.now()
  let executedSql = request.rawSql.trim()
  let database: InstanceType<SqlJsStatic['Database']> | null = null
  try {
    emitStage(emit, request.executionId, 'validating', 'Validating the exact generated SQL and frozen dataset identity.')
    executedSql = normalizeModelSql(request.rawSql)
    assertReadOnlySql(executedSql)
    const canonicalFixture = await verifyFixture(fixtureSql)
    database = new SQL.Database()
    database.run(canonicalFixture)
    database.run('PRAGMA query_only = ON')
    emitStage(emit, request.executionId, 'database_ready', 'Fresh in-memory SQLite loaded the frozen schema and fixture rows.')

    const statements = Array.from(database.iterateStatements(executedSql), (statement) => statement.getSQL())
    if (statements.length !== 1) {
      throw new SqlRejectedError('not_single_statement', 'Exactly one SQL statement is required; nothing was executed.')
    }
    executedSql = statements[0].trim()
    assertReadOnlySql(executedSql)
    emitStage(emit, request.executionId, 'executing', 'SQLite is executing this SQL now.', executedSql)

    const statement = database.prepare(executedSql)
    const columns = statement.getColumnNames()
    const rows: SqlCell[][] = []
    let truncated = false
    try {
      while (statement.step()) {
        if (rows.length === MAX_RESULT_ROWS) { truncated = true; break }
        rows.push(statement.get().map(normalizeCell))
      }
    } finally {
      statement.free()
    }
    emitStage(emit, request.executionId, 'succeeded', `SQLite returned ${rows.length} row${rows.length === 1 ? '' : 's'}.`)
    return resultFor(request, 'succeeded', executedSql, engineVersion, started, columns, rows, truncated, null)
  } catch (error) {
    if (error instanceof SqlRejectedError) {
      emitStage(emit, request.executionId, 'rejected', error.message)
      return resultFor(request, 'rejected', executedSql, engineVersion, started, [], [], false, { code: error.code, message: error.message })
    }
    const message = boundedError(error)
    emitStage(emit, request.executionId, 'failed', message)
    return resultFor(request, 'sqlite_error', executedSql, engineVersion, started, [], [], false, { code: 'sqlite_execution_error', message })
  } finally {
    database?.close()
  }
}

export async function timeoutResult(
  request: SqlExecutionRequest,
  executedSql: string,
  engineVersion: string,
  durationMs: number,
): Promise<SqlExecutionResult> {
  return {
    executionId: request.executionId,
    completionId: request.completionId,
    status: 'timeout',
    executedSql,
    sqlSha256: await sha256(executedSql),
    dataset: executionDatasetIdentity(engineVersion),
    columns: [],
    rows: [],
    rowCount: 0,
    truncated: false,
    durationMs,
    executedAt: new Date().toISOString(),
    error: { code: 'query_timeout', message: 'SQLite exceeded the two second execution limit and the isolated worker was terminated.' },
  }
}

function resultFor(
  request: SqlExecutionRequest,
  status: SqlExecutionStatus,
  executedSql: string,
  engineVersion: string,
  started: number,
  columns: string[],
  rows: SqlCell[][],
  truncated: boolean,
  error: { code: string; message: string } | null,
): Promise<SqlExecutionResult> {
  return sha256(executedSql).then((sqlSha256) => ({
    executionId: request.executionId,
    completionId: request.completionId,
    status,
    executedSql,
    sqlSha256,
    dataset: executionDatasetIdentity(engineVersion),
    columns,
    rows,
    rowCount: rows.length,
    truncated,
    durationMs: performance.now() - started,
    executedAt: new Date().toISOString(),
    error,
  }))
}

export function executionDatasetIdentity(engineVersion: string): SqlDatasetIdentity {
  return {
    datasetId: manifest.datasetId,
    sourceSha256: manifest.sourceSha256,
    schemaSha256: manifest.schemaSha256,
    engine: 'sqlite',
    mode: 'browser_ephemeral_wasm',
    sqlJsVersion: SQL_JS_VERSION,
    sqliteVersion: engineVersion,
  }
}

function normalizeCell(value: SqlValue): SqlCell {
  if (value instanceof Uint8Array) return `[BLOB ${value.byteLength} bytes]`
  return value
}

function emitStage(
  emit: (stage: SqlExecutionStage) => void,
  executionId: string,
  phase: SqlExecutionStage['phase'],
  detail: string,
  executedSql?: string,
): void {
  emit({ executionId, phase, detail, at: new Date().toISOString(), executedSql })
}

function boundedError(error: unknown): string {
  const detail = error instanceof Error ? error.message : String(error)
  return detail.replace(/\s+/g, ' ').trim().slice(0, 500) || 'SQLite execution failed.'
}
