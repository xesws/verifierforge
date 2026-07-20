import { ArrowRight, BadgeDollarSign, Check, CircleDollarSign, Rows3, ShieldCheck, Target } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArenaComparison } from '../components/ArenaComparison'
import { EvidenceBadge } from '../components/EvidenceBadge'
import { GlassPanel } from '../components/GlassPanel'
import { HeldoutChart } from '../components/HeldoutChart'
import { MetricCard } from '../components/MetricCard'
import { jobEvidence } from '../data/evidence'
import { productSummary } from '../data/productScenario'
import { formatCurrency, formatPercent } from '../utils/format'

function CountUp({ value }: { value: number }) {
  const [shown, setShown] = useState(0)
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { setShown(value); return }
    const started = performance.now()
    let frame = 0
    const tick = (now: number) => {
      const progress = Math.min((now - started) / 700, 1)
      setShown(value * (1 - (1 - progress) ** 3))
      if (progress < 1) frame = window.requestAnimationFrame(tick)
    }
    frame = window.requestAnimationFrame(tick)
    return () => window.cancelAnimationFrame(frame)
  }, [value])
  return <>{shown.toFixed(1)}</>
}

export function ReportPage() {
  return (
    <div className="page report-page">
      <header className="report-hero reveal">
        <div className="report-kicker"><EvidenceBadge>60-row held-out evaluation</EvidenceBadge><span className="verdict-badge"><Check size={13} />{jobEvidence.verdict}</span></div>
        <div className="report-score"><div><span>BEFORE</span><strong>{formatPercent(jobEvidence.heldout.passAt1Before)}</strong></div><ArrowRight size={34} strokeWidth={1.4} /><div className="after-score"><span>AFTER · STEP {jobEvidence.selectedCheckpoint}</span><strong>{formatPercent(jobEvidence.heldout.passAt1After)}</strong></div><div className="gain-orb"><span>HELD-OUT LIFT</span><strong>+<CountUp value={jobEvidence.heldout.improvementPoints} /> pp</strong></div></div>
        <p>A specialist earned a real shipping decision: independent pass@1 improved by twenty percentage points without borrowing from the training monitor.</p>
      </header>
      <section className="report-metrics reveal reveal-1">
        <MetricCard label="HELD-OUT ROWS" value={jobEvidence.heldoutRows.toString()} note="independent evaluation" icon={<Rows3 size={17} />} />
        <MetricCard label="HELD-OUT PASS@8" value={`${formatPercent(jobEvidence.heldout.passAt8Before)} → ${formatPercent(jobEvidence.heldout.passAt8After)}`} note="eight attempts per prompt" icon={<Target size={17} />} tone="blue" />
        <MetricCard label="SELECTED" value={`Step ${jobEvidence.selectedCheckpoint}`} note="maximum pass@1" icon={<ShieldCheck size={17} />} tone="green" />
      </section>
      <div className="report-grid">
        <GlassPanel className="heldout-panel reveal reveal-2"><div className="panel-heading"><div><span className="eyebrow"><ShieldCheck size={13} /> Held-out checkpoint selection</span><h2>Step 350 wins on pass@1.</h2><p>Eight independent checkpoint evaluations; baseline remains visible for context.</p></div></div><HeldoutChart /><div className="selection-explainer"><strong>Why not step 400?</strong><p>Step 400 reaches a higher pass@8, but pass@1 falls to {formatPercent(jobEvidence.checkpoints.at(-1)?.pass_at_1 ?? 0)}. The locked rule selects maximum held-out pass@1, so step {jobEvidence.selectedCheckpoint} wins.</p></div></GlassPanel>
        <GlassPanel className="savings-card reveal reveal-3"><div className="savings-icon"><CircleDollarSign size={25} /></div><span>Projected monthly savings</span><strong>{formatCurrency(productSummary.projectedMonthlySavings)}</strong><p>Product scenario estimate based on Data Pull SQL volume and routing assumptions.</p><div className="projection-label"><BadgeDollarSign size={14} />Projection · not held-out evidence</div></GlassPanel>
      </div>
      <ArenaComparison />
      <div className="report-footer reveal reveal-5"><p><ShieldCheck size={17} /><span><strong>Evidence boundary locked.</strong> Checkpoints above come from the committed held-out report. Savings and arena samples are labeled product fixtures.</span></p><Link className="primary-button" to="/ship/data-pull-sql">Configure local ship plan <ArrowRight size={16} /></Link></div>
    </div>
  )
}
