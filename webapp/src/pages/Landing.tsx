import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../auth/useAuth'
import EnduraiLogo from '../components/EnduraiLogo'

export default function Landing() {
  const { t } = useTranslation()
  const { isAuthenticated, logout } = useAuth()
  const hasTg = !!window.Telegram?.WebApp?.initData
  const hasJwt = !!localStorage.getItem('auth_token')

  return (
    <>
      {/* Hero */}
      <section className="min-h-screen flex flex-col items-center justify-center text-center px-6 py-10 relative overflow-hidden">
        <div className="absolute top-[-40%] left-1/2 -translate-x-1/2 w-[600px] h-[600px] bg-[radial-gradient(circle,var(--accent-glow)_0%,transparent_70%)] pointer-events-none" />

        <div className="inline-flex items-center gap-1.5 bg-surface border border-border rounded-full px-3.5 py-1.5 text-[13px] text-text-dim mb-5">
          <span className="w-2 h-2 rounded-full bg-green" style={{ animation: 'pulse-dot 2s infinite' }} />
          Syncing with Intervals.icu
        </div>

        <div className="mb-6 flex justify-center">
          <EnduraiLogo height={72} />
        </div>
        <p className="text-[clamp(16px,3vw,20px)] text-text-dim max-w-[480px] mb-10">
          Personal AI coach for triathletes. Analyzes HRV, training load and recovery — delivers actionable recommendations every morning.
        </p>

        <div className="flex gap-3 flex-wrap justify-center max-[480px]:flex-col max-[480px]:items-stretch">
          {isAuthenticated ? (
            <>
              <Link to="/" className="btn-primary">📊 Dashboard</Link>
              <Link to="/plan" className="btn-secondary">📋 Training Plan</Link>
              <Link to="/activities" className="btn-secondary">🏃 Activities</Link>
              <Link to="/wellness" className="btn-secondary">💚 Wellness</Link>
              <a href="https://t.me/radikrunbot" className="btn-secondary">💬 Open in Telegram</a>
              {hasJwt && !hasTg && (
                <button onClick={logout} className="btn-secondary opacity-60">{t('settings.logout')}</button>
              )}
            </>
          ) : (
            <>
              <Link to="/login" className="btn-primary">{t('login.submit')}</Link>
              <a href="https://t.me/radikrunbot" className="btn-primary">💬 Open in Telegram</a>
            </>
          )}
        </div>
      </section>

      {/* Features */}
      <section className="max-w-[960px] mx-auto px-6 py-20">
        <h2 className="text-center text-[28px] font-bold mb-12">Features</h2>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-5">
          {[
            { icon: '🟢', title: 'Morning Report', desc: 'Recovery score, dual-algorithm HRV analysis (Flatt & Esco + AIEndurance), sleep quality and RHR. AI-powered workout recommendation.' },
            { icon: '📈', title: 'Training Load', desc: 'CTL / ATL / TSB from Intervals.icu. Per-sport CTL for swim, bike and run. Ramp rate monitoring.' },
            { icon: '❤️‍🔥', title: 'DFA Alpha 1', desc: 'Post-activity HRV analysis: HRVT1/HRVT2 thresholds, Readiness (Ra), Durability (Da). Automatically processed from FIT files.' },
            { icon: '🏁', title: 'Race Progress', desc: 'Per-sport CTL tracking against target values. Countdown to race day with goal completion percentage.' },
            { icon: '🤖', title: 'Claude AI Analysis', desc: "Daily AI recommendation powered by Claude. Considers recovery, load, planned workouts and yesterday's DFA data." },
            { icon: '🌙', title: 'Evening Digest', desc: "Daily summary at 21:00 — completed workouts, DFA analysis, tomorrow's plan. Delivered via Telegram." },
          ].map(f => (
            <div key={f.title} className="bg-surface border border-border rounded-2xl p-7 transition-colors hover:border-accent">
              <div className="text-[32px] mb-4">{f.icon}</div>
              <h3 className="text-[17px] font-semibold mb-2">{f.title}</h3>
              <p className="text-sm text-text-dim leading-relaxed">{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* How it works */}
      <section className="max-w-[720px] mx-auto px-6 py-16 pb-20">
        <h2 className="text-center text-[28px] font-bold mb-12">How It Works</h2>
        <div className="flex flex-col gap-6">
          {[
            { title: 'Data from Intervals.icu', desc: 'Wellness, activities and workout plans sync automatically every 10-30 minutes.' },
            { title: 'Analysis & Metrics', desc: 'Dual-algorithm HRV, RHR baselines, recovery score (0-100), DFA alpha 1 from FIT files, ESS/Banister model.' },
            { title: 'AI Recommendation', desc: 'Claude evaluates all data and delivers a specific recommendation: what to train, which zones, target volume.' },
            { title: 'Telegram + Dashboard', desc: 'Morning and evening reports via Telegram. Detailed analytics in the web dashboard.' },
          ].map((s, i) => (
            <div key={i} className="flex gap-5 items-start">
              <div className="shrink-0 w-10 h-10 rounded-[10px] bg-surface-2 border border-border flex items-center justify-center font-bold text-base text-accent">
                {i + 1}
              </div>
              <div>
                <h3 className="text-base font-semibold mb-1">{s.title}</h3>
                <p className="text-sm text-text-dim">{s.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Stack */}
      <section className="text-center px-6 py-16 border-t border-border">
        <h2 className="text-xl font-bold mb-6">Built With</h2>
        <div className="flex gap-8 justify-center items-center flex-wrap">
          <div className="flex flex-col items-center gap-2 text-text-dim text-[13px]">
            <span className="text-[28px]">📊</span> Intervals.icu
          </div>
          <div className="flex flex-col items-center gap-2 text-text-dim text-[13px]">
            <span className="text-[28px]">🧠</span> Claude AI
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="text-center px-6 py-8 text-text-dim text-[13px] border-t border-border">
        EndurAI &copy; {new Date().getFullYear()} &middot; <a href="https://t.me/radikrunbot" className="text-accent no-underline">@radikrunbot</a>
      </footer>

      <style>{`
        .btn-primary {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 14px 28px;
          border-radius: 12px;
          font-size: 15px;
          font-weight: 600;
          text-decoration: none;
          transition: all 0.2s;
          border: none;
          cursor: pointer;
          background: var(--accent);
          color: #fff;
          font-family: inherit;
        }
        .btn-primary:hover {
          background: #2563eb;
          transform: translateY(-1px);
          box-shadow: 0 8px 24px #3b82f640;
        }
        .btn-secondary {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 14px 28px;
          border-radius: 12px;
          font-size: 15px;
          font-weight: 600;
          text-decoration: none;
          transition: all 0.2s;
          cursor: pointer;
          background: var(--surface-2);
          color: var(--text);
          border: 1px solid var(--border);
          font-family: inherit;
        }
        .btn-secondary:hover {
          background: var(--border);
          transform: translateY(-1px);
        }
        @media (max-width: 480px) {
          .btn-primary, .btn-secondary { justify-content: center; }
        }
      `}</style>
    </>
  )
}
