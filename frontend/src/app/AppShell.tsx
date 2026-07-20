import { Outlet } from 'react-router-dom'
import { FloatingNav } from '../components/FloatingNav'

export function AppShell() {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <div className="ambient ambient-one" aria-hidden="true" />
      <div className="ambient ambient-two" aria-hidden="true" />
      <FloatingNav />
      <main id="main-content" className="workspace">
        <Outlet />
      </main>
    </div>
  )
}
