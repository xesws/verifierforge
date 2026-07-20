import { ArrowRight, Coins, Cpu, DatabaseZap, Gauge, Layers3, ShieldCheck, Sparkles } from 'lucide-react'
import { Link } from 'react-router-dom'
import { EvidenceBadge } from '../components/EvidenceBadge'
import { GlassPanel } from '../components/GlassPanel'
import { PageHeader } from '../components/PageHeader'
import { StatusPill } from '../components/StatusPill'
import { jobEvidence } from '../data/evidence'
import { clusters, productSummary } from '../data/productScenario'
import { formatCompact, formatCurrency, formatPercent } from '../utils/format'

const clusterIcons = [DatabaseZap, Layers3, Coins] as const

export function DiscoverPage() {
  return (
    <div className="page discover-page">
      <PageHeader eyebrow="Opportunity map" title="Turn recurring model spend into owned performance." description="Discover high-volume tasks, forge a specialist, and demand held-out evidence before routing traffic." action={<Link className="primary-button" to="/forge/new"><Sparkles size={17} />Forge a model</Link>} />
      <section className="summary-strip reveal reveal-1" aria-label="Portfolio summary">
        <div><Layers3 size={17} /><strong>{productSummary.clusterCount}</strong><span>task clusters</span></div>
        <div><Gauge size={17} /><strong>{formatCurrency(productSummary.monthlySpend / 1000)}k</strong><span>monthly spend</span></div>
        <div><Cpu size={17} /><strong>{productSummary.provenModels}</strong><span>proven model</span></div>
        <EvidenceBadge>Evidence locked</EvidenceBadge>
      </section>
      <section className="cluster-grid" aria-label="Discovered task clusters">
        {clusters.map((cluster, index) => {
          const Icon = clusterIcons[index]
          const isLive = cluster.status === 'live'
          return (
            <GlassPanel as="article" key={cluster.id} className={`cluster-card ${isLive ? 'cluster-live' : ''} reveal reveal-${index + 2}`}>
              <div className="cluster-top"><div className="cluster-icon"><Icon size={22} /></div><StatusPill status={cluster.status} /></div>
              <div className="cluster-copy"><span className="mono">{formatCompact(cluster.callsPerMonth)} CALLS / MONTH</span><h2>{cluster.name}</h2><p>{cluster.description}</p></div>
              <div className="cluster-cost"><span>Monthly model spend</span><strong>{formatCurrency(cluster.spendPerMonth)}</strong><small>/ month</small></div>
              {isLive ? (
                <div className="cluster-proof">
                  <div><span>Held-out pass@1</span><strong>{formatPercent(jobEvidence.heldout.passAt1Before)} <ArrowRight size={17} /> {formatPercent(jobEvidence.heldout.passAt1After)}</strong></div>
                  <span className="gain-chip"><ShieldCheck size={14} />+{jobEvidence.heldout.improvementPoints.toFixed(0)} pp</span>
                  <Link className="card-link" to={`/reports/${cluster.linkedJob}`}>View proof <ArrowRight size={15} /></Link>
                </div>
              ) : (
                <Link className="secondary-button card-action" to={`/forge/new?cluster=${cluster.id}`}>Forge <ArrowRight size={16} /></Link>
              )}
            </GlassPanel>
          )
        })}
      </section>
      <p className="evidence-footnote reveal reveal-5"><ShieldCheck size={14} /> The SQL lift is verified on a separate {jobEvidence.heldoutRows}-row held-out set. Commercial opportunity values are product scenario fixtures.</p>
    </div>
  )
}
