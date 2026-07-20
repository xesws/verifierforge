import { ArrowRight, CheckCircle2, Database, FileJson2, FlaskConical, Network } from 'lucide-react'
import { type FormEvent, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { GlassPanel } from '../components/GlassPanel'
import { PageHeader } from '../components/PageHeader'
import { StatusPill } from '../components/StatusPill'
import { clusters, forgeDefaults } from '../data/productScenario'
import { saveLocalJob } from '../data/localJobs'
import type { LocalJob } from '../types'

export function ForgePage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const initialCluster = useMemo(() => clusters.find((cluster) => cluster.id === searchParams.get('cluster')) ?? clusters[1], [searchParams])
  const [clusterId, setClusterId] = useState(initialCluster.id)
  const [description, setDescription] = useState<string>(forgeDefaults.taskDescription)
  const [schemaContext, setSchemaContext] = useState<string>(forgeDefaults.schemaContext)
  const [examples, setExamples] = useState<string>(forgeDefaults.examplePairs)
  const [createdJob, setCreatedJob] = useState<LocalJob | null>(null)

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const cluster = clusters.find((item) => item.id === clusterId) ?? clusters[0]
    const job: LocalJob = {
      id: `local-${Date.now().toString(36)}`,
      clusterId: cluster.id,
      clusterName: cluster.name,
      description,
      schemaContext,
      examples,
      createdAt: new Date().toISOString(),
      status: 'queued',
    }
    saveLocalJob(job)
    setCreatedJob(job)
    window.setTimeout(() => navigate(`/jobs/${job.id}`), 900)
  }

  return (
    <div className="page forge-page">
      <PageHeader eyebrow="Forge specialist" title="Define the behavior worth owning." description="Package one repeated task into a local specialist job. This prototype saves your draft on-device only." action={<StatusPill status="local" />} />
      <div className="forge-layout">
        <GlassPanel className="forge-form-panel reveal reveal-1">
          {createdJob ? (
            <div className="success-state"><div><CheckCircle2 size={28} /></div><span className="eyebrow">Job queued locally</span><h2>{createdJob.clusterName} is ready for review.</h2><p>No network call was made. Opening the local run detail…</p><Link to={`/jobs/${createdJob.id}`} className="primary-button">Open job <ArrowRight size={16} /></Link></div>
          ) : (
            <form onSubmit={handleSubmit}>
              <div className="form-section"><label htmlFor="cluster"><span><Network size={15} />Cluster</span></label><select id="cluster" value={clusterId} onChange={(event) => setClusterId(event.target.value)}>{clusters.map((cluster) => <option key={cluster.id} value={cluster.id}>{cluster.name} · {cluster.status}</option>)}</select></div>
              <div className="form-section"><label htmlFor="description"><span><FlaskConical size={15} />Task description</span><small>What should the specialist do?</small></label><textarea id="description" rows={3} value={description} onChange={(event) => setDescription(event.target.value)} required /></div>
              <div className="form-section"><label htmlFor="schema"><span><Database size={15} />Schema / context</span><small>Give the verifier the world it needs.</small></label><textarea className="mono-input" id="schema" rows={5} value={schemaContext} onChange={(event) => setSchemaContext(event.target.value)} required /></div>
              <div className="form-section"><label htmlFor="examples"><span><FileJson2 size={15} />Example pairs</span><small>One representative input and target output.</small></label><textarea className="mono-input" id="examples" rows={5} value={examples} onChange={(event) => setExamples(event.target.value)} required /></div>
              <div className="form-footer"><div><strong>Static demo — no backend request</strong><small>Submitting stores a queued job in localStorage.</small></div><button className="primary-button" type="submit">Queue forge job <ArrowRight size={16} /></button></div>
            </form>
          )}
        </GlassPanel>
        <aside className="forge-aside reveal reveal-2"><span className="eyebrow">Local workflow</span><ol><li className="active"><span>01</span><div><strong>Describe</strong><small>Frame the task contract</small></div></li><li><span>02</span><div><strong>Train</strong><small>Static queue simulation</small></div></li><li><span>03</span><div><strong>Prove</strong><small>Require held-out lift</small></div></li></ol><div className="aside-note"><CheckCircle2 size={17} /><p>Your entered text stays in this browser and survives refresh.</p></div></aside>
      </div>
    </div>
  )
}
