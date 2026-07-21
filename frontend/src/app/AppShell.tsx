import { Outlet } from 'react-router-dom'
import { FloatingNav } from '../components/FloatingNav'
import { InviteGate } from '../components/InviteGate'
import { useAuth } from '../state/AuthContext'

export function AppShell() {
  const { invitation, clearInvitation } = useAuth()
  if (!invitation) return <InviteGate />
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <div className="ambient ambient-one" aria-hidden="true" />
      <div className="ambient ambient-two" aria-hidden="true" />
      <FloatingNav />
      <button className="session-button" type="button" onClick={clearInvitation}>Leave session</button>
      <main id="main-content" className="workspace">
        <Outlet />
      </main>
    </div>
  )
}
