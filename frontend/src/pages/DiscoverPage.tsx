import { ArrowRight, Coins, Cpu, DatabaseZap, Gauge, Layers3, Link2, ShieldCheck, Sparkles } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError } from '../api/client'
import type { AgentAnalysisResponse, ApprovalRecord, Cluster, ForgeExecutionStatus } from '../api/contracts'
import { AgentDecisionCard } from '../components/AgentDecisionCard'
import { EvidenceBadge } from '../components/EvidenceBadge'
import { ErrorState, LoadingState } from '../components/ResourceState'
import { GlassPanel } from '../components/GlassPanel'
import { PageHeader } from '../components/PageHeader'
import { StatusPill } from '../components/StatusPill'
import { WakeModelControl } from '../components/WakeModelControl'
import { clusterDescriptions, FLAGSHIP_JOB_ID, SQL_SAMPLE_SOURCE } from '../data/presentation'
import { useAuth } from '../state/AuthContext'
import { useResource } from '../state/useResource'
import { formatCompact, formatCurrency, formatPercent } from '../utils/format'

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
      <PageHeader eyebrow="Opportunity map" title="Turn recurring model spend into owned performance." description="Inspect real workload volume, let the Forge Agent propose a bounded plan, then approve and start it as two separate decisions." action={<Link className="primary-button" to="/forge/new"><Sparkles size={17} />New job</Link>} />
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
              {isSql ? <a className="card-link" href="#sql-analysis">Review evidence & agent plan <ArrowRight size={15} /></a> : cluster.job_id ? <Link className="card-link" to={`/jobs/${cluster.job_id}`}>View run <ArrowRight size={15} /></Link> : <span className="cluster-muted">Awaiting an approved verifier source</span>}
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
  const [analysis, setAnalysis] = useState<AgentAnalysisResponse | null>(null)
  const [approval, setApproval] = useState<ApprovalRecord | null>(null)
  const [execution, setExecution] = useState<ForgeExecutionStatus | null>(null)
  const [sourceUri, setSourceUri] = useState<string>(SQL_SAMPLE_SOURCE.uri)
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [confirmStart, setConfirmStart] = useState(false)

  const executionState = execution?.state
  useEffect(() => {
    if (!client || !cluster) return
    void client.getDecision(cluster.cluster_id).then((value) => setAnalysis(value.data)).catch((error) => { if (!(error instanceof ApiError && error.status === 404)) setMessage(error.message) })
  }, [client, cluster])

  useEffect(() => {
    if (!client || !analysis) return
    void client.getApproval(analysis.decision_id).then((value) => setApproval(value.data)).catch((error) => { if (!(error instanceof ApiError && error.status === 404)) setMessage(error.message) })
  }, [analysis, client])

  useEffect(() => {
    if (!client || !approval) return
    const load = () => client.getForgeExecution(approval.approval_id).then((value) => setExecution(value.data)).catch((error) => { if (!(error instanceof ApiError && error.status === 404)) setMessage(error.message) })
    void load()
    if (!executionState || ['approved', 'provisioning', 'running', 'collecting'].includes(executionState)) {
      const timer = window.setInterval(() => void load(), 5_000)
      return () => window.clearInterval(timer)
    }
  }, [approval, client, executionState])

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label); setMessage(null)
    try { await action() } catch (error) { setMessage(error instanceof Error ? error.message : `${label} failed`) } finally { setBusy(null) }
  }

  if (!cluster) return null
  const decision = analysis?.decision ?? cluster.analyzer_decision
  return (
    <section className="analysis-workflow reveal reveal-4" id="sql-analysis">
      <div className="workflow-heading"><div><span className="eyebrow">Data Pull SQL · decision workflow</span><h2>Inspect the input before approving the solution.</h2><p>95k monthly calls and {formatCurrency(cluster.monthly_cost_usd)} in monthly model cost make the opportunity visible before any recommendation appears.</p></div><Link2 size={24} /></div>
      <div className="source-row">
        <label htmlFor="sample-source"><span>Input</span><small>The governed repository source the agent may inspect.</small></label>
        <div><input id="sample-source" value={sourceUri} onChange={(event) => setSourceUri(event.target.value)} /><button className="secondary-button" type="button" disabled={busy !== null} onClick={() => void run('input', async () => { if (!client) return; await client.putSampleSource(cluster.cluster_id, { uri: sourceUri, approved_by: 'judge', expected_sha256: SQL_SAMPLE_SOURCE.sha256, expected_row_count: SQL_SAMPLE_SOURCE.rowCount }); onClusterReload(); setMessage('Input source approved and identity-checked.') })}>Input</button></div>
        <code>{cluster.approved_sample_source ? `${cluster.approved_sample_source.row_count} rows · ${cluster.approved_sample_source.sha256.slice(0, 12)}…` : 'Not yet approved'}</code>
      </div>
      <div className="analysis-actions">
        <button className="primary-button" type="button" disabled={busy !== null} onClick={() => void run('analyze', async () => { if (!client) return; const result = await client.analyze(cluster.cluster_id, { data_source: sourceUri }); setAnalysis(result.data) })}><Sparkles size={16} />{busy === 'analyze' ? 'Analyzing…' : 'Analyze'}</button>
        <span>Agent analysis is advisory and cannot provision a GPU.</span>
      </div>
      {decision && <AgentDecisionCard analysis={analysis} fallback={cluster.analyzer_decision} />}
      {decision?.config && <GlassPanel className="approval-panel"><div><span className="eyebrow">Human control boundary</span><h3>Approve the proposed schema, then start separately.</h3><p><strong>Approve & Forge</strong> stores who accepted this exact config. It does not allocate a GPU. <strong>Start Forge</strong> is a second confirmation that may provision a budget-capped GPU and begin the training lifecycle.</p></div><div className="approval-actions">{!approval ? <button className="secondary-button" type="button" disabled={!analysis || busy !== null} onClick={() => void run('approve', async () => { if (!client || !analysis) return; setApproval((await client.approve(analysis.decision_id, 'judge')).data) })}>Approve & Forge</button> : <StatusPill status={execution?.state ?? 'approved'} />}{approval && !execution && <label className="confirmation-row"><input type="checkbox" checked={confirmStart} onChange={(event) => setConfirmStart(event.target.checked)} /><span>I confirm provider spend may begin.</span></label>}{approval && !execution && <button className="primary-button" type="button" disabled={!confirmStart || busy !== null} onClick={() => void run('start', async () => { if (!client) return; setExecution((await client.startForge(approval.approval_id, { requested_by: 'judge', confirm_provider_spend: true })).data) })}>{busy === 'start' ? 'Starting…' : 'Start Forge'}</button>}</div></GlassPanel>}
      {message && <div className="inline-notice" role="status">{message}</div>}
      <WakeModelControl />
      <div className="workflow-proof"><span>Existing proof</span><strong>{formatPercent(.583333)} <ArrowRight size={15} /> {formatPercent(.783333)}</strong><Link to={`/reports/${FLAGSHIP_JOB_ID}`}>Open held-out report</Link></div>
    </section>
  )
}
