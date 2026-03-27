import { useState, useCallback } from 'react'
import { fmtDateYmd } from '../lib/formatters'

function todayLocal(): Date {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  return d
}

export function useDayNav() {
  const [currentDate, setCurrentDate] = useState(() => todayLocal())

  const dateStr = fmtDateYmd(currentDate)
  const isToday = dateStr === fmtDateYmd(todayLocal())

  const prev = useCallback(() => {
    setCurrentDate(d => {
      const next = new Date(d)
      next.setDate(next.getDate() - 1)
      return next
    })
  }, [])

  const next = useCallback(() => {
    setCurrentDate(d => {
      const today = todayLocal()
      if (fmtDateYmd(d) < fmtDateYmd(today)) {
        const next = new Date(d)
        next.setDate(next.getDate() + 1)
        return next
      }
      return d
    })
  }, [])

  return { currentDate, dateStr, isToday, prev, next }
}
