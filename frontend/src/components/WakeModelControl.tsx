import { useState } from 'react'
import { CircleDollarSign, CloudCog, LoaderCircle, Moon, Radio, ShieldAlert } from 'lucide-react'
import { ApiError } from '../api/client'
import type { ServingStatus } from '../api/contracts'
import { SERVING_MODEL_ID } from '../data/presentation'
import { useAuth } from '../state/AuthContext'
import { useResource } from '../state/useResource'
import { formatCurrency } from '../utils/format'
import { GlassPanel } from './GlassPanel'
import { StatusPill } from './StatusPill'

export function WakeModelControl({ onStatus }: { onStatus?: (status: ServingStatus) => void }) {
  const { client } = useAuth()
  const [confirmed, setConfirmed] = useState(false)
  const [waking, setWaking] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [disabled, setDisabled] = useState(false)
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

  async function wake() {
    if (!client || !confirmed) return
    setWaking(true)
    setActionError(null)
    try {
      const response = await client.wakeServing({ model_id: SERVING_MODEL_ID, confirm_provider_spend: true })
      onStatus?.(response.data)
      resource.reload()
    } catch (error) {
      if (error instanceof ApiError && error.status === 404 && error.detail.includes('VF_SERVING_WAKE_ENABLED=false')) setDisabled(true)
      setActionError(error instanceof Error ? error.message : 'Wake failed')
    } finally {
      setWaking(false)
    }
  }

  return (
    <GlassPanel className="wake-panel">
      <div className="wake-heading"><div className="cluster-icon"><CloudCog size={22} /></div><div><span className="eyebrow">Scale-to-zero serving</span><h2>Wake the tuned model</h2></div><StatusPill status={visualState} /></div>
      <div className="wake-progress" aria-label={`Serving state ${visualState}`}>
        {['provisioning', 'loading', 'ready'].map((step) => <span key={step} className={status && ['provisioning', 'loading', 'ready'].indexOf(status.state) >= ['provisioning', 'loading', 'ready'].indexOf(step) ? 'active' : ''}><i />{step}</span>)}
      </div>
      {resource.status === 'loading' && <p><LoaderCircle className="spin" size={15} />Reading serving registry…</p>}
      {status && <div className="wake-detail"><span>{status.state === 'cold' ? <Moon size={16} /> : <Radio size={16} />}{status.detail}</span><small>{status.gpu_model ?? 'GPU allocated only after confirmation'}{status.hourly_price_usd === null ? '' : ` · ${formatCurrency(status.hourly_price_usd)}/hr`}{status.cold_start_seconds === null ? '' : ` · ${Math.round(status.cold_start_seconds)}s cold start`}</small><small>Accrued this session: {formatCurrency(status.cost_accrued_usd)}</small></div>}
      {actionError && <div className={`inline-notice ${disabled ? '' : 'error'}`} role="status"><ShieldAlert size={15} />{disabled ? 'Reviewer policy: model wake is not enabled. Reports remain available.' : actionError}</div>}
      {status?.state === 'cold' && !disabled && <><label className="confirmation-row"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>I understand this provisions one budget-capped GPU. It does not start training.</span></label><button className="primary-button" type="button" disabled={!confirmed || waking} onClick={wake}><CircleDollarSign size={16} />{waking ? 'Requesting…' : 'Wake model'}</button></>}
    </GlassPanel>
  )
}
