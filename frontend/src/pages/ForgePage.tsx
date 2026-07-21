import { ArrowRight, CheckCircle2, Cpu, KeyRound, ListChecks } from 'lucide-react'
import { type FormEvent, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { DEFAULT_BASE_MODEL } from '../data/presentation'
import { ErrorState, LoadingState } from '../components/ResourceState'
import { GlassPanel } from '../components/GlassPanel'
import { PageHeader } from '../components/PageHeader'
import { StatusPill } from '../components/StatusPill'
import { useAuth } from '../state/AuthContext'
import { useResource } from '../state/useResource'

export function ForgePage() {
  const { client } = useAuth()
  const navigate = useNavigate()
  const [template, setTemplate] = useState('nl2sql')
  const [model, setModel] = useState(DEFAULT_BASE_MODEL)
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [credentialMessage, setCredentialMessage] = useState<string | null>(null)
  const jobs = useResource(async () => { if (!client) throw new Error('API client is unavailable'); return (await client.listJobs()).data }, [client], { enabled: Boolean(client) })
  const credential = useResource(async () => { if (!client) throw new Error('API client is unavailable'); return (await client.getProviderCredential('runpod', 'judge')).data }, [client], { enabled: Boolean(client) })

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!client) return
    setSubmitting(true); setFormError(null)
    try { const created = await client.createJob({ template, model }); jobs.reload(); navigate(`/jobs/${created.data.job_id}`) } catch (error) { setFormError(error instanceof Error ? error.message : 'Job creation failed') } finally { setSubmitting(false) }
  }

  async function saveCredential(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!client || !apiKey.trim()) return
    setCredentialMessage(null)
    try { const result = await client.putProviderCredential('runpod', 'judge', apiKey); setApiKey(''); setCredentialMessage(result.data.configured ? 'Encrypted credential saved. The value is never returned.' : 'Credential was not saved.'); credential.reload() } catch (error) { setCredentialMessage(error instanceof Error ? error.message : 'Credential save failed') }
  }

  return <div className="page forge-page">
    <PageHeader eyebrow="Forge specialist" title="Queue metadata now. Provision only after approval." description="A new job records the requested task and model. GPU execution remains behind the Discover page's explicit Approve and Start Forge boundary." action={<StatusPill status="queued" />} />
    <div className="forge-layout">
      <GlassPanel className="forge-form-panel reveal reveal-1"><form onSubmit={submit}>
        <div className="form-section"><label htmlFor="template"><span><ListChecks size={15} />Task template</span><small>Contract identifier</small></label><input id="template" value={template} onChange={(event) => setTemplate(event.target.value)} required /></div>
        <div className="form-section"><label htmlFor="model"><span><Cpu size={15} />Base model</span><small>Execution still requires approval</small></label><input id="model" value={model} onChange={(event) => setModel(event.target.value)} required /></div>
        {formError && <div className="inline-notice error">{formError}</div>}
        <div className="form-footer"><div><strong>Metadata only</strong><small>POST /jobs cannot provision or train.</small></div><button className="primary-button" type="submit" disabled={submitting}>{submitting ? 'Queuing…' : 'Queue forge job'} <ArrowRight size={16} /></button></div>
      </form></GlassPanel>
      <aside className="forge-aside reveal reveal-2"><span className="eyebrow">Live job ledger</span>{jobs.status === 'loading' ? <LoadingState label="Loading jobs…" /> : jobs.status === 'error' ? <ErrorState message={jobs.error ?? 'Jobs unavailable'} onRetry={jobs.reload} /> : <ol className="job-list">{jobs.data?.slice(-6).reverse().map((job) => <li key={job.job_id}><StatusPill status={job.status} /><Link to={`/jobs/${job.job_id}`}>{job.job_id}</Link></li>)}</ol>}</aside>
    </div>
    <GlassPanel className="credential-panel reveal reveal-3"><div><KeyRound size={21} /><span><strong>Bring your own RunPod key</strong><small>{credential.data?.configured ? `Configured via ${credential.data.source}` : 'Optional; system demo credential may be used.'}</small></span></div><form onSubmit={saveCredential}><input aria-label="RunPod API key" type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="API key (encrypted at rest)" /><button className="secondary-button" disabled={!apiKey.trim()} type="submit">Save securely</button></form>{credentialMessage && <p><CheckCircle2 size={14} />{credentialMessage}</p>}</GlassPanel>
  </div>
}
