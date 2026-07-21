import { Activity } from 'lucide-react'
import type { ReactNode } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { LivePassRate } from '../api/contracts'
import { GlassPanel } from './GlassPanel'
import type { GuardianRunView } from './guardianRun'

const HISTORY_WINDOW = 40

export function GuardianChart({ value, action, run }: { value: LivePassRate; action?: ReactNode; run?: GuardianRunView | null }) {
  const historyOffset = Math.max(0, value.points.length - HISTORY_WINDOW)
  const history = value.points.slice(-HISTORY_WINDOW).map((point, index) => ({ ...point, sample: historyOffset + index + 1 }))
  const points = run ? run.points : history
  const xKey = run ? 'request' : 'sample'
  const latest = run?.latestPassRate ?? history.at(-1)?.pass_rate
  const yDomain = visibleRateDomain(points.map((point) => point.pass_rate))
  const chip = run
    ? `${run.sent}/${run.total} requests${latest === null || latest === undefined ? '' : ` · ${(latest * 100).toFixed(0)}%`}`
    : `${value.points.length} points${latest === undefined ? '' : ` · ${(latest * 100).toFixed(0)}%`}`
  const ariaLabel = run
    ? `Live Guardian run with ${run.sent} of ${run.total} traffic moments and ${run.guardianSamples} new verifier samples`
    : `Live pass rate with ${value.points.length} points`
  return (
    <GlassPanel className="guardian-panel reveal reveal-3">
      <div className="panel-heading compact"><div><span className="eyebrow"><Activity size={13} /> Guard</span><h2>Live guardian</h2></div><span className="local-chip">{chip}</span></div>
      {action}
      {run && <div className={`guardian-run-summary ${run.running ? 'running' : 'complete'}`} role="status" aria-live="polite">
        <div><strong>{run.running ? `Live traffic · ${run.sent}/${run.total} requests` : `Run complete · ${run.success}/${run.total} succeeded`}</strong><span>{run.guardianSamples} new Guardian sample{run.guardianSamples === 1 ? '' : 's'} · {run.failed} failed</span></div>
        <i aria-hidden="true"><b style={{ width: `${Math.min(100, run.total ? run.sent / run.total * 100 : 0)}%` }} /></i>
      </div>}
      <div className="guardian-chart" role="img" aria-label={ariaLabel}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points} margin={{ top: 22, right: 18, left: 0, bottom: 8 }} accessibilityLayer>
            <CartesianGrid stroke="rgba(23,33,43,.08)" vertical={false} />
            <XAxis dataKey={xKey} type="number" domain={run ? [0, run.total] : ['dataMin', 'dataMax']} ticks={run ? trafficTicks(run.total) : undefined} allowDecimals={false} tickLine={false} axisLine={false} tick={{ fill: '#63717d', fontSize: 10 }} />
            <YAxis domain={yDomain} tickFormatter={(number: number) => `${Math.round(number * 100)}%`} tickLine={false} axisLine={false} width={40} tick={{ fill: '#63717d', fontSize: 10 }} />
            <Tooltip formatter={(number: number) => `${(number * 100).toFixed(1)}%`} labelFormatter={(label) => run ? `Traffic request ${label}` : `Guardian sample ${label}`} />
            <Line type="monotone" dataKey="pass_rate" name="Live pass rate" stroke="#00a67e" strokeWidth={3} dot={run ? { r: 1.5, fill: '#00a67e', strokeWidth: 0 } : false} activeDot={{ r: 4 }} connectNulls={false} isAnimationActive={Boolean(run?.running)} animationDuration={300} animationEasing="linear" />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="guardian-note">{run ? 'The line advances once per traffic request. Only sampled tuned SQL changes the rolling pass rate; other requests carry the latest observed value forward.' : `Showing the latest ${history.length} of ${value.points.length} asynchronous verifier points; the y-axis fits the visible range.`}</p>
    </GlassPanel>
  )
}

function visibleRateDomain(values: Array<number | null>): [number, number] {
  const rates = values.filter((value): value is number => typeof value === 'number')
  if (!rates.length) return [0, 1]
  const minimum = Math.min(...rates)
  const maximum = Math.max(...rates)
  const lower = Math.max(0, Math.floor((minimum - 0.05) * 20) / 20)
  const upper = Math.min(1, Math.ceil((maximum + 0.05) * 20) / 20)
  return lower === upper ? [Math.max(0, lower - 0.1), Math.min(1, upper + 0.1)] : [lower, upper]
}

function trafficTicks(total: number): number[] {
  return [0, 0.25, 0.5, 0.75, 1].map((part) => Math.round(total * part))
}
