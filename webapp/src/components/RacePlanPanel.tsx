import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, apiFetch } from '../api/client'
import type { ConfidenceTier, InheritableRace, RaceConditionsInput, RacePlanLeg, RacePlanResponse } from '../api/types'
import RaceConditionsForm from './RaceConditionsForm'

// Tier → visual cue. Spec §3 cutoffs: final <7d / late 7-14d / mid 14-60d / early 60-200d.
// Color says "how much should the athlete trust this": green = settled, amber = preliminary,
// dimmer = early-stage. Tier labels stay English-only — race-day terminology is universal,
// and short ALL-CAPS reads at a glance regardless of UI language (review M1 carve-out).
const TIER_BADGE: Record<ConfidenceTier, { label: string; cls: string; tooltip: string }> = {
  final: {
    label: 'FINAL',
    cls: 'bg-green-100 text-green-700 border-green-300',
    tooltip: 'Race within 7 days — corridors are settled.',
  },
  late: {
    label: 'LATE',
    cls: 'bg-blue-100 text-blue-700 border-blue-300',
    tooltip: 'Race within 2 weeks — minor tweaks possible.',
  },
  mid: {
    label: 'MID',
    cls: 'bg-amber-100 text-amber-700 border-amber-300',
    tooltip: 'Race 2-8 weeks out — corridors will tighten closer to race day.',
  },
  early: {
    label: 'EARLY',
    cls: 'bg-zinc-100 text-zinc-600 border-zinc-300',
    tooltip: 'Race more than 2 months out — structure is useful, corridors are placeholder.',
  },
}

function ConfidenceBadge({ tier }: { tier: ConfidenceTier }) {
  const info = TIER_BADGE[tier] ?? TIER_BADGE.mid
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded-md text-[10px] font-bold border ${info.cls}`}
      title={info.tooltip}
    >
      {info.label}
    </span>
  )
}

function LegRow({ leg }: { leg: RacePlanLeg }) {
  const { t } = useTranslation()
  return (
    <div className="border-l-2 border-border pl-2 py-1">
      <div className="flex items-baseline gap-2 text-sm">
        <span className="font-semibold capitalize">{leg.leg}</span>
        {leg.distance && <span className="text-text-dim">{leg.distance}</span>}
      </div>
      <div className="text-xs tabular-nums mt-0.5">
        {leg.pacing.low} <span className="text-text-dim">→</span>{' '}
        <span className="font-semibold">{leg.pacing.target}</span>{' '}
        <span className="text-text-dim">→</span> {leg.pacing.cap}
        {leg.hr_ceiling_bpm && (
          <span className="text-text-dim ml-2">{t('race_plan.leg_hr_cap', { value: leg.hr_ceiling_bpm })}</span>
        )}
      </div>
      {leg.notes && <div className="text-[11px] text-text-dim mt-1">{leg.notes}</div>}
    </div>
  )
}

// Service errors shape: { error: string, ...optional fields like retry_after_sec }.
type ErrorState = { status: number; detail: { error?: string; [k: string]: unknown } | string | null }

function useErrorMessage() {
  const { t } = useTranslation()
  return (err: ErrorState): string => {
    // L2: 403 (demo locked out) gets a dedicated copy line — generic "HTTP 403"
    // confuses athletes who forgot they're on a demo token.
    if (err.status === 403) return t('race_plan.demo_blocked')
    const detail = err.detail
    if (typeof detail === 'string') return detail
    if (detail && typeof detail === 'object' && 'error' in detail && typeof detail.error === 'string') {
      return detail.error
    }
    return t('race_plan.request_failed', { status: err.status })
  }
}

function RateLimitNotice({
  detail,
}: {
  detail: { retry_after_sec?: number; next_available_at?: string }
}) {
  const { t } = useTranslation()
  // 429 detail carries retry_after_sec + next_available_at — render the latter
  // as a wall-clock time so athlete sees "next regen at HH:MM" not raw seconds.
  if (detail.next_available_at) {
    const dt = new Date(detail.next_available_at)
    const time = dt.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    return <span>{t('race_plan.rate_limit_with_time', { time })}</span>
  }
  if (detail.retry_after_sec) {
    const hours = Math.ceil(detail.retry_after_sec / 3600)
    return <span>{t('race_plan.rate_limit_with_hours', { hours })}</span>
  }
  return <span>{t('race_plan.rate_limit_generic')}</span>
}

export default function RacePlanPanel({ goalId }: { goalId: number }) {
  const { t } = useTranslation()
  const formatErrorMessage = useErrorMessage()

  const [plan, setPlan] = useState<RacePlanResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<ErrorState | null>(null)
  const [conditions, setConditions] = useState<RaceConditionsInput>({})
  // Hoisted form state — shared between the no-plan and has-plan render
  // branches of the panel so the conditions section stays open + the
  // inheritable dropdown stays loaded across the no-plan → has-plan
  // transition (after a successful Generate). See review N1.
  const [conditionsFormOpen, setConditionsFormOpen] = useState(false)
  const [inheritableRaces, setInheritableRaces] = useState<InheritableRace[] | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    apiFetch<RacePlanResponse>(`/api/race-plan?goal_id=${goalId}`)
      .then(p => setPlan(p))
      .catch((err: unknown) => {
        // 404 is the canonical "no plan yet" — leave plan=null, no error to user.
        if (err instanceof ApiError && err.status === 404) {
          setPlan(null)
          return
        }
        setError({
          status: err instanceof ApiError ? err.status : 0,
          // Non-ApiError branch: prefer Error.message, but fall back to String(err)
          // so the UI never shows blank when something non-Error gets thrown
          // (e.g. a string, a plain object, or AbortError without .message).
          detail:
            err instanceof ApiError
              ? (err.detail as ErrorState['detail'])
              : err instanceof Error
                ? err.message
                : String(err),
        })
      })
      .finally(() => setLoading(false))
  }, [goalId])

  const generate = async (forceRegen: boolean) => {
    setGenerating(true)
    setError(null)
    // PR2.5: only include race_conditions in the body when at least one field
    // is populated. Empty {} would still be valid (Pydantic strips Nones), but
    // omitting saves a few bytes and keeps the wire intent clear.
    const body: { goal_id: number; force_regen: boolean; race_conditions?: RaceConditionsInput } = {
      goal_id: goalId,
      force_regen: forceRegen,
    }
    if (conditions.elevation_gain_m != null || conditions.expected_temp_c != null) {
      body.race_conditions = conditions
    }
    try {
      const fresh = await apiFetch<RacePlanResponse>('/api/race-plan/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      setPlan(fresh)
    } catch (err: unknown) {
      setError({
        status: err instanceof ApiError ? err.status : 0,
        detail:
          err instanceof ApiError
            ? (err.detail as ErrorState['detail'])
            : err instanceof Error
              ? err.message
              : String(err),
      })
    } finally {
      setGenerating(false)
    }
  }

  if (loading) {
    return (
      <div className="bg-surface border border-border rounded-[14px] p-3 mb-3 text-center text-sm text-text-dim">
        {t('race_plan.loading')}
      </div>
    )
  }

  // No plan yet → invite generation. Surface inline error from a failed prior attempt.
  if (!plan) {
    return (
      <div className="bg-surface border border-border rounded-[14px] p-3 mb-3">
        <div className="flex items-center justify-between mb-2">
          <div className="text-sm font-semibold">{t('race_plan.title')}</div>
        </div>
        <div className="text-xs text-text-dim mb-3">{t('race_plan.intro')}</div>
        {error && (
          <div className="text-[11px] text-red-600 mb-2" role="status">
            <span aria-hidden="true">⚠ </span>
            {error.status === 429 ? (
              <RateLimitNotice
                detail={error.detail as { retry_after_sec?: number; next_available_at?: string }}
              />
            ) : (
              formatErrorMessage(error)
            )}
          </div>
        )}
        <RaceConditionsForm
          goalId={goalId}
          value={conditions}
          onChange={setConditions}
          open={conditionsFormOpen}
          onOpenChange={setConditionsFormOpen}
          inheritable={inheritableRaces}
          onInheritableLoaded={setInheritableRaces}
        />
        <button
          onClick={() => generate(false)}
          disabled={generating}
          className="w-full mt-3 py-2 bg-accent text-white rounded-md text-sm font-semibold disabled:opacity-50"
        >
          {generating ? t('race_plan.generating') : t('race_plan.generate_cta')}
        </button>
      </div>
    )
  }

  const inner = plan.payload.plan
  return (
    <div className="bg-surface border border-border rounded-[14px] p-3 mb-3">
      <div className="flex items-center justify-between mb-2">
        <div className="text-sm font-semibold">{t('race_plan.title')}</div>
        <ConfidenceBadge tier={plan.confidence_tier} />
      </div>

      {inner.headline && (
        <div className="text-sm italic text-text-dim mb-3 border-l-2 border-accent pl-2">
          "{inner.headline}"
        </div>
      )}

      <div className="text-[11px] uppercase font-semibold text-text-dim mt-3 mb-1">
        {t('race_plan.section_warmup')}
      </div>
      <div className="text-xs">{inner.warmup}</div>

      <div className="text-[11px] uppercase font-semibold text-text-dim mt-3 mb-1">
        {t('race_plan.section_legs')}
      </div>
      <div className="space-y-2">
        {/* L4: index keys are safe — `legs` is rendered top-to-bottom in API order. */}
        {inner.legs.map((leg, i) => (
          <LegRow key={i} leg={leg} />
        ))}
      </div>

      <div className="text-[11px] uppercase font-semibold text-text-dim mt-3 mb-1">
        {t('race_plan.section_fueling')}
      </div>
      <div className="text-xs">
        <span className="font-semibold tabular-nums">
          {t('race_plan.fueling_carbs', { value: inner.fueling.carbs_g_per_hour })}
        </span>
        {inner.fueling.fluid_ml_per_hour != null && (
          <span className="text-text-dim">
            {t('race_plan.fueling_fluid', { value: inner.fueling.fluid_ml_per_hour })}
          </span>
        )}
        {inner.fueling.sodium_mg_per_hour != null && (
          <span className="text-text-dim">
            {t('race_plan.fueling_sodium', { value: inner.fueling.sodium_mg_per_hour })}
          </span>
        )}
        {inner.fueling.notes && <div className="text-[11px] text-text-dim mt-1">{inner.fueling.notes}</div>}
      </div>

      {inner.transitions && inner.transitions.length > 0 && (
        <>
          <div className="text-[11px] uppercase font-semibold text-text-dim mt-3 mb-1">
            {t('race_plan.section_transitions')}
          </div>
          <div className="space-y-1 text-xs">
            {inner.transitions.map((tx, i) => (
              <div key={i}>
                <span className="font-semibold">{tx.name}:</span> {tx.checklist.join(' · ')}
                {tx.target_time_sec != null && (
                  <span className="text-text-dim">
                    {t('race_plan.leg_transition_target', { value: tx.target_time_sec })}
                  </span>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      <div className="text-[11px] uppercase font-semibold text-text-dim mt-3 mb-1">
        {t('race_plan.section_contingencies')}
      </div>
      <div className="space-y-1 text-xs">
        {/* L4: scenario-strings can collide (two "weather" plans) — index key is the safe default. */}
        {inner.contingencies.map((c, i) => (
          <div key={i}>
            <span className="font-semibold capitalize">{c.scenario}:</span> {c.plan}
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between text-[10px] text-text-dim mt-4 pt-2 border-t border-bg">
        <span>
          {t('race_plan.footer_generated', {
            when: plan.generated_at
              ? new Date(plan.generated_at).toLocaleString(undefined, {
                  month: 'short',
                  day: 'numeric',
                  hour: '2-digit',
                  minute: '2-digit',
                })
              : '',
            model: plan.model_version,
          })}
        </span>
      </div>

      {/* L1: surface the service-emitted note (e.g. "regenerated in place 1/1 today") so
          the athlete gets confirmation text, not just a silently-updated panel. */}
      {plan.note && <div className="text-[11px] text-text-dim mt-1 italic">{plan.note}</div>}

      <RaceConditionsForm
        goalId={goalId}
        value={conditions}
        onChange={setConditions}
        open={conditionsFormOpen}
        onOpenChange={setConditionsFormOpen}
        inheritable={inheritableRaces}
        onInheritableLoaded={setInheritableRaces}
      />

      {error && (
        <div className="text-[11px] text-red-600 mt-2" role="status">
          <span aria-hidden="true">⚠ </span>
          {error.status === 429 ? (
            <RateLimitNotice
              detail={error.detail as { retry_after_sec?: number; next_available_at?: string }}
            />
          ) : (
            formatErrorMessage(error)
          )}
        </div>
      )}

      <button
        onClick={() => generate(true)}
        disabled={generating || error?.status === 429}
        className="w-full mt-3 py-2 border border-border rounded-md text-sm disabled:opacity-50 hover:bg-bg"
      >
        {generating ? t('race_plan.regenerating') : t('race_plan.regenerate_cta')}
      </button>
    </div>
  )
}
