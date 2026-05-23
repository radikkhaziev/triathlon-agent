import { useTranslation } from 'react-i18next'
import Layout from '../components/Layout'
import { TopBar } from '../components/halo'
import MergedWeek from './MergedWeek'

/**
 * `/calendar` route host. Per design `BWeek` (direction-b-halo.jsx:1351) the
 * Week tab is a single merged view — plan + actuals zipped by day. The earlier
 * `[Week · Plan]` segmented toggle was a transitional spec compromise (no new
 * routes, two mount modes) and got dropped on design-reconcile 2026-05-23.
 * MergedWeek now mounts directly; the standalone Plan list is retired (see
 * the same commit deleting `pages/Plan.tsx`).
 */
export default function PlanScreen() {
  const { t } = useTranslation()
  return (
    <Layout maxWidth="480px">
      <div className="-mx-4 -mt-4 md:-mb-8 min-h-screen bg-halo-bg px-4 md:px-9 font-sans text-halo-ink">
        <TopBar title={t('nav.week')} subtitle={t('plan.desktop_subtitle')} />
        <div className="pt-3">
          <MergedWeek />
        </div>
      </div>
    </Layout>
  )
}
