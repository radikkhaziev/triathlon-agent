import { useTranslation } from 'react-i18next'
import { formatDateDisplay } from '../lib/formatters'

interface DayNavProps {
  currentDate: Date
  isToday: boolean
  hasPrev?: boolean
  hasNext?: boolean
  onPrev: () => void
  onNext: () => void
}

export default function DayNav({ currentDate, isToday, hasPrev = true, hasNext, onPrev, onNext }: DayNavProps) {
  const { t } = useTranslation()
  const disableNext = hasNext !== undefined ? !hasNext : isToday

  return (
    <div className="flex items-center justify-center gap-3 py-2 pb-4">
      <button
        onClick={onPrev}
        disabled={hasPrev === false}
        className="bg-surface-2 border border-border text-text rounded-lg px-3.5 py-2 text-[13px] font-semibold cursor-pointer transition-all hover:bg-border active:scale-[0.97] disabled:opacity-30 disabled:cursor-default font-sans"
      >
        &larr; {t('common.prev')}
      </button>
      <span className="text-sm font-semibold min-w-[140px] text-center">
        {formatDateDisplay(currentDate)}
        {isToday && (
          <span className="inline-block bg-[var(--button)] text-white text-[10px] font-bold px-1.5 py-px rounded ml-1.5 align-middle">
            {t('common.today_lower')}
          </span>
        )}
      </span>
      {!disableNext && (
        <button
          onClick={onNext}
          className="bg-surface-2 border border-border text-text rounded-lg px-3.5 py-2 text-[13px] font-semibold cursor-pointer transition-all hover:bg-border active:scale-[0.97] font-sans"
        >
          {t('common.next')} &rarr;
        </button>
      )}
      {disableNext && (
        <div className="px-3.5 py-2 min-w-[76px]" />
      )}
    </div>
  )
}
