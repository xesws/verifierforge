import { Check, CircleDotDashed, Radio } from 'lucide-react'

type StatusTone = 'live' | 'discovered' | 'done' | 'queued' | 'local'

export function StatusPill({ status }: { status: StatusTone }) {
  const Icon = status === 'live' ? Radio : status === 'done' ? Check : CircleDotDashed
  return (
    <span className={`status-pill ${status}`}>
      <Icon size={12} strokeWidth={2.2} aria-hidden="true" />
      {status}
    </span>
  )
}
