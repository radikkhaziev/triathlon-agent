import { useState, useEffect, useCallback, useRef } from 'react'
import { apiFetch } from '../api/client'

export function useApi<T>(endpoint: string | null) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const controllerRef = useRef<AbortController | null>(null)

  const load = useCallback((ep: string) => {
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller

    setLoading(true)
    setError(null)
    apiFetch<T>(ep, { signal: controller.signal })
      .then(result => {
        if (!controller.signal.aborted) setData(result)
      })
      .catch(err => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : 'Unknown error')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
  }, [])

  useEffect(() => {
    if (endpoint) load(endpoint)
    return () => { controllerRef.current?.abort() }
  }, [endpoint, load])

  const reload = useCallback(() => {
    if (endpoint) load(endpoint)
  }, [endpoint, load])

  return { data, loading, error, reload }
}
