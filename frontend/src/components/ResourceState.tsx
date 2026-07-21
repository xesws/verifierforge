import { AlertTriangle, LoaderCircle, RefreshCw } from 'lucide-react'
import { GlassPanel } from './GlassPanel'

export function LoadingState({ label = 'Loading verified data…' }: { label?: string }) {
  return <GlassPanel className="resource-state"><LoaderCircle className="spin" size={24} /><strong>{label}</strong><span>The page stays usable while this request resolves.</span></GlassPanel>
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return <GlassPanel className="resource-state resource-error"><AlertTriangle size={24} /><strong>That data is temporarily unavailable.</strong><span>{message}</span>{onRetry && <button className="secondary-button" type="button" onClick={onRetry}><RefreshCw size={15} />Retry</button>}</GlassPanel>
}
