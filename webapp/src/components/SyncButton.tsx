import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { apiFetch } from '../api/client'
import type { SyncResponse } from '../api/types'
import { relativeTime } from '../lib/formatters'

interface SyncButtonProps {
  endpoint: string
  lastSyncedAt: string | null
  onSynced: (result: SyncResponse) => void
}

export default function SyncButton({ endpoint, lastSyncedAt, onSynced }: SyncButtonProps) {
  const [syncing, setSyncing] = useState(false)
  const [queued, setQueued] = useState(false)
  const { t } = useTranslation()

  useEffect(() => {
    setQueued(false)
  }, [lastSyncedAt])

  const handleSync = async () => {
    setSyncing(true)
    try {
      const result = await apiFetch<SyncResponse>(endpoint, { method: 'POST' })
      setQueued(true)
      onSynced(result)
    } catch (err) {
      const msg = t('common.error')
      if (window.Telegram?.WebApp?.showAlert) {
        window.Telegram.WebApp.showAlert(msg)
      } else {
        alert(msg)
      }
    } finally {
      setSyncing(false)
    }
  }

  return (
    <div className="flex items-center justify-center gap-2.5 py-2.5 pb-4">
      <button
        onClick={handleSync}
        disabled={syncing || queued}
        className="bg-accent text-white border-none rounded-lg px-4 py-2 text-[13px] font-semibold cursor-pointer transition-all hover:bg-[#2563eb] active:scale-[0.97] disabled:opacity-60 disabled:cursor-not-allowed font-sans flex items-center gap-1.5"
      >
        {queued ? (
          <><span>&#x2705;</span> {t('common.queued')}</>
        ) : (
          <><span className={syncing ? 'animate-spin inline-block' : ''}>&#x1f504;</span> {t('common.sync')}</>
        )}
      </button>
      <span className="text-xs text-text-dim">
        {lastSyncedAt ? t('common.updated_at', { time: relativeTime(lastSyncedAt) }) : t('common.not_synced')}
      </span>
    </div>
  )
}
