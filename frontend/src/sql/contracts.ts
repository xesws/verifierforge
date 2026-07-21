export type SqlExecutionStatus = 'succeeded' | 'rejected' | 'sqlite_error' | 'timeout' | 'runtime_error'
export type SqlExecutionPhase = 'validating' | 'database_ready' | 'executing' | 'succeeded' | 'rejected' | 'failed' | 'timeout'
export type SqlCell = string | number | null

export interface SqlDatasetIdentity {
  datasetId: string
  sourceSha256: string
  schemaSha256: string
  engine: 'sqlite'
  mode: 'browser_ephemeral_wasm'
  sqlJsVersion: string
  sqliteVersion: string
}

export interface SqlExecutionError {
  code: string
  message: string
}

export interface SqlExecutionResult {
  executionId: string
  completionId: string
  status: SqlExecutionStatus
  executedSql: string
  sqlSha256: string
  dataset: SqlDatasetIdentity
  columns: string[]
  rows: SqlCell[][]
  rowCount: number
  truncated: boolean
  durationMs: number
  executedAt: string
  error: SqlExecutionError | null
}

export interface SqlExecutionStage {
  executionId: string
  phase: SqlExecutionPhase
  detail: string
  at: string
  executedSql?: string
}

export interface SqlExecutionRequest {
  executionId: string
  completionId: string
  rawSql: string
}

export type SqlWorkerRequest = {
  type: 'execute'
  request: SqlExecutionRequest
}

export type SqlWorkerMessage =
  | { type: 'ready'; sqliteVersion: string }
  | { type: 'init_failed'; detail: string }
  | { type: 'stage'; stage: SqlExecutionStage }
  | { type: 'result'; result: SqlExecutionResult }
