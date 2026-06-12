import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, apiFetch } from '../api/client'
import { useAuth } from '../auth/useAuth'
import type { InheritableRace, RaceConditionsInput, RacePlanInner, RacePlanLeg, RacePlanResponse } from '../api/types'
import DemoSampleBadge from './DemoSampleBadge'
import RaceConditionsForm from './RaceConditionsForm'

// Hand-written sample plan for demo sessions — the server stubs the real
// payload (its free-text is generated from private athlete context), so the
// panel shows the product's form on canned English data instead. English-only
// by design: demo language is pinned to "en" (docs/DEMO_PUBLIC_ACCESS_SPEC.md
// Phase 2; structured data stays a TSX constant rather than i18n keys).
const DEMO_SAMPLE_PLAN: RacePlanInner = {
  headline: 'Even pacing wins this course — hold back for the first 20 minutes.',
  warmup: 'Race morning: 10 min easy spin + 3×30s builds, then 5 min swim loosen-up ending 20 min before the start.',
  legs: [
    {
      leg: 'swim',
      distance: '1.9 km',
      pacing: { low: '1:55', target: '1:50', cap: '1:45' },
      notes: 'Settle through the first 200 m, then find feet to draft.',
    },
    {
      leg: 'bike',
      distance: '90 km',
      pacing: { low: '180 W', target: '195 W', cap: '210 W' },
      hr_ceiling_bpm: 152,
      notes: 'Cap power on the climbs — this race is won on the run.',
    },
    {
      leg: 'run',
      distance: '21.1 km',
      pacing: { low: '5:30', target: '5:15', cap: '5:00' },
      notes: 'First 3 km strictly easy, then settle at target pace.',
    },
  ],
  fueling: {
    carbs_g_per_hour: 80,
    fluid_ml_per_hour: 750,
    sodium_mg_per_hour: 600,
    notes: 'Start fueling at minute 15 on the bike, nothing solid after the bike.',
  },
  transitions: [],
  contingencies: [
    { scenario: 'hot day', plan: 'Add 250 ml/h of fluid and drop the run target by 10s/km.' },
    { scenario: 'HR spikes early on the bike', plan: 'Sit up for 5 min, eat, reassess at the next corridor check.' },
  ],
}

// Refresh icon for the "Recalculate plan" CTA (prototype `RacePlanCard`).
function RefreshIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 12a9 9 0 0 1 15.5-6.4L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-15.5 6.4L3 16" />
      <path d="M3 21v-5h5" />
    </svg>
  )
}

// Small inline button spinner — matches the design's generating state.
function Spinner({ light }: { light?: boolean }) {
  return (
    <span
      className={`h-3.5 w-3.5 animate-spin rounded-full border-2 ${
        light ? 'border-white/40 border-t-white' : 'border-halo-ink-dimmer border-t-halo-ink'
      }`}
      aria-hidden="true"
    />
  )
}

function LegRow({ leg }: { leg: RacePlanLeg }) {
  const { t } = useTranslation()
  // pacing is JSON-schema-required but legacy plans may lack it — guard.
  const pacing = leg.pacing
  return (
    <div className="rounded-[10px] border-l-[3px] border-halo-brand bg-halo-surface-2 px-3 py-2.5">
      <div className="flex items-baseline gap-2 text-sm">
        <span className="font-semibold capitalize">{leg.leg}</span>
        {leg.distance && <span className="text-halo-ink-dim">{leg.distance}</span>}
      </div>
      {pacing && (pacing.low || pacing.target || pacing.cap) && (
        <div className="mt-0.5 text-xs tabular-nums">
          {pacing.low} <span className="text-halo-ink-dim">→</span>{' '}
          <span className="font-semibold">{pacing.target}</span>{' '}
          <span className="text-halo-ink-dim">→</span> {pacing.cap}
          {leg.hr_ceiling_bpm && (
            <span className="ml-2 text-halo-ink-dim">{t('race_plan.leg_hr_cap', { value: leg.hr_ceiling_bpm })}</span>
          )}
        </div>
      )}
      {leg.notes && <div className="mt-1 text-[11px] text-halo-ink-dim">{leg.notes}</div>}
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

// Card pill — days-to-race (prototype `RacePlanCard`), or "EARLY" once a plan
// exists and the backend tier says it's early-stage (race > ~8 weeks out, so
// the corridors are provisional). English-only "EARLY" — race-day terminology
// is universal and short ALL-CAPS reads at a glance regardless of UI language.
function RacePill({ daysRemaining, isEarly }: { daysRemaining: number; isEarly: boolean }) {
  const { t } = useTranslation()
  if (isEarly) {
    return (
      <span
        className="shrink-0 whitespace-nowrap rounded-pill px-2 py-[3px] text-[10px] font-bold tracking-[0.5px]"
        style={{ background: 'color-mix(in srgb, var(--color-amber) 16%, transparent)', color: 'var(--color-amber)' }}
      >
        EARLY
      </span>
    )
  }
  return (
    <span className="shrink-0 whitespace-nowrap rounded-pill bg-halo-surface-2 px-2 py-[3px] text-[10px] font-bold tracking-[0.5px] text-halo-ink-dim">
      {t('race_plan.days_to_race', { days: daysRemaining })}
    </span>
  )
}

export default function RacePlanPanel({ goalId, daysRemaining }: { goalId: number; daysRemaining: number }) {
  const { t } = useTranslation()
  const { isDemo } = useAuth()
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
    // Demo renders the canned sample — no fetch (the server would stub the
    // payload anyway, see GET /api/race-plan demo branch).
    if (isDemo) {
      setLoading(false)
      return
    }
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
  }, [goalId, isDemo])

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
      <div className="rounded-card border border-halo-border bg-halo-surface p-[18px] text-center text-sm text-halo-ink-dim shadow-card">
        {t('race_plan.loading')}
      </div>
    )
  }

  // No plan yet → invite generation. Surface inline error from a failed prior attempt.
  // Demo never lands here — it renders the sample below regardless of plan state.
  if (!plan && !isDemo) {
    return (
      <div className="rounded-card border border-halo-border bg-halo-surface p-[18px] shadow-card">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-[15px] font-semibold tracking-[-0.2px]">{t('race_plan.title')}</div>
            <div className="mt-1 max-w-[280px] text-xs leading-relaxed text-halo-ink-dim">{t('race_plan.intro')}</div>
          </div>
          <RacePill daysRemaining={daysRemaining} isEarly={false} />
        </div>
        {error && (
          <div className="mt-2 text-[11px] text-halo-coral" role="status">
            <span aria-hidden="true">⚠ </span>
            {error.status === 429 ? (
              <RateLimitNotice detail={error.detail as { retry_after_sec?: number; next_available_at?: string }} />
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
          className="mt-3.5 flex w-full items-center justify-center gap-2 rounded-[12px] bg-halo-brand py-3 text-sm font-semibold text-white disabled:opacity-60"
        >
          {generating && <Spinner light />}
          {generating ? t('race_plan.generating') : t('race_plan.generate_cta')}
        </button>
      </div>
    )
  }

  // Defensive: payload is JSONB (no DB schema enforcement) and Claude can omit
  // optional sections even when JSON-schema marks them required (observed in
  // prod plan_id=1 where ``contingencies`` was absent). Treat every section
  // as optional in the renderer — better empty card than white-screen exception.
  const inner: Partial<RacePlanInner> = isDemo ? DEMO_SAMPLE_PLAN : (plan?.payload?.plan ?? {})
  const legs = inner.legs ?? []
  const fueling = inner.fueling
  const transitions = inner.transitions ?? []
  const contingencies = inner.contingencies ?? []
  return (
    <div className="rounded-card border border-halo-border bg-halo-surface p-[18px] shadow-card">
      <div className="flex items-start justify-between gap-3">
        <div className="text-[15px] font-semibold tracking-[-0.2px]">{t('race_plan.title')}</div>
        <RacePill daysRemaining={daysRemaining} isEarly={!isDemo && plan?.confidence_tier === 'early'} />
      </div>

      {isDemo && <DemoSampleBadge textKey="demo.race_plan_badge" />}

      {inner.headline && (
        <div className="mb-3 mt-3 border-l-2 border-halo-brand pl-2 text-sm italic text-halo-ink-dim">
          "{inner.headline}"
        </div>
      )}

      {inner.warmup && (
        <>
          <div className="mb-1 mt-3 text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
            {t('race_plan.section_warmup')}
          </div>
          <div className="text-xs">{inner.warmup}</div>
        </>
      )}

      {legs.length > 0 && (
        <>
          <div className="mb-1 mt-3 text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
            {t('race_plan.section_legs')}
          </div>
          <div className="space-y-2">
            {/* L4: index keys are safe — `legs` is rendered top-to-bottom in API order. */}
            {legs.map((leg, i) => (
              <LegRow key={i} leg={leg} />
            ))}
          </div>
        </>
      )}

      {fueling && fueling.carbs_g_per_hour != null && (
        <>
          <div className="mb-1 mt-3 text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
            {t('race_plan.section_fueling')}
          </div>
          <div className="text-xs">
            <span className="font-semibold tabular-nums">
              {t('race_plan.fueling_carbs', { value: fueling.carbs_g_per_hour })}
            </span>
            {fueling.fluid_ml_per_hour != null && (
              <span className="text-halo-ink-dim">
                {t('race_plan.fueling_fluid', { value: fueling.fluid_ml_per_hour })}
              </span>
            )}
            {fueling.sodium_mg_per_hour != null && (
              <span className="text-halo-ink-dim">
                {t('race_plan.fueling_sodium', { value: fueling.sodium_mg_per_hour })}
              </span>
            )}
            {fueling.notes && <div className="mt-1 text-[11px] text-halo-ink-dim">{fueling.notes}</div>}
          </div>
        </>
      )}

      {transitions.length > 0 && (
        <>
          <div className="mb-1 mt-3 text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
            {t('race_plan.section_transitions')}
          </div>
          <div className="space-y-1 text-xs">
            {transitions.map((tx, i) => (
              <div key={i}>
                <span className="font-semibold">{tx.name}:</span> {(tx.checklist ?? []).join(' · ')}
                {tx.target_time_sec != null && (
                  <span className="text-halo-ink-dim">
                    {t('race_plan.leg_transition_target', { value: tx.target_time_sec })}
                  </span>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {contingencies.length > 0 && (
        <>
          <div className="mb-1 mt-3 text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
            {t('race_plan.section_contingencies')}
          </div>
          <div className="space-y-1 text-xs">
            {/* L4: scenario-strings can collide (two "weather" plans) — index key is the safe default. */}
            {contingencies.map((c, i) => (
              <div key={i}>
                <span className="font-semibold capitalize">{c.scenario}:</span> {c.plan}
              </div>
            ))}
          </div>
        </>
      )}

      {/* Footer / conditions / regenerate are real-plan chrome — meaningless
          for the demo sample (and regenerate would 403 on a demo token). */}
      {!isDemo && plan && (
        <>
          <div className="mt-4 flex items-center justify-between border-t border-halo-border pt-2 text-[10px] text-halo-ink-dim">
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
          {plan.note && <div className="mt-1 text-[11px] italic text-halo-ink-dim">{plan.note}</div>}

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
            <div className="mt-2 text-[11px] text-halo-coral" role="status">
              <span aria-hidden="true">⚠ </span>
              {error.status === 429 ? (
                <RateLimitNotice detail={error.detail as { retry_after_sec?: number; next_available_at?: string }} />
              ) : (
                formatErrorMessage(error)
              )}
            </div>
          )}

          <button
            onClick={() => generate(true)}
            disabled={generating || error?.status === 429}
            className="mt-3.5 flex w-full items-center justify-center gap-2 rounded-[12px] border border-halo-border py-3 text-sm font-semibold text-halo-ink hover:bg-halo-surface-2 disabled:opacity-50"
          >
            {generating ? <Spinner /> : <RefreshIcon />}
            {generating ? t('race_plan.regenerating') : t('race_plan.regenerate_cta')}
          </button>
        </>
      )}
    </div>
  )
}
