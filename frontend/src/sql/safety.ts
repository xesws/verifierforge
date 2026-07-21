const COMPLETE_SQL_FENCE = /^\s*```(?:sql)?[ \t]*\r?\n([\s\S]*?)(?:\r?\n)?```\s*$/i
const FORBIDDEN_KEYWORDS = new Set([
  'ALTER', 'ANALYZE', 'ATTACH', 'BEGIN', 'COMMIT', 'CREATE', 'DELETE',
  'DETACH', 'DROP', 'END', 'INSERT', 'LOAD_EXTENSION', 'PRAGMA', 'REINDEX',
  'RELEASE', 'REPLACE', 'ROLLBACK', 'SAVEPOINT', 'UPDATE', 'VACUUM',
])

export class SqlRejectedError extends Error {
  readonly code: string

  constructor(code: string, message: string) {
    super(message)
    this.name = 'SqlRejectedError'
    this.code = code
  }
}

export function normalizeModelSql(rawSql: string): string {
  if (!rawSql.trim()) throw new SqlRejectedError('empty_sql', 'The model returned no SQL to execute.')
  if (rawSql.length > 10_000) throw new SqlRejectedError('sql_too_long', 'SQL exceeds the 10,000 character sandbox limit.')
  const fence = COMPLETE_SQL_FENCE.exec(rawSql)
  if (rawSql.includes('```') && !fence) {
    throw new SqlRejectedError('unsupported_completion_format', 'Run requires plain SQL or one complete SQL code fence.')
  }
  const sql = (fence?.[1] ?? rawSql).trim()
  if (!sql) throw new SqlRejectedError('empty_sql', 'The model returned no SQL to execute.')
  if (sql.includes('```')) {
    throw new SqlRejectedError('unsupported_completion_format', 'Multiple or nested code fences are not executable.')
  }
  return sql
}

export function assertReadOnlySql(sql: string): void {
  const visible = stripQuotedTextAndComments(sql)
  const words = visible.toUpperCase().match(/[A-Z_]+/g) ?? []
  if (!['SELECT', 'WITH'].includes(words[0] ?? '')) {
    throw new SqlRejectedError('not_read_only_query', 'Only one SELECT or WITH query may run in the reviewer sandbox.')
  }
  const forbidden = words.find((word) => FORBIDDEN_KEYWORDS.has(word))
  if (forbidden) {
    throw new SqlRejectedError('forbidden_sql_keyword', `${forbidden} is not allowed in the read-only reviewer sandbox.`)
  }
}

function stripQuotedTextAndComments(sql: string): string {
  let result = ''
  let state: 'plain' | 'single' | 'double' | 'backtick' | 'bracket' | 'line_comment' | 'block_comment' = 'plain'
  for (let index = 0; index < sql.length; index += 1) {
    const char = sql[index]
    const next = sql[index + 1]
    if (state === 'plain') {
      if (char === '-' && next === '-') { state = 'line_comment'; result += '  '; index += 1; continue }
      if (char === '/' && next === '*') { state = 'block_comment'; result += '  '; index += 1; continue }
      if (char === "'") state = 'single'
      else if (char === '"') state = 'double'
      else if (char === '`') state = 'backtick'
      else if (char === '[') state = 'bracket'
      result += state === 'plain' ? char : ' '
      continue
    }
    if (state === 'line_comment') {
      if (char === '\n') { state = 'plain'; result += '\n' } else result += ' '
      continue
    }
    if (state === 'block_comment') {
      if (char === '*' && next === '/') { state = 'plain'; result += '  '; index += 1 } else result += ' '
      continue
    }
    result += ' '
    const closer = state === 'single' ? "'" : state === 'double' ? '"' : state === 'backtick' ? '`' : ']'
    if (char === closer) {
      if (next === closer && state !== 'bracket') { result += ' '; index += 1 }
      else state = 'plain'
    }
  }
  return result
}
