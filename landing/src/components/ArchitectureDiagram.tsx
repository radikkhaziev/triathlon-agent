interface DiagramNode {
  id: string
  label: string
  icon: string
  x: number
  w: number
}

const NODES: DiagramNode[] = [
  { id: 'engine', label: 'Recovery / HRV Engine', icon: '⚙️', x: 20, w: 190 },
  { id: 'claude', label: 'Claude AI', icon: '🤖', x: 250, w: 130 },
  { id: 'telegram', label: 'Telegram Bot', icon: '💬', x: 420, w: 160 },
  { id: 'athlete', label: 'Athlete', icon: '🏃', x: 620, w: 120 },
]

const VIEW_W = 760
const VIEW_H = 90
const NODE_H = 50
const NODE_Y = 20
const CENTER_Y = NODE_Y + NODE_H / 2

export default function ArchitectureDiagram() {
  return (
    <>
      {/* Desktop: horizontal SVG flow */}
      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        className="hidden md:block w-full h-auto"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
      >
        <defs>
          <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="var(--accent)" />
          </marker>
        </defs>

        {NODES.map((n) => (
          <Node key={n.id} x={n.x} y={NODE_Y} w={n.w} label={n.label} icon={n.icon} />
        ))}

        {NODES.slice(0, -1).map((n, i) => {
          const next = NODES[i + 1]
          return (
            <Arrow
              key={`${n.id}->${next.id}`}
              x1={n.x + n.w}
              y1={CENTER_Y}
              x2={next.x}
              y2={CENTER_Y}
              delay={`${i * 0.4}s`}
            />
          )
        })}

        <style>{`
          @keyframes arrow-dash { to { stroke-dashoffset: 0; } }
        `}</style>
      </svg>

      {/* Mobile: vertical stack */}
      <div className="md:hidden space-y-2">
        {NODES.map((n, i) => (
          <div key={n.id}>
            <div className="bg-surface border border-border rounded-lg px-4 py-2.5 flex items-center gap-3">
              <span className="text-lg">{n.icon}</span>
              <span className="text-sm font-semibold">{n.label}</span>
            </div>
            {i < NODES.length - 1 && <div className="text-center text-text-dim text-xs py-1">↓</div>}
          </div>
        ))}
      </div>
    </>
  )
}

interface NodeProps {
  x: number
  y: number
  w: number
  label: string
  icon: string
}

function Node({ x, y, w, label, icon }: NodeProps) {
  return (
    <g>
      <rect
        x={x}
        y={y}
        width={w}
        height={NODE_H}
        rx="10"
        fill="var(--surface)"
        stroke="var(--border)"
        strokeWidth="1"
      />
      <text x={x + 14} y={y + NODE_H / 2 + 5} fontSize="16" fontFamily="Inter, sans-serif">
        {icon}
      </text>
      <text
        x={x + 38}
        y={y + NODE_H / 2 + 5}
        fontSize="12"
        fontWeight="600"
        fill="var(--text)"
        fontFamily="Inter, sans-serif"
      >
        {label}
      </text>
    </g>
  )
}

interface ArrowProps {
  x1: number
  y1: number
  x2: number
  y2: number
  delay: string
}

function Arrow({ x1, y1, x2, y2, delay }: ArrowProps) {
  const length = Math.hypot(x2 - x1, y2 - y1)
  return (
    <line
      x1={x1}
      y1={y1}
      x2={x2}
      y2={y2}
      stroke="var(--accent)"
      strokeWidth="2"
      strokeLinecap="round"
      markerEnd="url(#arrow)"
      strokeDasharray={length}
      strokeDashoffset={length}
      style={{ animation: `arrow-dash 0.6s ease-out ${delay} forwards` }}
    />
  )
}
