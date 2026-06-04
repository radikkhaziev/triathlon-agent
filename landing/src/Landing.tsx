// ── Endurai public landing · "Спокойный" (calm / premium) ───────────────
// Ported 1:1 from the Claude Design handoff (final revision q3xdSpTQ...).
// Rendered with inline styles to stay pixel-faithful to the prototype.
// This is the public endurai.me site (served by the `landing` nginx service).
import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import { BRAND, COPY } from './landingContent'
import type { Lang, LandingCopy } from './landingContent'
import { PhoneFrame, AppScreen } from './components/PhoneMockups'

const TG_URL = 'https://t.me/endurai_bot'
const GITHUB_URL = 'https://github.com/radikkhaziev/triathlon-agent'
const DEMO_URL = 'https://bot.endurai.me/login'

function useWide(bp = 900) {
  const [w, setW] = useState(() => (typeof window !== 'undefined' ? window.innerWidth >= bp : false))
  useEffect(() => {
    const f = () => setW(window.innerWidth >= bp)
    window.addEventListener('resize', f)
    f()
    return () => window.removeEventListener('resize', f)
  }, [bp])
  return w
}

// Default is always English; Russian only on explicit user request (the nav
// toggle persists the choice). Browser locale is intentionally NOT used.
function detectLang(): Lang {
  if (typeof window === 'undefined') return 'en'
  return window.localStorage.getItem('en_lang') === 'ru' ? 'ru' : 'en'
}

// ── shared brand bits ───────────────────────────────────────────────────

function Wordmark({ tone = BRAND.ink, size = 19, icon = 26 }: { tone?: string; size?: number; icon?: number }) {
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
      <img src="/endurai-icon.png" alt="" width={icon} height={icon} style={{ display: 'block', borderRadius: icon * 0.26 }} />
      <span style={{ fontSize: size, fontWeight: 800, letterSpacing: -0.5, color: tone }}>Endurai</span>
    </div>
  )
}

function Footer({ t }: { t: LandingCopy }) {
  const fg = BRAND.ink
  const dim = BRAND.dim
  return (
    <footer style={{ padding: '36px 24px 44px', borderTop: `1px solid ${BRAND.border}` }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 16 }}>
        <div>
          <Wordmark tone={fg} />
          <div style={{ fontSize: 12.5, color: dim, marginTop: 8 }}>{t.foot_tag}</div>
        </div>
        <div style={{ display: 'flex', gap: 18, fontSize: 13, fontWeight: 600 }}>
          <a href="#demo" style={{ color: fg, textDecoration: 'none' }}>{t.foot_demo}</a>
          <a href={TG_URL} target="_blank" rel="noopener noreferrer" style={{ color: fg, textDecoration: 'none' }}>{t.foot_tg}</a>
        </div>
      </div>
      <div style={{ fontSize: 12, color: dim, marginTop: 26 }}>{t.foot_rights}</div>
    </footer>
  )
}

// ── style atoms ─────────────────────────────────────────────────────────

const aBtnPrimary: CSSProperties = {
  display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8,
  background: BRAND.ink, color: '#fff', fontWeight: 600, fontSize: 15,
  padding: '15px 26px', borderRadius: 999, border: 'none', cursor: 'pointer',
  fontFamily: BRAND.sans, textDecoration: 'none',
  boxShadow: '0 12px 28px -12px rgba(10,13,24,0.5)',
}
const aBtnGhost: CSSProperties = {
  display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8,
  background: 'transparent', color: BRAND.ink, fontWeight: 600, fontSize: 15,
  padding: '15px 26px', borderRadius: 999, border: `1.5px solid ${BRAND.border}`,
  cursor: 'pointer', fontFamily: BRAND.sans, textDecoration: 'none',
}
const aKicker: CSSProperties = {
  fontSize: 12, fontWeight: 700, letterSpacing: 1.4, textTransform: 'uppercase', color: BRAND.cobalt,
}
const osMono = '"SF Mono", ui-monospace, "JetBrains Mono", Menlo, monospace'

// ── MCP config terminal card ────────────────────────────────────────────

function AMcpCard({ label, note, wide }: { label: string; note: string; wide: boolean }) {
  const K = '#8fb0ff', S = '#86e3a8', P = 'rgba(255,255,255,0.45)'
  return (
    <div style={{ background: BRAND.ink, borderRadius: 18, overflow: 'hidden', border: '1px solid rgba(255,255,255,0.08)',
      boxShadow: '0 24px 60px -28px rgba(10,13,24,0.55)', marginTop: wide ? 0 : 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '11px 14px', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
        <span style={{ display: 'flex', gap: 6 }}>
          {['#ff5f57', '#febc2e', '#28c840'].map((c) => <span key={c} style={{ width: 10, height: 10, borderRadius: 5, background: c }}/>)}
        </span>
        <span style={{ fontFamily: osMono, fontSize: 11.5, color: 'rgba(255,255,255,0.5)', marginLeft: 4 }}>{label}</span>
      </div>
      <pre style={{ margin: 0, padding: '16px 18px', fontFamily: osMono, fontSize: 12.5, lineHeight: 1.65, color: 'rgba(255,255,255,0.82)', overflowX: 'auto' }}>
<span style={{ color: P }}>{'{'}</span>{'\n'}
{'  '}<span style={{ color: K }}>"mcpServers"</span><span style={{ color: P }}>{': {'}</span>{'\n'}
{'    '}<span style={{ color: K }}>"endurai"</span><span style={{ color: P }}>{': {'}</span>{'\n'}
{'      '}<span style={{ color: K }}>"url"</span><span style={{ color: P }}>{': '}</span><span style={{ color: S }}>"https://endurai.me/mcp"</span><span style={{ color: P }}>{','}</span>{'\n'}
{'      '}<span style={{ color: K }}>"headers"</span><span style={{ color: P }}>{': {'}</span>{'\n'}
{'        '}<span style={{ color: K }}>"Authorization"</span><span style={{ color: P }}>{': '}</span><span style={{ color: S }}>{'"Bearer <your-token>"'}</span>{'\n'}
{'      '}<span style={{ color: P }}>{'}'}</span>{'\n'}
{'    '}<span style={{ color: P }}>{'}'}</span>{'\n'}
{'  '}<span style={{ color: P }}>{'}'}</span>{'\n'}
<span style={{ color: P }}>{'}'}</span>
      </pre>
      {note && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '11px 16px', borderTop: '1px solid rgba(255,255,255,0.08)',
          fontFamily: osMono, fontSize: 11, color: 'rgba(255,255,255,0.55)' }}>
          <span aria-hidden="true">🔑</span>{note}
        </div>
      )}
    </div>
  )
}

// ── section pieces ──────────────────────────────────────────────────────

function AHowStep({ s }: { s: LandingCopy['how_steps'][number] }) {
  return (
    <div style={{ display: 'flex', gap: 16, padding: '20px 0', borderBottom: `1px solid ${BRAND.border}` }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: BRAND.cobalt, letterSpacing: 1, paddingTop: 3, width: 24, flexShrink: 0 }}>{s.n}</div>
      <div>
        <div style={{ fontSize: 17, fontWeight: 600, letterSpacing: -0.3 }}>{s.t}</div>
        <div style={{ fontSize: 14, lineHeight: 1.55, color: BRAND.dim, marginTop: 6, textWrap: 'pretty' }}>{s.d}</div>
      </div>
    </div>
  )
}

function AFeature({ f }: { f: LandingCopy['feats'][number] }) {
  return (
    <div style={{ background: '#fff', borderRadius: 22, padding: 22, border: `1px solid ${BRAND.border}`,
      boxShadow: '0 1px 2px rgba(10,13,24,0.03)', height: '100%' }}>
      <div style={{ display: 'inline-block', fontSize: 10.5, fontWeight: 700, letterSpacing: 0.5,
        textTransform: 'uppercase', color: BRAND.cobaltDark, background: BRAND.cobaltLite,
        padding: '4px 9px', borderRadius: 999 }}>{f.tag}</div>
      <div style={{ fontSize: 18, fontWeight: 600, marginTop: 14, letterSpacing: -0.3 }}>{f.t}</div>
      <div style={{ fontSize: 14, lineHeight: 1.55, color: BRAND.dim, marginTop: 7, textWrap: 'pretty' }}>{f.d}</div>
    </div>
  )
}

function AChat({ chat }: { chat: LandingCopy['tg_chat'] }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {chat.map((m, i) => (
        <div key={i} style={{ alignSelf: m.who === 'me' ? 'flex-end' : 'flex-start', maxWidth: '86%' }}>
          <div style={{
            fontSize: 13.5, lineHeight: 1.5, padding: '11px 14px',
            borderRadius: m.who === 'me' ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
            background: m.who === 'me' ? BRAND.cobalt : '#fff',
            color: m.who === 'me' ? '#fff' : BRAND.ink,
            border: m.who === 'me' ? 'none' : `1px solid ${BRAND.border}`,
            boxShadow: m.who === 'me' ? '0 8px 20px -10px rgba(59,109,255,0.5)' : '0 1px 2px rgba(10,13,24,0.04)',
            textWrap: 'pretty',
          }}>{m.text}</div>
        </div>
      ))}
    </div>
  )
}

// ── page ────────────────────────────────────────────────────────────────

export default function Landing() {
  const [lang, setLang] = useState<Lang>(detectLang)
  const wide = useWide(900)
  const t = COPY[lang]

  useEffect(() => {
    window.localStorage.setItem('en_lang', lang)
    document.documentElement.lang = lang
  }, [lang])

  const toggleLang = () => setLang((l) => (l === 'ru' ? 'en' : 'ru'))

  const pad = wide ? '0 48px' : '0 24px'
  const sec = (extra: CSSProperties = {}): CSSProperties => ({ padding: pad, ...extra })
  const h2: CSSProperties = { fontSize: wide ? 34 : 28, fontWeight: 700, letterSpacing: -0.8, textWrap: 'balance' }

  return (
    <div style={{ minHeight: '100%', background: BRAND.warm }}>
      <div style={{ width: '100%', maxWidth: wide ? 1180 : 460, margin: '0 auto', minHeight: '100%',
        background: BRAND.warm, color: BRAND.ink, fontFamily: BRAND.sans, position: 'relative', overflow: 'hidden' }}>
        {/* halo */}
        <div style={{ position: 'absolute', top: wide ? -200 : -120, left: wide ? '32%' : '50%', transform: 'translateX(-50%)',
          width: wide ? 720 : 520, height: wide ? 720 : 520, borderRadius: '50%', pointerEvents: 'none',
          background: 'radial-gradient(circle, rgba(59,109,255,0.16), rgba(59,109,255,0) 62%)' }}/>

        {/* nav */}
        <div style={{ ...sec(), display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          paddingTop: 22, paddingBottom: 10, position: 'relative', zIndex: 2 }}>
          <Wordmark tone={BRAND.ink} />
          <div style={{ display: 'flex', alignItems: 'center', gap: wide ? 26 : 10 }}>
            {wide && (
              <div style={{ display: 'flex', gap: 26, fontSize: 14, fontWeight: 500, color: BRAND.dim }}>
                <a href="#how" style={{ color: 'inherit', textDecoration: 'none' }}>{t.nav_how}</a>
                <a href="#features" style={{ color: 'inherit', textDecoration: 'none' }}>{t.nav_features}</a>
              </div>
            )}
            <button onClick={toggleLang} aria-label={lang === 'ru' ? 'Switch to English' : 'Переключить на русский'}
              style={{ fontSize: 12.5, fontWeight: 700, letterSpacing: 0.5, color: BRAND.ink,
                background: '#fff', border: `1px solid ${BRAND.border}`, padding: '9px 13px', borderRadius: 999,
                cursor: 'pointer', fontFamily: BRAND.sans }}>{lang === 'ru' ? 'EN' : 'RU'}</button>
            <a href="#demo" style={{ fontSize: 13, fontWeight: 600, color: '#fff', textDecoration: 'none', whiteSpace: 'nowrap',
              background: BRAND.ink, border: `1px solid ${BRAND.ink}`, padding: '9px 16px', borderRadius: 999 }}>{t.nav_demo}</a>
          </div>
        </div>

        {/* hero */}
        <div style={{ ...sec(), paddingTop: wide ? 40 : 26, position: 'relative', zIndex: 2 }}>
          <div style={wide
            ? { display: 'grid', gridTemplateColumns: '1.04fr 0.96fr', gap: 56, alignItems: 'center' }
            : { textAlign: 'center' }}>
            <div style={{ textAlign: wide ? 'left' : 'center' }}>
              <div style={aKicker}>{t.hero_eyebrow}</div>
              <h1 style={{ fontSize: wide ? 56 : 'clamp(34px, 9vw, 44px)', lineHeight: 1.02, fontWeight: 700,
                letterSpacing: -1.6, margin: '16px 0 0', textWrap: 'balance' }}>
                {t.hero_title[0]}<br/><span style={{ color: BRAND.cobalt }}>{t.hero_title[1]}</span>
              </h1>
              <p style={{ fontSize: wide ? 18 : 16.5, lineHeight: 1.55, color: BRAND.dim, margin: '18px 0 0',
                maxWidth: 420, marginLeft: wide ? 0 : 'auto', marginRight: wide ? 0 : 'auto', textWrap: 'pretty' }}>{t.hero_lead}</p>
              <div style={{ display: 'flex', flexDirection: wide ? 'row' : 'column', gap: 12, marginTop: 28,
                maxWidth: wide ? 'none' : 340, marginLeft: wide ? 0 : 'auto', marginRight: wide ? 0 : 'auto',
                flexWrap: 'wrap', justifyContent: wide ? 'flex-start' : 'stretch' }}>
                <a href={DEMO_URL} style={{ ...aBtnPrimary, width: wide ? 'auto' : '100%' }}>{t.cta_demo} →</a>
                <a href={TG_URL} target="_blank" rel="noopener noreferrer" style={{ ...aBtnGhost, width: wide ? 'auto' : '100%' }}>{t.cta_tg}</a>
              </div>
              <div style={{ fontSize: 12.5, color: BRAND.dim, marginTop: 14, textAlign: wide ? 'left' : 'center' }}>{t.cta_demo_sub}</div>
            </div>
            <div style={{ display: 'flex', justifyContent: 'center', marginTop: wide ? 0 : 40, paddingBottom: 10 }}>
              <PhoneFrame tone="#0a0d18" scale={wide ? 1.04 : 1}>
                <AppScreen which="recovery" />
              </PhoneFrame>
            </div>
          </div>
        </div>

        {/* sources */}
        <div style={{ ...sec(), paddingTop: wide ? 64 : 44, textAlign: 'center' }}>
          <div style={{ fontSize: 12.5, color: BRAND.dim, fontWeight: 600 }}>{t.proof_sub}</div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 11, marginTop: 16 }}>
            <img src="/intervals-icu-logo.png" alt="" style={{ width: wide ? 32 : 28, height: wide ? 32 : 28, display: 'block' }} />
            <span style={{ fontSize: wide ? 21 : 18, fontWeight: 700, color: BRAND.ink, letterSpacing: -0.3 }}>Intervals.icu</span>
          </div>
        </div>

        {/* how */}
        <div id="how" style={{ ...sec(), paddingTop: wide ? 96 : 64 }}>
          <div style={{ textAlign: wide ? 'center' : 'left' }}>
            <div style={aKicker}>{t.how_kicker}</div>
            <h2 style={{ ...h2, margin: '12px 0 8px' }}>{t.how_title}</h2>
          </div>
          <div style={{ marginTop: 18, borderTop: `1px solid ${BRAND.border}`,
            display: wide ? 'grid' : 'block', gridTemplateColumns: wide ? '1fr 1fr' : 'none', columnGap: 56,
            maxWidth: wide ? 980 : 'none', marginLeft: 'auto', marginRight: 'auto' }}>
            {t.how_steps.map((s) => <AHowStep key={s.n} s={s} />)}
          </div>
        </div>

        {/* features */}
        <div id="features" style={{ ...sec(), paddingTop: wide ? 96 : 64 }}>
          <div style={{ textAlign: wide ? 'center' : 'left' }}>
            <div style={aKicker}>{t.feat_kicker}</div>
            <h2 style={{ ...h2, margin: '12px 0 24px' }}>{t.feat_title}</h2>
          </div>
          <div style={{ display: 'grid', gap: wide ? 16 : 12, gridTemplateColumns: wide ? 'repeat(3,1fr)' : '1fr' }}>
            {t.feats.map((f) => <AFeature key={f.t} f={f} />)}
          </div>
        </div>

        {/* telegram */}
        <div style={{ marginTop: wide ? 96 : 64, background: '#fff', borderTop: `1px solid ${BRAND.border}`, borderBottom: `1px solid ${BRAND.border}` }}>
          <div style={{ ...sec(), paddingTop: wide ? 80 : 56, paddingBottom: wide ? 80 : 56 }}>
            <div style={wide ? { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 56, alignItems: 'center' } : {}}>
              <div>
                <div style={aKicker}>{t.tg_kicker}</div>
                <h2 style={{ ...h2, margin: '12px 0 12px' }}>{t.tg_title}</h2>
                <p style={{ fontSize: 15.5, lineHeight: 1.6, color: BRAND.dim, textWrap: 'pretty' }}>{t.tg_lead}</p>
                <ul style={{ listStyle: 'none', padding: 0, margin: '20px 0 0', display: 'grid', gap: 12 }}>
                  {t.tg_bullets.map((b) => (
                    <li key={b} style={{ display: 'flex', gap: 11, fontSize: 14.5, lineHeight: 1.5, color: BRAND.ink }}>
                      <span style={{ color: BRAND.cobalt, fontWeight: 800, flexShrink: 0 }} aria-hidden="true">✓</span>{b}
                    </li>
                  ))}
                </ul>
              </div>
              <div style={{ background: BRAND.warm, borderRadius: 22, padding: 18, border: `1px solid ${BRAND.border}`, marginTop: wide ? 0 : 28 }}>
                <AChat chat={t.tg_chat} />
              </div>
            </div>
          </div>
        </div>

        {/* screens */}
        <div style={{ ...sec(), paddingTop: wide ? 96 : 64, textAlign: 'center' }}>
          <div style={aKicker}>{t.screens_kicker}</div>
          <h2 style={{ ...h2, margin: '12px 0 30px' }}>{t.screens_title}</h2>
          <div style={{ display: 'flex', gap: wide ? 28 : 16, overflowX: wide ? 'visible' : 'auto',
            padding: '4px 4px 18px', scrollSnapType: 'x mandatory', justifyContent: wide ? 'center' : 'flex-start' }}>
            {(['recovery', 'plan', 'activity'] as const).map((w, i) => (
              <div key={w} style={{ scrollSnapAlign: 'center', flexShrink: 0, textAlign: 'center' }}>
                <PhoneFrame tone="#0a0d18" scale={0.92}>
                  <AppScreen which={w} />
                </PhoneFrame>
                <div style={{ fontSize: 12.5, fontWeight: 600, color: BRAND.dim, marginTop: 14 }}>{t.screen_labels[i]}</div>
              </div>
            ))}
          </div>
        </div>

        {/* open source / MCP */}
        <div style={{ ...sec(), paddingTop: wide ? 96 : 64 }}>
          <div style={wide ? { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 56, alignItems: 'center' } : {}}>
            <div>
              <div style={aKicker}>{t.os_kicker}</div>
              <h2 style={{ ...h2, margin: '12px 0 12px' }}>{t.os_title}</h2>
              <p style={{ fontSize: 15.5, lineHeight: 1.6, color: BRAND.dim, textWrap: 'pretty' }}>{t.os_lead}</p>
              <ul style={{ listStyle: 'none', padding: 0, margin: '20px 0 0', display: 'grid', gap: 12 }}>
                {t.os_bullets.map((b) => (
                  <li key={b} style={{ display: 'flex', gap: 11, fontSize: 14.5, lineHeight: 1.5, color: BRAND.ink }}>
                    <span style={{ color: BRAND.cobalt, fontWeight: 800, flexShrink: 0 }} aria-hidden="true">✓</span>{b}
                  </li>
                ))}
              </ul>
              <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer"
                style={{ display: 'inline-flex', alignItems: 'center', gap: 8, marginTop: 24, fontSize: 14, fontWeight: 600,
                  color: BRAND.ink, textDecoration: 'none', border: `1.5px solid ${BRAND.border}`, background: '#fff', padding: '11px 18px', borderRadius: 999 }}>
                {t.os_cta} ↗
              </a>
            </div>
            <AMcpCard label={t.os_code_label} note={t.os_code_note} wide={wide} />
          </div>
        </div>

        {/* final */}
        <div id="demo" style={{ ...sec(), paddingTop: wide ? 80 : 56, paddingBottom: wide ? 80 : 56, textAlign: 'center' }}>
          <div style={{ background: BRAND.ink, color: '#fff', borderRadius: 28, padding: wide ? '64px 26px' : '44px 26px',
            position: 'relative', overflow: 'hidden', maxWidth: wide ? 920 : 'none', margin: '0 auto' }}>
            <div style={{ position: 'absolute', top: -80, left: '50%', transform: 'translateX(-50%)', width: 420, height: 420, borderRadius: '50%', background: 'radial-gradient(circle, rgba(59,109,255,0.5), transparent 65%)' }}/>
            <h2 style={{ fontSize: wide ? 40 : 28, fontWeight: 700, letterSpacing: -1, position: 'relative', textWrap: 'balance' }}>{t.final_title}</h2>
            <p style={{ fontSize: 15.5, lineHeight: 1.55, color: 'rgba(255,255,255,0.7)', marginTop: 14, position: 'relative', textWrap: 'pretty', maxWidth: 460, marginLeft: 'auto', marginRight: 'auto' }}>{t.final_lead}</p>
            <a href={DEMO_URL} style={{ ...aBtnPrimary, background: BRAND.cobalt, marginTop: 26, position: 'relative', boxShadow: '0 14px 30px -10px rgba(59,109,255,0.6)' }}>{t.cta_demo} →</a>
          </div>
        </div>

        <Footer t={t} />
      </div>
    </div>
  )
}
