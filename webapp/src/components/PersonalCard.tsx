import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

/**
 * Halo Personal card — prototype `BmPersonalRead`/`BmPersonalInline` +
 * `BpStepper`/`BpSource` + batch-save footer (direction-b-personal-edit.jsx).
 * Extracted from Settings.tsx so the new `/settings/personal/edit` focused
 * page (Halo-v3 prototype `Редактировать` affordance) reuses the same source
 * of truth — zero logic duplication.
 *
 * Age is the only writable field today (PATCH /api/athlete/profile);
 * Weight + per-sport HR-max are read-only with `BpSource` provenance badges.
 * The prototype's manual-override / popover-slider / 90d-history flow is
 * backend-blocked and deferred (G1=B precedent, see SPEC §10.4 story #4).
 */
export interface PersonalCardProps {
  age: number | null
  weight: number | null
  hrMax: { run?: number | null; bike?: number | null; swim?: number | null } | null
  disabled: boolean
  saveError: string | null
  onSaveAge: (next: number | null) => void
}

const AGE_MIN = 18
const AGE_MAX = 90
const clampAge = (n: number) => Math.max(AGE_MIN, Math.min(AGE_MAX, Math.round(n)))

// Source-provenance badge — only the two data-honest kinds we actually have:
// Weight ← wellness sample, HR-max ← Intervals auto-sync. `manual` kind unused
// (no manual write API).
function BpSource({ kind, label }: { kind: 'wellness' | 'intervals'; label: string }) {
  const m =
    kind === 'intervals'
      ? { wrap: 'bg-halo-brand-light text-halo-brand-dark', dot: 'var(--color-brand)' }
      : { wrap: 'text-halo-amber', dot: 'var(--color-amber)' }
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-pill px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.5px] ${m.wrap}`}
      style={kind === 'wellness' ? { background: '#fdf3e5' } : undefined}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: m.dot }} />
      {label}
    </span>
  )
}

/**
 * Renders the Age/Weight/HR-max rows + autosave footer **bare** — no card
 * chrome, no eyebrow. The caller wraps it (Settings does via `<Panel>`;
 * PersonalEdit gives it its own focused-page surround). Keeps the card a
 * single source of truth without forking chrome variants.
 */
export default function PersonalCard({
  age,
  weight,
  hrMax,
  disabled,
  saveError,
  onSaveAge,
}: PersonalCardProps) {
  const { t } = useTranslation()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(age != null ? String(age) : '')
  const [secs, setSecs] = useState(2)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!editing) setDraft(age != null ? String(age) : '')
  }, [age, editing])

  const parsed = draft.trim() === '' ? NaN : Number(draft)
  const valid = !Number.isNaN(parsed)
  const dirty = editing && valid && clampAge(parsed) !== age

  const stopEdit = () => setEditing(false)
  const commit = () => {
    if (!valid) return
    const c = clampAge(parsed)
    if (c !== age) onSaveAge(c)
    stopEdit()
  }
  const cancel = () => {
    setDraft(age != null ? String(age) : '')
    stopEdit()
  }
  const bump = (d: number) => {
    const base = valid ? clampAge(parsed) : age ?? AGE_MIN
    setDraft(String(clampAge(base + d)))
    setEditing(true)
  }

  // Autosave: 2s timeout that resets on every edit; a display-only interval
  // ticks the countdown. Restarts whenever `draft`/`editing` change; cleared
  // on Save/Cancel (dirty → false). No side-effect inside a setState updater.
  useEffect(() => {
    if (!dirty) {
      setSecs(2)
      return
    }
    setSecs(2)
    const tick = setInterval(() => setSecs(s => (s > 0 ? s - 1 : 0)), 1000)
    const save = setTimeout(commit, 2000)
    return () => {
      clearInterval(tick)
      clearTimeout(save)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, editing])

  const enterEdit = () => {
    if (disabled) return
    setEditing(true)
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  return (
    <>
      {/* Age — the only editable field */}
      <div className="flex items-center justify-between gap-4 border-b border-halo-border py-3.5">
        <div className="min-w-0">
          <div className="text-[15px] font-semibold tracking-[-0.1px] text-halo-ink">{t('settings.profile.age')}</div>
          <div className="mt-px text-[12px] text-halo-ink-dim">
            {editing ? t('settings.profile.age_sub_edit') : t('settings.profile.age_sub')}
          </div>
        </div>
        {editing ? (
          <div
            className="inline-flex items-stretch overflow-hidden rounded-[10px] border-[1.5px] border-halo-brand bg-halo-surface"
            style={{ boxShadow: '0 0 0 3px color-mix(in srgb, var(--color-brand) 22%, transparent)' }}
          >
            <input
              ref={inputRef}
              value={draft}
              inputMode="numeric"
              onChange={e => setDraft(e.target.value.replace(/\D/g, '').slice(0, 3))}
              onKeyDown={e => {
                if (e.key === 'Enter') commit()
                else if (e.key === 'Escape') cancel()
              }}
              className="w-16 border-0 bg-transparent px-2.5 py-2 text-right text-[18px] font-semibold tracking-[-0.2px] text-halo-ink outline-none font-sans"
            />
            <div className="flex flex-col border-l border-halo-border">
              <button
                type="button"
                onClick={() => bump(1)}
                aria-label="+1"
                className="flex w-6 flex-1 items-center justify-center border-b border-halo-border text-[9px] text-halo-ink-dim"
              >
                ▲
              </button>
              <button
                type="button"
                onClick={() => bump(-1)}
                aria-label="-1"
                className="flex w-6 flex-1 items-center justify-center text-[9px] text-halo-ink-dim"
              >
                ▼
              </button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            onClick={enterEdit}
            disabled={disabled}
            aria-label={`${t('settings.profile.age')} ${age ?? '—'}`}
            className="inline-flex items-baseline gap-1.5 rounded-lg px-2.5 py-1.5 disabled:cursor-not-allowed"
          >
            <span className="text-[20px] font-semibold tracking-[-0.3px] text-halo-ink">{age ?? '—'}</span>
            <span className="text-sm text-halo-ink-dimmer">›</span>
          </button>
        )}
      </div>

      {weight != null && (
        <div className="flex items-center justify-between gap-4 border-b border-halo-border py-3.5">
          <div className="min-w-0">
            <div className="text-[15px] font-semibold tracking-[-0.1px] text-halo-ink">{t('settings.profile.weight')}</div>
            <div className="mt-px text-[12px] text-halo-ink-dim">{t('settings.profile.weight_sub')}</div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <BpSource kind="wellness" label={t('settings.profile.src_wellness')} />
            <span className="text-[20px] font-semibold tracking-[-0.3px] text-halo-ink">
              {weight.toFixed(1)}
              <span className="ml-1 text-[11px] font-medium text-halo-ink-dim">kg</span>
            </span>
          </div>
        </div>
      )}

      {hrMax && (hrMax.swim != null || hrMax.bike != null || hrMax.run != null) && (
        <div className="pt-3.5">
          <div className="flex items-center justify-between gap-4">
            <div className="min-w-0">
              <div className="text-[15px] font-semibold tracking-[-0.1px] text-halo-ink">{t('settings.profile.hr_max')}</div>
              <div className="mt-px text-[12px] text-halo-ink-dim">{t('settings.profile.hr_max_sub')}</div>
            </div>
            <BpSource kind="intervals" label={t('settings.profile.src_intervals')} />
          </div>
          <div className="mt-2.5 grid grid-cols-3 gap-2">
            {([['swim', 'Swim'], ['bike', 'Bike'], ['run', 'Run']] as const).map(([k, en]) => (
              <div key={k} className="rounded-xl bg-halo-surface-2 px-3 py-3">
                {/* English by request — not localized to Плав/Вело/Бег. */}
                <div className="text-[9px] font-bold uppercase tracking-[0.5px] text-halo-ink-dim">{en}</div>
                <div className="mt-1 text-[19px] font-semibold tracking-[-0.3px] text-halo-ink">
                  {hrMax[k] ?? '—'}{' '}
                  <span className="text-[10px] font-medium text-halo-ink-dim">bpm</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {saveError && <div className="mt-3 text-[12px] text-halo-coral">{saveError}</div>}

      {dirty && (
        <div className="mt-4 flex items-center justify-between gap-3 border-t border-halo-border pt-3.5">
          <span className="text-[12px] text-halo-ink-dim">
            {t('settings.profile.autosave', { count: 1, sec: secs })}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={cancel}
              className="rounded-[10px] border border-halo-border bg-halo-surface px-3.5 py-2 text-[13px] font-semibold text-halo-ink-dim cursor-pointer font-sans"
            >
              {t('settings.profile.cancel')}
            </button>
            <button
              type="button"
              onClick={commit}
              className="rounded-[10px] border-none bg-halo-brand px-[18px] py-2 text-[13px] font-semibold text-white cursor-pointer font-sans"
            >
              {t('settings.profile.save')}
            </button>
          </div>
        </div>
      )}
    </>
  )
}
