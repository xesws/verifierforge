import { AlertTriangle, CheckCircle2, Clock3, Database, Play, TerminalSquare } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { BrowserSqlSandbox, type SqlSandboxState } from '../sql/client'
import type { SqlCell, SqlExecutionResult, SqlExecutionStage } from '../sql/contracts'

interface SqlExecutionPanelProps {
  completionId: string
  sql: string
  onBusyChange: (busy: boolean) => void
}

export function SqlExecutionPanel({ completionId, sql, onBusyChange }: SqlExecutionPanelProps) {
  const sandboxRef = useRef<BrowserSqlSandbox | null>(null)
  const outcomeRef = useRef<HTMLHeadingElement | null>(null)
  const [sandboxState, setSandboxState] = useState<SqlSandboxState>('initializing')
  const [sandboxDetail, setSandboxDetail] = useState('Loading the local SQLite/WASM sandbox and frozen fixture.')
  const [executing, setExecuting] = useState(false)
  const [elapsedMs, setElapsedMs] = useState(0)
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const [stages, setStages] = useState<SqlExecutionStage[]>([])
  const [result, setResult] = useState<SqlExecutionResult | null>(null)
  const [transportError, setTransportError] = useState<string | null>(null)

  useEffect(() => {
    const sandbox = new BrowserSqlSandbox((state, detail) => {
      setSandboxState(state)
      setSandboxDetail(detail)
    })
    sandboxRef.current = sandbox
    return () => {
      sandbox.dispose()
      sandboxRef.current = null
      onBusyChange(false)
    }
  }, [onBusyChange])

  useEffect(() => {
    setResult(null)
    setStages([])
    setTransportError(null)
  }, [completionId, sql])

  useEffect(() => {
    if (!executing || startedAt === null) return
    const timer = window.setInterval(() => setElapsedMs(performance.now() - startedAt), 100)
    return () => window.clearInterval(timer)
  }, [executing, startedAt])

  async function runSql() {
    if (!sandboxRef.current || sandboxState !== 'ready' || executing) return
    const started = performance.now()
    setExecuting(true)
    onBusyChange(true)
    setStartedAt(started)
    setElapsedMs(0)
    setStages([])
    setResult(null)
    setTransportError(null)
    try {
      const execution = await sandboxRef.current.execute(completionId, sql, (stage) => {
        setStages((current) => [...current, stage])
      })
      setElapsedMs(performance.now() - started)
      setResult(execution)
      window.setTimeout(() => outcomeRef.current?.focus(), 0)
    } catch (error) {
      setTransportError(error instanceof Error ? error.message : 'The local SQL sandbox failed.')
    } finally {
      setExecuting(false)
      onBusyChange(false)
    }
  }

  const succeeded = result?.status === 'succeeded'
  const unavailable = sandboxState !== 'ready'
  return <section className="sql-execution-panel" aria-busy={executing} aria-label="Live SQL execution">
    <header className="sql-execution-header">
      <div>
        <span className="eyebrow"><Database size={13} /> Live browser execution</span>
        <h3>Run the generated SQL on real fixture rows</h3>
        <p>queries run against the frozen demo dataset (the same schema the verifier trained on)</p>
        <small>This proves read-only execution and displays actual rows; it does not prove semantic correctness.</small>
      </div>
      <button className="primary-button" type="button" disabled={executing || unavailable} onClick={() => void runSql()}>
        {executing ? <><Clock3 size={15} />Running live · {(elapsedMs / 1000).toFixed(1)}s</> : <><Play size={15} />Run SQL on frozen demo data</>}
      </button>
    </header>
    <div className={`sandbox-readiness ${sandboxState}`} role={sandboxState === 'failed' ? 'alert' : 'status'} aria-live="polite">
      {sandboxState === 'ready' ? <CheckCircle2 size={15} /> : sandboxState === 'failed' ? <AlertTriangle size={15} /> : <Clock3 size={15} />}
      <span><strong>{sandboxState === 'ready' ? 'Local sandbox ready' : sandboxState === 'failed' ? 'Local sandbox unavailable' : 'Preparing local sandbox'}</strong><small>{sandboxDetail}</small></span>
    </div>
    <section className="activity-console sql-execution-console" aria-label="Live SQL execution activity">
      <header><TerminalSquare size={14} /><strong>SQL execution activity</strong><span>{executing ? 'live' : result ? 'complete' : 'idle'}</span></header>
      <ol role="log" aria-live="polite">
        {stages.length ? stages.map((stage, index) => <li key={`${stage.at}-${stage.phase}-${index}`}><time>{new Date(stage.at).toLocaleTimeString()}</time><b>{stage.phase.replace('_', ' ')}</b><span>{stage.detail}</span></li>) : <li><time>—</time><b>waiting</b><span>Press Run SQL to create a fresh in-memory database and execute this exact completion.</span></li>}
      </ol>
    </section>
    {transportError && <div className="sql-execution-error" role="alert"><AlertTriangle size={17} /><div><strong>Local SQL sandbox failed</strong><p>{transportError}</p></div></div>}
    {result && <section className={`sql-execution-outcome ${succeeded ? 'success' : 'error'}`} role={succeeded ? 'status' : 'alert'}>
      <header>
        <div>
          <span className="eyebrow">{succeeded ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />} Result</span>
          <h3 ref={outcomeRef} tabIndex={-1}>{succeeded ? 'Live execution result' : result.status === 'rejected' ? 'SQL was not executed' : result.status === 'timeout' ? 'Execution timed out' : 'Execution failed in isolated SQLite'}</h3>
        </div>
        <span className={`execution-status ${result.status}`}>{result.status.replace('_', ' ')}</span>
      </header>
      {succeeded ? <ResultTable result={result} /> : <div className="sql-error-detail"><code>{result.error?.code ?? result.status}</code><p>{result.error?.message ?? 'The SQL sandbox did not return an error detail.'}</p></div>}
      <dl className="execution-metadata">
        <div><dt>Execution ID</dt><dd title={result.executionId}>{result.executionId}</dd></div>
        <div><dt>Rows returned</dt><dd>{result.rowCount}{result.truncated ? '+' : ''}</dd></div>
        <div><dt>Execution</dt><dd>{result.durationMs.toFixed(2)} ms</dd></div>
        <div><dt>Round trip</dt><dd>{(elapsedMs / 1000).toFixed(2)} s</dd></div>
        <div><dt>SQLite</dt><dd>{result.dataset.sqliteVersion}</dd></div>
        <div><dt>Mode</dt><dd>{result.dataset.mode}</dd></div>
        <div><dt>Dataset</dt><dd title={result.dataset.datasetId}>{result.dataset.datasetId}</dd></div>
        <div><dt>Schema SHA</dt><dd title={result.dataset.schemaSha256}>{result.dataset.schemaSha256.slice(0, 12)}…</dd></div>
        <div><dt>SQL SHA</dt><dd title={result.sqlSha256}>{result.sqlSha256 ? `${result.sqlSha256.slice(0, 12)}…` : 'unavailable'}</dd></div>
        <div><dt>Executed at</dt><dd>{new Date(result.executedAt).toLocaleTimeString()}</dd></div>
      </dl>
    </section>}
  </section>
}

function ResultTable({ result }: { result: SqlExecutionResult }) {
  if (!result.rows.length) return <div className="sql-empty-result"><CheckCircle2 size={17} /><span><strong>Query completed with zero rows.</strong><small>The empty result came from a real SQLite execution.</small></span></div>
  return <>
    {result.truncated && <div className="sql-truncated" role="status">Showing the first 100 rows. The result was truncated in the browser sandbox.</div>}
    <div className="sql-result-scroll" tabIndex={0}>
      <table>
        <caption>Rows returned by the live SQLite execution</caption>
        <thead><tr>{result.columns.map((column) => <th key={column} scope="col">{column}</th>)}</tr></thead>
        <tbody>{result.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, columnIndex) => <td key={`${rowIndex}-${result.columns[columnIndex] ?? columnIndex}`}>{renderCell(cell)}</td>)}</tr>)}</tbody>
      </table>
    </div>
  </>
}

function renderCell(cell: SqlCell) {
  if (cell === null) return <span className="sql-null">NULL</span>
  return String(cell)
}
