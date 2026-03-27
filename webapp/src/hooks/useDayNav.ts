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

  return { currentDate, dateStr, isToday, prev, next }
}
