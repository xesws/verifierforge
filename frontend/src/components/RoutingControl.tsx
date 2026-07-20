import { Route, SlidersHorizontal } from 'lucide-react'
import { routingTargets } from '../data/productScenario'
import { GlassPanel } from './GlassPanel'

export interface RoutingState { enabled: boolean; canary: number; target: string }

export function RoutingControl({ value, onChange }: { value: RoutingState; onChange: (next: RoutingState) => void }) {
  return (
    <GlassPanel className="routing-control reveal reveal-2">
      <div className="panel-heading compact"><div><span className="eyebrow"><Route size={13} /> Local routing policy</span><h2>Ship with a narrow canary</h2></div><span className="local-chip">Local simulation</span></div>
      <div className="control-row"><div><strong>Route live traffic</strong><small>Simulate enabling the tuned route on this device.</small></div><button type="button" role="switch" aria-checked={value.enabled} className={`toggle ${value.enabled ? 'on' : ''}`} onClick={() => onChange({ ...value, enabled: !value.enabled })}><span /></button></div>
      <label className="slider-field"><span><strong>Canary allocation</strong><output>{value.canary}%</output></span><input type="range" min="0" max="100" step="5" value={value.canary} onChange={(event) => onChange({ ...value, canary: Number(event.target.value) })} /></label>
      <label className="select-field"><span><SlidersHorizontal size={15} />Target model</span><select value={value.target} onChange={(event) => onChange({ ...value, target: event.target.value })}>{routingTargets.map((target) => <option key={target}>{target}</option>)}</select></label>
      <div className="routing-summary mono"><span>POLICY</span><strong>{value.enabled ? `${value.canary}% canary → tuned` : 'routing disabled'}</strong><small>No endpoint is created. State stays in this browser.</small></div>
    </GlassPanel>
  )
}
