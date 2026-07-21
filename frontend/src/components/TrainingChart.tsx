import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { EvidenceMetric, MetricPoint } from '../types'

interface ChartRow { step: number; main?: number; control?: number }

const metricConfig: Record<EvidenceMetric, { key: keyof Pick<MetricPoint, 'pass_at_1' | 'reward_mean' | 'entropy'>; label: string; percent: boolean; domain: [number, number] }> = {
  quality: { key: 'pass_at_1', label: 'Training monitor pass@1', percent: true, domain: [0, 1] },
  reward: { key: 'reward_mean', label: 'Mean verifier reward', percent: false, domain: [0, 1] },
  entropy: { key: 'entropy', label: 'Token entropy', percent: false, domain: [0, 0.8] },
}

function chartRows(metric: EvidenceMetric, main: MetricPoint[], control: MetricPoint[]): ChartRow[] {
  const key = metricConfig[metric].key
  const rows = new Map<number, ChartRow>()
  main.forEach((point) => rows.set(point.step, { step: point.step, main: point[key] }))
  control.forEach((point) => rows.set(point.step, { ...rows.get(point.step), step: point.step, control: point[key] }))
  return [...rows.values()]
}

interface TrainingTooltipProps {
  active?: boolean
  label?: number
  payload?: readonly { name?: string; value?: number }[]
  metric: EvidenceMetric
}

function TrainingTooltip({ active, label, payload, metric }: TrainingTooltipProps) {
  if (!active || !payload?.length) return null
  const format = (value: number) => metricConfig[metric].percent ? `${(value * 100).toFixed(0)}%` : value.toFixed(3)
  return (
    <div className="chart-tooltip">
      <span>TRAINING MONITOR · STEP {label}</span>
      {payload.map((item) => item.value !== undefined && <strong key={item.name}>{item.name}: {format(item.value)}</strong>)}
      <small>10-row monitoring split · not held-out</small>
    </div>
  )
}

export function TrainingChart({ metric, main, control, selectedStep }: { metric: EvidenceMetric; main: MetricPoint[]; control: MetricPoint[]; selectedStep?: number | null }) {
  const config = metricConfig[metric]
  const rows = chartRows(metric, main, control)
  const finalStep = Math.max(1, ...rows.map((row) => row.step))
  return (
    <div className="chart-wrap" role="img" aria-label={`${config.label} from step 1 through ${finalStep}. Main model is solid blue and green; the random reward control is dashed graphite.`}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 18, right: 20, left: 2, bottom: 8 }} accessibilityLayer>
          <defs>
            <linearGradient id={`main-${metric}`} x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" stopColor="#087cf0" />
              <stop offset="100%" stopColor="#00a67e" />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="rgba(23,33,43,.08)" vertical={false} />
          <XAxis dataKey="step" type="number" domain={[0, finalStep]} tickLine={false} axisLine={false} tick={{ fill: '#63717d', fontSize: 11 }} label={{ value: 'TRAINING STEP', position: 'insideBottomRight', offset: -4, fill: '#7a8792', fontSize: 10 }} />
          <YAxis domain={config.domain} tickFormatter={(value: number) => config.percent ? `${Math.round(value * 100)}%` : value.toFixed(1)} tickLine={false} axisLine={false} width={42} tick={{ fill: '#63717d', fontSize: 11 }} />
          <Tooltip content={<TrainingTooltip metric={metric} />} cursor={{ stroke: 'rgba(8,124,240,.25)' }} />
          <Legend verticalAlign="top" align="right" iconType="plainline" wrapperStyle={{ fontSize: 11, fontFamily: 'IBM Plex Mono', paddingBottom: 12 }} />
          {selectedStep && <ReferenceLine x={selectedStep} stroke="#00a67e" strokeDasharray="3 4" label={{ value: `SELECTED · ${selectedStep}`, fill: '#007e61', position: 'insideTopLeft', fontSize: 10 }} />}
          <Line name="Main · 1.5B" type="linear" dataKey="main" stroke={`url(#main-${metric})`} strokeWidth={3} dot={false} activeDot={{ r: 4 }} connectNulls={false} isAnimationActive animationDuration={900} />
          <Line name="Random reward · 0.5B" type="linear" dataKey="control" stroke="#5c6973" strokeWidth={2} strokeDasharray="7 6" dot={false} activeDot={{ r: 3 }} connectNulls={false} isAnimationActive animationDuration={700} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
