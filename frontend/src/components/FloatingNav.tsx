import { BarChart3, BookOpenText, FlaskConical, LockKeyhole, PackageCheck, Radar, ShieldCheck } from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { FLAGSHIP_JOB_ID } from '../data/presentation'
import { journeyAllows, type JourneyStage, useJourney } from '../state/JourneyContext'

const navItems = [
  { label: 'Discover', required: 'discover', icon: Radar, reason: '' },
  { label: 'Forge', required: 'forge', icon: FlaskConical, reason: 'Analyze a forgeable workload first' },
  { label: 'Runs', required: 'runs', icon: BarChart3, reason: 'Approve and select a Run first' },
  { label: 'Proof', required: 'proof', icon: ShieldCheck, reason: 'Review a completed Run first' },
  { label: 'Ship', required: 'ship', icon: PackageCheck, reason: 'Accept the held-out Proof first' },
] as const

export function FloatingNav() {
  const journey = useJourney()
  const selectedJob = journey.selectedJobId ?? FLAGSHIP_JOB_ID
  const paths: Record<JourneyStage, string> = {
    discover: '/discover',
    forge: '/forge/new',
    runs: `/jobs/${selectedJob}`,
    proof: `/reports/${selectedJob}`,
    ship: '/ship/data-pull-sql',
  }
  return (
    <aside className="floating-nav" aria-label="Primary navigation">
      <div className="brand-lockup" aria-label="VerifierForge">
        <img className="brand-mark brand-mark-image" src="/favicon.svg" alt="" aria-hidden="true" />
        <span className="brand-name">Verifier<br />Forge</span>
      </div>
      <nav className="nav-links">
        {navItems.map(({ label, required, icon: Icon, reason }) => {
          const unlocked = journeyAllows(journey.stage, required)
          if (!unlocked) return (
            <span key={label} className="nav-item locked" aria-disabled="true" title={reason}>
              <LockKeyhole size={16} strokeWidth={1.8} aria-hidden="true" />
              <span>{label}</span>
              <small>{reason}</small>
            </span>
          )
          return (
            <NavLink key={label} to={paths[required]} className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
              <Icon size={19} strokeWidth={1.8} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          )
        })}
      </nav>
      <NavLink to="/tech" className={({ isActive }) => `nav-item tech-nav-item${isActive ? ' active' : ''}`}>
        <BookOpenText size={18} strokeWidth={1.8} aria-hidden="true" />
        <span>Tech</span>
      </NavLink>
      <div className="nav-evidence">
        <span><i className="status-dot" />Live API</span>
        <span><ShieldCheck size={13} aria-hidden="true" />Evidence locked</span>
      </div>
    </aside>
  )
}
