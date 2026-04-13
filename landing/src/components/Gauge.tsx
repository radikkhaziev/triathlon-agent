interface GaugeProps {
  value: number
  size?: number
  lineWidth?: number
}

function colorForValue(v: number): string {
  if (v >= 85) return 'var(--green)'
  if (v >= 70) return 'var(--green)'
  if (v >= 40) return 'var(--yellow)'
  return 'var(--red)'
}

export default function Gauge({ value, size = 140, lineWidth = 10 }: GaugeProps) {
  const center = size / 2
  const radius = size / 2 - lineWidth - 4
  const startAngle = 0.75 * Math.PI
  const endAngle = 2.25 * Math.PI
  const pct = Math.max(0, Math.min(1, value / 100))

  const polar = (angle: number) => ({
    x: center + radius * Math.cos(angle),
    y: center + radius * Math.sin(angle),
  })

  const arcPath = (a0: number, a1: number) => {
    const p0 = polar(a0)
    const p1 = polar(a1)
    const large = a1 - a0 > Math.PI ? 1 : 0
    return `M ${p0.x} ${p0.y} A ${radius} ${radius} 0 ${large} 1 ${p1.x} ${p1.y}`
  }

  const bg = arcPath(startAngle, endAngle)
  const fg = arcPath(startAngle, startAngle + (endAngle - startAngle) * pct)

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`Score ${Math.round(value)}`}>
      <path d={bg} stroke="rgba(128,128,128,0.15)" strokeWidth={lineWidth} strokeLinecap="round" fill="none" />
      <path d={fg} stroke={colorForValue(value)} strokeWidth={lineWidth} strokeLinecap="round" fill="none" />
      <text
        x="50%"
        y="54%"
        textAnchor="middle"
        dominantBaseline="middle"
        fontSize={size * 0.28}
        fontWeight={700}
        fill="var(--text)"
      >
        {Math.round(value)}
      </text>
    </svg>
  )
}
