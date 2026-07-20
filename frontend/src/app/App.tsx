import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './AppShell'
import { DiscoverPage } from '../pages/DiscoverPage'
import { ForgePage } from '../pages/ForgePage'
import { JobPage } from '../pages/JobPage'
import { ReportPage } from '../pages/ReportPage'
import { ShipPage } from '../pages/ShipPage'

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/discover" replace />} />
          <Route path="discover" element={<DiscoverPage />} />
          <Route path="forge/new" element={<ForgePage />} />
          <Route path="jobs/:jobId" element={<JobPage />} />
          <Route path="reports/:jobId" element={<ReportPage />} />
          <Route path="ship/data-pull-sql" element={<ShipPage />} />
          <Route path="*" element={<Navigate to="/discover" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
