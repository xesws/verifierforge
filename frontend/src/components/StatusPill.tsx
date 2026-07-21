import { Check, CircleDotDashed, Radio } from 'lucide-react'

type StatusTone = 'live' | 'discovered' | 'forging' | 'done' | 'queued' | 'running' | 'failed' | 'early_stopped' | 'approved' | 'collecting' | 'cold' | 'provisioning' | 'loading' | 'ready' | 'draining' | 'local'

export function StatusPill({ status }: { status: StatusTone }) {
  const Icon = status === 'live' || status === 'ready' ? Radio : status === 'done' ? Check : CircleDotDashed
  return (
    <span className={`status-pill ${status}`}>
      <Icon size={12} strokeWidth={2.2} aria-hidden="true" />
      {status}
    </span>
  )
}
