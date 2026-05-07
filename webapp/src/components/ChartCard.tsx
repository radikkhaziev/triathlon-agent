import React from 'react'

export function ChartCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-surface border border-border rounded-[14px] p-3 mb-3">
      <div style={{ height: 280 }}>{children}</div>
    </div>
  )
}

export function chartOptions(title: string): Record<string, unknown> {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { position: 'top', labels: { boxWidth: 12, padding: 10, font: { size: 13 } } },
      title: { display: true, text: title, font: { size: 14, weight: 'bold' } },
    },
    scales: {
      x: { grid: { color: 'rgba(128,128,128,0.2)' }, ticks: { font: { size: 12 }, maxRotation: 45, autoSkip: true, maxTicksLimit: 10 } },
      y: { grid: { color: 'rgba(128,128,128,0.2)' }, ticks: { font: { size: 12 } } },
    },
  }
}
