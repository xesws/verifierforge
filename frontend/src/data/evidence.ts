import trainingMetrics from './generated/trainingMetrics.json'
import type { MetricPoint } from '../types'

export const jobEvidence = {
  mainJobId: trainingMetrics.source.mainJob,
  controlJobId: trainingMetrics.source.controlJob,
  mainModel: 'Qwen/Qwen2.5-1.5B-Instruct',
  controlModel: 'Qwen/Qwen2.5-0.5B-Instruct',
  mainSteps: 400,
  controlSteps: 200,
  trainingRows: 50,
  heldoutRows: trainingMetrics.evidence.heldoutRows,
  selectedCheckpoint: trainingMetrics.evidence.selectedCheckpointStep,
  verdict: trainingMetrics.evidence.verdict,
  selectionRule: trainingMetrics.evidence.selectionRule,
  heldout: {
    passAt1Before: trainingMetrics.evidence.heldoutBefore.pass_at_1,
    passAt1After: trainingMetrics.evidence.heldoutAfter.pass_at_1,
    passAt8Before: trainingMetrics.evidence.heldoutBefore.pass_at_8,
    passAt8After: trainingMetrics.evidence.heldoutAfter.pass_at_8,
    improvementPoints:
      (trainingMetrics.evidence.heldoutAfter.pass_at_1 -
        trainingMetrics.evidence.heldoutBefore.pass_at_1) *
      100,
  },
  checkpoints: trainingMetrics.evidence.checkpoints,
  source: trainingMetrics.source,
} as const

export const trainingEvidence = {
  main: trainingMetrics.main satisfies MetricPoint[],
  control: trainingMetrics.control satisfies MetricPoint[],
}
