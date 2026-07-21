import type { HTMLAttributes, ReactNode } from 'react'

interface GlassPanelProps extends HTMLAttributes<HTMLElement> {
  children: ReactNode
  as?: 'section' | 'article' | 'div'
}

export function GlassPanel({ children, className = '', as: Tag = 'section', ...props }: GlassPanelProps) {
  return <Tag className={`glass-panel ${className}`.trim()} {...props}>{children}</Tag>
}
