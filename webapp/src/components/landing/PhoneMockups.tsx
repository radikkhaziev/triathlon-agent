// ── Endurai landing · phone mockups (recreated Halo app screens) ────────
// Light app UI, fixed. The landing wraps these in a device frame.
// Ported 1:1 from the Claude Design handoff (landing/landing-mockups.jsx).
// Text inside the phone is EN-only by design decision (it depicts the app).
import type { CSSProperties, ReactNode } from 'react'
import { BRAND } from './landingContent'

const mkFont = BRAND.sans

interface PhoneFrameProps {
  children: ReactNode
  tone?: string
  scale?: number
  shadow?: boolean
}

// Generic rounded-rect device frame. `tone` = bezel color.
export function PhoneFrame({ children, tone = '#0a0d18', scale = 1, shadow = true }: PhoneFrameProps) {
  return (
    <div style={{
      width: 300 * scale, height: 620 * scale, borderRadius: 46 * scale,
      background: tone, padding: 11 * scale, position: 'relative', flexShrink: 0,
      boxShadow: shadow ? '0 40px 90px -30px rgba(10,13,24,0.55), 0 8px 24px -12px rgba(10,13,24,0.4)' : 'none',
    }}>
      <div style={{
        width: '100%', height: '100%', borderRadius: 36 * scale, overflow: 'hidden',
        background: BRAND.cool, position: 'relative', fontFamily: mkFont,
      }}>
        {/* notch */}
        <div style={{
          position: 'absolute', top: 8 * scale, left: '50%', transform: 'translateX(-50%)',
          width: 92 * scale, height: 22 * scale, borderRadius: 12 * scale, background: tone, zIndex: 5,
        }}/>
        <div style={{ width: '100%', height: '100%', transform: `scale(${scale})`, transformOrigin: 'top left' }}>
          <div style={{ width: 300, height: 620 }}>{children}</div>
        </div>
      </div>
    </div>
  )
}

function StatusBar() {
  return (
    <div style={{ height: 40, display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between',
      padding: '0 22px 4px', fontSize: 12, fontWeight: 600, color: BRAND.ink }}>
      <span>7:24</span>
      <div style={{ display: 'flex', gap: 5, alignItems: 'center', opacity: 0.85 }}>
        <span style={{ fontSize: 10 }}>▮▮▮</span>
        <span style={{ fontSize: 10 }}>᎒</span>
        <span style={{ display: 'inline-block', width: 18, height: 9, border: `1.4px solid ${BRAND.ink}`, borderRadius: 2, position: 'relative' }}>
          <span style={{ position: 'absolute', inset: 1.5, right: 4, background: BRAND.green, borderRadius: 1 }}/>
        </span>
      </div>
    </div>
  )
}

function TabBar({ active = 0 }: { active?: number }) {
  const tabs = ['Today', 'Plan', 'Log', 'Trends', 'You']
  return (
    <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, height: 58,
      background: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(8px)',
      borderTop: `1px solid ${BRAND.border}`, display: 'flex', alignItems: 'center',
      justifyContent: 'space-around', padding: '0 6px' }}>
      {tabs.map((tName, i) => (
        <div key={tName} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
          <span style={{ width: 18, height: 18, borderRadius: 9,
            background: i === active ? BRAND.cobalt : 'transparent',
            border: i === active ? 'none' : `1.6px solid ${BRAND.dimmer}` }}/>
          <span style={{ fontSize: 8.5, fontWeight: 600, letterSpacing: 0.2,
            color: i === active ? BRAND.ink : BRAND.dimmer }}>{tName}</span>
        </div>
      ))}
    </div>
  )
}

// Recovery arc gauge — 270° sweep, value 0..100.
function Arc({ score = 65, size = 150 }: { score?: number; size?: number }) {
  const r = size / 2 - 12, cx = size / 2, cy = size / 2
  const start = 135, sweep = 270
  const pol = (a: number): [number, number] => {
    const rad = (a - 90) * Math.PI / 180
    return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)]
  }
  const arcPath = (a0: number, a1: number) => {
    const [x0, y0] = pol(a0), [x1, y1] = pol(a1)
    const large = (a1 - a0) > 180 ? 1 : 0
    return `M ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1}`
  }
  const end = start + sweep * (score / 100)
  const col = score >= 70 ? BRAND.green : score >= 40 ? BRAND.amber : BRAND.coral
  return (
    <svg width={size} height={size} style={{ display: 'block' }}>
      <path d={arcPath(start, start + sweep)} fill="none" stroke="#e7eaf1" strokeWidth="12" strokeLinecap="round" />
      <path d={arcPath(start, end)} fill="none" stroke={col} strokeWidth="12" strokeLinecap="round" />
      {[40, 70, 85].map((tick) => {
        const [tx, ty] = pol(start + sweep * (tick / 100))
        return <circle key={tick} cx={tx} cy={ty} r="2.4" fill="#fff" stroke={BRAND.dimmer} strokeWidth="1" />
      })}
      <text x={cx} y={cy - 2} textAnchor="middle" fontFamily={mkFont} fontWeight="800"
        fontSize="46" fill={BRAND.ink} letterSpacing="-1">{score}</text>
      <text x={cx} y={cy + 20} textAnchor="middle" fontFamily={mkFont} fontWeight="600"
        fontSize="11" fill={BRAND.dim} letterSpacing="1">/ 100</text>
    </svg>
  )
}

const mkCard: CSSProperties = {
  background: BRAND.surface, border: `1px solid ${BRAND.border}`, borderRadius: 16, padding: 12,
}
const mkMicro: CSSProperties = {
  fontSize: 10, fontWeight: 700, letterSpacing: 0.6, color: BRAND.dim, textTransform: 'uppercase',
}

type Status = 'green' | 'amber' | 'red'

function Chip({ label, value, unit, status }: { label: string; value: string; unit: string; status: Status }) {
  const bg = status === 'green' ? BRAND.greenWash : status === 'amber' ? '#f5e6c8' : '#fde6e6'
  const fg = status === 'green' ? BRAND.green : status === 'amber' ? '#92400e' : '#991b1b'
  return (
    <div style={{ ...mkCard, flex: 1, padding: 10 }}>
      <div style={mkMicro}>{label}</div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 4, marginTop: 6 }}>
        <span style={{ fontSize: 19, fontWeight: 800, color: BRAND.ink, letterSpacing: -0.4, whiteSpace: 'nowrap' }}>{value}</span>
        <span style={{ fontSize: 10, fontWeight: 600, color: BRAND.dim }}>{unit}</span>
      </div>
      <div style={{ marginTop: 7, display: 'inline-flex', alignItems: 'center', gap: 4,
        background: bg, color: fg, fontSize: 9, fontWeight: 700, padding: '2px 6px', borderRadius: 999 }}>
        <span style={{ width: 5, height: 5, borderRadius: 3, background: fg }}/>
        {status === 'green' ? 'Good' : status === 'amber' ? 'Watch' : 'Low'}
      </div>
    </div>
  )
}

function RecoveryScreen({ lead }: { lead?: string }) {
  return (
    <div style={{ height: '100%', position: 'relative', color: BRAND.ink, background: BRAND.cool }}>
      <StatusBar />
      <div style={{ padding: '4px 16px 70px', overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
          <div>
            <div style={{ fontSize: 18, fontWeight: 800, letterSpacing: -0.4 }}>Today</div>
            <div style={{ fontSize: 11, color: BRAND.dim }}>Thu · 4 Jun</div>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <span style={{ width: 28, height: 28, borderRadius: 14, border: `1px solid ${BRAND.border}`, background: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, color: BRAND.dim }}>‹</span>
            <span style={{ width: 28, height: 28, borderRadius: 14, border: `1px solid ${BRAND.border}`, background: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, color: BRAND.dimmer }}>›</span>
          </div>
        </div>

        <div style={{ ...mkCard, padding: 14, display: 'flex', flexDirection: 'column', alignItems: 'center', marginTop: 8 }}>
          <Arc score={72} size={150} />
          <div style={{ marginTop: 2, display: 'inline-flex', alignItems: 'center', gap: 6,
            background: BRAND.greenWash, color: BRAND.green, fontSize: 10, fontWeight: 700,
            padding: '4px 10px', borderRadius: 999 }}>
            <span style={{ width: 6, height: 6, borderRadius: 3, background: BRAND.green }}/>
            READY · cleared to push
          </div>
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
          <Chip label="HRV" value="78" unit="ms" status="green" />
          <Chip label="RHR" value="48" unit="bpm" status="green" />
        </div>

        <div style={{ ...mkCard, marginTop: 10, background: BRAND.cobaltLite, border: 'none' }}>
          <div style={{ fontSize: 9.5, fontWeight: 800, letterSpacing: 0.6, color: BRAND.cobaltDark, textTransform: 'uppercase' }}>AI · today</div>
          <div style={{ fontSize: 11.5, lineHeight: 1.45, color: '#1a2440', marginTop: 6, textWrap: 'pretty' }}>
            {lead || 'Solid HRV trend. Aerobic volume today, no max efforts. Recovery: get some daylight.'}
          </div>
        </div>
      </div>
      <TabBar active={0} />
    </div>
  )
}

interface PlanRowData {
  dot: string
  name: string
  type: string
  dur: string
  today?: boolean
}

function PlanRow({ dot, name, type, dur, today }: PlanRowData) {
  return (
    <div style={{ ...mkCard, padding: '10px 12px', display: 'flex', alignItems: 'center', gap: 10,
      borderColor: today ? BRAND.cobalt : BRAND.border, borderWidth: today ? 1.5 : 1 }}>
      <span style={{ width: 8, height: 8, borderRadius: 4, background: dot, flexShrink: 0 }}/>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12.5, fontWeight: 700, color: BRAND.ink, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{name}</div>
        <div style={{ fontSize: 10, color: BRAND.dim }}>{type}</div>
      </div>
      <div style={{ fontSize: 11, fontWeight: 600, color: BRAND.dim }}>{dur}</div>
    </div>
  )
}

function PlanScreen() {
  const days: { wd: string; rows: PlanRowData[] }[] = [
    { wd: 'MON', rows: [{ dot: BRAND.run, name: 'Recovery Run', type: 'Run · Z2', dur: '40m' }] },
    { wd: 'TUE', rows: [{ dot: BRAND.ride, name: 'Tempo Intervals', type: 'Ride · 3×8m', dur: '1h10', today: true }] },
    { wd: 'WED', rows: [{ dot: BRAND.swim, name: 'Endurance Swim', type: 'Swim · technique', dur: '45m' }] },
    { wd: 'THU', rows: [{ dot: BRAND.ride, name: 'Long Ride', type: 'Ride · Z2', dur: '2h30' }] },
  ]
  return (
    <div style={{ height: '100%', position: 'relative', color: BRAND.ink, background: BRAND.cool }}>
      <StatusBar />
      <div style={{ padding: '4px 16px 70px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
          <div style={{ fontSize: 18, fontWeight: 800, letterSpacing: -0.4 }}>This week</div>
          <div style={{ fontSize: 11, fontWeight: 700, color: BRAND.cobaltDark }}>TSS 412</div>
        </div>
        <div style={{ fontSize: 11, color: BRAND.dim, marginBottom: 10 }}>2–8 Jun · build block</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {days.map((d) => (
            <div key={d.wd}>
              <div style={{ ...mkMicro, marginBottom: 6, color: d.rows[0].today ? BRAND.cobaltDark : BRAND.dim }}>
                {d.wd}{d.rows[0].today ? ' · today' : ''}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {d.rows.map((r, i) => <PlanRow key={i} {...r} />)}
              </div>
            </div>
          ))}
        </div>
      </div>
      <TabBar active={1} />
    </div>
  )
}

function ZoneBar() {
  const zones = [
    { z: 'Z1', pct: 14, c: '#9bd0a8' },
    { z: 'Z2', pct: 41, c: BRAND.green },
    { z: 'Z3', pct: 22, c: BRAND.amber },
    { z: 'Z4', pct: 16, c: '#e0732e' },
    { z: 'Z5', pct: 7, c: BRAND.coral },
  ]
  return (
    <div>
      <div style={{ display: 'flex', height: 14, borderRadius: 7, overflow: 'hidden' }}>
        {zones.map((z) => <div key={z.z} style={{ width: `${z.pct}%`, background: z.c }}/>)}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 7 }}>
        {zones.map((z) => (
          <div key={z.z} style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 8.5, fontWeight: 700, color: BRAND.dim }}>{z.z}</div>
            <div style={{ fontSize: 9.5, fontWeight: 700, color: BRAND.ink }}>{z.pct}%</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function ActivityScreen() {
  const stats = [
    { k: 'Duration', v: '1h 24m' }, { k: 'Load', v: '78 TSS' },
    { k: 'EF', v: '1.24' }, { k: 'Decoupling', v: '4.1%' },
  ]
  return (
    <div style={{ height: '100%', position: 'relative', color: BRAND.ink, background: BRAND.cool }}>
      <StatusBar />
      <div style={{ padding: '4px 16px 70px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ width: 9, height: 9, borderRadius: 5, background: BRAND.ride }}/>
          <div style={{ fontSize: 16, fontWeight: 800, letterSpacing: -0.3 }}>Tempo Intervals</div>
        </div>
        <div style={{ fontSize: 11, color: BRAND.dim, marginBottom: 10, marginLeft: 17 }}>Ride · today · 3×8m</div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {stats.map((s) => (
            <div key={s.k} style={{ ...mkCard, padding: 10 }}>
              <div style={mkMicro}>{s.k}</div>
              <div style={{ fontSize: 18, fontWeight: 800, marginTop: 4, letterSpacing: -0.4 }}>{s.v}</div>
            </div>
          ))}
        </div>

        <div style={{ ...mkCard, marginTop: 10 }}>
          <div style={{ ...mkMicro, marginBottom: 10 }}>Time in zones · HR</div>
          <ZoneBar />
        </div>

        <div style={{ ...mkCard, marginTop: 10, background: BRAND.cobaltLite, border: 'none' }}>
          <div style={{ fontSize: 9.5, fontWeight: 800, letterSpacing: 0.6, color: BRAND.cobaltDark, textTransform: 'uppercase' }}>AI · review</div>
          <div style={{ fontSize: 11.5, lineHeight: 1.45, color: '#1a2440', marginTop: 6, textWrap: 'pretty' }}>
            Clean execution. Decoupling 4.1% — the aerobic base holds the pace. Plan completed 96%.
          </div>
        </div>
      </div>
      <TabBar active={2} />
    </div>
  )
}

// switch helper
export type ScreenKind = 'recovery' | 'plan' | 'activity'

export function AppScreen({ which = 'recovery', lead }: { which?: ScreenKind; lead?: string }) {
  if (which === 'plan') return <PlanScreen />
  if (which === 'activity') return <ActivityScreen />
  return <RecoveryScreen lead={lead} />
}
