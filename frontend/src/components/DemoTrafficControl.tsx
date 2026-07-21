import { LoaderCircle, Play } from 'lucide-react'
import type { DemoTrafficStatus } from '../api/contracts'

const COLD_NOTE = 'endpoint cold — traffic will route to default; wake the model to see tuned scoring'

interface DemoTrafficControlProps {
  status: DemoTrafficStatus | null
  cold: boolean
  starting: boolean
  error: string | null
  onStart: () => void
}

export function DemoTrafficControl({ status, cold, starting, error, onStart }: DemoTrafficControlProps) {
  const running = status?.running ?? false
  const sent = status?.sent ?? 0
  const total = status?.total ?? 200
  const taskError = error ?? status?.error ?? null
  return (
    <div className="demo-traffic-control">
      <button
        className="secondary-button"
        type="button"
        disabled={starting || running}
        aria-busy={starting || running}
        onClick={onStart}
      >
        {starting ? <><LoaderCircle className="spin" size={14} />Starting traffic…</> : running ? <><LoaderCircle className="spin" size={14} />Simulating traffic · {sent}/{total}</> : <><Play size={14} />Simulate traffic (200 requests)</>}
      </button>
      <div aria-live="polite">
        {cold && <small className="demo-traffic-cold">{COLD_NOTE}</small>}
        {taskError && <small className="demo-traffic-error" role="alert">{taskError}</small>}
      </div>
    </div>
  )
}
