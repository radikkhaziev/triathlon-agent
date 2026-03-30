import { useState, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { Chart, registerables } from 'chart.js'
import Layout from '../components/Layout'
import TabSwitcher from '../components/TabSwitcher'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import { useApi } from '../hooks/useApi'
import { CHART_COLORS } from '../lib/constants'
import { num } from '../lib/formatters'
import type { ProgressResponse } from '../api/types'

Chart.register(...registerables)

type Sport = 'bike' | 'run' | 'swim'

const SPORT_TABS = [
  { key: 'bike', label: 'Bike' },
  { key: 'run', label: 'Run' },
  { key: 'swim', label: 'Swim' },
]

const DAYS_TABS = [
  { key: '90', label: '90d' },
  { key: '180', label: '180d' },
  { key: '365', label: '1y' },
]

export default function Progress() {
  const [sport, setSport] = useState<Sport>('bike')
  const [days, setDays] = useState('90')

  const endpoint = `/api/progress?sport=${sport}&days=${days}`
  const { data, loading, error } = useApi<ProgressResponse>(endpoint)

  return (
    <Layout title="Progress">
      <TabSwitcher tabs={SPORT_TABS} active={sport} onChange={k => setSport(k as Sport)} />
      <TabSwitcher tabs={DAYS_TABS} active={days} onChange={setDays} />

      {loading && <LoadingSpinner />}
      {error && <ErrorMessage message="Failed to load progress data" />}
      {!loading && !error && data && data.data_points > 0 && (
        <ProgressContent data={data} sport={sport} />
      )}
      {!loading && !error && (!data || data.data_points === 0) && (
        <EmptyState sport={sport} />
      )}
    </Layout>
  )
}

function ProgressContent({ data, sport }: { data: ProgressResponse; sport: Sport }) {
  return (
    <>
      <TrendBadge data={data} sport={sport} />
      {sport === 'swim' ? <SwimCharts data={data} /> : <EFChart data={data} sport={sport} />}
      <ActivityList data={data} sport={sport} />
    </>
  )
}

function TrendBadge({ data, sport }: { data: ProgressResponse; sport: Sport }) {
  const trend = sport === 'swim'
    ? data.metrics?.pace_100m?.trend
    : data.trend

  if (!trend || trend.direction === 'insufficient_data') return null

  const isPositive = sport === 'swim'
    ? trend.direction === 'falling'  // lower pace = better
    : trend.direction === 'rising'   // higher EF = better

  const color = isPositive ? 'var(--green)' : trend.direction === 'stable' ? 'var(--text-dim)' : 'var(--red)'
  const arrow = trend.direction === 'rising' ? '\u2191' : trend.direction === 'falling' ? '\u2193' : '\u2192'
  const label = sport === 'swim' ? 'Pace trend' : 'EF trend'

  return (
    <div className="bg-surface border border-border rounded-[14px] p-4 mb-3 flex items-center justify-between">
      <span className="text-[13px] text-text-dim">{label}</span>
      <span className="text-sm font-bold" style={{ color }}>
        {arrow} {Math.abs(trend.pct).toFixed(1)}%
      </span>
    </div>
  )
}

function EFChart({ data, sport }: { data: ProgressResponse; sport: Sport }) {
  const chartRef = useRef<HTMLCanvasElement>(null)
  const chartInstRef = useRef<Chart | null>(null)

  useEffect(() => {
    if (!chartRef.current || !data.weekly?.length) return

    chartInstRef.current?.destroy()

    const labels = data.weekly.map(w => w.week.replace(/^\d{4}-/, ''))
    const values = data.weekly.map(w => w.ef_mean ?? null)
    const color = sport === 'bike' ? CHART_COLORS.bike : CHART_COLORS.run

    chartInstRef.current = new Chart(chartRef.current, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: `EF (${data.unit || ''})`,
          data: values,
          borderColor: color,
          backgroundColor: color + '20',
          fill: true,
          tension: 0.3,
          pointRadius: 3,
          pointBackgroundColor: color,
          borderWidth: 2,
          spanGaps: true,
        }],
      },
      options: chartOptions(`Efficiency Factor — ${sport === 'bike' ? 'Bike' : 'Run'}`),
    })

    return () => { chartInstRef.current?.destroy() }
  }, [data, sport])

  return (
    <ChartContainer>
      <canvas ref={chartRef} />
    </ChartContainer>
  )
}

function SwimCharts({ data }: { data: ProgressResponse }) {
  const paceRef = useRef<HTMLCanvasElement>(null)
  const swolfRef = useRef<HTMLCanvasElement>(null)
  const chartsRef = useRef<Chart[]>([])

  useEffect(() => {
    if (!data.weekly?.length) return

    chartsRef.current.forEach(c => c.destroy())
    chartsRef.current = []

    const labels = data.weekly.map(w => w.week.replace(/^\d{4}-/, ''))

    if (paceRef.current) {
      const values = data.weekly.map(w => w.pace_mean ?? null)
      chartsRef.current.push(new Chart(paceRef.current, {
        type: 'line',
        data: {
          labels,
          datasets: [{
            label: 'Pace (sec/100m)',
            data: values,
            borderColor: CHART_COLORS.swim,
            backgroundColor: CHART_COLORS.swim + '20',
            fill: true,
            tension: 0.3,
            pointRadius: 3,
            pointBackgroundColor: CHART_COLORS.swim,
            borderWidth: 2,
            spanGaps: true,
          }],
        },
        options: chartOptions('Pace per 100m'),
      }))
    }

    if (swolfRef.current) {
      const values = data.weekly.map(w => w.swolf_mean ?? null)
      const hasSwolf = values.some(v => v !== null)
      if (hasSwolf) {
        chartsRef.current.push(new Chart(swolfRef.current, {
          type: 'line',
          data: {
            labels,
            datasets: [{
              label: 'SWOLF',
              data: values,
              borderColor: '#8b5cf6',
              backgroundColor: '#8b5cf620',
              fill: true,
              tension: 0.3,
              pointRadius: 3,
              pointBackgroundColor: '#8b5cf6',
              borderWidth: 2,
              spanGaps: true,
            }],
          },
          options: chartOptions('SWOLF'),
        }))
      }
    }

    return () => { chartsRef.current.forEach(c => c.destroy()) }
  }, [data])

  const hasSwolf = data.weekly?.some(w => w.swolf_mean != null) ?? false

  return (
    <>
      <ChartContainer><canvas ref={paceRef} /></ChartContainer>
      {hasSwolf && <ChartContainer><canvas ref={swolfRef} /></ChartContainer>}
    </>
  )
}

function ActivityList({ data, sport }: { data: ProgressResponse; sport: Sport }) {
  const activities = [...data.activities].reverse().slice(0, 10)

  return (
    <div className="bg-surface border border-border rounded-[14px] p-4 mb-3">
      <div className="text-[15px] font-bold mb-3">Recent Sessions</div>
      {activities.map(act => (
        <Link
          key={act.id}
          to={`/activity/${act.id}`}
          className="flex justify-between items-center py-1.5 border-b border-border last:border-b-0 no-underline text-text"
        >
          <div>
            <span className="text-[13px] text-text-dim">{act.date}</span>
            <span className="text-[12px] text-text-dim ml-2">{act.duration_min}min</span>
          </div>
          <div className="text-sm font-semibold">
            {sport === 'swim' ? (
              <>
                {act.pace_100m != null && <span>{num(act.pace_100m, 0)}s/100m</span>}
                {act.swolf != null && <span className="text-text-dim text-[12px] ml-2">SWOLF {num(act.swolf, 0)}</span>}
              </>
            ) : (
              <>
                {act.ef != null && <span>{num(act.ef, 4)}</span>}
                {act.decoupling != null && (
                  <span className="text-text-dim text-[12px] ml-2">{num(act.decoupling, 1)}% dec</span>
                )}
              </>
            )}
          </div>
        </Link>
      ))}
    </div>
  )
}

function EmptyState({ sport }: { sport: Sport }) {
  const sportLabel = sport === 'bike' ? 'cycling' : sport === 'run' ? 'running' : 'swimming'
  return (
    <div className="text-center py-12 text-text-dim">
      <div className="text-3xl mb-3">{sport === 'bike' ? '\u{1F6B4}' : sport === 'run' ? '\u{1F3C3}' : '\u{1F3CA}'}</div>
      <div className="text-sm">No Z2 {sportLabel} sessions found in this period</div>
      <div className="text-[12px] mt-1">Complete steady-state workouts to track progress</div>
    </div>
  )
}

function ChartContainer({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-surface border border-border rounded-[14px] p-3 mb-3">
      <div style={{ maxHeight: 250 }}>{children}</div>
    </div>
  )
}

function chartOptions(title: string): Record<string, unknown> {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { position: 'top', labels: { boxWidth: 12, padding: 8, font: { size: 11 } } },
      title: { display: true, text: title, font: { size: 13 } },
    },
    scales: {
      x: { grid: { color: 'rgba(128,128,128,0.15)' }, ticks: { font: { size: 10 }, maxRotation: 45 } },
      y: { grid: { color: 'rgba(128,128,128,0.15)' }, ticks: { font: { size: 10 } } },
    },
  }
}
