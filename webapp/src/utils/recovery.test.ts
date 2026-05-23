import { describe, expect, it } from 'vitest'
import {
  classifyRecovery,
  classifySleep,
  computeRecoveryMeaningStat,
  recommendTraining,
  sleepZoneOf,
  RECOVERY_CHIP,
  RECOVERY_REC_COPY,
  SLEEP_ZONE,
  SLEEP_ZONES,
  STATUS_EMOJI,
} from './recovery'

describe('classifyRecovery — strict > boundaries (gotcha #1)', () => {
  it('85.0 → good, 85.1 → excellent (boundary is exclusive)', () => {
    expect(classifyRecovery(85.0)).toBe('good')
    expect(classifyRecovery(85.1)).toBe('excellent')
    expect(classifyRecovery(85.0001)).toBe('excellent')
  })

  it('70.0 → moderate, 70.1 → good', () => {
    expect(classifyRecovery(70.0)).toBe('moderate')
    expect(classifyRecovery(70.1)).toBe('good')
  })

  it('40.0 → low, 40.1 → moderate', () => {
    expect(classifyRecovery(40.0)).toBe('low')
    expect(classifyRecovery(40.1)).toBe('moderate')
  })

  it('extremes', () => {
    expect(classifyRecovery(100)).toBe('excellent')
    expect(classifyRecovery(0)).toBe('low')
    expect(classifyRecovery(-5)).toBe('low')
  })
})

describe('classifySleep — Garmin-style >=/< boundaries (Halo-v3)', () => {
  it('50.0 → fair, 49.x → poor (lower boundary is inclusive on fair)', () => {
    expect(classifySleep(50)).toBe('fair')
    expect(classifySleep(49.99)).toBe('poor')
    expect(classifySleep(49)).toBe('poor')
  })

  it('70.0 → good, 69.x → fair', () => {
    expect(classifySleep(70)).toBe('good')
    expect(classifySleep(69.99)).toBe('fair')
  })

  it('90.0 → excellent, 89.x → good', () => {
    expect(classifySleep(90)).toBe('excellent')
    expect(classifySleep(89.99)).toBe('good')
  })

  it('extremes', () => {
    expect(classifySleep(100)).toBe('excellent')
    expect(classifySleep(0)).toBe('poor')
    expect(classifySleep(-5)).toBe('poor')
  })
})

describe('sleep zones — SLEEP_ZONES / SLEEP_ZONE / sleepZoneOf', () => {
  it('SLEEP_ZONES is the 4 zones ascending with contiguous boundaries', () => {
    expect(SLEEP_ZONES.map(z => z.id)).toEqual(['poor', 'fair', 'good', 'excellent'])
    expect(SLEEP_ZONES.map(z => z.lo)).toEqual([0, 50, 70, 90])
    expect(SLEEP_ZONES.map(z => z.hi)).toEqual([50, 70, 90, Infinity])
    // Each zone's lower bound meets the previous zone's upper bound — the
    // chart bands + legend captions rely on this with no gaps/overlaps.
    for (let i = 1; i < SLEEP_ZONES.length; i++) {
      expect(SLEEP_ZONES[i].lo).toBe(SLEEP_ZONES[i - 1].hi)
    }
  })

  it('SLEEP_ZONE maps every category to its zone object', () => {
    for (const z of SLEEP_ZONES) {
      expect(SLEEP_ZONE[z.id]).toBe(z)
    }
  })

  it('sleepZoneOf agrees with classifySleep across the boundaries', () => {
    expect(sleepZoneOf(49.99).id).toBe('poor')
    expect(sleepZoneOf(50).id).toBe('fair')
    expect(sleepZoneOf(70).id).toBe('good')
    expect(sleepZoneOf(90).id).toBe('excellent')
  })

  it('every zone carries a solid line + translucent fill colour', () => {
    for (const z of SLEEP_ZONES) {
      expect(z.line).toMatch(/^#/)
      expect(z.fill).toMatch(/^rgba\(/)
    }
  })
})

describe('recommendTraining', () => {
  it('rmssd red overrides to skip regardless of score (gotcha #2)', () => {
    // score 90 → excellent, but red rmssd ⇒ skip
    expect(recommendTraining(classifyRecovery(90), 'red')).toBe('skip')
    expect(recommendTraining('excellent', 'red')).toBe('skip')
    expect(recommendTraining('low', 'red')).toBe('skip')
  })

  it('non-red maps category → rec', () => {
    expect(recommendTraining('excellent', 'green')).toBe('zone2_ok')
    expect(recommendTraining('good', 'green')).toBe('zone2_ok')
    expect(recommendTraining('moderate', 'yellow')).toBe('zone1_long')
    expect(recommendTraining('low', 'yellow')).toBe('zone1_short')
  })

  it('yellow rmssd does not leak into the category mapping (zone2 branch)', () => {
    expect(recommendTraining('excellent', 'yellow')).toBe('zone2_ok')
    expect(recommendTraining('good', 'yellow')).toBe('zone2_ok')
  })

  it('insufficient_data does not trigger the skip override', () => {
    // gotcha #3 — its own status; rec still derives from category
    expect(recommendTraining('good', 'insufficient_data')).toBe('zone2_ok')
    expect(recommendTraining('moderate', 'insufficient_data')).toBe('zone1_long')
  })
})

describe('copy + status maps are exhaustive', () => {
  const cats = ['excellent', 'good', 'moderate', 'low'] as const
  const recs = ['zone2_ok', 'zone1_long', 'zone1_short', 'skip'] as const
  const statuses = ['green', 'yellow', 'red', 'insufficient_data'] as const

  it('RECOVERY_CHIP covers every category in en + ru', () => {
    for (const c of cats) {
      expect(RECOVERY_CHIP.en[c].label).toBeTruthy()
      expect(RECOVERY_CHIP.ru[c].label).toBeTruthy()
    }
  })

  it('RECOVERY_REC_COPY covers every rec in en + ru', () => {
    for (const r of recs) {
      expect(RECOVERY_REC_COPY.en[r]).toBeTruthy()
      expect(RECOVERY_REC_COPY.ru[r]).toBeTruthy()
    }
  })

  it('STATUS_EMOJI covers every rmssd status; insufficient_data is ⚪', () => {
    for (const s of statuses) {
      expect(STATUS_EMOJI[s]).toBeTruthy()
    }
    expect(STATUS_EMOJI.insufficient_data).toBe('⚪')
  })
})

describe('computeRecoveryMeaningStat — period summary for /wellness/recovery', () => {
  it('returns null when the entire period is empty (cold-start)', () => {
    expect(computeRecoveryMeaningStat([])).toBeNull()
    expect(computeRecoveryMeaningStat([null, null, null])).toBeNull()
  })

  it('counts only non-null days; goodPct uses STRICT >70 to match classifier', () => {
    // 5 days · 3 in good+excellent (75, 80, 90) · 1 moderate (50) · 1 low (30).
    // 70.0 exactly is `moderate`, NOT `good` — the strict boundary on
    // `classifyRecovery` must propagate here, otherwise the meaning would
    // claim «N days in green zone» while the user's tile reads «moderate»
    // for the same value (visible inconsistency between two surfaces).
    const stat = computeRecoveryMeaningStat([75, 80, 90, 50, 30])
    expect(stat).not.toBeNull()
    expect(stat!.days).toBe(5)
    expect(stat!.goodPct).toBe(60) // 3/5
    expect(stat!.lowPct).toBe(20) // 1/5
    expect(stat!.avg).toBe(65) // (75+80+90+50+30)/5 = 65
    expect(stat!.todayCategory).toBe('low') // last non-null = 30
  })

  it('70.0 boundary is moderate (matches classifyRecovery), 70.1 is good', () => {
    // Sanity-lock — `goodPct` uses `> 70`, NOT `>= 70`. A regression to `>=`
    // would silently shift the threshold.
    expect(computeRecoveryMeaningStat([70.0])!.goodPct).toBe(0)
    expect(computeRecoveryMeaningStat([70.1])!.goodPct).toBe(100)
  })

  it('40.0 boundary is low (≤40), 40.1 is moderate', () => {
    expect(computeRecoveryMeaningStat([40.0])!.lowPct).toBe(100)
    expect(computeRecoveryMeaningStat([40.1])!.lowPct).toBe(0)
  })

  it('todayCategory is the LAST non-null score, not the period mean', () => {
    // Period averages to 60 (moderate), but today is 90 → excellent.
    // Template selection is keyed off today, not the mean — most actionable.
    const stat = computeRecoveryMeaningStat([30, 30, 90])
    expect(stat!.todayCategory).toBe('excellent')
    expect(stat!.avg).toBe(50)
  })

  it('returns no_today-friendly null category when the tail is missing', () => {
    // Period has data, but today is null (sync gap on the edge). Render
    // falls back to `recovery_trend.meaning.no_today` rather than picking
    // a stale category.
    const stat = computeRecoveryMeaningStat([60, 70, null, null])
    expect(stat!.todayCategory).toBeNull()
    expect(stat!.days).toBe(2)
    expect(stat!.avg).toBe(65)
  })
})
