import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, apiFetch } from '../api/client'
import type { InheritableConditionsResponse, InheritableRace, RaceConditionsInput } from '../api/types'

// PR2.5 / spec §11.10: optional course/weather inputs + "inherit from a past
// race" dropdown. Both inputs stay empty by default — they're hints to Claude,
// not hard requirements. Inheritance is opt-in via the dropdown; we do NOT
// auto-match by name (fragile, e.g. "Oceanlava 2024" vs "OceanLava Montenegro").

interface RaceConditionsFormProps {
  goalId: number
  value: RaceConditionsInput
  onChange: (next: RaceConditionsInput) => void
  // ``open`` + ``inheritable`` are HOISTED to the parent so two render-sites
  // of this form (no-plan vs has-plan branches in RacePlanPanel) share state.
  // Without this, the dropdown's open/loaded state resets on the no-plan →
  // has-plan transition (after a successful Generate). See review N1.
  // Optional — falls back to local state if parent doesn't manage them.
  open?: boolean
  onOpenChange?: (next: boolean) => void
  inheritable?: InheritableRace[] | null
  onInheritableLoaded?: (rows: InheritableRace[]) => void
}

export default function RaceConditionsForm({
  goalId,
  value,
  onChange,
  open: openProp,
  onOpenChange,
  inheritable: inheritableProp,
  onInheritableLoaded,
}: RaceConditionsFormProps) {
  const { t } = useTranslation()
  const [openLocal, setOpenLocal] = useState(false)
  const [inheritableLocal, setInheritableLocal] = useState<InheritableRace[] | null>(null)
  const open = openProp ?? openLocal
  const setOpen = (next: boolean) => (onOpenChange ? onOpenChange(next) : setOpenLocal(next))
  const inheritable = inheritableProp ?? inheritableLocal
  const setInheritable = (rows: InheritableRace[]) =>
    onInheritableLoaded ? onInheritableLoaded(rows) : setInheritableLocal(rows)
  const [loadingInherit, setLoadingInherit] = useState(false)

  // Lazy-load the dropdown options the first time the section opens. Avoids
  // a fetch on every Goal-tab mount when conditions UI isn't expanded.
  useEffect(() => {
    if (!open || inheritable !== null) return
    setLoadingInherit(true)
    apiFetch<InheritableConditionsResponse>(`/api/race-plan/inheritable-conditions?goal_id=${goalId}`)
      .then(resp => setInheritable(resp.races))
      .catch((err: unknown) => {
        // 404 (goal not found by then?) → empty list, not error toast.
        if (err instanceof ApiError && err.status === 404) {
          setInheritable([])
        } else {
          setInheritable([]) // fail-soft: form still works without inheritance
        }
      })
      .finally(() => setLoadingInherit(false))
  }, [open, goalId, inheritable])

  // Parse a temp value out of the freeform weather string (e.g. "sunny, 24°C").
  // Race row stores weather as text; we heuristically pull the FIRST integer
  // followed by °C / C / degrees within plausible race-day bounds. Misses are
  // OK — the user can still type the value manually. Review N2.
  const parseTempFromWeather = (weather: string | null): number | null => {
    if (!weather) return null
    const match = weather.match(/(-?\d+)\s*°?\s*C/i)
    if (!match) return null
    const n = parseInt(match[1], 10)
    return Number.isFinite(n) && n >= -50 && n <= 60 ? n : null
  }

  const handleInherit = (raceId: string) => {
    if (!raceId || !inheritable) return
    const picked = inheritable.find(r => String(r.id) === raceId)
    if (!picked) return
    // Only fill fields the past race actually carried — keep current value
    // for fields we can't recover (don't blank what the user already typed).
    const inferredTemp = parseTempFromWeather(picked.weather)
    onChange({
      elevation_gain_m: picked.elevation_gain_m ?? value.elevation_gain_m ?? null,
      expected_temp_c: inferredTemp ?? value.expected_temp_c ?? null,
    })
  }

  // Number-input handler that keeps null for empty (vs 0, which means flat
  // course / freezing temp — meaningfully different from "not specified").
  const handleNumberChange = (field: 'elevation_gain_m' | 'expected_temp_c', raw: string) => {
    const trimmed = raw.trim()
    if (!trimmed) {
      onChange({ ...value, [field]: null })
      return
    }
    const parsed = parseFloat(trimmed)
    if (!Number.isFinite(parsed)) return
    onChange({ ...value, [field]: parsed })
  }

  return (
    <div className="border-t border-bg pt-3 mt-3">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between text-[11px] text-text-dim hover:text-text"
      >
        <span>{t('race_plan.conditions.title')}</span>
        <span aria-hidden="true">{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className="mt-2 space-y-2">
          <div className="flex items-center gap-2">
            <label className="text-[11px] text-text-dim w-32">
              {t('race_plan.conditions.elevation_label')}
            </label>
            <input
              type="number"
              min={0}
              max={15000}
              step={50}
              value={value.elevation_gain_m ?? ''}
              onChange={e => handleNumberChange('elevation_gain_m', e.target.value)}
              className="flex-1 px-2 py-1 text-sm border border-border rounded bg-bg tabular-nums"
            />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-[11px] text-text-dim w-32">
              {t('race_plan.conditions.temp_label')}
            </label>
            <input
              type="number"
              min={-50}
              max={60}
              step={1}
              value={value.expected_temp_c ?? ''}
              onChange={e => handleNumberChange('expected_temp_c', e.target.value)}
              className="flex-1 px-2 py-1 text-sm border border-border rounded bg-bg tabular-nums"
            />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-[11px] text-text-dim w-32">
              {t('race_plan.conditions.inherit_label')}
            </label>
            <select
              onChange={e => handleInherit(e.target.value)}
              disabled={loadingInherit || (inheritable?.length === 0)}
              defaultValue=""
              className="flex-1 px-2 py-1 text-sm border border-border rounded bg-bg disabled:opacity-50"
            >
              <option value="" disabled>
                {loadingInherit
                  ? t('race_plan.conditions.inherit_loading')
                  : inheritable?.length === 0
                    ? t('race_plan.conditions.inherit_empty')
                    : t('race_plan.conditions.inherit_placeholder')}
              </option>
              {inheritable?.map(r => (
                <option key={r.id} value={r.id}>
                  {r.name}
                  {r.date ? ` · ${r.date}` : ''}
                  {r.elevation_gain_m ? ` · ${r.elevation_gain_m}m` : ''}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}
    </div>
  )
}
