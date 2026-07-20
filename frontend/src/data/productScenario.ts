export type ClusterStatus = 'live' | 'discovered'

export interface ProductCluster {
  id: string
  name: string
  description: string
  callsPerMonth: number
  spendPerMonth: number
  status: ClusterStatus
  linkedJob?: string
}

export const productSummary = {
  clusterCount: 3,
  monthlySpend: 16_300,
  provenModels: 1,
  projectedMonthlySavings: 4_300,
  projectedArenaWinRate: 0.95,
} as const

export const clusters: readonly ProductCluster[] = [
  {
    id: 'data-pull-sql',
    name: 'Data Pull SQL',
    description: 'Natural language to production-safe analytical SQL.',
    callsPerMonth: 95_000,
    spendPerMonth: 5_500,
    status: 'live',
    linkedJob: 'd4-m3-1p5b-r1-v0125',
  },
  {
    id: 'support-ticket-extraction',
    name: 'Support Ticket Extraction',
    description: 'Structured intent, urgency, and ownership extraction.',
    callsPerMonth: 240_000,
    spendPerMonth: 4_800,
    status: 'discovered',
  },
  {
    id: 'invoice-field-extraction',
    name: 'Invoice Field Extraction',
    description: 'Normalize vendor, line item, and payment fields.',
    callsPerMonth: 180_000,
    spendPerMonth: 6_000,
    status: 'discovered',
  },
]

export interface ArenaSample {
  id: number
  prompt: string
  baseline: string
  tuned: string
  reason: string
}

export const arenaSamples: readonly ArenaSample[] = [
  { id: 1, prompt: 'Weekly revenue by region, highest first', baseline: 'SELECT region, SUM(revenue) FROM orders;', tuned: 'SELECT region, SUM(revenue) AS revenue FROM orders GROUP BY region ORDER BY revenue DESC;', reason: 'Adds grouping and deterministic ordering.' },
  { id: 2, prompt: 'Customers with no orders in the last 90 days', baseline: 'SELECT * FROM customers WHERE last_order < 90;', tuned: "SELECT c.* FROM customers c LEFT JOIN orders o ON o.customer_id = c.id AND o.created_at >= DATE('now', '-90 days') WHERE o.id IS NULL;", reason: 'Uses an anti-join with a valid date boundary.' },
  { id: 3, prompt: 'Median ticket resolution time by team', baseline: 'SELECT team, AVG(resolved_at-created_at) FROM tickets;', tuned: 'SELECT team_id, PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY resolved_at - created_at) AS median_resolution FROM tickets GROUP BY team_id;', reason: 'Calculates the requested median, not a mean.' },
  { id: 4, prompt: 'Top three products in every category', baseline: 'SELECT * FROM products ORDER BY sales DESC LIMIT 3;', tuned: 'WITH ranked AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY category_id ORDER BY sales DESC) AS rank FROM products) SELECT * FROM ranked WHERE rank <= 3;', reason: 'Ranks independently inside every category.' },
  { id: 5, prompt: 'Active subscriptions that failed renewal', baseline: "SELECT * FROM subscriptions WHERE status = 'failed';", tuned: "SELECT s.* FROM subscriptions s JOIN payments p ON p.subscription_id = s.id WHERE s.status = 'active' AND p.kind = 'renewal' AND p.status = 'failed';", reason: 'Preserves active subscription semantics.' },
  { id: 6, prompt: 'Month-over-month conversion change', baseline: 'SELECT month, conversions / visits FROM funnel;', tuned: 'WITH rates AS (SELECT month, 1.0 * conversions / NULLIF(visits, 0) AS rate FROM funnel) SELECT month, rate, rate - LAG(rate) OVER (ORDER BY month) AS mom_change FROM rates;', reason: 'Adds safe division and a windowed comparison.' },
  { id: 7, prompt: 'Duplicate invoices by vendor and amount', baseline: 'SELECT vendor, amount FROM invoices;', tuned: 'SELECT vendor_id, amount, COUNT(*) AS duplicates FROM invoices GROUP BY vendor_id, amount HAVING COUNT(*) > 1;', reason: 'Returns only repeated vendor/amount pairs.' },
]

export const routingTargets = [
  'Qwen/Qwen2.5-1.5B-Instruct · tuned',
  'Baseline provider model',
] as const

export const forgeDefaults = {
  taskDescription: 'Translate analyst questions into safe, executable SQL for the warehouse schema.',
  schemaContext: 'orders(id, customer_id, region, revenue, created_at)\ncustomers(id, name, segment)',
  examplePairs: 'Question: Weekly revenue by region\nAnswer: SELECT region, SUM(revenue) ... GROUP BY region',
} as const
