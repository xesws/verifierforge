/// <reference lib="webworker" />

import initSqlJs from 'sql.js'
import wasmUrl from 'sql.js/dist/sql-wasm.wasm?url'
import fixtureSql from '../data/generated/nl2sql-review-sandbox.sql?raw'
import type { SqlWorkerMessage, SqlWorkerRequest } from './contracts'
import { executeSql, executionDatasetIdentity, sha256, sqliteVersion, verifyFixture } from './runtime'

const scope = self as DedicatedWorkerGlobalScope
let engineVersion = 'unknown'

const ready = Promise.all([
  initSqlJs({ locateFile: () => wasmUrl }),
  verifyFixture(fixtureSql),
]).then(([SQL]) => {
  engineVersion = sqliteVersion(SQL)
  scope.postMessage({ type: 'ready', sqliteVersion: engineVersion } satisfies SqlWorkerMessage)
  return SQL
}).catch((error: unknown) => {
  scope.postMessage({
    type: 'init_failed',
    detail: error instanceof Error ? error.message : String(error),
  } satisfies SqlWorkerMessage)
  throw error
})

scope.onmessage = async (event: MessageEvent<SqlWorkerRequest>) => {
  if (event.data.type !== 'execute') return
  try {
    const SQL = await ready
    const result = await executeSql(SQL, fixtureSql, event.data.request, engineVersion, (stage) => {
      scope.postMessage({ type: 'stage', stage } satisfies SqlWorkerMessage)
    })
    scope.postMessage({ type: 'result', result } satisfies SqlWorkerMessage)
  } catch (error) {
    const executedSql = event.data.request.rawSql.trim()
    scope.postMessage({
      type: 'result',
      result: {
        executionId: event.data.request.executionId,
        completionId: event.data.request.completionId,
        status: 'runtime_error',
        executedSql,
        sqlSha256: await sha256(executedSql),
        dataset: executionDatasetIdentity(engineVersion),
        columns: [], rows: [], rowCount: 0, truncated: false, durationMs: 0,
        executedAt: new Date().toISOString(),
        error: { code: 'sandbox_runtime_error', message: error instanceof Error ? error.message.slice(0, 500) : 'SQL sandbox failed.' },
      },
    } satisfies SqlWorkerMessage)
  }
}
