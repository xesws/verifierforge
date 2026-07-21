import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../app/App'
import { AuthProvider } from '../state/AuthContext'

const decision = { decision: 'forge', rationale: 'High SQL volume, deterministic verification, and positive payback support a forge.', confidence: .96, config: { base_model: 'Qwen/Qwen2.5-1.5B-Instruct', steps: 400, k: 8, checkpoint_interval: 50, budget_usd_cap: 5, provider_pref: 'auto' } }
const clusters = [
  { cluster_id: 'support-ticket-extraction', name: 'Support ticket extraction', monthly_calls: 240000, monthly_cost_usd: 4800, trainable: true, status: 'live', job_id: 'nl2sql-gain', routing: null, live_pass_rate: null, approved_sample_source: null, analyzer_decision: null },
  { cluster_id: 'invoice-field-extraction', name: 'Invoice field extraction', monthly_calls: 180000, monthly_cost_usd: 6000, trainable: true, status: 'discovered', job_id: null, routing: null, live_pass_rate: null, approved_sample_source: null, analyzer_decision: null },
  { cluster_id: 'data-pull-sql', name: 'Data Pull SQL', monthly_calls: 95000, monthly_cost_usd: 5500, trainable: true, status: 'discovered', job_id: null, routing: { cluster_id: 'data-pull-sql', enabled: false, canary_percent: 0, target_model: 'tuned' }, live_pass_rate: { cluster_id: 'data-pull-sql', points: [] }, approved_sample_source: null, analyzer_decision: decision },
]
const arena = { win_rate: .7, samples: Array.from({ length: 10 }, (_, index) => ({ prompt: `Held-out prompt ${index + 1}`, baseline_output: 'SELECT wrong;', tuned_output: 'SELECT correct;', baseline_score: index < 2 ? 1 : 0, tuned_score: index < 8 ? 1 : 0 })) }
const job = { job_id: 'd4-m3-1p5b-r1-v0125', template: 'nl2sql', status: 'done', model: 'Qwen/Qwen2.5-1.5B-Instruct', created_at: '2026-07-16T00:00:00Z', metrics: { steps: [1, 2], reward_mean: [.3, .7], pass_at_1: [.4, .8], entropy: [.7, .4] }, control: { pass_at_1: [.4, .45] }, report: { baseline_pass_at_1: .583333, final_pass_at_1: .783333, control_final_pass_at_1: .466667, verdict: 'real_gain', narrative: 'The tuned checkpoint improved on unseen SQL tasks.', projected_monthly_savings_usd: 3850, arena, savings_projection: { current_monthly_cost_usd: 5500, projected_monthly_cost_usd: 1650, projected_monthly_savings_usd: 3850, formula: 'savings = 5500 - 1650', assumptions: ['70% of eligible calls route to the specialist.'] }, provenance: { artifact_version: 'v1', s3_prefix: null, generated_at: '2026-07-16T00:00:00Z', content_sha256: 'a'.repeat(64), sources: [{ path: 'held-out.jsonl', sha256: 'b'.repeat(64) }] } }, endpoint: { base_url: 'registry://vf-demo', model_name: 'step-350' } }

function response(payload: unknown, status = 200) { return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } }) }
function renderAt(path: string, fetcher: (path: string, method: string) => Response | Promise<Response>) {
  window.history.replaceState({}, '', path)
  window.sessionStorage.setItem('verifierforge.invitation.session.v1', 'fixture-invite')
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => fetcher(new URL(String(input)).pathname, init?.method ?? 'GET')))
  return render(<AuthProvider><App /></AuthProvider>)
}

beforeEach(() => vi.stubEnv('VITE_VF_API_BASE_URL', 'https://api.example.test'))
afterEach(() => { cleanup(); window.sessionStorage.clear(); vi.unstubAllEnvs(); vi.unstubAllGlobals() })

describe('reviewer product path', () => {
  it('shows real cluster economics, agent rationale, input, and separate human controls', async () => {
    renderAt('/discover', (path, method) => {
      if (path === '/clusters') return response(clusters)
      if (path.endsWith('/agent/decision')) return response({ detail: 'missing' }, 404)
      if (path === '/serving/status') return response({ session_id: null, model_id: 'vf-demo', state: 'cold', url: null, detail: 'Wake required', error_code: null, gpu_model: null, hourly_price_usd: null, cost_accrued_usd: 0, cold_start_seconds: null, updated_at: '2026-07-20T00:00:00Z' })
      if (path.endsWith('/agent/analyze') && method === 'POST') return response({ decision_id: 'decision-1', cluster_id: 'data-pull-sql', decision, cached: false, created_at: '2026-07-20T00:00:00Z' })
      return response({ detail: 'missing fixture' }, 404)
    })
    expect(await screen.findByText('Data Pull SQL')).toBeInTheDocument()
    expect(screen.getByText('$5,500')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Input' })).toBeInTheDocument()
    expect(screen.getByText('A specialist is economically defensible.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve & Forge' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Analyze' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Approve & Forge' })).toBeEnabled())
    expect(screen.getByText(/does not allocate a GPU/i)).toBeInTheDocument()
  })

  it('renders the report four-piece payload without static fixtures', async () => {
    renderAt('/reports/d4-m3-1p5b-r1-v0125', (path) => path.includes('/jobs/') ? response(job) : response({}, 404))
    expect(await screen.findByText('78.3%')).toBeInTheDocument()
    expect(screen.getByText('$3,850')).toBeInTheDocument()
    expect(screen.getByText('Held-out prompt 1')).toBeInTheDocument()
    expect(screen.getByText('real_gain')).toBeInTheDocument()
  })

  it('keeps Ship usable while serving is cold and saves routing explicitly', async () => {
    let saved = false
    renderAt('/ship/data-pull-sql', (path, method) => {
      if (path.endsWith('/routing')) { if (method === 'PUT') saved = true; return response({ cluster_id: 'data-pull-sql', enabled: false, canary_percent: 0, target_model: 'tuned' }) }
      if (path.endsWith('/live-pass-rate')) return response({ cluster_id: 'data-pull-sql', points: [{ timestamp: '2026-07-20T00:00:00Z', pass_rate: .9 }] })
      if (path === '/serving/status') return response({ session_id: null, model_id: 'vf-demo', state: 'cold', url: null, detail: 'Endpoint is cold; click Wake model.', error_code: null, gpu_model: null, hourly_price_usd: null, cost_accrued_usd: 0, cold_start_seconds: 267, updated_at: '2026-07-20T00:00:00Z' })
      return response({}, 404)
    })
    expect(await screen.findByText('Live guardian')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run tuned completion' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Save routing policy' }))
    await waitFor(() => expect(saved).toBe(true))
  })
})
