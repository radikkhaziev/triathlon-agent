import { Link } from 'react-router-dom'
import Layout from '../components/Layout'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import Gauge from '../components/Gauge'
import AiRecommendation from '../components/AiRecommendation'
import { useApi } from '../hooks/useApi'
import { formatDate, num } from '../lib/formatters'
import { CATEGORY_COLORS } from '../lib/constants'
import type { WellnessResponse } from '../api/types'

function StatusBadgeInline({ status }: { status: string }) {
  const map: Record<string, [string, string]> = {
    green: ['bg-[#22c55e18] text-green', 'Норма'],
    yellow: ['bg-[#f59e0b18] text-yellow', 'Внимание'],
    red: ['bg-[#ef444418] text-red', 'Тревога'],
  }
  const info = map[status]
  if (!info) return null
  return <span className={`ml-auto text-[11px] font-semibold px-2 py-0.5 rounded-[10px] ${info[0]}`}>{info[1]}</span>
}

export default function Report() {
  const { data, loading, error } = useApi<WellnessResponse>('/api/report')

  if (loading) return <Layout maxWidth="480px"><LoadingSpinner /></Layout>
  if (error) return <Layout maxWidth="480px"><ErrorMessage message="Не удалось загрузить отчёт." /></Layout>
  if (!data?.has_data) return <Layout maxWidth="480px"><ErrorMessage message="Нет данных на сегодня. Данные обновляются автоматически." /></Layout>

  const rec = data.recovery || {} as WellnessResponse['recovery']
  const cat = rec.category || 'moderate'
  const color = CATEGORY_COLORS[cat] || CATEGORY_COLORS.moderate
  const hrv = data.hrv || {} as WellnessResponse['hrv']
  const primary = hrv.primary_algorithm || 'flatt_esco'
  const hrvBlock = hrv[primary as keyof typeof hrv]
  const hrvData = typeof hrvBlock === 'object' && hrvBlock !== null && 'status' in hrvBlock ? hrvBlock : null
  const rhr = data.rhr
  const sleep = data.sleep || {} as WellnessResponse['sleep']
  const load = data.training_load || {} as WellnessResponse['training_load']
  const sc = load.sport_ctl || { swim: null, bike: null, run: null }

  return (
    <Layout maxWidth="480px">
      <div className="flex justify-end pt-3">
        <Link to="/plan" className="text-[13px] text-[var(--button)] no-underline">План тренировок &rarr;</Link>
      </div>

      <div className="text-center text-[13px] text-text-dim pt-3 pb-1">{formatDate(data.date)}</div>

      {/* Recovery Header */}
      <div className="text-center px-4 py-6 mx-[-16px] mb-4 border-b border-[var(--tg-theme-secondary-bg-color,#1c1c27)]">
        <div className="text-xs font-bold tracking-[1.5px] uppercase mb-3" style={{ color }}>{rec.title || ''}</div>
        <div className="relative w-[140px] h-[140px] mx-auto mb-3">
          <Gauge score={rec.score || 0} color={color} size={140} />
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-center">
            <div className="text-[42px] font-extrabold leading-none" style={{ color }}>
              {rec.score != null ? Math.round(rec.score) : '--'}
            </div>
            <div className="text-[11px] text-text-dim mt-0.5">Готовность</div>
          </div>
        </div>
        <div className="text-sm text-text-dim">{rec.recommendation || ''}</div>
      </div>

      {/* Quick metrics */}
      <div className="grid grid-cols-2 gap-2 mb-2.5">
        <div className="bg-[var(--tg-theme-bg-color,var(--bg))] rounded-[10px] p-3 text-center">
          <div className="text-2xl font-bold">{sleep.score != null ? sleep.score : '--'}</div>
          <div className="text-[11px] text-text-dim mt-0.5">Оценка сна</div>
        </div>
      </div>

      {/* HRV Section */}
      {hrvData && hrvData.status !== 'insufficient_data' && (
        <Section icon="🫀" title="HRV (RMSSD)" badge={<StatusBadgeInline status={hrvData.status} />}>
          {hrvData.delta_pct != null && (
            <div className="text-[22px] font-bold text-center py-1 pb-2" style={{ color: hrvData.delta_pct >= 0 ? 'var(--green)' : 'var(--red)' }}>
              {hrvData.delta_pct >= 0 ? '+' : ''}{hrvData.delta_pct}%
            </div>
          )}
          <MetricRow label="Сегодня" value={hrvData.today != null ? `${hrvData.today.toFixed(0)} мс` : '--'} />
          <MetricRow label="Норма 7д" value={hrvData.mean_7d != null ? `${hrvData.mean_7d.toFixed(0)} мс` : '--'} />
          {hrvData.mean_60d != null && <MetricRow label="Норма 60д" value={`${hrvData.mean_60d.toFixed(0)} мс`} />}
          {hrvData.swc != null && hrvData.swc_verdict && (
            <MetricRow label="SWC" value={`${hrvData.swc.toFixed(1)} мс — ${hrvData.swc_verdict}`} />
          )}
          {hrvData.cv_7d != null && hrvData.cv_verdict && (
            <MetricRow label="Стабильность" value={`${hrvData.cv_verdict} (CV ${hrvData.cv_7d.toFixed(1)}%)`} />
          )}
        </Section>
      )}

      {/* RHR Section */}
      {rhr && rhr.status !== 'insufficient_data' && rhr.today != null && (
        <Section icon="💓" title="Пульс покоя" badge={<StatusBadgeInline status={rhr.status} />}>
          <MetricRow label="Сегодня" value={`${rhr.today.toFixed(0)} уд`} />
          {rhr.mean_30d != null && <MetricRow label="Норма 30д" value={`${rhr.mean_30d.toFixed(0)} уд`} />}
          {rhr.delta_30d != null && (
            <MetricRow
              label="Отклонение"
              value={`${rhr.delta_30d >= 0 ? '+' : ''}${rhr.delta_30d.toFixed(0)} уд`}
              valueClass={rhr.delta_30d > 0 ? 'text-red' : rhr.delta_30d < 0 ? 'text-green' : ''}
            />
          )}
        </Section>
      )}

      {/* Sleep Section */}
      {sleep.score != null && (
        <Section icon="😴" title="Сон">
          <MetricRow label="Оценка" value={`${sleep.score}/100`} />
          <MetricRow label="Длительность" value={sleep.duration || '--'} />
        </Section>
      )}

      {/* Training Load */}
      {(load.ctl != null || load.atl != null) && (
        <Section icon="📊" title="Нагрузка">
          <div className="grid grid-cols-2 gap-2 mb-0">
            <div className="bg-[var(--tg-theme-bg-color,var(--bg))] rounded-[10px] p-3 text-center">
              <div className="text-2xl font-bold">{load.ctl != null ? load.ctl.toFixed(0) : '--'}</div>
              <div className="text-[11px] text-text-dim mt-0.5">CTL (Фитнес)</div>
            </div>
            <div className="bg-[var(--tg-theme-bg-color,var(--bg))] rounded-[10px] p-3 text-center">
              <div className="text-2xl font-bold">{load.atl != null ? load.atl.toFixed(0) : '--'}</div>
              <div className="text-[11px] text-text-dim mt-0.5">ATL (Усталость)</div>
            </div>
          </div>
          {load.tsb != null && (
            <MetricRow
              label="TSB (Форма)"
              value={`${load.tsb >= 0 ? '+' : ''}${load.tsb.toFixed(0)}`}
              valueClass={load.tsb > 10 ? 'text-green' : load.tsb < -25 ? 'text-red' : ''}
            />
          )}
          {load.ramp_rate != null && (
            <MetricRow
              label="Ramp Rate"
              value={`${load.ramp_rate.toFixed(1)} TSS/нед`}
              valueClass={load.ramp_rate > 7 ? 'text-red' : ''}
            />
          )}
          {(sc.swim != null || sc.bike != null || sc.run != null) && (
            <div className="pt-2.5 mt-2.5 border-t border-[var(--tg-theme-bg-color,var(--bg))]">
              {sc.swim != null && <MetricRow label="🏊 Swim CTL" value={num(sc.swim)} />}
              {sc.bike != null && <MetricRow label="🚴 Bike CTL" value={num(sc.bike)} />}
              {sc.run != null && <MetricRow label="🏃 Run CTL" value={num(sc.run)} />}
            </div>
          )}
        </Section>
      )}

      {/* AI */}
      <AiRecommendation claude={data.ai_recommendation} gemini={data.ai_recommendation_gemini} />
    </Layout>
  )
}

function Section({ icon, title, badge, children }: { icon: string; title: string; badge?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="bg-[var(--tg-theme-secondary-bg-color,#f0f0f0)] rounded-[14px] p-4 mb-2.5">
      <div className="flex items-center gap-2 mb-2.5">
        <span className="text-lg leading-none">{icon}</span>
        <span className="text-sm font-bold">{title}</span>
        {badge}
      </div>
      {children}
    </div>
  )
}

function MetricRow({ label, value, valueClass }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="flex justify-between items-center py-[5px] text-[13px]">
      <span className="text-text-dim">{label}</span>
      <span className={`font-semibold text-right ${valueClass || ''}`}>{value}</span>
    </div>
  )
}
