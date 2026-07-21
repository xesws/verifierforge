import { ArrowRight, BadgeDollarSign, Check, CircleDollarSign, Rows3, ShieldCheck, Target } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { selectedCheckpoint } from '../api/mappers'
import { ArenaComparison } from '../components/ArenaComparison'
import { EvidenceBadge } from '../components/EvidenceBadge'
import { GlassPanel } from '../components/GlassPanel'
import { HeldoutChart } from '../components/HeldoutChart'
import { MetricCard } from '../components/MetricCard'
import { ErrorState, LoadingState } from '../components/ResourceState'
import { useAuth } from '../state/AuthContext'
import { useResource } from '../state/useResource'
import { formatCurrency, formatPercent } from '../utils/format'

function CountUp({ value }: { value: number }) {
  const [shown, setShown] = useState(0)
  useEffect(() => { if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { setShown(value); return }; const start = performance.now(); let frame = 0; const tick = (now: number) => { const progress = Math.min((now - start) / 700, 1); setShown(value * (1 - (1 - progress) ** 3)); if (progress < 1) frame = window.requestAnimationFrame(tick) }; frame = window.requestAnimationFrame(tick); return () => window.cancelAnimationFrame(frame) }, [value])
  return <>{shown.toFixed(1)}</>
}

export function ReportPage() {
  const { jobId = '' } = useParams()
  const { client } = useAuth()
  const resource = useResource(async () => { if (!client) throw new Error('API client is unavailable'); return (await client.getJob(jobId)).data }, [client, jobId], { enabled: Boolean(client && jobId) })
  if (resource.status === 'loading' || resource.status === 'idle') return <LoadingState label="Loading the frozen report…" />
  if (resource.status === 'error' || !resource.data) return <ErrorState message={resource.error ?? 'Report unavailable'} onRetry={resource.reload} />
  const job = resource.data
  const report = job.report
  if (!report || !report.arena || !report.savings_projection) return <ErrorState message="This job has no complete held-out report yet." onRetry={resource.reload} />
  const before = report.baseline_pass_at_1
  const after = report.final_pass_at_1
  const lift = (after - before) * 100
  const selected = selectedCheckpoint(job)
  const provenanceRows = report.provenance?.sources.length ?? 0

  return <div className="page report-page">
    <header className="report-hero reveal"><div className="report-kicker"><EvidenceBadge>Frozen held-out evaluation</EvidenceBadge><span className="verdict-badge"><Check size={13} />{report.verdict}</span></div><div className="report-score"><div><span>BEFORE</span><strong>{formatPercent(before)}</strong></div><ArrowRight size={34} strokeWidth={1.4} /><div className="after-score"><span>AFTER{selected ? ` · STEP ${selected}` : ''}</span><strong>{formatPercent(after)}</strong></div><div className="gain-orb"><span>HELD-OUT LIFT</span><strong>+<CountUp value={lift} /> pp</strong></div></div><p>{report.narrative}</p></header>
    <section className="report-metrics reveal reveal-1"><MetricCard label="ARENA" value={`${report.arena.samples.length} samples`} note={`${provenanceRows} frozen sources`} icon={<Rows3 size={17} />} /><MetricCard label="CONTROL AFTER" value={formatPercent(report.control_final_pass_at_1)} note="random-reward comparison" icon={<Target size={17} />} tone="blue" /><MetricCard label="SELECTED" value={selected ? `Step ${selected}` : 'Frozen'} note="maximum held-out pass@1" icon={<ShieldCheck size={17} />} tone="green" /></section>
    <div className="report-grid"><GlassPanel className="heldout-panel reveal reveal-2"><div className="panel-heading"><div><span className="eyebrow"><ShieldCheck size={13} /> Before / after branch</span><h2>The tuned path separates from control.</h2><p>All three values come from the report contract, not a presentation fixture.</p></div></div><HeldoutChart baseline={before} tuned={after} control={report.control_final_pass_at_1} /><div className="selection-explainer"><strong>Verdict: {report.verdict}</strong><p>{report.narrative}</p></div></GlassPanel><GlassPanel className="savings-card reveal reveal-3"><div className="savings-icon"><CircleDollarSign size={25} /></div><span>Projected monthly savings</span><strong>{formatCurrency(report.projected_monthly_savings_usd ?? 0)}</strong><p>{report.savings_projection.formula}</p><ul>{report.savings_projection.assumptions.map((assumption) => <li key={assumption}>{assumption}</li>)}</ul><div className="projection-label"><BadgeDollarSign size={14} />Explicit assumptions · API payload</div></GlassPanel></div>
    <ArenaComparison arena={report.arena} />
    <div className="report-footer reveal reveal-5"><p><ShieldCheck size={17} /><span><strong>Evidence boundary locked.</strong> Arena samples are from the unseen held-out set and include failures.</span></p><Link className="primary-button" to="/ship/data-pull-sql">Configure routing <ArrowRight size={16} /></Link></div>
  </div>
}
