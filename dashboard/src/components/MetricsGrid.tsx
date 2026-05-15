import type { BacktestMetrics } from '../types'

// Metrics to display and their display order
const METRIC_CONFIG: { key: string; label: string; colorize?: boolean; invert?: boolean }[] = [
  { key: 'Total Return',      label: 'Total Return',     colorize: true },
  { key: 'Alpha',             label: 'Alpha vs Index',   colorize: true },
  { key: 'Win Rate',          label: 'Win Rate',         colorize: true },
  { key: 'Sharpe Ratio',      label: 'Sharpe Ratio',     colorize: true },
  { key: 'Calmar Ratio',      label: 'Calmar Ratio',     colorize: true },
  { key: 'Max Drawdown',      label: 'Max Drawdown',     colorize: true, invert: true },
  { key: 'Total Trades',      label: 'Total Trades' },
  { key: 'Total Commissions', label: 'Commissions Paid' },
  { key: 'Final Portfolio',   label: 'Final Portfolio' },
]

function parseNum(val: string | number | undefined): number | null {
  if (val === undefined || val === null) return null
  if (typeof val === 'number') return val
  const n = parseFloat(val.replace(/%|,| EGP/g, ''))
  return isNaN(n) ? null : n
}

function valueClass(
  val: string | number | undefined,
  colorize: boolean,
  invert: boolean,
): string {
  if (!colorize) return 'neutral'
  const n = parseNum(val)
  if (n === null) return 'neutral'
  // For inverted metrics (drawdown) negative is bad, positive is good
  if (invert) return n < 0 ? 'negative' : 'neutral'
  return n > 0 ? 'positive' : n < 0 ? 'negative' : 'neutral'
}

interface Props {
  metrics: BacktestMetrics
}

export default function MetricsGrid({ metrics }: Props) {
  return (
    <div className="metrics-grid">
      {METRIC_CONFIG.map(({ key, label, colorize = false, invert = false }) => {
        const raw = metrics[key]
        if (raw === undefined) return null
        return (
          <div className="metric-card" key={key}>
            <span className="metric-label">{label}</span>
            <span className={`metric-value ${valueClass(raw, colorize, invert)}`}>
              {String(raw)}
            </span>
          </div>
        )
      })}
    </div>
  )
}
