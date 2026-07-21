import { useCallback, useEffect, useMemo, useState } from 'react'
import { CircleDollarSign, CloudCog, LoaderCircle, Moon, Radio, ShieldAlert, TerminalSquare } from 'lucide-react'
import { ApiError } from '../api/client'
import type { ServingStatus } from '../api/contracts'
import { SERVING_MODEL_ID } from '../data/presentation'
import { useAuth } from '../state/AuthContext'
import { SERVING_ACTIVITY_STORAGE_KEY } from '../state/storage'
import { useResource } from '../state/useResource'
import { formatCurrency } from '../utils/format'
import { GlassPanel } from './GlassPanel'
import { StatusPill } from './StatusPill'

interface ActivityLine {
  observedAt: string
  sourceUpdatedAt: string
  state: string
  detail: string
}

const EXPECTED_COLD_START_SECONDS = 274

function storedActivity(): ActivityLine[] {
  try {
    const value = JSON.parse(window.sessionStorage.getItem(SERVING_ACTIVITY_STORAGE_KEY) ?? '[]')
    return Array.isArray(value) ? value.slice(-20) as ActivityLine[] : []
  } catch {
    return []
  }
}

function formatElapsed(seconds: number) {
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`
}

export function WakeModelControl({ onStatus }: { onStatus?: (status: ServingStatus) => void }) {
  const { client } = useAuth()
  const [confirmed, setConfirmed] = useState(false)
  const [waking, setWaking] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [disabled, setDisabled] = useState(false)
  const [activity, setActivity] = useState<ActivityLine[]>(storedActivity)
  const [clock, setClock] = useState(Date.now())

  const append = useCallback((line: ActivityLine, replace = false) => {
    setActivity((current) => {
      const last = current.at(-1)
      if (!replace && last?.state === line.state && last.detail === line.detail && last.sourceUpdatedAt === line.sourceUpdatedAt) return current
      const next = [...(replace ? [] : current), line].slice(-20)
      window.sessionStorage.setItem(SERVING_ACTIVITY_STORAGE_KEY, JSON.stringify(next))
      return next
    })
  }, [])

  const resource = useResource(
    async () => {
      if (!client) throw new Error('API client is unavailable')
      const status = (await client.getServingStatus(SERVING_MODEL_ID)).data
      onStatus?.(status)
      return status
    },
    [client],
    { enabled: Boolean(client), pollMs: (status) => ['provisioning', 'loading', 'draining'].includes(status.state) ? 5_000 : status.state === 'ready' ? 15_000 : null },
  )
  const status = resource.data
  const visualState = status?.state === 'cold' && status.error_code ? 'failed' : status?.state ?? 'cold'

  useEffect(() => {
    if (!status) return
    append({
      observedAt: new Date().toISOString(),
      sourceUpdatedAt: status.updated_at,
      state: visualState,
      detail: status.detail,
    })
  }, [append, status, visualState])

  useEffect(() => {
    if (!['provisioning', 'loading', 'draining'].includes(visualState)) return
    const timer = window.setInterval(() => setClock(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [visualState])

  const startedAt = useMemo(() => activity.find((line) => ['requesting', 'provisioning'].includes(line.state))?.observedAt, [activity])
  const elapsed = startedAt ? Math.max(0, Math.floor((clock - Date.parse(startedAt)) / 1_000)) : 0
  const remaining = Math.max(0, EXPECTED_COLD_START_SECONDS - elapsed)

  async function wake() {
    if (!client || !confirmed) return
    setWaking(true)
    setActionError(null)
    const requestedAt = new Date().toISOString()
    append({ observedAt: requestedAt, sourceUpdatedAt: requestedAt, state: 'requesting', detail: 'Reviewer confirmed one budget-capped serving wake.' }, true)
    try {
      const response = await client.wakeServing({ model_id: SERVING_MODEL_ID, confirm_provider_spend: true })
      onStatus?.(response.data)
      append({ observedAt: new Date().toISOString(), sourceUpdatedAt: response.data.updated_at, state: response.data.state, detail: response.data.detail })
      resource.reload()
    } catch (error) {
      if (error instanceof ApiError && error.status === 404 && error.detail.includes('VF_SERVING_WAKE_ENABLED=false')) setDisabled(true)
      const detail = error instanceof Error ? error.message : 'Wake failed'
      append({ observedAt: new Date().toISOString(), sourceUpdatedAt: new Date().toISOString(), state: 'failed', detail })
      setActionError(detail)
    } finally {
      setWaking(false)
    }
  }

  return (
    <GlassPanel className="wake-panel" id="wake-model-control">
      <div className="wake-heading"><div className="cluster-icon"><CloudCog size={22} /></div><div><span className="eyebrow">Scale-to-zero serving</span><h2>Wake the tuned model</h2></div><StatusPill status={visualState} /></div>
      <div className="wake-progress" aria-label={`Serving state ${visualState}`}>
        {['provisioning', 'loading', 'ready'].map((step) => <span key={step} className={status && ['provisioning', 'loading', 'ready'].indexOf(status.state) >= ['provisioning', 'loading', 'ready'].indexOf(step) ? 'active' : ''}><i />{step}</span>)}
      </div>
      <div className="wake-estimate"><strong>{['provisioning', 'loading'].includes(visualState) ? `${formatElapsed(elapsed)} elapsed` : visualState === 'ready' ? 'Model ready' : 'Expected cold start: about 4.5 minutes'}</strong>{['provisioning', 'loading'].includes(visualState) && <span>Estimated {formatElapsed(remaining)} remaining · historical range 267–282s</span>}<small>Stage estimate only; readiness always comes from the serving registry.</small></div>
      {resource.status === 'loading' && <p><LoaderCircle className="spin" size={15} />Reading serving registry…</p>}
      {status && <div className="wake-detail"><span>{status.state === 'cold' ? <Moon size={16} /> : <Radio size={16} />}{status.detail}</span><small>{status.gpu_model ?? 'GPU allocated only after confirmation'}{status.hourly_price_usd === null ? '' : ` · ${formatCurrency(status.hourly_price_usd)}/hr`}{status.cold_start_seconds === null ? '' : ` · ${Math.round(status.cold_start_seconds)}s cold start`}</small><small>Accrued this session: {formatCurrency(status.cost_accrued_usd)}</small></div>}
      <section className="activity-console" aria-label="Serving registry activity"><header><TerminalSquare size={14} /><strong>Serving registry activity</strong><span>live · 5s poll</span></header><ol role="log" aria-live="polite">{activity.length ? activity.map((line, index) => <li key={`${line.observedAt}-${index}`}><time>{new Date(line.observedAt).toLocaleTimeString()}</time><b>{line.state}</b><span>{line.detail}</span></li>) : <li><time>—</time><b>cold</b><span>No wake requested in this reviewer session.</span></li>}</ol></section>
      {actionError && <div className={`inline-notice ${disabled ? '' : 'error'}`} role={disabled ? 'status' : 'alert'}><ShieldAlert size={15} />{disabled ? 'Reviewer policy: model wake is not enabled. Reports remain available.' : actionError}</div>}
      {status?.state === 'cold' && !disabled && <><label className="confirmation-row"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>I understand this provisions one budget-capped GPU. It does not start training.</span></label><button className="primary-button" type="button" disabled={!confirmed || waking} onClick={() => void wake()}><CircleDollarSign size={16} />{waking ? 'Requesting…' : 'Wake model'}</button></>}
    </GlassPanel>
  )
}
