import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { ApiError } from '../api/client'
import type { AgentAnalysisResponse, ApprovalRecord } from '../api/contracts'
import { useAuth } from './AuthContext'
import { JOURNEY_STORAGE_KEY } from './storage'

export type JourneyStage = 'discover' | 'forge' | 'runs' | 'proof' | 'ship'

interface JourneyRecord {
  version: 1
  clusterId: string | null
  decisionId: string | null
  approvalId: string | null
  selectedJobId: string | null
  runReviewed: boolean
  proofAcknowledged: boolean
}

interface JourneyState extends JourneyRecord {
  stage: JourneyStage
  validating: boolean
  validationError: string | null
  recordAnalysis: (analysis: AgentAnalysisResponse) => void
  recordApproval: (approval: ApprovalRecord) => void
  selectJob: (jobId: string) => void
  markRunReviewed: () => void
  acknowledgeProof: () => void
  reset: () => void
}

const EMPTY: JourneyRecord = {
  version: 1,
  clusterId: null,
  decisionId: null,
  approvalId: null,
  selectedJobId: null,
  runReviewed: false,
  proofAcknowledged: false,
}

const JourneyContext = createContext<JourneyState | null>(null)

function storedJourney(): JourneyRecord {
  try {
    const value = JSON.parse(window.sessionStorage.getItem(JOURNEY_STORAGE_KEY) ?? 'null') as Partial<JourneyRecord> | null
    if (!value || value.version !== 1) return EMPTY
    return {
      version: 1,
      clusterId: typeof value.clusterId === 'string' ? value.clusterId : null,
      decisionId: typeof value.decisionId === 'string' ? value.decisionId : null,
      approvalId: typeof value.approvalId === 'string' ? value.approvalId : null,
      selectedJobId: typeof value.selectedJobId === 'string' ? value.selectedJobId : null,
      runReviewed: value.runReviewed === true,
      proofAcknowledged: value.proofAcknowledged === true,
    }
  } catch {
    return EMPTY
  }
}

function stageFor(value: JourneyRecord): JourneyStage {
  if (value.proofAcknowledged) return 'ship'
  if (value.runReviewed) return 'proof'
  if (value.selectedJobId) return 'runs'
  if (value.decisionId) return 'forge'
  return 'discover'
}

function sameRecord(left: JourneyRecord, right: JourneyRecord) {
  return JSON.stringify(left) === JSON.stringify(right)
}

export function JourneyProvider({ children }: { children: ReactNode }) {
  const { client, invitation } = useAuth()
  const [record, setRecord] = useState<JourneyRecord>(storedJourney)
  const [validating, setValidating] = useState(false)
  const [validationError, setValidationError] = useState<string | null>(null)

  const update = useCallback((next: JourneyRecord) => {
    window.sessionStorage.setItem(JOURNEY_STORAGE_KEY, JSON.stringify(next))
    setRecord(next)
  }, [])
  const reset = useCallback(() => {
    window.sessionStorage.removeItem(JOURNEY_STORAGE_KEY)
    setRecord(EMPTY)
    setValidationError(null)
  }, [])

  useEffect(() => {
    if (!invitation) reset()
  }, [invitation, reset])

  useEffect(() => {
    if (!client || !invitation || !record.clusterId || !record.decisionId) {
      setValidating(false)
      return
    }
    let cancelled = false
    const validate = async () => {
      setValidating(true)
      setValidationError(null)
      let next = record
      try {
        const analysis = (await client.getDecision(record.clusterId!)).data
        if (
          analysis.decision_id !== record.decisionId
          || analysis.decision.decision !== 'forge'
          || !analysis.decision.config
        ) {
          next = EMPTY
        }
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) next = EMPTY
        else throw error
      }
      if (next.approvalId && next.decisionId) {
        try {
          const approval = (await client.getApproval(next.decisionId)).data
          if (approval.approval_id !== next.approvalId) {
            next = { ...next, approvalId: null, selectedJobId: null, runReviewed: false, proofAcknowledged: false }
          }
        } catch (error) {
          if (error instanceof ApiError && error.status === 404) {
            next = { ...next, approvalId: null, selectedJobId: null, runReviewed: false, proofAcknowledged: false }
          } else throw error
        }
      }
      if (next.selectedJobId) {
        try {
          const job = (await client.getJob(next.selectedJobId)).data
          if (next.runReviewed && (job.status !== 'done' || !job.report)) {
            next = { ...next, runReviewed: false, proofAcknowledged: false }
          }
        } catch (error) {
          if (error instanceof ApiError && error.status === 404) {
            next = { ...next, selectedJobId: null, runReviewed: false, proofAcknowledged: false }
          } else throw error
        }
      }
      if (!cancelled && !sameRecord(next, record)) update(next)
    }
    void validate()
      .catch((error) => {
        if (!cancelled) setValidationError(error instanceof Error ? error.message : 'Journey validation failed')
      })
      .finally(() => { if (!cancelled) setValidating(false) })
    return () => { cancelled = true }
  }, [client, invitation, record, update])

  const value = useMemo<JourneyState>(() => ({
    ...record,
    stage: stageFor(record),
    validating,
    validationError,
    recordAnalysis: (analysis) => {
      if (analysis.decision.decision !== 'forge' || !analysis.decision.config) return
      update({ ...EMPTY, clusterId: analysis.cluster_id, decisionId: analysis.decision_id })
    },
    recordApproval: (approval) => {
      if (approval.decision_id !== record.decisionId) return
      update({ ...record, approvalId: approval.approval_id, selectedJobId: null, runReviewed: false, proofAcknowledged: false })
    },
    selectJob: (jobId) => {
      if (!record.approvalId) return
      update({ ...record, selectedJobId: jobId, runReviewed: false, proofAcknowledged: false })
    },
    markRunReviewed: () => {
      if (record.selectedJobId) update({ ...record, runReviewed: true, proofAcknowledged: false })
    },
    acknowledgeProof: () => {
      if (record.runReviewed) update({ ...record, proofAcknowledged: true })
    },
    reset,
  }), [record, reset, update, validating, validationError])

  return <JourneyContext.Provider value={value}>{children}</JourneyContext.Provider>
}

// Provider and hook intentionally share the private context.
// eslint-disable-next-line react-refresh/only-export-components
export function useJourney(): JourneyState {
  const value = useContext(JourneyContext)
  if (!value) throw new Error('useJourney must be used inside JourneyProvider')
  return value
}

// Navigation guards share the provider's stage ordering.
// eslint-disable-next-line react-refresh/only-export-components
export function journeyAllows(current: JourneyStage, required: JourneyStage) {
  const order: JourneyStage[] = ['discover', 'forge', 'runs', 'proof', 'ship']
  return order.indexOf(current) >= order.indexOf(required)
}
