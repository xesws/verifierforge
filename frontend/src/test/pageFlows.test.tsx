import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../app/App'
import { AuthProvider } from '../state/AuthContext'
import { JourneyProvider } from '../state/JourneyContext'

const decision = { decision: 'forge', rationale: 'High SQL volume, deterministic verification, and positive payback support a forge.', confidence: .96, config: { base_model: 'Qwen/Qwen2.5-1.5B-Instruct', steps: 400, k: 8, checkpoint_interval: 50, budget_usd_cap: 5, provider_pref: 'auto' } }
const analysis = { decision_id: 'decision-1', cluster_id: 'data-pull-sql', decision, cached: false, created_at: '2026-07-20T00:00:00Z' }
const approval = { approval_id: 'approval-1', decision_id: 'decision-1', approved_by: 'judge', approved_at: '2026-07-20T00:01:00Z' }
const clusters = [
  { cluster_id: 'support-ticket-extraction', name: 'Support ticket extraction', monthly_calls: 240000, monthly_cost_usd: 4800, trainable: true, status: 'live', job_id: 'nl2sql-gain', routing: null, live_pass_rate: null, approved_sample_source: null, analyzer_decision: null },
  { cluster_id: 'invoice-field-extraction', name: 'Invoice field extraction', monthly_calls: 180000, monthly_cost_usd: 6000, trainable: true, status: 'discovered', job_id: null, routing: null, live_pass_rate: null, approved_sample_source: null, analyzer_decision: null },
  { cluster_id: 'data-pull-sql', name: 'Data Pull SQL', monthly_calls: 95000, monthly_cost_usd: 5500, trainable: true, status: 'discovered', job_id: 'd4-m3-1p5b-r1-v0125', routing: { cluster_id: 'data-pull-sql', enabled: false, canary_percent: 0, target_model: 'tuned' }, live_pass_rate: { cluster_id: 'data-pull-sql', points: [] }, approved_sample_source: null, analyzer_decision: decision },
]
const arena = { win_rate: .7, samples: Array.from({ length: 10 }, (_, index) => ({ prompt: `Held-out prompt ${index + 1}`, baseline_output: 'SELECT wrong;', tuned_output: 'SELECT correct;', baseline_score: index < 2 ? 1 : 0, tuned_score: index < 8 ? 1 : 0 })) }
const job = { job_id: 'd4-m3-1p5b-r1-v0125', template: 'nl2sql', status: 'done', model: 'Qwen/Qwen2.5-1.5B-Instruct', created_at: '2026-07-16T00:00:00Z', metrics: { steps: [1, 2], reward_mean: [.3, .7], pass_at_1: [.4, .8], entropy: [.7, .4] }, control: { pass_at_1: [.4, .45] }, report: { baseline_pass_at_1: .583333, final_pass_at_1: .783333, control_final_pass_at_1: .466667, verdict: 'real_gain', narrative: 'The tuned checkpoint improved on unseen SQL tasks.', projected_monthly_savings_usd: 3850, arena, savings_projection: { current_monthly_cost_usd: 5500, projected_monthly_cost_usd: 1650, projected_monthly_savings_usd: 3850, formula: 'savings = 5500 - 1650', assumptions: ['70% of eligible calls route to the specialist.'] }, provenance: { artifact_version: 'v1', s3_prefix: null, generated_at: '2026-07-16T00:00:00Z', content_sha256: 'a'.repeat(64), sources: [{ path: 'held-out.jsonl', sha256: 'b'.repeat(64) }] } }, endpoint: { base_url: 'registry://vf-demo', model_name: 'step-350' } }
const cold = { session_id: null, model_id: 'vf-demo', state: 'cold', url: null, detail: 'Endpoint is cold; click Wake model.', error_code: null, gpu_model: null, hourly_price_usd: null, cost_accrued_usd: 0, cold_start_seconds: 267, updated_at: '2026-07-20T00:00:00Z' }
const ready = { ...cold, session_id: 'sv-ready', state: 'ready', url: 'https://model.example/v1', detail: 'S3 identity, vLLM, completion, and public tunnel gates passed', gpu_model: 'RTX 4000 Ada', hourly_price_usd: .2, cold_start_seconds: 272, updated_at: '2026-07-20T00:05:00Z' }

function response(payload: unknown, status = 200, headers: Record<string, string> = {}) { return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json', ...headers } }) }

function apiFixture(serving: Record<string, unknown> = cold) {
  return (path: string, method: string) => {
    if (path === '/clusters') return response(clusters)
    if (path === '/clusters/data-pull-sql') return response(clusters[2])
    if (path.endsWith('/agent/analyze') && method === 'POST') return response(analysis)
    if (path.endsWith('/agent/decision')) return response(analysis)
    if (path.endsWith('/approvals') && method === 'POST') return response(approval)
    if (path.endsWith('/approval')) return response(approval)
    if (path.endsWith('/forge-execution')) return response({ approval_id: 'approval-1', decision_id: 'decision-1', job_id: job.job_id, provider: 'runpod', state: 'approved', budget_usd_cap: 5, cost_accrued_usd: 0, provision_handle: null, credential_source: null, detail: 'Approved; Start disabled', created_at: approval.approved_at, updated_at: approval.approved_at })
    if (path.endsWith('/start-forge')) return response({ detail: 'Start Forge is disabled because VF_AUTOPROVISION=false' }, 404)
    if (path === '/jobs') return response([{ job_id: job.job_id, status: 'done' }])
    if (path === `/jobs/${job.job_id}`) return response(job)
    if (path === `/jobs/${job.job_id}/metrics`) return response(job.metrics)
    if (path.endsWith('/routing')) return response({ cluster_id: 'data-pull-sql', enabled: false, canary_percent: 0, target_model: 'tuned' })
    if (path.endsWith('/live-pass-rate')) return response({ cluster_id: 'data-pull-sql', points: [{ timestamp: '2026-07-20T00:00:00Z', pass_rate: .9 }] })
    if (path.endsWith('/sample-source')) return response(null)
    if (path.includes('/settings/provider-credentials/')) return response({ user_id: 'judge', provider: 'runpod', configured: false, source: 'missing', credential_id: null, updated_at: null })
    if (path === '/serving/status') return response(serving)
    if (path === '/serving/tuned-completion' && method === 'POST') return response({ id: 'chatcmpl-tuned-1', model: 'verifierforge-step-350', choices: [{ index: 0, message: { role: 'assistant', content: 'SELECT customer_id, SUM(total) FROM orders GROUP BY customer_id ORDER BY SUM(total) DESC LIMIT 5;' }, finish_reason: 'stop' }], usage: { prompt_tokens: 18, completion_tokens: 24, total_tokens: 42 } }, 200, { 'X-VerifierForge-Route': 'tuned' })
    return response({ detail: `missing fixture ${method} ${path}` }, 404)
  }
}

function renderAt(path: string, fetcher = apiFixture()) {
  window.history.replaceState({}, '', path)
  window.sessionStorage.setItem('verifierforge.invitation.session.v1', 'fixture-invite')
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => fetcher(new URL(String(input)).pathname, init?.method ?? 'GET')))
  return render(<AuthProvider><JourneyProvider><App /></JourneyProvider></AuthProvider>)
}

function seedJourney(stage: 'forge' | 'runs' | 'proof' | 'ship') {
  window.sessionStorage.setItem('verifierforge.journey.session.v1', JSON.stringify({
    version: 1,
    clusterId: 'data-pull-sql',
    decisionId: 'decision-1',
    approvalId: stage === 'forge' ? null : 'approval-1',
    selectedJobId: ['runs', 'proof', 'ship'].includes(stage) ? job.job_id : null,
    runReviewed: ['proof', 'ship'].includes(stage),
    proofAcknowledged: stage === 'ship',
  }))
}

beforeEach(() => vi.stubEnv('VITE_VF_API_BASE_URL', 'https://api.example.test'))
afterEach(() => { cleanup(); window.sessionStorage.clear(); vi.unstubAllEnvs(); vi.unstubAllGlobals() })

describe('reviewer product path', () => {
  it.each(['/forge/new', `/jobs/${job.job_id}`, `/reports/${job.job_id}`, '/ship/data-pull-sql'])('redirects a fresh deep link %s to Discover', async (path) => {
    renderAt(path)
    expect(await screen.findByText('Data Pull SQL')).toBeInTheDocument()
    expect(screen.getByText(/First Analyze an optimizable workload/i)).toBeInTheDocument()
    expect(screen.getByText('Forge').closest('.nav-item')).toHaveAttribute('aria-disabled', 'true')
  })

  it('enforces Discover → Forge → Runs → Proof → Ship in one reviewer session', async () => {
    renderAt('/discover')
    expect(await screen.findByText('Data Pull SQL')).toBeInTheDocument()
    expect(screen.getByText('$5,500')).toBeInTheDocument()
    expect(screen.queryByText('Wake the tuned model')).not.toBeInTheDocument()
    expect(screen.queryByText('Existing proof')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve & Forge' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Analyze' }))
    const forgeLink = await screen.findByRole('link', { name: /Continue to Forge/i })
    const analyzeCall = vi.mocked(fetch).mock.calls.find(([input, init]) => new URL(String(input)).pathname.endsWith('/agent/analyze') && init?.method === 'POST')
    expect(analyzeCall).toBeDefined()
    expect(JSON.parse(String(analyzeCall?.[1]?.body))).toEqual({})
    fireEvent.click(forgeLink)
    expect(await screen.findByText('Approve first. Start separately.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Approve & Forge' }))
    expect(await screen.findByText(/No GPU has been allocated/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Review completed Run/i }))
    expect(await screen.findByText('Training converged. Now inspect the evidence.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Continue to Proof/i }))
    expect(await screen.findByText('78.3%')).toBeInTheDocument()
    expect(screen.getByText('$3,850')).toBeInTheDocument()
    expect(screen.getByText('Held-out prompt 1')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Accept evidence & continue to Ship/i }))
    expect(await screen.findByText('Wake the tuned model')).toBeInTheDocument()
    expect(screen.getByText('Try one tuned SQL compilation')).toBeInTheDocument()
  })

  it('shows the complete tuned result and request activity instead of only an ID', async () => {
    seedJourney('ship')
    renderAt('/ship/data-pull-sql', apiFixture(ready))
    const run = await screen.findByRole('button', { name: /Run tuned completion/i })
    const sample = screen.getByRole('button', { name: 'Aggregate hours' })
    fireEvent.click(sample)
    expect(sample).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByLabelText('Natural-language query')).toHaveValue('For every project whose combined employee hours are no less than 100, output the project name and total hours, sorted by project name ascending.')
    fireEvent.click(run)
    expect(await screen.findByText('Result is ready')).toBeInTheDocument()
    expect(screen.getByText(/SELECT customer_id, SUM\(total\)/)).toBeInTheDocument()
    expect(screen.getByText('chatcmpl-tuned-1')).toBeInTheDocument()
    expect(screen.getByText('42')).toBeInTheDocument()
    expect(screen.getAllByText('tuned').length).toBeGreaterThan(0)
    expect(screen.getByText(/Canary is not consulted/i)).toBeInTheDocument()
    const completionCall = vi.mocked(fetch).mock.calls.find(([input, init]) => new URL(String(input)).pathname === '/serving/tuned-completion' && init?.method === 'POST')
    const completionBody = JSON.parse(String(completionCall?.[1]?.body)) as { messages: Array<{ role: string; content: string }> }
    expect(completionBody.messages[1].content).toBe('For every project whose combined employee hours are no less than 100, output the project name and total hours, sorted by project name ascending.')
  })

  it('restores a valid session journey and resets it when leaving', async () => {
    seedJourney('proof')
    renderAt(`/reports/${job.job_id}`)
    expect(await screen.findByText('78.3%')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Leave session' }))
    expect(await screen.findByText('Reviewer access')).toBeInTheDocument()
    expect(window.sessionStorage.getItem('verifierforge.journey.session.v1')).toBeNull()
  })
})
