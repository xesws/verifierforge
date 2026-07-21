import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

export function HeldoutChart({ baseline, tuned, control }: { baseline: number; tuned: number; control: number }) {
  const data = [
    { stage: 'Before', tunedPath: baseline, controlPath: baseline },
    { stage: 'After', tunedPath: tuned, controlPath: control },
  ]
  return (
    <div className="chart-wrap heldout-chart" role="img" aria-label={`Held-out pass at 1 branches from ${(baseline * 100).toFixed(1)} percent to tuned ${(tuned * 100).toFixed(1)} percent and control ${(control * 100).toFixed(1)} percent.`}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 24, right: 22, left: 4, bottom: 8 }} accessibilityLayer>
          <CartesianGrid stroke="rgba(23,33,43,.08)" vertical={false} />
          <XAxis dataKey="stage" tickLine={false} axisLine={false} tick={{ fill: '#63717d', fontSize: 11 }} />
          <YAxis domain={[0, 1]} tickFormatter={(value: number) => `${Math.round(value * 100)}%`} tickLine={false} axisLine={false} width={42} tick={{ fill: '#63717d', fontSize: 11 }} />
          <Tooltip formatter={(value: number) => `${(value * 100).toFixed(1)}%`} />
          <Legend verticalAlign="top" align="right" />
          <Line type="linear" dataKey="tunedPath" name="Tuned specialist" stroke="#00a67e" strokeWidth={3} dot={{ r: 5 }} />
          <Line type="linear" dataKey="controlPath" name="Random reward control" stroke="#5c6973" strokeDasharray="7 6" strokeWidth={2} dot={{ r: 4 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
