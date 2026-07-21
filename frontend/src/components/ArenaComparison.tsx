import { useState } from 'react'
import { ChevronDown, ChevronUp, Sparkles } from 'lucide-react'
import type { Arena } from '../api/contracts'
import { GlassPanel } from './GlassPanel'

export function ArenaComparison({ arena }: { arena: Arena }) {
  const [expanded, setExpanded] = useState(false)
  const visibleSamples = expanded ? arena.samples : arena.samples.slice(0, 2)
  return (
    <GlassPanel className="arena-panel reveal reveal-4">
      <div className="panel-heading">
        <div><span className="eyebrow"><Sparkles size={13} aria-hidden="true" /> Held-out arena · {Math.round(arena.win_rate * 100)}% tuned-only wins</span><h2>See the behavior change</h2><p>Ten deterministic samples from the frozen 60-row held-out evaluation, including wins, shared passes, and shared failures.</p></div>
        <button className="secondary-button" type="button" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>{expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}{expanded ? 'Show less' : `Expand all ${arena.samples.length}`}</button>
      </div>
      <div className="arena-list">
        {visibleSamples.map((sample, index) => <article className="arena-row" key={`${index}-${sample.prompt}`}><div className="arena-prompt"><span>{String(index + 1).padStart(2, '0')}</span><strong>{sample.prompt}</strong><small>Scores {sample.baseline_score.toFixed(1)} → {sample.tuned_score.toFixed(1)}</small></div><div className="code-compare"><div><span>BASELINE · {sample.baseline_score.toFixed(1)}</span><code>{sample.baseline_output}</code></div><div className="tuned-code"><span>TUNED · {sample.tuned_score.toFixed(1)}</span><code>{sample.tuned_output}</code></div></div></article>)}
      </div>
    </GlassPanel>
  )
}
