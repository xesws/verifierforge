import { ArrowRight, CheckCircle2, Cpu, KeyRound, ListChecks, PlayCircle, ShieldCheck } from 'lucide-react'
import { type FormEvent, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import type { ApprovalRecord, ForgeExecutionStatus } from '../api/contracts'
import { AgentDecisionCard } from '../components/AgentDecisionCard'
import { ErrorState, LoadingState } from '../components/ResourceState'
import { GlassPanel } from '../components/GlassPanel'
import { PageHeader } from '../components/PageHeader'
import { StatusPill } from '../components/StatusPill'
import { DEFAULT_BASE_MODEL, FLAGSHIP_JOB_ID } from '../data/presentation'
import { useAuth } from '../state/AuthContext'
import { useJourney } from '../state/JourneyContext'
import { useResource } from '../state/useResource'

export function ForgePage() {
  const { client } = useAuth()
  const journey = useJourney()
  const navigate = useNavigate()
  const [approval, setApproval] = useState<ApprovalRecord | null>(null)
  const [execution, setExecution] = useState<ForgeExecutionStatus | null>(null)
  const [confirmStart, setConfirmStart] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [template, setTemplate] = useState('nl2sql')
  const [model, setModel] = useState(DEFAULT_BASE_MODEL)
  const [apiKey, setApiKey] = useState('')
  const [credentialMessage, setCredentialMessage] = useState<string | null>(null)

  const proposal = useResource(async () => {
    if (!client || !journey.clusterId || !journey.decisionId) throw new Error('Discover decision is unavailable')
    const [analysis, cluster] = await Promise.all([
      client.getDecision(journey.clusterId),
      client.getCluster(journey.clusterId),
    ])
    if (analysis.data.decision_id !== journey.decisionId) throw new Error('The Discover decision changed; analyze again')
    return { analysis: analysis.data, cluster: cluster.data }
  }, [client, journey.clusterId, journey.decisionId], { enabled: Boolean(client && journey.decisionId) })
  const jobs = useResource(async () => { if (!client) throw new Error('API client is unavailable'); return (await client.listJobs()).data }, [client], { enabled: Boolean(client) })
  const credential = useResource(async () => { if (!client) throw new Error('API client is unavailable'); return (await client.getProviderCredential('runpod', 'judge')).data }, [client], { enabled: Boolean(client) })

  const executionState = execution?.state
  useEffect(() => {
    if (!client || !journey.decisionId || !journey.approvalId) return
    void client.getApproval(journey.decisionId)
      .then((value) => { if (value.data.approval_id === journey.approvalId) setApproval(value.data) })
      .catch((error) => { if (!(error instanceof ApiError && error.status === 404)) setMessage(error.message) })
  }, [client, journey.approvalId, journey.decisionId])

  useEffect(() => {
    if (!client || !approval) return
    let cancelled = false
    const load = () => client.getForgeExecution(approval.approval_id)
      .then((value) => { if (!cancelled) setExecution(value.data) })
      .catch((error) => { if (!cancelled && !(error instanceof ApiError && error.status === 404)) setMessage(error.message) })
    void load()
    const timer = window.setInterval(() => {
      if (!executionState || ['approved', 'provisioning', 'running', 'collecting'].includes(executionState)) void load()
    }, 5_000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [approval, client, executionState])

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label); setMessage(null)
    try { await action() } catch (error) { setMessage(error instanceof Error ? error.message : `${label} failed`) } finally { setBusy(null) }
  }

  async function approve() {
    if (!client || !proposal.data) return
    const result = await client.approve(proposal.data.analysis.decision_id, 'judge')
    setApproval(result.data)
    journey.recordApproval(result.data)
    setMessage('Approval recorded. No GPU has been allocated.')
  }

  async function startForge() {
    if (!client || !approval) return
    const result = await client.startForge(approval.approval_id, { requested_by: 'judge', confirm_provider_spend: true })
    setExecution(result.data)
    journey.selectJob(result.data.job_id)
    navigate(`/jobs/${result.data.job_id}`)
  }

  async function useReferenceRun() {
    if (!client || !proposal.data) throw new Error('No completed reference Run is registered for this cluster')
    const referenceJobId = proposal.data.cluster.job_id ?? FLAGSHIP_JOB_ID
    const result = await client.getJob(referenceJobId)
    if (result.data.status !== 'done' || !result.data.report) throw new Error('The reference Run has no complete held-out report')
    journey.selectJob(result.data.job_id)
    navigate(`/jobs/${result.data.job_id}`)
  }

  async function queueMetadata(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    await run('queue', async () => {
      if (!client) return
      const created = await client.createJob({ template, model })
      jobs.reload()
      setMessage(`Metadata job ${created.data.job_id} queued. It does not unlock Runs or start training.`)
    })
  }

  async function saveCredential(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!client || !apiKey.trim()) return
    setCredentialMessage(null)
    try { const result = await client.putProviderCredential('runpod', 'judge', apiKey); setApiKey(''); setCredentialMessage(result.data.configured ? 'Encrypted credential saved. The value is never returned.' : 'Credential was not saved.'); credential.reload() } catch (error) { setCredentialMessage(error instanceof Error ? error.message : 'Credential save failed') }
  }

  if (proposal.status === 'loading' || proposal.status === 'idle') return <LoadingState label="Loading the approved Forge proposal…" />
  if (proposal.status === 'error' || !proposal.data) return <ErrorState message={proposal.error ?? 'Forge proposal unavailable'} onRetry={proposal.reload} />

  return <div className="page forge-page">
    <PageHeader eyebrow="Forge authorization" title="Review the plan before any training can begin." description="This stage owns the proposed config, the no-spend approval, and the separate provider-spend boundary. Discover and serving controls do not appear here." action={<StatusPill status={execution?.state ?? (approval ? 'approved' : 'queued')} />} />
    <div className="forge-layout">
      <div className="forge-primary">
        <AgentDecisionCard analysis={proposal.data.analysis} />
        <GlassPanel className="approval-panel reveal reveal-2">
          <div><span className="eyebrow">Human control boundary</span><h3>Approve first. Start separately.</h3><p><strong>Approve & Forge</strong> stores acceptance of this exact config and never allocates a GPU. <strong>Start Forge</strong> is a second action that may begin budget-capped provider spend when enabled.</p></div>
          <div className="approval-actions">
            {!approval ? <button className="secondary-button" type="button" disabled={busy !== null} onClick={() => void run('approve', approve)}>Approve & Forge</button> : <StatusPill status={execution?.state ?? 'approved'} />}
            {approval && !journey.selectedJobId && <label className="confirmation-row"><input type="checkbox" checked={confirmStart} onChange={(event) => setConfirmStart(event.target.checked)} /><span>I understand Start Forge may allocate a GPU. Public reviewer mode normally keeps this disabled.</span></label>}
            {approval && !journey.selectedJobId && <button className="primary-button" type="button" disabled={!confirmStart || busy !== null} onClick={() => void run('start', startForge)}><PlayCircle size={16} />{busy === 'start' ? 'Starting…' : 'Start Forge'}</button>}
          </div>
        </GlassPanel>
        {approval && <GlassPanel className="reference-run-panel reveal reveal-3"><div><span className="eyebrow"><ShieldCheck size={13} /> Zero-spend reviewer handoff</span><h3>Continue with the completed flagship Run.</h3><p>This does not pretend a new training job ran. It opens the frozen Run produced for this exact Data Pull SQL workflow so you can inspect process and held-out proof.</p></div><button className="primary-button" type="button" disabled={busy !== null} onClick={() => void run('reference', useReferenceRun)}>{busy === 'reference' ? 'Validating Run…' : 'Review completed Run'} <ArrowRight size={16} /></button></GlassPanel>}
        {message && <div className="inline-notice" role="status">{message}</div>}
      </div>
      <aside className="forge-aside reveal reveal-2"><span className="eyebrow">Job ledger</span>{jobs.status === 'loading' ? <LoadingState label="Loading jobs…" /> : jobs.status === 'error' ? <ErrorState message={jobs.error ?? 'Jobs unavailable'} onRetry={jobs.reload} /> : <ol className="job-list">{jobs.data?.slice(-6).reverse().map((job) => <li key={job.job_id}><StatusPill status={job.status} /><code>{job.job_id}</code></li>)}</ol>}</aside>
    </div>
    <details className="operator-tools glass-panel">
      <summary>Operator tools · metadata and provider settings</summary>
      <p>These controls do not unlock the Reviewer Journey and never substitute for Start Forge.</p>
      <form className="metadata-form" onSubmit={(event) => void queueMetadata(event)}><div className="form-section"><label htmlFor="template"><span><ListChecks size={15} />Task template</span></label><input id="template" value={template} onChange={(event) => setTemplate(event.target.value)} required /></div><div className="form-section"><label htmlFor="model"><span><Cpu size={15} />Base model</span></label><input id="model" value={model} onChange={(event) => setModel(event.target.value)} required /></div><button className="secondary-button" type="submit" disabled={busy !== null}>Queue metadata</button></form>
      <div className="credential-panel"><div><KeyRound size={21} /><span><strong>Bring your own RunPod key</strong><small>{credential.data?.configured ? `Configured via ${credential.data.source}` : 'Optional; system demo credential may be used.'}</small></span></div><form onSubmit={(event) => void saveCredential(event)}><input aria-label="RunPod API key" type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="API key (encrypted at rest)" /><button className="secondary-button" disabled={!apiKey.trim()} type="submit">Save securely</button></form>{credentialMessage && <p><CheckCircle2 size={14} />{credentialMessage}</p>}</div>
    </details>
  </div>
}
