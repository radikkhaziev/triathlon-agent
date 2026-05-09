import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import Layout from './Layout'
import { apiFetch } from '../api/client'
import type { SportTag } from '../api/types'

const ALL_SPORTS: { tag: SportTag; emoji: string }[] = [
  { tag: 'swim', emoji: '🏊' },
  { tag: 'ride', emoji: '🚴' },
  { tag: 'run', emoji: '🏃' },
]

interface Props {
  /** Called with the canonical list returned by the server after a successful
   *  PUT. Parent (App) updates its `sports` state to release the gate. */
  onSaved: (sports: SportTag[]) => void
}

/**
 * Multi-select gate prompt — vertical checkbox buttons for swim/ride/run.
 * Hides bottom tabs because the user is in pre-data state and tab navigation
 * would just bounce them back here. Mirrors OnboardingPrompt's full-screen
 * empty-state pattern.
 *
 * Starts with empty selection so the «pick a sport» action is visually
 * unambiguous: all buttons inactive, Save disabled until user clicks at
 * least one. Re-edit flow lives on the Settings page.
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

  return (
    <Layout maxWidth="480px" hideBottomTabs>
      <div className="flex flex-col items-center text-center px-6 py-12">
        <div aria-hidden="true" className="text-5xl mb-4">🏊‍♂️ 🚴 🏃</div>
        <h1 className="text-xl font-bold mb-3">{t('sports_picker.title')}</h1>
        <p className="text-sm text-text-dim leading-relaxed mb-8 max-w-[320px]">
          {t('sports_picker.description')}
        </p>

        <div className="flex flex-col gap-2 w-full max-w-[320px] mb-8">
          {ALL_SPORTS.map(({ tag, emoji }) => {
            const active = selected.has(tag)
            return (
              <button
                key={tag}
                type="button"
                onClick={() => toggle(tag)}
                aria-pressed={active}
                className={`w-full py-3 rounded-xl text-[15px] font-semibold border cursor-pointer transition-colors font-sans ${
                  active
                    ? 'bg-accent text-white border-accent'
                    : 'bg-surface border-border text-text hover:bg-surface-2'
                }`}
              >
                <span className="mr-2">{emoji}</span>
                {t(`settings.sports.${tag}`)}
              </button>
            )
          })}
        </div>

        <button
          type="button"
          onClick={submit}
          disabled={selected.size === 0 || busy}
          className="flex items-center justify-center gap-2 w-full max-w-[320px] py-3.5 bg-accent text-white text-center rounded-xl text-[15px] font-semibold border-none cursor-pointer font-sans disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {busy && <span className="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />}
          {busy ? t('sports_picker.saving') : t('sports_picker.cta')}
        </button>

        {error && (
          <p className="text-[12px] text-red mt-3 max-w-[320px]">{error}</p>
        )}
      </div>
    </Layout>
  )
}
