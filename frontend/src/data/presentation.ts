export const FLAGSHIP_JOB_ID = 'd4-m3-1p5b-r1-v0125'
export const SERVING_MODEL_ID = 'vf-demo'
export const DEFAULT_BASE_MODEL = 'Qwen/Qwen2.5-1.5B-Instruct'
export const SQL_SYSTEM_PROMPT = 'Return exactly one read-only SQL SELECT or WITH statement. Do not include an explanation.'
export const SQL_SAMPLE_SOURCE = {
  uri: 'data/nl2sql/v0.10.0-training-pool.jsonl',
  sha256: 'c97a5adea789fae3be249bc9ac95a1902ae5a9769de9eefbc08277f056878e8c',
  rowCount: 50,
} as const

export const clusterDescriptions: Record<string, string> = {
  'data-pull-sql': 'Natural language to production-safe analytical SQL.',
  'support-ticket-extraction': 'Structured intent, urgency, and ownership extraction.',
  'invoice-field-extraction': 'Normalize vendor, line item, and payment fields.',
}

export const clusterOrder = [
  'data-pull-sql',
  'support-ticket-extraction',
  'invoice-field-extraction',
] as const
