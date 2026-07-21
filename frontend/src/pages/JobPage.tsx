import { Activity, ArrowRight, Cpu, Gauge, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { controlPoints, metricPoints, selectedCheckpoint } from '../api/mappers'
import { EvidenceBadge } from '../components/EvidenceBadge'
import { GlassPanel } from '../components/GlassPanel'
import { MetricCard } from '../components/MetricCard'
import { PageHeader } from '../components/PageHeader'
import { ErrorState, LoadingState } from '../components/ResourceState'
import { StatusPill } from '../components/StatusPill'
import { TrainingChart } from '../components/TrainingChart'
import { useAuth } from '../state/AuthContext'
import { useResource } from '../state/useResource'
import type { EvidenceMetric } from '../types'

const tabs: readonly { id: EvidenceMetric; label: string }[] = [{ id: 'quality', label: 'Quality' }, { id: 'reward', label: 'Reward' }, { id: 'entropy', label: 'Entropy' }]

export function JobPage() {
  const { jobId = '' } = useParams()
  const { client } = useAuth()
  const [metric, setMetric] = useState<EvidenceMetric>('quality')
  const resource = useResource(async () => {
    if (!client) throw new Error('API client is unavailable')
    const [job, metrics] = await Promise.all([client.getJob(jobId), client.getMetrics(jobId)])
    return { job: job.data, metrics: metrics.data }
  }, [client, jobId], { enabled: Boolean(client && jobId) })

  if (resource.status === 'loading' || resource.status === 'idle') return <LoadingState label="Loading the run ledger…" />
  if (resource.status === 'error' || !resource.data) return <ErrorState message={resource.error ?? 'Job unavailable'} onRetry={resource.reload} />
  const { job, metrics } = resource.data
  const main = metricPoints(metrics)
  const control = controlPoints(metrics, job.control)
  const selected = selectedCheckpoint(job)

  return <div className="page job-page">
    <PageHeader eyebrow={`Run / ${job.job_id}`} title={job.status === 'done' ? 'Training converged. Now inspect the evidence.' : 'This run is recorded in the live job ledger.'} description="The main training curve and random-reward control share the same axes and preserve every API point—no frontend smoothing or extrapolation." action={<StatusPill status={job.status} />} />
    <section className="job-metrics reveal reveal-1">
      <MetricCard label="MODEL" value={job.model.includes('1.5B') ? 'Qwen 2.5 · 1.5B' : job.model} note={job.model} icon={<Cpu size={17} />} />
      <MetricCard label="MAIN CURVE" value={`${metrics.steps.length} points`} note={`last step ${metrics.steps.at(-1) ?? 0}`} icon={<Activity size={17} />} />
      <MetricCard label="SELECTED" value={selected ? `Step ${selected}` : 'Pending'} note={selected ? 'maximum held-out pass@1' : 'not selected yet'} icon={<ShieldCheck size={17} />} tone="green" />
    </section>
    <GlassPanel className="training-panel reveal reveal-2"><div className="panel-heading chart-heading"><div><EvidenceBadge>API artifact</EvidenceBadge><h2>Training vs. spurious control</h2><p>{main.length} main points and {control.length} control points, rendered together.</p></div><div className="chart-tabs" role="tablist" aria-label="Training chart metric">{tabs.map((tab) => <button key={tab.id} role="tab" aria-selected={metric === tab.id} className={metric === tab.id ? 'active' : ''} onClick={() => setMetric(tab.id)}>{tab.label}</button>)}</div></div><TrainingChart metric={metric} main={main} control={control} selectedStep={selected} /><div className="chart-notes"><span><i className="main-line" />Main · {main.length} points</span><span><i className="control-line" />Random reward · {control.length} points</span><span><Gauge size={14} />Exact API values</span></div></GlassPanel>
    <div className="job-footer reveal reveal-3"><p><ShieldCheck size={17} /><span><strong>Training is monitoring, not proof.</strong> The held-out report determines whether this checkpoint ships.</span></p>{job.report && <Link className="primary-button" to={`/reports/${job.job_id}`}>Open proof report <ArrowRight size={16} /></Link>}</div>
  </div>
}
