/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)',
        surface: 'var(--surface)',
        'surface-2': 'var(--surface-2)',
        border: 'var(--border)',
        text: 'var(--text)',
        'text-dim': 'var(--text-dim)',
        accent: 'var(--accent)',
        green: 'var(--green)',
        yellow: 'var(--yellow)',
        orange: 'var(--orange)',
        red: 'var(--red)',

        // Halo redesign palette (docs/WEBAPP_HALO_REDESIGN_SPEC.md §5).
        // Single namespace, opt-in: bg-halo-surface, text-halo-ink, etc.
        // Additive — legacy keys above are untouched. Delete this whole
        // `halo` group + the --color-* vars once migration completes.
        halo: {
          bg: 'var(--color-bg)',
          surface: 'var(--color-surface)',
          'surface-2': 'var(--color-surface-2)',
          border: 'var(--color-border)',
          ink: 'var(--color-ink)',
          'ink-dim': 'var(--color-ink-dim)',
          'ink-dimmer': 'var(--color-ink-dimmer)',
          brand: 'var(--color-brand)',
          'brand-dark': 'var(--color-brand-dark)',
          'brand-light': 'var(--color-brand-light)',
          'status-green': 'var(--color-status-green)',
          'status-red': 'var(--color-status-red)',
          amber: 'var(--color-amber)',
          coral: 'var(--color-coral)',
        },
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'sans-serif'],
      },
      borderRadius: {
        card: '20px',
        // README §5 says 999px; 9999px is the conventional Tailwind value
        // and visually identical (both fully clamp). Intentional deviation
        // — don't "fix" back to 999px on a handoff-reconcile pass.
        pill: '9999px',
        chip: '12px',
      },
      boxShadow: {
        card: '0 1px 2px rgba(28,27,24,0.04)',
      },
    },
  },
  plugins: [],
}
