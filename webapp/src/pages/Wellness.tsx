import { useState } from 'react'
import Layout from '../components/Layout'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import DayNav from '../components/DayNav'
import MetricCard from '../components/MetricCard'
import StatusBadge from '../components/StatusBadge'
import TabSwitcher from '../components/TabSwitcher'
import AiRecommendation from '../components/AiRecommendation'
import SportCtlBars from '../components/SportCtlBars'
import { useDayNav } from '../hooks/useDayNav'
import { useApi } from '../hooks/useApi'
import { num } from '../lib/formatters'
import type { WellnessResponse, HRVBlock } from '../api/types'

export default function Wellness() {
  const { currentDate, dateStr, isToday, prev, next } = useDayNav()
  const { data, loading, error } = useApi<WellnessResponse>(`/api/wellness-day?date=${dateStr}`)
  const [hrvTab, setHrvTab] = useState('flatt_esco')

  return (
    <Layout title="Wellness" backTo="/">
      <DayNav
        currentDate={currentDate}
        isToday={isToday}
        hasPrev={data?.has_prev !== false}
        hasNext={data?.has_next}
        onPrev={prev}
        onNext={next}
      />

      {loading && <LoadingSpinner />}
      {error && <ErrorMessage message="Не удалось загрузить данные." />}

      {!loading && !error && data && !data.has_data && (
        <ErrorMessage message="Нет данных за эту дату" />
      )}

      {!loading && !error && data?.has_data && (
        <>
          {/* Recovery */}
          <Section icon={data.recovery?.emoji || '⚪'} title="Восстановление">
            <div className="flex items-center gap-3 mb-3">
              <div className="text-4xl font-bold leading-none" style={{ color: gaugeColor(data.recovery?.score || 0) }}>
                {data.recovery?.score != null ? Math.round(data.recovery.score) : '--'}
              </div>
              <div className="flex-1">
                <div className="text-sm font-semibold">{data.recovery?.title || ''}</div>
                <div className="text-xs text-text-dim mt-0.5">{data.recovery?.recommendation || ''}</div>
              </div>
            </div>
            <div className="h-1.5 bg-border rounded-full overflow-hidden mb-2.5">
              <div className="h-full rounded-full transition-[width] duration-500" style={{ width: `${Math.min(100, data.recovery?.score || 0)}%`, background: gaugeColor(data.recovery?.score || 0) }} />
            </div>
            <MetricRow label="Readiness" value={data.recovery?.readiness_level ? <StatusBadge status={data.recovery.readiness_level} /> : '--'} />
            <MetricRow label="ESS" value={data.stress?.ess_today != null ? num(data.stress.ess_today) : '--'} />
            <MetricRow label="Banister Recovery" value={data.stress?.banister_recovery != null ? `${num(data.stress.banister_recovery)}%` : '--'} />
          </Section>

          {/* Sleep */}
          <Section icon="😴" title="Сон">
            <div className="grid grid-cols-2 gap-2">
              <MetricCard label="Sleep Score" value={data.sleep?.score != null ? String(data.sleep.score) : '--'} />
              <MetricCard label="Длительность" value={data.sleep?.duration || '--'} sub={data.sleep?.quality != null ? `Качество: ${data.sleep.quality}` : undefined} />
            </div>
          </Section>

          {/* HRV */}
          <Section icon="💓" title="HRV (RMSSD)">
            <TabSwitcher
              tabs={[
                { key: 'flatt_esco', label: 'Flatt & Esco', dot: data.hrv?.primary_algorithm === 'flatt_esco' },
                { key: 'ai_endurance', label: 'AIEndurance', dot: data.hrv?.primary_algorithm === 'ai_endurance' },
              ]}
              active={hrvTab}
              onChange={setHrvTab}
            />
            {hrvTab === 'flatt_esco' && data.hrv?.flatt_esco && <HRVBlockView block={data.hrv.flatt_esco} />}
            {hrvTab === 'ai_endurance' && data.hrv?.ai_endurance && <HRVBlockView block={data.hrv.ai_endurance} />}
          </Section>

          {/* RHR */}
          <Section icon="❤️" title="Пульс в покое">
            <div className="grid grid-cols-2 gap-2 mb-2">
              <MetricCard
                label="Сегодня"
                value={data.rhr?.today != null ? `${num(data.rhr.today, 0)} bpm` : '--'}
                sub={data.rhr?.delta_30d != null ? `\u03B4 ${data.rhr.delta_30d > 0 ? '+' : ''}${num(data.rhr.delta_30d)}` : undefined}
                subClass={data.rhr?.delta_30d != null ? (data.rhr.delta_30d > 0 ? 'text-red' : data.rhr.delta_30d < 0 ? 'text-green' : '') : undefined}
              />
              <div className="bg-[var(--bg)] border border-border rounded-[10px] px-3 py-2.5">
                <div className="text-[11px] text-text-dim uppercase">Статус</div>
                <div className="mt-0.5">{data.rhr ? <StatusBadge status={data.rhr.status} /> : '--'}</div>
              </div>
            </div>
            {data.rhr && (
              <div className="[&>div:last-child]:border-b-0">
                <MetricRow label="Среднее 7д" value={data.rhr.mean_7d != null ? `${num(data.rhr.mean_7d, 0)} \u00B1 ${num(data.rhr.sd_7d)}` : '--'} />
                <MetricRow label="Среднее 30д" value={data.rhr.mean_30d != null ? `${num(data.rhr.mean_30d, 0)} \u00B1 ${num(data.rhr.sd_30d)}` : '--'} />
                <MetricRow label="Среднее 60д" value={data.rhr.mean_60d != null ? `${num(data.rhr.mean_60d, 0)} \u00B1 ${num(data.rhr.sd_60d)}` : '--'} />
                <MetricRow label="Bounds" value={data.rhr.lower_bound != null ? `${num(data.rhr.lower_bound, 0)} \u2014 ${num(data.rhr.upper_bound, 0)}` : '--'} />
                <MetricRow label="CV 7д" value={data.rhr.cv_7d != null ? `${num(data.rhr.cv_7d)}% ${data.rhr.cv_verdict || ''}` : '--'} />
                {data.rhr.trend && <MetricRow label="Тренд" value={data.rhr.trend.direction || '--'} />}
              </div>
            )}
          </Section>

          {/* Training Load */}
          <Section icon="📈" title="Тренировочная нагрузка">
            <div className="grid grid-cols-2 gap-2 mb-2">
              <MetricCard label="CTL (фитнес)" value={data.training_load?.ctl != null ? num(data.training_load.ctl) : '--'} />
              <MetricCard label="ATL (усталость)" value={data.training_load?.atl != null ? num(data.training_load.atl) : '--'} />
              <MetricCard label="TSB (форма)" value={data.training_load?.tsb != null ? `${data.training_load.tsb > 0 ? '+' : ''}${num(data.training_load.tsb)}` : '--'} />
              <MetricCard label="Ramp Rate" value={data.training_load?.ramp_rate != null ? `${num(data.training_load.ramp_rate)} TSS/нед` : '--'} />
            </div>
            {data.training_load?.sport_ctl && (data.training_load.sport_ctl.swim != null || data.training_load.sport_ctl.bike != null || data.training_load.sport_ctl.run != null) && (
              <SportCtlBars {...data.training_load.sport_ctl} />
            )}
          </Section>

          {/* Body */}
          {data.body && (data.body.weight != null || data.body.body_fat != null || data.body.vo2max != null || data.body.steps != null) && (
            <Section icon="🏋️" title="Тело">
              <div className="grid grid-cols-2 gap-2">
                {data.body.weight != null && <MetricCard label="Вес" value={`${num(data.body.weight)} кг`} />}
                {data.body.body_fat != null && <MetricCard label="Body Fat" value={`${num(data.body.body_fat)}%`} />}
                {data.body.vo2max != null && <MetricCard label="VO2max" value={num(data.body.vo2max)} />}
                {data.body.steps != null && <MetricCard label="Шаги" value={data.body.steps.toLocaleString()} />}
              </div>
            </Section>
          )}

          {/* AI */}
          <AiRecommendation claude={data.ai_recommendation} gemini={data.ai_recommendation_gemini} />
        </>
      )}
    </Layout>
  )
}

function Section({ icon, title, children }: { icon: string; title: string; children: React.ReactNode }) {
  return (
    <div className="bg-surface border border-border rounded-[14px] p-4 mb-3">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-lg">{icon}</span>
        <span className="text-[15px] font-bold">{title}</span>
      </div>
      {children}
    </div>
  )
}

function MetricRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between items-center py-1.5 border-b border-border last:border-b-0">
      <span className="text-[13px] text-text-dim">{label}</span>
      <span className="text-sm font-semibold">{value}</span>
    </div>
  )
}

function HRVBlockView({ block }: { block: HRVBlock }) {
  return (
    <>
      <div className="grid grid-cols-2 gap-2 mb-2">
        <MetricCard
          label="Сегодня"
          value={block.today != null ? `${num(block.today, 0)} мс` : '--'}
          sub={block.delta_pct != null ? `${block.delta_pct > 0 ? '+' : ''}${num(block.delta_pct)}%` : undefined}
          subClass={block.delta_pct != null ? (block.delta_pct > 0 ? 'text-green' : block.delta_pct < 0 ? 'text-red' : '') : undefined}
        />
        <div className="bg-[var(--bg)] border border-border rounded-[10px] px-3 py-2.5">
          <div className="text-[11px] text-text-dim uppercase">Статус</div>
          <div className="mt-0.5"><StatusBadge status={block.status} /></div>
        </div>
      </div>
      <MetricRow label="Среднее 7д" value={block.mean_7d != null ? `${num(block.mean_7d, 0)} \u00B1 ${num(block.sd_7d)}` : '--'} />
      <MetricRow label="Среднее 60д" value={block.mean_60d != null ? `${num(block.mean_60d, 0)} \u00B1 ${num(block.sd_60d)}` : '--'} />
      <MetricRow label="Bounds" value={block.lower_bound != null ? `${num(block.lower_bound, 0)} \u2014 ${num(block.upper_bound, 0)}` : '--'} />
      <MetricRow label="CV 7д" value={block.cv_7d != null ? `${num(block.cv_7d)}% ${block.cv_verdict || ''}` : '--'} />
      <MetricRow label="SWC" value={block.swc_verdict || '--'} />
      {block.trend && <MetricRow label="Тренд" value={block.trend.direction || '--'} />}
    </>
  )
}

function gaugeColor(score: number): string {
  if (score >= 70) return 'var(--green)'
  if (score >= 40) return 'var(--yellow)'
  return 'var(--red)'
}
