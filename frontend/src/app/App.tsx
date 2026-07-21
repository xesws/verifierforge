import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './AppShell'
import { DiscoverPage } from '../pages/DiscoverPage'
import { ForgePage } from '../pages/ForgePage'
import { JobPage } from '../pages/JobPage'
import { ReportPage } from '../pages/ReportPage'
import { ShipPage } from '../pages/ShipPage'
import { JourneyGuard } from '../components/JourneyGuard'

const TechPage = lazy(() => import('../pages/TechPage').then(({ TechPage: page }) => ({ default: page })))

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="tech" element={<Suspense fallback={<div className="tech-loading" role="status">Loading the evidence…</div>}><TechPage /></Suspense>} />
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/discover" replace />} />
          <Route path="discover" element={<DiscoverPage />} />
          <Route path="forge/new" element={<JourneyGuard required="forge"><ForgePage /></JourneyGuard>} />
          <Route path="jobs/:jobId" element={<JourneyGuard required="runs"><JobPage /></JourneyGuard>} />
          <Route path="reports/:jobId" element={<JourneyGuard required="proof"><ReportPage /></JourneyGuard>} />
          <Route path="ship/data-pull-sql" element={<JourneyGuard required="ship"><ShipPage /></JourneyGuard>} />
          <Route path="*" element={<Navigate to="/discover" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
