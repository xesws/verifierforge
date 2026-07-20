import { Activity, RadioTower } from 'lucide-react'
import { GlassPanel } from './GlassPanel'

export function EmptyGuardian() {
  return (
    <GlassPanel className="guardian-panel reveal reveal-3">
      <div className="panel-heading compact"><div><span className="eyebrow"><Activity size={13} /> Guard</span><h2>Live guardian</h2></div><span className="local-chip muted">No samples</span></div>
      <div className="guardian-empty">
        <div className="guardian-icon"><RadioTower size={28} aria-hidden="true" /></div>
        <h3>Awaiting sampled canary traffic</h3>
        <p>The source artifact contains no LivePassRate values. Guardian telemetry will appear only after sampled traffic exists.</p>
        <div className="chart-skeleton" aria-hidden="true"><i /><i /><i /><i /><i /><span /></div>
      </div>
    </GlassPanel>
  )
}
