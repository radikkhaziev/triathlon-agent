import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import Layout from './Layout'
import { ToggleTile } from './halo'
import { apiFetch } from '../api/client'
import type { SportTag } from '../api/types'

const ALL_SPORTS: { tag: SportTag; color: string }[] = [
  { tag: 'swim', color: 'var(--color-amber)' },
  { tag: 'ride', color: 'var(--color-brand)' },
  { tag: 'run', color: 'var(--color-coral)' },
]

interface Props {
  /** Called with the canonical list returned by the server after a successful
   *  PUT. Parent (App) updates its `sports` state to release the gate. */
  onSaved: (sports: SportTag[]) => void
}

/**
 * Multi-select gate prompt (prototype `BSportsPicker`): step eyebrow,
 * left-aligned heading, sport ToggleTiles, hint, dynamic-count CTA.
 * Hides bottom tabs because the user is in pre-data state and tab navigation
 * would just bounce them back here.
 *
 * Starts with empty selection so the «pick a sport» action is visually
 * unambiguous: all tiles off, CTA disabled until at least one. Re-edit flow
 * lives on the Settings page. Logic (toggle/submit/onSaved/error) unchanged.
 */
export default function SportsPicker({ onSaved }: Props) {
  const { t } = useTranslation()
  const [selected, setSelected] = useState<Set<SportTag>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const toggle = (s: SportTag) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(s)) next.delete(s)
      else next.add(s)
      return next
    })
    if (error) setError(null)
  }

  const submit = async () => {
    const sports = Array.from(selected)
    if (sports.length === 0 || busy) return
    setBusy(true)
    setError(null)
    try {
      const result = await apiFetch<{ sports: SportTag[] }>('/api/auth/sports', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sports }),
      })
      // Component unmounts immediately after `onSaved` (App swaps the gate
      // for the data page), so leaving `busy=true` is harmless — no
      // ``setBusy(false)`` needed. If this picker is ever embedded
      // non-modally somewhere, restore the reset.
      onSaved(result.sports)
    } catch (e) {
      // Show the localized fallback by default. Surface ``e.message`` only
      // when it looks like a user-meaningful HTTP error from apiFetch
      // (``ApiError`` formats messages like "401: Not authenticated") —
      // raw ``Error`` instances often carry stack-trace fragments that are
      // noisy in the UI.
      const fallback = t('settings.sports.save_failed')
      const raw = e instanceof Error ? e.message : ''
      setError(raw && /^\d{3}:/.test(raw) ? raw : fallback)
      setBusy(false)
    }
  }

  const count = selected.size

  return (
    <Layout maxWidth="480px" hideBottomTabs>
      <div className="-mx-4 -mt-4 -mb-8 flex min-h-screen flex-col bg-halo-bg px-4 font-sans text-halo-ink">
        <div className="px-5 pt-10">
          <div className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-brand-dark">
            {t('sports_picker.step')}
          </div>
          <h1 className="mt-2 text-[26px] font-semibold leading-tight tracking-[-0.6px] text-halo-ink">
            {t('sports_picker.title')}
          </h1>
          <p className="mt-2 text-sm leading-relaxed text-halo-ink-dim">
            {t('sports_picker.description')}
          </p>
        </div>

        <div className="flex flex-col gap-2.5 px-4 pt-6">
          {ALL_SPORTS.map(({ tag, color }) => {
            const label = t(`settings.sports.${tag}`)
            return (
              <ToggleTile
                key={tag}
                label={label}
                color={color}
                on={selected.has(tag)}
                onToggle={() => toggle(tag)}
                initial={label.charAt(0).toUpperCase()}
                disabled={busy}
              />
            )
          })}
        </div>

        <div className="px-4 pt-2.5 text-center text-xs leading-relaxed text-halo-ink-dim">
          {t('sports_picker.hint')}
        </div>

        <div className="mt-auto px-4 pb-7 pt-5">
          <button
            type="button"
            onClick={submit}
            disabled={count === 0 || busy}
            className="flex w-full items-center justify-center gap-2 rounded-card border-none bg-halo-ink py-3.5 text-[15px] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60 font-sans"
          >
            {busy && <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />}
            {busy
              ? t('sports_picker.saving')
              : count === 0
                ? t('sports_picker.cta')
                : t('sports_picker.cta_count', { count })}
          </button>
          {error && <p className="mt-3 text-center text-[12px] text-halo-coral">{error}</p>}
        </div>
      </div>
    </Layout>
  )
}
