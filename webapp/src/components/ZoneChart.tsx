import { useRef, useEffect } from 'react'
import { Chart, registerables } from 'chart.js'
import { ZONE_COLORS, ZONE_LABELS } from '../lib/constants'

Chart.register(...registerables)

interface ZoneChartProps {
  zones: number[]
  label: string
}

export default function ZoneChart({ zones, label }: ZoneChartProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const chartRef = useRef<Chart | null>(null)

  const total = zones.reduce((a, b) => a + (b || 0), 0)

  useEffect(() => {
    if (!canvasRef.current || total <= 0) return

    if (chartRef.current) {
      chartRef.current.destroy()
    }

    const data: number[] = []
    const colors: string[] = []
    const labels: string[] = []
    for (let i = 0; i < zones.length && i < 5; i++) {
      const v = zones[i] || 0
      data.push(v / 60)
      colors.push(ZONE_COLORS[i])
      const pct = Math.round((v / total) * 100)
      const mins = Math.round(v / 60)
      labels.push(`${ZONE_LABELS[i]}: ${mins}m (${pct}%)`)
    }

    chartRef.current = new Chart(canvasRef.current, {
      type: 'bar',
      data: {
        labels: [label],
        datasets: data.map((v, i) => ({
          label: labels[i],
          data: [v],
          backgroundColor: colors[i],
          borderRadius: 0,
          barPercentage: 1.0,
          categoryPercentage: 1.0,
        })),
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { stacked: true, display: false },
          y: { stacked: true, display: false },
        },
        plugins: {
          legend: {
            position: 'bottom',
            labels: { color: '#8888a0', font: { size: 10, family: 'Inter' }, boxWidth: 10, padding: 8 },
          },
          tooltip: { enabled: false },
        },
      },
    })

    return () => {
      chartRef.current?.destroy()
      chartRef.current = null
    }
  }, [zones, label, total])

  if (total <= 0) return null

  return (
    <div className="bg-surface border border-border rounded-xl p-3.5 mb-2.5">
      <canvas ref={canvasRef} height={50} />
    </div>
  )
}
