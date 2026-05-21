/**
 * Info-tip primitive — round «i» button + dark explainer panel below.
 *
 * Pattern lifted from the Dashboard Load tab's per-card explainer (prototype
 * `InfoIcon` / `InfoPanel`, originally local to `DashboardLoadTab.tsx`).
 * Extracted as a halo primitive so `/wellness/:metric` (and any future
 * detail screens) can reuse the exact same chrome — same lavender resting
 * state, same ink-filled open state, same dark panel with caret.
 *
 * Parent owns the open state and panel placement: render `<InfoIcon>` in the
 * card title row, then `{open && <InfoPanel>…</InfoPanel>}` below it.
 *
 * Why two components and not one: the panel's vertical position is card-
 * specific (right after the row that holds the icon, before the rest of the
 * card body). Bundling them would force a layout that doesn't always fit.
 */

interface InfoIconProps {
  open: boolean
  onClick: () => void
  /** Optional accessible label override; defaults to «What is this?». */
  ariaLabel?: string
}

export function InfoIcon({ open, onClick, ariaLabel = 'What is this?' }: InfoIconProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={ariaLabel}
      aria-expanded={open}
      className={`ml-1.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[12px] font-bold leading-none transition-colors ${
        open ? 'bg-halo-ink text-white' : 'bg-halo-brand-light text-halo-brand-dark'
      }`}
    >
      i
    </button>
  )
}

interface InfoPanelProps {
  children: string
}

export function InfoPanel({ children }: InfoPanelProps) {
  return (
    <div className="relative mt-2.5 rounded-[12px] bg-halo-ink px-3.5 py-3 text-[12.5px] leading-relaxed text-white">
      <div className="absolute -top-1.5 left-3.5 h-3 w-3 rotate-45 rounded-[2px] bg-halo-ink" />
      <div className="relative">{children}</div>
    </div>
  )
}
