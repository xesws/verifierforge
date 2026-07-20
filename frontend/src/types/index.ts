export interface MetricPoint {
  step: number
  reward_mean: number
  pass_at_1: number
  entropy: number
  timestamp: string
}

export type JobStatus = 'queued' | 'done'

export interface LocalJob {
  id: string
  clusterId: string
  clusterName: string
  description: string
  schemaContext: string
  examples: string
  createdAt: string
  status: JobStatus
}

export type EvidenceMetric = 'quality' | 'reward' | 'entropy'
