import { useState } from 'react'
import { ChevronDown, ChevronUp, Sparkles } from 'lucide-react'
import { arenaSamples, productSummary } from '../data/productScenario'
import { GlassPanel } from './GlassPanel'

export function ArenaComparison() {
  const [expanded, setExpanded] = useState(false)
  const visibleSamples = expanded ? arenaSamples : arenaSamples.slice(0, 2)
  return (
    <GlassPanel className="arena-panel reveal reveal-4">
      <div className="panel-heading">
        <div><span className="eyebrow"><Sparkles size={13} aria-hidden="true" /> Demo comparison samples · {Math.round(productSummary.projectedArenaWinRate * 100)}% projected win rate</span><h2>See the behavior change</h2><p>Illustrative SQL pairs for the product demo. These are not committed held-out completions.</p></div>
        <button className="secondary-button" type="button" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>{expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}{expanded ? 'Show less' : 'Expand all 7'}</button>
      </div>
      <div className="arena-list">
        {visibleSamples.map((sample) => <article className="arena-row" key={sample.id}><div className="arena-prompt"><span>0{sample.id}</span><strong>{sample.prompt}</strong><small>{sample.reason}</small></div><div className="code-compare"><div><span>BASELINE</span><code>{sample.baseline}</code></div><div className="tuned-code"><span>TUNED</span><code>{sample.tuned}</code></div></div></article>)}
      </div>
    </GlassPanel>
  )
}
