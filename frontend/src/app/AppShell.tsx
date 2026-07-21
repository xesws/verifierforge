import { useEffect, useRef } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { FloatingNav } from '../components/FloatingNav'
import { InviteGate } from '../components/InviteGate'
import { useAuth } from '../state/AuthContext'

export function AppShell() {
  const { invitation, clearInvitation } = useAuth()
  const location = useLocation()
  const notice = (location.state as { journeyNotice?: string } | null)?.journeyNotice
  const noticeRef = useRef<HTMLDivElement>(null)
  useEffect(() => { if (notice) noticeRef.current?.focus() }, [notice])
  if (!invitation) return <InviteGate />
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <div className="ambient ambient-one" aria-hidden="true" />
      <div className="ambient ambient-two" aria-hidden="true" />
      <FloatingNav />
      <button className="session-button" type="button" onClick={clearInvitation}>Leave session</button>
      <main id="main-content" className="workspace">
        {notice && <div ref={noticeRef} className="journey-notice" role="status" tabIndex={-1}>{notice}</div>}
        <Outlet />
      </main>
    </div>
  )
}
