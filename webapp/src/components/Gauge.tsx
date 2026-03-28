import { useRef, useEffect } from 'react'

interface GaugeProps {
  score: number
  color: string
  size?: number
  lineWidth?: number
}

export default function Gauge({ score, color, size = 140, lineWidth = 10 }: GaugeProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const center = size / 2
    const radius = size / 2 - lineWidth - 4
    const startAngle = 0.75 * Math.PI
    const endAngle = 2.25 * Math.PI

    ctx.clearRect(0, 0, size, size)

    // Background arc
    ctx.beginPath()
    ctx.arc(center, center, radius, startAngle, endAngle)
    ctx.strokeStyle = 'rgba(128,128,128,0.15)'
    ctx.lineWidth = lineWidth
    ctx.lineCap = 'round'
    ctx.stroke()

    // Score arc
    const pct = Math.max(0, Math.min(1, score / 100))
    const scoreEnd = startAngle + (endAngle - startAngle) * pct

    ctx.beginPath()
    ctx.arc(center, center, radius, startAngle, scoreEnd)
    ctx.strokeStyle = color
    ctx.lineWidth = lineWidth
    ctx.lineCap = 'round'
    ctx.stroke()
  }, [score, color, size, lineWidth])

  return (
    <canvas
      ref={canvasRef}
      width={size}
      height={size}
      style={{ width: size, height: size }}
    />
  )
}
