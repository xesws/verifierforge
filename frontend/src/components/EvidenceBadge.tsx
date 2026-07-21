import { LockKeyhole } from 'lucide-react'

export function EvidenceBadge({ children = 'Verified evidence' }: { children?: string }) {
  return <span className="evidence-badge"><LockKeyhole size={13} aria-hidden="true" />{children}</span>
}
