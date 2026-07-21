import { BrainCircuit, CheckCircle2 } from 'lucide-react'
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
    </GlassPanel>
  )
}
