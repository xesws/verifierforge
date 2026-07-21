import { ArrowRight, Coins, Cpu, DatabaseZap, Gauge, Layers3, Link2, ShieldCheck, Sparkles } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError } from '../api/client'
import type { AgentAnalysisResponse, Cluster } from '../api/contracts'
import { AgentDecisionCard } from '../components/AgentDecisionCard'
import { EvidenceBadge } from '../components/EvidenceBadge'
import { ErrorState, LoadingState } from '../components/ResourceState'
import { GlassPanel } from '../components/GlassPanel'
import { PageHeader } from '../components/PageHeader'
import { StatusPill } from '../components/StatusPill'
import { clusterDescriptions, SQL_SAMPLE_SOURCE } from '../data/presentation'
import { useAuth } from '../state/AuthContext'
import { useJourney } from '../state/JourneyContext'
import { useResource } from '../state/useResource'
import { formatCompact, formatCurrency } from '../utils/format'

const clusterIcons = [Layers3, Coins, DatabaseZap] as const

export function DiscoverPage() {
  const { client } = useAuth()
  const clusters = useResource(async () => {
    if (!client) throw new Error('API client is unavailable')
    return (await client.listClusters()).data
  }, [client], { enabled: Boolean(client), empty: (value) => value.length === 0 })

  if (clusters.status === 'loading' || clusters.status === 'idle') return <LoadingState label="Discovering verified workloads…" />
  if (clusters.status === 'error' || !clusters.data) return <ErrorState message={clusters.error ?? 'Clusters are unavailable'} onRetry={clusters.reload} />
  const monthlySpend = clusters.data.reduce((sum, cluster) => sum + cluster.monthly_cost_usd, 0)

  return (
    <div className="page discover-page">
      <PageHeader eyebrow="Opportunity map" title="Discover which recurring workload is worth optimizing." description="Inspect business volume and cost, approve the governed input, then ask the Forge Agent whether a bounded specialist is defensible. Training and serving live in later stages." />
      <section className="summary-strip reveal reveal-1" aria-label="Portfolio summary">
        <div><Layers3 size={17} /><strong>{clusters.data.length}</strong><span>task clusters</span></div>
        <div><Gauge size={17} /><strong>{formatCurrency(monthlySpend)}</strong><span>monthly model cost</span></div>
        <div><Cpu size={17} /><strong>{clusters.data.filter((cluster) => cluster.status === 'live').length}</strong><span>live workload</span></div>
        <EvidenceBadge>Supabase facts</EvidenceBadge>
      </section>
      <section className="cluster-grid" aria-label="Discovered task clusters">
        {clusters.data.map((cluster, index) => {
          const Icon = clusterIcons[index] ?? DatabaseZap
          const isSql = cluster.cluster_id === 'data-pull-sql'
          return (
            <GlassPanel as="article" key={cluster.cluster_id} className={`cluster-card ${isSql ? 'cluster-live' : ''} reveal reveal-${index + 2}`}>
              <div className="cluster-top"><div className="cluster-icon"><Icon size={22} /></div><StatusPill status={cluster.status} /></div>
              <div className="cluster-copy"><span className="mono">{formatCompact(cluster.monthly_calls)} SQL / MONTH</span><h2>{cluster.name}</h2><p>{clusterDescriptions[cluster.cluster_id] ?? 'A repeated model workload discovered from proxy traffic.'}</p></div>
              <div className="cluster-cost"><span>Monthly model cost</span><strong>{formatCurrency(cluster.monthly_cost_usd)}</strong><small> / month</small></div>
              {isSql ? <a className="card-link" href="#sql-analysis">Analyze this opportunity <ArrowRight size={15} /></a> : <span className="cluster-muted">Available for traffic analysis</span>}
            </GlassPanel>
          )
        })}
      </section>
      <SqlAnalysis cluster={clusters.data.find((cluster) => cluster.cluster_id === 'data-pull-sql') ?? null} onClusterReload={clusters.reload} />
      <p className="evidence-footnote reveal reveal-5"><ShieldCheck size={14} /> Volume and cost come from the API; the agent remains advisory until two explicit human actions authorize execution.</p>
    </div>
  )
}

function SqlAnalysis({ cluster, onClusterReload }: { cluster: Cluster | null; onClusterReload: () => void }) {
  const { client } = useAuth()
  const journey = useJourney()
  const [analysis, setAnalysis] = useState<AgentAnalysisResponse | null>(null)
  const [sourceUri, setSourceUri] = useState<string>(SQL_SAMPLE_SOURCE.uri)
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    if (!client || !cluster || journey.clusterId !== cluster.cluster_id || !journey.decisionId) return
    void client.getDecision(cluster.cluster_id)
      .then((value) => {
        if (value.data.decision_id === journey.decisionId) setAnalysis(value.data)
      })
      .catch((error) => {
        if (!(error instanceof ApiError && error.status === 404)) setMessage(error.message)
      })
  }, [client, cluster, journey.clusterId, journey.decisionId])

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label); setMessage(null)
    try { await action() } catch (error) { setMessage(error instanceof Error ? error.message : `${label} failed`) } finally { setBusy(null) }
  }

  if (!cluster) return null
  const decision = analysis?.decision
  return (
    <section className="analysis-workflow reveal reveal-4" id="sql-analysis">
      <div className="workflow-heading"><div><span className="eyebrow">Data Pull SQL · discovery workflow</span><h2>Inspect the input, then ask whether this workload is optimizable.</h2><p>95k monthly calls and {formatCurrency(cluster.monthly_cost_usd)} in monthly model cost make the opportunity visible before any recommendation appears.</p></div><Link2 size={24} /></div>
      <div className="source-row">
        <label htmlFor="sample-source"><span>Input</span><small>The governed training and evaluation sample set.</small></label>
        <div><input id="sample-source" value={sourceUri} onChange={(event) => setSourceUri(event.target.value)} /><button className="secondary-button" type="button" disabled={busy !== null} onClick={() => void run('input', async () => { if (!client) return; await client.putSampleSource(cluster.cluster_id, { uri: sourceUri, approved_by: 'judge', expected_sha256: SQL_SAMPLE_SOURCE.sha256, expected_row_count: SQL_SAMPLE_SOURCE.rowCount }); onClusterReload(); setMessage('Input source approved and identity-checked.') })}>Input</button></div>
        <code>{cluster.approved_sample_source ? `${cluster.approved_sample_source.row_count} rows · ${cluster.approved_sample_source.sha256.slice(0, 12)}…` : 'Not yet approved'}</code>
      </div>
      <div className="analysis-actions">
        <button className="primary-button" type="button" disabled={busy !== null} onClick={() => void run('analyze', async () => { if (!client) return; const result = await client.analyze(cluster.cluster_id); setAnalysis(result.data); journey.recordAnalysis(result.data); setMessage(result.data.decision.decision === 'forge' ? 'Opportunity confirmed. Forge is now unlocked.' : 'This workload does not pass the Forge decision gate yet.') })}><Sparkles size={16} />{busy === 'analyze' ? 'Analyzing…' : 'Analyze'}</button>
        <span>Analysis reads server-side traffic evidence, remains advisory, and cannot provision a GPU.</span>
      </div>
      {decision && <AgentDecisionCard analysis={analysis} />}
      {decision?.decision === 'forge' && decision.config && <div className="discovery-handoff"><div><strong>Discovery complete</strong><span>Review and authorize this exact proposal in Forge.</span></div><Link className="primary-button" to="/forge/new">Continue to Forge <ArrowRight size={16} /></Link></div>}
      {message && <div className="inline-notice" role="status">{message}</div>}
    </section>
  )
}
