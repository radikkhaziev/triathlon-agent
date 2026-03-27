import { useState, useCallback } from 'react'

export function useWeekNav() {
  const [offset, setOffset] = useState(0)

  const prev = useCallback(() => setOffset(o => o - 1), [])
  const next = useCallback(() => setOffset(o => o + 1), [])

  return { offset, prev, next, setOffset }
}
