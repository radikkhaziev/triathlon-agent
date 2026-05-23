/**
 * Pure SVG geometry helpers for the Halo bespoke gauges.
 *
 * Ported verbatim from the design package `shared.jsx` (arcPath / scaleToPts /
 * linePath / clamp) so the recreated gauges match the prototype's geometry
 * exactly. Pure functions only — unit-tested in node env (geometry.test.ts),
 * no DOM. Brief §2 / README §7 sanction inline SVG for one-off gauges.
 */

export const clamp = (n: number, a: number, b: number): number =>
  Math.max(a, Math.min(b, n))

/**
 * Arc path for a circular gauge. Angles in degrees, clockwise, 0° = 12
 * o'clock (the `-90` rotation matches `shared.jsx`). Orientation-agnostic —
 * the Halo `Gauge` sweeps 240° total via −120°→+120° (bottom-opening; the
 * mock's −210/+30 rendered right-opening and is not used).
 */
export function arcPath(
  cx: number,
  cy: number,
  r: number,
  a0: number,
  a1: number,
): string {
  const toRad = (a: number) => ((a - 90) * Math.PI) / 180
  const x0 = cx + r * Math.cos(toRad(a0))
  const y0 = cy + r * Math.sin(toRad(a0))
  const x1 = cx + r * Math.cos(toRad(a1))
  const y1 = cy + r * Math.sin(toRad(a1))
  const large = a1 - a0 > 180 ? 1 : 0
  return `M ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1}`
}

/** Point on the gauge circle at `pct` (0..1) of the sweep — for tick marks. */
export function pointAtPct(
  cx: number,
  cy: number,
  r: number,
  a0: number,
  a1: number,
  pct: number,
): [number, number] {
  const a = a0 + pct * (a1 - a0)
  const rad = ((a - 90) * Math.PI) / 180
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)]
}

/** Map values to SVG points within a box (Progress EF line, CTL projection). */
export function scaleToPts(
  values: number[],
  x0: number,
  y0: number,
  w: number,
  h: number,
  lo: number,
  hi: number,
): [number, number][] {
  const n = values.length
  return values.map((v, i) => {
    const x = x0 + (n === 1 ? 0 : (i / (n - 1)) * w)
    const y = y0 + h - ((v - lo) / (hi - lo)) * h
    return [x, y]
  })
}

/** Array of points → "M x y L x y …" path. */
export const linePath = (pts: [number, number][]): string =>
  pts
    .map((p, i) => (i === 0 ? 'M' : 'L') + p[0].toFixed(1) + ' ' + p[1].toFixed(1))
    .join(' ')

/**
 * Stacked-arc donut segment dashes. Returns per-segment
 * `{ dash, gap, offset }` for a `<circle>` of circumference `C`, drawn
 * cumulatively (matches the prototype's `strokeDasharray`/`offset` recipe).
 */
export function donutSegments(
  values: number[],
  C: number,
): { dash: number; gap: number; offset: number }[] {
  const total = values.reduce((a, b) => a + b, 0) || 1
  let acc = 0
  return values.map(v => {
    const pct = v / total
    const dash = pct * C
    const offset = acc === 0 ? 0 : -acc * C // avoid -0 in strokeDashoffset
    acc += pct
    return { dash, gap: C - dash, offset }
  })
}

/**
 * Per-sport CTL progress with taper overshoot (Dashboard · Goal).
 * `pct` = filled width %, `overPct` = faded dashed tail width % (capped 40).
 */
export function taperFill(
  current: number,
  target: number,
): { over: boolean; pct: number; overPct: number } {
  const over = current > target
  const pct = over ? 100 : target > 0 ? (current / target) * 100 : 0
  const overPct = over && target > 0 ? Math.min(40, ((current - target) / target) * 100) : 0
  return { over, pct, overPct }
}

/** Position (0..1) of `cur` inside a [lo, hi] range, clamped (HRV/RHR gauge). */
export const rangePct = (cur: number, lo: number, hi: number): number =>
  hi === lo ? 0 : clamp((cur - lo) / (hi - lo), 0, 1)
