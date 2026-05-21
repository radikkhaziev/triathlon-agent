import { describe, expect, it } from 'vitest'
import {
  arcPath,
  clamp,
  donutSegments,
  linePath,
  pointAtPct,
  rangePct,
  scaleToPts,
  taperFill,
} from './geometry'

// Golden constants computed independently of the rotation formula (so a bug
// copied into both sides can't stay green). Recovery sweep: r=92, c=(120,120),
// −210°→+30°. Start = (166, 199.674), end = (166, 40.326), large-arc flag 1.
describe('arcPath — frozen golden vs shared.jsx geometry', () => {
  it('exact endpoints + fixed sweep payload for the recovery arc', () => {
    const d = arcPath(120, 120, 92, -210, 30)
    const m = d.match(/^M ([\d.-]+) ([\d.-]+) A 92 92 0 (\d) 1 ([\d.-]+) ([\d.-]+)$/)!
    expect(m).not.toBeNull()
    const [, x0, y0, large, x1, y1] = m.map(Number)
    expect(x0).toBeCloseTo(166, 4)
    expect(y0).toBeCloseTo(199.6743, 4)
    expect(large).toBe(1)
    expect(x1).toBeCloseTo(166, 4)
    expect(y1).toBeCloseTo(40.3257, 4)
  })

  it('large-arc flag is 1 for the full 240° sweep, 0 for a 60° one', () => {
    expect(arcPath(0, 0, 10, -210, 30)).toContain(' 0 1 1 ')
    expect(arcPath(0, 0, 10, -210, -150)).toContain(' 0 0 1 ')
  })
})

describe('pointAtPct — tick positions', () => {
  it('0% = sweep start (166, 199.674), 100% = sweep end (166, 40.326)', () => {
    const [sx, sy] = pointAtPct(120, 120, 92, -210, 30, 0)
    expect(sx).toBeCloseTo(166, 4)
    expect(sy).toBeCloseTo(199.6743, 4)
    const [ex, ey] = pointAtPct(120, 120, 92, -210, 30, 1)
    expect(ex).toBeCloseTo(166, 4)
    expect(ey).toBeCloseTo(40.3257, 4)
    // 50% → angle −90° → leftmost point of the sweep: x=120−92=28, y=120.
    const [mx, my] = pointAtPct(120, 120, 92, -210, 30, 0.5)
    expect(mx).toBeCloseTo(28, 4)
    expect(my).toBeCloseTo(120, 4)
  })
})

describe('scaleToPts / linePath', () => {
  it('maps lo→bottom, hi→top, spreads x evenly', () => {
    const pts = scaleToPts([0, 5, 10], 0, 0, 100, 50, 0, 10)
    expect(pts[0]).toEqual([0, 50]) // value 0 → y = h
    expect(pts[2]).toEqual([100, 0]) // value hi → y = 0
    expect(pts[1][0]).toBeCloseTo(50, 5)
  })

  it('single value sits at x0', () => {
    expect(scaleToPts([7], 12, 0, 100, 50, 0, 10)[0][0]).toBe(12)
  })

  it('linePath emits M then L with .toFixed(1) rounding (shared.jsx contract)', () => {
    expect(linePath([[0, 0], [10, 20]])).toBe('M0.0 0.0 L10.0 20.0')
    expect(linePath([[1.04, 2.06], [3.95, 4.949]])).toBe('M1.0 2.1 L4.0 4.9')
  })
})

describe('donutSegments — cumulative dash/offset', () => {
  it('two equal halves of a C=100 circle', () => {
    const segs = donutSegments([50, 50], 100)
    expect(segs[0]).toEqual({ dash: 50, gap: 50, offset: 0 })
    expect(segs[1]).toEqual({ dash: 50, gap: 50, offset: -50 })
  })

  it('normalises to the value total (not assumed 100)', () => {
    const segs = donutSegments([1, 3], 80) // total 4 → 25% / 75%
    expect(segs[0].dash).toBeCloseTo(20, 5)
    expect(segs[1].dash).toBeCloseTo(60, 5)
    expect(segs[1].offset).toBeCloseTo(-20, 5)
  })

  it('all-zero input does not divide by zero', () => {
    expect(() => donutSegments([0, 0], 100)).not.toThrow()
  })
})

describe('taperFill — overshoot clamps at 40%', () => {
  it('under target: proportional fill, no tail', () => {
    expect(taperFill(25.6, 28)).toEqual({ over: false, pct: (25.6 / 28) * 100, overPct: 0 })
  })

  it('over target: fill 100, faded tail capped at 40', () => {
    const f = taperFill(38.2, 35) // ~9.1% over
    expect(f.over).toBe(true)
    expect(f.pct).toBe(100)
    expect(f.overPct).toBeCloseTo(((38.2 - 35) / 35) * 100, 5)
    expect(taperFill(100, 35).overPct).toBe(40) // huge overshoot → capped
  })

  it('zero target is safe (over → full fill, no tail)', () => {
    expect(taperFill(10, 0)).toEqual({ over: true, pct: 100, overPct: 0 })
  })
})

describe('rangePct / clamp', () => {
  it('clamps current to [lo,hi] → [0,1]', () => {
    expect(rangePct(50, 48, 54)).toBeCloseTo((50 - 48) / 6, 5)
    expect(rangePct(10, 48, 54)).toBe(0)
    expect(rangePct(99, 48, 54)).toBe(1)
    expect(rangePct(5, 5, 5)).toBe(0) // degenerate range
  })

  it('clamp basics', () => {
    expect(clamp(5, 0, 10)).toBe(5)
    expect(clamp(-1, 0, 10)).toBe(0)
    expect(clamp(11, 0, 10)).toBe(10)
  })
})
