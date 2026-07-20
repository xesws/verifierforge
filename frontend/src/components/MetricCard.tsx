import type { ReactNode } from 'react'
import { GlassPanel } from './GlassPanel'

interface MetricCardProps {
  label: string
  value: string
  note?: string
  icon?: ReactNode
  tone?: 'default' | 'green' | 'blue'
}

export function MetricCard({ label, value, note, icon, tone = 'default' }: MetricCardProps) {
  return (
    <GlassPanel className={`metric-card metric-${tone}`}>
      <div className="metric-card-top"><span>{label}</span>{icon}</div>
      <strong>{value}</strong>
      {note && <small>{note}</small>}
    </GlassPanel>
  )
}
