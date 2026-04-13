import Gauge from '../components/Gauge'
import Sparkline from '../components/Sparkline'
import TypingAnimation from '../components/TypingAnimation'
import { t, type TranslationKey } from '../i18n'

// Hardcoded representative data — no real fetch, just illustrative shapes.
const CTL_TREND = [35, 36, 38, 39, 41, 42, 42, 44, 46, 47, 48, 49, 50, 51, 51, 52, 52]
const DFA_TREND = [0.9, 1.1, 1.0, 0.85, 0.75, 0.7, 0.95, 1.2, 0.8, 0.65, 0.55, 0.9, 1.05, 0.75, 0.6]
const RACE_PROGRESS_PCT = 72

interface FeatureCard {
  titleKey: TranslationKey
  bodyKey: TranslationKey
  visual: React.ReactNode
}

export default function Features() {
  const cards: FeatureCard[] = [
    {
      titleKey: 'features_morning_title',
      bodyKey: 'features_morning_body',
      visual: <Gauge value={78} size={72} lineWidth={7} />,
    },
    {
      titleKey: 'features_load_title',
      bodyKey: 'features_load_body',
      visual: <Sparkline values={CTL_TREND} label="CTL 52" />,
    },
    {
      titleKey: 'features_dfa_title',
      bodyKey: 'features_dfa_body',
      visual: <Sparkline values={DFA_TREND} mode="dots" color="var(--green)" label="α1 0.75" />,
    },
    {
      titleKey: 'features_race_title',
      bodyKey: 'features_race_body',
      visual: <RaceProgressBar pct={RACE_PROGRESS_PCT} />,
    },
    {
      titleKey: 'features_ai_title',
      bodyKey: 'features_ai_body',
      visual: (
        <div className="flex items-center gap-2 text-xs text-text-dim">
          <TypingAnimation />
          <span>Claude</span>
        </div>
      ),
    },
    {
      titleKey: 'features_evening_title',
      bodyKey: 'features_evening_body',
      visual: (
        <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-text-dim bg-bg border border-border rounded-md px-2 py-1">
          🕘 21:00 daily
        </span>
      ),
    },
  ]

  return (
    <section className="max-w-4xl mx-auto px-6 py-12">
      <h2 className="text-xl font-bold text-center mb-8">{t('features_title')}</h2>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {cards.map((c) => (
          <article
            key={c.titleKey}
            className="bg-surface border border-border rounded-xl p-5 flex flex-col gap-3"
          >
            <div className="h-12 flex items-center">{c.visual}</div>
            <h3 className="text-base font-bold">{t(c.titleKey)}</h3>
            <p className="text-sm text-text-dim leading-relaxed">{t(c.bodyKey)}</p>
          </article>
        ))}
      </div>
    </section>
  )
}

function RaceProgressBar({ pct }: { pct: number }) {
  return (
    <div className="w-32">
      <div className="h-2 bg-bg rounded-full overflow-hidden border border-border">
        <div className="h-full bg-accent" style={{ width: `${pct}%` }} />
      </div>
      <div className="text-[11px] text-text-dim mt-1">CTL → target {pct}%</div>
    </div>
  )
}
