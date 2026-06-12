import { useTranslation } from 'react-i18next'

/**
 * "Sample" pill shown above canned demo content. Demo sessions never receive
 * real AI free-text (server stubs it — docs/DEMO_PUBLIC_ACCESS_SPEC.md
 * Phase 2); wherever the real app renders a Claude-generated string, the demo
 * renders a hand-written sample marked by this badge.
 */
export default function DemoSampleBadge({ textKey }: { textKey: string }) {
  const { t } = useTranslation()
  return (
    <div
      className="mb-2 mt-3 inline-block rounded-pill px-2.5 py-1 text-[11px] font-semibold leading-snug"
      style={{ background: 'color-mix(in srgb, var(--color-amber) 14%, transparent)', color: 'var(--color-amber)' }}
    >
      {t(textKey)}
    </div>
  )
}
