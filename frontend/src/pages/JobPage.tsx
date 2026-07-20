import { Activity, ArrowRight, CheckCircle2, Clock3, Cpu, DatabaseZap, Gauge, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { EvidenceBadge } from '../components/EvidenceBadge'
import { GlassPanel } from '../components/GlassPanel'
import { MetricCard } from '../components/MetricCard'
import { PageHeader } from '../components/PageHeader'
import { StatusPill } from '../components/StatusPill'
import { TrainingChart } from '../components/TrainingChart'
import { jobEvidence } from '../data/evidence'
import { findLocalJob } from '../data/localJobs'
import type { EvidenceMetric } from '../types'

const tabs: readonly { id: EvidenceMetric; label: string }[] = [{ id: 'quality', label: 'Quality' }, { id: 'reward', label: 'Reward' }, { id: 'entropy', label: 'Entropy' }]

export function JobPage() {
  const { jobId = '' } = useParams()
  const [metric, setMetric] = useState<EvidenceMetric>('quality')
  const isVerifiedJob = jobId === jobEvidence.mainJobId
  const localJob = isVerifiedJob ? undefined : findLocalJob(jobId)

  if (!isVerifiedJob) {
    return (
      <div className="page"><PageHeader eyebrow="Local run" title={localJob?.clusterName ?? 'Local forge job'} description="This queued state exists only in your browser. No trainer or backend was contacted." action={<StatusPill status="queued" />} /><GlassPanel className="queued-panel reveal reveal-1"><div className="queue-orbit"><Clock3 size={28} /></div><span className="eyebrow">Queued locally</span><h2>Static job created successfully</h2><p>{localJob?.description ?? 'The local job record is unavailable in this browser session.'}</p><div className="queue-facts"><span><DatabaseZap size={16} />{localJob?.clusterName ?? 'Unknown cluster'}</span><span><Clock3 size={16} />{localJob ? new Date(localJob.createdAt).toLocaleString() : 'Not persisted'}</span></div><Link className="secondary-button" to="/jobs/d4-m3-1p5b-r1-v0125">View completed evidence run <ArrowRight size={16} /></Link></GlassPanel></div>
    )
  }

  return (
    <div className="page job-page">
      <PageHeader eyebrow={`Run / ${jobEvidence.mainJobId}`} title="Training converged. Now inspect the evidence." description="Raw training monitor points from the committed artifact, compared with the random-reward control on the same axes." action={<StatusPill status="done" />} />
      <section className="job-metrics reveal reveal-1">
        <MetricCard label="MODEL" value="Qwen 2.5 · 1.5B" note={jobEvidence.mainModel} icon={<Cpu size={17} />} />
        <MetricCard label="TRAINING" value={`${jobEvidence.mainSteps} steps`} note={`${jobEvidence.trainingRows} training rows`} icon={<Activity size={17} />} />
        <MetricCard label="SELECTED" value={`Step ${jobEvidence.selectedCheckpoint}`} note="maximum held-out pass@1" icon={<ShieldCheck size={17} />} tone="green" />
      </section>
      <GlassPanel className="training-panel reveal reveal-2">
        <div className="panel-heading chart-heading"><div><EvidenceBadge>Artifact-derived</EvidenceBadge><h2>Training monitor</h2><p>Quality uses the 10-row monitoring split. It is intentionally separate from held-out proof.</p></div><div className="chart-tabs" role="tablist" aria-label="Training chart metric">{tabs.map((tab) => <button key={tab.id} role="tab" aria-selected={metric === tab.id} className={metric === tab.id ? 'active' : ''} onClick={() => setMetric(tab.id)}>{tab.label}</button>)}</div></div>
        <TrainingChart metric={metric} />
        <div className="chart-notes"><span><i className="main-line" />Main · {jobEvidence.mainModel}</span><span><i className="control-line" />Control ends at step {jobEvidence.controlSteps}</span><span><Gauge size={14} />No smoothing or extrapolation</span></div>
      </GlassPanel>
      <div className="job-footer reveal reveal-3"><div><CheckCircle2 size={20} /><p><strong>Checkpoint selection is held-out.</strong> The monitor is diagnostic; the independent report makes the shipping decision.</p></div><Link className="primary-button" to={`/reports/${jobEvidence.mainJobId}`}>Open proof report <ArrowRight size={16} /></Link></div>
    </div>
  )
}
