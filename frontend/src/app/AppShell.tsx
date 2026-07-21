import { useEffect, useRef, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { FloatingNav } from '../components/FloatingNav'
import { InviteGate } from '../components/InviteGate'
import { SERVING_MODEL_ID } from '../data/presentation'
import { useAuth } from '../state/AuthContext'

export function AppShell() {
  const { invitation, client, clearInvitation } = useAuth()
  const location = useLocation()
  const notice = (location.state as { journeyNotice?: string } | null)?.journeyNotice
  const noticeRef = useRef<HTMLDivElement>(null)
  const [leaving, setLeaving] = useState(false)
  const [leaveError, setLeaveError] = useState<string | null>(null)
  useEffect(() => { if (notice) noticeRef.current?.focus() }, [notice])

  async function leaveSession() {
    if (!client || leaving) return
    setLeaving(true)
    setLeaveError(null)
    try {
      const result = await client.sleepServing({ model_id: SERVING_MODEL_ID })
      if (result.data.state !== 'cold') throw new Error('Serving shutdown did not reach cold')
      clearInvitation()
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'Serving shutdown failed'
      setLeaveError(`${detail}. Session remains open so shutdown can be retried.`)
    } finally {
      setLeaving(false)
    }
  }

  if (!invitation) return <InviteGate />
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <div className="ambient ambient-one" aria-hidden="true" />
      <div className="ambient ambient-two" aria-hidden="true" />
      <FloatingNav />
      <button className="session-button" type="button" disabled={leaving} aria-busy={leaving} onClick={() => void leaveSession()}>{leaving ? 'Closing demo…' : 'Leave session'}</button>
      {leaveError && <div className="session-error" role="alert">{leaveError}</div>}
      <main id="main-content" className="workspace">
        {notice && <div ref={noticeRef} className="journey-notice" role="status" tabIndex={-1}>{notice}</div>}
        <Outlet />
      </main>
    </div>
  )
}
