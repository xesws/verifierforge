import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { ErrorState, LoadingState } from './ResourceState'
import { journeyAllows, type JourneyStage, useJourney } from '../state/JourneyContext'

const nextInstruction: Record<JourneyStage, string> = {
  discover: 'Analyze an optimizable workload in Discover',
  forge: 'approve a Forge plan and select its Run',
  runs: 'review the completed Run',
  proof: 'review and accept the held-out Proof',
  ship: 'use the Ship workspace',
}

export function JourneyGuard({ required, children }: { required: JourneyStage; children: ReactNode }) {
  const journey = useJourney()
  if (journey.validating) return <LoadingState label="Validating this reviewer step…" />
  if (journey.validationError) return <ErrorState message={journey.validationError} />
  if (!journeyAllows(journey.stage, required)) {
    const first = journey.stage === 'discover'
      ? '/discover'
      : journey.stage === 'forge'
        ? '/forge/new'
        : journey.stage === 'runs'
          ? `/jobs/${journey.selectedJobId}`
          : journey.stage === 'proof'
            ? `/reports/${journey.selectedJobId}`
            : '/ship/data-pull-sql'
    return <Navigate to={first} replace state={{ journeyNotice: `First ${nextInstruction[journey.stage]}.` }} />
  }
  return children
}
