import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../auth/useAuth'
import EnduraiLogo from '../components/EnduraiLogo'

// No prototype counterpart for the public marketing page — restyled to the
// Halo token language (brand cobalt, surface/border tokens, BLogin radial
// backdrop). Composition is intentionally unchanged.
const BTN =
  'inline-flex items-center gap-2 px-7 py-3.5 rounded-chip text-[15px] font-semibold no-underline cursor-pointer transition-all max-[480px]:justify-center font-sans'
const BTN_PRIMARY = `${BTN} bg-halo-brand text-white border-none hover:opacity-90`
const BTN_SECONDARY = `${BTN} bg-halo-surface-2 text-halo-ink border border-halo-border hover:bg-halo-border`

export default function Landing() {
  const { t } = useTranslation()
  const { isAuthenticated, logout } = useAuth()
  const hasTg = !!window.Telegram?.WebApp?.initData
  const hasJwt = !!localStorage.getItem('auth_token')

  return (
    <div className="bg-halo-bg font-sans text-halo-ink">
      {/* Hero */}
      <section
        className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden px-6 py-10 text-center"
        style={{ background: 'radial-gradient(ellipse at top, var(--color-brand-light) 0%, var(--color-bg) 60%)' }}
      >
        <div className="mb-5 inline-flex items-center gap-1.5 rounded-pill border border-halo-border bg-halo-surface px-3.5 py-1.5 text-[13px] text-halo-ink-dim">
          <span className="h-2 w-2 rounded-full bg-halo-status-green" />
          Syncing with Intervals.icu
        </div>

        <div className="mb-6 flex justify-center">
          <EnduraiLogo height={72} />
        </div>
        <p className="mb-10 max-w-[480px] text-[clamp(16px,3vw,20px)] text-halo-ink-dim">
          Personal AI coach for triathletes. Analyzes HRV, training load and recovery — delivers actionable recommendations every morning.
        </p>

        <div className="flex flex-wrap justify-center gap-3 max-[480px]:flex-col max-[480px]:items-stretch">
          {isAuthenticated ? (
            <>
              <Link to="/" className={BTN_PRIMARY}>📊 Dashboard</Link>
              <Link to="/calendar" className={BTN_SECONDARY}>📋 Week</Link>
              <Link to="/wellness" className={BTN_SECONDARY}>💚 Wellness</Link>
              <a href="https://t.me/endurai_bot" className={BTN_SECONDARY}>💬 Open in Telegram</a>
              {hasJwt && !hasTg && (
                <button onClick={logout} className={`${BTN_SECONDARY} opacity-60`}>{t('settings.logout')}</button>
              )}
            </>
          ) : (
            <>
              <Link to="/login" className={BTN_PRIMARY}>{t('login.submit')}</Link>
              <a href="https://t.me/endurai_bot" className={BTN_PRIMARY}>💬 Open in Telegram</a>
            </>
          )}
        </div>
      </section>

      {/* Features */}
      <section className="mx-auto max-w-[960px] px-6 py-20">
        <h2 className="mb-12 text-center text-[28px] font-bold">Features</h2>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-5">
          {[
            { icon: '🟢', title: 'Morning Report', desc: 'Recovery score, HRV analysis (Flatt & Esco), sleep quality and RHR. AI-powered workout recommendation.' },
            { icon: '📈', title: 'Training Load', desc: 'CTL / ATL / TSB from Intervals.icu. Per-sport CTL for swim, bike and run. Ramp rate monitoring.' },
            { icon: '❤️‍🔥', title: 'DFA Alpha 1', desc: 'Post-activity HRV analysis: HRVT1/HRVT2 thresholds, Readiness (Ra), Durability (Da). Automatically processed from FIT files.' },
            { icon: '🏁', title: 'Race Progress', desc: 'Per-sport CTL tracking against target values. Countdown to race day with goal completion percentage.' },
            { icon: '🤖', title: 'Claude AI Analysis', desc: "Daily AI recommendation powered by Claude. Considers recovery, load, planned workouts and yesterday's DFA data." },
            { icon: '🌙', title: 'Evening Digest', desc: "Daily summary at 21:00 — completed workouts, DFA analysis, tomorrow's plan. Delivered via Telegram." },
          ].map(f => (
            <div key={f.title} className="rounded-card border border-halo-border bg-halo-surface p-7 shadow-card transition-colors hover:border-halo-brand">
              <div className="mb-4 text-[32px]">{f.icon}</div>
              <h3 className="mb-2 text-[17px] font-semibold">{f.title}</h3>
              <p className="text-sm leading-relaxed text-halo-ink-dim">{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* How it works */}
      <section className="mx-auto max-w-[720px] px-6 py-16 pb-20">
        <h2 className="mb-12 text-center text-[28px] font-bold">How It Works</h2>
        <div className="flex flex-col gap-6">
          {[
            { title: 'Data from Intervals.icu', desc: 'Wellness, activities and workout plans sync automatically every 10-30 minutes.' },
            { title: 'Analysis & Metrics', desc: 'Dual-algorithm HRV, RHR baselines, recovery score (0-100), DFA alpha 1 from FIT files, ESS/Banister model.' },
            { title: 'AI Recommendation', desc: 'Claude evaluates all data and delivers a specific recommendation: what to train, which zones, target volume.' },
            { title: 'Telegram + Dashboard', desc: 'Morning and evening reports via Telegram. Detailed analytics in the web dashboard.' },
          ].map((s, i) => (
            <div key={i} className="flex items-start gap-5">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-chip border border-halo-border bg-halo-surface-2 text-base font-bold text-halo-brand">
                {i + 1}
              </div>
              <div>
                <h3 className="mb-1 text-base font-semibold">{s.title}</h3>
                <p className="text-sm text-halo-ink-dim">{s.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Stack */}
      <section className="border-t border-halo-border px-6 py-16 text-center">
        <h2 className="mb-6 text-xl font-bold">Built With</h2>
        <div className="flex flex-wrap items-center justify-center gap-8">
          <div className="flex flex-col items-center gap-2 text-[13px] text-halo-ink-dim">
            <span className="text-[28px]">📊</span> Intervals.icu
          </div>
          <div className="flex flex-col items-center gap-2 text-[13px] text-halo-ink-dim">
            <span className="text-[28px]">🧠</span> Claude AI
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-halo-border px-6 py-8 text-center text-[13px] text-halo-ink-dim">
        EndurAI &copy; {new Date().getFullYear()} &middot;{' '}
        <a href="https://t.me/endurai_bot" className="text-halo-brand no-underline">@endurai_bot</a>
      </footer>
    </div>
  )
}
