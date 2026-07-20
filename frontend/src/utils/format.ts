export const formatCurrency = (value: number): string => `$${value.toLocaleString('en-US')}`
export const formatCompact = (value: number): string => new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(value)
export const formatPercent = (value: number, digits = 1): string => `${(value * 100).toFixed(digits)}%`
