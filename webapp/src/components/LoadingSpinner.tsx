import { useTranslation } from 'react-i18next'

export default function LoadingSpinner({ text }: { text?: string }) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col items-center justify-center h-[50vh] text-text-dim gap-3">
      <div className="w-7 h-7 border-3 border-surface-2 border-t-accent rounded-full animate-spin" />
      <span>{text || t('common.loading')}</span>
    </div>
  )
}
