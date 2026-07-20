import { Info, PackageCheck, ShieldCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { EmptyGuardian } from '../components/EmptyGuardian'
import { PageHeader } from '../components/PageHeader'
import { RoutingControl, type RoutingState } from '../components/RoutingControl'
import { StatusPill } from '../components/StatusPill'
import { routingTargets } from '../data/productScenario'

const ROUTING_KEY = 'verifierforge.demo.routing.v1'
const defaultRouting: RoutingState = { enabled: true, canary: 10, target: routingTargets[0] }

function initialRouting(): RoutingState {
  try {
    const stored = window.localStorage.getItem(ROUTING_KEY)
    return stored ? (JSON.parse(stored) as RoutingState) : defaultRouting
  } catch {
    return defaultRouting
  }
}

export function ShipPage() {
  const [routing, setRouting] = useState<RoutingState>(initialRouting)
  useEffect(() => window.localStorage.setItem(ROUTING_KEY, JSON.stringify(routing)), [routing])
  return (
    <div className="page ship-page">
      <PageHeader eyebrow="Ship / Data Pull SQL" title="Route carefully. Guard honestly." description="Stage a local canary policy beside an intentionally empty live guardian—because offline evidence and live traffic are different signals." action={<StatusPill status="local" />} />
      <section className="ship-banner reveal reveal-1"><div><PackageCheck size={20} /><span><strong>Selected artifact</strong><small>Qwen 2.5 · 1.5B · step 350</small></span></div><div><ShieldCheck size={20} /><span><strong>Proof locked</strong><small>78.3% held-out pass@1</small></span></div><span className="local-chip">Local simulation</span></section>
      <div className="ship-grid"><RoutingControl value={routing} onChange={setRouting} /><EmptyGuardian /></div>
      <aside className="metric-boundary reveal reveal-4"><Info size={20} /><div><strong>Guardian score is not offline held-out pass@1.</strong><p>Held-out pass@1 measures the frozen 60-row evaluation set. A guardian score would measure sampled canary behavior after routing. This static artifact has no live samples, so no line is drawn.</p></div></aside>
    </div>
  )
}
