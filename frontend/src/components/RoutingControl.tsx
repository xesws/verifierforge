import { Route, SlidersHorizontal } from 'lucide-react'
import type { RoutingState } from '../api/contracts'
import { GlassPanel } from './GlassPanel'

export function RoutingControl({ value, onChange, onSave, saving = false }: { value: RoutingState; onChange: (next: RoutingState) => void; onSave: () => void; saving?: boolean }) {
  return (
    <GlassPanel className="routing-control reveal reveal-2">
      <div className="panel-heading compact"><div><span className="eyebrow"><Route size={13} /> Production routing policy</span><h2>Ship ordinary traffic with a narrow canary</h2><p>This policy does not affect the tuned-only Reviewer probe below.</p></div><span className="local-chip">Supabase</span></div>
      <div className="control-row"><div><strong>Route live traffic</strong><small>Simulate enabling the tuned route on this device.</small></div><button type="button" role="switch" aria-checked={value.enabled} className={`toggle ${value.enabled ? 'on' : ''}`} onClick={() => onChange({ ...value, enabled: !value.enabled })}><span /></button></div>
      <label className="slider-field"><span><strong>Canary allocation</strong><output>{value.canary_percent}%</output></span><input type="range" min="0" max="100" step="5" value={value.canary_percent} onChange={(event) => onChange({ ...value, canary_percent: Number(event.target.value) })} /></label>
      <label className="select-field"><span><SlidersHorizontal size={15} />Target model</span><select value={value.target_model} onChange={(event) => onChange({ ...value, target_model: event.target.value })}><option value="tuned">Tuned specialist</option></select></label>
      <div className="routing-summary mono"><span>POLICY</span><strong>{value.enabled ? `${value.canary_percent}% canary → tuned` : 'routing disabled'}</strong><small>Writes are explicit; changing controls does not save until confirmed.</small></div>
      <button className="primary-button routing-save" type="button" onClick={onSave} disabled={saving}>{saving ? 'Saving…' : 'Save routing policy'}</button>
    </GlassPanel>
  )
}
