import { AlertTriangle, ArrowUp, CheckCircle2, Clock3, Copy, Info, PackageCheck, Play, ShieldCheck, TerminalSquare } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'
import type { ChatCompletion, DemoTrafficStatus, RoutingState, RoutePath, ServingStatus } from '../api/contracts'
import { DemoTrafficControl } from '../components/DemoTrafficControl'
import { EmptyGuardian } from '../components/EmptyGuardian'
import { ErrorState, LoadingState } from '../components/ResourceState'
import { GuardianChart } from '../components/GuardianChart'
import { PageHeader } from '../components/PageHeader'
import { RoutingControl } from '../components/RoutingControl'
import { SqlExecutionPanel } from '../components/SqlExecutionPanel'
import { StatusPill } from '../components/StatusPill'
import { WakeModelControl } from '../components/WakeModelControl'
import { FLAGSHIP_JOB_ID, SQL_PROMPT_EXAMPLES, SQL_SYSTEM_PROMPT } from '../data/presentation'
import { useAuth } from '../state/AuthContext'
import { useResource } from '../state/useResource'

interface ProbeResult {
  completion: ChatCompletion
  content: string
  route: RoutePath
  latencyMs: number
  completedAt: string
}

interface RequestActivity {
  at: string
  state: string
  detail: string
}

function readinessMessage(status: ServingStatus | null) {
  if (!status) return { tone: 'waiting', title: 'Checking serving readiness', detail: 'The Run action will unlock only after the registry has been checked.', button: 'Checking serving status…' }
  if (status.state === 'ready') return { tone: 'ready', title: 'Ready to generate SQL', detail: 'The tuned endpoint passed its identity, vLLM, completion, and public tunnel gates.', button: 'Generate SQL' }
  if (status.state === 'provisioning') return { tone: 'waiting', title: 'Step 1 of 3 · GPU provisioning', detail: 'Wake is in progress. Wait for Loading, then Ready; Run does not call a provisioning endpoint.', button: 'Waiting for GPU…' }
  if (status.state === 'loading') return { tone: 'waiting', title: 'Step 2 of 3 · Model loading', detail: 'The GPU exists, but the tuned endpoint is not ready. Run unlocks automatically only when the registry reports Ready.', button: 'Waiting for Ready…' }
  if (status.state === 'draining') return { tone: 'blocked', title: 'Endpoint is shutting down', detail: 'Wait for Cold, then explicitly Wake a new serving session before running a compilation.', button: 'Endpoint is draining' }
  if (status.error_code) return { tone: 'blocked', title: 'The previous Wake failed', detail: `${status.detail}. Review the failure above, then retry Wake.`, button: 'Wake failed · review above' }
  return { tone: 'blocked', title: 'Model is asleep · Wake required', detail: 'First confirm the budget-capped GPU above, click Wake model, and wait until the serving registry says Ready.', button: 'Wake model above first' }
}

export function ShipPage() {
  const { client } = useAuth()
  const [routingDraft, setRoutingDraft] = useState<RoutingState | null>(null)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [serving, setServing] = useState<ServingStatus | null>(null)
  const [prompt, setPrompt] = useState<string>(SQL_PROMPT_EXAMPLES[0].prompt)
  const [result, setResult] = useState<ProbeResult | null>(null)
  const [requestActivity, setRequestActivity] = useState<RequestActivity[]>([])
  const [completing, setCompleting] = useState(false)
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const [elapsedMs, setElapsedMs] = useState(0)
  const [gateNotice, setGateNotice] = useState<string | null>(null)
  const [sqlExecuting, setSqlExecuting] = useState(false)
  const [trafficStatus, setTrafficStatus] = useState<DemoTrafficStatus | null>(null)
  const [trafficAvailable, setTrafficAvailable] = useState<boolean | null>(null)
  const [trafficStarting, setTrafficStarting] = useState(false)
  const [trafficError, setTrafficError] = useState<string | null>(null)
  const [trafficPollRevision, setTrafficPollRevision] = useState(0)
  const trafficWasRunning = useRef(false)
  const route = useResource(async () => { if (!client) throw new Error('API client is unavailable'); return (await client.getRouting('data-pull-sql')).data }, [client], { enabled: Boolean(client) })
  const guardian = useResource(async () => { if (!client) throw new Error('API client is unavailable'); return (await client.getLivePassRate('data-pull-sql')).data }, [client, Boolean(trafficStatus?.running)], { enabled: Boolean(client), pollMs: trafficStatus?.running ? 5_000 : 30_000 })
  const reloadGuardian = guardian.reload
  const value = routingDraft ?? route.data
  const readiness = readinessMessage(serving)
  const canRun = serving?.state === 'ready'

  useEffect(() => {
    if (!completing || startedAt === null) return
    const timer = window.setInterval(() => setElapsedMs(performance.now() - startedAt), 100)
    return () => window.clearInterval(timer)
  }, [completing, startedAt])

  useEffect(() => {
    if (canRun) setGateNotice(null)
  }, [canRun])

  useEffect(() => {
    if (!client) return
    let cancelled = false
    let timer: number | undefined
    const poll = async () => {
      try {
        const status = (await client.getDemoTrafficStatus()).data
        if (cancelled) return
        setTrafficAvailable(true)
        setTrafficStatus(status)
        setTrafficError(null)
        if (status.running) timer = window.setTimeout(() => void poll(), 1_000)
      } catch (error) {
        if (cancelled) return
        if (error instanceof ApiError && error.status === 404) {
          setTrafficAvailable(false)
          setTrafficError(null)
          return
        }
        setTrafficAvailable(true)
        setTrafficError(error instanceof Error ? error.message : 'Traffic status failed')
      }
    }
    void poll()
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [client, trafficPollRevision])

  useEffect(() => {
    const running = trafficStatus?.running ?? false
    if (trafficWasRunning.current && !running) reloadGuardian()
    trafficWasRunning.current = running
  }, [reloadGuardian, trafficStatus?.running])

  async function save() { if (!client || !value) return; setSaving(true); setMessage(null); try { const saved = await client.putRouting('data-pull-sql', value); setRoutingDraft(saved.data); route.reload(); setMessage('Production canary policy saved to Supabase. The reviewer probe remains tuned-only.') } catch (error) { setMessage(error instanceof Error ? error.message : 'Routing save failed') } finally { setSaving(false) } }

  async function startTraffic() {
    if (!client || trafficStarting || trafficStatus?.running) return
    setTrafficStarting(true)
    setTrafficError(null)
    try {
      const status = (await client.startDemoTraffic({ total: 200, rate: 5 })).data
      setTrafficAvailable(true)
      setTrafficStatus(status)
      setTrafficPollRevision((value) => value + 1)
      if (!status.running) reloadGuardian()
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) setTrafficAvailable(false)
      else setTrafficError(error instanceof Error ? error.message : 'Traffic simulation failed')
    } finally {
      setTrafficStarting(false)
    }
  }

  function log(state: string, detail: string) {
    setRequestActivity((current) => [...current, { at: new Date().toISOString(), state, detail }])
  }

  async function tryCompletion() {
    if (!client || serving?.state !== 'ready' || completing || sqlExecuting) return
    const started = performance.now()
    setStartedAt(started)
    setElapsedMs(0)
    setCompleting(true)
    setResult(null)
    setMessage(null)
    setRequestActivity([{ at: new Date().toISOString(), state: 'validated', detail: 'Serving registry reports the tuned model is ready.' }])
    log('dispatching', 'Sending one invitation-protected tuned-only completion. Canary is not consulted.')
    try {
      const response = await client.tunedCompletion({ model: 'vf-demo', messages: [{ role: 'system', content: SQL_SYSTEM_PROMPT }, { role: 'user', content: prompt }], temperature: 0, max_tokens: 180 })
      log('received', 'The tuned endpoint returned an OpenAI-compatible response.')
      if (response.route !== 'tuned') throw new Error('The response could not be verified as tuned; no result was accepted.')
      const content = response.data.choices[0]?.message.content?.trim()
      if (!content) throw new Error('The tuned endpoint returned no SQL content.')
      const latencyMs = performance.now() - started
      setElapsedMs(latencyMs)
      setResult({ completion: response.data, content, route: response.route, latencyMs, completedAt: new Date().toISOString() })
      log('ready', 'Result is ready and the route was verified as tuned.')
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'Tuned completion failed'
      log('failed', detail)
      setMessage(detail)
    } finally {
      setCompleting(false)
    }
  }

  async function copyResult() {
    if (!result) return
    await navigator.clipboard.writeText(result.content)
    setMessage('SQL copied to the clipboard.')
  }

  function choosePrompt(next: string) {
    setPrompt(next)
    setResult(null)
    setRequestActivity([])
    setMessage(null)
  }

  function runOrExplain() {
    if (!canRun) {
      const current = serving?.state ?? 'unknown'
      setGateNotice(`Run blocked: serving state is ${current}. Complete the Wake flow above and wait for Ready.`)
      document.getElementById('wake-model-control')?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
      return
    }
    void tryCompletion()
  }

  const trafficControl = trafficAvailable ? <DemoTrafficControl status={trafficStatus} cold={serving?.state === 'cold'} starting={trafficStarting} error={trafficError} onStart={() => void startTraffic()} /> : null

  return <div className="page ship-page">
    <PageHeader eyebrow="Ship / Data Pull SQL" title="Serve the selected checkpoint, generate SQL, then run it live." description="Ship owns scale-to-zero serving, production canary policy, Guardian signals, and a tuned-only reviewer playground. Training and proof are already complete." action={<StatusPill status={serving?.state ?? 'cold'} />} />
    <section className="ship-banner reveal reveal-1"><div><PackageCheck size={20} /><span><strong>Selected artifact</strong><small>{FLAGSHIP_JOB_ID} · step 350</small></span></div><div><ShieldCheck size={20} /><span><strong>Held-out proof</strong><small>58.3% → 78.3% pass@1</small></span></div><span className="local-chip">Live API</span></section>
    {route.status === 'loading' || route.status === 'idle' ? <LoadingState label="Loading routing policy…" /> : route.status === 'error' || !value ? <ErrorState message={route.error ?? 'Routing unavailable'} onRetry={route.reload} /> : <div className="ship-grid"><RoutingControl value={value} onChange={setRoutingDraft} onSave={() => void save()} saving={saving} />{guardian.status === 'loading' ? <LoadingState label="Loading guardian points…" /> : guardian.data?.points.length ? <GuardianChart value={guardian.data} action={trafficControl} /> : <EmptyGuardian action={trafficControl} />}</div>}
    <WakeModelControl onStatus={setServing} />
    <section className="completion-panel glass-panel reveal reveal-4">
      <div className="completion-intro"><span className="eyebrow"><Play size={13} /> Tuned-only reviewer probe</span><h2>Generate one tuned SQL query</h2><p>{serving?.state === 'ready' ? 'The registry reports ready. Generate SQL first, then run that exact response in the local frozen-data sandbox below.' : 'The endpoint is cold. Wake it above; reports remain available while you wait.'}</p></div>
      <div className={`completion-gate ${readiness.tone}`} role="status" aria-live="polite"><span>{canRun ? <CheckCircle2 size={17} /> : <AlertTriangle size={17} />}</span><div><strong>{readiness.title}</strong><small>{readiness.detail}</small></div></div>
      <div className="completion-input">
        <label htmlFor="tuned-sql-query">Natural-language query</label>
        <textarea id="tuned-sql-query" value={prompt} onChange={(event) => choosePrompt(event.target.value)} rows={4} disabled={completing || sqlExecuting} />
        <div className="prompt-examples" aria-label="Sample SQL questions">
          <span>Try an example from the frozen schema</span>
          <div>{SQL_PROMPT_EXAMPLES.map((example) => <button key={example.label} type="button" disabled={completing || sqlExecuting} aria-pressed={prompt === example.prompt} title={example.prompt} onClick={() => choosePrompt(example.prompt)}>{example.label}</button>)}</div>
        </div>
      </div>
      <button className={`primary-button ${canRun ? '' : 'gated-button'}`} type="button" disabled={completing || sqlExecuting || !prompt.trim()} aria-disabled={!canRun || completing || sqlExecuting || !prompt.trim()} aria-describedby="completion-readiness-feedback" onClick={runOrExplain}>{completing ? <><Clock3 size={16} />Generating · {(elapsedMs / 1000).toFixed(1)}s</> : canRun ? <>{readiness.button} <Play size={15} /></> : <>{readiness.button} <ArrowUp size={15} /></>}</button>
      <div id="completion-readiness-feedback" className={`completion-feedback ${gateNotice ? 'visible' : ''}`} role={gateNotice ? 'alert' : undefined}>{gateNotice ?? 'Run requires a Ready serving registry state.'}</div>
      <section className="activity-console request-console" aria-label="Tuned completion activity"><header><TerminalSquare size={14} /><strong>Generation activity</strong><span>{completing ? 'live' : result ? 'complete' : 'idle'}</span></header><ol role="log" aria-live="polite">{requestActivity.length ? requestActivity.map((line, index) => <li key={`${line.at}-${index}`}><time>{new Date(line.at).toLocaleTimeString()}</time><b>{line.state}</b><span>{line.detail}</span></li>) : <li><time>—</time><b>waiting</b><span>Wake the model, then generate SQL to see each request phase.</span></li>}</ol></section>
      <section className={`completion-output ${result ? 'has-result' : ''}`} aria-label="Tuned SQL result">
        <header><div><span className="eyebrow"><CheckCircle2 size={13} /> Model output</span><h3>{result ? 'SQL generated' : 'Generated SQL will appear here'}</h3></div>{result && <button className="secondary-button" type="button" onClick={() => void copyResult()}><Copy size={14} />Copy SQL</button>}</header>
        {result ? <><pre tabIndex={0}>{result.content}</pre><dl><div><dt>Route</dt><dd><span className="route-badge tuned">{result.route}</span></dd></div><div><dt>Model</dt><dd>{result.completion.model}</dd></div><div><dt>Request ID</dt><dd>{result.completion.id}</dd></div><div><dt>Finish</dt><dd>{result.completion.choices[0]?.finish_reason ?? 'unknown'}</dd></div><div><dt>Tokens</dt><dd>{result.completion.usage?.total_tokens ?? 'not reported'}</dd></div><div><dt>Latency</dt><dd>{(result.latencyMs / 1000).toFixed(2)}s</dd></div></dl></> : <p>No fake ID is treated as output. A successful run displays the complete tuned model response here.</p>}
      </section>
      {result && <SqlExecutionPanel completionId={result.completion.id} sql={result.content} onBusyChange={setSqlExecuting} />}
    </section>
    {message && <div className={`inline-notice ${result ? '' : 'error'}`} role={result ? 'status' : 'alert'}>{message}</div>}
    <aside className="metric-boundary reveal reveal-5"><Info size={20} /><div><strong>Production canary and Reviewer probe are separate.</strong><p>Canary controls what fraction of ordinary product traffic uses the tuned route. “Generate SQL” bypasses that policy and directly verifies the ready tuned endpoint. Browser execution then runs that exact SQL locally without another model or API call.</p></div></aside>
  </div>
}
