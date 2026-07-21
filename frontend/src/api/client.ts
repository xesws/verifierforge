import type {
  AgentAnalysisResponse,
  AgentAnalyzeRequest,
  ApprovalRecord,
  ApprovedSampleSource,
  ChatCompletion,
  Cluster,
  DemoTrafficRequest,
  DemoTrafficStatus,
  ForgeExecutionStatus,
  Job,
  JobCreateRequest,
  JobSummary,
  LivePassRate,
  Metrics,
  ProviderCredentialStatus,
  RoutePath,
  RoutingState,
  SampleSourceRequest,
  ServingStatus,
  ServingSleepRequest,
  ServingWakeRequest,
  StartForgeRequest,
} from './contracts'

export interface ApiResponse<T> {
  data: T
  status: number
  route: RoutePath | null
}

export class ApiConfigurationError extends Error {}

export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  readonly route: RoutePath | null

  constructor(status: number, detail: string, route: RoutePath | null = null) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.route = route
  }
}

interface ClientOptions {
  baseUrl?: string
  invitation?: () => string | null
  onUnauthorized?: () => void
  fetcher?: typeof fetch
  timeoutMs?: number
}

type JsonBody = object | undefined

export function configuredApiBaseUrl(): string {
  const value = import.meta.env.VITE_VF_API_BASE_URL?.trim()
  if (!value) throw new ApiConfigurationError('VITE_VF_API_BASE_URL is required')
  return normalizeBaseUrl(value)
}

export function normalizeBaseUrl(value: string): string {
  let url: URL
  try {
    url = new URL(value)
  } catch {
    throw new ApiConfigurationError('VITE_VF_API_BASE_URL must be an absolute URL')
  }
  const isLoopback = ['localhost', '127.0.0.1'].includes(url.hostname)
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && isLoopback)) {
    throw new ApiConfigurationError('API URL must use HTTPS except on loopback')
  }
  return value.replace(/\/+$/, '')
}

export class VerifierForgeClient {
  private readonly baseUrl: string
  private readonly invitation: () => string | null
  private readonly onUnauthorized: () => void
  private readonly fetcher: typeof fetch
  private readonly timeoutMs: number

  constructor(options: ClientOptions = {}) {
    this.baseUrl = normalizeBaseUrl(options.baseUrl ?? configuredApiBaseUrl())
    this.invitation = options.invitation ?? (() => null)
    this.onUnauthorized = options.onUnauthorized ?? (() => undefined)
    this.fetcher = (options.fetcher ?? window.fetch).bind(window)
    this.timeoutMs = options.timeoutMs ?? 20_000
  }

  listJobs = () => this.request<JobSummary[]>('/jobs')
  createJob = (body: JobCreateRequest) => this.request<Job>('/jobs', 'POST', body)
  getJob = (jobId: string) => this.request<Job>(`/jobs/${segment(jobId)}`)
  getMetrics = (jobId: string) => this.request<Metrics>(`/jobs/${segment(jobId)}/metrics`)
  listClusters = () => this.request<Cluster[]>('/clusters')
  getCluster = (clusterId: string) => this.request<Cluster>(`/clusters/${segment(clusterId)}`)
  analyze = (clusterId: string, body: AgentAnalyzeRequest = {}) =>
    this.request<AgentAnalysisResponse>(`/clusters/${segment(clusterId)}/agent/analyze`, 'POST', body)
  getDecision = (clusterId: string) =>
    this.request<AgentAnalysisResponse>(`/clusters/${segment(clusterId)}/agent/decision`)
  approve = (decisionId: string, approvedBy: string) =>
    this.request<ApprovalRecord>(`/agent-decisions/${segment(decisionId)}/approvals`, 'POST', { approved_by: approvedBy })
  getApproval = (decisionId: string) =>
    this.request<ApprovalRecord>(`/agent-decisions/${segment(decisionId)}/approval`)
  startForge = (approvalId: string, body: StartForgeRequest) =>
    this.request<ForgeExecutionStatus>(`/approvals/${segment(approvalId)}/start-forge`, 'POST', body)
  getForgeExecution = (approvalId: string) =>
    this.request<ForgeExecutionStatus>(`/approvals/${segment(approvalId)}/forge-execution`)
  getRouting = (clusterId: string) =>
    this.request<RoutingState>(`/clusters/${segment(clusterId)}/routing`)
  putRouting = (clusterId: string, body: RoutingState) =>
    this.request<RoutingState>(`/clusters/${segment(clusterId)}/routing`, 'PUT', body)
  getLivePassRate = (clusterId: string) =>
    this.request<LivePassRate>(`/clusters/${segment(clusterId)}/live-pass-rate`)
  getSampleSource = (clusterId: string) =>
    this.request<ApprovedSampleSource | null>(`/clusters/${segment(clusterId)}/sample-source`)
  putSampleSource = (clusterId: string, body: SampleSourceRequest) =>
    this.request<ApprovedSampleSource>(`/clusters/${segment(clusterId)}/sample-source`, 'PUT', body)
  getProviderCredential = (provider: 'runpod' | 'nebius', userId: string) =>
    this.request<ProviderCredentialStatus>(`/settings/provider-credentials/${provider}?user_id=${encodeURIComponent(userId)}`)
  putProviderCredential = (provider: 'runpod' | 'nebius', userId: string, apiKey: string) =>
    this.request<ProviderCredentialStatus>(`/settings/provider-credentials/${provider}`, 'PUT', { user_id: userId, api_key: apiKey })
  wakeServing = (body: ServingWakeRequest) => this.request<ServingStatus>('/serving/wake', 'POST', body)
  sleepServing = (body: ServingSleepRequest) => this.request<ServingStatus>('/serving/sleep', 'POST', body)
  getServingStatus = (modelId = 'vf-demo') =>
    this.request<ServingStatus>(`/serving/status?model_id=${encodeURIComponent(modelId)}`)
  tunedCompletion = (body: Record<string, unknown>) =>
    this.request<ChatCompletion>('/serving/tuned-completion', 'POST', body)
  chatCompletion = (body: Record<string, unknown>) =>
    this.request<ChatCompletion>('/proxy/v1/chat/completions', 'POST', body)
  startDemoTraffic = (body: DemoTrafficRequest = {}) =>
    this.request<DemoTrafficStatus>('/demo/traffic', 'POST', body)
  getDemoTrafficStatus = () =>
    this.request<DemoTrafficStatus>('/demo/traffic/status')

  private async request<T>(path: string, method = 'GET', body?: JsonBody): Promise<ApiResponse<T>> {
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), this.timeoutMs)
    const headers = new Headers({ Accept: 'application/json' })
    const invite = this.invitation()
    if (invite) headers.set('Authorization', basicAuthorization(invite))
    if (body !== undefined) headers.set('Content-Type', 'application/json')
    try {
      const response = await this.fetcher(`${this.baseUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      })
      const route = routeHeader(response.headers.get('X-VerifierForge-Route'))
      const payload = await parseJson(response)
      if (!response.ok) {
        if (response.status === 401) this.onUnauthorized()
        throw new ApiError(response.status, errorDetail(payload, response.status), route)
      }
      return { data: payload as T, status: response.status, route }
    } catch (error) {
      if (error instanceof ApiError) throw error
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new ApiError(0, 'Request timed out')
      }
      throw new ApiError(0, error instanceof Error ? error.message : 'Network request failed')
    } finally {
      window.clearTimeout(timeout)
    }
  }
}

function basicAuthorization(invite: string): string {
  const bytes = new TextEncoder().encode(`judge:${invite}`)
  let binary = ''
  bytes.forEach((byte) => { binary += String.fromCharCode(byte) })
  return `Basic ${window.btoa(binary)}`
}

function routeHeader(value: string | null): RoutePath | null {
  return value === 'default' || value === 'tuned' || value === 'default-fallback' ? value : null
}

function segment(value: string): string {
  return encodeURIComponent(value)
}

async function parseJson(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) return null
  try { return JSON.parse(text) as unknown } catch { return { detail: `HTTP ${response.status}` } }
}

function errorDetail(payload: unknown, status: number): string {
  if (payload && typeof payload === 'object' && 'detail' in payload && typeof payload.detail === 'string') {
    return payload.detail.slice(0, 1000)
  }
  return `Request failed with HTTP ${status}`
}
