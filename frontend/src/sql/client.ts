import type { SqlExecutionRequest, SqlExecutionResult, SqlExecutionStage, SqlWorkerMessage, SqlWorkerRequest } from './contracts'
import { timeoutResult } from './runtime'

export type SqlSandboxState = 'initializing' | 'ready' | 'failed'

interface PendingRun {
  request: SqlExecutionRequest
  started: number
  executedSql: string
  resolve: (result: SqlExecutionResult) => void
  reject: (error: Error) => void
  onStage: (stage: SqlExecutionStage) => void
  timer: number
}

export class BrowserSqlSandbox {
  private worker: Worker | null = null
  private pending: PendingRun | null = null
  private engineVersion = 'unknown'
  private initError: Error | null = null
  private readyPromise!: Promise<void>
  private readyResolve!: () => void
  private readonly onState: (state: SqlSandboxState, detail: string) => void

  constructor(onState: (state: SqlSandboxState, detail: string) => void) {
    this.onState = onState
    this.startWorker()
  }

  async execute(completionId: string, rawSql: string, onStage: (stage: SqlExecutionStage) => void): Promise<SqlExecutionResult> {
    if (this.pending) throw new Error('A SQL execution is already in progress.')
    await this.waitUntilReady()
    const request = {
      executionId: `sqlrun-${crypto.randomUUID()}`,
      completionId,
      rawSql,
    }
    return new Promise<SqlExecutionResult>((resolve, reject) => {
      const timer = window.setTimeout(() => void this.handleTimeout(), 5_000)
      this.pending = { request, started: performance.now(), executedSql: rawSql.trim(), resolve, reject, onStage, timer }
      this.worker?.postMessage({ type: 'execute', request } satisfies SqlWorkerRequest)
    })
  }

  dispose(): void {
    if (this.pending) {
      window.clearTimeout(this.pending.timer)
      this.pending.reject(new Error('SQL sandbox was closed.'))
      this.pending = null
    }
    this.worker?.terminate()
    this.worker = null
  }

  private startWorker(): void {
    this.initError = null
    this.onState('initializing', 'Loading the local SQLite/WASM sandbox and frozen fixture.')
    this.readyPromise = new Promise<void>((resolve) => {
      this.readyResolve = resolve
    })
    const worker = new Worker(new URL('./sqlExecution.worker.ts', import.meta.url), { type: 'module' })
    worker.onmessage = (event: MessageEvent<SqlWorkerMessage>) => this.handleMessage(event.data)
    worker.onerror = () => this.failWorker('The local SQLite/WASM worker could not start.')
    this.worker = worker
  }

  private async waitUntilReady(): Promise<void> {
    let timer = 0
    const timeout = new Promise<never>((_, reject) => {
      timer = window.setTimeout(() => reject(new Error('The local SQL sandbox did not initialize within 10 seconds.')), 10_000)
    })
    try {
      await Promise.race([this.readyPromise, timeout])
      if (this.initError) throw this.initError
    } finally {
      window.clearTimeout(timer)
    }
  }

  private handleMessage(message: SqlWorkerMessage): void {
    if (message.type === 'ready') {
      this.engineVersion = message.sqliteVersion
      this.onState('ready', `Local SQLite ${message.sqliteVersion} sandbox is ready.`)
      this.readyResolve()
      return
    }
    if (message.type === 'init_failed') {
      this.failWorker(message.detail)
      return
    }
    if (!this.pending) return
    if (message.type === 'stage' && message.stage.executionId === this.pending.request.executionId) {
      this.pending.onStage(message.stage)
      if (message.stage.executedSql) this.pending.executedSql = message.stage.executedSql
      if (message.stage.phase === 'executing') {
        window.clearTimeout(this.pending.timer)
        this.pending.timer = window.setTimeout(() => void this.handleTimeout(), 2_000)
      }
      return
    }
    if (message.type === 'result' && message.result.executionId === this.pending.request.executionId) {
      const pending = this.pending
      window.clearTimeout(pending.timer)
      this.pending = null
      pending.resolve(message.result)
    }
  }

  private failWorker(detail: string): void {
    const error = new Error(detail.slice(0, 500))
    this.initError = error
    this.onState('failed', error.message)
    this.readyResolve()
    if (this.pending) {
      window.clearTimeout(this.pending.timer)
      this.pending.reject(error)
      this.pending = null
    }
  }

  private async handleTimeout(): Promise<void> {
    if (!this.pending) return
    const pending = this.pending
    this.pending = null
    window.clearTimeout(pending.timer)
    this.worker?.terminate()
    const duration = performance.now() - pending.started
    pending.onStage({
      executionId: pending.request.executionId,
      phase: 'timeout',
      detail: 'The two second query limit was reached; the isolated worker was terminated.',
      at: new Date().toISOString(),
    })
    pending.resolve(await timeoutResult(pending.request, pending.executedSql, this.engineVersion, duration))
    this.startWorker()
  }
}
