const NODES = [
  { id: 'intervals', label: 'Intervals.icu', icon: '📡' },
  { id: 'db', label: 'PostgreSQL', icon: '🗄️' },
  { id: 'engine', label: 'Recovery / HRV Engine', icon: '⚙️' },
  { id: 'claude', label: 'Claude AI', icon: '🤖' },
  { id: 'telegram', label: 'Telegram Bot', icon: '💬' },
  { id: 'athlete', label: 'Athlete', icon: '🏃' },
]

export default function ArchitectureDiagram() {
  return (
    <>
      {/* Desktop: horizontal SVG flow */}
      <svg
        viewBox="0 0 760 200"
        className="hidden md:block w-full h-auto"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
      >
        {/* Animated arrows */}
        <defs>
          <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="var(--accent)" />
          </marker>
        </defs>

        {/* Row 1: Intervals → DB → Engine */}
        <Node x={20} y={20} w={140} label="Intervals.icu" icon="📡" />
        <Arrow x1={160} y1={45} x2={230} y2={45} delay="0s" />
        <Node x={230} y={20} w={140} label="PostgreSQL" icon="🗄️" />
        <Arrow x1={370} y1={45} x2={440} y2={45} delay="0.4s" />
        <Node x={440} y={20} w={200} label="Recovery / HRV Engine" icon="⚙️" />

        {/* Split from Engine */}
        <Arrow x1={540} y1={70} x2={540} y2={115} delay="0.8s" />
        <Node x={440} y={115} w={200} label="Claude AI" icon="🤖" />
        <Arrow x1={540} y1={165} x2={540} y2={200} delay="1.2s" vertical />

        {/* Row 3: Telegram → Athlete */}
        <Node x={350} y={150} w={180} label="Telegram Bot" icon="💬" />
        <Arrow x1={350} y1={180} x2={280} y2={180} delay="1.6s" />
        <Node x={140} y={150} w={140} label="Athlete" icon="🏃" />

        <style>{`
          @keyframes arrow-dash { to { stroke-dashoffset: 0; } }
        `}</style>
      </svg>

      {/* Mobile: vertical stack of cards with simple arrows */}
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
  const h = 50
  return (
    <g>
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        rx="10"
        fill="var(--surface)"
        stroke="var(--border)"
        strokeWidth="1"
      />
      <text
        x={x + 14}
        y={y + h / 2 + 5}
        fontSize="16"
        fontFamily="Inter, sans-serif"
      >
        {icon}
      </text>
      <text
        x={x + 38}
        y={y + h / 2 + 5}
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
  vertical?: boolean
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
