import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiConfigurationError, ApiError, VerifierForgeClient, normalizeBaseUrl } from './client'
import { FROZEN_OPERATIONS } from './operations'

afterEach(() => vi.unstubAllEnvs())

function jsonResponse(payload: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json', ...init.headers },
    ...init,
  })
}

describe('frozen operation registry', () => {
  it('contains exactly the 21 frontend-api-v1 operations', () => {
    expect(FROZEN_OPERATIONS).toHaveLength(21)
    expect(new Set(FROZEN_OPERATIONS.map(([method, path]) => `${method} ${path}`)).size).toBe(21)
  })
})

describe('VerifierForgeClient', () => {
  it('normalizes only HTTPS and loopback HTTP origins', () => {
    expect(normalizeBaseUrl('https://api.example.test///')).toBe('https://api.example.test')
    expect(normalizeBaseUrl('http://127.0.0.1:8010/')).toBe('http://127.0.0.1:8010')
    expect(() => normalizeBaseUrl('http://api.example.test')).toThrow(ApiConfigurationError)
    expect(() => normalizeBaseUrl('/api')).toThrow(ApiConfigurationError)
  })

  it('exposes every frozen operation with its exact method and path', async () => {
    const calls: Array<{ method: string; path: string }> = []
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input))
      calls.push({ method: init?.method ?? 'GET', path: url.pathname })
      return jsonResponse(null)
    })
    const client = new VerifierForgeClient({ baseUrl: 'https://api.example.test', fetcher: fetcher as typeof fetch })

    await client.listJobs()
    await client.createJob({ template: 'nl2sql', model: 'Qwen/Qwen2.5-1.5B-Instruct' })
    await client.getJob('job-1')
    await client.getMetrics('job-1')
    await client.listClusters()
    await client.getCluster('data-pull-sql')
    await client.analyze('data-pull-sql')
    await client.getDecision('data-pull-sql')
    await client.approve('decision-1', 'owner')
    await client.getApproval('decision-1')
    await client.startForge('approval-1', { requested_by: 'owner', confirm_provider_spend: true })
    await client.getForgeExecution('approval-1')
    await client.getRouting('data-pull-sql')
    await client.putRouting('data-pull-sql', { cluster_id: 'data-pull-sql', enabled: true, canary_percent: 50, target_model: 'tuned' })
    await client.getLivePassRate('data-pull-sql')
    await client.getSampleSource('data-pull-sql')
    await client.putSampleSource('data-pull-sql', { uri: 'data/nl2sql/pool.jsonl', approved_by: 'owner' })
    await client.getProviderCredential('nebius', 'owner')
    await client.putProviderCredential('nebius', 'owner', 'fixture-only')
    await client.wakeServing({ model_id: 'vf-demo', confirm_provider_spend: true })
    await client.getServingStatus()

    const expected = FROZEN_OPERATIONS.map(([method, path]) => ({
      method,
      path: path
        .replace('{job_id}', 'job-1')
        .replace('{cluster_id}', 'data-pull-sql')
        .replace('{decision_id}', 'decision-1')
        .replace('{approval_id}', 'approval-1')
        .replace('{provider}', 'nebius'),
    }))
    expect(calls).toEqual(expected)
  })

  it('adds runtime Basic auth and preserves an observable tuned route', async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(new Headers(init?.headers).get('Authorization')).toBe('Basic anVkZ2U6aW52aXRlLWZpeHR1cmU=')
      return jsonResponse({ choices: [] }, { headers: { 'X-VerifierForge-Route': 'tuned' } })
    })
    const client = new VerifierForgeClient({
      baseUrl: 'https://api.example.test',
      invitation: () => 'invite-fixture',
      fetcher: fetcher as typeof fetch,
    })
    const response = await client.chatCompletion({ model: 'vf-demo', messages: [] })
    expect(response.route).toBe('tuned')
  })

  it('clears auth on 401 and retains safe server detail', async () => {
    const unauthorized = vi.fn()
    const client = new VerifierForgeClient({
      baseUrl: 'https://api.example.test',
      onUnauthorized: unauthorized,
      fetcher: vi.fn(async () => jsonResponse({ detail: 'Invitation required' }, { status: 401 })) as typeof fetch,
    })
    await expect(client.listClusters()).rejects.toMatchObject({ status: 401, detail: 'Invitation required' } satisfies Partial<ApiError>)
    expect(unauthorized).toHaveBeenCalledOnce()
  })

  it('never retries a failed mutation', async () => {
    const fetcher = vi.fn(async () => { throw new TypeError('offline') })
    const client = new VerifierForgeClient({ baseUrl: 'https://api.example.test', fetcher: fetcher as typeof fetch })
    await expect(client.createJob({ template: 'nl2sql', model: 'model' })).rejects.toMatchObject({ status: 0 })
    expect(fetcher).toHaveBeenCalledOnce()
  })
})
