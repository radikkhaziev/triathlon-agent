import { useState, useCallback } from 'react'
import { fmtDateYmd } from '../lib/formatters'

function todayLocal(): Date {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  return d
}

export function useDayNav(initialDate?: Date) {
  // `initialDate` lets a caller deep-link a specific day — the All-history
  // calendar passes the tapped day via `?date=`. Clamped to ≤ today; a future
  // or absent value falls back to today (mirrors `goTo`'s no-future rule).
  const [currentDate, setCurrentDate] = useState(() => {
    if (!initialDate) return todayLocal()
    const d = new Date(initialDate)
    d.setHours(0, 0, 0, 0)
    const today = todayLocal()
    return fmtDateYmd(d) > fmtDateYmd(today) ? today : d
  })

  const dateStr = fmtDateYmd(currentDate)
  const isToday = dateStr === fmtDateYmd(todayLocal())

  const prev = useCallback(() => {
    setCurrentDate(d => {
      const newDate = new Date(d)
      newDate.setDate(newDate.getDate() - 1)
      return newDate
    })
  }, [])

  const next = useCallback(() => {
    setCurrentDate(d => {
      const today = todayLocal()
      if (fmtDateYmd(d) < fmtDateYmd(today)) {
        const newDate = new Date(d)
        newDate.setDate(newDate.getDate() + 1)
        return newDate
      }
      return d
    })
  }, [])

  // Jump to an arbitrary day (Halo date-strip pill). Clamped to ≤ today,
  // mirroring `next`'s no-future rule. Wellness is the only consumer.
  const goTo = useCallback((d: Date) => {
    const day = new Date(d)
    day.setHours(0, 0, 0, 0)
    const today = todayLocal()
    setCurrentDate(fmtDateYmd(day) > fmtDateYmd(today) ? today : day)
  }, [])

  return { currentDate, dateStr, isToday, prev, next, goTo }
}
