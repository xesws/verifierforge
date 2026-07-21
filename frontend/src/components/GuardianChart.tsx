import { Activity } from 'lucide-react'
import type { ReactNode } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { LivePassRate } from '../api/contracts'
import { GlassPanel } from './GlassPanel'

export function GuardianChart({ value, action }: { value: LivePassRate; action?: ReactNode }) {
  const points = value.points.map((point, index) => ({ ...point, sample: index + 1 }))
  const latest = points.at(-1)?.pass_rate
  return (
    <GlassPanel className="guardian-panel reveal reveal-3">
      <div className="panel-heading compact"><div><span className="eyebrow"><Activity size={13} /> Guard</span><h2>Live guardian</h2></div><span className="local-chip">{points.length} points{latest === undefined ? '' : ` · ${(latest * 100).toFixed(0)}%`}</span></div>
      {action}
      <div className="guardian-chart" role="img" aria-label={`Live pass rate with ${points.length} points`}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points} margin={{ top: 22, right: 18, left: 0, bottom: 8 }} accessibilityLayer>
            <CartesianGrid stroke="rgba(23,33,43,.08)" vertical={false} />
            <XAxis dataKey="sample" tickLine={false} axisLine={false} tick={{ fill: '#63717d', fontSize: 10 }} />
            <YAxis domain={[0, 1]} tickFormatter={(number: number) => `${Math.round(number * 100)}%`} tickLine={false} axisLine={false} width={40} tick={{ fill: '#63717d', fontSize: 10 }} />
            <Tooltip formatter={(number: number) => `${(number * 100).toFixed(1)}%`} labelFormatter={(label) => `Guardian sample ${label}`} />
            <Line type="monotone" dataKey="pass_rate" name="Live pass rate" stroke="#00a67e" strokeWidth={3} dot={false} activeDot={{ r: 4 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="guardian-note">Asynchronous verifier scores; proxy requests never wait for this chart.</p>
    </GlassPanel>
  )
}
