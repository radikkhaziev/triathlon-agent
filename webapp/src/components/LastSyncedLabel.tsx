import { useTranslation } from 'react-i18next'
import { useAuth } from '../auth/useAuth'
import { relativeTime } from '../lib/formatters'

interface LastSyncedLabelProps {
  at: string | null
}

export default function LastSyncedLabel({ at }: LastSyncedLabelProps) {
  const { t, i18n } = useTranslation()
  const { isDemo } = useAuth()

  if (isDemo) return null

  return (
    <div className="flex items-center justify-center py-2.5 pb-4">
      <span className="text-xs text-text-dim">
        {at ? t('common.updated_at', { time: relativeTime(at, i18n.language) }) : t('common.not_synced')}
      </span>
    </div>
  )
}
