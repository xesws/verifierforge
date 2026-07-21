import { BarChart3, FlaskConical, PackageCheck, Radar, ShieldCheck } from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { FLAGSHIP_JOB_ID } from '../data/presentation'

const navItems = [
  { label: 'Discover', to: '/discover', icon: Radar },
  { label: 'Forge', to: '/forge/new', icon: FlaskConical },
  { label: 'Runs', to: `/jobs/${FLAGSHIP_JOB_ID}`, icon: BarChart3 },
  { label: 'Prove', to: `/reports/${FLAGSHIP_JOB_ID}`, icon: ShieldCheck },
  { label: 'Ship', to: '/ship/data-pull-sql', icon: PackageCheck },
] as const

export function FloatingNav() {
  return (
    <aside className="floating-nav" aria-label="Primary navigation">
      <div className="brand-lockup" aria-label="VerifierForge">
        <span className="brand-mark" aria-hidden="true"><i>V</i><i>F</i></span>
        <span className="brand-name">Verifier<br />Forge</span>
      </div>
      <nav className="nav-links">
        {navItems.map(({ label, to, icon: Icon }) => (
          <NavLink key={label} to={to} className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
            <Icon size={19} strokeWidth={1.8} aria-hidden="true" />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="nav-evidence">
        <span><i className="status-dot" />Live API</span>
        <span><ShieldCheck size={13} aria-hidden="true" />Evidence locked</span>
      </div>
    </aside>
  )
}
