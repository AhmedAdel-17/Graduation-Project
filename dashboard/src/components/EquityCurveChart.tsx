import { useEffect, useRef } from 'react'
import { createChart, LineSeries, AreaSeries } from 'lightweight-charts'
import type { DailyPortfolio, BenchmarkPoint } from '../types'

interface Props {
  portfolio: DailyPortfolio[]
  benchmark?: BenchmarkPoint[]
  btPortfolio?: DailyPortfolio[]   // Classical strategy equity curve (for comparison)
  height?: number
}

// Compute drawdown series (as % of peak) from portfolio values
function buildDrawdown(data: DailyPortfolio[]) {
  let peak = data[0]?.portfolio_value ?? 0
  return data.map((d) => {
    if (d.portfolio_value > peak) peak = d.portfolio_value
    const dd = peak > 0 ? ((d.portfolio_value - peak) / peak) * 100 : 0
    return { time: d.date as `${number}-${number}-${number}`, value: dd }
  })
}

export default function EquityCurveChart({
  portfolio,
  benchmark,
  btPortfolio,
  height = 340,
}: Props) {
  const equityRef   = useRef<HTMLDivElement>(null)
  const drawdownRef = useRef<HTMLDivElement>(null)

  // ---- Equity Curve Chart ----
  useEffect(() => {
    if (!equityRef.current || !portfolio.length) return

    const chart = createChart(equityRef.current, {
      width:  equityRef.current.clientWidth,
      height,
      layout: {
        background: { color: '#12122a' },
        textColor:  '#7878a8',
      },
      grid: {
        vertLines: { color: '#1a1a38' },
        horzLines: { color: '#1a1a38' },
      },
      rightPriceScale: { borderColor: '#252550' },
      timeScale:       { borderColor: '#252550', timeVisible: true },
      crosshair: { mode: 1 },
    })

    // LLM / primary equity curve
    const llmSeries = chart.addSeries(LineSeries, {
      color:     '#00d4aa',
      lineWidth: 2,
      title:     'LLM Agent',
    })
    llmSeries.setData(
      portfolio.map((d) => ({
        time:  d.date as `${number}-${number}-${number}`,
        value: d.portfolio_value,
      })),
    )

    // Benchmark (EGX30) curve
    if (benchmark?.length) {
      const bmSeries = chart.addSeries(LineSeries, {
        color:     '#505075',
        lineWidth: 1,
        lineStyle: 2, // dashed
        title:     'EGX30',
      })
      bmSeries.setData(
        benchmark.map((d) => ({
          time:  d.date as `${number}-${number}-${number}`,
          value: d.value,
        })),
      )
    }

    // Backtrader classical curve
    if (btPortfolio?.length) {
      const btSeries = chart.addSeries(LineSeries, {
        color:     '#4488ff',
        lineWidth: 2,
        lineStyle: 1, // dotted
        title:     'Classical BT',
      })
      btSeries.setData(
        btPortfolio.map((d) => ({
          time:  d.date as `${number}-${number}-${number}`,
          value: d.portfolio_value,
        })),
      )
    }

    chart.timeScale().fitContent()

    const handleResize = () => {
      if (equityRef.current) {
        chart.applyOptions({ width: equityRef.current.clientWidth })
      }
    }
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      chart.remove()
    }
  }, [portfolio, benchmark, btPortfolio, height])

  // ---- Drawdown Chart ----
  useEffect(() => {
    if (!drawdownRef.current || !portfolio.length) return

    const chart = createChart(drawdownRef.current, {
      width:  drawdownRef.current.clientWidth,
      height: 120,
      layout: {
        background: { color: '#12122a' },
        textColor:  '#7878a8',
      },
      grid: {
        vertLines: { color: '#1a1a38' },
        horzLines: { color: '#1a1a38' },
      },
      rightPriceScale: { borderColor: '#252550' },
      timeScale:       { borderColor: '#252550', timeVisible: true },
    })

    const ddSeries = chart.addSeries(AreaSeries, {
      topColor:    'rgba(255, 82, 82, 0.2)',
      bottomColor: 'rgba(255, 82, 82, 0.0)',
      lineColor:   '#ff5252',
      lineWidth:   1,
      title:       'Drawdown %',
    })
    ddSeries.setData(buildDrawdown(portfolio))

    chart.timeScale().fitContent()

    const handleResize = () => {
      if (drawdownRef.current) {
        chart.applyOptions({ width: drawdownRef.current.clientWidth })
      }
    }
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      chart.remove()
    }
  }, [portfolio])

  if (!portfolio.length) {
    return (
      <div
        style={{
          height,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--text-muted)',
          border: '1px dashed var(--border)',
          borderRadius: 'var(--radius)',
        }}
      >
        No equity curve data available
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {/* Legend */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 8, flexWrap: 'wrap' }}>
        <LegendItem color="#00d4aa" label="LLM Multi-Agent" />
        {benchmark?.length ? <LegendItem color="#505075" label="EGX30 Benchmark" dashed /> : null}
        {btPortfolio?.length ? <LegendItem color="#4488ff" label="Classical (BT)" dotted /> : null}
      </div>

      {/* Equity chart */}
      <div ref={equityRef} className="chart-container" style={{ height }} />

      {/* Drawdown chart */}
      <div style={{ marginTop: 4 }}>
        <div
          style={{
            fontSize: 11,
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: '0.8px',
            color: 'var(--text-secondary)',
            marginBottom: 4,
          }}
        >
          Drawdown
        </div>
        <div ref={drawdownRef} style={{ width: '100%', height: 120 }} />
      </div>
    </div>
  )
}

function LegendItem({
  color,
  label,
  dashed,
  dotted,
}: {
  color: string
  label: string
  dashed?: boolean
  dotted?: boolean
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
      <svg width="20" height="2" style={{ overflow: 'visible' }}>
        <line
          x1="0" y1="1" x2="20" y2="1"
          stroke={color}
          strokeWidth="2"
          strokeDasharray={dashed ? '4 3' : dotted ? '2 2' : undefined}
        />
      </svg>
      <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
    </div>
  )
}
