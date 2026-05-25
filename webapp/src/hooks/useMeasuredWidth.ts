import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Measures the rendered CSS width of an element via ResizeObserver.
 * Returns `[ref, w]` — attach `ref` to the wrapper as `ref={ref}` (callback
 * ref, no `useRef` needed). `w` is `fallback` until the first measurement.
 *
 * Why a callback ref instead of accepting a `RefObject`: charts often early-
 * return on «no data» before mounting the wrapper. A useEffect with `[ref]`
 * deps runs once with `ref.current === null` and never reattaches when data
 * arrives later. The callback ref fires whenever the DOM node attaches or
 * detaches, so the observer follows the element through re-renders.
 *
 * Used by inline-SVG charts to set a viewBox-W that matches DOM width 1:1,
 * removing the non-uniform stretch that comes from a fixed viewBox + 100%-
 * wide SVG + `preserveAspectRatio="none"`.
 */
const MIN_W = 80 // below this innerW goes negative in every chart's pad budget

export function useMeasuredWidth<T extends HTMLElement = HTMLDivElement>(
  fallback: number,
): [(el: T | null) => void, number] {
  const [w, setW] = useState(fallback)
  const observerRef = useRef<ResizeObserver | null>(null)

  const ref = useCallback((el: T | null) => {
    observerRef.current?.disconnect()
    observerRef.current = null
    if (!el) return
    const ro = new ResizeObserver(entries => {
      const next = Math.round(entries[0].contentRect.width)
      if (next >= MIN_W) setW(next)
    })
    ro.observe(el)
    observerRef.current = ro
  }, [])

  useEffect(() => () => observerRef.current?.disconnect(), [])
  return [ref, w]
}
