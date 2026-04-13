interface SparklineProps {
  values: number[]
  width?: number
  height?: number
  color?: string
  label?: string
  mode?: 'line' | 'dots'
}

export default function Sparkline({
  values,
  width = 140,
  height = 40,
  color = 'var(--accent)',
  label,
  mode = 'line',
}: SparklineProps) {
  if (values.length < 2) return null

  const pad = 3
  const w = width - pad * 2
  const h = height - pad * 2
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const stepX = w / (values.length - 1)

  const points = values.map((v, i) => ({
    x: pad + i * stepX,
    y: pad + h - ((v - min) / range) * h,
  }))

  const pathD = points
    .map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`)
    .join(' ')

  return (
    <div className="flex items-center gap-2">
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        aria-hidden="true"
      >
        {mode === 'line' && (
          <path d={pathD} fill="none" stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        )}
        {mode === 'dots' &&
          points.map((p, i) => <circle key={i} cx={p.x} cy={p.y} r="1.6" fill={color} />)}
      </svg>
      {label && <span className="text-[11px] font-semibold text-text-dim whitespace-nowrap">{label}</span>}
    </div>
  )
}
