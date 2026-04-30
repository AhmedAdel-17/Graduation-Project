import type { TradeRecord } from '../types'

interface Props {
  trades: TradeRecord[]
}

function fmt(n: number | undefined, decimals = 2) {
  if (n === undefined || n === null) return '—'
  return n.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
}

export default function TradeLogTable({ trades }: Props) {
  if (!trades.length) {
    return (
      <div className="empty-state" style={{ padding: '30px 20px' }}>
        <span style={{ color: 'var(--text-muted)' }}>No trades executed in this backtest</span>
      </div>
    )
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th>Action</th>
            <th>Shares</th>
            <th>Close Price</th>
            <th>Exec Price</th>
            <th>Value (EGP)</th>
            <th>Commission</th>
            <th>Realized PnL</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => {
            // Normalize fields between LLM engine and BT engine
            const execPrice  = t.exec_price ?? t.price ?? 0
            const closePrice = t.close_price ?? t.price ?? execPrice
            const commission = t.commission ?? t.comm ?? 0
            const pnl        = t.realized_pnl ?? t.pnl ?? 0
            const value      = t.value ?? (t.shares * execPrice)

            const isBuy = t.action === 'BUY'
            const pnlClass = pnl > 0 ? 'cell-pos' : pnl < 0 ? 'cell-neg' : ''

            return (
              <tr key={i}>
                <td>{t.date}</td>
                <td className={isBuy ? 'cell-buy' : 'cell-sell'}>{t.action}</td>
                <td>{t.shares.toLocaleString()}</td>
                <td>{fmt(closePrice)}</td>
                <td>{fmt(execPrice)}</td>
                <td>{fmt(value)}</td>
                <td style={{ color: 'var(--text-secondary)' }}>{fmt(commission)}</td>
                <td className={pnlClass}>{isBuy ? '—' : fmt(pnl)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
