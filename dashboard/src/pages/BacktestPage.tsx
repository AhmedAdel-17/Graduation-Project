import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { RefreshCw, Play, ChevronDown } from 'lucide-react'

import {
  fetchSessions,
  fetchDetail,
  fetchComparison,
  runLLMBacktest,
  runBTBacktest,
} from '../api'
import type { BacktestSession } from '../types'

import MetricsGrid      from '../components/MetricsGrid'
import EquityCurveChart from '../components/EquityCurveChart'
import TradeLogTable    from '../components/TradeLogTable'
import ComparisonTable  from '../components/ComparisonTable'

type Tab = 'equity' | 'trades' | 'compare'

const DEFAULT_FORM = {
  ticker:          'COMI.CA',
  start_date:      '2023-10-01',
  end_date:        '2024-01-01',
  initial_capital: '1000000',
}

export default function BacktestPage() {
  const [selectedId, setSelectedId]     = useState<string | null>(null)
  const [activeTab, setActiveTab]       = useState<Tab>('equity')
  const [showForm, setShowForm]         = useState(false)
  const [form, setForm]                 = useState(DEFAULT_FORM)
  const [runStatus, setRunStatus]       = useState<string | null>(null)

  // ---- Data queries ----
  const { data: sessionsData, isLoading: sessionsLoading, refetch: refetchSessions } =
    useQuery({ queryKey: ['backtests'], queryFn: fetchSessions })

  const sessions = sessionsData?.sessions ?? []

  const selectedSession = sessions.find((s) => s.session_id === selectedId) ?? null

  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ['backtest-detail', selectedId],
    queryFn:  () => fetchDetail(selectedId!),
    enabled:  !!selectedId,
  })

  const ticker = selectedSession?.ticker ?? null

  const { data: comparison } = useQuery({
    queryKey: ['comparison', ticker],
    queryFn:  () => fetchComparison(ticker!),
    enabled:  !!ticker,
    retry:    false,          // don't retry 404s
  })

  // ---- Handlers ----
  function handleFormChange(key: string, value: string) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  async function handleRunLLM() {
    setRunStatus('Starting LLM backtest...')
    try {
      await runLLMBacktest({
        ticker:          form.ticker,
        start_date:      form.start_date,
        end_date:        form.end_date,
        initial_capital: parseFloat(form.initial_capital),
        selected_analysts: ['market'],
      })
      setRunStatus('LLM backtest started in background. Refresh after it completes.')
    } catch (e) {
      setRunStatus(`Error: ${(e as Error).message}`)
    }
  }

  async function handleRunBT() {
    setRunStatus('Starting Backtrader benchmark...')
    try {
      await runBTBacktest({
        ticker:          form.ticker,
        start_date:      form.start_date,
        end_date:        form.end_date,
        initial_capital: parseFloat(form.initial_capital),
      })
      setRunStatus('Backtrader benchmark started in background. Refresh after it completes.')
    } catch (e) {
      setRunStatus(`Error: ${(e as Error).message}`)
    }
  }

  // ---- Derived data for charts ----
  const portfolio        = detail?.daily_portfolio     ?? []
  const benchmarkHistory = detail?.benchmark_history   ?? []
  const trades           = detail?.trades              ?? []

  // Pull the BT equity curve from comparison data when viewing an LLM session
  const btPortfolio =
    comparison?.bt?.daily_portfolio ??
    (selectedSession?.engine === 'classical_technical' ? [] : undefined)

  // Has both LLM + BT results for this ticker
  const hasComparison = !!(comparison?.llm && comparison?.bt)

  // ---- Render ----
  return (
    <div>
      {/* Page header */}
      <div className="page-header">
        <div>
          <h1 className="page-title">Backtesting Results</h1>
          <p className="page-subtitle">
            LLM Multi-Agent vs Classical Technical Strategy
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className="btn btn-ghost"
            onClick={() => refetchSessions()}
            title="Refresh session list"
          >
            <RefreshCw size={14} />
            Refresh
          </button>
          <button
            className="btn btn-primary"
            onClick={() => setShowForm((v) => !v)}
          >
            <Play size={14} />
            Run Backtest
            <ChevronDown size={12} style={{ transform: showForm ? 'rotate(180deg)' : undefined, transition: '0.2s' }} />
          </button>
        </div>
      </div>

      {/* Run form */}
      {showForm && (
        <div className="section">
          <div className="run-form">
            <div>
              <label className="label">Ticker</label>
              <input
                className="input"
                style={{ width: '100%' }}
                value={form.ticker}
                onChange={(e) => handleFormChange('ticker', e.target.value)}
                placeholder="COMI.CA"
              />
            </div>
            <div>
              <label className="label">Start Date</label>
              <input
                className="input"
                style={{ width: '100%' }}
                type="date"
                value={form.start_date}
                onChange={(e) => handleFormChange('start_date', e.target.value)}
              />
            </div>
            <div>
              <label className="label">End Date</label>
              <input
                className="input"
                style={{ width: '100%' }}
                type="date"
                value={form.end_date}
                onChange={(e) => handleFormChange('end_date', e.target.value)}
              />
            </div>
            <div>
              <label className="label">Capital (EGP)</label>
              <input
                className="input"
                style={{ width: '100%' }}
                value={form.initial_capital}
                onChange={(e) => handleFormChange('initial_capital', e.target.value)}
                placeholder="1000000"
              />
            </div>
            <div className="form-actions">
              <button className="btn btn-primary" onClick={handleRunLLM}>
                <Play size={13} /> LLM Run
              </button>
              <button className="btn btn-secondary" onClick={handleRunBT}>
                <Play size={13} /> Classical Run
              </button>
            </div>
          </div>
          {runStatus && (
            <p style={{ marginTop: 10, fontSize: 12, color: 'var(--text-secondary)' }}>
              {runStatus}
            </p>
          )}
        </div>
      )}

      {/* Session selector */}
      <div className="section card">
        <div className="card-header">
          <span className="card-title">Session</span>
          {selectedSession && (
            <EngineBadge engine={selectedSession.engine} />
          )}
        </div>
        {sessionsLoading ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-secondary)' }}>
            <div className="spinner" />
            Loading sessions...
          </div>
        ) : sessions.length === 0 ? (
          <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>
            No backtest results yet. Run a backtest above to get started.
          </p>
        ) : (
          <select
            className="select"
            style={{ width: '100%' }}
            value={selectedId ?? ''}
            onChange={(e) => {
              setSelectedId(e.target.value || null)
              setActiveTab('equity')
            }}
          >
            <option value="">— Select a session —</option>
            {sessions.map((s) => (
              <option key={s.session_id} value={s.session_id}>
                [{s.engine === 'llm_multi_agent' ? 'LLM' : 'BT'}] {s.ticker} — {s.session_id.slice(-15)} — {s.metrics?.['Total Return'] ?? '?'}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Main content — only shown when a session is selected */}
      {selectedId && (
        detailLoading ? (
          <div className="empty-state">
            <div className="spinner" />
            <span>Loading backtest data...</span>
          </div>
        ) : detail ? (
          <>
            {/* Metrics */}
            <div className="section">
              <div style={{ marginBottom: 12 }} className="card-title">Performance Metrics</div>
              <MetricsGrid metrics={detail.metrics} />
            </div>

            {/* Tabs */}
            <div className="tabs">
              <button className={`tab${activeTab === 'equity'  ? ' active' : ''}`} onClick={() => setActiveTab('equity')}>
                Equity Curve
              </button>
              <button className={`tab${activeTab === 'trades'  ? ' active' : ''}`} onClick={() => setActiveTab('trades')}>
                Trade Log ({trades.length})
              </button>
              {hasComparison && (
                <button className={`tab${activeTab === 'compare' ? ' active' : ''}`} onClick={() => setActiveTab('compare')}>
                  LLM vs Classical
                </button>
              )}
            </div>

            {/* Tab panels */}
            {activeTab === 'equity' && (
              <div className="section card">
                <EquityCurveChart
                  portfolio={portfolio}
                  benchmark={benchmarkHistory}
                  btPortfolio={btPortfolio}
                />
              </div>
            )}

            {activeTab === 'trades' && (
              <div className="section card">
                <div className="card-header">
                  <span className="card-title">Trade Log</span>
                  <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                    {trades.length} trades
                  </span>
                </div>
                <TradeLogTable trades={trades} />
              </div>
            )}

            {activeTab === 'compare' && hasComparison && (
              <div className="section card">
                <div className="card-header">
                  <span className="card-title">LLM Multi-Agent vs Classical Technical</span>
                  <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                    {comparison!.ticker}
                  </span>
                </div>
                <ComparisonTable
                  llm={comparison!.llm!.metrics}
                  bt={comparison!.bt!.metrics}
                />

                {/* Dual equity curve */}
                <div style={{ marginTop: 20 }}>
                  <div className="card-title" style={{ marginBottom: 12 }}>
                    Equity Curve — Side by Side
                  </div>
                  <EquityCurveChart
                    portfolio={comparison!.llm!.daily_portfolio}
                    benchmark={comparison!.llm!.benchmark_history}
                    btPortfolio={comparison!.bt!.daily_portfolio}
                  />
                </div>
              </div>
            )}
          </>
        ) : (
          <div className="empty-state">
            <span style={{ color: 'var(--text-muted)' }}>Failed to load session data.</span>
          </div>
        )
      )}
    </div>
  )
}

function EngineBadge({ engine }: { engine: BacktestSession['engine'] }) {
  if (engine === 'llm_multi_agent') {
    return <span className="badge badge-green">LLM Multi-Agent</span>
  }
  return <span className="badge badge-blue">Classical Technical</span>
}
