import { BrainCircuit, CheckCircle2, ChevronDown, FileJson2 } from 'lucide-react'
import type { AgentAnalysisResponse, AgentDecision } from '../api/contracts'
import { formatCurrency, formatPercent } from '../utils/format'
import { GlassPanel } from './GlassPanel'

export function AgentDecisionCard({ analysis, fallback }: { analysis?: AgentAnalysisResponse | null; fallback?: AgentDecision | null }) {
  const decision = analysis?.decision ?? fallback
  if (!decision) return null
  return (
    <GlassPanel className="decision-card">
      <div className="decision-heading"><div><BrainCircuit size={20} /><span className="eyebrow">Agent proposes</span></div><span className={`decision-badge ${decision.decision}`}>{decision.decision.replaceAll('_', ' ')}</span></div>
      <h3>{decision.decision === 'forge' ? 'A specialist is economically defensible.' : decision.decision === 'skip' ? 'Keep this workload on the default route.' : 'Collect more evidence before spending.'}</h3>
      <p>{decision.rationale}</p>
      <div className="decision-confidence"><span>Confidence</span><strong>{formatPercent(decision.confidence, 0)}</strong></div>
      {decision.config && <div className="config-grid"><span><small>Base model</small><strong>{decision.config.base_model}</strong></span><span><small>Steps / k</small><strong>{decision.config.steps} / {decision.config.k}</strong></span><span><small>Checkpoint</small><strong>every {decision.config.checkpoint_interval}</strong></span><span><small>Budget cap</small><strong>{formatCurrency(decision.config.budget_usd_cap)}</strong></span><span><small>Provider</small><strong>{decision.config.provider_pref}</strong></span></div>}
      <div className="decision-foot"><CheckCircle2 size={15} />{analysis?.cached ? 'Reused because evidence did not change.' : 'Advisory only. Approval does not start a GPU.'}</div>
      {analysis && <AgentRunReceipt analysis={analysis} />}
    </GlassPanel>
  )
}

function AgentRunReceipt({ analysis }: { analysis: AgentAnalysisResponse }) {
  const source = analysis.provider === 'mock' ? 'mock' : analysis.cached ? 'cached' : 'live'
  const sourceLabel = source === 'mock'
    ? 'Deterministic mock · not a live model'
    : source === 'cached'
      ? 'Cached audited model run'
      : 'Fresh live model run'
  const terminal = analysis.trace?.terminal_decision ?? analysis.decision
  return (
    <section className={`agent-receipt ${source}`} aria-label="Agent run receipt">
      <header><div><FileJson2 size={15} /><strong>Agent run receipt</strong></div><span>{sourceLabel}</span></header>
      <p>This is the persisted tool trace and validated structured submission for this run. It is not hidden chain-of-thought.</p>
      <dl>
        <div><dt>Trace ID</dt><dd>{analysis.trace_id}</dd></div>
        <div><dt>Provider</dt><dd>{analysis.provider}</dd></div>
        <div><dt>Model</dt><dd>{analysis.model}</dd></div>
        <div><dt>Created</dt><dd>{new Date(analysis.created_at).toLocaleString()}</dd></div>
        <div><dt>Tokens</dt><dd>{analysis.total_input_tokens} in / {analysis.total_output_tokens} out</dd></div>
        <div><dt>Cache</dt><dd>{analysis.cached ? 'reused' : 'fresh'}</dd></div>
      </dl>
      <div className="agent-terminal"><span>Exact validated submit_decision output</span><pre>{JSON.stringify(terminal, null, 2)}</pre></div>
      {analysis.trace ? <div className="agent-tool-chain">
        <span>Read-only tool chain · {analysis.trace.tool_calls.length} calls</span>
        {analysis.trace.tool_calls.map((call, index) => <details key={`${call.tool_name}-${index}`}>
          <summary><span>{String(index + 1).padStart(2, '0')} · {call.tool_name}</span><small>{call.error ? 'error' : 'completed'}</small><ChevronDown size={14} /></summary>
          <pre>{JSON.stringify({ arguments: call.arguments, output: call.output, error: call.error, started_at: call.started_at, finished_at: call.finished_at }, null, 2)}</pre>
        </details>)}
      </div> : <div className="agent-trace-missing" role="alert">The full trace archive is unavailable. This receipt must not be treated as proof of a live run.</div>}
    </section>
  )
}
