import { useState } from 'react'
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

  const handleSync = async () => {
    setSyncing(true)
    try {
      const result = await apiFetch<SyncResponse>(endpoint, { method: 'POST' })
      onSynced(result)
    } catch (err) {
      const msg = 'Ошибка синхронизации. Проверьте соединение.'
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
        disabled={syncing}
        className="bg-accent text-white border-none rounded-lg px-4 py-2 text-[13px] font-semibold cursor-pointer transition-all hover:bg-[#2563eb] active:scale-[0.97] disabled:opacity-60 disabled:cursor-not-allowed font-sans flex items-center gap-1.5"
      >
        <span className={syncing ? 'animate-spin inline-block' : ''}>&#x1f504;</span> Синхронизировать
      </button>
      <span className="text-xs text-text-dim">
        {lastSyncedAt ? `Обновлено: ${relativeTime(lastSyncedAt)}` : 'Не синхронизировано'}
      </span>
    </div>
  )
}
