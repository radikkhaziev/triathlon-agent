import { useState, useEffect } from 'react'
import { apiFetch } from '../api/client'

export function useApi<T>(endpoint: string | null) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!endpoint) return

    const controller = new AbortController()

    const doLoad = async () => {
      setLoading(true)
      setError(null)
      try {
        const result = await apiFetch<T>(endpoint, { signal: controller.signal })
        setData(result)
      } catch (err) {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : 'Unknown error')
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false)
        }
      }
    }

    doLoad()
    return () => controller.abort()
  }, [endpoint])

  const reload = () => {
    if (!endpoint) return
    setLoading(true)
    setError(null)
    apiFetch<T>(endpoint)
      .then(setData)
      .catch(err => setError(err instanceof Error ? err.message : 'Unknown error'))
      .finally(() => setLoading(false))
  }

  return { data, loading, error, reload }
}
