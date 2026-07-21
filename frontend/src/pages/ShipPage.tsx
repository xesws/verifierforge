import { Info, PackageCheck, Play, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import type { RoutingState, ServingStatus } from '../api/contracts'
import { EmptyGuardian } from '../components/EmptyGuardian'
import { ErrorState, LoadingState } from '../components/ResourceState'
import { GuardianChart } from '../components/GuardianChart'
import { PageHeader } from '../components/PageHeader'
import { RoutingControl } from '../components/RoutingControl'
import { StatusPill } from '../components/StatusPill'
import { WakeModelControl } from '../components/WakeModelControl'
import { FLAGSHIP_JOB_ID, SQL_SYSTEM_PROMPT } from '../data/presentation'
import { useAuth } from '../state/AuthContext'
import { useResource } from '../state/useResource'

export function ShipPage() {
  const { client } = useAuth()
  const [routingDraft, setRoutingDraft] = useState<RoutingState | null>(null)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [serving, setServing] = useState<ServingStatus | null>(null)
  const [prompt, setPrompt] = useState('List the five customers with the highest total order value.')
  const [completion, setCompletion] = useState<string | null>(null)
  const [completing, setCompleting] = useState(false)
  const route = useResource(async () => { if (!client) throw new Error('API client is unavailable'); return (await client.getRouting('data-pull-sql')).data }, [client], { enabled: Boolean(client) })
  const guardian = useResource(async () => { if (!client) throw new Error('API client is unavailable'); return (await client.getLivePassRate('data-pull-sql')).data }, [client], { enabled: Boolean(client), pollMs: 30_000 })
  const value = routingDraft ?? route.data

  async function save() { if (!client || !value) return; setSaving(true); setMessage(null); try { const result = await client.putRouting('data-pull-sql', value); setRoutingDraft(result.data); route.reload(); setMessage('Routing policy saved to Supabase.') } catch (error) { setMessage(error instanceof Error ? error.message : 'Routing save failed') } finally { setSaving(false) } }
  async function tryCompletion() { if (!client || serving?.state !== 'ready') return; setCompleting(true); setMessage(null); try { const result = await client.chatCompletion({ model: 'vf-demo', messages: [{ role: 'system', content: SQL_SYSTEM_PROMPT }, { role: 'user', content: prompt }], temperature: 0, max_tokens: 180 }); setCompletion(result.data.choices[0]?.message.content ?? 'No completion returned'); setMessage(result.route === 'default-fallback' ? 'The tuned endpoint was unavailable, so the proxy used the safe default route.' : `Completion served through ${result.route ?? 'the registered route'}.`) } catch (error) { setMessage(error instanceof Error ? error.message : 'Completion failed') } finally { setCompleting(false) } }

  return <div className="page ship-page">
    <PageHeader eyebrow="Ship / Data Pull SQL" title="Route carefully. Guard honestly." description="The policy is stored in Supabase, the guardian scores traffic asynchronously, and the tuned model is resolved from the serving registry—never a hard-coded URL." action={<StatusPill status={serving?.state ?? 'cold'} />} />
    <section className="ship-banner reveal reveal-1"><div><PackageCheck size={20} /><span><strong>Selected artifact</strong><small>{FLAGSHIP_JOB_ID} · step 350</small></span></div><div><ShieldCheck size={20} /><span><strong>Held-out proof</strong><small>58.3% → 78.3% pass@1</small></span></div><span className="local-chip">Live API</span></section>
    {route.status === 'loading' || route.status === 'idle' ? <LoadingState label="Loading routing policy…" /> : route.status === 'error' || !value ? <ErrorState message={route.error ?? 'Routing unavailable'} onRetry={route.reload} /> : <div className="ship-grid"><RoutingControl value={value} onChange={setRoutingDraft} onSave={() => void save()} saving={saving} />{guardian.status === 'loading' ? <LoadingState label="Loading guardian points…" /> : guardian.data?.points.length ? <GuardianChart value={guardian.data} /> : <EmptyGuardian />}</div>}
    <WakeModelControl onStatus={setServing} />
    <section className="completion-panel glass-panel reveal reveal-4"><div><span className="eyebrow"><Play size={13} /> Ready-only model probe</span><h2>Try one tuned SQL completion</h2><p>{serving?.state === 'ready' ? 'The registry reports ready. This request may enter the tuned canary.' : 'The endpoint is cold. Reports remain available; wake the model before trying a tuned request.'}</p></div><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} rows={3} /><button className="primary-button" type="button" disabled={serving?.state !== 'ready' || completing} onClick={() => void tryCompletion()}>{completing ? 'Running…' : 'Run tuned completion'}</button>{completion && <pre>{completion}</pre>}</section>
    {message && <div className="inline-notice" role="status">{message}</div>}
    <aside className="metric-boundary reveal reveal-5"><Info size={20} /><div><strong>Guardian score is not offline held-out pass@1.</strong><p>Held-out pass@1 measures the frozen 60-row evaluation. LivePassRate measures sampled production-like responses and never blocks the proxy path.</p></div></aside>
  </div>
}
