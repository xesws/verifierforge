import { CartesianGrid, Line, LineChart, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { jobEvidence } from '../data/evidence'

interface HeldoutTooltipProps {
  active?: boolean
  label?: number
  payload?: readonly { value?: number }[]
}

function HeldoutTooltip({ active, label, payload }: HeldoutTooltipProps) {
  if (!active || payload?.[0]?.value === undefined) return null
  return <div className="chart-tooltip"><span>HELD-OUT CHECKPOINT</span><strong>Step {label}: {(payload[0].value * 100).toFixed(2)}%</strong><small>Independent 60-row evaluation</small></div>
}

export function HeldoutChart() {
  const selected = jobEvidence.checkpoints.find((point) => point.step === jobEvidence.selectedCheckpoint)
  return (
    <div className="chart-wrap heldout-chart" role="img" aria-label="Held-out pass at 1 across eight checkpoints. Baseline is 58.33 percent. Step 350 is selected at 78.33 percent. Step 400 declines to 71.67 percent.">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={jobEvidence.checkpoints} margin={{ top: 24, right: 22, left: 4, bottom: 8 }} accessibilityLayer>
          <defs><linearGradient id="heldout-line" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stopColor="#087cf0" /><stop offset="1" stopColor="#00a67e" /></linearGradient></defs>
          <CartesianGrid stroke="rgba(23,33,43,.08)" vertical={false} />
          <XAxis dataKey="step" tickLine={false} axisLine={false} tick={{ fill: '#63717d', fontSize: 11 }} label={{ value: 'CHECKPOINT STEP', position: 'insideBottomRight', offset: -4, fill: '#7a8792', fontSize: 10 }} />
          <YAxis domain={[0.5, 0.85]} ticks={[0.5, 0.6, 0.7, 0.8]} tickFormatter={(value: number) => `${Math.round(value * 100)}%`} tickLine={false} axisLine={false} width={42} tick={{ fill: '#63717d', fontSize: 11 }} />
          <Tooltip content={<HeldoutTooltip />} />
          <ReferenceLine y={jobEvidence.heldout.passAt1Before} stroke="#7a8792" strokeDasharray="5 5" label={{ value: 'BASELINE 58.33%', fill: '#63717d', position: 'insideTopLeft', fontSize: 10 }} />
          {selected && <ReferenceDot x={selected.step} y={selected.pass_at_1} r={13} fill="rgba(0,166,126,.14)" stroke="rgba(0,166,126,.28)" />}
          {selected && <ReferenceDot x={selected.step} y={selected.pass_at_1} r={6} fill="#00a67e" stroke="#ffffff" strokeWidth={2} label={{ value: 'SELECTED', position: 'top', fill: '#007e61', fontSize: 10 }} />}
          <Line type="linear" dataKey="pass_at_1" name="Held-out pass@1" stroke="url(#heldout-line)" strokeWidth={3} dot={{ r: 3, fill: '#fff', stroke: '#087cf0', strokeWidth: 2 }} activeDot={{ r: 5 }} isAnimationActive animationDuration={900} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
