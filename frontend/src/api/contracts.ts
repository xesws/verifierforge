export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'early_stopped'
export type ClusterStatus = 'discovered' | 'forging' | 'live'
export type AgentDecisionType = 'forge' | 'skip' | 'need_more_data'
export type ForgeLifecycle = 'approved' | 'provisioning' | 'running' | 'collecting' | 'done' | 'failed'
export type ServingState = 'cold' | 'provisioning' | 'loading' | 'ready' | 'draining'
export type RoutePath = 'default' | 'tuned' | 'default-fallback'

export interface Metrics {
  steps: number[]
  reward_mean: number[]
  pass_at_1: number[]
  entropy: number[]
}

export interface Control {
  pass_at_1: number[]
}

export interface ArenaSample {
  prompt: string
  baseline_output: string
  tuned_output: string
  baseline_score: number
  tuned_score: number
}

export interface Arena {
  win_rate: number
  samples: ArenaSample[]
}

export interface SavingsProjection {
  current_monthly_cost_usd: number
  projected_monthly_cost_usd: number
  projected_monthly_savings_usd: number
  formula: string
  assumptions: string[]
}

export interface ArtifactSource {
  path: string
  sha256: string
}

export interface ReportProvenance {
  artifact_version: string
  s3_prefix: string | null
  generated_at: string
  content_sha256: string
  sources: ArtifactSource[]
}

export interface Report {
  baseline_pass_at_1: number
  final_pass_at_1: number
  control_final_pass_at_1: number
  verdict: 'real_gain' | 'suspect_formatting' | 'collapsed'
  narrative: string
  projected_monthly_savings_usd: number | null
  arena: Arena | null
  savings_projection: SavingsProjection | null
  provenance: ReportProvenance | null
}

export interface Endpoint {
  base_url: string
  model_name: string
}

export interface Job {
  job_id: string
  template: string
  status: JobStatus
  model: string
  created_at: string
  metrics: Metrics
  control: Control
  report: Report | null
  endpoint: Endpoint | null
}

export interface JobSummary {
  job_id: string
  status: JobStatus
}

export interface RoutingState {
  cluster_id: string
  enabled: boolean
  canary_percent: number
  target_model: string
}

export interface LivePassRatePoint {
  timestamp: string
  pass_rate: number
}

export interface LivePassRate {
  cluster_id: string
  points: LivePassRatePoint[]
}

export interface ApprovedSampleSource {
  kind: 'repository_jsonl'
  uri: string
  sha256: string
  row_count: number
  approved_by: string
  approved_at: string
}

export interface TrainingConfig {
  base_model: string
  steps: number
  k: number
  checkpoint_interval: number
  budget_usd_cap: number
  provider_pref: 'runpod' | 'nebius' | 'auto'
}

export interface AgentDecision {
  decision: AgentDecisionType
  rationale: string
  confidence: number
  config: TrainingConfig | null
}

export interface AgentAnalysisResponse {
  decision_id: string
  cluster_id: string
  decision: AgentDecision
  cached: boolean
  created_at: string
}

export interface ApprovalRecord {
  approval_id: string
  decision_id: string
  approved_by: string
  approved_at: string
}

export interface ForgeExecutionStatus {
  approval_id: string
  decision_id: string
  job_id: string
  provider: 'runpod' | 'nebius'
  state: ForgeLifecycle
  budget_usd_cap: number
  cost_accrued_usd: number
  provision_handle: string | null
  credential_source: 'stored' | 'system_env' | 'missing' | null
  detail: string
  created_at: string
  updated_at: string
}

export interface ProviderCredentialStatus {
  user_id: string
  provider: 'runpod' | 'nebius'
  configured: boolean
  source: 'stored' | 'system_env' | 'missing'
  credential_id: string | null
  updated_at: string | null
}

export interface ServingStatus {
  session_id: string | null
  model_id: string
  state: ServingState
  url: string | null
  detail: string
  error_code: string | null
  gpu_model: string | null
  hourly_price_usd: number | null
  cost_accrued_usd: number
  cold_start_seconds: number | null
  updated_at: string
}

export interface Cluster {
  cluster_id: string
  name: string
  monthly_calls: number
  monthly_cost_usd: number
  trainable: boolean
  status: ClusterStatus
  job_id: string | null
  routing: RoutingState | null
  live_pass_rate: LivePassRate | null
  approved_sample_source: ApprovedSampleSource | null
  analyzer_decision: AgentDecision | null
}

export interface JobCreateRequest {
  template: string
  model: string
}

export interface SampleSourceRequest {
  uri: string
  approved_by: string
  expected_sha256?: string
  expected_row_count?: number
}

export interface AgentAnalyzeRequest {
  data_source?: string
  execution_profile?: 'standard' | 'p2_gate_b'
  force_refresh?: boolean
}

export interface StartForgeRequest {
  requested_by: string
  confirm_provider_spend: true
}

export interface ServingWakeRequest {
  model_id: string
  confirm_provider_spend: true
}

export interface ChatCompletion {
  id: string
  model: string
  choices: Array<{ message: { role: string; content: string }; finish_reason?: string }>
}
