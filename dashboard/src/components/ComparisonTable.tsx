import type { BacktestMetrics } from '../types'

const METRIC_ROWS: { key: string; label: string; higherBetter?: boolean; lowerBetter?: boolean }[] = [
  { key: 'Total Return',      label: 'Total Return',         higherBetter: true },
  { key: 'Alpha',             label: 'Alpha vs EGX30',       higherBetter: true },
  { key: 'Win Rate',          label: 'Win Rate',             higherBetter: true },
  { key: 'Sharpe Ratio',      label: 'Sharpe Ratio',         higherBetter: true },
  { key: 'Calmar Ratio',      label: 'Calmar Ratio',         higherBetter: true },
  { key: 'Max Drawdown',      label: 'Max Drawdown',         lowerBetter: true  },  // less negative = better
  { key: 'Total Trades',      label: 'Total Trades' },
  { key: 'Total Commissions', label: 'Commissions Paid' },
  { key: 'Final Portfolio',   label: 'Final Portfolio' },
]

function parseNum(v: string | number | undefined): number | null {
  if (v === undefined || v === null) return null
  if (typeof v === 'number') return v
  const n = parseFloat(String(v).replace(/%|,| EGP/g, ''))
  return isNaN(n) ? null : n
}

type Winner = 'llm' | 'bt' | null

function calcWinner(
  llmVal: string | number | undefined,
  btVal:  string | number | undefined,
  higherBetter?: boolean,
  lowerBetter?: boolean,
): Winner {
  const lf = parseNum(llmVal)
  const bf = parseNum(btVal)
  if (lf === null || bf === null) return null

  if (higherBetter) {
    if (lf > bf) return 'llm'
    if (bf > lf) return 'bt'
  }
  // lowerBetter: for drawdown, -5% > -15% so larger (less negative) is still better
  if (lowerBetter) {
    if (lf > bf) return 'llm'
    if (bf > lf) return 'bt'
  }
  return null
}

interface Props {
  llm: BacktestMetrics
  bt:  BacktestMetrics
}

export default function ComparisonTable({ llm, bt }: Props) {
  let llmWins = 0
  let btWins  = 0

  const rows = METRIC_ROWS.map(({ key, label, higherBetter, lowerBetter }) => {
    const llmVal = llm[key]
    const btVal  = bt[key]
    const winner = calcWinner(llmVal, btVal, higherBetter, lowerBetter)
    if (winner === 'llm') llmWins++
    if (winner === 'bt')  btWins++
    return { label, llmVal, btVal, winner }
  })

  const verdictClass =
    llmWins > btWins ? 'verdict-llm' :
    btWins  > llmWins ? 'verdict-bt' : 'verdict-neutral'

  const verdictText =
    llmWins > btWins
      ? `LLM Multi-Agent outperforms on ${llmWins} of key metrics`
      : btWins > llmWins
        ? `Classical Technical outperforms on ${btWins} of key metrics`
        : 'Results are mixed — no clear overall winner'

  return (
    <div>
      {/* Verdict banner */}
      <div className={`verdict ${verdictClass}`}>
        <span>{verdictText}</span>
        <span style={{ marginLeft: 'auto', opacity: 0.7, fontSize: 12 }}>
          LLM {llmWins} — {btWins} Classical
        </span>
      </div>

      <div className="table-wrap">
        <table className="compare-table">
          <thead>
            <tr>
              <th>Metric</th>
              <th>LLM Multi-Agent</th>
              <th>Classical (Backtrader)</th>
              <th>Winner</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ label, llmVal, btVal, winner }) => (
              <tr key={label}>
                <td style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-sans)' }}>
                  {label}
                </td>
                <td style={{ color: winner === 'llm' ? 'var(--green)' : undefined }}>
                  {llmVal !== undefined ? String(llmVal) : '—'}
                </td>
                <td style={{ color: winner === 'bt' ? 'var(--blue)' : undefined }}>
                  {btVal !== undefined ? String(btVal) : '—'}
                </td>
                <td>
                  {winner === 'llm' && <span className="compare-winner">LLM +</span>}
                  {winner === 'bt'  && <span style={{ color: 'var(--blue)', fontSize: 11, fontWeight: 700 }}>BT +</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
